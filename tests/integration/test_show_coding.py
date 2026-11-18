"""Tests for ``show --path`` on a coding candidate (coding-eval-harvest C-2, Part 2), in-process via ``Cli.run``.

The candidate and its two patches are written by :meth:`FixCandidate.write_to_dataset` on the real
fix-history fixture, so the patches are git's own bytes. PR 115 changes three files: it modifies
``src/scan.py``, deletes ``src/tiny.py``, and adds ``tests/test_scan.py``. The deleted file is the case
the review path's ``+++ b/`` rule would drop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.fix_candidate import FixCandidate, FixFacts

_PR = 115


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """One fix-history fixture for the module; tests write only their own datasets."""
    return RepoBuilder.build_fix_history(tmp_path_factory.mktemp("show-coding"), capture_scenarios=True)


def _capture(fixture: FixHistoryFixture, dataset: Path) -> Path:
    facts = FixFacts(
        pr_number=_PR,
        pr_url=f"https://github.com/acme/widgets/pull/{_PR}",
        pr_title="fix: scan an empty list",
        pr_body="Fixes the crash.",
        merge_commit=fixture.substitutions[f"MERGE_{_PR}"],
        merged_at="2026-08-20T09:00:00Z",
        issues=(),
        author_commits=3,
    )
    return FixCandidate.write_to_dataset(facts, repo="acme/widgets", clone=fixture.clone, dataset_dir=dataset)


def _diff_git_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("diff --git ")]


def test_show_path_prints_only_that_file(
    fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _capture(fix_history, tmp_path / "ds")
    candidate = FixCandidate.load(path)
    assert candidate["source_paths"] == ["src/scan.py", "src/tiny.py"]
    assert candidate["test_paths"] == ["tests/test_scan.py"]
    code = Cli.run(["show", str(path), "--path", "src/scan.py"])
    out = capsys.readouterr().out
    assert code == ExitCode.SUCCESS
    assert out.splitlines()[0] == "src/scan.py · source"
    assert _diff_git_lines(out) == ["diff --git a/src/scan.py b/src/scan.py"]
    assert "@@" in out
    assert "+    return sum(items or [])" in out


def test_show_path_finds_test_file(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _capture(fix_history, tmp_path / "ds")
    code = Cli.run(["--json", "show", str(path), "--path", "tests/test_scan.py"])
    payload = json.loads(capsys.readouterr().out)
    assert code == ExitCode.SUCCESS
    assert payload["path"] == "tests/test_scan.py"
    assert payload["part"] == "tests"
    assert len(payload["sections"]) == 1
    assert payload["sections"][0].startswith("diff --git a/tests/test_scan.py b/tests/test_scan.py\n")
    assert list(payload) == ["path", "part", "sections"]


def test_show_path_deleted_file(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _capture(fix_history, tmp_path / "ds")
    code = Cli.run(["show", str(path), "--path", "src/tiny.py"])
    out = capsys.readouterr().out
    assert code == ExitCode.SUCCESS
    assert _diff_git_lines(out) == ["diff --git a/src/tiny.py b/src/tiny.py"]
    assert "+++ /dev/null" in out
    assert "-def tiny() -> int:" in out


def test_show_path_unknown_path(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _capture(fix_history, tmp_path / "ds")
    code = Cli.run(["show", str(path), "--path", "nope.py"])
    captured = capsys.readouterr()
    assert code == ExitCode.REFUSAL
    assert captured.out == ""
    assert "refused: unknown-path" in captured.err
    assert "nope.py is not changed by this fix" in captured.err
    assert "src/scan.py, src/tiny.py, tests/test_scan.py" in captured.err


def test_show_path_unknown_path_lists_at_most_20(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "ds" / "candidates" / "fix-9.json"
    path.parent.mkdir(parents=True)
    source_paths = [f"src/m{index:02d}.py" for index in range(25)]
    path.write_text(json.dumps({"datapoint_type": "coding", "source_paths": source_paths, "test_paths": []}), encoding="utf-8")
    code = Cli.run(["show", str(path), "--path", "nope.py"])
    err = capsys.readouterr().err
    assert code == ExitCode.REFUSAL
    assert "src/m19.py (first 20 of 25)" in err
    assert "src/m20.py" not in err


def test_show_coding_requires_path(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _capture(fix_history, tmp_path / "ds")
    assert Cli.run(["show", str(path), "--comment", "1"]) == ExitCode.USAGE
    assert "--path" in capsys.readouterr().err
    assert Cli.run(["show", str(path)]) == ExitCode.USAGE
    assert Cli.run(["show", str(path), "--path", "src/scan.py", "--iteration", "0"]) == ExitCode.USAGE


def _review_candidate(tmp_path: Path) -> Path:
    path = tmp_path / "ds" / "candidates" / "pr-7.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"repo": "acme/widgets", "pr_number": 7, "iterations": [], "comments": []}), encoding="utf-8")
    return path


def test_show_review_rejects_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _review_candidate(tmp_path)
    assert Cli.run(["show", str(path), "--path", "x"]) == ExitCode.USAGE
    assert "--comment" in capsys.readouterr().err
    assert Cli.run(["show", str(path), "--comment", "1", "--path", "x"]) == ExitCode.USAGE


def test_show_review_without_comment_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _review_candidate(tmp_path)
    assert Cli.run(["show", str(path)]) == ExitCode.USAGE
    assert "--comment" in capsys.readouterr().err


def test_show_coding_missing_patch(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dataset = tmp_path / "ds"
    path = _capture(fix_history, dataset)
    (dataset / FixCandidate.source_patch_relpath(_PR)).unlink()
    code = Cli.run(["show", str(path), "--path", "tests/test_scan.py"])
    err = capsys.readouterr().err
    assert code == ExitCode.REFUSAL
    assert "refused: missing-patch" in err
    assert FixCandidate.source_patch_relpath(_PR) in err
