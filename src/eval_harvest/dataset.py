"""The `dataset` verb: assemble the Harbor manifest and report the distributions, by datapoint type.

Once `emit` has written a handful of task directories, the customer needs two things before they
trust any score (tech plan §9, US-4): a Harbor dataset manifest so `harbor run` can consume the
whole set, and a report of *how the datapoints are distributed by risk and severity* — because the
decision they actually care about is whether they have enough **low-risk** datapoints to justify
switching automated approval on, and a number computed over a dataset that is mostly high-risk
changes answers the wrong question.

A dataset holds two datapoint types (coding-eval-harvest US-7): review tasks, and coding tasks
(`[metadata.harvest].datapoint_type = "coding"`; a missing value is `review`). A coding task has no
change risk, so the risk and severity distributions count review tasks only (TP-4), and the report
also gives the count of each type, the coding tasks with no valid runner proof, and the current
refusal reasons of each type (FR-32, FR-33).

This module reads every `tasks/*/task.toml` (a read-only aggregator — no forge call, no git call,
no judgment), collects each task's `content_digest` (recorded by `emit`, D-3) and its
`[metadata.harvest]` fields, and:

- writes `dataset.toml` (the Harbor manifest, FR-29) via :meth:`Harbor.dataset_manifest_document`,
- writes a local `registry.json` — a JSON list of dataset entries, each built by
  :meth:`Harbor.local_registry_document`: one for all tasks, then one for each type that has tasks
  (FR-34, ADR-5), so each type can be run alone on Harbor's `--registry-path` path with no hub, no
  auth, and no network, and its means are not diluted by the other type's missing reward keys,
- (re)writes the dataset `metric.py` via :meth:`Harbor.metric_script_by_type` so the per-type,
  per-key aggregation is *stated and runnable* even though Harbor's local path ignores it and falls
  back to `Mean()` (§3),
- and returns the report: the **risk distribution** (count per change-risk level, plus the count
  where structural and classified risk disagree), the **severity distribution** (count per
  finding-severity level) (FR-23), the per-type counts, and one `harbor run` command per entry.

Determinism (NFR-1): the manifest, the registry, the metric, and the report are byte-stable
functions of the tasks, refusal files, and proof files present — task lists are sorted by their
builders, the refusal check names are sorted, and nothing here reads the wall clock.

**Digests are not invented.** This module does not define the per-task content-digest scheme — it
reads what `emit` recorded under `[metadata.harvest].content_digest`. A task missing its digest is a
broken input, and this refuses loudly rather than fabricating one (FR-23).

**Proofs are not parsed here.** Whether a coding task is proven is the answer of
:meth:`Verify.check_hidden_tests_proven` (E-2) on `proofs/<task-dir>.json`; a second parser here
could disagree with the one `verify` uses.

The dataset's Harbor name is derived from the datapoints' shared origin repo (`[metadata.origin].repo`,
already `org/name` form): v1 mines one repository at a time (the workflow is "point at *your*
repository"), so a dataset that mixes origin repos is outside the v1 model and refuses rather than
silently pick one.
"""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from eval_harvest.emit import FIX_REWARD_KEYS, REWARD_KEYS
from eval_harvest.forge_host import ForgeHost
from eval_harvest.harbor import DATASET_MANIFEST_FILENAME, Harbor
from eval_harvest.refusal_log import RefusalLog, RefusalRecordDict
from eval_harvest.riskmap import RISK_LEVELS
from eval_harvest.tomlw import emit_document
from eval_harvest.verify import Verify

#: Exit codes carried by `DatasetRefusalError`, pinned to the tech-plan §9 values (`cli.ExitCode`).
#: Restated as an int rather than importing `cli` (which imports this module); `tests/test_cli.py`
#: and the CLI's own `ExitCode` lock the same number, so a drift is caught there.
_REFUSAL_EXIT_CODE: Final = 3

#: The dataset artefacts this verb writes at the dataset root.
_REGISTRY_FILENAME: Final = "registry.json"
_METRIC_FILENAME: Final = "metric.py"

#: The subdirectory `emit` promotes each verified datapoint into.
_TASKS_DIRNAME: Final = "tasks"

#: The subdirectory a runner writes each coding task's proof into (`proofs/<task-dir>.json`, §8).
_PROOFS_DIRNAME: Final = "proofs"

#: The default registry version when none is derived (`local_registry_document` uses "1.0" for "").
_DEFAULT_REGISTRY_VERSION: Final = "1.0"

#: The two datapoint types, in report and registry order. A task with no `datapoint_type` is review.
_REVIEW_TYPE: Final = "review"
_CODING_TYPE: Final = "coding"
_DATAPOINT_TYPES_IN_REPORT_ORDER: Final = (_REVIEW_TYPE, _CODING_TYPE)

#: The `run_commands` key of the entry that holds every task.
_ALL_TASKS_KEY: Final = "all"

#: Printed next to the combined `harbor run` command (ADR-5): a mixed run's means are wrong.
_COMBINED_ENTRY_WARNING: Final = (
    "the combined entry mixes reward keys; Harbor counts a missing key as 0, so use the per-type commands for means"
)

_REEMIT_NEXT: Final = "re-run `eval-harvest emit` for this datapoint; do not hand-edit task.toml"


class DatasetRefusalError(Exception):
    """A refusal `dataset` raises for the CLI to render in the FR-2 four-field shape with its exit code."""

    # Five attributes because FR-2 fixes the four-field shape and each refusal also carries its
    # exit-code class; the CLI maps them straight onto `Cli.refuse`.
    def __init__(self, *, check: str, datapoint: str, offending: str, next_: str, exit_code: int = _REFUSAL_EXIT_CODE) -> None:
        super().__init__(f"{check}: {offending}")
        self.check = check
        self.datapoint = datapoint
        self.offending = offending
        self.next_ = next_
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class _TaskRecord:
    """One emitted datapoint as the aggregator reads it: its identity, type, digest, and risk/severity fields.

    `task_name` is Harbor's `org/name` id from `[task].name` (the manifest key); `directory` is the
    on-disk directory name (the registry key, and the datapoint label); `content_digest` is the
    `sha256:`-prefixed digest `emit` recorded. The three risk fields and `finding_severities` come
    from `[metadata.harvest]` for a review task, and are empty for a coding task, which has none.
    `harvest` is the parsed `[metadata.harvest]` table, kept for the coding proof check.
    """

    task_name: str
    directory: str
    origin_repo: str
    content_digest: str
    datapoint_type: str
    change_risk_structural: str
    change_risk_classified: str
    change_risk_disagreement: bool
    finding_severities: tuple[str, ...]
    harvest: dict[str, object]


@dataclass(frozen=True, slots=True)
class DatasetReport:
    """The risk and severity distribution over the review datapoints (FR-23), plus the per-type counts.

    `risk_counts` buckets every review datapoint by its **classified** change risk — the agent's
    considered holistic judgment of the change's danger, of which the structural path-rule risk is
    one input; `disagreements` counts, separately and without collapsing, the review datapoints where
    structural and classified risk disagree, so the contested calls are never hidden (decisions log:
    "records the disagreement rather than picking a winner"). `severity_counts` buckets every
    *finding* across the review datapoints by its severity, so its total is the finding count.

    `type_counts` and `refusal_counts` always carry both types, a zero included; `coding_unproven`
    counts the coding datapoints with no valid runner proof; `run_commands` has `all`, plus one
    command for each type that has tasks (FR-32, FR-33, FR-34).
    """

    datapoints: int
    risk_counts: dict[str, int]
    disagreements: int
    severity_counts: dict[str, int]
    type_counts: dict[str, int]
    coding_unproven: int
    refusal_counts: dict[str, dict[str, int]]
    run_commands: dict[str, str]

    def as_json(self) -> dict[str, object]:
        """The `--json` object (tech plan §9): risk (with disagreements), severity, count, `by_type`, `run_commands`."""
        risk_distribution: dict[str, int] = dict(self.risk_counts)
        risk_distribution["disagreements"] = self.disagreements
        return {
            "risk_distribution": risk_distribution,
            "severity_distribution": dict(self.severity_counts),
            "datapoints": self.datapoints,
            "by_type": {
                _REVIEW_TYPE: {
                    "datapoints": self.type_counts[_REVIEW_TYPE],
                    "refusals": dict(self.refusal_counts[_REVIEW_TYPE]),
                },
                _CODING_TYPE: {
                    "datapoints": self.type_counts[_CODING_TYPE],
                    "unproven": self.coding_unproven,
                    "refusals": dict(self.refusal_counts[_CODING_TYPE]),
                },
            },
            "run_commands": dict(self.run_commands),
        }

    def render_human(self) -> str:
        """The default human rendering: the review distributions first, then the per-type lines and run commands."""
        lines = [f"datapoints: {self.datapoints}", "change-risk distribution (by classified risk):"]
        lines += [f"  {level}: {self.risk_counts[level]}" for level in RISK_LEVELS]
        lines.append(f"  structural/classified disagreements: {self.disagreements}")
        lines.append("finding-severity distribution:")
        lines += [f"  {level}: {self.severity_counts[level]}" for level in RISK_LEVELS]
        lines.append("datapoints by type:")
        lines += [
            f"  {datapoint_type}: {self.type_counts[datapoint_type]}" for datapoint_type in _DATAPOINT_TYPES_IN_REPORT_ORDER
        ]
        lines.append(f"coding datapoints without a valid proof: {self.coding_unproven}")
        lines.append("refusal reasons by type:")
        for datapoint_type in _DATAPOINT_TYPES_IN_REPORT_ORDER:
            lines += self._refusal_lines(datapoint_type, self.refusal_counts[datapoint_type])
        lines.append("run with Harbor:")
        for entry_key, command in self.run_commands.items():
            lines.append(f"  {entry_key}: {command}")
            if entry_key == _ALL_TASKS_KEY:
                lines.append(f"    ({_COMBINED_ENTRY_WARNING})")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _refusal_lines(datapoint_type: str, counts: dict[str, int]) -> list[str]:
        """The human lines for one type's refusal reasons: a heading, then one `check: count` line each."""
        if not counts:
            return [f"  {datapoint_type}: none"]
        return [f"  {datapoint_type}:", *(f"    {check}: {count}" for check, count in counts.items())]


class Dataset:
    """Read every emitted task, assemble the Harbor manifest + registry + metric, and report. Holds no state."""

    @classmethod
    def build_dataset(cls, dataset_dir: Path) -> DatasetReport:
        """Assemble `dataset.toml`, `registry.json`, `metric.py`, and return the per-type distribution report.

        Reads every `tasks/*/task.toml`, refusing `no-datapoints` (exit 3) on an empty dataset and
        loudly on any task missing its `content_digest` or carrying an unknown `datapoint_type`, and
        reads the refusal log (refusing `malformed-refusal` on a broken record). Writes the three
        artefacts at the dataset root (byte-stable, NFR-1) and returns the report for the CLI to print.
        """
        records = cls._read_task_records(dataset_dir)
        refusal_counts = cls._refusal_counts(dataset_dir)
        dataset_name = cls._dataset_name(records)
        cls._write_manifest(dataset_dir, dataset_name, records)
        entry_names = cls._write_registry(dataset_dir, dataset_name, records)
        cls._write_metric(dataset_dir)
        return cls._report(
            records,
            coding_unproven=cls._count_unproven_coding_tasks(dataset_dir, records),
            refusal_counts=refusal_counts,
            run_commands=cls._run_commands(dataset_dir, entry_names),
        )

    # ───────────────────────────── reading the tasks ─────────────────────────────

    @classmethod
    def _read_task_records(cls, dataset_dir: Path) -> list[_TaskRecord]:
        """Every emitted datapoint under `tasks/`, sorted by directory name; refuses `no-datapoints` if none."""
        tasks_dir = dataset_dir / _TASKS_DIRNAME
        task_dirs = sorted(path for path in tasks_dir.glob("*") if (path / "task.toml").is_file()) if tasks_dir.is_dir() else []
        if not task_dirs:
            raise DatasetRefusalError(
                check="no-datapoints",
                datapoint=str(dataset_dir),
                offending=f"no emitted task directories under {tasks_dir}",
                next_="run `eval-harvest emit <candidate> --kind reject|approve|fix` to build at least one datapoint first",
            )
        return [cls._read_one_task_record(task_dir) for task_dir in task_dirs]

    @classmethod
    def _read_one_task_record(cls, task_dir: Path) -> _TaskRecord:
        """Parse one `task.toml` into a `_TaskRecord`, refusing loudly on a missing name, digest, or type."""
        document = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        task_section = cls._table(document.get("task"))
        metadata = cls._table(document.get("metadata"))
        origin = cls._table(metadata.get("origin"))
        harvest = cls._table(metadata.get("harvest"))
        datapoint = task_dir.name
        datapoint_type = cls._datapoint_type(harvest.get("datapoint_type"), datapoint)
        task_name = cls._require_str(task_section.get("name"), "task.name", datapoint)
        origin_repo = cls._require_str(origin.get("repo"), "metadata.origin.repo", datapoint)
        content_digest = cls._require_digest(harvest.get("content_digest"), datapoint)
        if datapoint_type == _CODING_TYPE:
            # A coding task has no change risk (TP-4): the risk fields stay empty and are never counted.
            return _TaskRecord(
                task_name=task_name,
                directory=datapoint,
                origin_repo=origin_repo,
                content_digest=content_digest,
                datapoint_type=datapoint_type,
                change_risk_structural="",
                change_risk_classified="",
                change_risk_disagreement=False,
                finding_severities=(),
                harvest=harvest,
            )
        return _TaskRecord(
            task_name=task_name,
            directory=datapoint,
            origin_repo=origin_repo,
            content_digest=content_digest,
            datapoint_type=datapoint_type,
            change_risk_structural=cls._require_risk(harvest.get("change_risk_structural"), "change_risk_structural", datapoint),
            change_risk_classified=cls._require_risk(harvest.get("change_risk_classified"), "change_risk_classified", datapoint),
            change_risk_disagreement=cls._as_bool(harvest.get("change_risk_disagreement")),
            finding_severities=cls._severities(harvest.get("finding_severities"), datapoint),
            harvest=harvest,
        )

    @staticmethod
    def _datapoint_type(value: object, datapoint: str) -> str:
        """The task's `datapoint_type`: missing is `review`; anything but `review` or `coding` is a loud refusal.

        A typo must not count silently as review — it would put a coding task into the risk counts.
        """
        if value is None:
            return _REVIEW_TYPE
        if isinstance(value, str) and value in _DATAPOINT_TYPES_IN_REPORT_ORDER:
            return value
        raise DatasetRefusalError(
            check="malformed-task",
            datapoint=datapoint,
            offending=f"metadata.harvest.datapoint_type is {value!r}, not one of {list(_DATAPOINT_TYPES_IN_REPORT_ORDER)}",
            next_=_REEMIT_NEXT,
        )

    @staticmethod
    def _table(value: object) -> dict[str, object]:
        """`value` as a parsed-TOML table, or an empty table — the missing-field paths handle emptiness."""
        return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}

    @staticmethod
    def _require_str(value: object, pointer: str, datapoint: str) -> str:
        """A non-empty string at `pointer`, or a loud refusal — a datapoint with no identity is broken."""
        if isinstance(value, str) and value:
            return value
        raise DatasetRefusalError(
            check="malformed-task",
            datapoint=datapoint,
            offending=f"{pointer} is missing or not a non-empty string in this task's task.toml",
            next_=_REEMIT_NEXT,
        )

    @classmethod
    def _require_risk(cls, value: object, pointer: str, datapoint: str) -> str:
        """A risk level (one of RISK_LEVELS) at `pointer`, or a loud refusal."""
        risk = cls._require_str(value, pointer, datapoint)
        if risk not in RISK_LEVELS:
            raise DatasetRefusalError(
                check="malformed-task",
                datapoint=datapoint,
                offending=f"{pointer} is {risk!r}, not one of {list(RISK_LEVELS)}",
                next_=_REEMIT_NEXT,
            )
        return risk

    @staticmethod
    def _require_digest(value: object, datapoint: str) -> str:
        """The `content_digest` `emit` recorded — required, never invented."""
        if isinstance(value, str) and value:
            return value
        raise DatasetRefusalError(
            check="missing-content-digest",
            datapoint=datapoint,
            offending="this task's [metadata.harvest] has no content_digest; the manifest cannot reference it",
            next_="re-run `eval-harvest emit` for this datapoint so the content_digest is recorded",
        )

    @staticmethod
    def _as_bool(value: object) -> bool:
        """The recorded `change_risk_disagreement` flag; anything non-boolean reads as no disagreement."""
        return value if isinstance(value, bool) else False

    @staticmethod
    def _severities(value: object, datapoint: str) -> tuple[str, ...]:
        """The `finding_severities` list as a tuple of risk-level strings; refuses on an off-scale entry."""
        if not isinstance(value, list):
            return ()
        severities: list[str] = []
        for entry in value:
            if not isinstance(entry, str) or entry not in RISK_LEVELS:
                raise DatasetRefusalError(
                    check="malformed-task",
                    datapoint=datapoint,
                    offending=f"finding_severities contains {entry!r}, not one of {list(RISK_LEVELS)}",
                    next_=_REEMIT_NEXT,
                )
            severities.append(entry)
        return tuple(severities)

    # ───────────────────────────── refusals and proofs ─────────────────────────────

    @classmethod
    def _refusal_counts(cls, dataset_dir: Path) -> dict[str, dict[str, int]]:
        """The current refusals of each type, counted by check and sorted by check name (FR-33, NFR-1).

        A broken refusal file refuses `malformed-refusal`: skipping it would hide a refusal.
        """
        try:
            records_by_type = RefusalLog.read_all(dataset_dir)
        except ValueError as error:
            raise cls._malformed_refusal(error) from error
        return {
            datapoint_type: cls._count_by_check(records_by_type.get(datapoint_type, []))
            for datapoint_type in _DATAPOINT_TYPES_IN_REPORT_ORDER
        }

    @staticmethod
    def _count_by_check(records: list[RefusalRecordDict]) -> dict[str, int]:
        """`{check: count}` over `records`, keys sorted so the output is deterministic."""
        counts = Counter(record["check"] for record in records)
        return {check: counts[check] for check in sorted(counts)}

    @staticmethod
    def _malformed_refusal(error: ValueError) -> DatasetRefusalError:
        """The `malformed-refusal` refusal for a refusal record `RefusalLog` could not read.

        `RefusalLog` names the file in its message (`refusal record <path> is …`); the path is lifted
        out of it for `next`, so the SDE knows which file to delete or fix.
        """
        message = str(error)
        record_path = message.removeprefix("refusal record ").split(" is ", 1)[0]
        return DatasetRefusalError(
            check="malformed-refusal",
            datapoint=record_path,
            offending=message,
            next_=f"delete or fix {record_path}, then run `eval-harvest dataset` again",
        )

    @staticmethod
    def _count_unproven_coding_tasks(dataset_dir: Path, records: list[_TaskRecord]) -> int:
        """How many coding tasks have no valid runner proof: missing, stale, and failing proofs all count.

        The proof is judged by :meth:`Verify.check_hidden_tests_proven` (E-2), the one parser: an empty
        list means proven.
        """
        return sum(
            1
            for record in records
            if record.datapoint_type == _CODING_TYPE
            and Verify.check_hidden_tests_proven(
                dataset_dir / _TASKS_DIRNAME / record.directory,
                record.harvest,
                dataset_dir / _PROOFS_DIRNAME / f"{record.directory}.json",
                record.directory,
            )
        )

    # ───────────────────────────── the dataset name ─────────────────────────────

    @staticmethod
    def _dataset_name(records: list[_TaskRecord]) -> str:
        """The dataset's Harbor `org/name`: the datapoints' shared origin repo (v1 mines one repo).

        The origin repo keeps a nested GitLab group's full path as provenance; the name folds it into
        Harbor's one-slash shape exactly as each task name does.

        A dataset that mixes origin repos is outside the v1 model, so this refuses rather than pick a
        name silently — the manifest and registry each need exactly one addressable name.
        """
        repos = sorted({record.origin_repo for record in records})
        if len(repos) != 1:
            raise DatasetRefusalError(
                check="mixed-origin-repos",
                datapoint=", ".join(repos),
                offending=f"the datapoints come from {len(repos)} repos ({repos}); a v1 dataset is one repository",
                next_="build one dataset per repository (one `--dataset` dir per repo)",
            )
        return ForgeHost.harbor_org_name(repos[0])

    # ───────────────────────────── writing the artefacts ─────────────────────────────

    @staticmethod
    def _write_manifest(dataset_dir: Path, dataset_name: str, records: list[_TaskRecord]) -> None:
        """Write `dataset.toml` — the Harbor manifest keyed by each task's `org/name` and content digest."""
        task_digests = {record.task_name: record.content_digest for record in records}
        document = Harbor.dataset_manifest_document(dataset_name, task_digests, files=(_METRIC_FILENAME,))
        (dataset_dir / DATASET_MANIFEST_FILENAME).write_bytes(emit_document(document))

    @classmethod
    def _write_registry(cls, dataset_dir: Path, dataset_name: str, records: list[_TaskRecord]) -> dict[str, str]:
        """Write `registry.json` — a JSON list of `--registry-path` entries; return `{run_commands key: entry name}`.

        A list, not one object: Harbor's `Registry.from_path` validates each element of the top-level
        value as a `DatasetSpec`, so a bare object is iterated key by key and fails to load. The
        entries, in this fixed order (NFR-1): `<short>` (all tasks), `<short>-review` (only when review
        tasks exist), `<short>-coding` (only when coding tasks exist) — FR-34, ADR-5.
        """
        entry_tasks: dict[str, tuple[str, list[_TaskRecord]]] = {_ALL_TASKS_KEY: (dataset_name, records)}
        for datapoint_type in _DATAPOINT_TYPES_IN_REPORT_ORDER:
            typed_records = [record for record in records if record.datapoint_type == datapoint_type]
            if typed_records:
                entry_tasks[datapoint_type] = (f"{dataset_name}-{datapoint_type}", typed_records)
        document = [cls._registry_entry(name, entry_records) for name, entry_records in entry_tasks.values()]
        text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (dataset_dir / _REGISTRY_FILENAME).write_bytes(text.encode("utf-8"))
        return {key: str(entry["name"]) for key, entry in zip(entry_tasks, document, strict=True)}

    @staticmethod
    def _registry_entry(name: str, records: list[_TaskRecord]) -> dict[str, object]:
        """One `registry.json` entry named by the short part of `name`, listing `records`' task directories."""
        return Harbor.local_registry_document(
            name=name,
            version=_DEFAULT_REGISTRY_VERSION,
            description="",
            task_directories=[record.directory for record in records],
            root=f"./{_TASKS_DIRNAME}",
        )

    @staticmethod
    def _write_metric(dataset_dir: Path) -> None:
        """(Re)write the dataset `metric.py`: each key's mean over its own type's rows only (ADR-5, FR-34)."""
        script = Harbor.metric_script_by_type({_REVIEW_TYPE: REWARD_KEYS, _CODING_TYPE: FIX_REWARD_KEYS})
        (dataset_dir / _METRIC_FILENAME).write_bytes(script)

    @staticmethod
    def _run_commands(dataset_dir: Path, entry_names: dict[str, str]) -> dict[str, str]:
        """One `harbor run` command for each registry entry, keyed `all`, `review`, `coding`.

        The dataset path is the one the CLI received, not resolved, written with forward slashes so
        the same input gives the same output on macOS and Windows (NFR-10). A bare `--dataset <name>`
        resolves the entry's only version (Harbor 0.22.0 `JsonRegistryClient._get_dataset_metadata`).
        """
        registry_path = (dataset_dir / _REGISTRY_FILENAME).as_posix()
        return {key: f"harbor run --registry-path {registry_path} --dataset {name}" for key, name in entry_names.items()}

    # ───────────────────────────── the distribution report ─────────────────────────────

    @staticmethod
    def _report(
        records: list[_TaskRecord],
        *,
        coding_unproven: int,
        refusal_counts: dict[str, dict[str, int]],
        run_commands: dict[str, str],
    ) -> DatasetReport:
        """Count review datapoints per classified risk level, disagreements, findings per severity, and each type."""
        risk_counts = {level: 0 for level in RISK_LEVELS}
        severity_counts = {level: 0 for level in RISK_LEVELS}
        disagreements = 0
        # Coding records carry no risk (an empty level would raise KeyError), so they are skipped first.
        review_records = [record for record in records if record.datapoint_type == _REVIEW_TYPE]
        for record in review_records:
            risk_counts[record.change_risk_classified] += 1
            if record.change_risk_disagreement:
                disagreements += 1
            for severity in record.finding_severities:
                severity_counts[severity] += 1
        return DatasetReport(
            datapoints=len(records),
            risk_counts=risk_counts,
            disagreements=disagreements,
            severity_counts=severity_counts,
            type_counts={_REVIEW_TYPE: len(review_records), _CODING_TYPE: len(records) - len(review_records)},
            coding_unproven=coding_unproven,
            refusal_counts=refusal_counts,
            run_commands=run_commands,
        )
