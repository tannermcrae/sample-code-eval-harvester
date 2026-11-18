"""Tests for `refusal_log` — the per-datapoint refusal files the `dataset` report reads (FR-33).

The log is only useful if it holds exactly the current reasons: one file per refused datapoint,
under its own type, replaced on a new refusal and removed on a success. Each test names the way
the report would go wrong if that failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_harvest.refusal_log import RefusalLog, RefusalRecordDict


def _record(datapoint: str, *, check: str = "no-emittable-iteration", verb: str = "capture") -> RefusalRecordDict:
    return RefusalRecordDict(check=check, datapoint=datapoint, offending="no recoverable tip", next="pick another PR", verb=verb)


def test_write_then_read_all_round_trips(tmp_path: Path) -> None:
    """A review and a coding record each come back under their own type, unchanged."""
    review = _record("pr-338/reject", check="finding-line-absent", verb="emit")
    coding = _record("acme/widgets", check="no-harvestable-fix", verb="survey")
    RefusalLog.write(tmp_path, "review", review)
    RefusalLog.write(tmp_path, "coding", coding)

    assert RefusalLog.read_all(tmp_path) == {"coding": [coding], "review": [review]}


def test_write_replaces_older_record(tmp_path: Path) -> None:
    """A second refusal for the same datapoint replaces the first: one file, the newer reason."""
    RefusalLog.write(tmp_path, "review", _record("pr-7", check="forge-unavailable"))
    path = RefusalLog.write(tmp_path, "review", _record("pr-7", check="no-emittable-iteration"))

    assert list(path.parent.iterdir()) == [path]
    assert RefusalLog.read_all(tmp_path)["review"] == [_record("pr-7", check="no-emittable-iteration")]
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_clear_removes_and_tolerates_missing(tmp_path: Path) -> None:
    """`clear` removes the record; clearing a datapoint that was never refused does not raise."""
    path = RefusalLog.write(tmp_path, "review", _record("pr-7"))
    RefusalLog.clear(tmp_path, "review", "pr-7")
    assert not path.exists()
    RefusalLog.clear(tmp_path, "review", "pr-7")
    RefusalLog.clear(tmp_path / "no-such-dataset", "coding", "pr-1")


def test_path_for_maps_slash_to_double_underscore(tmp_path: Path) -> None:
    """`/` becomes `__`, so a datapoint never makes a subdirectory `read_all` would not look in."""
    assert RefusalLog.path_for(tmp_path, "review", "pr-338/reject") == tmp_path / "refusals" / "review" / "pr-338__reject.json"
    assert RefusalLog.path_for(tmp_path, "coding", "acme/widgets") == tmp_path / "refusals" / "coding" / "acme__widgets.json"


@pytest.mark.parametrize(
    ("datapoint_type", "datapoint"),
    [("review", "../x"), ("review", ".hidden"), ("review", "a\\b"), ("review", ""), ("other", "pr-1")],
)
def test_path_for_rejects_escape(tmp_path: Path, datapoint_type: str, datapoint: str) -> None:
    """A datapoint or type that could write outside `refusals/<type>/` is refused."""
    with pytest.raises(ValueError):
        RefusalLog.path_for(tmp_path, datapoint_type, datapoint)


def test_read_all_rejects_malformed_file(tmp_path: Path) -> None:
    """A record that is not an object with the five string keys raises, naming the file."""
    bad = tmp_path / "refusals" / "review" / "pr-1.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pr-1.json"):
        RefusalLog.read_all(tmp_path)

    bad.write_text('{"check": "x", "datapoint": "pr-1", "offending": "o", "next": "n"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="pr-1.json"):
        RefusalLog.read_all(tmp_path)


def test_read_all_is_sorted(tmp_path: Path) -> None:
    """Records come back by datapoint, whatever order they were written in (NFR-1)."""
    for number in (9, 1, 5):
        RefusalLog.write(tmp_path, "review", _record(f"pr-{number}"))

    assert [record["datapoint"] for record in RefusalLog.read_all(tmp_path)["review"]] == ["pr-1", "pr-5", "pr-9"]


def test_read_all_on_a_dataset_without_refusals(tmp_path: Path) -> None:
    """An old dataset with no `refusals/` directory reads as empty lists for every type."""
    assert RefusalLog.read_all(tmp_path) == {"coding": [], "review": []}
