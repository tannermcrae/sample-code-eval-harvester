"""Tests for the answer-absence checker: the git-channel checklist and the content scanner (E-1).

A leaked answer is the expensive silent failure — every score looks fine and measures nothing —
so these tests plant real leaks in real fixture repositories and assert each is caught. Two
properties matter most: the git-channel half catches routes that leave the visible tree looking
clean (``objects/info/alternates``, a bare repo smuggled into the tree, an unreachable reflog
entry), and the content scanner refuses ``scan-did-not-run`` when its planted control token is
absent — because an empty result and a broken scan are otherwise identical (NFR-6).
"""

from __future__ import annotations

import subprocess  # nosec B404  # the seal path is asserted to shell out only to local git; see test_seal_makes_no_network_call
from pathlib import Path

import pytest
from fixtures.repo_builder import RepoBuilder

from eval_harvest.seal import Finding, Report, Seal

#: The control token these tests plant in the corpus so the scanner can prove it scanned something
#: (NFR-6). Named rather than repeated as a literal at each call site: it is one value, it has to be
#: the same value in the planted text and in the ``control_token=`` argument, and a bare string in a
#: ``control_token=`` keyword also reads to a secret scanner as a hardcoded credential.
CONTROL_TOKEN = "CONTROL-TOKEN-XYZ"  # nosec B105  # a scan-liveness marker, not a credential


def _finding(report: Report, check: str) -> Finding:
    """The single finding whose check name is ``check`` — fails the test loudly if absent."""
    matches = [f for f in report.findings if f.check == check]
    assert matches, f"no finding named {check!r}; findings were {[f.check for f in report.findings]}"
    return matches[0]


def test_alternates_channel_leak_caught(tmp_path: Path) -> None:
    """``objects/info/alternates`` pointing at the source is flagged even though the tree greps clean.

    Catches a tree that greps clean while ``git show`` prints the answer (the motivating S-3 case).
    """
    repo = RepoBuilder.build_sealed_repo(tmp_path / "sealed")
    source = RepoBuilder.build_sealed_repo(tmp_path / "source")
    RepoBuilder.plant_alternates(repo, source / ".git" / "objects")
    report = Seal.check_repo(repo, canaries=())
    assert not _finding(report, "absent:objects/info/alternates").passed


def test_packed_refs_and_commit_graph_leaks_caught(tmp_path: Path) -> None:
    """packed-refs, commit-graph, refs/replace, an unreachable reflog entry, and include.path each flag.

    Catches every non-obvious git channel ``seal_assert`` exists for (S-3): a bad seal that only
    checks commit counts and tree greps passes all of these.
    """
    packed = RepoBuilder.build_sealed_repo(tmp_path / "packed")
    RepoBuilder.plant_packed_refs(packed)
    assert not _finding(Seal.check_repo(packed, canaries=()), "absent:packed-refs").passed

    graphed = RepoBuilder.build_sealed_repo(tmp_path / "graphed")
    RepoBuilder.plant_commit_graph(graphed)
    assert not _finding(Seal.check_repo(graphed, canaries=()), "absent:objects/info/commit-graph").passed

    replaced = RepoBuilder.build_sealed_repo(tmp_path / "replaced")
    RepoBuilder.plant_replace_ref(replaced)
    assert not _finding(Seal.check_repo(replaced, canaries=()), "no-refs:refs/replace").passed

    reflogged = RepoBuilder.build_sealed_repo(tmp_path / "reflogged")
    RepoBuilder.plant_unreachable_reflog_entry(reflogged)
    assert not _finding(Seal.check_repo(reflogged, canaries=()), "reflog-references-nothing-unreachable").passed

    included = RepoBuilder.build_sealed_repo(tmp_path / "included")
    RepoBuilder.plant_include_path(included)
    assert not _finding(Seal.check_repo(included, canaries=()), "no-config:include.path").passed


def test_bare_repo_in_tree_caught(tmp_path: Path) -> None:
    """A bare ``answerkey.git/`` committed into the tree is flagged by shape, not by name.

    Catches a whole second repository smuggled into the task tree (S-3): a literal ``.git`` search
    cannot see a bare repo, which has no ``.git`` entry — only its ``objects/`` + ``HEAD`` shape does.
    """
    repo = RepoBuilder.build_sealed_repo(tmp_path / "sealed")
    RepoBuilder.plant_bare_repo(repo)
    report = Seal.check_repo(repo, canaries=())
    finding = _finding(report, "no-bare-repositories")
    assert not finding.passed
    assert "answerkey.git" in finding.detail


def test_clean_repo_reports_no_failures(tmp_path: Path) -> None:
    """A freshly built single-commit repo passes every seal check — the baseline leaks deviate from."""
    repo = RepoBuilder.build_sealed_repo(tmp_path / "sealed")
    report = Seal.check_repo(repo, canaries=())
    assert report.failures == [], f"clean repo unexpectedly failed: {[(f.check, f.detail) for f in report.failures]}"


def test_content_scan_finds_pr_number_in_instruction(tmp_path: Path) -> None:
    """ "#1234" in ``instruction.md`` is flagged with the file and line it leaked into.

    Catches the PR number leaking into agent-visible content (S-2): a reviewer could look the PR up.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    body = f"Review this change.\nRelated to #1234 upstream.\n{CONTROL_TOKEN}\n"
    (task_dir / "instruction.md").write_text(body, encoding="utf-8")
    report = Seal.scan_agent_visible(task_dir, tokens=["#1234"], control_token=CONTROL_TOKEN)
    finding = _finding(report, "answer-absent:#1234")
    assert not finding.passed
    assert "instruction.md:2" in finding.detail


def test_solution_text_in_agent_tree_caught(tmp_path: Path) -> None:
    """Verifier-only solution text present in an agent-visible file is flagged (FR-40).

    Catches the reference answer leaking to the evaluated agent: the fix in ``solution/solve.sh`` must
    never appear where the agent reads. The scanner derives the answer from the verifier-only tree.
    """
    task_dir = tmp_path / "task"
    (task_dir / "solution").mkdir(parents=True)
    (task_dir / "solution" / "solve.sh").write_text("#!/bin/sh\nreturn_early_on_null_pointer_guard\n", encoding="utf-8")
    (task_dir / "instruction.md").write_text(
        f"Fix the crash.\nhint: return_early_on_null_pointer_guard\n{CONTROL_TOKEN}\n", encoding="utf-8"
    )
    report = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN)
    assert report.failures, "solution text leaking into the agent tree was not flagged"
    assert any("instruction.md" in f.detail for f in report.failures)


def test_missing_control_token_refuses_scan_did_not_run(tmp_path: Path) -> None:
    """With no planted control token in the corpus, the scan refuses rather than reporting clean.

    Catches an empty result mistaken for a clean one (S-4): if the scan enumerated nothing, it must
    say so, not pass. This is the honesty guard — E-3 exercises it by removing the token.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("Review this change. Nothing to see here.\n", encoding="utf-8")
    report = Seal.scan_agent_visible(task_dir, tokens=["#1234"], control_token=CONTROL_TOKEN)
    finding = _finding(report, "scan-did-not-run")
    assert not finding.passed


def test_control_token_present_lets_clean_scan_pass(tmp_path: Path) -> None:
    """With the control token planted and no answer tokens present, the scan passes (scan ran, clean)."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text(f"Review this change.\n{CONTROL_TOKEN}\n", encoding="utf-8")
    report = Seal.scan_agent_visible(task_dir, tokens=["#1234"], control_token=CONTROL_TOKEN)
    assert report.failures == [], f"clean scan unexpectedly failed: {[(f.check, f.detail) for f in report.failures]}"


#: A coding oracle patch: one context line and two added lines, each long enough to be a fragment.
_ORACLE_PATCH = """diff --git a/src/sum_module.py b/src/sum_module.py
--- a/src/sum_module.py
+++ b/src/sum_module.py
@@ -1,2 +1,3 @@
 def unchanged_context_helper():
+    guard_against_empty_list_input()
+    return compute_the_running_total()
"""


def _coding_task_with_oracle(tmp_path: Path, agent_text: str) -> Path:
    """A task with `solution/oracle.patch`, a `solution/notes.txt` that is not an answer source, and `instruction.md`."""
    task_dir = tmp_path / "task"
    (task_dir / "solution").mkdir(parents=True)
    (task_dir / "solution" / "oracle.patch").write_text(_ORACLE_PATCH, encoding="utf-8")
    (task_dir / "solution" / "notes.txt").write_text("a_line_that_only_the_default_sources_mine\n", encoding="utf-8")
    (task_dir / "instruction.md").write_text(f"{agent_text}\n{CONTROL_TOKEN}\n", encoding="utf-8")
    return task_dir


def test_answer_sources_mine_added_patch_lines(tmp_path: Path) -> None:
    """An added line of a `.patch` answer source in an agent-visible file is a leak (E-1, FR-25)."""
    task_dir = _coding_task_with_oracle(tmp_path, "hint: guard_against_empty_list_input()")
    report = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN, answer_sources=("solution/oracle.patch",))
    assert [f.check for f in report.failures] == ["answer-source-leaked"]
    assert "instruction.md:1" in report.failures[0].detail


def test_answer_sources_skip_patch_context_and_headers(tmp_path: Path) -> None:
    """A context line or a header of a `.patch` source is base text, not a leak."""
    task_dir = _coding_task_with_oracle(tmp_path, "def unchanged_context_helper():\n+++ b/src/sum_module.py")
    report = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN, answer_sources=("solution/oracle.patch",))
    assert report.failures == []


def test_answer_sources_replace_the_default_sources(tmp_path: Path) -> None:
    """Given `answer_sources`, only those files are mined; `None` keeps the default `solution/` prefix."""
    task_dir = _coding_task_with_oracle(tmp_path, "a_line_that_only_the_default_sources_mine")
    sources = ("solution/oracle.patch",)
    explicit = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN, answer_sources=sources)
    default = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN)
    assert explicit.failures == []
    assert [f.check for f in default.failures] == ["answer-source-leaked"]


def test_answer_sources_directory_prefix(tmp_path: Path) -> None:
    """A source that ends with `/` is a directory: every file under it is mined."""
    task_dir = tmp_path / "task"
    (task_dir / "tests" / "verify" / "tests").mkdir(parents=True)
    (task_dir / "tests" / "verify" / "tests" / "test_sum.py").write_text("assert running_total([1, 2]) == 3\n", encoding="utf-8")
    (task_dir / "instruction.md").write_text(f"assert running_total([1, 2]) == 3\n{CONTROL_TOKEN}\n", encoding="utf-8")
    sources = ("tests/verify/",)
    report = Seal.scan_agent_visible(task_dir, tokens=[], control_token=CONTROL_TOKEN, answer_sources=sources)
    assert [f.check for f in report.failures] == ["answer-source-leaked"]


def test_instruction_scan_reports_each_name_with_its_line(tmp_path: Path) -> None:
    """Each token found in `instruction.md` is one failing `instruction-names:<token>`, with its first line."""
    instruction = f"Fix it.\nsrc/sum.py breaks; see sum.py\nTotaller\n{CONTROL_TOKEN}\n"
    (tmp_path / "instruction.md").write_text(instruction, encoding="utf-8")
    tokens = ["src/sum.py", "sum.py", "Totaller", "absent_name"]
    report = Seal.scan_instruction(tmp_path, tokens=tokens, control_token=CONTROL_TOKEN)
    assert [(f.check, f.detail) for f in report.failures] == [
        ("instruction-names:src/sum.py", "'src/sum.py' at instruction.md:2"),
        ("instruction-names:sum.py", "'sum.py' at instruction.md:2"),
        ("instruction-names:Totaller", "'Totaller' at instruction.md:3"),
    ]


def test_instruction_scan_needs_a_word_boundary(tmp_path: Path) -> None:
    """`scan` does not match `scanner` or `rescan`, and `sum.py` does not match `checksum.py`."""
    (tmp_path / "instruction.md").write_text(f"The scanner and rescan; checksum.py.\n{CONTROL_TOKEN}\n", encoding="utf-8")
    report = Seal.scan_instruction(tmp_path, tokens=["scan", "sum.py"], control_token=CONTROL_TOKEN)
    assert report.failures == []


def test_instruction_scan_without_control_token_refuses(tmp_path: Path) -> None:
    """Without the control token in `instruction.md`, the scan refuses `scan-did-not-run`, even with a name present."""
    (tmp_path / "instruction.md").write_text("scan is broken\n", encoding="utf-8")
    report = Seal.scan_instruction(tmp_path, tokens=["scan"], control_token=CONTROL_TOKEN)
    assert [f.check for f in report.failures] == ["scan-did-not-run"]


def test_canary_absence_flagged(tmp_path: Path) -> None:
    """A canary token found anywhere under the tree is reported; absent, it passes.

    The ported control-token half of ``check_repo`` (FR-36/NFR-6): a canary that *should* be absent
    is asserted absent, and its presence is a finding.
    """
    repo = RepoBuilder.build_sealed_repo(tmp_path / "sealed")
    (repo / "notes.txt").write_text("leftover SECRET_CANARY_TOKEN here\n", encoding="utf-8")
    report = Seal.check_repo(repo, canaries=("SECRET_CANARY_TOKEN",))
    assert not _finding(report, "canary-absent:SECRET_CANARY_TOKEN").passed


def test_seal_makes_no_network_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither ``check_repo`` nor ``scan_agent_visible`` issues a ``gh`` or a git remote/network call.

    Catches the sealing path reaching the forge (NFR-2): sealing reads local files and the local
    ``.git`` only. Every subprocess argv is captured and asserted to be a local, read-only git verb.
    """
    calls: list[list[str]] = []
    real_run = subprocess.run

    def _recording_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(argv, (list, tuple)):
            calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _recording_run)

    repo = RepoBuilder.build_sealed_repo(tmp_path / "sealed")
    calls.clear()  # ignore the fixture's own build calls; measure only the seal path
    Seal.check_repo(repo, canaries=("X",))
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text(f"clean\n{CONTROL_TOKEN}\n", encoding="utf-8")
    Seal.scan_agent_visible(task_dir, tokens=["#1"], control_token=CONTROL_TOKEN)

    # `git remote` (bare, no subcommand) is a local listing, not a forge call; the network verbs are
    # the ones that actually reach a remote.
    network_verbs = {"fetch", "clone", "pull", "push", "ls-remote"}
    for argv in calls:
        assert "gh" not in Path(argv[0]).name, f"seal path invoked gh: {argv}"
        assert not (network_verbs & set(argv)), f"seal path issued a network git verb: {argv}"


def test_report_json_shape() -> None:
    """The JSON shape E-2 maps to the FR-2 refusal: ``{ok, repo, checks, findings:[...]}``."""
    report = Report(repo="/x")
    report.add("some-check", passed=False, detail="why", severity="error")
    payload = report.as_json()
    assert payload["ok"] is False
    assert payload["repo"] == "/x"
    assert payload["checks"] == 1
    assert payload["findings"] == [{"check": "some-check", "passed": False, "detail": "why", "severity": "error"}]
