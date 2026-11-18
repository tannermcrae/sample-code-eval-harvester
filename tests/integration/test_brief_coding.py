"""Tests for ``brief`` on a coding candidate (coding-eval-harvest C-2, Part 1), in-process via ``Cli.run``.

The candidate is built with :meth:`FixCandidate.scaffold` — the same pure function ``capture`` calls —
so the facts have the shape ``capture`` writes, and ``brief`` reads only the file. The load-bearing
assertions are that no patch byte reaches the brief (FR-15), that the issue text and the cut marker are
there, that the unfilled list follows the file, and that the human brief stays under the NFR-3 straw man.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.fix_candidate import BODY_CHAR_LIMIT, FIX_FILL_CONTRACT_GUIDE, FixCandidate, FixFacts, FixIssue

_PR = 338

#: The NFR-3 straw man for one brief (tech plan §2.2; G-1 sets the real target).
_BRIEF_SIZE_STRAW_MAN = 12 * 1024

#: Every slot ``validate_filled`` names on a fresh candidate.
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


def _facts(*, pr_body: str = "Fixes the dropped tool call.", issues: tuple[FixIssue, ...] | None = None) -> FixFacts:
    default_issue = FixIssue(337, "https://github.com/acme/widgets/issues/337", "Second tool call lost", "Steps: call two tools.")
    return FixFacts(
        pr_number=_PR,
        pr_url=f"https://github.com/acme/widgets/pull/{_PR}",
        pr_title="fix: keep every tool call of one turn",
        pr_body=pr_body,
        merge_commit="m" * 40,
        merged_at="2026-07-31T12:00:00Z",
        issues=(default_issue,) if issues is None else issues,
        author_commits=41,
    )


def _write(tmp_path: Path, facts: FixFacts | None = None, **overrides: Any) -> Path:
    """Write a scaffolded coding candidate under ``<tmp>/ds/candidates/`` and return its path."""
    candidate: dict[str, Any] = dict(
        FixCandidate.scaffold(
            facts or _facts(),
            repo="acme/widgets",
            base_commit="b" * 40,
            base_commit_date="2026-07-29",
            source_paths=["src/extract.py", "src/turns.py"],
            test_paths=["tests/test_extract.py"],
            lines_added=12,
            lines_deleted=3,
        )
    )
    candidate.update(overrides)
    path = tmp_path / "ds" / FixCandidate.candidate_relpath(_PR)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    patch = path.parent.parent / FixCandidate.source_patch_relpath(_PR)
    patch.parent.mkdir(parents=True)
    patch.write_text(
        "diff --git a/src/extract.py b/src/extract.py\n--- a/src/extract.py\n+++ b/src/extract.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    return path


def _brief(path: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    """Run `brief` on `path`; `--json` in `extra` is the global flag, so it goes before the verb."""
    flags = [flag for flag in extra if flag == "--json"]
    code = Cli.run([*flags, "brief", str(path), *[flag for flag in extra if flag != "--json"]])
    return code, capsys.readouterr().out


def test_brief_coding_has_no_hunk_header(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path)
    human_code, human = _brief(path, capsys)
    json_code, machine = _brief(path, capsys, "--json")
    assert (human_code, json_code) == (ExitCode.SUCCESS, ExitCode.SUCCESS)
    for text in (human, machine):
        assert not [line for line in text.splitlines() if line.startswith("@@")]
        assert "diff --git" not in text
        assert "+++ b/" not in text


def test_brief_coding_shows_issue_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _brief(_write(tmp_path), capsys)
    assert code == ExitCode.SUCCESS
    assert "#337 Second tool call lost · https://github.com/acme/widgets/issues/337" in out
    assert "Steps: call two tools." in out
    assert "fix: keep every tool call of one turn" in out
    assert "Fixes the dropped tool call." in out


def test_brief_coding_issues_none_when_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out = _brief(_write(tmp_path, _facts(issues=())), capsys)
    assert "issues\n  (none)" in out


def test_brief_coding_marks_truncated_body(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    long_body = "x" * (BODY_CHAR_LIMIT + 10)
    issue = FixIssue(337, "https://github.com/acme/widgets/issues/337", "t", long_body)
    _, out = _brief(_write(tmp_path, _facts(pr_body=long_body, issues=(issue,))), capsys)
    assert out.count("(body cut at 4000 characters)") == 2
    _, short = _brief(_write(tmp_path / "short"), capsys)
    assert "body cut" not in short


def test_brief_coding_lists_unfilled_slots(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path)
    _, fresh = _brief(path, capsys, "--json")
    assert json.loads(fresh)["unfilled"] == _ALL_SLOTS
    _, human = _brief(path, capsys)
    for slot in _ALL_SLOTS:
        assert f"unfilled: {slot} —" in human
    candidate = json.loads(path.read_text(encoding="utf-8"))
    candidate["instruction"] = "Calling extract() on a turn with two tool calls drops the second."
    path.write_text(json.dumps(candidate), encoding="utf-8")
    _, after = _brief(path, capsys, "--json")
    assert json.loads(after)["unfilled"] == _ALL_SLOTS[1:]
    _, human_after = _brief(path, capsys)
    assert "unfilled: instruction —" not in human_after


def test_brief_coding_next_commands_use_real_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path)
    _, out = _brief(path, capsys, "--json")
    assert json.loads(out)["next_commands"] == [
        f"eval-harvest show {path} --path src/extract.py",
        f"eval-harvest annotate {path} --stdin",
        f"eval-harvest emit {path} --kind fix",
    ]
    _, human = _brief(path, capsys)
    assert f"eval-harvest emit {path} --kind fix" in human
    assert "<" not in human.split("\nnext\n", 1)[1]


def test_brief_coding_size_bound(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = ("word " * BODY_CHAR_LIMIT)[: BODY_CHAR_LIMIT + 1]
    issue = FixIssue(337, "https://github.com/acme/widgets/issues/337", "a long issue", body)
    _, out = _brief(_write(tmp_path, _facts(pr_body=body, issues=(issue,))), capsys)
    size = len(out.encode("utf-8"))
    assert size < _BRIEF_SIZE_STRAW_MAN, f"the coding brief is {size} bytes (NFR-3 straw man: {_BRIEF_SIZE_STRAW_MAN})"


def test_brief_coding_json_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out = _brief(_write(tmp_path), capsys, "--json")
    payload = json.loads(out)
    assert list(payload) == [
        "datapoint_type",
        "repo",
        "pr_number",
        "pr_url",
        "base_commit",
        "base_commit_date",
        "merge_commit",
        "merge_date",
        "pr",
        "issues",
        "size",
        "source_paths",
        "test_paths",
        "fill_contract",
        "unfilled",
        "next_commands",
    ]
    assert payload["datapoint_type"] == "coding"
    assert payload["pr"] == {
        "title": "fix: keep every tool call of one turn",
        "body": "Fixes the dropped tool call.",
        "body_truncated": False,
    }
    assert payload["issues"][0]["number"] == 337
    assert payload["size"] == {
        "source_files": 2,
        "source_lines_added": 12,
        "source_lines_deleted": 3,
        "test_files": 1,
        "author_commits": 41,
    }
    assert payload["fill_contract"] == list(FIX_FILL_CONTRACT_GUIDE)


def test_brief_coding_human_sections_in_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out = _brief(_write(tmp_path), capsys)
    lines = out.splitlines()
    assert lines[0] == f"fix-{_PR} · acme/widgets · https://github.com/acme/widgets/pull/{_PR}"
    assert lines[1] == f"base {'b' * 12} (2026-07-29) · merge {'m' * 12} · merge_date 2026-07-31"
    headings = [line for line in lines if line and not line.startswith(" ")][2:]
    assert headings == ["pr", "issues", "size", "source paths", "test paths", "you fill", "next"]
    assert "  source: 2 file(s), +12 -3 · tests: 1 file(s) · author_commits: 41" in lines


def test_brief_coding_refuses_malformed_candidate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path)
    candidate = json.loads(path.read_text(encoding="utf-8"))
    del candidate["merge_commit"]
    path.write_text(json.dumps(candidate), encoding="utf-8")
    code = Cli.run(["brief", str(path)])
    err = capsys.readouterr().err
    assert code == ExitCode.REFUSAL
    assert "refused: malformed-candidate" in err
    assert "merge_commit" in err
    assert f"capture {_PR} --type coding" in err


def test_brief_coding_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path)
    before = path.read_bytes()
    _brief(path, capsys)
    assert path.read_bytes() == before
