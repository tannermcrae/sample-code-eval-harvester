"""The coding candidate file: the facts of one merged fix, and the empty judgment slots for the agent.

A coding datapoint starts as ``candidates/fix-<n>.json``. ``capture --type coding`` fills the *facts*
— the PR and issue text, the merge commit and its first parent (the base), the two halves of the fix
on disk as ``patches/fix-<n>/source.patch`` and ``patches/fix-<n>/tests.patch``, and the line counts —
and leaves every *judgment slot* empty: the instruction, the tooling note, the hidden tests, the rubric
criteria, and the environment recipe. The agent fills the slots; ``emit --kind fix`` reads both halves.

**A second module, not a second schema in ``candidate.py`` (ADR-1).** The review candidate's schema is
pinned (tech plan §3, Compatibility), so the coding candidate lives here. It reuses the review
candidate's :class:`~eval_harvest.candidate.Violation`, so ``emit`` reports both types in one refusal
shape (FR-30), and its byte-stable JSON round-trip.

**No forge call here.** :meth:`FixCandidate.write_to_dataset` reads only the local clone, through
:class:`~eval_harvest.gitcmd.GitCommandRunner`; the CLI builds :class:`FixFacts` from the forge first.
It makes no ``git fetch`` either: the merge commit is already on the mainline of the clone.

**Determinism (NFR-1).** Every date comes from the forge or from git, the lists are sorted, and the
file is written with ``sort_keys=False`` in the §8 field order, so two captures give the same bytes.

**The validators never raise** (pr-eval-harvest ADR-2): each returns a ``list[Violation]``, so ``emit``
and ``annotate`` can report every problem at once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, TypedDict, TypeGuard  # a loaded candidate is arbitrary JSON; the validators read it defensively.

from eval_harvest.candidate import Violation, newline_terminated
from eval_harvest.gitcmd import GitCommandRunner
from eval_harvest.patchsplit import PatchSplit

#: The most characters of a PR or issue body the candidate keeps (FR-13); the brief stays small.
BODY_CHAR_LIMIT: Final = 4000

#: The ``@TOOLING@`` slot is one line of at most this many characters.
TOOLING_NOTE_MAX_CHARS: Final = 200

#: The two kinds of hidden test, and where each one came from: the PR's own tests, or the agent's.
HIDDEN_TEST_KINDS: Final = ("fail_to_pass", "pass_to_pass")
HIDDEN_TEST_ORIGINS: Final = ("pr", "agent")

#: The kinds a rubric criterion can grade.
RUBRIC_CRITERION_KINDS: Final = ("fail_to_pass", "pass_to_pass")

#: The one registry a coding task's base image may come from — the same rule as ``emit.py``'s
#: ``_AGENT_BASE_IMAGE``: Docker Hub rate-limits anonymous pulls, ECR Public does not.
ALLOWED_BASE_IMAGE_PREFIX: Final = "public.ecr.aws/"

#: The coding fill contract — one short line for each slot the agent owns — that ``brief`` prints under
#: "you fill". F-1 writes the full method text in the help; these lines only name each slot and its shape.
FIX_FILL_CONTRACT_GUIDE: Final = (
    "instruction — the symptom and the expected behavior; no file paths, function names, or cause",
    "instruction_rationale — why this instruction gives enough information and does not give the answer",
    f"tooling_note — one line, at most {TOOLING_NOTE_MAX_CHARS} characters, how to run the tests in this repository",
    "hidden_tests[] — each with repo_path, name, kind (fail_to_pass|pass_to_pass), origin (pr|agent), and evidence;"
    " put each file in candidates/fix-<n>/hidden/<repo_path>",
    "rubric_criteria[] — each with id, kind, and text",
    "environment — base_image (from public.ecr.aws/), setup_steps (single lines), lockfile (a file in"
    " candidates/fix-<n>/), test_command (writes JUnit XML to $JUNIT_PATH), and rationale; pin dependencies to merge_date",
)

#: The ``datapoint_type`` of a candidate with no such field: every review candidate written before C-1.
_REVIEW_DATAPOINT_TYPE: Final = "review"

#: The length of a ``YYYY-MM-DD`` date.
_ISO_DATE_LENGTH: Final = 10

#: The ``git diff --numstat`` count git writes for a binary file instead of a line count.
_BINARY_COUNT: Final = "-"

#: Fields of a ``-z`` numstat record before the path: added, deleted.
_NUMSTAT_COUNT_FIELDS: Final = 2

#: The environment recipe's string fields, in §8 order; ``setup_steps`` is the list between them.
_ENVIRONMENT_SLOTS: Final = ("base_image", "setup_steps", "lockfile", "test_command", "rationale")

#: The top-level text slots, in §8 order.
_TEXT_SLOTS: Final = ("instruction", "instruction_rationale", "tooling_note")


class FixIssueDict(TypedDict):
    """One linked issue as stored: its page, title, and body cut to :data:`BODY_CHAR_LIMIT`."""

    number: int
    url: str
    title: str
    body: str
    body_truncated: bool


class HiddenTestDict(TypedDict):
    """One hidden test the agent names: its file in the repository, its name, kind, origin, and evidence."""

    repo_path: str
    name: str
    kind: str  # "fail_to_pass" | "pass_to_pass"
    origin: str  # "pr" | "agent"
    evidence: str


class RubricCriterionDict(TypedDict):
    """One rubric criterion the agent writes."""

    id: str
    kind: str  # "fail_to_pass" | "pass_to_pass"
    text: str


class EnvironmentDict(TypedDict):
    """The environment recipe the agent writes (ADR-3); the CLI checks only its form."""

    base_image: str
    setup_steps: list[str]
    lockfile: str
    test_command: str
    rationale: str


class FixCandidateDict(TypedDict):
    """The whole coding candidate file (tech plan §8), in its field order: facts, then the agent's slots."""

    datapoint_type: str
    task_type: str
    repo: str
    pr_number: int
    pr_url: str
    pr_title: str
    pr_body: str
    pr_body_truncated: bool
    issues: list[FixIssueDict]
    merge_commit: str
    base_commit: str
    merged_at: str
    merge_date: str
    base_commit_date: str
    source_patch_path: str
    test_patch_path: str
    source_paths: list[str]
    test_paths: list[str]
    source_lines_added: int
    source_lines_deleted: int
    author_commits: int
    instruction: str
    instruction_rationale: str
    tooling_note: str
    hidden_tests: list[HiddenTestDict]
    rubric_criteria: list[RubricCriterionDict]
    environment: EnvironmentDict


@dataclass(frozen=True, slots=True)
class FixIssue:
    """One linked issue as the forge returned it; the candidate cuts the body."""

    number: int
    url: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class FixFacts:
    """The forge facts of one merged fix, which the CLI reads before :meth:`FixCandidate.write_to_dataset`."""

    pr_number: int
    pr_url: str
    pr_title: str
    pr_body: str
    merge_commit: str
    merged_at: str
    issues: tuple[FixIssue, ...]
    author_commits: int


class FixCaptureError(Exception):
    """A coding capture the CLI refuses: ``check`` is the refusal name, ``offending`` says why."""

    def __init__(self, check: str, offending: str) -> None:
        super().__init__(f"{check}: {offending}")
        self.check = check
        self.offending = offending


@dataclass(frozen=True, slots=True)
class _ChangedPath:
    """One changed path from ``git diff --numstat -z``: its new name and its line counts (0 for a binary)."""

    path: str
    added: int
    deleted: int


@dataclass(frozen=True, slots=True)
class _FixGeometry:
    """What git says about one fix: the base, its date, the two patches, and the changed paths."""

    base_commit: str
    base_commit_date: str
    source_patch: str
    test_patch: str
    changed: tuple[_ChangedPath, ...]


class FixCandidate:
    """Writes, reads, and checks the coding candidate file. Holds no state."""

    # ───────────────────────────── paths (pure functions of the PR number) ─────────────────────────────

    @staticmethod
    def candidate_relpath(pr_number: int) -> str:
        """``candidates/fix-<n>.json``, relative to the dataset."""
        return f"candidates/fix-{pr_number}.json"

    @staticmethod
    def workdir_relpath(pr_number: int) -> str:
        """``candidates/fix-<n>``: the directory for the hidden tests, the validation spec, and the lockfile."""
        return f"candidates/fix-{pr_number}"

    @staticmethod
    def source_patch_relpath(pr_number: int) -> str:
        """``patches/fix-<n>/source.patch``: the oracle half of the fix."""
        return f"patches/fix-{pr_number}/source.patch"

    @staticmethod
    def test_patch_relpath(pr_number: int) -> str:
        """``patches/fix-<n>/tests.patch``: the test half of the fix."""
        return f"patches/fix-{pr_number}/tests.patch"

    # ───────────────────────────── capture core (local git only) ─────────────────────────────

    @classmethod
    def write_to_dataset(cls, facts: FixFacts, *, repo: str, clone: Path, dataset_dir: Path) -> Path:
        """Write the two patches and ``candidates/fix-<n>.json`` for one merged fix; return the candidate path.

        Raises :class:`FixCaptureError` (``fix-base-unrecoverable`` or ``no-source-change``) before it
        writes any file, because a candidate with no base or no source patch can never be emitted.
        """
        geometry = cls._read_fix_geometry(facts.merge_commit, clone)
        if not geometry.source_patch:
            raise FixCaptureError(
                "no-source-change",
                f"the diff {geometry.base_commit[:12]}..{facts.merge_commit[:12]} changes only test files",
            )
        cls._write_patch(dataset_dir / cls.source_patch_relpath(facts.pr_number), geometry.source_patch)
        cls._write_patch(dataset_dir / cls.test_patch_relpath(facts.pr_number), geometry.test_patch)
        source = [entry for entry in geometry.changed if not PatchSplit.is_test_path(entry.path)]
        candidate = cls.scaffold(
            facts,
            repo=repo,
            base_commit=geometry.base_commit,
            base_commit_date=geometry.base_commit_date,
            source_paths=[entry.path for entry in source],
            test_paths=[entry.path for entry in geometry.changed if PatchSplit.is_test_path(entry.path)],
            lines_added=sum(entry.added for entry in source),
            lines_deleted=sum(entry.deleted for entry in source),
        )
        candidate_path = dataset_dir / cls.candidate_relpath(facts.pr_number)
        cls.dump(candidate, candidate_path)
        return candidate_path

    @classmethod
    def _read_fix_geometry(cls, merge_commit: str, clone: Path) -> _FixGeometry:
        """The base (first parent), its date, the split diff, and the numstat of ``base..merge``."""
        base = cls._first_parent_or_refuse(merge_commit, clone)
        _, base_date = GitCommandRunner.git(clone, "show", "-s", "--format=%cs", base)
        diff_code, diff = GitCommandRunner.git(clone, "diff", base, merge_commit)
        if diff_code != 0:
            raise FixCaptureError("fix-base-unrecoverable", f"`git diff {base} {merge_commit}` failed in the clone {clone}")
        split = PatchSplit.split(diff)
        _, numstat = GitCommandRunner.git(clone, "diff", "--numstat", "-z", base, merge_commit)
        return _FixGeometry(
            base_commit=base,
            base_commit_date=base_date,
            source_patch=split.source_patch,
            test_patch=split.test_patch,
            changed=cls.parse_numstat(numstat),
        )

    @staticmethod
    def _first_parent_or_refuse(merge_commit: str, clone: Path) -> str:
        """The first parent of ``merge_commit``, or ``fix-base-unrecoverable`` when the clone cannot give it.

        The first parent, never the merge-base: a coding task needs the mainline state just before the
        fix landed, which for a true merge is parent 1 and for a squash commit its only parent.
        """
        if not merge_commit:
            raise FixCaptureError(
                "fix-base-unrecoverable",
                "the forge gives no merge commit for this pull request (GitLab does not expose the squash commit of this MR)",
            )
        code, _ = GitCommandRunner.git(clone, "rev-parse", "--verify", "--quiet", f"{merge_commit}^{{commit}}")
        if code != 0:
            raise FixCaptureError("fix-base-unrecoverable", f"the merge commit {merge_commit} is not in the clone {clone}")
        _, parents_line = GitCommandRunner.git(clone, "rev-list", "--parents", "-n", "1", merge_commit)
        parents = parents_line.split()[1:]
        if not parents:
            raise FixCaptureError("fix-base-unrecoverable", f"the merge commit {merge_commit} is a root commit with no parent")
        base = parents[0]
        base_code, _ = GitCommandRunner.git(clone, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
        if base_code != 0:
            raise FixCaptureError(
                "fix-base-unrecoverable", f"the first parent {base} of {merge_commit} is not in the clone {clone}"
            )
        return base

    @staticmethod
    def _write_patch(path: Path, patch: str) -> None:
        """Write one patch newline-terminated; an empty patch is still written, as an empty file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(newline_terminated(patch))

    @classmethod
    def parse_numstat(cls, output: str) -> tuple[_ChangedPath, ...]:
        """Parse ``git diff --numstat -z``; a rename's new path wins, and a binary file counts 0 lines.

        A plain record is ``added\\tdeleted\\tpath\\0``; a rename is ``added\\tdeleted\\t\\0old\\0new\\0``.
        """
        items = [item for item in output.split("\0") if item]
        parsed: list[_ChangedPath] = []
        index = 0
        while index < len(items):
            fields = items[index].split("\t", _NUMSTAT_COUNT_FIELDS)
            index += 1
            if len(fields) <= _NUMSTAT_COUNT_FIELDS:
                continue  # not a record head; git never writes one, but a short read must not crash
            path = fields[2]
            if not path:  # a rename: the next two items are the old path and the new path
                path = items[index + 1] if index + 1 < len(items) else ""
                index += 2
            if path:
                parsed.append(_ChangedPath(path, cls._line_count(fields[0]), cls._line_count(fields[1])))
        return tuple(parsed)

    @staticmethod
    def _line_count(field: str) -> int:
        """One numstat count; git's ``-`` for a binary file counts as 0 lines."""
        return 0 if field == _BINARY_COUNT or not field.isdigit() else int(field)

    # ───────────────────────────── scaffold (pure) ─────────────────────────────

    # The facts bundle plus the seven git facts write_to_dataset read; the count is the §8 facts half.
    @classmethod
    def scaffold(  # noqa: PLR0913
        cls,
        facts: FixFacts,
        *,
        repo: str,
        base_commit: str,
        base_commit_date: str,
        source_paths: list[str],
        test_paths: list[str],
        lines_added: int,
        lines_deleted: int,
    ) -> FixCandidateDict:
        """The candidate dict in §8 order: every fact filled, every judgment slot empty (FR-16)."""
        pr_body, pr_body_truncated = cls._cut_body(facts.pr_body)
        return FixCandidateDict(
            datapoint_type="coding",
            task_type="investigate-and-fix",
            repo=repo,
            pr_number=facts.pr_number,
            pr_url=facts.pr_url,
            pr_title=facts.pr_title,
            pr_body=pr_body,
            pr_body_truncated=pr_body_truncated,
            issues=[cls._issue_dict(issue) for issue in sorted(facts.issues, key=lambda issue: issue.number)],
            merge_commit=facts.merge_commit,
            base_commit=base_commit,
            merged_at=facts.merged_at,
            merge_date=facts.merged_at[:_ISO_DATE_LENGTH],
            base_commit_date=base_commit_date,
            source_patch_path=cls.source_patch_relpath(facts.pr_number),
            test_patch_path=cls.test_patch_relpath(facts.pr_number),
            source_paths=sorted(source_paths),
            test_paths=sorted(test_paths),
            source_lines_added=lines_added,
            source_lines_deleted=lines_deleted,
            author_commits=facts.author_commits,
            instruction="",
            instruction_rationale="",
            tooling_note="",
            hidden_tests=[],
            rubric_criteria=[],
            environment=EnvironmentDict(base_image="", setup_steps=[], lockfile="", test_command="", rationale=""),
        )

    @classmethod
    def _issue_dict(cls, issue: FixIssue) -> FixIssueDict:
        """One issue as stored, its body cut to the limit."""
        body, truncated = cls._cut_body(issue.body)
        return FixIssueDict(number=issue.number, url=issue.url, title=issue.title, body=body, body_truncated=truncated)

    @staticmethod
    def _cut_body(body: str) -> tuple[str, bool]:
        """``body`` cut to :data:`BODY_CHAR_LIMIT` characters, and whether the cut removed anything."""
        return body[:BODY_CHAR_LIMIT], len(body) > BODY_CHAR_LIMIT

    # ───────────────────────────── byte-stable JSON round-trip ─────────────────────────────

    @staticmethod
    def dumps(candidate: FixCandidateDict) -> bytes:
        """The canonical bytes: UTF-8, two-space indent, insertion order, LF-terminated (as ``Candidate.dumps``)."""
        text = json.dumps(candidate, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        return text.encode("utf-8")

    @classmethod
    def dump(cls, candidate: FixCandidateDict, path: Path) -> None:
        """Write the candidate to ``path`` as canonical bytes, creating parents."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cls.dumps(candidate))

    @staticmethod
    def load(path: Path) -> FixCandidateDict:
        """Read a coding candidate back (stdlib ``json``, no validation here)."""
        loaded: FixCandidateDict = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    @staticmethod
    def datapoint_type_of(path: Path) -> str:
        """The candidate's ``datapoint_type``; a candidate without one is a review candidate."""
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return _REVIEW_DATAPOINT_TYPE
        return str(loaded.get("datapoint_type", _REVIEW_DATAPOINT_TYPE))

    # ───────────────────────────── validation (never raises) ─────────────────────────────

    @staticmethod
    def validate_facts(candidate: FixCandidateDict | dict[str, Any]) -> list[Violation]:
        """Every missing or malformed fact that ``capture`` writes, as ``fix.facts-missing`` violations."""
        problems = [
            ("datapoint_type", candidate.get("datapoint_type") == "coding", '`datapoint_type` must be "coding"'),
            ("repo", bool(candidate.get("repo")), "`repo` is required"),
            ("pr_number", _is_positive_int(candidate.get("pr_number")), "`pr_number` must be a positive integer"),
            ("pr_url", bool(candidate.get("pr_url")), "`pr_url` is required"),
            ("merge_commit", bool(candidate.get("merge_commit")), "`merge_commit` is required"),
            ("base_commit", bool(candidate.get("base_commit")), "`base_commit` is required"),
            ("merge_date", _is_iso_date(candidate.get("merge_date")), "`merge_date` must be YYYY-MM-DD"),
            ("source_patch_path", bool(candidate.get("source_patch_path")), "`source_patch_path` is required"),
            ("source_paths", bool(candidate.get("source_paths")), "`source_paths` must name at least one source file"),
        ]
        return [Violation("fix.facts-missing", pointer, detail) for pointer, ok, detail in problems if not ok]

    @classmethod
    def validate_filled(cls, candidate: FixCandidateDict | dict[str, Any]) -> list[Violation]:
        """Every empty or invalid judgment slot.

        ``fix.slot-empty`` and ``fix.no-fail-to-pass-test`` mean "not filled yet"; every other code is a
        wrong value. ``annotate`` (C-2) relies on that split.
        """
        violations = cls._empty_slots(candidate)
        violations += cls._check_tooling_note(candidate.get("tooling_note"))
        violations += cls._check_hidden_tests(candidate.get("hidden_tests"))
        violations += cls._check_rubric_criteria(candidate.get("rubric_criteria"))
        return violations

    @staticmethod
    def _empty_slots(candidate: FixCandidateDict | dict[str, Any]) -> list[Violation]:
        """One ``fix.slot-empty`` for each top-level slot and each environment field that is still empty."""
        environment: Any = candidate.get("environment")  # arbitrary JSON until checked
        environment_values: dict[str, Any] = environment if isinstance(environment, dict) else {}
        pointers = [slot for slot in _TEXT_SLOTS if not candidate.get(slot)]
        pointers += [slot for slot in ("hidden_tests", "rubric_criteria") if not candidate.get(slot)]
        pointers += [f"environment.{field}" for field in _ENVIRONMENT_SLOTS if not environment_values.get(field)]
        return [Violation("fix.slot-empty", pointer, "the agent has not filled this slot") for pointer in pointers]

    @staticmethod
    def _check_tooling_note(note: object) -> list[Violation]:
        """The ``@TOOLING@`` slot is one line of at most :data:`TOOLING_NOTE_MAX_CHARS` characters."""
        if not note:
            return []  # reported as fix.slot-empty
        if not isinstance(note, str) or "\n" in note or "\r" in note or len(note) > TOOLING_NOTE_MAX_CHARS:
            detail = f"`tooling_note` must be one line of at most {TOOLING_NOTE_MAX_CHARS} characters"
            return [Violation("fix.slot-invalid", "tooling_note", detail)]
        return []

    @classmethod
    def _check_hidden_tests(cls, hidden_tests: object) -> list[Violation]:
        """Each hidden test's kind, origin, and evidence, and at least one ``fail_to_pass`` entry."""
        if not hidden_tests:
            return []  # reported as fix.slot-empty
        if not isinstance(hidden_tests, list):
            return [Violation("fix.hidden-test-invalid", "hidden_tests", "`hidden_tests` must be a list")]
        violations = [
            violation for index, entry in enumerate(hidden_tests) for violation in cls._check_one_hidden_test(index, entry)
        ]
        if not any(isinstance(entry, dict) and entry.get("kind") == "fail_to_pass" for entry in hidden_tests):
            detail = "no hidden test is fail_to_pass, so the task cannot fail at the base"
            violations.append(Violation("fix.no-fail-to-pass-test", "hidden_tests", detail))
        return violations

    @staticmethod
    def _check_one_hidden_test(index: int, entry: object) -> list[Violation]:
        """One hidden test's form: a known kind and origin, a name and a path, and some evidence."""
        pointer = f"hidden_tests[{index}]"
        if not isinstance(entry, dict):
            return [Violation("fix.hidden-test-invalid", pointer, "a hidden test must be an object")]
        problems = [
            ("kind", entry.get("kind") in HIDDEN_TEST_KINDS, f"`kind` must be one of {HIDDEN_TEST_KINDS}"),
            ("origin", entry.get("origin") in HIDDEN_TEST_ORIGINS, f"`origin` must be one of {HIDDEN_TEST_ORIGINS}"),
            ("evidence", bool(entry.get("evidence")), "`evidence` must say why this test belongs to the fix"),
            ("repo_path", bool(entry.get("repo_path")), "`repo_path` is required"),
            ("name", bool(entry.get("name")), "`name` is required"),
        ]
        return [Violation("fix.hidden-test-invalid", f"{pointer}.{field}", detail) for field, ok, detail in problems if not ok]

    @classmethod
    def _check_rubric_criteria(cls, criteria: object) -> list[Violation]:
        """Each criterion has a unique non-empty id, a known kind, and text."""
        if not criteria:
            return []  # reported as fix.slot-empty
        if not isinstance(criteria, list):
            return [Violation("fix.rubric-criterion-invalid", "rubric_criteria", "`rubric_criteria` must be a list")]
        violations: list[Violation] = []
        seen_ids: set[str] = set()
        for index, entry in enumerate(criteria):
            violations += cls._check_one_criterion(index, entry, seen_ids)
        return violations

    @staticmethod
    def _check_one_criterion(index: int, entry: object, seen_ids: set[str]) -> list[Violation]:
        """One criterion's form; ``seen_ids`` collects the ids so a repeat is reported."""
        pointer = f"rubric_criteria[{index}]"
        if not isinstance(entry, dict):
            return [Violation("fix.rubric-criterion-invalid", pointer, "a rubric criterion must be an object")]
        criterion_id = entry.get("id")
        problems = [
            ("id", bool(criterion_id), "`id` is required"),
            ("id", not criterion_id or str(criterion_id) not in seen_ids, f"`id` {criterion_id!r} is used twice"),
            ("kind", entry.get("kind") in RUBRIC_CRITERION_KINDS, f"`kind` must be one of {RUBRIC_CRITERION_KINDS}"),
            ("text", bool(entry.get("text")), "`text` is required"),
        ]
        if criterion_id:
            seen_ids.add(str(criterion_id))
        return [
            Violation("fix.rubric-criterion-invalid", f"{pointer}.{field}", detail) for field, ok, detail in problems if not ok
        ]

    @classmethod
    def validate_hidden_test_files(cls, candidate: FixCandidateDict | dict[str, Any], dataset_dir: Path) -> list[Violation]:
        """Each hidden test's file exists under ``candidates/fix-<n>/hidden/``; a path that could leave it is refused."""
        hidden_tests = candidate.get("hidden_tests")
        if not isinstance(hidden_tests, list) or not _is_positive_int(candidate.get("pr_number")):
            return []
        hidden_root = dataset_dir / cls.workdir_relpath(int(candidate["pr_number"])) / "hidden"
        return [
            violation
            for index, entry in enumerate(hidden_tests)
            if isinstance(entry, dict)
            for violation in cls._check_one_hidden_test_file(index, entry.get("repo_path"), hidden_root)
        ]

    @staticmethod
    def _check_one_hidden_test_file(index: int, repo_path: object, hidden_root: Path) -> list[Violation]:
        """One hidden test's file; the path is checked before the disk is read."""
        pointer = f"hidden_tests[{index}].repo_path"
        if not isinstance(repo_path, str) or not _is_safe_relative_path(repo_path):
            detail = f"`repo_path` {repo_path!r} must be a relative path inside the repository, with no `..` part"
            return [Violation("fix.hidden-test-invalid", pointer, detail)]
        if not (hidden_root / repo_path).is_file():
            detail = f"{hidden_root / repo_path} does not exist; write the hidden test file there"
            return [Violation("fix.hidden-test-file-missing", pointer, detail)]
        return []

    @classmethod
    def validate_recipe(cls, candidate: FixCandidateDict | dict[str, Any], dataset_dir: Path) -> list[Violation]:
        """The form of the environment recipe (ADR-3): single-line steps, an ECR Public image, a present lockfile.

        A line break or a trailing backslash in a step would let the recipe add its own Dockerfile line
        (FR-21, NFR-7), so each is refused here, before D-2 writes any Dockerfile.
        """
        environment = candidate.get("environment")
        if not isinstance(environment, dict):
            return [Violation("fix.environment-recipe-invalid", "environment", "`environment` must be an object")]
        violations = cls._check_setup_steps(environment.get("setup_steps"))
        violations += cls._check_base_image(environment.get("base_image"))
        violations += cls._check_lockfile(environment.get("lockfile"), candidate.get("pr_number"), dataset_dir)
        violations += cls._check_test_command(environment.get("test_command"))
        return violations

    @staticmethod
    def _check_setup_steps(steps: object) -> list[Violation]:
        """Each setup step is one line that does not continue onto the next."""
        if not steps:
            return []  # reported as fix.slot-empty
        if not isinstance(steps, list):
            return [Violation("fix.environment-recipe-invalid", "environment.setup_steps", "`setup_steps` must be a list")]
        return [
            Violation(
                "fix.environment-recipe-invalid",
                f"environment.setup_steps[{index}]",
                "a setup step must be one line, with no line break and no trailing backslash",
            )
            for index, step in enumerate(steps)
            if not isinstance(step, str) or "\n" in step or "\r" in step or step.rstrip().endswith("\\")
        ]

    @staticmethod
    def _check_base_image(base_image: object) -> list[Violation]:
        """The base image comes from ECR Public."""
        if not base_image:
            return []  # reported as fix.slot-empty
        if not isinstance(base_image, str) or not base_image.startswith(ALLOWED_BASE_IMAGE_PREFIX):
            detail = f"`base_image` {base_image!r} must start with {ALLOWED_BASE_IMAGE_PREFIX!r} (Docker Hub rate-limits pulls)"
            return [Violation("fix.environment-recipe-invalid", "environment.base_image", detail)]
        return []

    @classmethod
    def _check_lockfile(cls, lockfile: object, pr_number: object, dataset_dir: Path) -> list[Violation]:
        """The lockfile is a plain file name, present in ``candidates/fix-<n>/``."""
        pointer = "environment.lockfile"
        if not lockfile:
            return []  # reported as fix.slot-empty
        if not isinstance(lockfile, str) or "/" in lockfile or "\\" in lockfile or lockfile in {".", ".."}:
            return [Violation("fix.environment-recipe-invalid", pointer, f"`lockfile` {lockfile!r} must be a plain file name")]
        if not _is_positive_int(pr_number):
            return [Violation("fix.environment-recipe-invalid", pointer, "no `pr_number`, so the lockfile cannot be found")]
        path = dataset_dir / cls.workdir_relpath(pr_number) / lockfile
        if not path.is_file():
            return [Violation("fix.environment-recipe-invalid", pointer, f"{path} does not exist; write the lockfile there")]
        return []

    @staticmethod
    def _check_test_command(test_command: object) -> list[Violation]:
        """The test command is one line."""
        if not test_command:
            return []  # reported as fix.slot-empty
        if not isinstance(test_command, str) or "\n" in test_command or "\r" in test_command:
            detail = "`test_command` must be one line"
            return [Violation("fix.environment-recipe-invalid", "environment.test_command", detail)]
        return []


def _is_positive_int(value: object) -> TypeGuard[int]:
    """True for an ``int`` (not a ``bool``) above zero."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_iso_date(value: object) -> bool:
    """True for a ``YYYY-MM-DD`` string."""
    return isinstance(value, str) and len(value) == _ISO_DATE_LENGTH and value[4] == "-" and value[7] == "-"


def _is_safe_relative_path(repo_path: str) -> bool:
    """True for a non-empty relative POSIX path with no ``..`` part and no backslash or drive."""
    if not repo_path or "\\" in repo_path or ":" in repo_path:
        return False
    pure = PurePosixPath(repo_path)
    return not pure.is_absolute() and ".." not in pure.parts
