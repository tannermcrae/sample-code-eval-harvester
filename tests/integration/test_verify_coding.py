"""Tests for the coding checks of `verify` (E-1, FR-23 to FR-30).

A coding datapoint is worthless in ways no review check finds: the instruction names the file the
fix changes, the general instructions drift from task to task, a copy of the fix sits in the Docker
build context, or the spec files give the reward stack nothing to score. Each test here breaks one
of those on a correct coding task (`CodingTaskBuilder`) and watches its named check fire, or proves
that a correct task does not trip it. The task is written by hand because `emit --kind fix` (D-2)
does not exist yet.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fixtures.coding_task import (
    FIX_TEMPLATE,
    HIDDEN_TEST_ID,
    HIDDEN_TEST_NAME,
    HIDDEN_TEST_REPO_PATH,
    ISSUE_NUMBER,
    ISSUE_URL,
    PR_URL,
    CodingTaskBuilder,
    CodingTaskFixture,
    default_proof_path,
)

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.verify import (
    CHECK_NAMES,
    CODING_CHECK_NAMES,
    CODING_DATAPOINT_TYPE,
    HIDDEN_TESTS_RELDIR,
    ORACLE_PATCH_RELPATH,
    REFERENCE_PATCH_RELPATH,
    Verify,
    VerifyRefusal,
    VerifyReport,
)


@pytest.fixture
def coding_task(tmp_path: Path) -> CodingTaskFixture:
    """A correct coding task and the clone that holds its base commit."""
    return CodingTaskBuilder.build(tmp_path)


def _named(report: VerifyReport, check: str) -> list[VerifyRefusal]:
    """Every refusal (failure or unresolved) whose check name is `check`."""
    return [refusal for refusal in report.refusals if refusal.check == check]


def _checks(report: VerifyReport) -> list[str]:
    """The check names of every refusal, in report order."""
    return [refusal.check for refusal in report.refusals]


def _rewrite(path: Path, old: str, new: str) -> None:
    """Replace every `old` with `new` in the file at `path`; fail loudly when `old` is absent."""
    text = path.read_bytes().decode("utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_bytes(text.replace(old, new).encode("utf-8"))


def _set_oracle_hunk_context(task_dir: Path, context: str) -> None:
    """Put `context` after the second `@@` of each hunk header of the oracle patch (git ignores it when applying)."""
    oracle = task_dir / ORACLE_PATCH_RELPATH
    text = oracle.read_bytes().decode("utf-8")
    oracle.write_bytes(re.sub(r"^(@@ [^@]* @@).*$", rf"\1 {context}", text, flags=re.M).encode("utf-8"))


def _set_task_text(task_dir: Path, task_text: str) -> None:
    """Fill the instruction's `@TASK@` slot with `task_text`; the instruction stays uniform."""
    CodingTaskBuilder.write_instruction(task_dir, task_text=task_text)


def _write(path: Path, text: str) -> None:
    """Write `text` to `path`, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# ───────────────────────────── dispatch, oracle patch, tokens (FR-23 to FR-25) ─────────────────────────────


def test_coding_task_without_proof_exits_5(coding_task: CodingTaskFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """A correct coding task fails nothing; only `hidden-tests-proven` and `git-channel-absence` are unresolved (NFR-8).

    Runs through the CLI too, so the printed report is the one an agent reads (How To Verify, manual check 1).
    """
    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    assert report.failures == []
    assert sorted(refusal.check for refusal in report.unresolved) == ["git-channel-absence", "hidden-tests-proven"]
    unresolved_names = ("hidden-tests-proven", "git-channel-absence")
    assert report.passed == tuple(name for name in CODING_CHECK_NAMES if name not in unresolved_names)
    assert report.exit_code == ExitCode.RUNTIME_UNAVAILABLE

    code = Cli.run(["verify", str(coding_task.task_dir), "--clone", str(coding_task.clone)])
    printed = capsys.readouterr()
    print(printed.out, printed.err)
    assert code == ExitCode.RUNTIME_UNAVAILABLE
    assert "hidden-tests-proven" in printed.out + printed.err


def test_coding_hidden_tests_never_passed(coding_task: CodingTaskFixture) -> None:
    """`hidden-tests-proven` is never in `passed`, with or without a clone (NFR-8)."""
    for clone in (coding_task.clone, None):
        report = Verify.verify_task(coding_task.task_dir, clone=clone)
        assert "hidden-tests-proven" not in report.passed
        assert [refusal.unresolved for refusal in _named(report, "hidden-tests-proven")] == [True]


def test_broken_oracle_patch_fails_patch_does_not_apply(coding_task: CodingTaskFixture) -> None:
    """A changed context line in `solution/oracle.patch` fails `patch-does-not-apply`, naming `oracle.patch` (FR-24)."""
    _rewrite(coding_task.task_dir / ORACLE_PATCH_RELPATH, " def add(a: int, b: int) -> int:", " def add(a: int, b: int) -> str:")

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    [refusal] = _named(report, "patch-does-not-apply")
    assert "oracle.patch" in refusal.offending
    assert "change.patch" not in refusal.offending
    assert "capture --type coding" in refusal.next_


def test_coding_skips_review_only_checks(coding_task: CodingTaskFixture) -> None:
    """A coding task has no `tests/oracle.json` or `tests/rubric.md`, and their checks do not run on it."""
    assert not (coding_task.task_dir / "tests" / "oracle.json").exists()
    assert not (coding_task.task_dir / "tests" / "rubric.md").exists()

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    assert not {"finding-line-absent", "rubric-incoherent"} & set(_checks(report))
    assert not {"finding-lines-present", "rubric-coherent"} & set(report.passed)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(f"#{ISSUE_NUMBER}", id="issue-number"),
        pytest.param(PR_URL, id="pr-url"),
        pytest.param(ISSUE_URL, id="issue-url"),
        pytest.param("merge-sha", id="full-merge-sha"),
        pytest.param("merge-sha-12", id="short-merge-sha"),
        pytest.param(HIDDEN_TEST_NAME, id="hidden-test-name"),
    ],
)
def test_each_coding_token_leak_fails_answer_present(coding_task: CodingTaskFixture, token: str) -> None:
    """Each FR-25 token written into `instruction.md` fails `answer-present`, naming the token."""
    leaked = {"merge-sha": coding_task.merge_sha, "merge-sha-12": coding_task.merge_sha[:12]}.get(token, token)
    _set_task_text(coding_task.task_dir, f"See {leaked} for the report.")

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    hits = [refusal for refusal in _named(report, "answer-present") if repr(leaked) in refusal.offending]
    assert hits, f"no answer-present for {leaked!r}; refusals were {_checks(report)}"
    assert "instruction.md" in hits[0].offending


def test_oracle_added_line_in_environment_fails(coding_task: CodingTaskFixture) -> None:
    """An added line of the oracle (12+ characters) in `environment/Dockerfile` fails `answer-present` (FR-25)."""
    dockerfile = coding_task.task_dir / "environment" / "Dockerfile"
    _write(dockerfile, dockerfile.read_text(encoding="utf-8") + "# for item in items:\n")

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    hits = [refusal for refusal in _named(report, "answer-present") if "for item in items:" in refusal.offending]
    assert hits
    assert "environment/Dockerfile" in hits[0].offending


def test_oracle_context_line_is_not_mined(coding_task: CodingTaskFixture) -> None:
    """A context line of the oracle in the instruction is base text, not a leak."""
    _set_task_text(coding_task.task_dir, "The helper `def add(a: int, b: int) -> int:` is correct; the sum is not.")

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    assert _named(report, "answer-present") == []
    assert "content-absence" in report.passed


def test_unknown_datapoint_type_fails(coding_task: CodingTaskFixture) -> None:
    """`datapoint_type = "design"` gives one failure, `unknown-datapoint-type`, and runs nothing else."""
    _rewrite(coding_task.task_dir / "task.toml", f'datapoint_type = "{CODING_DATAPOINT_TYPE}"', 'datapoint_type = "design"')

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    assert _checks(report) == ["unknown-datapoint-type"]
    assert "'design'" in report.refusals[0].offending
    assert report.passed == ()
    assert report.exit_code == ExitCode.VERIFICATION


def test_review_report_unchanged() -> None:
    """The review check list is unchanged, and the coding list is its own (NFR-9).

    The existing review tests in `test_verify.py` pin the review bytes; this pins the two lists.
    """
    assert CHECK_NAMES == ("base-and-patch", "finding-lines-present", "rubric-coherent", "content-absence", "git-channel-absence")
    assert CODING_CHECK_NAMES == (
        "base-and-patch",
        "content-absence",
        "instruction-names-fix",
        "instruction-uniform",
        "answer-placement",
        "spec-coherent",
        "hidden-tests-proven",
        "git-channel-absence",
    )


# ───────────────────────────── instruction names the fix (FR-26) ─────────────────────────────


def test_instruction_with_changed_path_fails(coding_task: CodingTaskFixture) -> None:
    """The full path of a changed file fails `instruction-names-fix`; `offending` names the path and the line."""
    _rewrite(coding_task.task_dir / ORACLE_PATCH_RELPATH, "code.py", "src/calc/code.py")
    _set_task_text(coding_task.task_dir, "The sum in src/calc/code.py is wrong.")

    report = Verify.verify_task(coding_task.task_dir)

    offending = [refusal.offending for refusal in _named(report, "instruction-names-fix")]
    assert "'src/calc/code.py' at instruction.md:3" in offending


def test_instruction_with_changed_basename_fails(coding_task: CodingTaskFixture) -> None:
    """The basename of a changed file alone fails: an agent writes `code.py`, not the full path."""
    _rewrite(coding_task.task_dir / ORACLE_PATCH_RELPATH, "code.py", "src/calc/code.py")
    _set_task_text(coding_task.task_dir, "The sum in code.py is wrong.")

    report = Verify.verify_task(coding_task.task_dir)

    assert [refusal.offending for refusal in _named(report, "instruction-names-fix")] == ["'code.py' at instruction.md:3"]


def test_instruction_with_changed_symbol_fails(coding_task: CodingTaskFixture) -> None:
    """A function name from a `+def` line of the oracle fails."""
    _set_task_text(coding_task.task_dir, "Calling scan on a list raises an IndexError.")

    report = Verify.verify_task(coding_task.task_dir)

    assert [refusal.offending for refusal in _named(report, "instruction-names-fix")] == ["'scan' at instruction.md:3"]


def test_instruction_with_hunk_header_symbol_fails(coding_task: CodingTaskFixture) -> None:
    """A class name that occurs only in a hunk header fails (§4 risk row)."""
    _set_oracle_hunk_context(coding_task.task_dir, "class Calculator:")
    _set_task_text(coding_task.task_dir, "The Calculator gives a wrong total.")

    report = Verify.verify_task(coding_task.task_dir)

    assert [refusal.offending for refusal in _named(report, "instruction-names-fix")] == ["'Calculator' at instruction.md:3"]


def test_path_present_at_base_still_fails(coding_task: CodingTaskFixture) -> None:
    """`code.py` exists at the base, and with `--clone` it still fails: the instruction scan suppresses nothing."""
    _set_task_text(coding_task.task_dir, "The sum in code.py is wrong.")

    report = Verify.verify_task(coding_task.task_dir, clone=coding_task.clone)

    assert [refusal.offending for refusal in _named(report, "instruction-names-fix")] == ["'code.py' at instruction.md:3"]


def test_short_symbol_not_scanned(coding_task: CodingTaskFixture) -> None:
    """A 3-character symbol (`add`, here from a hunk header) in the instruction does not fail."""
    _set_oracle_hunk_context(coding_task.task_dir, "def add(a: int, b: int) -> int:")
    _set_task_text(coding_task.task_dir, "Adding works; please add a correct sum too.")

    report = Verify.verify_task(coding_task.task_dir)

    assert _named(report, "instruction-names-fix") == []


def test_symbol_match_needs_word_boundary(coding_task: CodingTaskFixture) -> None:
    """The symbol `scan` does not match the word `scanner`."""
    _set_task_text(coding_task.task_dir, "The scanner of totals raises an IndexError.")

    report = Verify.verify_task(coding_task.task_dir)

    assert _named(report, "instruction-names-fix") == []


def test_clean_instruction_passes(coding_task: CodingTaskFixture) -> None:
    """The fixture instruction passes `instruction-names-fix`."""
    report = Verify.verify_task(coding_task.task_dir)

    assert "instruction-names-fix" in report.passed


def test_instruction_scan_without_control_token_refuses(coding_task: CodingTaskFixture) -> None:
    """Without the planted instruction control token, the scan refuses `scan-did-not-run`; the content scan still runs."""
    _set_task_text(coding_task.task_dir, "Calling scan on a list raises an IndexError.")

    report = Verify.verify_task(coding_task.task_dir, plant_instruction_control_token=False)

    assert _checks(report).count("scan-did-not-run") == 1
    assert _named(report, "instruction-names-fix") == []
    assert "instruction-names-fix" not in report.passed
    assert "content-absence" in report.passed


def test_all_instruction_hits_reported(coding_task: CodingTaskFixture) -> None:
    """Two names in the instruction give two refusals: the scan does not stop at the first (FR-39)."""
    _set_task_text(coding_task.task_dir, "In code.py,\ncalling scan raises an IndexError.")

    report = Verify.verify_task(coding_task.task_dir)

    assert sorted(refusal.offending for refusal in _named(report, "instruction-names-fix")) == [
        "'code.py' at instruction.md:3",
        "'scan' at instruction.md:4",
    ]


def test_instruction_with_unicode_text(coding_task: CodingTaskFixture) -> None:
    """An instruction with non-ASCII text and no names passes both instruction checks."""
    _set_task_text(coding_task.task_dir, "Le total est faux — 合計が間違っています。")

    report = Verify.verify_task(coding_task.task_dir)

    assert {"instruction-names-fix", "instruction-uniform"} <= set(report.passed)


# ───────────────────────────── instruction uniform (FR-27) ─────────────────────────────


def test_one_word_change_in_general_instructions_fails(coding_task: CodingTaskFixture) -> None:
    """One changed word in the general instructions fails `instruction-not-uniform`, naming the line."""
    instruction = coding_task.task_dir / "instruction.md"
    _rewrite(instruction, "act autonomously", "act independently")
    changed_line = next(
        number for number, line in enumerate(instruction.read_text(encoding="utf-8").splitlines(), 1) if "independently" in line
    )

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "instruction-not-uniform")
    assert refusal.offending.startswith(f"instruction.md:{changed_line}: ")
    assert "independently" in refusal.offending


def test_slot_text_does_not_affect_uniformity(coding_task: CodingTaskFixture) -> None:
    """Two different `@TASK@` values both pass `instruction-uniform`."""
    for task_text in ("The total is wrong.", "A list of three numbers sums to the wrong value.\n\nIt worked before."):
        _set_task_text(coding_task.task_dir, task_text)
        assert "instruction-uniform" in Verify.verify_task(coding_task.task_dir).passed


def test_missing_slot_section_fails(coding_task: CodingTaskFixture) -> None:
    """An instruction with the tooling line removed fails: a partial match is not a match."""
    instruction = coding_task.task_dir / "instruction.md"
    lines = instruction.read_text(encoding="utf-8").splitlines(keepends=True)
    instruction.write_bytes("".join(line for line in lines if "Docker container" not in line).encode("utf-8"))

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "instruction-not-uniform")
    assert "Docker container" in refusal.offending


def test_text_after_template_fails(coding_task: CodingTaskFixture) -> None:
    """Extra text after the template's last line fails: the whole instruction must match, not a prefix."""
    instruction = coding_task.task_dir / "instruction.md"
    instruction.write_bytes(instruction.read_bytes() + b"- This is a bug-fix task.\n")

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "instruction-not-uniform")
    assert "bug-fix task" in refusal.offending


def test_template_without_two_slots_is_a_package_defect(tmp_path: Path) -> None:
    """A template that lost a slot raises: it is a defect in the package, not in the task."""
    template = FIX_TEMPLATE.read_bytes().decode("utf-8").replace("@TOOLING@", "")

    with pytest.raises(RuntimeError, match="@TOOLING@"):
        Verify._instruction_template_pattern(template)


# ───────────────────────────── answer placement (FR-28) ─────────────────────────────


def test_oracle_copy_in_environment_fails(coding_task: CodingTaskFixture) -> None:
    """A copy of `oracle.patch` at `environment/fix.diff` fails `answer-misplaced` (the Docker build context)."""
    shutil.copyfile(coding_task.task_dir / ORACLE_PATCH_RELPATH, coding_task.task_dir / "environment" / "fix.diff")

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "answer-misplaced")
    assert refusal.offending == f"{ORACLE_PATCH_RELPATH} has a copy at environment/fix.diff"
    assert "remove environment/fix.diff" in refusal.next_


def test_hidden_test_copy_in_solution_fails(coding_task: CodingTaskFixture) -> None:
    """A copy of the hidden test file under `solution/` fails: hidden tests belong in `tests/verify/` only."""
    hidden = coding_task.task_dir / HIDDEN_TESTS_RELDIR / HIDDEN_TEST_REPO_PATH
    shutil.copyfile(hidden, coding_task.task_dir / "solution" / "test_scan.py")

    report = Verify.verify_task(coding_task.task_dir)

    assert [refusal.offending for refusal in _named(report, "answer-misplaced")] == [
        f"{HIDDEN_TESTS_RELDIR}/{HIDDEN_TEST_REPO_PATH} has a copy at solution/test_scan.py"
    ]


def test_reference_patch_same_bytes_as_oracle_passes(coding_task: CodingTaskFixture) -> None:
    """The fixture's two patch files have the same bytes, and `answer-placement` passes."""
    task_dir = coding_task.task_dir
    assert (task_dir / ORACLE_PATCH_RELPATH).read_bytes() == (task_dir / REFERENCE_PATCH_RELPATH).read_bytes()

    assert "answer-placement" in Verify.verify_task(task_dir).passed


def test_empty_files_do_not_match(coding_task: CodingTaskFixture) -> None:
    """An empty hidden test and an empty `environment/.keep` have equal digests, and give no failure."""
    (coding_task.task_dir / HIDDEN_TESTS_RELDIR / HIDDEN_TEST_REPO_PATH).write_bytes(b"")
    (coding_task.task_dir / "environment" / ".keep").write_bytes(b"")

    report = Verify.verify_task(coding_task.task_dir)

    assert _named(report, "answer-misplaced") == []
    assert "answer-placement" in report.passed


def test_misnamed_oracle_patch_fails(coding_task: CodingTaskFixture) -> None:
    """A file named `oracle.patch` in `environment/`, with different bytes, still fails."""
    _write(coding_task.task_dir / "environment" / "oracle.patch", "not the fix\n")

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "answer-misplaced")
    assert "environment/oracle.patch" in refusal.offending


def test_missing_reference_patch_fails(coding_task: CodingTaskFixture) -> None:
    """A task without `tests/judge/reference.patch` fails `answer-misplaced`, naming the missing file."""
    (coding_task.task_dir / REFERENCE_PATCH_RELPATH).unlink()

    report = Verify.verify_task(coding_task.task_dir)

    assert [refusal.offending for refusal in _named(report, "answer-misplaced")] == [f"{REFERENCE_PATCH_RELPATH} is missing"]


# ───────────────────────────── spec coherent (FR-29) ─────────────────────────────


def test_validation_spec_without_cases_fails(coding_task: CodingTaskFixture) -> None:
    """A story with no `[[stories.cases]]` fails `spec-incoherent`: the validation stage has nothing to run."""
    spec = coding_task.task_dir / "tests" / "validate" / "validation_spec.toml"
    text = spec.read_text(encoding="utf-8")
    spec.write_bytes(text.split("[[stories.cases]]")[0].encode("utf-8"))

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "spec-incoherent")
    assert "validation_spec.toml" in refusal.offending
    assert "stories.cases" in refusal.offending


def test_rubric_without_fail_to_pass_fails(coding_task: CodingTaskFixture) -> None:
    """A rubric with only `pass_to_pass` criteria fails: it cannot score the fix."""
    _rewrite(coding_task.task_dir / "tests" / "judge" / "rubric.toml", 'kind = "fail_to_pass"', 'kind = "pass_to_pass"')

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "spec-incoherent")
    assert "rubric.toml" in refusal.offending


def test_tests_toml_without_fail_to_pass_fails(coding_task: CodingTaskFixture) -> None:
    """`tests.toml` with only `pass_to_pass` tests fails: the task has no fail-to-pass test."""
    _rewrite(coding_task.task_dir / HIDDEN_TESTS_RELDIR / "tests.toml", 'kind = "fail_to_pass"', 'kind = "pass_to_pass"')

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "spec-incoherent")
    assert "tests.toml" in refusal.offending
    assert "annotate" in refusal.next_


def test_listed_hidden_test_file_missing_fails(coding_task: CodingTaskFixture) -> None:
    """A listed hidden test whose file is absent fails and names the path the verifier would copy from."""
    (coding_task.task_dir / HIDDEN_TESTS_RELDIR / HIDDEN_TEST_REPO_PATH).unlink()

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "spec-incoherent")
    assert f"{HIDDEN_TESTS_RELDIR}/{HIDDEN_TEST_REPO_PATH} is missing" in refusal.offending


def test_all_spec_failures_reported(coding_task: CodingTaskFixture) -> None:
    """Two missing items give two refusals: the check does not stop at the first (FR-39)."""
    _rewrite(coding_task.task_dir / "tests" / "judge" / "rubric.toml", 'kind = "fail_to_pass"', 'kind = "pass_to_pass"')
    (coding_task.task_dir / HIDDEN_TESTS_RELDIR / HIDDEN_TEST_REPO_PATH).unlink()

    report = Verify.verify_task(coding_task.task_dir)

    assert len(_named(report, "spec-incoherent")) == 2


def test_unparsable_spec_fails_with_the_error(coding_task: CodingTaskFixture) -> None:
    """A spec file that is not TOML fails `spec-incoherent` with the parse error, and `verify` does not crash."""
    _write(coding_task.task_dir / "tests" / "judge" / "rubric.toml", "[[criteria]\n")

    report = Verify.verify_task(coding_task.task_dir)

    [refusal] = _named(report, "spec-incoherent")
    assert "does not parse" in refusal.offending


# ───────────────────────────── the four fields (FR-30) ─────────────────────────────


def test_each_coding_failure_has_four_actionable_fields(coding_task: CodingTaskFixture) -> None:
    """Every coding failure names the datapoint, the offending file, and a `next` with a command or a file (FR-30)."""
    task_dir = coding_task.task_dir
    _set_task_text(task_dir, "Calling scan raises an IndexError.")
    _rewrite(task_dir / "instruction.md", "act autonomously", "act independently")
    shutil.copyfile(task_dir / ORACLE_PATCH_RELPATH, task_dir / "environment" / "fix.diff")
    _rewrite(task_dir / "tests" / "judge" / "rubric.toml", 'kind = "fail_to_pass"', 'kind = "pass_to_pass"')

    report = Verify.verify_task(task_dir)

    failures = {refusal.check: refusal for refusal in report.failures}
    assert {"instruction-names-fix", "instruction-not-uniform", "answer-misplaced", "spec-incoherent"} <= set(failures)
    datapoint = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))["metadata"]["origin"]["pr_numbers"][0]
    for refusal in failures.values():
        assert refusal.datapoint == f"pr-{datapoint}/fix"
        assert refusal.offending
        assert re.search(r"annotate|emit|remove |\.toml|\.md", refusal.next_), refusal


# ───────────────────────────── hidden tests proven by a runner's proof (FR-35, FR-23) ─────────────────────────────

_P2P_TEST_NAME = "test_scan_of_empty_list"
_P2P_TEST_ID = f"{HIDDEN_TEST_REPO_PATH}::{_P2P_TEST_NAME}"


def _verify_with_proof(coding_task: CodingTaskFixture, proof: dict[str, Any]) -> VerifyReport:
    """Write `proof` to the default proof path and verify the task with it."""
    path = default_proof_path(coding_task)
    CodingTaskBuilder.write_proof(path, proof)
    return Verify.verify_task(coding_task.task_dir, clone=coding_task.clone, proof_path=path)


def _add_pass_to_pass_test(task_dir: Path) -> None:
    """List a second hidden test, of kind pass_to_pass, in the same file as the fail-to-pass test."""
    spec = task_dir / HIDDEN_TESTS_RELDIR / "tests.toml"
    entry = (
        f'\n[[hidden_tests]]\nid = "{_P2P_TEST_ID}"\nrepo_path = "{HIDDEN_TEST_REPO_PATH}"\n'
        f'name = "{_P2P_TEST_NAME}"\nkind = "pass_to_pass"\n'
    )
    spec.write_bytes(spec.read_bytes() + entry.encode("utf-8"))


def _hidden_tests_failures(report: VerifyReport) -> list[VerifyRefusal]:
    """The `hidden-tests-proven` hard failures of `report`."""
    return [refusal for refusal in report.failures if refusal.check == "hidden-tests-proven"]


def test_valid_proof_passes(coding_task: CodingTaskFixture) -> None:
    """A valid proof puts `hidden-tests-proven` in `passed`; only `git-channel-absence` stays unresolved (FR-35)."""
    report = _verify_with_proof(coding_task, CodingTaskBuilder.valid_proof())
    print(report)

    assert "hidden-tests-proven" in report.passed
    assert report.failures == []
    assert [refusal.check for refusal in report.unresolved] == ["git-channel-absence"]


def test_f2p_passing_at_nop_fails_by_name(coding_task: CodingTaskFixture) -> None:
    """A fail-to-pass test that passes at the base fails the check and names the test (FR-35)."""
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["tests"][HIDDEN_TEST_ID] = "passed"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _hidden_tests_failures(report)
    assert f"{HIDDEN_TEST_ID!r} passes at the base" in refusal.offending
    assert "fail_to_pass" in refusal.offending
    assert "does not observe the bug" in refusal.next_
    assert report.exit_code == ExitCode.VERIFICATION


def test_f2p_error_at_nop_counts_as_failing(coding_task: CodingTaskFixture) -> None:
    """A fail-to-pass test that errors at the base (it imports what the fix adds) obeys the rule."""
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["tests"][HIDDEN_TEST_ID] = "error"

    report = _verify_with_proof(coding_task, proof)

    assert "hidden-tests-proven" in report.passed


def test_p2p_failing_at_oracle_fails_by_name(coding_task: CodingTaskFixture) -> None:
    """A pass-to-pass test that fails with the fix fails the check and names the test (FR-35)."""
    _add_pass_to_pass_test(coding_task.task_dir)
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["tests"][_P2P_TEST_ID] = "passed"
    proof["oracle"]["tests"][_P2P_TEST_ID] = "failed"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _hidden_tests_failures(report)
    assert _P2P_TEST_ID in refusal.offending
    assert "with the fix" in refusal.offending


def test_p2p_failing_at_nop_fails_by_name(coding_task: CodingTaskFixture) -> None:
    """A pass-to-pass test that does not pass at the base fails the check and names the test (FR-35)."""
    _add_pass_to_pass_test(coding_task.task_dir)
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["tests"][_P2P_TEST_ID] = "failed"
    proof["oracle"]["tests"][_P2P_TEST_ID] = "passed"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _hidden_tests_failures(report)
    assert _P2P_TEST_ID in refusal.offending
    assert "at the base" in refusal.offending


def test_existing_suite_failing_at_nop_fails(coding_task: CodingTaskFixture) -> None:
    """A base whose own suite fails is not a usable base (US-5)."""
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["existing_suite"] = "failed"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _hidden_tests_failures(report)
    assert "existing suite" in refusal.offending
    assert "nop trial" in refusal.offending


def test_build_failed_gives_base_does_not_build(coding_task: CodingTaskFixture) -> None:
    """A trial whose environment did not build fails `base-does-not-build` with the failed step."""
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["status"] = "build-failed"
    proof["nop"]["build_error"] = "RUN uv pip sync requirements.lock"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = [refusal for refusal in report.failures if refusal.check == "base-does-not-build"]
    assert refusal.offending == "nop trial: RUN uv pip sync requirements.lock"
    assert "pin dependencies to merge_date" in refusal.next_
    assert "hidden-tests-proven" not in report.passed


def test_missing_test_result_fails(coding_task: CodingTaskFixture) -> None:
    """A listed test with no result in the oracle trial fails: a proof cannot pass a test by omission."""
    proof = CodingTaskBuilder.valid_proof()
    del proof["oracle"]["tests"][HIDDEN_TEST_ID]

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _hidden_tests_failures(report)
    assert refusal.offending == f"{HIDDEN_TEST_ID!r} has no result in the oracle trial"


def test_unlisted_test_in_proof_is_ignored(coding_task: CodingTaskFixture) -> None:
    """A test id in the proof that `tests.toml` does not list is ignored, not failed."""
    proof = CodingTaskBuilder.valid_proof()
    proof["nop"]["tests"]["tests/test_other.py::test_unlisted"] = "passed"
    proof["oracle"]["tests"]["tests/test_other.py::test_unlisted"] = "failed"

    report = _verify_with_proof(coding_task, proof)

    assert "hidden-tests-proven" in report.passed


def test_stale_digest_is_unresolved(coding_task: CodingTaskFixture) -> None:
    """A digest with one changed character is unresolved: not a pass, and not a failure (FR-35)."""
    proof = CodingTaskBuilder.valid_proof()
    proof["content_digest"] = str(proof["content_digest"])[:-1] + "2"

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _named(report, "hidden-tests-proven")
    assert refusal.unresolved
    assert "stale proof" in refusal.offending
    assert "hidden-tests-proven" not in report.passed
    assert report.exit_code == ExitCode.RUNTIME_UNAVAILABLE


def test_malformed_proof_is_unresolved(coding_task: CodingTaskFixture) -> None:
    """A proof that is not valid JSON is unresolved, and `offending` carries the parse error."""
    path = default_proof_path(coding_task)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"content_digest": ')

    report = Verify.verify_task(coding_task.task_dir, proof_path=path)

    [refusal] = _named(report, "hidden-tests-proven")
    assert refusal.unresolved
    assert "does not parse" in refusal.offending
    assert "Expecting value" in refusal.offending


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("runner",), ""),
        (("content_digest",), 7),
        (("nop", "status"), "skipped"),
        (("oracle", "tests"), ["tests/test_scan.py::test_scan_sums_items"]),
        (("oracle", "existing_suite"), None),
    ],
)
def test_mistyped_proof_field_is_unresolved(coding_task: CodingTaskFixture, field_path: tuple[str, ...], value: object) -> None:
    """A missing or mistyped proof field is unresolved, and `offending` names the field (§8)."""
    proof = CodingTaskBuilder.valid_proof()
    table: dict[str, Any] = proof
    for key in field_path[:-1]:
        table = table[key]
    table[field_path[-1]] = value

    report = _verify_with_proof(coding_task, proof)

    [refusal] = _named(report, "hidden-tests-proven")
    assert refusal.unresolved
    assert field_path[-1] in refusal.offending


def test_no_proof_is_unresolved_exit_5(coding_task: CodingTaskFixture) -> None:
    """No proof file at the default path gives exit 5 and names `hidden-tests-proven` (FR-23)."""
    code = Cli.run(["--json", "verify", str(coding_task.task_dir), "--clone", str(coding_task.clone)])

    assert code == ExitCode.RUNTIME_UNAVAILABLE
    report = Verify.verify_task(coding_task.task_dir, proof_path=default_proof_path(coding_task))
    [refusal] = _named(report, "hidden-tests-proven")
    assert refusal.unresolved
    assert "does not exist" in refusal.offending


def test_cli_default_proof_path(coding_task: CodingTaskFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """`verify <dataset>/tasks/<dir>` reads `<dataset>/proofs/<dir>.json`."""
    CodingTaskBuilder.write_valid_proof(coding_task)

    code = Cli.run(["--json", "verify", str(coding_task.task_dir), "--clone", str(coding_task.clone)])

    payload = json.loads(capsys.readouterr().out)
    assert [entry["check"] for entry in payload["unresolved"]] == ["git-channel-absence"]
    assert payload["failures"] == []
    assert code == ExitCode.RUNTIME_UNAVAILABLE


def test_cli_proof_flag_overrides_default(
    coding_task: CodingTaskFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`--proof <file>` reads that file, not the default path."""
    stale = CodingTaskBuilder.valid_proof()
    stale["content_digest"] = "sha256:stale"
    CodingTaskBuilder.write_proof(default_proof_path(coding_task), stale)
    elsewhere = CodingTaskBuilder.write_valid_proof(coding_task, tmp_path / "elsewhere" / "proof.json")

    code = Cli.run(["--json", "verify", str(coding_task.task_dir), "--clone", str(coding_task.clone), "--proof", str(elsewhere)])

    payload = json.loads(capsys.readouterr().out)
    assert [entry["check"] for entry in payload["unresolved"]] == ["git-channel-absence"]
    assert code == ExitCode.RUNTIME_UNAVAILABLE


def test_task_outside_tasks_dir_reads_no_proof(coding_task: CodingTaskFixture, tmp_path: Path) -> None:
    """A task directory not under `tasks/` (as at emit) gets no default proof, so a stray proof is never read."""
    moved = tmp_path / "elsewhere" / coding_task.task_dir.name
    shutil.copytree(coding_task.task_dir, moved)
    CodingTaskBuilder.write_proof(moved.parent.parent / "proofs" / f"{moved.name}.json", CodingTaskBuilder.valid_proof())

    code = Cli.run(["--json", "verify", str(moved), "--clone", str(coding_task.clone)])

    assert code == ExitCode.RUNTIME_UNAVAILABLE
    assert "hidden-tests-proven" not in Verify.verify_task(moved, clone=coding_task.clone).passed
