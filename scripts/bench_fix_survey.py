"""The NFR-5 benchmark: time ``survey --type coding`` on 1000 squash-merged PRs (coding-eval-harvest G-1).

Run it via ``mise run bench-fix-survey`` (``uv run python scripts/bench_fix_survey.py`` — never bare
``python``, CLAUDE.md). It is a measurement, not a test, so it is not part of ``mise run check``.

The script builds a repository with 1000 squash-merged PRs in a temporary directory with one
``git fast-import`` process (fast, and deterministic: fixed author emails and fixed dates), writes the
matching ``gh pr list`` payload, and runs the survey in-process through ``Cli.run`` with ``--from-json``,
so it needs no network. It counts the git calls of each PR with a wrapper on
``GitCommandRunner.git_with_stderr`` and prints one line for a person to read::

    prs=1000 seconds=<s> max_git_calls_per_pr=<n> mainline_passes=<n>

A benchmark that times a survey which did nothing gives a false fast result. Thus the script exits
non-zero when the survey ranks 0 PRs, or when the saved cache holds fewer than 1000 rows in ``ranked``
and ``excluded`` together ("a scanner that finds nothing must prove it scanned something", CLAUDE.md).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess  # nosec B404 - fixture git calls only: literal "git" argv[0], shell=False
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # a gh pr list row is arbitrary JSON; dict[str, Any] is its honest shape.

from eval_harvest.cli import Cli
from eval_harvest.fix_survey import FixSurvey
from eval_harvest.gitcmd import GitCommandRunner

#: How many squash-merged PRs the fixture holds (the NFR-5 scale).
PULL_REQUEST_COUNT = 1000

#: The first PR number; numbers run from here upwards.
FIRST_PULL_REQUEST_NUMBER = 1

#: The fixed epoch of the root commit (2026-01-01T00:00:00Z); each PR lands one hour after the one before.
ROOT_EPOCH_SECONDS = 1767225600

#: The fixed number of distinct authors; PR ``n`` is by author ``n % AUTHOR_COUNT``.
AUTHOR_COUNT = 17

#: The NFR-5 limit on git calls for one PR, which the benchmark reports against.
MAX_GIT_CALLS_PER_PULL_REQUEST = 6

#: The repository name the fixture's remote and payload use.
FIXTURE_REPOSITORY = "bench/fix-survey"


@dataclass(frozen=True)
class FixturePullRequestShape:
    """The files and line counts of one generated PR (pure data, derived from its number)."""

    number: int
    source_paths: list[str]
    source_lines_per_file: int
    test_paths: list[str]


@dataclass
class GitCallTally:
    """The git calls the survey made: per PR, and how many were a mainline ``git log --first-parent`` pass."""

    current_pull_request: int | None = None
    calls_per_pull_request: dict[int, int] = field(default_factory=dict)
    mainline_passes: int = 0


class FixSurveyBenchmark:
    """Groups the steps of the NFR-5 benchmark. Holds no state; the tally is passed in."""

    @staticmethod
    def pull_request_shape(number: int) -> FixturePullRequestShape:
        """The deterministic shape of PR ``number``: 1–3 source files, 0–1 test files, 2–41 lines per source file."""
        source_file_count = 1 + number % 3
        source_paths = [f"src/pkg_{number % 40}/fix_{number}_{index}.py" for index in range(source_file_count)]
        test_paths = [] if number % 4 == 0 else [f"tests/test_fix_{number}.py"]
        return FixturePullRequestShape(
            number=number,
            source_paths=source_paths,
            source_lines_per_file=2 + (number * 7) % 40,
            test_paths=test_paths,
        )

    @staticmethod
    def file_content(path: str, line_count: int) -> str:
        """A Python file of ``line_count`` distinct lines (distinct so git never sees two files as one)."""
        return "".join(f"VALUE_{index} = {path!r}\n" for index in range(line_count))

    @classmethod
    def fast_import_stream(cls, numbers: list[int]) -> bytes:
        """The ``git fast-import`` stream: a root commit (mark 1), then one squash commit per PR (mark ``n + 1``)."""
        parts = [cls._root_commit_stream()]
        parts.extend(cls._pull_request_commit_stream(number) for number in numbers)
        return "".join(parts).encode("utf-8")

    @classmethod
    def _root_commit_stream(cls) -> str:
        """The root commit: a README and an empty test package, so every PR has a parent."""
        return cls._commit_stream(
            mark=1,
            author_email="root@example.com",
            epoch_seconds=ROOT_EPOCH_SECONDS,
            message="Initial commit",
            files={"README.md": "# bench\n", "tests/__init__.py": ""},
        )

    @classmethod
    def _pull_request_commit_stream(cls, number: int) -> str:
        """One squash-merge commit: the PR's source and test files, by a fixed author at a fixed time."""
        shape = cls.pull_request_shape(number)
        files = {path: cls.file_content(path, shape.source_lines_per_file) for path in shape.source_paths}
        files.update({path: cls.file_content(path, 3) for path in shape.test_paths})
        return cls._commit_stream(
            mark=number + 1,
            author_email=f"author-{number % AUTHOR_COUNT}@example.com",
            epoch_seconds=ROOT_EPOCH_SECONDS + number * 3600,
            message=f"{cls.pull_request_title(number)} (#{number})",
            files=files,
        )

    @staticmethod
    def _commit_stream(*, mark: int, author_email: str, epoch_seconds: int, message: str, files: dict[str, str]) -> str:
        """One ``commit refs/heads/main`` block with inline file data (fast-import sizes are in bytes)."""
        identity = f"Bench <{author_email}> {epoch_seconds} +0000"
        message_bytes = message.encode("utf-8")
        lines = [
            "commit refs/heads/main",
            f"mark :{mark}",
            f"author {identity}",
            f"committer {identity}",
            f"data {len(message_bytes)}",
            message,
        ]
        for path, content in files.items():
            lines.extend([f"M 100644 inline {path}", f"data {len(content.encode('utf-8'))}", content])
        return "\n".join(lines) + "\n"

    @staticmethod
    def pull_request_title(number: int) -> str:
        """A fix title for two PRs in three, a feature title for the rest (so the fix signal varies)."""
        return f"fix: handle case {number}" if number % 3 else f"Add feature {number}"

    @classmethod
    def build_repository(cls, repository: Path, numbers: list[int]) -> dict[int, str]:
        """Build the fixture repository with one fast-import process; return each mark's commit SHA."""
        repository.mkdir(parents=True)
        cls._run_git(repository, ["git", "init", "--quiet", "--initial-branch=main"])
        cls._run_git(repository, ["git", "remote", "add", "origin", f"https://github.com/{FIXTURE_REPOSITORY}.git"])
        marks_file = repository / ".git" / "bench-marks"
        cls._run_git(
            repository,
            ["git", "fast-import", "--quiet", f"--export-marks={marks_file}"],
            stdin=cls.fast_import_stream(numbers),
        )
        cls._run_git(repository, ["git", "checkout", "--quiet", "main"])
        return cls.parse_marks(marks_file.read_text(encoding="utf-8"))

    @staticmethod
    def _run_git(repository: Path, argv: list[str], stdin: bytes | None = None) -> None:
        """Run one fixture git command in ``repository`` and fail loudly when it fails."""
        # argv[0] is the literal "git" and every other element is its own argv entry (shell=False), as in gitcmd.py.
        subprocess.run(argv, cwd=repository, input=stdin, check=True, capture_output=True)

    @staticmethod
    def parse_marks(marks_text: str) -> dict[int, str]:
        """``:<mark> <sha>`` lines → ``{mark: sha}``."""
        pairs = (line.split(" ", 1) for line in marks_text.splitlines() if line.startswith(":"))
        return {int(mark[1:]): sha.strip() for mark, sha in pairs}

    @classmethod
    def pull_request_payload(cls, numbers: list[int], shas: dict[int, str]) -> list[dict[str, Any]]:
        """The ``gh pr list`` rows that match the fixture, newest first, as ``gh`` returns them."""
        return [cls._pull_request_row(number, shas) for number in sorted(numbers, reverse=True)]

    @classmethod
    def _pull_request_row(cls, number: int, shas: dict[int, str]) -> dict[str, Any]:
        """One merged PR row: the squash commit is ``mergeCommit``, its parent is ``baseRefOid``."""
        shape = cls.pull_request_shape(number)
        merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ROOT_EPOCH_SECONDS + number * 3600))
        additions = len(shape.source_paths) * shape.source_lines_per_file + 3 * len(shape.test_paths)
        return {
            "number": number,
            "title": cls.pull_request_title(number),
            "body": f"Fixes #{number + 5000}" if number % 2 else "No linked issue.",
            "state": "MERGED",
            "createdAt": merged_at,
            "closedAt": merged_at,
            "mergedAt": merged_at,
            "mergeCommit": {"oid": shas[number + 1]},
            "headRefName": f"pr-{number}",
            "headRefOid": shas[number + 1],
            "baseRefName": "main",
            "baseRefOid": shas[number],
            "additions": additions,
            "deletions": 0,
            "changedFiles": len(shape.source_paths) + len(shape.test_paths),
            "labels": [],
            "reviews": [],
            "url": f"https://github.com/{FIXTURE_REPOSITORY}/pull/{number}",
            "author": {"login": f"author-{number % AUTHOR_COUNT}", "is_bot": False},
        }

    @staticmethod
    def counting_git_with_stderr(
        tally: GitCallTally, real_git_with_stderr: Callable[..., tuple[int, str, str]]
    ) -> Callable[..., tuple[int, str, str]]:
        """A ``git_with_stderr`` that counts each call against the PR being surveyed, then runs the real one."""

        def counted(repo: Path, *args: str) -> tuple[int, str, str]:
            if args[:2] == ("log", "--first-parent"):
                tally.mainline_passes += 1
            if tally.current_pull_request is not None:
                tally.calls_per_pull_request[tally.current_pull_request] += 1
            return real_git_with_stderr(repo, *args)

        return counted

    @staticmethod
    def tracking_survey_one(tally: GitCallTally, real_survey_one: Callable[..., Any]) -> Callable[..., Any]:
        """A ``FixSurvey.survey_one`` that marks which PR the git calls belong to while it runs."""

        def tracked(pull_request: dict[str, Any], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — passes through
            tally.current_pull_request = int(pull_request["number"])
            tally.calls_per_pull_request[tally.current_pull_request] = 0
            try:
                return real_survey_one(pull_request, *args, **kwargs)
            finally:
                tally.current_pull_request = None

        return tracked

    @classmethod
    def run_survey(cls, clone: Path, payload: Path, dataset: Path, tally: GitCallTally) -> tuple[int, float]:
        """Run ``survey --type coding`` in-process with the counters installed; return (exit code, seconds)."""
        argv = ["survey", "--type", "coding", "--clone", str(clone), "--from-json", str(payload), "--dataset", str(dataset)]
        real_git_with_stderr = GitCommandRunner.git_with_stderr
        real_survey_one = FixSurvey.survey_one
        # The wrappers are installed for this one call and removed after it; the benchmark is the only caller.
        GitCommandRunner.git_with_stderr = staticmethod(cls.counting_git_with_stderr(tally, real_git_with_stderr))  # type: ignore[method-assign]
        FixSurvey.survey_one = staticmethod(cls.tracking_survey_one(tally, real_survey_one))  # type: ignore[method-assign]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                started = time.perf_counter()
                exit_code = Cli.run(argv)
                seconds = time.perf_counter() - started
        finally:
            GitCommandRunner.git_with_stderr = real_git_with_stderr  # type: ignore[method-assign]
            FixSurvey.survey_one = real_survey_one  # type: ignore[method-assign]
        return exit_code, seconds

    @staticmethod
    def self_check_problem(exit_code: int, document: dict[str, Any], tally: GitCallTally) -> str | None:
        """Why the timed survey cannot count as a measurement, or ``None`` when it surveyed every PR."""
        if exit_code != 0:
            return f"the survey exited {exit_code}, not 0"
        if not document["ranked"]:
            return "the survey ranked 0 PRs, so the time measures a survey that did nothing"
        rows = len(document["ranked"]) + len(document["excluded"])
        if rows < PULL_REQUEST_COUNT:
            return f"the cache holds {rows} rows in ranked + excluded, fewer than {PULL_REQUEST_COUNT}"
        if len(tally.calls_per_pull_request) < PULL_REQUEST_COUNT:
            return f"the git-call counter saw {len(tally.calls_per_pull_request)} PRs, fewer than {PULL_REQUEST_COUNT}"
        return None

    @classmethod
    def main(cls) -> int:
        """Build the fixture, time one survey, self-check it, and print the result line; non-zero on a failed check."""
        numbers = list(range(FIRST_PULL_REQUEST_NUMBER, FIRST_PULL_REQUEST_NUMBER + PULL_REQUEST_COUNT))
        with tempfile.TemporaryDirectory(prefix="bench-fix-survey-") as work:
            clone = Path(work) / "clone"
            shas = cls.build_repository(clone, numbers)
            payload = Path(work) / "prs.json"
            payload.write_text(json.dumps(cls.pull_request_payload(numbers, shas)), encoding="utf-8")
            dataset = Path(work) / "dataset"
            tally = GitCallTally()
            exit_code, seconds = cls.run_survey(clone, payload, dataset, tally)
            document = FixSurvey.read_cache(dataset) if exit_code == 0 else {"ranked": [], "excluded": []}
        problem = cls.self_check_problem(exit_code, document, tally)
        if problem is not None:
            print(f"bench-fix-survey: FAILED: {problem}", file=sys.stderr)
            return 1
        return cls.report_result(seconds, tally)

    @staticmethod
    def report_result(seconds: float, tally: GitCallTally) -> int:
        """Print the one result line; non-zero when one PR made more git calls than the NFR-5 limit."""
        max_calls = max(tally.calls_per_pull_request.values())
        print(
            f"prs={PULL_REQUEST_COUNT} seconds={seconds:.2f} "
            f"max_git_calls_per_pr={max_calls} mainline_passes={tally.mainline_passes}"
        )
        if max_calls <= MAX_GIT_CALLS_PER_PULL_REQUEST:
            return 0
        limit = MAX_GIT_CALLS_PER_PULL_REQUEST
        print(f"bench-fix-survey: FAILED: one PR made {max_calls} git calls, over {limit}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(FixSurveyBenchmark.main())
