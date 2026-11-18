"""Tests for the coding candidate file (coding-eval-harvest C-1, Part 1): real git on the fix-history fixture.

``FixCandidate.write_to_dataset`` reads the base, the diff, and the line counts from a real clone, so the
tests run it against :meth:`RepoBuilder.build_fix_history` (with the C-1 capture scenarios) rather than a
fake git: the first-parent rule and the source/test split are git's behaviour. The validator tests work
on dicts alone.
"""

from __future__ import annotations

import ast
import copy
import subprocess  # nosec B404  # the root-commit test builds a one-commit repo with argv-only git
from pathlib import Path
from typing import Any

import pytest
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

from eval_harvest.fix_candidate import BODY_CHAR_LIMIT, FixCandidate, FixCaptureError, FixFacts, FixIssue
from eval_harvest.gitcmd import GIT_EXECUTABLE, GitCommandRunner
from eval_harvest.patchsplit import PatchSplit

#: Every slot ``validate_filled`` must name after a capture, in its order (task step 12).
_ALL_SLOTS = [
    "instruction",
    "instruction_rationale",
    "tooling_note",
    "hidden_tests",
    "rubric_criteria",
    "environment.base_image",
    "environment.setup_steps",
    "environment.lockfile",
    "environment.test_command",
    "environment.rationale",
]


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """One fix-history fixture for the module; the tests only read the clone."""
    return RepoBuilder.build_fix_history(tmp_path_factory.mktemp("fix-history"), capture_scenarios=True)


def _facts(fixture: FixHistoryFixture, number: int, **overrides: Any) -> FixFacts:
    """The forge facts of PR ``number`` in the fixture, with ``overrides`` applied."""
    values: dict[str, Any] = {
        "pr_number": number,
        "pr_url": f"https://github.com/acme/widgets/pull/{number}",
        "pr_title": f"fix: PR {number}",
        "pr_body": "Fixes the bug.",
        "merge_commit": fixture.substitutions[f"MERGE_{number}"],
        "merged_at": "2026-08-01T09:00:00Z",
        "issues": (),
        "author_commits": 4,
    }
    values.update(overrides)
    return FixFacts(**values)


def _write(fixture: FixHistoryFixture, number: int, dataset: Path, **overrides: Any) -> Path:
    return FixCandidate.write_to_dataset(
        _facts(fixture, number, **overrides), repo="acme/widgets", clone=fixture.clone, dataset_dir=dataset
    )


def _git(clone: Path, *args: str) -> str:
    code, out = GitCommandRunner.git(clone, *args)
    assert code == 0, args
    return out


def _sections(patch_text: str) -> list[str]:
    """A patch's file sections, each with a trailing newline, for comparing as a multiset."""
    return [section if section.endswith("\n") else section + "\n" for section in PatchSplit.file_sections(patch_text)]


def _filled(**overrides: Any) -> dict[str, Any]:
    """A candidate with every judgment slot filled with a valid value, then ``overrides`` applied."""
    candidate: dict[str, Any] = {
        "datapoint_type": "coding",
        "pr_number": 101,
        "instruction": "The scan returns the wrong total.",
        "instruction_rationale": "From issue #3.",
        "tooling_note": "Run the tests with pytest.",
        "hidden_tests": [
            {
                "repo_path": "tests/test_scan.py",
                "name": "test_total",
                "kind": "fail_to_pass",
                "origin": "pr",
                "evidence": "fails at base",
            }
        ],
        "rubric_criteria": [{"id": "c1", "kind": "fail_to_pass", "text": "the total is right"}],
        "environment": {
            "base_image": "public.ecr.aws/docker/library/python:3.12-slim",
            "setup_steps": ["pip install -r requirements.txt"],
            "lockfile": "requirements.txt",
            "test_command": "pytest --junitxml=$JUNIT_PATH",
            "rationale": "the repo uses pytest",
        },
    }
    candidate.update(overrides)
    return candidate


def _with_environment(**environment: Any) -> dict[str, Any]:
    candidate = _filled()
    candidate["environment"] = {**candidate["environment"], **environment}
    return candidate


def _codes(violations: list[Any]) -> set[str]:
    return {violation.code for violation in violations}


def _write_lockfile(dataset: Path, number: int = 101, name: str = "requirements.txt") -> None:
    workdir = dataset / FixCandidate.workdir_relpath(number)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / name).write_text("pytest==8.0\n", encoding="utf-8")


# ───────────────────────────── write_to_dataset: geometry ─────────────────────────────


@pytest.mark.parametrize("number", [101, 116])
def test_write_splits_fix_into_two_patches(fix_history: FixHistoryFixture, tmp_path: Path, number: int) -> None:
    """The two patches together hold each section of `git diff base merge` exactly once (FR-12).

    PR 116 interleaves source and test files in path order, so the sections are compared as a multiset.
    """
    candidate = FixCandidate.load(_write(fix_history, number, tmp_path))
    source = (tmp_path / candidate["source_patch_path"]).read_text(encoding="utf-8")
    tests = (tmp_path / candidate["test_patch_path"]).read_text(encoding="utf-8")
    full = _git(fix_history.clone, "diff", candidate["base_commit"], candidate["merge_commit"])
    assert sorted(_sections(source) + _sections(tests)) == sorted(_sections(full))
    source_paths = {PatchSplit.section_path(section) for section in PatchSplit.file_sections(source)}
    test_paths = {PatchSplit.section_path(section) for section in PatchSplit.file_sections(tests)}
    assert source_paths and test_paths and not source_paths & test_paths
    assert all(not PatchSplit.is_test_path(path) for path in source_paths)
    assert all(PatchSplit.is_test_path(path) for path in test_paths)


def test_base_is_first_parent_of_true_merge(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """For the `--no-ff` merge (PR 113), the base is `<merge>^1`, not the branch side or the merge-base."""
    candidate = FixCandidate.load(_write(fix_history, 113, tmp_path))
    assert candidate["base_commit"] == _git(fix_history.clone, "rev-parse", f"{candidate['merge_commit']}^1")
    assert candidate["base_commit"] == fix_history.substitutions["BASE_113"]
    assert candidate["source_paths"] == ["src/merged.py"]


def test_base_is_parent_of_squash_commit(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """For a squash commit, the base is its only parent."""
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path))
    assert candidate["base_commit"] == fix_history.substitutions["BASE_101"]
    assert candidate["base_commit_date"] == "2026-07-31"


def test_deleted_source_file_is_a_source_path(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """PR 115 deletes `src/tiny.py`: the deletion is in `source.patch`, and its path is a source path."""
    candidate = FixCandidate.load(_write(fix_history, 115, tmp_path))
    assert candidate["source_paths"] == ["src/scan.py", "src/tiny.py"]
    assert candidate["test_paths"] == ["tests/test_scan.py"]
    assert "deleted file mode" in (tmp_path / candidate["source_patch_path"]).read_text(encoding="utf-8")


def test_source_line_counts_exclude_tests(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """PR 101's `tests/conftest.py` lines are not counted; `code.py` (+2/-1) and `src/scan.py` (+4) are."""
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path))
    assert candidate["source_paths"] == ["code.py", "src/scan.py"]
    assert candidate["test_paths"] == ["tests/conftest.py"]
    assert (candidate["source_lines_added"], candidate["source_lines_deleted"]) == (6, 1)


def test_binary_file_counts_zero_lines_and_keeps_its_path(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """PR 112's `src/logo.png` is a source path with 0 lines; only `src/logo.py`'s 3 lines count."""
    candidate = FixCandidate.load(_write(fix_history, 112, tmp_path))
    assert candidate["source_paths"] == ["src/logo.png", "src/logo.py"]
    assert candidate["source_lines_added"] == 3


# ───────────────────────────── write_to_dataset: refusals ─────────────────────────────


def test_refuses_merge_commit_not_in_clone(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """An unknown SHA refuses `fix-base-unrecoverable`, names the SHA, and writes no file (FR-14)."""
    with pytest.raises(FixCaptureError) as raised:
        _write(fix_history, 101, tmp_path, merge_commit="0" * 40)
    assert raised.value.check == "fix-base-unrecoverable"
    assert "0" * 40 in raised.value.offending
    assert not tmp_path.joinpath("candidates").exists() and not tmp_path.joinpath("patches").exists()


def test_refuses_empty_merge_commit(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """A GitLab squash MR gives no merge commit: `fix-base-unrecoverable`, saying GitLab does not expose it."""
    with pytest.raises(FixCaptureError) as raised:
        _write(fix_history, 101, tmp_path, merge_commit="")
    assert raised.value.check == "fix-base-unrecoverable"
    assert "GitLab does not expose the squash commit" in raised.value.offending


def test_refuses_root_commit(tmp_path: Path) -> None:
    """A merge commit with no parent refuses `fix-base-unrecoverable` (no index error on `rev-list`)."""
    repo = tmp_path / "root-only"
    repo.mkdir()
    env = RepoBuilder.isolated_git_variables()
    for args in (["init", "--quiet", "--initial-branch=main"], ["commit", "--quiet", "--allow-empty", "-m", "root"]):
        subprocess.run(["git", *args], executable=GIT_EXECUTABLE, cwd=repo, env=env, check=True)  # nosec B603 B607
    root = _git(repo, "rev-parse", "HEAD")
    facts = FixFacts(101, "u", "t", "", root, "2026-08-01T00:00:00Z", (), 0)
    with pytest.raises(FixCaptureError) as raised:
        FixCandidate.write_to_dataset(facts, repo="acme/widgets", clone=repo, dataset_dir=tmp_path / "ds")
    assert raised.value.check == "fix-base-unrecoverable"
    assert "root commit" in raised.value.offending


def test_refuses_fix_with_only_test_changes(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """PR 102 changes only tests: `no-source-change`, and no file is written (FR-14)."""
    with pytest.raises(FixCaptureError) as raised:
        _write(fix_history, 102, tmp_path)
    assert raised.value.check == "no-source-change"
    assert not tmp_path.joinpath("candidates").exists() and not tmp_path.joinpath("patches").exists()


# ───────────────────────────── scaffold facts ─────────────────────────────


def test_scaffold_leaves_every_slot_empty(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """After a capture every judgment slot is empty, and `validate_filled` names each one (FR-16, FR-19)."""
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path))
    assert [violation.pointer for violation in FixCandidate.validate_filled(candidate)] == _ALL_SLOTS
    assert _codes(FixCandidate.validate_filled(candidate)) == {"fix.slot-empty"}
    assert (candidate["instruction"], candidate["instruction_rationale"], candidate["tooling_note"]) == ("", "", "")
    assert candidate["hidden_tests"] == [] and candidate["rubric_criteria"] == []
    assert candidate["environment"] == {"base_image": "", "setup_steps": [], "lockfile": "", "test_command": "", "rationale": ""}
    assert FixCandidate.validate_facts(candidate) == []


def test_candidate_field_order_is_tech_plan_order(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """The file's keys are in the §8 order, because `json.dumps(sort_keys=False)` writes insertion order."""
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path))
    assert list(candidate)[:4] == ["datapoint_type", "task_type", "repo", "pr_number"]
    assert list(candidate)[-6:] == [
        "instruction",
        "instruction_rationale",
        "tooling_note",
        "hidden_tests",
        "rubric_criteria",
        "environment",
    ]
    assert (candidate["datapoint_type"], candidate["task_type"]) == ("coding", "investigate-and-fix")
    assert candidate["source_patch_path"] == "patches/fix-101/source.patch"
    assert candidate["test_patch_path"] == "patches/fix-101/tests.patch"


def test_body_truncated_at_4000(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """A 10 000-character body is cut to 4000 and flagged; a body of exactly 4000 is not flagged (FR-13)."""
    issues = (FixIssue(3, "u3", "long", "x" * 10_000), FixIssue(4, "u4", "exact", "y" * BODY_CHAR_LIMIT))
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path, pr_body="z" * BODY_CHAR_LIMIT, issues=issues))
    long_issue, exact_issue = candidate["issues"]
    assert (len(long_issue["body"]), long_issue["body_truncated"]) == (BODY_CHAR_LIMIT, True)
    assert (len(exact_issue["body"]), exact_issue["body_truncated"]) == (BODY_CHAR_LIMIT, False)
    assert candidate["pr_body_truncated"] is False


def test_issues_sorted_by_number(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """Issues 12, 3, 7 are written as 3, 7, 12, whatever order the forge gave (NFR-1)."""
    issues = tuple(FixIssue(number, f"u{number}", f"t{number}", "") for number in (12, 3, 7))
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path, issues=issues))
    assert [issue["number"] for issue in candidate["issues"]] == [3, 7, 12]


def test_merge_date_from_merged_at(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """`merge_date` is the forge date's first 10 characters: no wall clock, no time-zone shift (FR-22)."""
    candidate = FixCandidate.load(_write(fix_history, 101, tmp_path, merged_at="2026-07-31T23:59:59Z"))
    assert candidate["merge_date"] == "2026-07-31"


def test_write_is_byte_identical_twice(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """Two writes into two directories give the same candidate and patch bytes (NFR-1)."""
    issues = (FixIssue(7, "u7", "t", "b"), FixIssue(3, "u3", "t", "b"))
    first = _write(fix_history, 116, tmp_path / "a", issues=issues)
    second = _write(fix_history, 116, tmp_path / "b", issues=issues)
    assert first.read_bytes() == second.read_bytes()
    for relative in (FixCandidate.source_patch_relpath(116), FixCandidate.test_patch_relpath(116)):
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes()


def test_patches_are_newline_terminated(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """Both patches end with a newline, or `git apply` fails with `corrupt patch at line N`."""
    _write(fix_history, 101, tmp_path)
    for relative in (FixCandidate.source_patch_relpath(101), FixCandidate.test_patch_relpath(101)):
        assert (tmp_path / relative).read_bytes().endswith(b"\n")


def test_source_patch_applies_at_the_base(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """`source.patch` applies cleanly on a checkout of the base: it is a real patch, not a text dump."""
    candidate = FixCandidate.load(_write(fix_history, 115, tmp_path))
    worktree = tmp_path / "base-tree"
    _git(fix_history.clone, "worktree", "add", "--detach", str(worktree), candidate["base_commit"])
    try:
        _git(worktree, "apply", "--check", str(tmp_path / candidate["source_patch_path"]))
    finally:
        _git(fix_history.clone, "worktree", "remove", "--force", str(worktree))


def test_fix_candidate_imports_no_forge_module() -> None:
    """`fix_candidate.py` imports no forge module, so it can make no forge call (acceptance criterion)."""
    source = Path(__file__).parents[2] / "src" / "eval_harvest" / "fix_candidate.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imported, "the scan found no import at all, so it scanned nothing"
    assert not imported & {"eval_harvest.forge", "eval_harvest.gitlab", "eval_harvest.forge_host", "eval_harvest.survey"}
    assert not imported & {"urllib", "urllib.request", "http", "http.client", "socket"}


def test_datapoint_type_of(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """A coding candidate reads `coding`; a candidate with no `datapoint_type` (every review one) reads `review`."""
    assert FixCandidate.datapoint_type_of(_write(fix_history, 101, tmp_path)) == "coding"
    review = tmp_path / "pr-1.json"
    review.write_text('{"repo": "acme/widgets", "pr_number": 1}\n', encoding="utf-8")
    assert FixCandidate.datapoint_type_of(review) == "review"


# ───────────────────────────── validate_filled ─────────────────────────────


def test_filled_candidate_has_no_violation(tmp_path: Path) -> None:
    """The valid control: a fully filled candidate passes every validator (so the refusals below are real)."""
    _write_lockfile(tmp_path)
    hidden = tmp_path / FixCandidate.workdir_relpath(101) / "hidden" / "tests" / "test_scan.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("def test_total(): ...\n", encoding="utf-8")
    candidate = _filled()
    assert FixCandidate.validate_filled(candidate) == []
    assert FixCandidate.validate_hidden_test_files(candidate, tmp_path) == []
    assert FixCandidate.validate_recipe(candidate, tmp_path) == []


@pytest.mark.parametrize(("field", "value"), [("kind", "flaky"), ("evidence", ""), ("origin", "model")])
def test_hidden_test_kind_and_evidence_checked(field: str, value: str) -> None:
    """An unknown kind or origin, or empty evidence, gives `fix.hidden-test-invalid` (FR-17)."""
    candidate = _filled()
    second = {**candidate["hidden_tests"][0], "name": "test_other", field: value}
    candidate["hidden_tests"] = [candidate["hidden_tests"][0], second]
    violations = FixCandidate.validate_filled(candidate)
    assert [violation.pointer for violation in violations] == [f"hidden_tests[1].{field}"]
    assert _codes(violations) == {"fix.hidden-test-invalid"}


def test_no_fail_to_pass_test_is_reported() -> None:
    """Only `pass_to_pass` entries give `fix.no-fail-to-pass-test`: such a task cannot fail at the base."""
    candidate = _filled()
    candidate["hidden_tests"][0]["kind"] = "pass_to_pass"
    assert _codes(FixCandidate.validate_filled(candidate)) == {"fix.no-fail-to-pass-test"}


def test_tooling_note_must_be_one_short_line() -> None:
    """A two-line note, or one over 200 characters, gives `fix.slot-invalid`."""
    assert _codes(FixCandidate.validate_filled(_filled(tooling_note="one\ntwo"))) == {"fix.slot-invalid"}
    assert _codes(FixCandidate.validate_filled(_filled(tooling_note="x" * 201))) == {"fix.slot-invalid"}
    assert FixCandidate.validate_filled(_filled(tooling_note="x" * 200)) == []


def test_rubric_criterion_checked() -> None:
    """A repeated id, an unknown kind, or empty text gives `fix.rubric-criterion-invalid`."""
    criterion = {"id": "c1", "kind": "fail_to_pass", "text": "t"}
    for bad in (
        [criterion, dict(criterion)],
        [{**criterion, "kind": "style"}],
        [{**criterion, "text": ""}],
        [{**criterion, "id": ""}],
    ):
        violations = FixCandidate.validate_filled(_filled(rubric_criteria=bad))
        assert _codes(violations) == {"fix.rubric-criterion-invalid"}, bad


# ───────────────────────────── validate_hidden_test_files ─────────────────────────────


def test_hidden_test_file_missing(tmp_path: Path) -> None:
    """An entry with no file under `candidates/fix-<n>/hidden/` gives `fix.hidden-test-file-missing` (FR-17)."""
    violations = FixCandidate.validate_hidden_test_files(_filled(), tmp_path)
    assert _codes(violations) == {"fix.hidden-test-file-missing"}


@pytest.mark.parametrize("repo_path", ["../../etc/x", "/etc/passwd", "tests/../../x.py", "C:\\x.py", ""])
def test_hidden_test_path_traversal_refused(tmp_path: Path, repo_path: str) -> None:
    """A path that could leave the candidate directory is refused before the disk is read.

    The planted file is where `../../etc/x` resolves from the hidden root: the check must refuse the
    path itself, not pass because the file happens to exist.
    """
    planted = tmp_path / "candidates" / "etc" / "x"
    planted.parent.mkdir(parents=True)
    planted.write_text("outside\n", encoding="utf-8")
    candidate = _filled()
    candidate["hidden_tests"][0]["repo_path"] = repo_path
    violations = FixCandidate.validate_hidden_test_files(candidate, tmp_path)
    assert _codes(violations) == {"fix.hidden-test-invalid"}


# ───────────────────────────── validate_recipe ─────────────────────────────


def test_recipe_step_with_line_break_refused(tmp_path: Path) -> None:
    """A step with a line break would add its own Dockerfile line (FR-21, NFR-7)."""
    _write_lockfile(tmp_path)
    for step in ("pip install x\nCOPY / /leak", "pip install x\rRUN y"):
        violations = FixCandidate.validate_recipe(_with_environment(setup_steps=["ok", step]), tmp_path)
        assert [(violation.code, violation.pointer) for violation in violations] == [
            ("fix.environment-recipe-invalid", "environment.setup_steps[1]")
        ]


def test_recipe_trailing_backslash_refused(tmp_path: Path) -> None:
    """A trailing backslash (even before spaces) joins the next Dockerfile line (FR-21)."""
    _write_lockfile(tmp_path)
    for step in ("pip install x \\", "pip install x \\   "):
        assert _codes(FixCandidate.validate_recipe(_with_environment(setup_steps=[step]), tmp_path)) == {
            "fix.environment-recipe-invalid"
        }


def test_recipe_base_image_must_be_ecr_public(tmp_path: Path) -> None:
    """`python:3.12` (Docker Hub) is refused; an ECR Public image passes (FR-21)."""
    _write_lockfile(tmp_path)
    assert _codes(FixCandidate.validate_recipe(_with_environment(base_image="python:3.12"), tmp_path)) == {
        "fix.environment-recipe-invalid"
    }
    ecr = _with_environment(base_image="public.ecr.aws/docker/library/python:3.12-slim")
    assert FixCandidate.validate_recipe(ecr, tmp_path) == []


def test_recipe_lockfile_missing_or_not_a_name(tmp_path: Path) -> None:
    """A lockfile that is absent, or that is not a plain file name, is refused."""
    assert _codes(FixCandidate.validate_recipe(_filled(), tmp_path)) == {"fix.environment-recipe-invalid"}
    _write_lockfile(tmp_path)
    (tmp_path / "x.lock").write_text("", encoding="utf-8")
    for name in ("../x.lock", "sub/x.lock", "a\\b", ".", ".."):
        violations = FixCandidate.validate_recipe(_with_environment(lockfile=name), tmp_path)
        assert _codes(violations) == {"fix.environment-recipe-invalid"}, name


def test_recipe_test_command_one_line(tmp_path: Path) -> None:
    """A test command with a line break is refused."""
    _write_lockfile(tmp_path)
    violations = FixCandidate.validate_recipe(_with_environment(test_command="pytest\nRUN curl x"), tmp_path)
    assert _codes(violations) == {"fix.environment-recipe-invalid"}


# ───────────────────────────── never raise ─────────────────────────────


@pytest.mark.parametrize(
    "candidate",
    [
        {},
        {"hidden_tests": "x", "rubric_criteria": 3, "environment": []},
        {"hidden_tests": [1, None], "rubric_criteria": ["x"], "environment": {"setup_steps": "x"}, "pr_number": True},
    ],
)
def test_validators_never_raise(tmp_path: Path, candidate: dict[str, Any]) -> None:
    """Every validator returns violations for malformed input and never raises (pr-eval-harvest ADR-2)."""
    snapshot = copy.deepcopy(candidate)
    assert FixCandidate.validate_facts(candidate)
    assert FixCandidate.validate_filled(candidate)
    FixCandidate.validate_hidden_test_files(candidate, tmp_path)
    assert FixCandidate.validate_recipe(candidate, tmp_path)
    assert candidate == snapshot
