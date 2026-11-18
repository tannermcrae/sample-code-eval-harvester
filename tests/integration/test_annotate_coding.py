"""Tests for ``annotate`` on a coding candidate (coding-eval-harvest C-2, Part 3), in-process via ``Cli.run``.

The candidate is written by :meth:`FixCandidate.write_to_dataset` on the real fix-history fixture (PR 115),
so it has the bytes ``capture`` writes. The load-bearing assertions: an unknown slot or a missing key
refuses and leaves the file byte-identical (FR-18, FR-19); a hidden test and a rubric criterion upsert by
their key and the report names every replacement; ``hidden_tests`` stays sorted; the "not filled yet"
codes do not block a partial fill; and ``--check`` exits 0 only when every ``emit`` slot check passes.
"""

from __future__ import annotations

import io
import json
import subprocess  # nosec B404  # only replaced by a failing stub, never run
from pathlib import Path
from typing import Any

import pytest
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

from eval_harvest.annotate import Annotate
from eval_harvest.cli import Cli, ExitCode
from eval_harvest.fix_candidate import FixCandidate, FixFacts
from eval_harvest.gitcmd import GitCommandRunner

_PR = 115

_INSTRUCTION = "Calling scan() on an empty list crashes instead of returning 0."
_HIDDEN_TEST: dict[str, Any] = {
    "repo_path": "tests/test_scan.py",
    "name": "test_f2p_empty",
    "kind": "fail_to_pass",
    "origin": "pr",
    "evidence": "fails at base with TypeError",
}
_CRITERION: dict[str, Any] = {"id": "R1", "kind": "fail_to_pass", "text": "scan([]) returns 0."}
_ENVIRONMENT: dict[str, Any] = {
    "base_image": "public.ecr.aws/docker/library/python:3.12-slim",
    "setup_steps": ["pip install uv", "uv pip install --system -r requirements.lock"],
    "lockfile": "requirements.lock",
    "test_command": "uv run pytest --junitxml=$JUNIT_PATH",
    "rationale": "the repository pins pytest in requirements.lock",
}

#: The six records of the task's Schemas & Contracts section, shaped for PR 115.
_SIX_RECORDS: list[dict[str, Any]] = [
    {"slot": "instruction", "value": _INSTRUCTION},
    {"slot": "instruction_rationale", "value": "names the symptom, not the cause"},
    {"slot": "tooling_note", "value": "Run the tests with `uv run pytest`."},
    {"hidden_test": _HIDDEN_TEST},
    {"rubric_criterion": _CRITERION},
    {"environment": _ENVIRONMENT},
]


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """One fix-history fixture for the module; tests write only their own datasets."""
    return RepoBuilder.build_fix_history(tmp_path_factory.mktemp("annotate-coding"), capture_scenarios=True)


@pytest.fixture
def candidate_path(fix_history: FixHistoryFixture, tmp_path: Path) -> Path:
    """A freshly captured coding candidate for PR 115 under ``<tmp>/ds``."""
    facts = FixFacts(
        pr_number=_PR,
        pr_url=f"https://github.com/acme/widgets/pull/{_PR}",
        pr_title="fix: scan an empty list",
        pr_body="Fixes the crash.",
        merge_commit=fix_history.substitutions[f"MERGE_{_PR}"],
        merged_at="2026-08-20T09:00:00Z",
        issues=(),
        author_commits=3,
    )
    return FixCandidate.write_to_dataset(facts, repo="acme/widgets", clone=fix_history.clone, dataset_dir=tmp_path / "ds")


def _run(monkeypatch: pytest.MonkeyPatch, path: Path, records: list[dict[str, Any]], *extra: str, json_mode: bool = False) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(record) + "\n" for record in records)))
    return Cli.run([*(["--json"] if json_mode else []), "annotate", str(path), "--stdin", *extra])


def _hidden(name: str, *, repo_path: str = "tests/test_scan.py", kind: str = "fail_to_pass") -> dict[str, Any]:
    return {"hidden_test": {**_HIDDEN_TEST, "repo_path": repo_path, "name": name, "kind": kind}}


def _write_support_files(path: Path) -> None:
    """The hidden test file and the lockfile the six records name, where the validators look for them."""
    workdir = path.parent / f"fix-{_PR}"
    hidden = workdir / "hidden" / "tests" / "test_scan.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("def test_f2p_empty() -> None:\n    assert True\n", encoding="utf-8")
    (workdir / "requirements.lock").write_text("pytest==8.3.2\n", encoding="utf-8")


def test_annotate_coding_sets_text_slots(candidate_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, candidate_path, _SIX_RECORDS[:3]) == ExitCode.SUCCESS
    candidate = FixCandidate.load(candidate_path)
    assert candidate["instruction"] == _INSTRUCTION
    assert candidate["instruction_rationale"] == "names the symptom, not the cause"
    assert candidate["tooling_note"] == "Run the tests with `uv run pytest`."
    assert candidate["hidden_tests"] == []


def test_annotate_coding_unknown_slot_refused(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    code = _run(monkeypatch, candidate_path, [{"slot": "instructions", "value": "x"}])
    err = capsys.readouterr().err
    assert code == ExitCode.REFUSAL
    assert "refused: annotate.unknown-slot" in err
    assert "'instructions'" in err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_missing_value_refused(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, [{"slot": "instruction"}]) == ExitCode.REFUSAL
    assert "refused: annotate.slot-missing-field" in capsys.readouterr().err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_hidden_test_upsert(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, candidate_path, [_hidden("test_a"), _hidden("test_b")]) == ExitCode.SUCCESS
    capsys.readouterr()
    replacement = _hidden("test_a", kind="pass_to_pass")
    assert _run(monkeypatch, candidate_path, [replacement, _hidden("test_c")]) == ExitCode.SUCCESS
    out = capsys.readouterr().out
    tests = FixCandidate.load(candidate_path)["hidden_tests"]
    assert [(entry["name"], entry["kind"]) for entry in tests] == [
        ("test_a", "pass_to_pass"),
        ("test_b", "fail_to_pass"),
        ("test_c", "fail_to_pass"),
    ]
    assert "hidden tests: 1 added, 1 replaced (tests/test_scan.py::test_a)" in out


def test_annotate_coding_hidden_tests_sorted(candidate_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_hidden("t", repo_path="b.py"), _hidden("z", repo_path="a.py"), _hidden("a", repo_path="a.py")]
    assert _run(monkeypatch, candidate_path, records) == ExitCode.SUCCESS
    tests = FixCandidate.load(candidate_path)["hidden_tests"]
    assert [f"{entry['repo_path']}::{entry['name']}" for entry in tests] == ["a.py::a", "a.py::z", "b.py::t"]


def test_annotate_coding_hidden_test_missing_key(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    record = {"hidden_test": {key: value for key, value in _HIDDEN_TEST.items() if key != "evidence"}}
    assert _run(monkeypatch, candidate_path, [record]) == ExitCode.REFUSAL
    err = capsys.readouterr().err
    assert "refused: annotate.slot-missing-field" in err
    assert "hidden_test.evidence" in err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_hidden_test_unknown_key(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, [{"hidden_test": {**_HIDDEN_TEST, "evidense": "typo"}}]) == ExitCode.REFUSAL
    assert "hidden_test.evidense" in capsys.readouterr().err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_rubric_upsert_by_id(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = {"rubric_criterion": _CRITERION}
    second = {"rubric_criterion": {**_CRITERION, "id": "R2"}}
    assert _run(monkeypatch, candidate_path, [first, second]) == ExitCode.SUCCESS
    capsys.readouterr()
    again = {"rubric_criterion": {**_CRITERION, "text": "scan([]) returns 0 and does not raise."}}
    assert _run(monkeypatch, candidate_path, [again], json_mode=True) == ExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    criteria = FixCandidate.load(candidate_path)["rubric_criteria"]
    assert [(entry["id"], entry["text"]) for entry in criteria] == [
        ("R1", "scan([]) returns 0 and does not raise."),
        ("R2", "scan([]) returns 0."),
    ]
    assert payload["rubric_criteria_replaced"] == ["R1"]
    assert payload["rubric_criteria_added"] == 0


def test_annotate_coding_environment_requires_all_keys(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    partial = {key: value for key, value in _ENVIRONMENT.items() if key != "test_command"}
    assert _run(monkeypatch, candidate_path, [{"environment": partial}]) == ExitCode.REFUSAL
    assert "environment.test_command" in capsys.readouterr().err
    assert (
        _run(monkeypatch, candidate_path, [{"environment": {**_ENVIRONMENT, "setup_steps": "pip install uv"}}])
        == ExitCode.REFUSAL
    )
    assert "environment.setup_steps" in capsys.readouterr().err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_environment_in_field_order(candidate_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reversed_record = dict(reversed(list(_ENVIRONMENT.items())))
    assert _run(monkeypatch, candidate_path, [{"environment": reversed_record}]) == ExitCode.SUCCESS
    environment = FixCandidate.load(candidate_path)["environment"]
    assert list(environment) == ["base_image", "setup_steps", "lockfile", "test_command", "rationale"]
    assert environment == _ENVIRONMENT


def test_annotate_coding_bad_kind_is_fatal(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, [_hidden("test_a", kind="flaky")]) == ExitCode.REFUSAL
    assert "refused: fix.hidden-test-invalid" in capsys.readouterr().err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_partial_fill_is_allowed(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, candidate_path, [_SIX_RECORDS[0]]) == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "set instruction" in out
    remaining = out.split("remaining slots:\n", 1)[1]
    assert "  - instruction\n" not in remaining
    for slot in ("instruction_rationale", "tooling_note", "hidden_tests", "rubric_criteria", "environment.base_image"):
        assert f"  - {slot}\n" in remaining


def test_annotate_coding_check_exits_3_until_ready(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, candidate_path, _SIX_RECORDS) == ExitCode.SUCCESS
    capsys.readouterr()
    assert Cli.run(["--json", "annotate", str(candidate_path), "--check"]) == ExitCode.REFUSAL
    payload = json.loads(capsys.readouterr().out)
    assert payload["remaining_slots"] == []
    assert len(payload["violations"]) == 2
    assert any("fix.hidden-test-file-missing" in violation for violation in payload["violations"])
    assert any("requirements.lock does not exist" in violation for violation in payload["violations"])
    _write_support_files(candidate_path)
    assert Cli.run(["annotate", str(candidate_path), "--check"]) == ExitCode.SUCCESS
    assert "ready for `emit --kind fix`" in capsys.readouterr().out


def test_annotate_coding_check_writes_nothing(candidate_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, _SIX_RECORDS, "--check") == ExitCode.REFUSAL
    assert candidate_path.read_bytes() == before


def test_annotate_coding_replace_hidden_tests(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, candidate_path, [_hidden("test_a"), _hidden("test_b")]) == ExitCode.SUCCESS
    capsys.readouterr()
    code = _run(monkeypatch, candidate_path, [_hidden("test_a")], "--replace-hidden-tests", json_mode=True)
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.SUCCESS
    assert [entry["name"] for entry in FixCandidate.load(candidate_path)["hidden_tests"]] == ["test_a"]
    assert payload["hidden_tests_added"] == 1
    assert payload["hidden_tests_replaced"] == []


def test_annotate_flag_type_mismatch(candidate_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, [_SIX_RECORDS[0]], "--replace-findings") == ExitCode.USAGE
    assert candidate_path.read_bytes() == before
    review = tmp_path / "review" / "pr-7.json"
    review.parent.mkdir()
    review.write_text(json.dumps({"repo": "acme/widgets", "comments": [], "findings": []}), encoding="utf-8")
    assert _run(monkeypatch, review, [], "--replace-hidden-tests") == ExitCode.USAGE


def test_annotate_review_record_on_coding_candidate(
    candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = candidate_path.read_bytes()
    assert _run(monkeypatch, candidate_path, [{"comment": 1, "classification": "defect"}]) == ExitCode.REFUSAL
    assert "refused: annotate.unrecognised-record" in capsys.readouterr().err
    assert candidate_path.read_bytes() == before


def test_annotate_coding_byte_identical_to_dump(candidate_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, candidate_path, _SIX_RECORDS) == ExitCode.SUCCESS
    merged = FixCandidate.load(candidate_path)
    assert candidate_path.read_bytes() == FixCandidate.dumps(merged)
    expected, violations = Annotate.apply_fix_records(merged, [])
    assert violations == []
    assert FixCandidate.dumps(expected) == candidate_path.read_bytes()


def test_apply_fix_records_does_not_mutate_input(candidate_path: Path) -> None:
    candidate = FixCandidate.load(candidate_path)
    snapshot = json.dumps(candidate)
    merged, violations = Annotate.apply_fix_records(candidate, _SIX_RECORDS)
    assert violations == []
    assert json.dumps(candidate) == snapshot
    assert merged["instruction"] == _INSTRUCTION


def test_coding_views_offline(candidate_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With git, gh, and glab all failing, brief, show --path, and annotate still succeed (NFR-2)."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a coding view called a forge or git")

    for name in ("git", "git_with_stderr", "gh", "glab"):
        monkeypatch.setattr(GitCommandRunner, name, staticmethod(refuse))
    monkeypatch.setattr(subprocess, "run", refuse)
    assert Cli.run(["brief", str(candidate_path)]) == ExitCode.SUCCESS
    assert Cli.run(["show", str(candidate_path), "--path", "src/scan.py"]) == ExitCode.SUCCESS
    assert _run(monkeypatch, candidate_path, _SIX_RECORDS) == ExitCode.SUCCESS
    assert Cli.run(["annotate", str(candidate_path), "--check"]) == ExitCode.REFUSAL
    capsys.readouterr()
