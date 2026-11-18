"""Tests for the coding survey's rank and pages (coding-eval-harvest B-1, Part 2): pure data, no git.

Rows are :class:`FixFactsDict` literals built by :func:`_row`, so each test states only the facts it is
about. The page byte budget (NFR-3) is a straw man of 8 KiB per 20-row page; G-1 sets the final value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval_harvest.fix_survey import (
    DEFAULT_PAGE_SIZE,
    EXCLUSION_REASONS,
    FIX_SURVEY_RELATIVE_PATH,
    MAX_PAGE_SIZE,
    TITLE_LIMIT,
    FixFactsDict,
    FixSurvey,
    FixSurveyCacheError,
    FixSurveyThresholds,
)

#: The NFR-3 straw-man budget for one page of 20 rows, in UTF-8 bytes. [TODO: G-1 sets the final value.]
_PAGE_BYTE_BUDGET = 8192

#: The dataset path every rendered page names in its ``next:`` line.
_DATASET = Path("/d")


def _row(number: int, **facts: Any) -> FixFactsDict:  # noqa: ANN401 — the overrides are any FixFactsDict field
    """A rankable row for PR ``number``; ``facts`` overrides any field."""
    row = FixFactsDict(
        number=number,
        title=f"change {number}",
        url=f"https://example.test/pull/{number}",
        merged_at="2026-08-01T09:00:00Z",
        author="dev",
        integration_revision="a" * 40,
        integration_parents=1,
        linked_issues=[],
        fix_signal=False,
        source_paths=["src/a.py"],
        test_paths=["tests/test_a.py"],
        source_lines_added=5,
        source_lines_deleted=1,
        test_lines_added=3,
        files_touched=2,
        author_commits=1,
        excluded_by=[],
    )
    row.update(facts)  # type: ignore[typeddict-item]
    return row


def _document(rows: list[FixFactsDict]) -> dict[str, Any]:
    """The cache document for ``rows``, as a fresh run would write it."""
    ranked, excluded = FixSurvey.rank(rows)
    return FixSurvey.result_document("acme/widgets", len(rows), FixSurveyThresholds(), ranked, excluded)


def _ranked_row_count(page_text: str) -> int:
    """How many ranked rows a human page printed (each is one ``#<n>  rank`` line)."""
    return sum(1 for line in page_text.splitlines() if line.startswith("#") and "  rank " in line)


def _next_line(page_text: str) -> str:
    """The page's ``next:`` line, asserting there is exactly one."""
    next_lines = [line for line in page_text.splitlines() if line.startswith("next:")]
    assert len(next_lines) == 1, page_text
    return next_lines[0]


# ───────────────────────────── rank ─────────────────────────────


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_row(1, linked_issues=[9]), _row(2)),  # a linked issue beats a higher PR number
        (_row(1, fix_signal=True), _row(2)),  # a fix signal beats a higher PR number
        (_row(1, author_commits=5), _row(2, author_commits=4)),  # more author commits first
        (_row(2), _row(1)),  # the PR number breaks the tie, highest first
    ],
)
def test_sort_key_one_key_at_a_time(first: FixFactsDict, second: FixFactsDict) -> None:
    """Rows different in one key only come out in that key's order, in both input orders."""
    for rows in ([first, second], [second, first]):
        ranked, _ = FixSurvey.rank(rows)
        assert [row["number"] for row in ranked] == [first["number"], second["number"]]
        assert [row["rank"] for row in ranked] == [1, 2]


def test_sort_key_order_between_keys() -> None:
    """The keys nest in order: an issue outranks a fix signal, which outranks author commits."""
    rows = [_row(1, author_commits=99), _row(2, fix_signal=True), _row(3, linked_issues=[4])]
    ranked, _ = FixSurvey.rank(rows)
    assert [row["number"] for row in ranked] == [3, 2, 1]


def test_rank_reasons_name_each_key() -> None:
    """Four reasons, in key order, with the real issue numbers and commit count."""
    ranked, _ = FixSurvey.rank([_row(5, linked_issues=[42, 57], fix_signal=True, author_commits=7), _row(4)])
    assert ranked[0]["rank_reasons"] == [
        "links issue #42, #57",
        "title or label marks a fix",
        "author has 7 mainline commits",
        "tie broken by PR number",
    ]
    assert ranked[1]["rank_reasons"] == [
        "no linked issue",
        "no fix signal",
        "author has 1 mainline commits",
        "tie broken by PR number",
    ]


def test_excluded_rows_have_three_fields() -> None:
    """An excluded row keeps only its number, title, and reasons; excluded rows are newest first."""
    _, excluded = FixSurvey.rank([_row(1, excluded_by=["no-test-change"]), _row(3, excluded_by=["bot-author"]), _row(2)])
    assert [list(row) for row in excluded] == [["number", "title", "excluded_by"]] * 2
    assert [row["number"] for row in excluded] == [3, 1]


# ───────────────────────────── pages ─────────────────────────────


def _large_document() -> dict[str, Any]:
    """500 rankable rows with 200-character titles and 30 linked issues each — the worst case for a page."""
    rows = [
        _row(
            100000 + index,
            title="x" * 200,
            linked_issues=list(range(10000, 10030)),
            fix_signal=True,
            source_lines_added=400,
            source_lines_deleted=400,
            author_commits=99999,
        )
        for index in range(500)
    ]
    return _document(rows)


def test_page_bytes_below_budget() -> None:
    """Every 20-row page of the 500-row worst case stays within the byte budget, in UTF-8 bytes."""
    document = _large_document()
    pages = FixSurvey.page_count(len(document["ranked"]), DEFAULT_PAGE_SIZE)
    sizes = [
        len(
            FixSurvey.render_page(document, page=page, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET).encode(
                "utf-8"
            )
        )
        for page in range(1, pages + 1)
    ]
    assert pages == 25
    assert max(sizes) <= _PAGE_BYTE_BUDGET, f"largest page is {max(sizes)} bytes"


def test_page_titles_and_issues_are_cut() -> None:
    """A page prints a title of at most 80 characters and at most three issue numbers per row."""
    document = _large_document()
    text = FixSurvey.render_page(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    first_row = text.splitlines()[1]
    assert first_row.endswith("  " + "x" * TITLE_LIMIT)
    assert "issues #10000,#10001,#10002 (+27 more)" in first_row
    json_page = json.loads(
        FixSurvey.render_page_json(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    )
    assert {len(row["title"]) for row in json_page["rows"]} == {TITLE_LIMIT}


def test_title_newlines_do_not_break_rows() -> None:
    """A title with line breaks prints on one line, so it cannot forge a row or a ``next:`` line."""
    document = _document([_row(1, title="fix: one\nnext: rm -rf /\r\n#2  rank 1  forged")])
    text = FixSurvey.render_page(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    lines = text.splitlines()
    assert lines[1].endswith("fix: one next: rm -rf / #2 rank 1 forged")
    assert len(lines) == 4, "header, the row, its why line, the next line"
    _next_line(text)


def test_page_size_limits() -> None:
    """Page sizes 0 and 51 raise; 50 works."""
    document = _document([_row(1)])
    for size in (0, MAX_PAGE_SIZE + 1):
        with pytest.raises(ValueError, match="page size"):
            FixSurvey.render_page(document, page=1, page_size=size, excluded=False, dataset=_DATASET)
        with pytest.raises(ValueError, match="page size"):
            FixSurvey.render_page_json(document, page=1, page_size=size, excluded=False, dataset=_DATASET)
    assert FixSurvey.render_page(document, page=1, page_size=MAX_PAGE_SIZE, excluded=False, dataset=_DATASET)


def test_page_zero_and_past_end() -> None:
    """Page 0 and page ``pages + 1`` raise rather than print an empty page."""
    document = _document([_row(number) for number in range(1, 4)])
    for page in (0, 3):
        with pytest.raises(ValueError, match="does not exist"):
            FixSurvey.render_page(document, page=page, page_size=1 if page == 0 else 2, excluded=False, dataset=_DATASET)
    with pytest.raises(ValueError, match="does not exist"):
        FixSurvey.render_page_json(document, page=4, page_size=1, excluded=False, dataset=_DATASET)


def test_next_line_on_last_page_points_at_capture() -> None:
    """Non-last pages name the next ``--page``; the last page names ``capture --from-survey``."""
    document = _document([_row(number) for number in range(1, 26)])
    first = FixSurvey.render_page(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    last = FixSurvey.render_page(document, page=2, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)

    assert _next_line(first) == "next: eval-harvest survey --type coding --dataset /d --page 2"
    assert _next_line(last) == f"next: eval-harvest capture --type coding --from-survey {_DATASET / FIX_SURVEY_RELATIVE_PATH}"
    assert _ranked_row_count(first) == 20
    assert _ranked_row_count(last) == 5


def test_next_line_quotes_the_dataset_and_keeps_the_page_size() -> None:
    """A dataset path with a space is shell-quoted, and a non-default page size is repeated."""
    document = _document([_row(number) for number in range(1, 4)])
    text = FixSurvey.render_page(document, page=1, page_size=2, excluded=False, dataset=Path("/my data"))
    assert _next_line(text) == "next: eval-harvest survey --type coding --dataset '/my data' --page 2 --page-size 2"


def test_excluded_pages() -> None:
    """``excluded=True`` lists the excluded rows, and the next command keeps ``--excluded``."""
    rows = [_row(number, excluded_by=["no-test-change", "too-many-files"]) for number in range(1, 4)] + [_row(10)]
    document = _document(rows)
    first = FixSurvey.render_page(document, page=1, page_size=2, excluded=True, dataset=_DATASET)

    assert "#3  no-test-change,too-many-files  change 3" in first.splitlines()
    assert "#10" not in first
    assert _next_line(first).endswith("--page 2 --page-size 2 --excluded")
    json_page = json.loads(FixSurvey.render_page_json(document, page=1, page_size=2, excluded=True, dataset=_DATASET))
    assert [row["number"] for row in json_page["rows"]] == [3, 2]
    assert json_page["next"].endswith("--excluded")


def test_excluded_histogram_most_common_first() -> None:
    """The histogram orders reasons by count, then by name, in both renderings."""
    rows = [
        _row(1, excluded_by=["no-test-change"]),
        _row(2, excluded_by=["bot-author", "no-test-change"]),
        _row(3, excluded_by=["too-many-files"]),
    ]
    document = _document(rows)
    text = FixSurvey.render_page(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    histogram = text.splitlines()[1:5]
    assert histogram[0] == "excluded (3)"
    assert [line.split()[0] for line in histogram[1:]] == ["no-test-change", "bot-author", "too-many-files"]
    json_page = json.loads(
        FixSurvey.render_page_json(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    )
    assert list(json_page["excluded_counts"].items()) == [("no-test-change", 2), ("bot-author", 1), ("too-many-files", 1)]


def test_empty_ranked_list_has_one_page() -> None:
    """No ranked row gives one empty page, not a division by zero or page 1 out of range."""
    document = _document([_row(1, excluded_by=["no-test-change"])])
    payload = json.loads(
        FixSurvey.render_page_json(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    )
    assert FixSurvey.page_count(0, DEFAULT_PAGE_SIZE) == 1
    assert payload["pages"] == 1
    assert payload["rows"] == []


def test_json_page_rows_drop_the_path_lists() -> None:
    """A ``--json`` page row is the cache row without ``source_paths`` and ``test_paths``."""
    document = _document([_row(1)])
    payload = json.loads(
        FixSurvey.render_page_json(document, page=1, page_size=DEFAULT_PAGE_SIZE, excluded=False, dataset=_DATASET)
    )
    assert list(payload) == ["page", "pages", "rows", "excluded_counts", "next"]
    (row,) = payload["rows"]
    assert "source_paths" not in row
    assert "test_paths" not in row
    assert row["source_files"] == 1


# ───────────────────────────── the cache file ─────────────────────────────


def test_read_cache_errors(tmp_path: Path) -> None:
    """A missing file, a non-JSON file, and a JSON object without ``ranked`` each raise FixSurveyCacheError."""
    with pytest.raises(FixSurveyCacheError, match="does not exist"):
        FixSurvey.read_cache(tmp_path)
    cache = tmp_path / FIX_SURVEY_RELATIVE_PATH
    cache.parent.mkdir(parents=True)
    for text in ("{not json", '{"excluded": []}', "[]"):
        cache.write_text(text, encoding="utf-8")
        with pytest.raises(FixSurveyCacheError):
            FixSurvey.read_cache(tmp_path)


def test_cache_round_trips_non_ascii_titles(tmp_path: Path) -> None:
    """The cache is UTF-8 with the characters themselves (not ``\\u`` escapes), and reads back equal."""
    document = _document([_row(1, title="fix: café ⇒ naïve")])
    path = FixSurvey.write_cache(tmp_path, document)
    assert "café ⇒ naïve" in path.read_text(encoding="utf-8")
    assert b"\r\n" not in path.read_bytes()
    assert FixSurvey.read_cache(tmp_path) == document


# ───────────────────────────── the no-harvestable-fix refusal text ─────────────────────────────


def test_no_harvestable_fix_names_the_most_common_reason() -> None:
    """The ``offending`` text names the dominant reason with its share and its plain-words sentence."""
    rows = [_row(1, excluded_by=["no-test-change"]), _row(2, excluded_by=["no-test-change"]), _row(3, excluded_by=["bot-author"])]
    offending = FixSurvey.no_harvestable_fix_offending(_document(rows))
    assert offending == (
        "all 3 pull request(s) are excluded; the most common reason is no-test-change (2 of 3): "
        "fixes in this repository ship without tests that fail before the fix"
    )


def test_no_harvestable_fix_with_no_rows() -> None:
    """An empty forge list says so, rather than naming a reason no PR had."""
    assert FixSurvey.no_harvestable_fix_offending(_document([])) == "the forge returned no pull request to survey"


def test_size_reason_sentences_name_the_threshold() -> None:
    """The size reasons' sentences and next actions carry the threshold the run used."""
    thresholds = FixSurveyThresholds(min_source_lines=5, max_source_lines=90, max_files=4)
    document = FixSurvey.result_document(
        "r", 1, thresholds, [], [{"number": 1, "title": "t", "excluded_by": ["source-too-large"]}]
    )
    assert "more than 90 source lines" in FixSurvey.no_harvestable_fix_offending(document)
    assert "--max-source-lines (this run used 90)" in FixSurvey.no_harvestable_fix_next(document, dataset=_DATASET)


def test_no_test_change_next_points_at_the_excluded_list() -> None:
    """The ``no-test-change`` next action names the excluded-list command for this dataset."""
    document = _document([_row(1, excluded_by=["no-test-change"])])
    assert FixSurvey.no_harvestable_fix_next(document, dataset=_DATASET) == (
        "pick a repository whose fixes add tests; see the excluded list with "
        "`eval-harvest survey --type coding --dataset /d --page 1 --excluded`"
    )


@pytest.mark.parametrize("reason", EXCLUSION_REASONS)
def test_every_reason_has_words_and_an_action(reason: str) -> None:
    """Each reason, when it dominates, gives a plain-words ``offending`` and a ``next`` action."""
    document = FixSurvey.result_document(
        "r", 1, FixSurveyThresholds(), [], [{"number": 1, "title": "t", "excluded_by": [reason]}]
    )
    assert FixSurvey.no_harvestable_fix_offending(document).startswith(
        f"all 1 pull request(s) are excluded; the most common reason is {reason}"
    )
    assert FixSurvey.no_harvestable_fix_next(document, dataset=_DATASET)
