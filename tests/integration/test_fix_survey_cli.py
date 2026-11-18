"""Tests for ``eval-harvest survey --type coding`` (coding-eval-harvest B-1, Part 3), in-process via ``Cli.run``.

The clone is :meth:`RepoBuilder.build_fix_history` and the forge list is its recorded payload, either
as a ``--from-json`` file or replayed behind a stubbed ``GitCommandRunner.gh`` for the ``--repo`` path —
no test calls GitHub. The page-request tests replace git, ``gh``, and ``glab`` with functions that raise,
which is how "a page makes no forge call and no git call" (FR-8, NFR-2) is shown rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.fix_survey import FIX_SURVEY_RELATIVE_PATH
from eval_harvest.forge import Forge
from eval_harvest.gitcmd import GitCommandRunner


@pytest.fixture
def fix_history(tmp_path: Path) -> FixHistoryFixture:
    """A fresh fix-history fixture per test (tests write datasets and refusals next to it)."""
    return RepoBuilder.build_fix_history(tmp_path / "repo")


def _payload(fixture: FixHistoryFixture, tmp_path: Path, prs: list[dict[str, Any]] | None = None) -> Path:
    """Write the fixture's ``gh`` rows (or ``prs``) as a ``--from-json`` file."""
    path = tmp_path / "prs.json"
    path.write_text(json.dumps(prs if prs is not None else fixture.pull_requests), encoding="utf-8")
    return path


def _fresh_run(fixture: FixHistoryFixture, tmp_path: Path, *extra: str, prs: list[dict[str, Any]] | None = None) -> int:
    """``survey --type coding --clone C --from-json P --dataset D`` plus ``extra``; returns the exit code."""
    payload = _payload(fixture, tmp_path, prs)
    argv = [
        "survey",
        "--type",
        "coding",
        "--clone",
        str(fixture.clone),
        "--from-json",
        str(payload),
        "--dataset",
        str(tmp_path / "ds"),
    ]
    return Cli.run([*argv, *extra])


def _forbid_git_and_forge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every git, gh, and glab call raise, so a page request that reaches one fails loudly."""

    def forbidden(*args: object, **kwargs: object) -> tuple[int, str, str]:
        raise AssertionError(f"a page request made a git or forge call: {args}")

    for name in ("git_with_stderr", "gh", "glab"):
        monkeypatch.setattr(GitCommandRunner, name, staticmethod(forbidden))


def _no_test_change_prs(fixture: FixHistoryFixture) -> list[dict[str, Any]]:
    """The fixture rows whose PRs are merged but add no test (PRs 103, 110, 111)."""
    return [pr for pr in fixture.pull_requests if pr["number"] in {103, 110, 111}]


def _replay_gh(monkeypatch: pytest.MonkeyPatch, fixture: FixHistoryFixture, calls: list[list[str]]) -> None:
    """Stub ``GitCommandRunner.gh`` to record its argv and answer with the fixture's rows."""
    rows = json.dumps(fixture.pull_requests)

    def replay(argv_tail: list[str], *, repo: str | None = None) -> tuple[int, str, str]:
        calls.append([*argv_tail, *(["--repo", repo] if repo else [])])
        return 0, rows, ""

    monkeypatch.setattr(GitCommandRunner, "gh", staticmethod(replay))


# ───────────────────────────── fresh run and pages ─────────────────────────────


def test_fresh_run_writes_the_cache_and_prints_page_one(
    fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh run writes ``<dataset>/survey/fix-survey.json`` and prints page 1 with one ``next:`` line."""
    code = _fresh_run(fix_history, tmp_path, "--page-size", "2")

    out = capsys.readouterr().out
    assert code == ExitCode.SUCCESS
    assert (tmp_path / "ds" / FIX_SURVEY_RELATIVE_PATH).is_file()
    assert out.startswith("fix survey: 4 ranked, 10 excluded, page 1 of 2\n")
    assert out.splitlines()[-1] == f"next: eval-harvest survey --type coding --dataset {tmp_path / 'ds'} --page 2 --page-size 2"


def test_page_two_is_offline(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a fresh run, ``--page 2`` succeeds with git, gh, and glab all replaced by functions that raise."""
    assert _fresh_run(fix_history, tmp_path, "--page-size", "2") == ExitCode.SUCCESS
    capsys.readouterr()
    _forbid_git_and_forge(monkeypatch)

    code = Cli.run(["survey", "--type", "coding", "--dataset", str(tmp_path / "ds"), "--page", "2", "--page-size", "2"])

    out = capsys.readouterr().out
    assert code == ExitCode.SUCCESS
    assert out.startswith("fix survey: 4 ranked, 10 excluded, page 2 of 2\n")
    assert out.splitlines()[-1].startswith("next: eval-harvest capture --type coding --from-survey ")


def test_page_request_without_clone(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The exact ``next:`` command page 1 prints runs as-is: no ``--clone`` needed."""
    assert _fresh_run(fix_history, tmp_path) == ExitCode.SUCCESS
    capsys.readouterr()

    assert Cli.run(["survey", "--type", "coding", "--dataset", str(tmp_path / "ds"), "--page", "1"]) == ExitCode.SUCCESS
    assert capsys.readouterr().out.startswith("fix survey: 4 ranked")


def test_review_still_requires_clone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A review survey with no ``--clone`` is still a usage error (exit 2), as argparse made it before."""
    payload = tmp_path / "prs.json"
    payload.write_text("[]", encoding="utf-8")

    assert Cli.run(["survey", "--from-json", str(payload)]) == ExitCode.USAGE
    assert "the following arguments are required: --clone" in capsys.readouterr().err


def test_fresh_coding_run_requires_clone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A coding run that reads the forge list needs a clone; only a page request can go without."""
    payload = tmp_path / "prs.json"
    payload.write_text("[]", encoding="utf-8")

    assert Cli.run(["survey", "--type", "coding", "--from-json", str(payload), "--dataset", str(tmp_path)]) == ExitCode.USAGE
    assert "--clone" in capsys.readouterr().err


def test_coding_requires_dataset(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--type coding`` with no ``--dataset`` is a usage error."""
    payload = _payload(fix_history, tmp_path)
    code = Cli.run(["survey", "--type", "coding", "--clone", str(fix_history.clone), "--from-json", str(payload)])
    assert code == ExitCode.USAGE
    assert "--dataset" in capsys.readouterr().err


def test_cache_missing_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--page 1`` on an empty dataset refuses ``survey-cache-missing`` (exit 3) with all four fields."""
    code = Cli.run(["survey", "--type", "coding", "--dataset", str(tmp_path), "--page", "1"])

    err = capsys.readouterr().err
    assert code == ExitCode.REFUSAL
    assert "refused: survey-cache-missing" in err
    for field in ("datapoint:", "offending:", "next:"):
        assert field in err
    assert "eval-harvest survey --type coding --clone <dir> --repo <org/name> --dataset" in err


def test_page_past_the_end_is_usage(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A page past the last one exits 2 and names how many pages there are."""
    assert _fresh_run(fix_history, tmp_path) == ExitCode.SUCCESS
    capsys.readouterr()

    assert Cli.run(["survey", "--type", "coding", "--dataset", str(tmp_path / "ds"), "--page", "2"]) == ExitCode.USAGE
    assert "page 2 does not exist; the survey has 1 page(s)" in capsys.readouterr().err


@pytest.mark.parametrize(("flag", "value"), [("--page-size", "0"), ("--page-size", "51"), ("--page", "0")])
def test_page_size_out_of_range_is_usage(tmp_path: Path, flag: str, value: str, capsys: pytest.CaptureFixture[str]) -> None:
    """``--page-size`` 0 and 51, and ``--page 0``, exit 2 before any file is read."""
    argv = ["survey", "--type", "coding", "--dataset", str(tmp_path), "--page", "1", flag, value]
    assert Cli.run(argv) == ExitCode.USAGE
    assert flag in capsys.readouterr().err


def test_json_page_shape(fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` prints ``{page, pages, rows, excluded_counts, next}``."""
    assert Cli.run(["--json", *_fresh_argv(fix_history, tmp_path)]) == ExitCode.SUCCESS

    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ["page", "pages", "rows", "excluded_counts", "next"]
    assert [row["number"] for row in payload["rows"]] == [112, 101, 113, 114]
    assert payload["excluded_counts"]["no-test-change"] == 3
    assert payload["next"].startswith("eval-harvest capture --type coding --from-survey ")


def _fresh_argv(fixture: FixHistoryFixture, tmp_path: Path) -> list[str]:
    """The fresh-run argv over the fixture payload, for a test that adds a top-level flag in front."""
    payload = _payload(fixture, tmp_path)
    return [
        "survey",
        "--type",
        "coding",
        "--clone",
        str(fixture.clone),
        "--from-json",
        str(payload),
        "--dataset",
        str(tmp_path / "ds"),
    ]


# ───────────────────────────── the no-harvestable-fix refusal ─────────────────────────────


def test_all_no_test_change_refuses_plain_words(
    fix_history: FixHistoryFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every merged PR without a test change refuses ``no-harvestable-fix`` (exit 3) in words an SDE can use."""
    code = _fresh_run(fix_history, tmp_path, prs=_no_test_change_prs(fix_history))

    captured = capsys.readouterr()
    assert code == ExitCode.REFUSAL
    assert captured.out == "", "no empty page on a refusal"
    assert "refused: no-harvestable-fix" in captured.err
    assert "fixes in this repository ship without tests that fail before the fix" in captured.err
    assert "no-test-change (3 of 3)" in captured.err


def test_refusal_is_recorded(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """The refusal is kept under ``<dataset>/refusals/coding/``, its file name free of ``/``."""
    payload = _payload(fix_history, tmp_path, _no_test_change_prs(fix_history))
    argv = ["survey", "--type", "coding", "--clone", str(fix_history.clone), "--from-json", str(payload)]
    code = Cli.run([*argv, "--repo", "acme/widgets", "--dataset", str(tmp_path / "ds")])

    record_path = tmp_path / "ds" / "refusals" / "coding" / "acme__widgets.json"
    assert code == ExitCode.REFUSAL
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["check"] == "no-harvestable-fix"
    assert record["datapoint"] == "acme/widgets"
    assert record["verb"] == "survey"
    assert "fixes in this repository ship without tests" in record["offending"]


def test_cache_written_before_refusal(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After the refusal, ``--page 1 --excluded`` lists every excluded PR, offline."""
    assert _fresh_run(fix_history, tmp_path, prs=_no_test_change_prs(fix_history)) == ExitCode.REFUSAL
    capsys.readouterr()
    _forbid_git_and_forge(monkeypatch)

    code = Cli.run(["survey", "--type", "coding", "--dataset", str(tmp_path / "ds"), "--page", "1", "--excluded"])

    out = capsys.readouterr().out
    assert code == ExitCode.SUCCESS
    assert [line.split()[0] for line in out.splitlines() if line.startswith("#")] == ["#111", "#110", "#103"]


def test_success_clears_an_old_refusal(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """A later successful survey of the same datapoint removes its refusal record, so the log stays current."""
    payload = _payload(fix_history, tmp_path, _no_test_change_prs(fix_history))
    base = [
        "survey",
        "--type",
        "coding",
        "--clone",
        str(fix_history.clone),
        "--repo",
        "acme/widgets",
        "--dataset",
        str(tmp_path / "ds"),
    ]
    assert Cli.run([*base, "--from-json", str(payload)]) == ExitCode.REFUSAL
    record_path = tmp_path / "ds" / "refusals" / "coding" / "acme__widgets.json"
    assert record_path.is_file()

    assert Cli.run([*base, "--from-json", str(_payload(fix_history, tmp_path))]) == ExitCode.SUCCESS
    assert not record_path.exists()


# ───────────────────────────── forge and git cost on the --repo path ─────────────────────────────


def test_no_pull_head_fetch_for_coding(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A coding ``--repo`` run with a replayed ``gh`` never fetches pull heads."""
    _replay_gh(monkeypatch, fix_history, [])
    fetched: list[str] = []
    monkeypatch.setattr(Forge, "fetch_refs", staticmethod(lambda *args, **kwargs: fetched.append("fetch")))

    code = Cli.run(
        ["survey", "--type", "coding", "--clone", str(fix_history.clone), "--repo", "acme/widgets", "--dataset", str(tmp_path)]
    )

    assert code == ExitCode.SUCCESS
    assert fetched == []


def test_one_forge_query_and_one_mainline_pass(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NFR-4: one ``gh pr list`` call and one ``git log --first-parent`` pass for the whole survey."""
    gh_calls: list[list[str]] = []
    _replay_gh(monkeypatch, fix_history, gh_calls)
    real_git_with_stderr = GitCommandRunner.git_with_stderr
    mainline_passes: list[tuple[str, ...]] = []

    def counting_git(repo: Path, *args: str) -> tuple[int, str, str]:
        if args[:2] == ("log", "--first-parent"):
            mainline_passes.append(args)
        return real_git_with_stderr(repo, *args)

    monkeypatch.setattr(GitCommandRunner, "git_with_stderr", staticmethod(counting_git))

    code = Cli.run(
        ["survey", "--type", "coding", "--clone", str(fix_history.clone), "--repo", "acme/widgets", "--dataset", str(tmp_path)]
    )

    assert code == ExitCode.SUCCESS
    assert len(gh_calls) == 1
    assert gh_calls[0][:2] == ["pr", "list"]
    assert len(mainline_passes) == 1


@pytest.mark.parametrize(
    ("extra", "expected_state"),
    [
        (["--type", "coding", "--dataset", "DATASET"], "merged"),
        ([], "all"),
        (["--type", "coding", "--dataset", "DATASET", "--state", "open"], "open"),
        (["--state", "open"], "open"),
    ],
)
def test_state_defaults(
    fix_history: FixHistoryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    expected_state: str,
) -> None:
    """Coding asks ``gh`` for merged PRs, review still asks for all, and an explicit ``--state`` wins for both."""
    gh_calls: list[list[str]] = []
    _replay_gh(monkeypatch, fix_history, gh_calls)
    arguments = [str(tmp_path / "ds") if item == "DATASET" else item for item in extra]

    Cli.run(["survey", "--clone", str(fix_history.clone), "--repo", "acme/widgets", "--no-fetch", *arguments])

    (call,) = gh_calls
    assert call[call.index("--state") + 1] == expected_state
