"""A coding task directory written by hand, for the coding checks of `verify` (E-1, E-2).

`emit --kind fix` (D-2) does not exist yet, so this fixture writes the §6 coding task tree itself:
the oracle fix and the judge's reference (the same bytes), a `task.toml` with the §8 coding fields,
the instruction filled from the real D-1 template, a small agent `Dockerfile`, and small valid spec
files. The fix is the real diff of the squash-merge fixture, so `base-and-patch` can apply it at the
base commit of the fixture's clone.
"""

from __future__ import annotations

import subprocess  # nosec B404  # one fixed `git diff` argv, never a shell string
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from eval_harvest.gitcmd import GIT_EXECUTABLE
from eval_harvest.harbor import FixHarvestFields, FixTaskOrigin, Harbor
from eval_harvest.tomlw import emit_document
from eval_harvest.verify import HIDDEN_TESTS_RELDIR, ORACLE_PATCH_RELPATH, REFERENCE_PATCH_RELPATH
from fixtures.repo_builder import RepoBuilder

#: The fix template the instruction is filled from: the package data `verify` compares it with.
FIX_TEMPLATE: Final = Path(__file__).parents[2] / "src" / "eval_harvest" / "verifier_tpl" / "fix" / "instruction.md"

#: The slot values of the fixture instruction. They name no changed path and no changed symbol.
DEFAULT_TASK_TEXT: Final = "Summing a list of numbers raises an IndexError on the last element. Make it return the sum."
DEFAULT_TOOLING_NOTE: Final = "Python 3.12 and pytest are installed."

_REPO: Final = "our-org/our-repo"
_PR_NUMBER: Final = 1234
ISSUE_NUMBER: Final = 77
PR_URL: Final = f"https://github.com/{_REPO}/pull/{_PR_NUMBER}"
ISSUE_URL: Final = f"https://github.com/{_REPO}/issues/{ISSUE_NUMBER}"

#: The one hidden fail-to-pass test, at its repository path.
HIDDEN_TEST_REPO_PATH: Final = "tests/test_scan.py"
HIDDEN_TEST_NAME: Final = "test_scan_sums_items"
HIDDEN_TEST_SOURCE: Final = "from code import scan\n\n\ndef test_scan_sums_items():\n    assert scan([1, 2, 3]) == 6\n"

_TESTS_TOML: Final = f"""test_command = "python -m pytest --junitxml=/logs/verifier/junit.xml"
timeout_sec = 900
protected_markers = ["/tests/"]

[[hidden_tests]]
id = "{HIDDEN_TEST_REPO_PATH}::{HIDDEN_TEST_NAME}"
repo_path = "{HIDDEN_TEST_REPO_PATH}"
name = "{HIDDEN_TEST_NAME}"
kind = "fail_to_pass"
"""

_VALIDATION_SPEC: Final = '''instructions = """Check that summing works."""

[[stories]]
id = "sum_of_three_numbers"
description = """A caller sums three numbers."""
procedure = ["Call the sum with [1, 2, 3].", "Compare with 6."]

[[stories.cases]]
input = "[1, 2, 3]"
expected = "6"
'''

_RUBRIC: Final = """[[criteria]]
id = "sum_has_no_index_error"
kind = "fail_to_pass"
text = "The sum of a list returns the total and raises no IndexError."
"""

_AGENT_DOCKERFILE: Final = "FROM public.ecr.aws/docker/library/buildpack-deps:bookworm-scm\nWORKDIR /work/repo\n"


@dataclass(frozen=True, slots=True)
class CodingTaskFixture:
    """A coding task directory, the clone that holds its base commit, and the base and merge commits."""

    task_dir: Path
    clone: Path
    base_sha: str
    merge_sha: str


class CodingTaskBuilder:
    """Writes one correct coding task directory from the squash-merge fixture. Holds no state."""

    @classmethod
    def build(cls, root: Path) -> CodingTaskFixture:
        """Build the squash-merge fixture under `root` and write a correct coding task for its fix.

        The oracle fix is `git diff <base> <round 2 tip>`; `round2_tip` stands in for the merge commit.
        `verify` on the result fails nothing: only `hidden-tests-proven` and `git-channel-absence` are
        unresolved.
        """
        pull_request = RepoBuilder.build_squash_merged_pull_request(root)
        task_dir = root / "ds" / "tasks" / f"pr-{_PR_NUMBER}-fix"
        oracle = cls._diff(pull_request.clone, pull_request.base_sha, pull_request.round2_tip)
        cls._write_bytes(task_dir / ORACLE_PATCH_RELPATH, oracle)
        cls._write_bytes(task_dir / REFERENCE_PATCH_RELPATH, oracle)
        cls._write_bytes(task_dir / "task.toml", cls._task_toml(pull_request.base_sha, pull_request.round2_tip))
        cls.write_instruction(task_dir)
        cls._write_bytes(task_dir / "environment" / "Dockerfile", _AGENT_DOCKERFILE.encode())
        cls._write_bytes(task_dir / HIDDEN_TESTS_RELDIR / "tests.toml", _TESTS_TOML.encode())
        cls._write_bytes(task_dir / HIDDEN_TESTS_RELDIR / HIDDEN_TEST_REPO_PATH, HIDDEN_TEST_SOURCE.encode())
        cls._write_bytes(task_dir / "tests" / "validate" / "validation_spec.toml", _VALIDATION_SPEC.encode())
        cls._write_bytes(task_dir / "tests" / "judge" / "rubric.toml", _RUBRIC.encode())
        return CodingTaskFixture(
            task_dir=task_dir, clone=pull_request.clone, base_sha=pull_request.base_sha, merge_sha=pull_request.round2_tip
        )

    @classmethod
    def write_instruction(
        cls, task_dir: Path, task_text: str = DEFAULT_TASK_TEXT, tooling_note: str = DEFAULT_TOOLING_NOTE
    ) -> None:
        """Write `instruction.md` from the real template, with `task_text` and `tooling_note` in its slots."""
        template = FIX_TEMPLATE.read_bytes().decode("utf-8")
        instruction = template.replace("@TASK@", task_text).replace("@TOOLING@", tooling_note)
        cls._write_bytes(task_dir / "instruction.md", instruction.encode("utf-8"))

    @staticmethod
    def _task_toml(base_sha: str, merge_sha: str) -> bytes:
        """The §8 coding `task.toml`, built by the real Harbor document builder."""
        document = Harbor.task_config_document(
            name=f"our-org/our-repo-pr-{_PR_NUMBER}-fix",
            description="Fix the sum of a list.",
            origin=FixTaskOrigin(
                repo=_REPO,
                pr_numbers=(_PR_NUMBER,),
                pr_url=PR_URL,
                issue_numbers=(ISSUE_NUMBER,),
                issue_urls=(ISSUE_URL,),
                base_commit=base_sha,
                merge_commit=merge_sha,
                base_commit_date="2026-08-01T00:00:00Z",
                merged_at="2026-08-03T09:00:00Z",
            ),
            harvest=FixHarvestFields(kind="fix", task_type="investigate-and-fix", fail_to_pass=1, pass_to_pass=0, overrides=()),
            artifacts=({"source": "/work/repo", "exclude": [".git"]},),
        )
        return emit_document(document)

    @staticmethod
    def _diff(clone: Path, base_sha: str, tip_sha: str) -> bytes:
        """The exact bytes of `git diff <base> <tip>` in `clone` (not stripped, so the patch still applies)."""
        completed = subprocess.run(  # noqa: S603 - literal argv[0], resolved binary via executable=  # nosec B603,B607
            ["git", "-C", str(clone), "diff", base_sha, tip_sha],
            executable=GIT_EXECUTABLE,
            capture_output=True,
            env=RepoBuilder.isolated_git_variables(),
            check=True,
        )
        return completed.stdout

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        """Write `content` to `path`, creating its parent directories."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
