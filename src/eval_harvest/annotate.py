"""The `annotate` verb's core: parse per-comment fill records and merge them into a candidate.

The candidate is both what the agent reads to decide and what it writes the decision back into.
Without record-level merging, every fill and every correction is a whole-file rewrite — tens of KB
read, judged, and written back per PR, and again after each `emit` refusal. `annotate`
takes the judgments as a stream of small records (one comment's classification, one finding, the
change risk) and merges each into the candidate, so the agent never reproduces the bytes it is not
changing (tech plan §5.3).

The merge is a *pure* function (:meth:`Annotate.apply_records`): a candidate dict in, a new candidate
dict plus every :class:`~eval_harvest.candidate.Violation` out — no filesystem access, so the
abort-on-violation rule the CLI enforces is one branch rather than a cleanup path, and the whole
thing is trivially testable. Only the CLI's ``handle_annotate`` reads stdin and writes the file, and
it writes through :meth:`~eval_harvest.candidate.Candidate.dump` so the bytes stay byte-identical to
what ``capture`` would produce for the same content (FR-10).

**The CLI never writes a judgment (FR-9).** ``annotate`` records exactly what the agent said and
infers nothing: it does not guess a severity, default ``reference_only``, or fill ``rubric_version``.
A finding record that omits a judgment is a violation the agent must resolve, not a slot the CLI
fills for it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Final

from eval_harvest.candidate import CandidateDict, CommentDict, FindingDict, Violation
from eval_harvest.fix_candidate import EnvironmentDict, FixCandidate, FixCandidateDict, HiddenTestDict, RubricCriterionDict

#: The text slots a ``{"slot": …, "value": …}`` record may name, in §8 order.
_FIX_TEXT_SLOTS: Final = ("instruction", "instruction_rationale", "tooling_note")

#: The keys of a hidden-test, rubric-criterion, and environment record: the TypedDict fields, in §8 order.
_HIDDEN_TEST_FIELDS: Final = tuple(HiddenTestDict.__annotations__)
_RUBRIC_CRITERION_FIELDS: Final = tuple(RubricCriterionDict.__annotations__)
_ENVIRONMENT_FIELDS: Final = tuple(EnvironmentDict.__annotations__)


class AnnotateParseError(Exception):
    """A record stream that is not valid JSON, carrying the 1-based line the parse failed on.

    Distinct from a :class:`~eval_harvest.candidate.Violation`: a syntactically broken stream yields
    no records at all, so it is reported before the merge rather than aggregated with the coherence
    violations. ``handle_annotate`` turns it into the FR-2 ``malformed-record`` refusal.
    """

    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line = line
        self.message = message


@dataclass(frozen=True, slots=True)
class AnnotateStats:
    """What a merge did, for the run's stdout: comments classified, ids overwritten, findings added."""

    classified: int
    overwritten: list[int]
    findings_added: int


@dataclass(frozen=True, slots=True)
class FixAnnotateStats:
    """What a coding merge did: the text slots set, the hidden tests and rubric criteria added or replaced, the recipe."""

    slots_set: list[str]
    hidden_tests_added: int
    hidden_tests_replaced: list[str]
    rubric_criteria_added: int
    rubric_criteria_replaced: list[str]
    environment_set: bool


class Annotate:
    """Parse fill records and merge them into a candidate. Holds no state."""

    #: The discriminators that select a record's kind. A record carrying none of them is unrecognised
    #: and refuses (never a silent skip): a dropped judgment is worse than a loud refusal.
    _COMMENT_KEY: Final = "comment"
    _FINDING_KEY: Final = "finding"
    _RISK_KEY: Final = "risk_classified"
    _RUBRIC_KEY: Final = "rubric_version"

    #: The discriminators of the four coding records (C-2). A review record on a coding candidate, or a
    #: coding record on a review candidate, carries none of the other type's keys and is unrecognised.
    _SLOT_KEY: Final = "slot"
    _HIDDEN_TEST_KEY: Final = "hidden_test"
    _RUBRIC_CRITERION_KEY: Final = "rubric_criterion"
    _ENVIRONMENT_KEY: Final = "environment"

    # ───────────────────────────── parse (one place for both input forms) ─────────────────────────────

    @classmethod
    def parse_records(cls, text: str, *, jsonl: bool) -> list[dict[str, object]]:
        """Parse the record stream: JSONL (one object per line) when ``jsonl``, else a JSON array.

        A malformed record raises :class:`AnnotateParseError` naming its 1-based line so a broken
        record in a long stream is locatable, rather than a bare ``json`` traceback.
        """
        if jsonl:
            return cls._parse_jsonl(text)
        return cls._parse_json_array(text)

    @staticmethod
    def _parse_jsonl(text: str) -> list[dict[str, object]]:
        """One JSON object per non-blank line; blank lines are skipped without shifting line numbers."""
        records: list[dict[str, object]] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise AnnotateParseError(line_number, f"line {line_number} is not valid JSON: {error.msg}") from error
            if not isinstance(record, dict):
                raise AnnotateParseError(line_number, f"line {line_number} is not a JSON object")
            records.append(record)
        return records

    @staticmethod
    def _parse_json_array(text: str) -> list[dict[str, object]]:
        """A single JSON array of objects; the element index doubles as the reported line number."""
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as error:
            raise AnnotateParseError(error.lineno, f"line {error.lineno} is not valid JSON: {error.msg}") from error
        if not isinstance(loaded, list):
            raise AnnotateParseError(1, "the --from-json file must contain a JSON array of records")
        for index, record in enumerate(loaded, start=1):
            if not isinstance(record, dict):
                raise AnnotateParseError(index, f"record {index} is not a JSON object")
        records: list[dict[str, object]] = loaded
        return records

    # ───────────────────────────── merge (pure: dict in, new dict + violations out) ─────────────────────────────

    @classmethod
    def apply_records(
        cls, candidate: CandidateDict, records: list[dict[str, object]], *, replace_findings: bool = False
    ) -> tuple[CandidateDict, list[Violation]]:
        """Merge ``records`` into a *copy* of ``candidate``; return the copy and every apply-time violation.

        Pure: the input candidate is never mutated (a deep copy is merged), so the caller can compare
        the before and after, and abort the write on any violation without a rollback. The returned
        violations are the ones only ``annotate`` can see — an unknown comment id, an unrecognised
        record shape, a finding that omits ``reference_only``. Coherence of the *values* (a finding's
        severity and evidence, FR-15) is left to :meth:`Candidate.validate_filled`, which the CLI runs
        on the merged result, so ``annotate`` never carries a second copy of those rules (ADR-2).

        With ``replace_findings`` the finding list is cleared before applying, so a rescoping run can
        drop the previous set in one command instead of hand-editing the file.
        """
        merged = copy.deepcopy(candidate)
        if replace_findings:
            merged["findings"] = []
        comments_by_id: dict[int, CommentDict] = {comment["id"]: comment for comment in merged["comments"]}
        violations: list[Violation] = []
        for position, record in enumerate(records, start=1):
            violations += cls._apply_one_record(merged, comments_by_id, position, record)
        return merged, violations

    @classmethod
    def _apply_one_record(
        cls, merged: CandidateDict, comments_by_id: dict[int, CommentDict], position: int, record: dict[str, object]
    ) -> list[Violation]:
        """Apply one record by its discriminator; an unrecognised shape is a violation, never a skip."""
        if cls._COMMENT_KEY in record:
            return cls._apply_comment_record(comments_by_id, position, record)
        if cls._FINDING_KEY in record:
            return cls._apply_finding_record(merged, position, record)
        if cls._RISK_KEY in record or cls._RUBRIC_KEY in record:
            cls._apply_scalar_record(merged, record)
            return []
        return [
            Violation(
                "annotate.unrecognised-record",
                f"records[{position}]",
                f"record {position} carries none of: {cls._COMMENT_KEY}, {cls._FINDING_KEY}, {cls._RISK_KEY}, {cls._RUBRIC_KEY}",
            )
        ]

    @staticmethod
    def _apply_comment_record(
        comments_by_id: dict[int, CommentDict], position: int, record: dict[str, object]
    ) -> list[Violation]:
        """Set one comment's classification slots from a comment record; an unknown id refuses.

        Mutates the existing comment dict's values in place rather than rebuilding it, so the §8 field
        order is preserved (``sort_keys=False`` makes insertion order the file's order, FR-10).
        """
        comment_id = record[Annotate._COMMENT_KEY]
        comment = comments_by_id.get(comment_id) if isinstance(comment_id, int) else None
        if comment is None:
            return [
                Violation(
                    "annotate.unknown-comment",
                    f"records[{position}].comment",
                    f"no comment with id {comment_id!r} in the candidate",
                )
            ]
        comment["classification"] = str(record.get("classification", ""))
        comment["classification_rationale"] = str(record.get("rationale", ""))
        return []

    @staticmethod
    def _apply_finding_record(merged: CandidateDict, position: int, record: dict[str, object]) -> list[Violation]:
        """Append one finding; ``reference_only`` must be stated explicitly (FR-9 — never defaulted).

        The finding dict is built in the §8 field order so its serialized form matches what ``capture``
        would write. The severity/evidence/rationale rules are *not* checked here: the CLI runs
        :meth:`Candidate.validate_filled` on the merged candidate, which owns FR-15 for every finding.
        """
        finding = record[Annotate._FINDING_KEY]
        if not isinstance(finding, dict):
            return [Violation("annotate.unrecognised-record", f"records[{position}].finding", "finding must be a JSON object")]
        if "reference_only" not in finding:
            return [
                Violation(
                    "annotate.finding-missing-field",
                    f"records[{position}].finding.reference_only",
                    "reference_only must be stated explicitly (true|false); the CLI never defaults a block/no-block call (FR-9)",
                )
            ]
        merged["findings"].append(
            FindingDict(
                comment_ids=list(finding.get("comment_ids", [])),
                statement=str(finding.get("statement", "")),
                severity=str(finding.get("severity", "")),
                severity_evidence=str(finding.get("severity_evidence", "")),
                severity_rationale=str(finding.get("severity_rationale", "")),
                reference_only=bool(finding["reference_only"]),
            )
        )
        return []

    @staticmethod
    def _apply_scalar_record(merged: CandidateDict, record: dict[str, object]) -> None:
        """Set the change-risk classification and/or the rubric version from a scalar record.

        Both live on existing keys (``capture`` seeds them empty), so the values are overwritten in
        place and the field order is untouched. Neither value is inferred: a missing rationale is
        recorded as the empty string, not guessed.
        """
        if Annotate._RISK_KEY in record:
            merged["change_risk"]["risk_classified"] = str(record[Annotate._RISK_KEY])
            merged["change_risk"]["risk_classified_rationale"] = str(record.get("rationale", ""))
        if Annotate._RUBRIC_KEY in record:
            merged["rubric_version"] = str(record[Annotate._RUBRIC_KEY])

    # ───────────────────────────── report helpers (pure) ─────────────────────────────

    @classmethod
    def summarize(cls, original: CandidateDict, records: list[dict[str, object]]) -> AnnotateStats:
        """What a clean merge of ``records`` did, measured against the pre-merge ``original``.

        ``overwritten`` is the ids whose classification was *already* set before this run — reclassifying
        after re-reading a comment is normal, but a silent overwrite the agent cannot see is not, so the
        run reports it. Only reached on a run with no violations, so every comment id is known.
        """
        already_classified = {comment["id"] for comment in original["comments"] if comment["classification"]}
        classified = 0
        overwritten: list[int] = []
        findings_added = 0
        for record in records:
            if cls._COMMENT_KEY in record:
                classified += 1
                comment_id = record[cls._COMMENT_KEY]
                if isinstance(comment_id, int) and comment_id in already_classified:
                    overwritten.append(comment_id)
            elif cls._FINDING_KEY in record:
                findings_added += 1
        return AnnotateStats(classified=classified, overwritten=overwritten, findings_added=findings_added)

    @classmethod
    def remaining_slots(cls, candidate: CandidateDict) -> list[str]:
        """The judgment slots still unfilled after a merge, one compact line each, for stdout.

        Rendered from the candidate itself so a driver can loop ``annotate`` until this is empty. The
        countable required slots only: unclassified comments, the change-risk classification, and the
        rubric version. Findings are not listed — a PR of only nits legitimately states none.
        """
        slots: list[str] = []
        comments = candidate["comments"]
        unclassified = sum(1 for comment in comments if not comment["classification"])
        if unclassified:
            slots.append(f"comments[].classification — {unclassified} of {len(comments)} still unclassified")
        if not candidate["change_risk"]["risk_classified"]:
            slots.append("change_risk.risk_classified — the change's risk is unclassified")
        if not candidate["rubric_version"]:
            slots.append("rubric_version — no rubric version pinned")
        return slots

    # ───────────────────────────── coding candidate: the four slot records ─────────────────────────────

    @classmethod
    def apply_fix_records(
        cls, candidate: FixCandidateDict, records: list[dict[str, object]], *, replace_hidden_tests: bool = False
    ) -> tuple[FixCandidateDict, list[Violation]]:
        """Merge coding ``records`` into a *copy* of ``candidate``; return the copy and every record-shape violation.

        Pure, as :meth:`apply_records` is. Only the record shape is checked here — an unknown slot, a
        missing key, a field of the wrong JSON type. The value rules (the ``kind`` and ``origin`` enums,
        the ``tooling_note`` length, the recipe form) belong to :meth:`FixCandidate.validate_filled` and
        :meth:`FixCandidate.validate_recipe`, which the CLI runs on the result, so each rule has one copy.
        ``hidden_tests`` is sorted by ``(repo_path, name)`` after the merge (tech plan §8, NFR-1).
        """
        merged = copy.deepcopy(candidate)
        if replace_hidden_tests:
            merged["hidden_tests"] = []
        violations: list[Violation] = []
        for position, record in enumerate(records, start=1):
            violations += cls._apply_one_fix_record(merged, position, record)
        merged["hidden_tests"].sort(key=lambda entry: (entry["repo_path"], entry["name"]))
        return merged, violations

    @classmethod
    def _apply_one_fix_record(cls, merged: FixCandidateDict, position: int, record: dict[str, object]) -> list[Violation]:
        """Apply one coding record by its discriminator; a record with none is a violation, never a skip."""
        if cls._SLOT_KEY in record:
            return cls._apply_fix_text_slot(merged, position, record)
        if cls._HIDDEN_TEST_KEY in record:
            return cls._apply_fix_hidden_test(merged, position, record[cls._HIDDEN_TEST_KEY])
        if cls._RUBRIC_CRITERION_KEY in record:
            return cls._apply_fix_rubric_criterion(merged, position, record[cls._RUBRIC_CRITERION_KEY])
        if cls._ENVIRONMENT_KEY in record:
            return cls._apply_fix_environment(merged, position, record[cls._ENVIRONMENT_KEY])
        known = ", ".join((cls._SLOT_KEY, cls._HIDDEN_TEST_KEY, cls._RUBRIC_CRITERION_KEY, cls._ENVIRONMENT_KEY))
        return [
            Violation(
                "annotate.unrecognised-record",
                f"records[{position}]",
                f"record {position} carries none of the coding record keys: {known}",
            )
        ]

    @classmethod
    def _apply_fix_text_slot(cls, merged: FixCandidateDict, position: int, record: dict[str, object]) -> list[Violation]:
        """Set one text slot to ``str(value)``; an unknown slot name or a missing ``value`` refuses (FR-18, FR-19)."""
        name = record[cls._SLOT_KEY]
        if name not in _FIX_TEXT_SLOTS:
            detail = f"no slot {name!r} in a coding candidate; the text slots are {', '.join(_FIX_TEXT_SLOTS)}"
            return [Violation("annotate.unknown-slot", f"records[{position}].slot", detail)]
        if "value" not in record:
            detail = f"the record for slot {name!r} has no `value`; the CLI never writes a slot the agent did not send"
            return [Violation("annotate.slot-missing-field", f"records[{position}].value", detail)]
        value = str(record["value"])
        if name == "instruction":
            merged["instruction"] = value
        elif name == "instruction_rationale":
            merged["instruction_rationale"] = value
        else:
            merged["tooling_note"] = value
        return []

    @classmethod
    def _apply_fix_hidden_test(cls, merged: FixCandidateDict, position: int, payload: object) -> list[Violation]:
        """Upsert one hidden test by ``(repo_path, name)``: an existing key is replaced, a new key is added."""
        pointer = f"records[{position}].{cls._HIDDEN_TEST_KEY}"
        fields, violations = cls._string_fields(payload, pointer, _HIDDEN_TEST_FIELDS)
        if violations:
            return violations
        entry = HiddenTestDict(
            repo_path=fields["repo_path"],
            name=fields["name"],
            kind=fields["kind"],
            origin=fields["origin"],
            evidence=fields["evidence"],
        )
        key = (entry["repo_path"], entry["name"])
        existing = [index for index, old in enumerate(merged["hidden_tests"]) if (old["repo_path"], old["name"]) == key]
        if existing:
            merged["hidden_tests"][existing[0]] = entry
        else:
            merged["hidden_tests"].append(entry)
        return []

    @classmethod
    def _apply_fix_rubric_criterion(cls, merged: FixCandidateDict, position: int, payload: object) -> list[Violation]:
        """Upsert one rubric criterion by ``id``: an existing id is replaced in place, a new id is added at the end."""
        pointer = f"records[{position}].{cls._RUBRIC_CRITERION_KEY}"
        fields, violations = cls._string_fields(payload, pointer, _RUBRIC_CRITERION_FIELDS)
        if violations:
            return violations
        criterion = RubricCriterionDict(id=fields["id"], kind=fields["kind"], text=fields["text"])
        existing = [index for index, old in enumerate(merged["rubric_criteria"]) if old["id"] == criterion["id"]]
        if existing:
            merged["rubric_criteria"][existing[0]] = criterion
        else:
            merged["rubric_criteria"].append(criterion)
        return []

    @classmethod
    def _apply_fix_environment(cls, merged: FixCandidateDict, position: int, payload: object) -> list[Violation]:
        """Replace the whole environment recipe; the new dict is built in the §8 field order, not the record's."""
        pointer = f"records[{position}].{cls._ENVIRONMENT_KEY}"
        string_fields = tuple(field for field in _ENVIRONMENT_FIELDS if field != "setup_steps")
        fields, violations = cls._string_fields(payload, pointer, string_fields, list_fields=("setup_steps",))
        if violations:
            return violations
        steps: list[str] = [str(step) for step in payload["setup_steps"]] if isinstance(payload, dict) else []
        merged["environment"] = EnvironmentDict(
            base_image=fields["base_image"],
            setup_steps=steps,
            lockfile=fields["lockfile"],
            test_command=fields["test_command"],
            rationale=fields["rationale"],
        )
        return []

    @staticmethod
    def _string_fields(
        payload: object, pointer: str, string_fields: tuple[str, ...], *, list_fields: tuple[str, ...] = ()
    ) -> tuple[dict[str, str], list[Violation]]:
        """The string fields of a record object, or the shape violations: not an object, a key missing, a wrong type.

        ``list_fields`` must be JSON lists of strings. A key the shape does not know is refused too, so
        a misspelt key is never dropped without a word.
        """
        if not isinstance(payload, dict):
            return {}, [Violation("annotate.unrecognised-record", pointer, "the value must be a JSON object")]
        expected = (*string_fields, *list_fields)
        violations = [
            Violation("annotate.slot-missing-field", f"{pointer}.{field}", f"`{field}` is required")
            for field in expected
            if field not in payload
        ]
        violations += [
            Violation("annotate.unrecognised-record", f"{pointer}.{key}", f"`{key}` is not a field of this record")
            for key in payload
            if key not in expected
        ]
        violations += [
            Violation("annotate.unrecognised-record", f"{pointer}.{field}", f"`{field}` must be a JSON string")
            for field in string_fields
            if field in payload and not isinstance(payload[field], str)
        ]
        violations += [
            Violation("annotate.unrecognised-record", f"{pointer}.{field}", f"`{field}` must be a JSON list of strings")
            for field in list_fields
            if field in payload and not _is_string_list(payload[field])
        ]
        return {field: str(payload[field]) for field in string_fields if field in payload}, violations

    # ───────────────────────────── coding report helpers (pure) ─────────────────────────────

    @classmethod
    def summarize_fix(
        cls, original: FixCandidateDict, records: list[dict[str, object]], *, replace_hidden_tests: bool = False
    ) -> FixAnnotateStats:
        """What a clean coding merge did, against the pre-merge ``original``.

        A "replaced" entry is one whose key was in the candidate before this run, so an overwrite is
        never silent (the reason ``summarize`` reports ``overwritten``). With ``replace_hidden_tests`` the
        old list was cleared first, so no hidden test counts as replaced.
        """
        old_tests = set() if replace_hidden_tests else {(old["repo_path"], old["name"]) for old in original["hidden_tests"]}
        old_ids = {old["id"] for old in original["rubric_criteria"]}
        slots_set: list[str] = []
        tests_seen: list[tuple[str, str]] = []
        ids_seen: list[str] = []
        environment_set = False
        for record in records:
            if cls._SLOT_KEY in record:
                slots_set.append(str(record[cls._SLOT_KEY]))
            elif cls._HIDDEN_TEST_KEY in record:
                tests_seen.append(cls._hidden_test_key(record[cls._HIDDEN_TEST_KEY]))
            elif cls._RUBRIC_CRITERION_KEY in record:
                ids_seen.append(cls._criterion_id(record[cls._RUBRIC_CRITERION_KEY]))
            elif cls._ENVIRONMENT_KEY in record:
                environment_set = True
        unique_tests = list(dict.fromkeys(tests_seen))
        unique_ids = list(dict.fromkeys(ids_seen))
        return FixAnnotateStats(
            slots_set=list(dict.fromkeys(slots_set)),
            hidden_tests_added=sum(1 for key in unique_tests if key not in old_tests),
            hidden_tests_replaced=[f"{path}::{name}" for path, name in unique_tests if (path, name) in old_tests],
            rubric_criteria_added=sum(1 for criterion_id in unique_ids if criterion_id not in old_ids),
            rubric_criteria_replaced=[criterion_id for criterion_id in unique_ids if criterion_id in old_ids],
            environment_set=environment_set,
        )

    @staticmethod
    def _hidden_test_key(payload: object) -> tuple[str, str]:
        """The ``(repo_path, name)`` key of a hidden-test record that already passed the merge."""
        fields: dict[str, object] = payload if isinstance(payload, dict) else {}
        return str(fields.get("repo_path", "")), str(fields.get("name", ""))

    @staticmethod
    def _criterion_id(payload: object) -> str:
        """The ``id`` of a rubric-criterion record that already passed the merge."""
        fields: dict[str, object] = payload if isinstance(payload, dict) else {}
        return str(fields.get("id", ""))

    @staticmethod
    def remaining_fix_slots(candidate: FixCandidateDict) -> list[str]:
        """The coding slots still empty: the ``fix.slot-empty`` pointers of :meth:`FixCandidate.validate_filled`."""
        return [violation.pointer for violation in FixCandidate.validate_filled(candidate) if violation.code == "fix.slot-empty"]


def _is_string_list(value: object) -> bool:
    """True for a JSON list whose every item is a string."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
