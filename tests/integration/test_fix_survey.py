"""Tests for the coding survey's per-PR facts and its saved result (coding-eval-harvest B-1, Parts 1 and 2).

Real git on :meth:`RepoBuilder.build_fix_history`: the facts the survey reports (which side of the fix a
path is on, a rename's new name, a binary file's ``-`` counts, a true merge's second parent) *are* git's
output, so a mock would only test the mock. The ``gh pr list`` rows are the recorded template under
``tests/fixtures/gh/fix_survey/`` with the fixture's real SHAs filled in.

To regenerate the golden after an intended change to the cache shape: build the fixture, run
``FixSurvey.survey`` + ``rank`` + ``result_document`` + ``write_cache``, and copy the file to
``fixtures/gh/fix_survey/expected-fix-survey.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

from eval_harvest.fix_survey import (
    EXCLUSION_REASONS,
    FixFactsDict,
    FixSurvey,
    FixSurveyThresholds,
    _SplitCounts,
)
from eval_harvest.gitcmd import GitCommandRunner
from eval_harvest.survey import Survey

#: The committed golden ``fix-survey.json`` for the fix-history fixture.
_GOLDEN_CACHE = Path(__file__).parents[1] / "fixtures" / "gh" / "fix_survey" / "expected-fix-survey.json"

#: The review survey's recorded payloads — A-1's TP-7 PR lives there.
_REVIEW_SURVEY_PAYLOAD_DIR = Path(__file__).parents[1] / "fixtures" / "gh" / "survey"

#: The fixture PR that carries each exclusion reason alone.
_REASON_SCENARIOS = {
    "not-merged": 108,
    "no-integration-commit": 109,
    "bot-author": 107,
    "no-source-change": 102,
    "no-test-change": 103,
    "source-too-small": 104,
    "source-too-large": 105,
    "too-many-files": 106,
}


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """The fix-history fixture, built once for the module (its repos are only read)."""
    return RepoBuilder.build_fix_history(tmp_path_factory.mktemp("fix-history"))


def _rows_by_number(fixture: FixHistoryFixture, prs: list[dict[str, Any]] | None = None) -> dict[int, FixFactsDict]:
    """Survey the fixture (or ``prs`` over its clone) and index the rows by PR number."""
    commits = Survey.mainline(fixture.clone, "main")
    rows = FixSurvey.survey(fixture.clone, prs or fixture.pull_requests, commits, FixSurveyThresholds())
    return {row["number"]: row for row in rows}


def _pull_request(fixture: FixHistoryFixture, number: int) -> dict[str, Any]:
    """A deep copy of one fixture ``gh`` row, safe to edit in a test."""
    row: dict[str, Any] = json.loads(json.dumps(next(pr for pr in fixture.pull_requests if pr["number"] == number)))
    return row


def _reviewed_pull_requests(clone: Path, round2_tip: str, base_sha: str) -> list[dict[str, Any]]:
    """The review survey's recorded ``reviewed.json`` rows with the squash fixture's real SHAs filled in."""
    text = (_REVIEW_SURVEY_PAYLOAD_DIR / "reviewed.json").read_text(encoding="utf-8")
    substitutions = {
        "MERGE_SHA": GitCommandRunner.git(clone, "rev-parse", "main")[1],
        "ROUND2_TIP": round2_tip,
        "BASE_SHA": base_sha,
    }
    for token, sha in substitutions.items():
        text = text.replace(f"{{{{{token}}}}}", sha)
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def _counts(source_lines: int, *, source_files: int = 1, test_files: int = 1) -> _SplitCounts:
    """A split with ``source_lines`` added lines over ``source_files`` source paths and ``test_files`` test paths."""
    return _SplitCounts(
        source_paths=[f"src/s{index}.py" for index in range(source_files)],
        test_paths=[f"tests/test_{index}.py" for index in range(test_files)],
        source_lines_added=source_lines,
        source_lines_deleted=0,
        test_lines_added=3 if test_files else 0,
    )


# ───────────────────────────── per-PR facts (Part 1) ─────────────────────────────


def test_clean_fix_facts(fix_history: FixHistoryFixture) -> None:
    """The clean PR's facts: two source files, ``tests/conftest.py`` as its one test, issue 42, a fix title."""
    row = _rows_by_number(fix_history)[101]

    assert list(row) == list(get_type_hints(FixFactsDict)), "every FixFactsDict field, in the fixed order"
    assert row["source_paths"] == ["code.py", "src/scan.py"]
    assert row["test_paths"] == ["tests/conftest.py"], "the shared splitter puts conftest.py on the test side"
    assert (row["source_lines_added"], row["source_lines_deleted"], row["test_lines_added"]) == (6, 1, 3)
    assert row["files_touched"] == 3
    assert row["linked_issues"] == [42]
    assert row["fix_signal"] is True
    assert row["integration_revision"] == fix_history.substitutions["MERGE_101"]
    assert row["integration_parents"] == 1
    assert row["excluded_by"] == []
    assert not any("@@" in str(value) or "diff --git" in str(value) for value in row.values()), "no diff text"


@pytest.mark.parametrize(("reason", "number"), sorted(_REASON_SCENARIOS.items()))
def test_each_reason_fires_alone(fix_history: FixHistoryFixture, reason: str, number: int) -> None:
    """Each scenario PR carries exactly its own reason — no gate also adds a size reason."""
    assert _rows_by_number(fix_history)[number]["excluded_by"] == [reason]


def test_every_reason_has_a_scenario() -> None:
    """The scenario table covers the reason tuple exactly, so a new reason cannot ship untested."""
    assert set(_REASON_SCENARIOS) == set(EXCLUSION_REASONS)


def test_reasons_keep_the_fixed_order() -> None:
    """A PR with several reasons lists them in the tuple's order, whatever order they were found in."""
    counts = _counts(500, source_files=13, test_files=0)
    assert FixSurvey._content_reasons(counts, FixSurveyThresholds(), bot=True) == [
        "bot-author",
        "no-test-change",
        "source-too-large",
        "too-many-files",
    ]


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (_counts(3), []),
        (_counts(400), []),
        (_counts(3, source_files=11, test_files=1), []),  # 12 files: equal to the limit
        (_counts(2), ["source-too-small"]),
        (_counts(401), ["source-too-large"]),
        (_counts(13, source_files=12, test_files=1), ["too-many-files"]),
    ],
)
def test_threshold_boundaries(counts: _SplitCounts, expected: list[str]) -> None:
    """3 and 400 source lines and 12 files are kept; 2, 401, and 13 are excluded (``<=``, not ``<``)."""
    assert FixSurvey._content_reasons(counts, FixSurveyThresholds(), bot=False) == expected


def test_test_change_that_only_deletes_is_no_test_change(fix_history: FixHistoryFixture) -> None:
    """A PR whose only test change deletes a test has no test that can fail before the fix."""
    row = _rows_by_number(fix_history)[110]
    assert row["test_paths"] == ["tests/test_only.py"]
    assert row["test_lines_added"] == 0
    assert row["excluded_by"] == ["no-test-change"]


def test_review_harvestable_pr_is_excluded_by_coding_survey(tmp_path: Path) -> None:
    """TP-7, coding half: A-1's reviewed PR with no test change is harvestable for review, excluded for coding."""
    fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
    prs = _reviewed_pull_requests(fixture.clone, fixture.round2_tip, fixture.base_sha)
    commits = Survey.mainline(fixture.clone, "main")

    review = Survey.build_payload(fixture.clone, prs, branch="main", followup_days=14, repo="")
    (coding,) = FixSurvey.survey(fixture.clone, prs, commits, FixSurveyThresholds())

    assert Survey.harvestable_numbers(review) == [fixture.pr_number]
    assert coding["excluded_by"] == ["no-test-change"]


def test_bot_author_is_excluded(fix_history: FixHistoryFixture) -> None:
    """Both bot shapes are excluded: gh's ``is_bot`` flag on a plain login, and a ``[bot]`` login with no flag."""
    flagged = _pull_request(fix_history, 114)
    flagged["author"] = {"login": "release-helper", "is_bot": True}
    app_login = _pull_request(fix_history, 112)
    app_login["author"] = {"login": "app/renovate"}
    rows = _rows_by_number(fix_history, [flagged, app_login, _pull_request(fix_history, 107)])

    assert rows[114]["excluded_by"] == ["bot-author"]
    assert rows[112]["excluded_by"] == ["bot-author"]
    assert rows[107]["author"] == "dependabot[bot]"
    assert rows[107]["excluded_by"] == ["bot-author"]


def test_true_merge_author_is_second_parent_author(fix_history: FixHistoryFixture) -> None:
    """The true merge counts the branch author's mainline commits (2), not the merger's (9 by then)."""
    row = _rows_by_number(fix_history)[113]
    assert row["integration_parents"] == 2
    assert row["source_paths"] == ["src/merged.py"], "the integrated effect is the first-parent diff"
    assert row["author_commits"] == 2


def test_author_commits_counts_by_email(fix_history: FixHistoryFixture) -> None:
    """PRs 104 and 112 share one email in two spellings and two names (and two logins): they count as 2."""
    rows = _rows_by_number(fix_history)
    assert rows[112]["author_commits"] == 2
    assert rows[104]["author_commits"] == 1, "a commit later than the integration commit is not counted"


def test_git_calls_per_pr_at_most_six(fix_history: FixHistoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-5: a squash PR and a true-merge PR each make at most 6 git calls; an unmerged PR makes none."""
    commits = Survey.mainline(fix_history.clone, "main")
    real_git_with_stderr = GitCommandRunner.git_with_stderr
    calls: list[tuple[str, ...]] = []

    def counting_git(repo: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return real_git_with_stderr(repo, *args)

    monkeypatch.setattr(GitCommandRunner, "git_with_stderr", staticmethod(counting_git))
    counted: dict[int, int] = {}
    for number in (101, 113, 108):
        calls.clear()
        FixSurvey.survey_one(_pull_request(fix_history, number), fix_history.clone, commits, FixSurveyThresholds())
        counted[number] = len(calls)

    assert 0 < counted[101] <= 6
    assert 0 < counted[113] <= 6
    assert counted[108] == 0


def test_rename_into_tests_counts_as_test(fix_history: FixHistoryFixture) -> None:
    """A rename into ``tests/`` is the new path on the test side, as capture's ``b/`` split will see it."""
    row = _rows_by_number(fix_history)[111]
    assert row["test_paths"] == ["tests/test_feature.py"]
    assert "src/feature.py" not in row["source_paths"]


def test_binary_file_counts_zero_lines(fix_history: FixHistoryFixture) -> None:
    """A binary file is a source path with 0 lines, not a crash on the ``-\\t-`` record."""
    row = _rows_by_number(fix_history)[112]
    assert "src/logo.png" in row["source_paths"]
    assert row["source_lines_added"] == 3, "only logo.py's three lines count"


def test_parse_numstat_records() -> None:
    """The ``-z`` parser: a plain record, a binary record, and a rename record that keeps the new path."""
    output = "3\t1\tsrc/a.py\0-\t-\tsrc/logo.png\0" + "0\t0\t\0src/old.py\0tests/test_new.py\0"
    parsed = FixSurvey.parse_numstat(output)
    assert [(entry.path, entry.added, entry.deleted) for entry in parsed] == [
        ("src/a.py", 3, 1),
        ("src/logo.png", 0, 0),
        ("tests/test_new.py", 0, 0),
    ]


# ───────────────────────────── the saved result (Part 2) ─────────────────────────────


def _write_fixture_cache(fixture: FixHistoryFixture, dataset: Path) -> Path:
    """Run the whole offline pipeline on the fixture and write the cache under ``dataset``."""
    commits = Survey.mainline(fixture.clone, "main")
    thresholds = FixSurveyThresholds()
    ranked, excluded = FixSurvey.rank(FixSurvey.survey(fixture.clone, fixture.pull_requests, commits, thresholds))
    return FixSurvey.write_cache(dataset, FixSurvey.result_document("acme/widgets", len(commits), thresholds, ranked, excluded))


def test_cache_is_byte_identical_twice(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """Two runs give the same bytes, equal to the golden — no set order, no timestamp (FR-10)."""
    first = _write_fixture_cache(fix_history, tmp_path / "first").read_bytes()
    second = _write_fixture_cache(fix_history, tmp_path / "second").read_bytes()

    assert first == second
    assert first == _GOLDEN_CACHE.read_bytes()


def test_cache_has_the_fixed_keys(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """The §8 keys, in order, at the top level and on every ranked and excluded row."""
    document = json.loads(_write_fixture_cache(fix_history, tmp_path).read_text(encoding="utf-8"))

    assert list(document) == ["repo", "mainline_commits", "thresholds", "ranked", "excluded"]
    assert document["thresholds"] == {"min_source_lines": 3, "max_source_lines": 400, "max_files": 12}
    assert [row["number"] for row in document["ranked"]] == [112, 101, 113, 114]
    assert list(document["ranked"][0]) == [
        "number",
        "title",
        "url",
        "merged_at",
        "rank",
        "linked_issues",
        "fix_signal",
        "source_files",
        "source_lines_added",
        "source_lines_deleted",
        "test_files",
        "files_touched",
        "author_commits",
        "rank_reasons",
        "source_paths",
        "test_paths",
    ]


def test_write_cache_replaces_an_old_file(fix_history: FixHistoryFixture, tmp_path: Path) -> None:
    """A fresh run overwrites the old cache rather than merging with it, and leaves no temporary file."""
    cache = tmp_path / "survey" / "fix-survey.json"
    cache.parent.mkdir(parents=True)
    cache.write_text('{"ranked": [{"number": 1}], "excluded": [], "stale": true}\n', encoding="utf-8")

    _write_fixture_cache(fix_history, tmp_path)

    assert "stale" not in json.loads(cache.read_text(encoding="utf-8"))
    assert sorted(path.name for path in cache.parent.iterdir()) == ["fix-survey.json"]
