"""Tests for ``eval-harvest capture --type coding`` (coding-eval-harvest C-1, Parts 2 and 3), in-process via ``Cli.run``.

The clone is :meth:`RepoBuilder.build_fix_history` with the C-1 capture scenarios, and git runs for real
on it. ``gh`` and ``glab`` are replaced by stubs that answer from the recorded payloads under
``tests/fixtures/gh/fix_capture/`` and ``tests/fixtures/glab/recorded/`` and count every call, so
"one PR call plus one call for each linked issue" (NFR-4) is measured, not assumed. No test calls a forge.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fixtures.repo_builder import GLAB_PAYLOAD_DIR, FixHistoryFixture, RepoBuilder

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.fix_candidate import FixCandidate
from eval_harvest.fix_survey import FIX_SURVEY_RELATIVE_PATH
from eval_harvest.forge import Forge, ForgeError
from eval_harvest.forge_host import ForgeHost
from eval_harvest.gitcmd import GitCommandRunner
from eval_harvest.gitlab import GitLab
from eval_harvest.riskmap import RISK_MAP_FILENAME

_FIX_CAPTURE_DIR = Path(__file__).parents[1] / "fixtures" / "gh" / "fix_capture"

#: The PR the recorded `gh pr view` template describes: two linked issues, plus one in another repository.
_RECORDED_PR = 101

#: A `gh` responder: the argv tail in, ``(code, stdout, stderr)`` out.
GhResponder = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """One fix-history fixture for the module; tests write only their own datasets."""
    return RepoBuilder.build_fix_history(tmp_path_factory.mktemp("capture-coding"), capture_scenarios=True)


def _repo(fixture: FixHistoryFixture) -> str:
    """The ``owner/name`` the CLI derives from the clone's local ``origin`` (its last two path segments)."""
    return ForgeHost.from_clone(fixture.clone).project_path


def _substitute(text: str, fixture: FixHistoryFixture) -> str:
    owner, name = _repo(fixture).split("/")
    text = text.replace("{{REPO_OWNER}}", owner).replace("{{REPO_NAME}}", name)
    for token, sha in fixture.substitutions.items():
        text = text.replace(f"{{{{{token}}}}}", sha)
    return text


def _pr_view(fixture: FixHistoryFixture, number: int, **overrides: Any) -> dict[str, Any]:
    """The `gh pr view --json` object for PR ``number``: the recorded template for 101, else built from the survey row."""
    if number == _RECORDED_PR:
        view: dict[str, Any] = json.loads(
            _substitute((_FIX_CAPTURE_DIR / "pr-view-101.json").read_text(encoding="utf-8"), fixture)
        )
    else:
        row = next(pr for pr in fixture.pull_requests if pr["number"] == number)
        view = {key: row.get(key) for key in ("number", "title", "body", "url", "mergeCommit", "mergedAt", "headRefName")}
        view["closingIssuesReferences"] = []
    view.update(overrides)
    return view


def _issue(fixture: FixHistoryFixture, number: int, *, pull_request: bool = False) -> dict[str, Any]:
    """The `gh api repos/<repo>/issues/<n>` object: a recorded one when there is one, else a minimal issue."""
    recorded = _FIX_CAPTURE_DIR / f"issue-{number}.json"
    if recorded.is_file():
        issue: dict[str, Any] = json.loads(_substitute(recorded.read_text(encoding="utf-8"), fixture))
    else:
        issue = {"html_url": f"https://github.com/{_repo(fixture)}/issues/{number}", "number": number, "title": f"issue {number}"}
        issue["body"] = None
    if pull_request:
        issue["pull_request"] = {"url": "…"}
    return issue


class _GhStub:
    """A recording `gh` stub: `pr view` answers from :meth:`_pr_view`, `api …/issues/<n>` from :meth:`_issue`."""

    def __init__(self, fixture: FixHistoryFixture) -> None:
        self.fixture = fixture
        self.calls: list[list[str]] = []
        self.pr_overrides: dict[int, dict[str, Any]] = {}
        self.pull_request_issues: set[int] = set()
        self.failing: set[str] = set()  # argv strings (joined by spaces) that exit 1

    def __call__(self, argv_tail: list[str], *, repo: str | None = None) -> tuple[int, str, str]:
        self.calls.append([*argv_tail, *(["--repo", repo] if repo else [])])
        joined = " ".join(argv_tail)
        if any(failing in joined for failing in self.failing):
            return 1, "", f"HTTP 502 for {joined}"
        if argv_tail[:2] == ["pr", "view"]:
            number = int(argv_tail[2])
            return 0, json.dumps(_pr_view(self.fixture, number, **self.pr_overrides.get(number, {}))), ""
        if argv_tail[0] == "api" and "/issues/" in argv_tail[1]:
            number = int(argv_tail[1].rsplit("/", 1)[1])
            return 0, json.dumps(_issue(self.fixture, number, pull_request=number in self.pull_request_issues)), ""
        raise AssertionError(f"unexpected gh call: {argv_tail}")


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch, fix_history: FixHistoryFixture) -> _GhStub:
    stub = _GhStub(fix_history)
    monkeypatch.setattr(GitCommandRunner, "gh", staticmethod(stub))
    return stub


def _capture(fixture: FixHistoryFixture, dataset: Path, *extra: str, clone: Path | None = None) -> int:
    """``capture <extra> --type coding --clone C --dataset D``; returns the exit code."""
    argv = ["capture", *extra, "--type", "coding", "--clone", str(clone or fixture.clone), "--dataset", str(dataset)]
    return Cli.run(argv)


def _survey(fixture: FixHistoryFixture, dataset: Path, tmp_path: Path, numbers: set[int] | None = None) -> Path:
    """Run the real `survey --type coding --from-json` into ``dataset`` and return the saved cache path."""
    rows = [pr for pr in fixture.pull_requests if numbers is None or pr["number"] in numbers]
    payload = tmp_path / "prs.json"
    payload.write_text(json.dumps(rows), encoding="utf-8")
    argv = ["survey", "--type", "coding", "--clone", str(fixture.clone), "--from-json", str(payload), "--dataset", str(dataset)]
    assert Cli.run(argv) == ExitCode.SUCCESS
    return dataset / FIX_SURVEY_RELATIVE_PATH


def _json_failure(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    """The one failure of a `--json` refusal on stdout."""
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and len(payload["failures"]) == 1
    failure: dict[str, str] = payload["failures"][0]
    assert set(failure) == {"check", "datapoint", "offending", "next"} and all(failure.values())
    return failure


# ───────────────────────────── single mode ─────────────────────────────


def test_capture_coding_writes_candidate_and_patches(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 0; the candidate and both patches exist; stdout lists the slots and ends with the `brief` command."""
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "101") == ExitCode.SUCCESS
    for relative in ("candidates/fix-101.json", "patches/fix-101/source.patch", "patches/fix-101/tests.patch"):
        assert (dataset / relative).is_file(), relative
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == str(dataset / "candidates" / "fix-101.json")
    assert "slots to fill:" in lines and "  - instruction" in lines and "  - environment.rationale" in lines
    assert lines[-1] == f"next: eval-harvest brief {dataset / 'candidates' / 'fix-101.json'}"
    candidate = FixCandidate.load(dataset / "candidates" / "fix-101.json")
    assert candidate["datapoint_type"] == "coding" and candidate["repo"] == _repo(fix_history)
    assert not (dataset / "candidates" / "pr-101.json").exists(), "the dispatch went to the review path"


def test_capture_coding_json_payload(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` prints the candidate path, the commits, the paths, the issue numbers, the unfilled slots, and `next`."""
    dataset = tmp_path / "ds"
    assert (
        Cli.run(["--json", "capture", "101", "--type", "coding", "--clone", str(fix_history.clone), "--dataset", str(dataset)])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == [
        "candidate",
        "pr_number",
        "base_commit",
        "merge_commit",
        "source_paths",
        "test_paths",
        "issues",
        "unfilled",
        "next",
    ]
    assert payload["merge_commit"] == fix_history.substitutions["MERGE_101"]
    assert payload["base_commit"] == fix_history.substitutions["BASE_101"]
    assert payload["issues"] == [42, 43]
    assert payload["unfilled"][0] == "instruction" and payload["next"].startswith("eval-harvest brief ")


def test_capture_coding_needs_no_risk_map(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """A dataset without `risk-map.toml` captures with exit 0: the coding path skips the review risk-map guard."""
    dataset = tmp_path / "no-risk-map"
    assert _capture(fix_history, dataset, "113") == ExitCode.SUCCESS
    assert not (dataset / RISK_MAP_FILENAME).exists()
    assert (dataset / "candidates" / "fix-113.json").is_file()


def test_capture_review_still_needs_risk_map(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """Without `--type`, capture is the review capture and refuses `missing-risk-map` before any forge call."""
    assert Cli.run(["capture", "101", "--clone", str(fix_history.clone), "--dataset", str(tmp_path / "ds")]) == ExitCode.REFUSAL
    assert gh.calls == []


def test_capture_coding_forge_call_count(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """One `pr view` call, then one call for each of the two linked issues — and nothing else (NFR-4).

    The recorded payload also references issue 5 of another repository; it is not read from this one.
    """
    assert _capture(fix_history, tmp_path / "ds", "101") == ExitCode.SUCCESS
    repo = _repo(fix_history)
    assert gh.calls == [
        [
            "pr",
            "view",
            "101",
            "--json",
            "number,title,body,url,mergeCommit,mergedAt,headRefName,closingIssuesReferences",
            "--repo",
            repo,
        ],
        ["api", f"repos/{repo}/issues/42"],
        ["api", f"repos/{repo}/issues/43"],
    ]
    assert not any(part.endswith(("/reviews", "/comments", "/timeline")) for call in gh.calls for part in call)


def test_capture_coding_makes_no_git_fetch(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git stub that fails on `fetch` does not fail the capture: no pull-head fetch is made."""
    real = GitCommandRunner.git_with_stderr
    fetches: list[tuple[str, ...]] = []

    def no_fetch(repo: Path, *args: str) -> tuple[int, str, str]:
        if args and args[0] == "fetch":
            fetches.append(args)
            return 128, "", "fatal: fetch is forbidden in this test"
        return real(repo, *args)

    monkeypatch.setattr(GitCommandRunner, "git_with_stderr", staticmethod(no_fetch))
    assert _capture(fix_history, tmp_path / "ds", "101") == ExitCode.SUCCESS
    assert fetches == []


def test_capture_coding_issue_fallback_from_text(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """With an empty `closingIssuesReferences` and "Fixes #12" in the body, the candidate has issue 12 (§4 risk)."""
    gh.pr_overrides[101] = {"closingIssuesReferences": [], "body": "Fixes #12", "headRefName": "scan-fix"}
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "101") == ExitCode.SUCCESS
    issues = FixCandidate.load(dataset / "candidates" / "fix-101.json")["issues"]
    assert [(issue["number"], issue["body"]) for issue in issues] == [(12, "")]


def test_capture_coding_skips_pull_request_reference(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """ "Fixes #12" where #12 is a PR gives no issue 12: PR text is not stored as issue text."""
    gh.pr_overrides[101] = {"closingIssuesReferences": [], "body": "Fixes #12 and #42", "headRefName": "scan-fix"}
    gh.pull_request_issues.add(12)
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "101") == ExitCode.SUCCESS
    assert [issue["number"] for issue in FixCandidate.load(dataset / "candidates" / "fix-101.json")["issues"]] == [42]


def test_capture_coding_refuses_base_unrecoverable(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A merge commit the clone lacks exits 3 with `fix-base-unrecoverable`, the four fields, and a `git fetch` next."""
    gh.pr_overrides[101] = {"mergeCommit": {"oid": "f" * 40}}
    dataset = tmp_path / "ds"
    code = Cli.run(["--json", "capture", "101", "--type", "coding", "--clone", str(fix_history.clone), "--dataset", str(dataset)])
    assert code == ExitCode.REFUSAL
    failure = _json_failure(capsys)
    assert (failure["check"], failure["datapoint"]) == ("fix-base-unrecoverable", "fix-101")
    assert "f" * 40 in failure["offending"]
    assert "`git fetch origin`" in failure["next"]
    assert not (dataset / "candidates").exists()


def test_capture_coding_refuses_no_source_change(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The tests-only fix (PR 102) exits 3 with `no-source-change`, and no candidate is written."""
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "102") == ExitCode.REFUSAL
    err = capsys.readouterr().err
    assert "refused: no-source-change" in err and "datapoint: fix-102" in err and "survey --type coding" in err
    assert not (dataset / "candidates" / "fix-102.json").exists()
    assert not (dataset / "patches").exists()


def test_capture_coding_forge_unavailable_single(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`gh` exits 1: the capture exits 5 with `forge-unavailable`, not a refusal of the PR."""
    gh.failing.add("pr view")
    assert _capture(fix_history, tmp_path / "ds", "101") == ExitCode.RUNTIME_UNAVAILABLE
    err = capsys.readouterr().err
    assert "refused: forge-unavailable" in err and "gh auth status" in err and "HTTP 502" in err


def test_capture_coding_writes_refusal_log(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """A refusal is kept in `refusals/coding/fix-<n>.json` with `verb = capture`; a later success removes it (FR-33)."""
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "102") == ExitCode.REFUSAL
    record = json.loads((dataset / "refusals" / "coding" / "fix-102.json").read_text(encoding="utf-8"))
    assert set(record) == {"check", "datapoint", "offending", "next", "verb"}
    assert (record["check"], record["datapoint"], record["verb"]) == ("no-source-change", "fix-102", "capture")

    gh.pr_overrides[101] = {"mergeCommit": {"oid": "f" * 40}}
    assert _capture(fix_history, dataset, "101") == ExitCode.REFUSAL
    refused = dataset / "refusals" / "coding" / "fix-101.json"
    assert json.loads(refused.read_text(encoding="utf-8"))["check"] == "fix-base-unrecoverable"
    del gh.pr_overrides[101]
    assert _capture(fix_history, dataset, "101") == ExitCode.SUCCESS
    assert not refused.exists()
    assert not (dataset / "refusals" / "review").exists()


def test_capture_coding_author_commits_matches_survey(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """Single mode without a cache computes the same `author_commits` as the survey row (§8: one definition).

    PR 113 is the true merge (the author is on the second parent); PR 114's author has many commits.
    """
    cache = json.loads(_survey(fix_history, tmp_path / "surveyed", tmp_path).read_text(encoding="utf-8"))
    by_number = {row["number"]: row["author_commits"] for row in cache["ranked"]}
    dataset = tmp_path / "no-cache"
    for number in (113, 114, 112):
        assert _capture(fix_history, dataset, str(number)) == ExitCode.SUCCESS
        assert FixCandidate.load(dataset / "candidates" / f"fix-{number}.json")["author_commits"] == by_number[number], number
    assert len(set(by_number.values())) > 1, "the rows must differ, or a constant would pass"


def test_capture_coding_single_reads_author_commits_from_cache(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a cache row for the PR, single mode takes `author_commits` from it and makes no `git log` pass."""
    dataset = tmp_path / "ds"
    cache_path = _survey(fix_history, dataset, tmp_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    next(row for row in cache["ranked"] if row["number"] == 114)["author_commits"] = 999
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    real = GitCommandRunner.git_with_stderr

    def no_log(repo: Path, *args: str) -> tuple[int, str, str]:
        assert args[0] != "log", "the mainline pass ran although the cache has the row"
        return real(repo, *args)

    monkeypatch.setattr(GitCommandRunner, "git_with_stderr", staticmethod(no_log))
    assert _capture(fix_history, dataset, "114") == ExitCode.SUCCESS
    assert FixCandidate.load(dataset / "candidates" / "fix-114.json")["author_commits"] == 999


def test_capture_coding_output_byte_identical_twice(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """Two captures give the same candidate and patch bytes (NFR-1)."""
    for name in ("a", "b"):
        assert _capture(fix_history, tmp_path / name, "101") == ExitCode.SUCCESS
    for relative in ("candidates/fix-101.json", "patches/fix-101/source.patch", "patches/fix-101/tests.patch"):
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes(), relative


# ───────────────────────────── batch mode ─────────────────────────────


def test_capture_coding_batch_continues_after_issue_failure(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Three ranked PRs; the issue call fails for the second. The first and third are captured; exit 3 (§7.3)."""
    dataset = tmp_path / "ds"
    cache = _survey(fix_history, dataset, tmp_path)
    ranked = [row["number"] for row in json.loads(cache.read_text(encoding="utf-8"))["ranked"]]
    first, second, third = ranked[:3]
    assert second == _RECORDED_PR, "the second ranked PR is the one whose issue call fails"
    gh.failing.add("/issues/42")
    capsys.readouterr()
    assert _capture(fix_history, dataset, "--from-survey", str(cache), "--limit", "3") == ExitCode.REFUSAL
    assert (dataset / "candidates" / f"fix-{first}.json").is_file()
    assert (dataset / "candidates" / f"fix-{third}.json").is_file()
    assert not (dataset / "candidates" / f"fix-{second}.json").exists()
    out = capsys.readouterr().out
    assert "3 attempted · 2 captured · 1 refused" in out and f"fix-{second}  forge-unavailable" in out
    assert json.loads((dataset / "refusals" / "coding" / f"fix-{second}.json").read_text(encoding="utf-8"))["verb"] == "capture"
    assert [call[2] for call in gh.calls if call[:2] == ["pr", "view"]] == [str(number) for number in ranked[:3]]


def test_capture_coding_batch_never_takes_excluded(fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path) -> None:
    """A cache with two ranked PRs and one excluded PR captures only the two ranked PRs, in rank order."""
    dataset = tmp_path / "ds"
    cache = _survey(fix_history, dataset, tmp_path, numbers={101, 113, 102})
    document = json.loads(cache.read_text(encoding="utf-8"))
    assert [row["number"] for row in document["excluded"]] == [102]
    assert _capture(fix_history, dataset, "--from-survey", str(cache)) == ExitCode.SUCCESS
    assert sorted(path.name for path in (dataset / "candidates").glob("*.json")) == ["fix-101.json", "fix-113.json"]
    assert [call[2] for call in gh.calls if call[:2] == ["pr", "view"]] == [str(row["number"]) for row in document["ranked"]]


def test_capture_coding_batch_pr_filter_and_excluded_pr(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--pr` narrows to the named ranked PR; a `--pr` naming only an excluded PR has no target (`no-clean-pr`)."""
    dataset = tmp_path / "ds"
    cache = _survey(fix_history, dataset, tmp_path)
    assert _capture(fix_history, dataset, "--from-survey", str(cache), "--pr", "113") == ExitCode.SUCCESS
    assert sorted(path.name for path in (dataset / "candidates").glob("*.json")) == ["fix-113.json"]
    capsys.readouterr()
    assert _capture(fix_history, dataset, "--from-survey", str(cache), "--pr", "102") == ExitCode.REFUSAL
    err = capsys.readouterr().err
    assert "refused: no-clean-pr" in err and "--excluded" in err


def test_capture_coding_batch_unknown_pr(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--pr 9999` refuses `unknown-pr` (exit 3) before any forge call."""
    dataset = tmp_path / "ds"
    cache = _survey(fix_history, dataset, tmp_path)
    assert _capture(fix_history, dataset, "--from-survey", str(cache), "--pr", "9999") == ExitCode.REFUSAL
    assert "refused: unknown-pr" in capsys.readouterr().err
    assert gh.calls == []


def test_capture_coding_batch_rejects_review_payload(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A review `survey --json` payload is not a coding cache: exit 2, at another path or at the cache path."""
    dataset = tmp_path / "ds"
    review_payload = {"repo": "acme/widgets", "prs": [{"number": 101, "harvestable": True}], "blocker_histogram": {}}
    elsewhere = tmp_path / "survey.json"
    elsewhere.write_text(json.dumps(review_payload), encoding="utf-8")
    assert _capture(fix_history, dataset, "--from-survey", str(elsewhere)) == ExitCode.USAGE
    cache_path = dataset / FIX_SURVEY_RELATIVE_PATH
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(review_payload), encoding="utf-8")
    assert _capture(fix_history, dataset, "--from-survey", str(cache_path)) == ExitCode.USAGE
    err = capsys.readouterr().err
    assert err.count("needs `<dataset>/survey/fix-survey.json` from `survey --type coding`") == 2
    assert gh.calls == []


# ───────────────────────────── GitLab ─────────────────────────────


def _gitlab_clone(fixture: FixHistoryFixture, tmp_path: Path) -> Path:
    """A local copy of the fixture clone whose `origin` is a gitlab.com URL, so the CLI picks GitLab."""
    clone = tmp_path / "gitlab-clone"
    code, _ = GitCommandRunner.git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(fixture.clone), str(clone))
    assert code == 0
    assert GitCommandRunner.git(clone, "remote", "set-url", "origin", "https://gitlab.com/acme/widgets.git")[0] == 0
    return clone


def _stub_glab(monkeypatch: pytest.MonkeyPatch, merge_request: dict[str, Any] | None) -> list[list[str]]:
    """A `glab` stub answering every GraphQL query with ``merge_request`` (and no issue); returns the calls."""
    calls: list[list[str]] = []

    def fake_glab(argv_tail: list[str]) -> tuple[int, str, str]:
        calls.append(argv_tail)
        return 0, json.dumps({"data": {"project": {"mergeRequest": merge_request}}}), ""

    monkeypatch.setattr(GitCommandRunner, "glab", staticmethod(fake_glab))
    return calls


def _recorded_fix_merge_request() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((GLAB_PAYLOAD_DIR / "recorded" / "mr-3978-fix.json").read_text(encoding="utf-8"))
    merge_request: dict[str, Any] = loaded["data"]["project"]["mergeRequest"]
    return merge_request


def test_capture_coding_gitlab_squash_refuses(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A GitLab squash MR with no `mergeCommitSha` refuses `fix-base-unrecoverable`, saying GitLab does not expose it."""
    node = {**_recorded_fix_merge_request(), "iid": "101", "mergeCommitSha": None, "squashOnMerge": True, "description": ""}
    node["title"] = "fix: handle an empty list in scan()"
    calls = _stub_glab(monkeypatch, node)
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "101", clone=_gitlab_clone(fix_history, tmp_path)) == ExitCode.REFUSAL
    err = capsys.readouterr().err
    assert "refused: fix-base-unrecoverable" in err and "GitLab does not expose the squash commit" in err
    assert len(calls) == 1 and not (dataset / "candidates").exists()


def test_capture_coding_gitlab_merge_commit(
    fix_history: FixHistoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GitLab MR with a `mergeCommitSha` in the clone captures from it, with one GraphQL call."""
    node = {
        **_recorded_fix_merge_request(),
        "iid": "113",
        "mergeCommitSha": fix_history.substitutions["MERGE_113"],
        "description": "",
    }
    node["title"] = "Handle a merge conflict marker"
    node["sourceBranch"] = "topic"
    calls = _stub_glab(monkeypatch, node)
    dataset = tmp_path / "ds"
    assert _capture(fix_history, dataset, "113", clone=_gitlab_clone(fix_history, tmp_path)) == ExitCode.SUCCESS
    candidate = FixCandidate.load(dataset / "candidates" / "fix-113.json")
    assert (candidate["repo"], candidate["base_commit"]) == ("acme/widgets", fix_history.substitutions["BASE_113"])
    assert candidate["pr_url"] == "https://gitlab.com/gitlab-org/cli/-/merge_requests/3978"
    assert len(calls) == 1


# ───────────────────────────── the forge reads (Part 2) ─────────────────────────────


def test_fetch_fix_pull_request_reshapes_recorded_gh_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorded `gh pr view` of strands-agents/evals#338 reshapes to the forge-neutral fix dict."""
    recorded = (_FIX_CAPTURE_DIR / "pr-view-338.json").read_text(encoding="utf-8")
    monkeypatch.setattr(GitCommandRunner, "gh", staticmethod(lambda argv_tail, *, repo=None: (0, recorded, "")))
    fix = Forge.fetch_fix_pull_request("strands-agents/evals", 338)
    assert list(fix) == ["number", "title", "body", "url", "merge_commit", "merged_at", "head_ref_name", "closing_issue_numbers"]
    assert fix["merge_commit"] == "cc0a39165677f2918a110bd97433d055bc4306aa"
    assert fix["closing_issue_numbers"] == [337]
    assert (fix["merged_at"], fix["head_ref_name"]) == ("2026-07-31T13:40:41Z", "tool-result-session")
    assert Forge.fetch_fix_pull_request("STRANDS-agents/Evals", 338)["closing_issue_numbers"] == [337]
    assert Forge.fetch_fix_pull_request("someone/fork", 338)["closing_issue_numbers"] == []


def test_fetch_fix_pull_request_null_body_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """`body: null` becomes ""; a non-zero exit or a body that is not a JSON object raises `ForgeError`."""
    responses = iter(
        [
            (0, json.dumps({"number": 1, "body": None, "mergeCommit": None}), ""),
            (1, "", "HTTP 404"),
            (0, "[]", ""),
            (0, "<html>", ""),
        ]
    )
    monkeypatch.setattr(GitCommandRunner, "gh", staticmethod(lambda argv_tail, *, repo=None: next(responses)))
    fix = Forge.fetch_fix_pull_request("acme/widgets", 1)
    assert (fix["body"], fix["merge_commit"], fix["closing_issue_numbers"]) == ("", "", [])
    for _ in range(3):
        with pytest.raises(ForgeError):
            Forge.fetch_fix_pull_request("acme/widgets", 1)


def test_fetch_fix_merge_request_reshapes_recorded_glab_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorded gitlab.com response for gitlab-org/cli!3978 reshapes to the same keys as the GitHub dict."""
    calls = _stub_glab(monkeypatch, _recorded_fix_merge_request())
    remote = ForgeHost.resolve_remote_url("https://gitlab.com/gitlab-org/cli.git")
    fix = GitLab.fetch_fix_merge_request(remote, 3978)
    assert list(fix) == ["number", "title", "body", "url", "merge_commit", "merged_at", "head_ref_name", "closing_issue_numbers"]
    assert fix["merge_commit"] == "1f64f7d44918f5d42926bde4616ba786a021a159"
    assert (fix["number"], fix["head_ref_name"], fix["closing_issue_numbers"]) == (3978, "test-mock-os-keyring", [])
    assert calls[0][:4] == ["api", "graphql", "--hostname", "gitlab.com"]
    assert "-f" in calls[0] and "iid=3978" in calls[0]


def test_fetch_fix_merge_request_missing_mr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project with no such MR raises `ForgeError` naming the MR."""
    _stub_glab(monkeypatch, None)
    remote = ForgeHost.resolve_remote_url("https://gitlab.com/gitlab-org/cli.git")
    with pytest.raises(ForgeError, match="!3978"):
        GitLab.fetch_fix_merge_request(remote, 3978)


def test_capture_coding_batch_needs_the_dataset_cache_path(
    fix_history: FixHistoryFixture, gh: _GhStub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A copy of a valid coding cache at another path is refused (exit 2): the batch reads only the dataset's own cache."""
    dataset = tmp_path / "ds"
    cache = _survey(fix_history, dataset, tmp_path)
    copy_path = tmp_path / "copy-of-fix-survey.json"
    copy_path.write_bytes(cache.read_bytes())
    assert _capture(fix_history, dataset, "--from-survey", str(copy_path)) == ExitCode.USAGE
    assert f"{copy_path} is not {dataset / FIX_SURVEY_RELATIVE_PATH}" in capsys.readouterr().err
    assert gh.calls == []
