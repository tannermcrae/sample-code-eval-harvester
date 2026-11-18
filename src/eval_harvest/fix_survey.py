"""The coding survey: per-PR fix facts, exclusion reasons, a fixed rank, a saved result, and small pages.

``survey --type coding`` answers a different question from the review survey: not "did this PR go
through review" but "is this merged PR a fix with a test that can fail before it". It reports, for each
merged PR, plain facts — which paths are source and which are tests (the one rule in
:mod:`eval_harvest.patchsplit`), how many lines each side changed, the issues it links, whether its
title or label marks a fix, and how many mainline commits its author wrote — and never diff text.

**Exclusions, not scores (ADR-6).** A PR that cannot become a coding datapoint carries every reason
from :data:`EXCLUSION_REASONS`, in that fixed order. The rest are ranked by one fixed sort key, and
each ranked row says in words why it sits where it does, so an agent can explain the order it tries.

**The cache is the bus (FR-8, NFR-2).** A fresh run writes the whole result to
``<dataset>/survey/fix-survey.json``; a page request reads only that file, so paging through 500 PRs
makes no forge call and no git call, and ``capture --type coding --from-survey`` (C-1) reads the same
file rather than surveying again.

**Determinism (FR-10, NFR-1).** Rows are ordered by explicit keys, path lists are sorted, and the
cache is written with a fixed key order, ``indent=2``, UTF-8, and ``\\n`` line ends, so two runs over
the same clone and payload give the same bytes on every OS.

Online layer: Part 1 (:meth:`FixSurvey.survey_one`) reads git through
:class:`~eval_harvest.gitcmd.GitCommandRunner`; everything after it works on data alone.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, TypedDict  # the cache document is arbitrary JSON once read back; dict[str, Any] is honest.

from eval_harvest.gitcmd import GitCommandRunner
from eval_harvest.patchsplit import PatchSplit
from eval_harvest.survey import Commit, Survey

#: Where a fresh coding survey saves its full result, relative to the dataset directory.
FIX_SURVEY_RELATIVE_PATH: Final = Path("survey") / "fix-survey.json"

#: Rows on one page when ``--page-size`` is not given, and the most a page may hold (FR-9).
DEFAULT_PAGE_SIZE: Final = 20
MAX_PAGE_SIZE: Final = 50

#: A page prints at most this many characters of a PR title (FR-9). The cache keeps the full title.
TITLE_LIMIT: Final = 80

#: A page or a rank reason names at most this many linked issues; the cache keeps every one. A PR body
#: is untrusted text, so without a limit a body full of ``#n`` references could fill one page (NFR-3).
LINKED_ISSUE_DISPLAY_LIMIT: Final = 3

#: Every reason a PR can be excluded for, in the fixed order ``excluded_by`` always uses (FR-6).
EXCLUSION_REASONS: Final = (
    "not-merged",
    "no-integration-commit",
    "bot-author",
    "no-source-change",
    "no-test-change",
    "source-too-small",
    "source-too-large",
    "too-many-files",
)

#: One sentence per reason, in words an SDE can act on — the ``no-harvestable-fix`` ``offending`` text
#: (FR-11). The size sentences name the threshold the run used, filled from the cache's ``thresholds``.
_REASON_PLAIN_WORDS: Final = {
    "not-merged": "these pull requests were never merged, so there is no fix on the mainline to rebuild",
    "no-integration-commit": "these merged pull requests record no merge commit, so there is no before-state to build from",
    "bot-author": "these pull requests were opened by a bot, not by a person fixing a bug",
    "no-source-change": "these pull requests change only tests, so there is no fix for an agent to write",
    "no-test-change": "fixes in this repository ship without tests that fail before the fix",
    "source-too-small": "these fixes change fewer than {min_source_lines} source lines, too little to be a task",
    "source-too-large": "these fixes change more than {max_source_lines} source lines, too much for one task",
    "too-many-files": "these fixes touch more than {max_files} files, too many for one task",
}

#: The ``git diff --numstat`` count git writes for a binary file instead of a line count.
_BINARY_COUNT: Final = "-"

#: A ``git log`` parent list this long is a true merge: the fix's author is on the second parent.
_TRUE_MERGE_PARENTS: Final = 2

#: Fields of a ``-z`` numstat record before the path: added, deleted.
_NUMSTAT_COUNT_FIELDS: Final = 2


class FixSurveyCacheError(RuntimeError):
    """The saved coding survey is missing, is not JSON, or is not a coding-survey document."""


@dataclass(frozen=True, slots=True)
class FixSurveyThresholds:
    """The size limits a rankable fix must stay within; the CLI flags fill it (FR-6)."""

    min_source_lines: int = 3
    max_source_lines: int = 400
    max_files: int = 12


class FixFactsDict(TypedDict):
    """One merged PR's coding facts and exclusion reasons, in the fixed field order (FR-5)."""

    number: int
    title: str  # verbatim from the forge row; the pages cut it
    url: str
    merged_at: str  # "" when not merged
    author: str  # login, "" when absent
    integration_revision: str  # "" when unknown
    integration_parents: int
    linked_issues: list[int]  # sorted ascending
    fix_signal: bool
    source_paths: list[str]  # sorted
    test_paths: list[str]  # sorted
    source_lines_added: int
    source_lines_deleted: int
    test_lines_added: int
    files_touched: int  # len(source_paths) + len(test_paths)
    author_commits: int
    excluded_by: list[str]  # [] means "rankable"; order = EXCLUSION_REASONS


@dataclass(frozen=True, slots=True)
class _PathLines:
    """One changed path from ``git diff --numstat``: its new name and its line counts (0 for a binary)."""

    path: str
    added: int
    deleted: int


@dataclass(frozen=True, slots=True)
class _SplitCounts:
    """A PR's changed paths cut into source and tests, with the summed line counts of each side."""

    source_paths: list[str]
    test_paths: list[str]
    source_lines_added: int
    source_lines_deleted: int
    test_lines_added: int


class FixSurvey:
    """Survey merged PRs as coding-fix candidates, rank them, save the result, and render pages. Holds no state."""

    # ───────────────────────────── per-PR facts ─────────────────────────────

    @classmethod
    def survey(
        cls, clone: Path, pull_requests: list[dict[str, Any]], commits: list[Commit], thresholds: FixSurveyThresholds
    ) -> list[FixFactsDict]:
        """Every PR's coding facts, newest PR number first; ``commits`` is the one mainline pass the caller made."""
        rows = [cls.survey_one(pull_request, clone, commits, thresholds) for pull_request in pull_requests]
        return sorted(rows, key=lambda row: row["number"], reverse=True)

    @classmethod
    def survey_one(
        cls, pull_request: dict[str, Any], clone: Path, commits: list[Commit], thresholds: FixSurveyThresholds
    ) -> FixFactsDict:
        """One PR's facts and exclusion reasons, in at most 6 git calls (NFR-5) and none for an unmerged PR."""
        bot = cls._is_bot_author(pull_request)
        if pull_request.get("state") != "MERGED":
            return cls._facts_row(pull_request, excluded_by=["not-merged"])
        integration = (pull_request.get("mergeCommit") or {}).get("oid") or ""
        # Reuse the review survey's range reader so the first-parent rule lives in one place (tech plan §5);
        # with have_tip=False it costs two git calls: rev-list --parents and diff --name-only.
        ranges = Survey._range_facts(clone, "", integration, have_tip=False)
        if not integration or not ranges.parents:
            reasons = {"no-integration-commit", "bot-author"} if bot else {"no-integration-commit"}
            return cls._facts_row(pull_request, integration=integration, excluded_by=cls._ordered_reasons(reasons))
        counts = cls._split_counts(cls._numstat(clone, ranges.parents[0], integration))
        excluded_by = cls._content_reasons(counts, thresholds, bot=bot)
        return cls._facts_row(
            pull_request,
            integration=integration,
            parents=len(ranges.parents),
            counts=counts,
            author_commits=cls.author_commits(clone, commits, integration, ranges.parents),
            excluded_by=excluded_by,
        )

    @classmethod
    def author_commits(cls, clone: Path, commits: list[Commit], integration: str, parents: list[str]) -> int:
        """How many mainline commits the fix's author wrote up to the integration commit (0 when unknown).

        The one definition: the survey row and ``capture --type coding`` (C-1) both call it, so the
        candidate's ``author_commits`` equals the survey row's (tech plan §8). At most one git call.
        """
        author_email = cls._author_email(clone, integration, parents, commits)
        return cls._author_commit_count(commits, author_email, integration)

    @staticmethod
    def _numstat(clone: Path, first_parent: str, integration: str) -> list[_PathLines]:
        """The per-path line counts of what the integration commit put on the mainline (one git call).

        Default rename detection on purpose: capture (C-1) splits ``git diff base merge`` by each section's
        ``b/`` path, so a rename into ``tests/`` must be a test here too, not "source delete + test add".
        """
        _, out = GitCommandRunner.git(clone, "diff", "--numstat", "-z", first_parent, integration)
        return FixSurvey.parse_numstat(out)

    @staticmethod
    def parse_numstat(output: str) -> list[_PathLines]:
        """Parse ``git diff --numstat -z`` records; a rename's new path wins, a binary file counts 0 lines.

        A plain record is ``added\\tdeleted\\tpath\\0``; a rename or copy is ``added\\tdeleted\\t\\0old\\0new\\0``.
        """
        items = [item for item in output.split("\0") if item]
        parsed: list[_PathLines] = []
        index = 0
        while index < len(items):
            fields = items[index].split("\t", _NUMSTAT_COUNT_FIELDS)
            index += 1
            if len(fields) <= _NUMSTAT_COUNT_FIELDS:
                continue  # not a record head; git never writes one, but a short read must not crash
            path = fields[2]
            if not path:  # a rename or copy: the next two items are the old path and the new path
                path = items[index + 1] if index + 1 < len(items) else ""
                index += 2
            if path:
                parsed.append(_PathLines(path, FixSurvey._line_count(fields[0]), FixSurvey._line_count(fields[1])))
        return parsed

    @staticmethod
    def _line_count(field: str) -> int:
        """One numstat count; git's ``-`` for a binary file counts as 0 lines."""
        return 0 if field == _BINARY_COUNT or not field.isdigit() else int(field)

    @staticmethod
    def _split_counts(changed: list[_PathLines]) -> _SplitCounts:
        """Cut the changed paths into source and tests with the shared rule, and sum each side's lines."""
        source = [entry for entry in changed if not PatchSplit.is_test_path(entry.path)]
        tests = [entry for entry in changed if PatchSplit.is_test_path(entry.path)]
        return _SplitCounts(
            source_paths=sorted(entry.path for entry in source),
            test_paths=sorted(entry.path for entry in tests),
            source_lines_added=sum(entry.added for entry in source),
            source_lines_deleted=sum(entry.deleted for entry in source),
            test_lines_added=sum(entry.added for entry in tests),
        )

    @staticmethod
    def _author_email(clone: Path, integration: str, parents: list[str], commits: list[Commit]) -> str:
        """The email of the person who wrote the fix, lowercased; "" when unknown.

        A squash or rebase commit carries the author itself, from the mainline pass. A true merge commit
        carries whoever pressed merge, so the fix's author is read from the second parent (one git call).
        """
        if len(parents) >= _TRUE_MERGE_PARENTS:
            _, out = GitCommandRunner.git(clone, "log", "-1", "--format=%ae", parents[1])
            return out.strip().lower()
        return next((commit.author_email for commit in commits if commit.sha == integration), "")

    @staticmethod
    def _author_commit_count(commits: list[Commit], author_email: str, integration: str) -> int:
        """How many mainline commits ``author_email`` wrote up to the integration commit's time (0 when unknown)."""
        if not author_email:
            return 0
        integrated_at = Survey._integrated_at(commits, integration)
        return sum(1 for commit in commits if commit.author_email == author_email and commit.at <= integrated_at)

    @staticmethod
    def _is_bot_author(pull_request: dict[str, Any]) -> bool:
        """True for a bot author: gh's ``is_bot``, a ``[bot]`` login, or an ``app/`` login."""
        author = pull_request.get("author") or {}
        login = str(author.get("login") or "")
        return bool(author.get("is_bot")) or login.endswith("[bot]") or login.startswith("app/")

    @staticmethod
    def _fix_signal(pull_request: dict[str, Any]) -> bool:
        """True when the title reads as a fix or a label is ``bug`` (any case)."""
        labels = {str(label.get("name", "")).lower() for label in pull_request.get("labels") or []}
        return Survey.is_fix_subject(pull_request.get("title") or "") or "bug" in labels

    @staticmethod
    def _linked_issues(pull_request: dict[str, Any]) -> list[int]:
        """Issue numbers from the title, body, and branch name — text only, no forge call; ascending."""
        candidates = Survey.issue_candidates(
            pull_request.get("title") or "", pull_request.get("body") or "", pull_request.get("headRefName") or ""
        )
        return [int(number) for number in candidates]

    @classmethod
    def _content_reasons(cls, counts: _SplitCounts, thresholds: FixSurveyThresholds, *, bot: bool) -> list[str]:
        """The exclusion reasons read from a merged PR's content; a value equal to a threshold is kept."""
        reasons: set[str] = {"bot-author"} if bot else set()
        if not counts.source_paths:
            reasons.add("no-source-change")
        if not counts.test_paths or counts.test_lines_added == 0:
            reasons.add("no-test-change")
        if counts.source_paths:
            source_lines = counts.source_lines_added + counts.source_lines_deleted
            if source_lines < thresholds.min_source_lines:
                reasons.add("source-too-small")
            if source_lines > thresholds.max_source_lines:
                reasons.add("source-too-large")
        if len(counts.source_paths) + len(counts.test_paths) > thresholds.max_files:
            reasons.add("too-many-files")
        return cls._ordered_reasons(reasons)

    @staticmethod
    def _ordered_reasons(reasons: set[str]) -> list[str]:
        """``reasons`` in the fixed :data:`EXCLUSION_REASONS` order."""
        return [reason for reason in EXCLUSION_REASONS if reason in reasons]

    # The row is the FixFactsDict contract; every optional fact defaults to "absent" so the early gates stay short.
    @classmethod
    def _facts_row(  # noqa: PLR0913
        cls,
        pull_request: dict[str, Any],
        *,
        integration: str = "",
        parents: int = 0,
        counts: _SplitCounts | None = None,
        author_commits: int = 0,
        excluded_by: list[str],
    ) -> FixFactsDict:
        """Build one row in the fixed field order; ``counts=None`` means no content facts were read."""
        split = counts or _SplitCounts([], [], 0, 0, 0)
        merged = pull_request.get("state") == "MERGED"
        return FixFactsDict(
            number=int(pull_request["number"]),
            title=pull_request.get("title") or "",
            url=pull_request.get("url") or "",
            merged_at=(pull_request.get("mergedAt") or "") if merged else "",
            author=str((pull_request.get("author") or {}).get("login") or ""),
            integration_revision=integration,
            integration_parents=parents,
            linked_issues=cls._linked_issues(pull_request) if merged else [],
            fix_signal=cls._fix_signal(pull_request) if merged else False,
            source_paths=split.source_paths,
            test_paths=split.test_paths,
            source_lines_added=split.source_lines_added,
            source_lines_deleted=split.source_lines_deleted,
            test_lines_added=split.test_lines_added,
            files_touched=len(split.source_paths) + len(split.test_paths),
            author_commits=author_commits,
            excluded_by=excluded_by,
        )

    # ───────────────────────────── rank ─────────────────────────────

    @classmethod
    def rank(cls, rows: list[FixFactsDict]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """``(ranked, excluded)``: the rankable rows by the fixed key, and the others by PR number (ADR-6).

        One key tuple, all descending: a linked issue first, then a fix signal, then the author's mainline
        commit count, then the PR number as the final tie break.
        """
        rankable = sorted(
            (row for row in rows if not row["excluded_by"]),
            key=lambda row: (bool(row["linked_issues"]), row["fix_signal"], row["author_commits"], row["number"]),
            reverse=True,
        )
        ranked = [cls._ranked_row(row, position) for position, row in enumerate(rankable, start=1)]
        excluded_rows = sorted((row for row in rows if row["excluded_by"]), key=lambda row: row["number"], reverse=True)
        excluded = [{"number": row["number"], "title": row["title"], "excluded_by": row["excluded_by"]} for row in excluded_rows]
        return ranked, excluded

    @classmethod
    def _ranked_row(cls, row: FixFactsDict, rank: int) -> dict[str, Any]:
        """One ranked row in the fixed cache key order (tech plan §8)."""
        return {
            "number": row["number"],
            "title": row["title"],
            "url": row["url"],
            "merged_at": row["merged_at"],
            "rank": rank,
            "linked_issues": row["linked_issues"],
            "fix_signal": row["fix_signal"],
            "source_files": len(row["source_paths"]),
            "source_lines_added": row["source_lines_added"],
            "source_lines_deleted": row["source_lines_deleted"],
            "test_files": len(row["test_paths"]),
            "files_touched": row["files_touched"],
            "author_commits": row["author_commits"],
            "rank_reasons": cls.rank_reasons(row),
            "source_paths": row["source_paths"],
            "test_paths": row["test_paths"],
        }

    @classmethod
    def rank_reasons(cls, row: FixFactsDict) -> list[str]:
        """One short reason per sort key, in key order, ending with the PR-number tie break."""
        issues = row["linked_issues"]
        return [
            f"links issue {cls._issue_list(issues, separator=', ')}" if issues else "no linked issue",
            "title or label marks a fix" if row["fix_signal"] else "no fix signal",
            f"author has {row['author_commits']} mainline commits",
            "tie broken by PR number",
        ]

    @staticmethod
    def _issue_list(issues: list[int], *, separator: str) -> str:
        """``#1, #2, #3 (+4 more)`` — at most :data:`LINKED_ISSUE_DISPLAY_LIMIT` numbers, then a count."""
        shown = separator.join(f"#{number}" for number in issues[:LINKED_ISSUE_DISPLAY_LIMIT])
        hidden = len(issues) - LINKED_ISSUE_DISPLAY_LIMIT
        return f"{shown} (+{hidden} more)" if hidden > 0 else shown

    # ───────────────────────────── the saved result ─────────────────────────────

    @staticmethod
    def result_document(
        repo: str,
        mainline_commits: int,
        thresholds: FixSurveyThresholds,
        ranked: list[dict[str, Any]],
        excluded: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The ``fix-survey.json`` document, in the fixed top-level key order (tech plan §8)."""
        return {
            "repo": repo,
            "mainline_commits": mainline_commits,
            "thresholds": asdict(thresholds),
            "ranked": ranked,
            "excluded": excluded,
        }

    @staticmethod
    def write_cache(dataset: Path, document: dict[str, Any]) -> Path:
        """Write ``document`` to ``<dataset>/survey/fix-survey.json``, replacing an old one atomically.

        The text goes to a temporary file in the same directory first and is then renamed over the old
        file, so a run that dies mid-write leaves the previous result, never half a file.
        """
        path = dataset / FIX_SURVEY_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".fix-survey-", suffix=".tmp", delete=False
        ) as handle:
            handle.write(text)
        os.replace(handle.name, path)
        return path

    @staticmethod
    def read_cache(dataset: Path) -> dict[str, Any]:
        """The saved coding survey, or :class:`FixSurveyCacheError` naming why it cannot be used."""
        path = dataset / FIX_SURVEY_RELATIVE_PATH
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise FixSurveyCacheError(f"{path} does not exist") from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FixSurveyCacheError(f"{path} is not readable JSON: {error}") from error
        if (
            not isinstance(loaded, dict)
            or not isinstance(loaded.get("ranked"), list)
            or not isinstance(loaded.get("excluded"), list)
        ):
            raise FixSurveyCacheError(f"{path} is not a coding survey (no 'ranked' and 'excluded' lists)")
        return loaded

    # ───────────────────────────── pages ─────────────────────────────

    @staticmethod
    def page_count(total_rows: int, page_size: int) -> int:
        """How many pages ``total_rows`` fill; at least 1, so an empty list has one empty page."""
        return max(1, math.ceil(total_rows / page_size))

    @classmethod
    def render_page(cls, document: dict[str, Any], *, page: int, page_size: int, excluded: bool, dataset: Path) -> str:
        """One human page: a header, the rows, the excluded histogram, and exactly one ``next:`` line (FR-9)."""
        rows, pages = cls._page_rows(document, page=page, page_size=page_size, excluded=excluded)
        lines = [f"fix survey: {len(document['ranked'])} ranked, {len(document['excluded'])} excluded, page {page} of {pages}"]
        for row in rows:
            lines.extend(cls._excluded_line(row) if excluded else cls._ranked_lines(row))
        lines.extend(cls._histogram_lines(document))
        lines.append(f"next: {cls._next_command(dataset, page=page, pages=pages, page_size=page_size, excluded=excluded)}")
        return "\n".join(lines) + "\n"

    @classmethod
    def render_page_json(cls, document: dict[str, Any], *, page: int, page_size: int, excluded: bool, dataset: Path) -> str:
        """One ``--json`` page: ``{page, pages, rows, excluded_counts, next}``, rows without their path lists."""
        rows, pages = cls._page_rows(document, page=page, page_size=page_size, excluded=excluded)
        payload = {
            "page": page,
            "pages": pages,
            "rows": [cls._page_json_row(row) for row in rows],
            "excluded_counts": dict(cls.excluded_counts(document)),
            "next": cls._next_command(dataset, page=page, pages=pages, page_size=page_size, excluded=excluded),
        }
        return json.dumps(payload, indent=2) + "\n"

    @classmethod
    def _page_rows(
        cls, document: dict[str, Any], *, page: int, page_size: int, excluded: bool
    ) -> tuple[list[dict[str, Any]], int]:
        """The rows on ``page`` and the page count, or ``ValueError`` for a page or size out of range."""
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page size {page_size} is not in 1..{MAX_PAGE_SIZE}")
        source: list[dict[str, Any]] = document["excluded"] if excluded else document["ranked"]
        pages = cls.page_count(len(source), page_size)
        if not 1 <= page <= pages:
            raise ValueError(f"page {page} does not exist; the survey has {pages} page(s)")
        start = (page - 1) * page_size
        return source[start : start + page_size], pages

    @staticmethod
    def _page_json_row(row: dict[str, Any]) -> dict[str, Any]:
        """A row as a ``--json`` page prints it: the cache row without the path lists, its title cut."""
        trimmed = {key: value for key, value in row.items() if key not in {"source_paths", "test_paths"}}
        trimmed["title"] = FixSurvey._cut_title(str(row["title"]))
        return trimmed

    @classmethod
    def _ranked_lines(cls, row: dict[str, Any]) -> list[str]:
        """A ranked row's two page lines: its facts, then the indented ``why:`` line."""
        issues = cls._issue_list(row["linked_issues"], separator=",") if row["linked_issues"] else "-"
        title = cls._cut_title(str(row["title"]))
        facts = (
            f"#{row['number']}  rank {row['rank']}  src +{row['source_lines_added']}/-{row['source_lines_deleted']} "
            f"in {row['source_files']} file(s)  tests {row['test_files']}  issues {issues}  {title}"
        )
        return [facts, f"    why: {'; '.join(row['rank_reasons'])}"]

    @staticmethod
    def _excluded_line(row: dict[str, Any]) -> list[str]:
        """An excluded row's one page line: its number, its reasons, and its title."""
        return [f"#{row['number']}  {','.join(row['excluded_by'])}  {FixSurvey._cut_title(str(row['title']))}"]

    @classmethod
    def _histogram_lines(cls, document: dict[str, Any]) -> list[str]:
        """The ``excluded (N)`` section, or nothing when no PR is excluded."""
        counts = cls.excluded_counts(document)
        if not counts:
            return []
        return [f"excluded ({len(document['excluded'])})", *(f"  {reason:<22} {count:>4}" for reason, count in counts)]

    @staticmethod
    def excluded_counts(document: dict[str, Any]) -> list[tuple[str, int]]:
        """``(reason, count)`` over the excluded rows, most common first, then by name (as ``blocker_histogram``)."""
        counts: dict[str, int] = {}
        for row in document["excluded"]:
            for reason in row["excluded_by"]:
                counts[reason] = counts.get(reason, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    @staticmethod
    def _next_command(dataset: Path, *, page: int, pages: int, page_size: int, excluded: bool) -> str:
        """The one command an agent runs next: the following page, or capture from the cache after the last.

        The dataset path is shell-quoted because an agent pastes this line. A non-default page size is
        repeated, so the next page starts where this one ended.
        """
        if page < pages:
            command = f"eval-harvest survey --type coding --dataset {shlex.quote(str(dataset))} --page {page + 1}"
            if page_size != DEFAULT_PAGE_SIZE:
                command += f" --page-size {page_size}"
            return command + (" --excluded" if excluded else "")
        return f"eval-harvest capture --type coding --from-survey {shlex.quote(str(dataset / FIX_SURVEY_RELATIVE_PATH))}"

    # ───────────────────────────── the no-harvestable-fix refusal ─────────────────────────────

    @classmethod
    def no_harvestable_fix_offending(cls, document: dict[str, Any]) -> str:
        """The ``no-harvestable-fix`` ``offending`` text: the most common reason, in plain words (FR-11)."""
        total = len(document["excluded"]) + len(document["ranked"])
        counts = cls.excluded_counts(document)
        if total == 0 or not counts:
            return "the forge returned no pull request to survey"
        reason, count = counts[0]
        return (
            f"all {total} pull request(s) are excluded; the most common reason is {reason} ({count} of {total}): "
            f"{cls.plain_words(reason, document)}"
        )

    @staticmethod
    def plain_words(reason: str, document: dict[str, Any]) -> str:
        """The sentence for one reason, with the run's thresholds filled in."""
        thresholds = {**asdict(FixSurveyThresholds()), **(document.get("thresholds") or {})}
        return _REASON_PLAIN_WORDS[reason].format(**thresholds)

    @classmethod
    def no_harvestable_fix_next(cls, document: dict[str, Any], *, dataset: Path) -> str:
        """The one action that addresses the most common reason — the refusal's ``next``."""
        counts = cls.excluded_counts(document)
        if not counts:
            return "check --repo, --state, and --limit: the forge listed no pull request for this survey"
        reason = counts[0][0]
        thresholds = {**asdict(FixSurveyThresholds()), **(document.get("thresholds") or {})}
        excluded_list = f"eval-harvest survey --type coding --dataset {shlex.quote(str(dataset))} --page 1 --excluded"
        actions = {
            "not-merged": "re-run with --state merged (the coding default) so only merged pull requests are surveyed",
            "no-integration-commit": "pick a repository whose merged pull requests land as a commit on the mainline branch",
            "bot-author": "pick a repository whose fixes are written by people; most of these pull requests come from bots",
            "no-source-change": f"pick a repository whose fixes change source files; see the excluded list: `{excluded_list}`",
            "no-test-change": f"pick a repository whose fixes add tests; see the excluded list with `{excluded_list}`",
            "source-too-small": f"re-run with a lower --min-source-lines (this run used {thresholds['min_source_lines']})",
            "source-too-large": f"re-run with a higher --max-source-lines (this run used {thresholds['max_source_lines']})",
            "too-many-files": f"re-run with a higher --max-files (this run used {thresholds['max_files']})",
        }
        return actions[reason]

    @staticmethod
    def _cut_title(title: str) -> str:
        """A title safe for one page line: each whitespace run (line breaks too) becomes one space, then 80 chars.

        A title is untrusted text; without this a line break in one could print a fake row or a fake ``next:``.
        """
        return " ".join(title.split())[:TITLE_LIMIT]
