"""The `emit --kind fix` verb: turn a filled coding candidate into a verified Harbor coding task.

The coding counterpart of :mod:`eval_harvest.emit` (ADR-1: one verb, a new module). It reads a filled
``candidates/fix-<n>.json``, the files the agent wrote next to it in ``candidates/fix-<n>/`` (the
hidden tests, the validation spec, the lockfile), and the fix's source patch, and assembles the
tech-plan §6 coding task directory from the ``verifier_tpl/fix/`` templates (D-1).

**Assemble-then-verify-then-promote, the review rule (§7.2).** The task is built in a temporary
directory, the coding ``verify`` (E-1, E-2) runs over it, and it is promoted to
``tasks/<repo>__pr<n>-fix/`` only when no check failed, or each failure is named in ``--override``. An
*unresolved* check does not block (FR-31): ``hidden-tests-proven`` stays unresolved until a runner
writes a proof file, and ``git-channel-absence`` until a container runtime materializes the seal.

**Shared helpers, not copies.** The digest, the materialization, the layout parse, and the promotion
are the pure static helpers of :class:`~eval_harvest.emit.Emit`; the review path's own ``_TaskPlan``
and ``_task_files`` are not touched, so the review task bytes do not change (NFR-9).

**No `metric.py` here.** The review emitter writes a review-only ``metric.py``; ``dataset`` (F-1) writes
the per-type one. Writing a coding-only file here would be overwritten by the next review emit.

**Offline and deterministic (NFR-1, NFR-2).** No git or network call: every byte comes from the
dataset directory and the templates, the hidden tests and the TOML tables are sorted, so two emits of
one candidate give the same directory and the same ``content_digest``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from eval_harvest.emit import (
    _REFUSAL_EXIT_CODE,
    _VERIFICATION_EXIT_CODE,
    _VERIFIER_BOTO3_PIN,
    Emit,
    EmitRefusalError,
    EmitResult,
    HookResult,
    VerifyHook,
    _real_verify_hook,
    _TaskFile,
)
from eval_harvest.fix_candidate import FixCandidate, FixCandidateDict, HiddenTestDict
from eval_harvest.forge_host import ForgeHost
from eval_harvest.harbor import FixHarvestFields, FixTaskOrigin, Harbor
from eval_harvest.patchsplit import TEST_MARKERS
from eval_harvest.tomlw import TomlValue, emit_document
from eval_harvest.verify import HIDDEN_TESTS_RELDIR, ORACLE_PATCH_RELPATH, REFERENCE_PATCH_RELPATH

#: The `[task].description` of every coding task: the notebook 03 text, the same for every task so it
#: tells the agent nothing about one fix (FR-26).
_FIX_TASK_DESCRIPTION: Final = (
    "Fix the bug in the issue report. Hidden tests, a validation agent, a rubric, and a taste judge grade the fix."
)
_FIX_KEYWORDS: Final = ("coding-agent", "bug-fix", "eval-harvest")

#: The agent's tree reaches the separate verifier as an artifact, without `.git` (ADR-4).
_FIX_ARTIFACTS: Final[tuple[dict[str, TomlValue], ...]] = ({"source": "/work/repo", "exclude": [".git"]},)

#: The reward stack calls Bedrock (D-1). Harbor's Docker environment applies `no-network` to the
#: verifier too, so the verifier gets an allowlist with the one Bedrock runtime host (notebook 03).
_VERIFIER_NETWORK_MODE: Final = "allowlist"
_VERIFIER_ALLOWED_HOSTS: Final = ("bedrock-runtime.us-east-1.amazonaws.com",)

#: The `[verifier.env]` table of notebook 03: references to the host shell, never secret values.
_VERIFIER_ENV: Final[dict[str, str]] = {
    "VERIFIER_MODEL_ID": "${VERIFIER_MODEL_ID:-us.anthropic.claude-haiku-5-5}",
    "AWS_ACCESS_KEY_ID": "${AWS_ACCESS_KEY_ID:-}",
    "AWS_SECRET_ACCESS_KEY": "${AWS_SECRET_ACCESS_KEY:-}",
    "AWS_SESSION_TOKEN": "${AWS_SESSION_TOKEN:-}",
    "AWS_REGION": "${AWS_REGION:-us-east-1}",
    "AWS_DEFAULT_REGION": "${AWS_REGION:-us-east-1}",
}

#: `tests/verify/tests.toml` (the D-1 schema): the hidden-test timeout, and the markers beyond
#: `TEST_MARKERS` whose files a fix may not change without the bloat judge counting them as tests.
_HIDDEN_TESTS_TIMEOUT_SEC: Final = 900
_EXTRA_PROTECTED_MARKERS: Final = ("sitecustomize.py", "usercustomize.py", "pytest.ini")

#: The two instruction slots. A slot value that contains a slot token would be replaced again.
_TASK_SLOT: Final = "@TASK@"
_TOOLING_SLOT: Final = "@TOOLING@"

_VALIDATION_SPEC_FILENAME: Final = "validation_spec.toml"
_LIB_TEMPLATE_DIR: Final = "fix/lib"

#: `validate_filled` reports a missing fail-to-pass test too; emit refuses it under its own name, last.
_NO_FAIL_TO_PASS_CODE: Final = "fix.no-fail-to-pass-test"

_FILL_NEXT: Final = "fill every coding slot with `annotate` and re-run emit"


class FixEmit:
    """Assemble, verify, and promote one coding datapoint's Harbor task directory. Holds no state."""

    # ───────────────────────────── the verb entry point ─────────────────────────────

    # Five parameters because emit's contract needs each: the candidate, the dataset, the recorded
    # overrides, the local clone handed to `verify`, and the test-only `verify_hook` seam.
    @classmethod
    def emit_datapoint(  # noqa: PLR0913
        cls,
        candidate: FixCandidateDict,
        *,
        dataset_dir: Path,
        overrides: tuple[str, ...] = (),
        clone: Path | None = None,
        verify_hook: VerifyHook | None = None,
    ) -> EmitResult:
        """Build `tasks/<repo>__pr<n>-fix/` and return its `EmitResult`, or raise `EmitRefusalError`.

        The four input refusals come first, in order, each exit 3: `unfilled-candidate`,
        `environment-recipe-invalid`, `hidden-test-file-missing`, `no-fail-to-pass-test`. Then the task
        is assembled in a temporary directory, validated against Harbor's schema (exit 4), verified,
        and promoted unless a check failed (exit 4). Unresolved checks ride out in the result.
        """
        if verify_hook is None:
            verify_hook = _real_verify_hook(clone)
        datapoint = f"pr-{candidate.get('pr_number')}/fix"
        cls._refuse_unfilled(candidate, dataset_dir, datapoint)
        cls._refuse_invalid_recipe(candidate, dataset_dir, datapoint)
        cls._refuse_missing_hidden_test_files(candidate, dataset_dir, datapoint)
        cls._refuse_no_fail_to_pass(candidate, datapoint)
        plan = cls._plan(candidate, dataset_dir, overrides)
        return cls._assemble_verify_promote(plan, dataset_dir, verify_hook)

    # ───────────────────────────── input refusals (exit 3) ─────────────────────────────

    @classmethod
    def _refuse_unfilled(cls, candidate: FixCandidateDict, dataset_dir: Path, datapoint: str) -> None:
        """Refuse missing facts, an empty or invalid slot, a slot token in a slot, or no validation spec."""
        violations = FixCandidate.validate_facts(candidate) + FixCandidate.validate_filled(candidate)
        problems = [str(violation) for violation in violations if violation.code != _NO_FAIL_TO_PASS_CODE]
        problems += cls._slot_token_problems(candidate)
        if not problems:
            spec_path = cls._workdir(candidate, dataset_dir) / _VALIDATION_SPEC_FILENAME
            if not spec_path.is_file():
                problems.append(f"{spec_path} does not exist; write the validation spec there")
        if problems:
            raise EmitRefusalError(
                check="unfilled-candidate",
                datapoint=datapoint,
                offending="; ".join(problems),
                next_=_FILL_NEXT,
                exit_code=_REFUSAL_EXIT_CODE,
            )

    @staticmethod
    def _slot_token_problems(candidate: FixCandidateDict) -> list[str]:
        """A slot value that holds `@TASK@` or `@TOOLING@` would put text into the wrong slot."""
        return [
            f"`{slot}` contains the slot token {token}"
            for slot in ("instruction", "tooling_note")
            for token in (_TASK_SLOT, _TOOLING_SLOT)
            if token in str(candidate.get(slot, ""))
        ]

    @staticmethod
    def _refuse_invalid_recipe(candidate: FixCandidateDict, dataset_dir: Path, datapoint: str) -> None:
        """Refuse a recipe that could add Dockerfile lines, a non-ECR image, or a missing lockfile (FR-21, NFR-7)."""
        violations = FixCandidate.validate_recipe(candidate, dataset_dir)
        if violations:
            slots = ", ".join(dict.fromkeys(violation.pointer for violation in violations))
            raise EmitRefusalError(
                check="environment-recipe-invalid",
                datapoint=datapoint,
                offending="; ".join(str(violation) for violation in violations),
                next_=f"change {slots} with `annotate` and re-run emit",
                exit_code=_REFUSAL_EXIT_CODE,
            )

    @classmethod
    def _refuse_missing_hidden_test_files(cls, candidate: FixCandidateDict, dataset_dir: Path, datapoint: str) -> None:
        """Refuse a hidden test whose file is not under `candidates/fix-<n>/hidden/<repo_path>`."""
        violations = FixCandidate.validate_hidden_test_files(candidate, dataset_dir)
        if violations:
            hidden_root = cls._workdir(candidate, dataset_dir) / "hidden"
            raise EmitRefusalError(
                check="hidden-test-file-missing",
                datapoint=datapoint,
                offending="; ".join(str(violation) for violation in violations),
                next_=f"write each hidden test file under {hidden_root}/<repo_path> and re-run emit",
                exit_code=_REFUSAL_EXIT_CODE,
            )

    @staticmethod
    def _refuse_no_fail_to_pass(candidate: FixCandidateDict, datapoint: str) -> None:
        """Refuse a task with no fail-to-pass test: it cannot tell a fix from no change."""
        if not any(hidden_test["kind"] == "fail_to_pass" for hidden_test in candidate["hidden_tests"]):
            raise EmitRefusalError(
                check="no-fail-to-pass-test",
                datapoint=datapoint,
                offending="no hidden test has kind = fail_to_pass, so the task cannot fail at the base",
                next_="add a hidden test that fails at the base and passes with the fix, with `annotate`, and re-run emit",
                exit_code=_REFUSAL_EXIT_CODE,
            )

    @staticmethod
    def _workdir(candidate: FixCandidateDict, dataset_dir: Path) -> Path:
        """`<dataset>/candidates/fix-<n>/`, where the agent writes the hidden tests, the spec, and the lockfile."""
        return dataset_dir / FixCandidate.workdir_relpath(candidate["pr_number"])

    # ───────────────────────────── the plan ─────────────────────────────

    @classmethod
    def _plan(cls, candidate: FixCandidateDict, dataset_dir: Path, overrides: tuple[str, ...]) -> _FixTaskPlan:
        """Read every input file once, so the writers below are pure functions of the plan."""
        workdir = cls._workdir(candidate, dataset_dir)
        hidden_tests = sorted(candidate["hidden_tests"], key=lambda hidden_test: hidden_test["repo_path"])
        return _FixTaskPlan(
            candidate=candidate,
            source_patch_bytes=(dataset_dir / candidate["source_patch_path"]).read_bytes(),
            hidden_test_bytes=tuple(
                (repo_path, (workdir / "hidden" / repo_path).read_bytes())
                for repo_path in dict.fromkeys(hidden_test["repo_path"] for hidden_test in hidden_tests)
            ),
            lockfile_bytes=(workdir / candidate["environment"]["lockfile"]).read_bytes(),
            validation_spec_bytes=(workdir / _VALIDATION_SPEC_FILENAME).read_bytes(),
            overrides=overrides,
        )

    # ───────────────────────────── assemble, verify, promote ─────────────────────────────

    @classmethod
    def _assemble_verify_promote(cls, plan: _FixTaskPlan, dataset_dir: Path, verify_hook: VerifyHook) -> EmitResult:
        """Write the task into a temp dir, validate and verify it, and promote it only when no check failed (§7.2)."""
        with tempfile.TemporaryDirectory() as staging_root:
            temp_dir = Path(staging_root) / plan.directory_name
            cls._write_task_directory(plan, temp_dir)
            Emit._refuse_invalid_layout(temp_dir, plan.datapoint)
            hook_result = cls._run_verify_hook(temp_dir, plan, verify_hook)
            promoted = Emit._promote(temp_dir, dataset_dir / "tasks" / plan.directory_name)
            return EmitResult(
                path=promoted,
                passed=hook_result.passed,
                unresolved=hook_result.unresolved,
                selection=plan.selection,
                iteration_index=0,
            )

    @staticmethod
    def _run_verify_hook(temp_dir: Path, plan: _FixTaskPlan, verify_hook: VerifyHook) -> HookResult:
        """Run `verify` on the assembled task; a failure blocks unless it is in `overrides`, unresolved never does (FR-31)."""
        result = verify_hook(temp_dir)
        unhandled = [failure for failure in result.failures if failure.check not in plan.overrides]
        if unhandled:
            first = unhandled[0]
            raise EmitRefusalError(
                check=first.check,
                datapoint=plan.datapoint,
                offending=first.offending,
                next_=first.next_,
                exit_code=_VERIFICATION_EXIT_CODE,
            )
        return result

    @classmethod
    def _write_task_directory(cls, plan: _FixTaskPlan, temp_dir: Path) -> None:
        """Write every file with an empty digest, then rewrite `task.toml` with the digest over them all."""
        files = cls._task_files(plan, content_digest="")
        Emit._materialize(files, temp_dir)
        digest = Emit._content_digest(files)
        (temp_dir / "task.toml").write_bytes(cls._task_config_bytes(plan, digest))

    @classmethod
    def _task_files(cls, plan: _FixTaskPlan, *, content_digest: str) -> list[_TaskFile]:
        """The §6 file list of the coding task, in a stable order."""
        instruction = cls._instruction_bytes(plan)
        lockfile = plan.candidate["environment"]["lockfile"]
        return [
            _TaskFile("task.toml", cls._task_config_bytes(plan, content_digest), executable=False, role="agent"),
            _TaskFile("instruction.md", instruction, executable=False, role="agent"),
            _TaskFile("environment/Dockerfile", cls._dockerfile(plan, "fix/Dockerfile.tmpl"), executable=False, role="agent"),
            _TaskFile(f"environment/{lockfile}", plan.lockfile_bytes, executable=False, role="agent"),
            _TaskFile("solution/solve.sh", Emit._read_template("fix/solve.sh"), executable=True, role="verifier"),
            _TaskFile(ORACLE_PATCH_RELPATH, plan.source_patch_bytes, executable=False, role="verifier"),
            _TaskFile(
                "tests/Dockerfile", cls._dockerfile(plan, "fix/verifier.Dockerfile.tmpl"), executable=False, role="verifier"
            ),
            _TaskFile(f"tests/{lockfile}", plan.lockfile_bytes, executable=False, role="verifier"),
            _TaskFile("tests/test.sh", Emit._read_template("fix/test.sh"), executable=True, role="verifier"),
            _TaskFile(f"{HIDDEN_TESTS_RELDIR}/tests.toml", cls._tests_toml_bytes(plan), executable=False, role="verifier"),
            *cls._hidden_test_files(plan),
            _TaskFile("tests/validate/validation_spec.toml", plan.validation_spec_bytes, executable=False, role="verifier"),
            _TaskFile(REFERENCE_PATCH_RELPATH, plan.source_patch_bytes, executable=False, role="verifier"),
            _TaskFile("tests/judge/instruction.md", instruction, executable=False, role="verifier"),
            _TaskFile("tests/judge/rubric.toml", cls._rubric_toml_bytes(plan), executable=False, role="verifier"),
            _TaskFile("tests/judge/models.toml", Emit._read_template("fix/judge/models.toml"), executable=False, role="verifier"),
            *cls._lib_files(),
        ]

    @staticmethod
    def _hidden_test_files(plan: _FixTaskPlan) -> list[_TaskFile]:
        """Each hidden test file at `tests/verify/<repo_path>`, sorted by `repo_path`."""
        return [
            _TaskFile(f"{HIDDEN_TESTS_RELDIR}/{repo_path}", data, executable=False, role="verifier")
            for repo_path, data in plan.hidden_test_bytes
        ]

    @staticmethod
    def _lib_files() -> list[_TaskFile]:
        """Each reward-stack module of `verifier_tpl/fix/lib/` at `tests/lib/<name>.py`, sorted by name."""
        lib_dir = Path(__file__).parent / "verifier_tpl" / _LIB_TEMPLATE_DIR
        names = sorted(path.name for path in lib_dir.glob("*.py") if path.is_file())
        return [
            _TaskFile(f"tests/lib/{name}", Emit._read_template(f"{_LIB_TEMPLATE_DIR}/{name}"), executable=False, role="verifier")
            for name in names
        ]

    # ───────────────────────────── rendered files ─────────────────────────────

    @staticmethod
    def _instruction_bytes(plan: _FixTaskPlan) -> bytes:
        """`fix/instruction.md` with `@TOOLING@`, then `@TASK@`, replaced by the agent's slot values."""
        template = Emit._read_template("fix/instruction.md").decode("utf-8")
        rendered = template.replace(_TOOLING_SLOT, plan.candidate["tooling_note"]).replace(
            _TASK_SLOT, plan.candidate["instruction"]
        )
        return rendered.encode("utf-8")

    @staticmethod
    def _dockerfile(plan: _FixTaskPlan, template_name: str) -> bytes:
        """One of the two coding Dockerfiles with every slot of the D-1 contract replaced."""
        environment = plan.candidate["environment"]
        template = Emit._read_template(template_name).decode("utf-8")
        rendered = (
            template.replace("@BASE_IMAGE@", environment["base_image"])
            .replace("@REPO_URL@", ForgeHost.clone_url_from_pull_request_url(plan.candidate["pr_url"], plan.candidate["repo"]))
            .replace("@BASE_COMMIT@", plan.candidate["base_commit"])
            .replace("@LOCKFILE@", Path(environment["lockfile"]).name)
            .replace("@SETUP_STEPS@", "\n".join(f"RUN {step}" for step in environment["setup_steps"]))
            .replace("@VERIFIER_BOTO3_PIN@", _VERIFIER_BOTO3_PIN)
        )
        return rendered.encode("utf-8")

    @staticmethod
    def _tests_toml_bytes(plan: _FixTaskPlan) -> bytes:
        """`tests/verify/tests.toml` in the D-1 schema, hidden tests sorted by `(repo_path, name)`."""
        hidden_tests = sorted(
            plan.candidate["hidden_tests"], key=lambda hidden_test: (hidden_test["repo_path"], hidden_test["name"])
        )
        document: dict[str, TomlValue] = {
            "test_command": plan.candidate["environment"]["test_command"],
            "timeout_sec": _HIDDEN_TESTS_TIMEOUT_SEC,
            "protected_markers": [*TEST_MARKERS, *_EXTRA_PROTECTED_MARKERS],
            "hidden_tests": [_hidden_test_table(hidden_test) for hidden_test in hidden_tests],
        }
        return emit_document(document)

    @staticmethod
    def _rubric_toml_bytes(plan: _FixTaskPlan) -> bytes:
        """`tests/judge/rubric.toml`: one `[[criteria]]` table per criterion, in the agent's order."""
        criteria: list[TomlValue] = [
            {"id": criterion["id"], "kind": criterion["kind"], "text": criterion["text"]}
            for criterion in plan.candidate["rubric_criteria"]
        ]
        return emit_document({"criteria": criteria})

    # ───────────────────────────── task.toml ─────────────────────────────

    @staticmethod
    def _task_config_bytes(plan: _FixTaskPlan, content_digest: str) -> bytes:
        """The §8 coding `task.toml` bytes, with `[metadata.harvest].content_digest` set to `content_digest`."""
        document = Harbor.task_config_document(
            name=plan.task_name,
            description=_FIX_TASK_DESCRIPTION,
            origin=plan.origin(),
            harvest=plan.harvest(),
            artifacts=_FIX_ARTIFACTS,
            keywords=_FIX_KEYWORDS,
            verifier_network_mode=_VERIFIER_NETWORK_MODE,
            verifier_allowed_hosts=_VERIFIER_ALLOWED_HOSTS,
            verifier_env=_VERIFIER_ENV,
        )
        # The builder just made this [metadata.harvest] table; the recursive TomlValue cannot be narrowed.
        metadata = cast("dict[str, TomlValue]", document["metadata"])
        harvest = cast("dict[str, TomlValue]", metadata["harvest"])
        harvest["content_digest"] = content_digest
        return emit_document(document)


def _hidden_test_table(hidden_test: HiddenTestDict) -> dict[str, TomlValue]:
    """One `[[hidden_tests]]` table; `id` is `<repo_path>::<name>`, the id the proof file uses (§8)."""
    return {
        "id": f"{hidden_test['repo_path']}::{hidden_test['name']}",
        "repo_path": hidden_test["repo_path"],
        "name": hidden_test["name"],
        "kind": hidden_test["kind"],
    }


@dataclass(frozen=True, slots=True)
class _FixTaskPlan:
    """Everything read for one coding datapoint, once, so the writers stay pure functions."""

    candidate: FixCandidateDict
    source_patch_bytes: bytes
    hidden_test_bytes: tuple[tuple[str, bytes], ...]
    lockfile_bytes: bytes
    validation_spec_bytes: bytes
    overrides: tuple[str, ...]

    @property
    def datapoint(self) -> str:
        return f"pr-{self.candidate['pr_number']}/fix"

    @property
    def task_name(self) -> str:
        """Harbor's `org/name` id, e.g. `our-org/our-repo__pr338-fix`; a nested GitLab path folds into the name."""
        return f"{ForgeHost.harbor_org_name(self.candidate['repo'])}__pr{self.candidate['pr_number']}-fix"

    @property
    def directory_name(self) -> str:
        """The on-disk directory, the repo slug's slashes flattened (§6)."""
        return f"{self.candidate['repo'].replace('/', '__')}__pr{self.candidate['pr_number']}-fix"

    @property
    def selection(self) -> str:
        """The note `emit` prints: a coding task is built from the first parent of the merge commit."""
        return f"base {self.candidate['base_commit'][:12]} (first parent of merge {self.candidate['merge_commit'][:12]})"

    def origin(self) -> FixTaskOrigin:
        """`[metadata.origin]`, with `pr_url` and the issue URLs the FR-25 scan reads as tokens."""
        issues = sorted(self.candidate["issues"], key=lambda issue: issue["number"])
        return FixTaskOrigin(
            repo=self.candidate["repo"],
            pr_numbers=(self.candidate["pr_number"],),
            pr_url=self.candidate["pr_url"],
            issue_numbers=tuple(issue["number"] for issue in issues),
            issue_urls=tuple(issue["url"] for issue in issues),
            base_commit=self.candidate["base_commit"],
            merge_commit=self.candidate["merge_commit"],
            base_commit_date=self.candidate["base_commit_date"],
            merged_at=self.candidate["merged_at"],
        )

    def harvest(self) -> FixHarvestFields:
        """`[metadata.harvest]`: the counts of hidden tests by kind, and the recorded overrides."""
        kinds = [hidden_test["kind"] for hidden_test in self.candidate["hidden_tests"]]
        return FixHarvestFields(
            kind="fix",
            task_type=self.candidate["task_type"],
            fail_to_pass=kinds.count("fail_to_pass"),
            pass_to_pass=kinds.count("pass_to_pass"),
            overrides=self.overrides,
        )
