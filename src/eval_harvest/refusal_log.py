"""The persistent refusal log: one JSON file per refused datapoint, under ``<dataset>/refusals/<type>/``.

A refusal is printed to stderr (or into a batch summary) and then gone, so the ``dataset`` report
cannot say why each datapoint type lost what it lost (FR-33). This module keeps the last refusal
for each datapoint as a file: a verb writes a record when it refuses one datapoint, and removes it
when the same datapoint later succeeds, so the directory always holds the *current* reasons, never
a pile of stale ones.

Stdlib only, no project imports, so every verb — and the offline ``dataset`` report — can use it
without an import cycle (the same rule ``diffspan.py`` follows).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, TypedDict

#: The directory under a dataset that holds the refusal records.
REFUSALS_DIRNAME: Final = "refusals"

#: The datapoint types a refusal can be filed under, sorted (``read_all`` returns them in this order).
DATAPOINT_TYPES: Final = ("coding", "review")

#: The five string keys every record carries — the FR-2 four fields plus the verb that refused.
_RECORD_KEYS: Final = frozenset({"check", "datapoint", "offending", "next", "verb"})

#: What replaces ``/`` in a datapoint to make its file name — the task directory names' separator.
_SLASH_REPLACEMENT: Final = "__"


class RefusalRecordDict(TypedDict):
    """One refusal as stored on disk: the FR-2 four fields plus the verb that refused."""

    check: str
    datapoint: str
    offending: str
    next: str
    verb: str  # "survey" | "capture" | "emit"


class RefusalLog:
    """Writes, clears, and reads the per-datapoint refusal records. Holds no state."""

    @staticmethod
    def path_for(dataset_dir: Path, datapoint_type: str, datapoint: str) -> Path:
        """The record file for one datapoint: ``refusals/<type>/<datapoint with / as __>.json``.

        ``pr-338/reject`` → ``pr-338__reject.json``; ``acme/widgets`` → ``acme__widgets.json``. A
        name that is empty, hidden, or could leave ``refusals/<type>/`` raises ``ValueError``.
        """
        if datapoint_type not in DATAPOINT_TYPES:
            raise ValueError(f"datapoint type {datapoint_type!r} is not one of {DATAPOINT_TYPES}")
        stem = datapoint.replace("/", _SLASH_REPLACEMENT)
        if not stem or stem.startswith(".") or "\\" in stem or ".." in stem:
            raise ValueError(f"datapoint {datapoint!r} cannot name a refusal record file")
        return dataset_dir / REFUSALS_DIRNAME / datapoint_type / f"{stem}.json"

    @classmethod
    def write(cls, dataset_dir: Path, datapoint_type: str, record: RefusalRecordDict) -> Path:
        """Write ``record`` as the datapoint's current refusal, replacing an older one; return its path."""
        path = cls.path_for(dataset_dir, datapoint_type, record["datapoint"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @classmethod
    def clear(cls, dataset_dir: Path, datapoint_type: str, datapoint: str) -> None:
        """Remove the datapoint's refusal record after a success; a datapoint never refused is a no-op."""
        cls.path_for(dataset_dir, datapoint_type, datapoint).unlink(missing_ok=True)

    @classmethod
    def read_all(cls, dataset_dir: Path) -> dict[str, list[RefusalRecordDict]]:
        """Every record, by type, sorted by datapoint; a missing directory gives empty lists.

        A file that is not a JSON object with the five string keys raises ``ValueError`` naming the
        file: skipping it would hide a refusal from the report.
        """
        return {
            datapoint_type: cls._read_type(dataset_dir / REFUSALS_DIRNAME / datapoint_type) for datapoint_type in DATAPOINT_TYPES
        }

    @classmethod
    def _read_type(cls, type_dir: Path) -> list[RefusalRecordDict]:
        """The records in one type's directory, sorted by datapoint (never by directory order, NFR-1)."""
        if not type_dir.is_dir():
            return []
        records = [cls._read_record(path) for path in type_dir.glob("*.json")]
        return sorted(records, key=lambda record: record["datapoint"])

    @staticmethod
    def _read_record(path: Path) -> RefusalRecordDict:
        """One record file, checked for the five string keys."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"refusal record {path} is not readable JSON: {error}") from error
        if not isinstance(payload, dict) or set(payload) != _RECORD_KEYS or not all(isinstance(v, str) for v in payload.values()):
            raise ValueError(f"refusal record {path} is not an object with the string keys {sorted(_RECORD_KEYS)}")
        return RefusalRecordDict(
            check=payload["check"],
            datapoint=payload["datapoint"],
            offending=payload["offending"],
            next=payload["next"],
            verb=payload["verb"],
        )
