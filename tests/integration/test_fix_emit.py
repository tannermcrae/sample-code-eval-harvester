"""Tests for `emit --kind fix` (D-2): a filled coding candidate becomes a verified Harbor coding task.

The candidate is captured from the coding fix history by the real capture core and filled the way an
agent fills it (`fixtures/fix_dataset.py`). Each refusal test breaks one input and watches its named
refusal fire with no task written; the layout and determinism tests pin the tech plan §6 directory and
its byte-stable `content_digest` (NFR-1); the hook tests pin the FR-31 rule that a failure blocks and
an unresolved proof does not.
"""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fixtures.dockerfile import copy_sources
from fixtures.fix_dataset import (
    FIX_INSTRUCTION,
    FIX_ISSUE_NUMBERS,
    FIX_LOCKFILE,
    FIX_PR_NUMBER,
    FIX_TOOLING_NOTE,
    FixDataset,
    FixDatasetBuilder,
)
from fixtures.repo_builder import FixHistoryFixture

from eval_harvest.cli import Cli, ExitCode
from eval_harvest.emit import FIX_REWARD_KEYS, EmitRefusalError, HookResult, VerifyFailure
from eval_harvest.fix_candidate import FixCandidate
from eval_harvest.fix_emit import FixEmit
from eval_harvest.patchsplit import TEST_MARKERS
from eval_harvest.verify import ORACLE_PATCH_RELPATH, REFERENCE_PATCH_RELPATH

_FIX_TEMPLATE_DIR = Path(__file__).parents[2] / "src" / "eval_harvest" / "verifier_tpl" / "fix"


@pytest.fixture(scope="module")
def fix_history(tmp_path_factory: pytest.TempPathFactory) -> FixHistoryFixture:
    """One coding fix history for the module; each test captures into its own dataset."""
    return FixDatasetBuilder.fix_history(tmp_path_factory.mktemp("fix-emit"))


@pytest.fixture
def fix_dataset(fix_history: FixHistoryFixture, tmp_path: Path) -> FixDataset:
    """A fresh dataset holding the filled `fix-101.json`."""
    return FixDatasetBuilder.build(fix_history, tmp_path / "ds")


def _emit(fix_dataset: FixDataset, **kwargs: Any) -> Path:
    """Emit the dataset's candidate as it is on disk and return the promoted task directory."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    return FixEmit.emit_datapoint(candidate, dataset_dir=fix_dataset.dataset, **kwargs).path


def _refusal(fix_dataset: FixDataset) -> EmitRefusalError:
    """The refusal an emit of the on-disk candidate raises; asserts no task directory was written."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    with pytest.raises(EmitRefusalError) as caught:
        FixEmit.emit_datapoint(candidate, dataset_dir=fix_dataset.dataset)
    assert not (fix_dataset.dataset / "tasks").exists(), "a refused emit wrote a task directory"
    assert caught.value.datapoint == f"pr-{FIX_PR_NUMBER}/fix"
    assert caught.value.offending and caught.value.next_  # the four-field shape (FR-30)
    return caught.value


def _edit_candidate(fix_dataset: FixDataset, **slots: Any) -> None:
    """Overwrite top-level slots of the on-disk candidate."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    candidate.update(slots)  # type: ignore[typeddict-item]
    FixCandidate.dump(candidate, fix_dataset.candidate_path)


def _edit_environment(fix_dataset: FixDataset, **fields: Any) -> None:
    """Overwrite fields of the on-disk candidate's environment recipe."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    candidate["environment"].update(fields)  # type: ignore[typeddict-item]
    FixCandidate.dump(candidate, fix_dataset.candidate_path)


def _read_tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by its POSIX-relative path."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _task_toml(task_dir: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    return parsed


def _failing_hook(check: str) -> Any:
    """A verify hook that reports one failure of `check`."""

    def hook(task_dir: Path) -> HookResult:
        del task_dir
        return HookResult(passed=(), failures=(VerifyFailure(check=check, offending="planted", next_="fix it"),), unresolved=())

    return hook


# ───────────────────────────── layout and determinism ─────────────────────────────


def test_fix_task_has_every_file(fix_dataset: FixDataset) -> None:
    """The promoted directory has exactly the §6 file list, and the two scripts are executable."""
    task_dir = _emit(fix_dataset)

    lib_names = sorted(path.name for path in (_FIX_TEMPLATE_DIR / "lib").glob("*.py"))
    expected = {
        "task.toml",
        "instruction.md",
        "environment/Dockerfile",
        f"environment/{FIX_LOCKFILE}",
        "solution/solve.sh",
        "solution/oracle.patch",
        "tests/Dockerfile",
        f"tests/{FIX_LOCKFILE}",
        "tests/test.sh",
        "tests/verify/tests.toml",
        "tests/verify/tests/test_code_more.py",
        "tests/verify/tests/test_scan_more.py",
        "tests/validate/validation_spec.toml",
        "tests/judge/reference.patch",
        "tests/judge/instruction.md",
        "tests/judge/rubric.toml",
        "tests/judge/models.toml",
        *(f"tests/lib/{name}" for name in lib_names),
    }
    assert task_dir == fix_dataset.dataset / "tasks" / f"{fix_dataset.candidate['repo'].replace('/', '__')}__pr101-fix"
    assert set(_read_tree(task_dir)) == expected
    assert (task_dir / "solution" / "solve.sh").stat().st_mode & 0o111
    assert (task_dir / "tests" / "test.sh").stat().st_mode & 0o111
    assert not (fix_dataset.dataset / "metric.py").exists(), "FixEmit must leave metric.py to `dataset` (F-1)"


def test_fix_task_is_byte_identical_across_emits(fix_dataset: FixDataset) -> None:
    """Two emits of one candidate give the same bytes and the same `content_digest` (NFR-1)."""
    first = _read_tree(_emit(fix_dataset))
    second_dir = _emit(fix_dataset)
    second = _read_tree(second_dir)

    assert first == second
    digest = _task_toml(second_dir)["metadata"]["harvest"]["content_digest"]
    assert digest.startswith("sha256:") and len(digest) == len("sha256:") + 64


def test_fix_task_toml_shape(fix_dataset: FixDataset) -> None:
    """`task.toml` carries the §8 coding fields: allowlisted Bedrock verifier, its env, the artifact, the counts."""
    document = _task_toml(_emit(fix_dataset))

    assert document["task"]["name"].endswith("__pr101-fix")
    assert document["task"]["keywords"] == ["coding-agent", "bug-fix", "eval-harvest"]
    assert document["environment"]["network_mode"] == "no-network"
    assert document["verifier"]["environment"] == {
        "network_mode": "allowlist",
        "os": "linux",
        "allowed_hosts": ["bedrock-runtime.us-east-1.amazonaws.com"],
    }
    assert document["verifier"]["env"]["AWS_REGION"] == "${AWS_REGION:-us-east-1}"
    assert set(document["verifier"]["env"]) == {
        "VERIFIER_MODEL_ID",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    }
    assert document["artifacts"] == [{"source": "/work/repo", "exclude": [".git"]}]
    harvest = document["metadata"]["harvest"]
    assert (harvest["datapoint_type"], harvest["kind"], harvest["task_type"]) == ("coding", "fix", "investigate-and-fix")
    assert (harvest["fail_to_pass"], harvest["pass_to_pass"], harvest["overrides"]) == (1, 2, [])


def test_fix_instruction_fills_both_slots(fix_dataset: FixDataset) -> None:
    """`instruction.md` is the template with both slots filled; the judge gets the same bytes."""
    task_dir = _emit(fix_dataset)

    text = (task_dir / "instruction.md").read_text(encoding="utf-8")
    assert FIX_INSTRUCTION in text and FIX_TOOLING_NOTE in text
    assert "@TASK@" not in text and "@TOOLING@" not in text
    assert (task_dir / "tests" / "judge" / "instruction.md").read_bytes() == (task_dir / "instruction.md").read_bytes()


def test_fix_dockerfile_seals_then_runs_setup(fix_dataset: FixDataset) -> None:
    """`environment/Dockerfile` seals the clone first, then runs one `RUN` line per setup step, in order (FR-20)."""
    task_dir = _emit(fix_dataset)
    steps = fix_dataset.candidate["environment"]["setup_steps"]

    for dockerfile in (task_dir / "environment" / "Dockerfile", task_dir / "tests" / "Dockerfile"):
        lines = dockerfile.read_text(encoding="utf-8").splitlines()
        seal = next(index for index, line in enumerate(lines) if line.startswith("RUN git remote remove origin"))
        run_steps = [index for index, line in enumerate(lines) if line in {f"RUN {step}" for step in steps}]
        assert [lines[index] for index in run_steps] == [f"RUN {step}" for step in steps]
        assert all(index > seal for index in run_steps), f"{dockerfile.name}: a setup step runs before the seal"
        text = "\n".join(lines)
        assert fix_dataset.candidate["base_commit"] in text
        assert "@" + "BASE_IMAGE@" not in text and "@SETUP_STEPS@" not in text and "@VERIFIER_BOTO3_PIN@" not in text
        assert lines[next(index for index, line in enumerate(lines) if line.startswith("FROM "))] == (
            "FROM public.ecr.aws/docker/library/python:3.12-slim"
        )


def test_fix_dockerfile_copy_sources_exist(fix_dataset: FixDataset) -> None:
    """Each `COPY` source of both Dockerfiles is a file or directory in its build context."""
    task_dir = _emit(fix_dataset)

    for context in (task_dir / "environment", task_dir / "tests"):
        sources = copy_sources((context / "Dockerfile").read_text(encoding="utf-8"))
        assert sources, f"{context.name}/Dockerfile has no COPY to check"
        for source in sources:
            assert (context / source).exists(), f"COPY {source} is outside the build context {context}"


def test_oracle_patch_only_in_solution(fix_dataset: FixDataset) -> None:
    """The fix's bytes are in `solution/oracle.patch` and `tests/judge/reference.patch` only."""
    task_dir = _emit(fix_dataset)
    oracle = (task_dir / ORACLE_PATCH_RELPATH).read_bytes()

    assert oracle == (fix_dataset.dataset / fix_dataset.candidate["source_patch_path"]).read_bytes()
    holders = sorted(path for path, data in _read_tree(task_dir).items() if oracle in data)
    assert holders == sorted([ORACLE_PATCH_RELPATH, REFERENCE_PATCH_RELPATH])


def test_tests_toml_lists_hidden_ids(fix_dataset: FixDataset) -> None:
    """`tests.toml` has one sorted entry per hidden test with `id = <repo_path>::<name>`; markers start with TEST_MARKERS."""
    document = tomllib.loads((_emit(fix_dataset) / "tests" / "verify" / "tests.toml").read_text(encoding="utf-8"))

    assert document["test_command"] == fix_dataset.candidate["environment"]["test_command"]
    assert document["timeout_sec"] == 900
    assert document["protected_markers"][: len(TEST_MARKERS)] == list(TEST_MARKERS)
    assert document["protected_markers"][len(TEST_MARKERS) :] == ["sitecustomize.py", "usercustomize.py", "pytest.ini"]
    assert [entry["id"] for entry in document["hidden_tests"]] == [
        "tests/test_code_more.py::test_add_keeps_zero",
        "tests/test_code_more.py::test_add_two_numbers",
        "tests/test_scan_more.py::test_scan_empty_is_zero",
    ]
    assert [entry["kind"] for entry in document["hidden_tests"]] == ["pass_to_pass", "fail_to_pass", "pass_to_pass"]


def test_rubric_toml_lists_criteria(fix_dataset: FixDataset) -> None:
    """`tests/judge/rubric.toml` has one `[[criteria]]` table per criterion, with id, kind, and text."""
    document = tomllib.loads((_emit(fix_dataset) / "tests" / "judge" / "rubric.toml").read_text(encoding="utf-8"))

    assert document["criteria"] == [dict(criterion) for criterion in fix_dataset.candidate["rubric_criteria"]]


def test_origin_has_pr_and_issue_urls(fix_dataset: FixDataset) -> None:
    """`[metadata.origin]` has `pr_url` and the issue URLs, sorted by issue number (FR-25)."""
    origin = _task_toml(_emit(fix_dataset))["metadata"]["origin"]
    repo = fix_dataset.candidate["repo"]

    assert origin["pr_url"] == fix_dataset.candidate["pr_url"]
    assert origin["issue_numbers"] == sorted(FIX_ISSUE_NUMBERS)
    assert origin["issue_urls"] == [f"https://github.com/{repo}/issues/{number}" for number in sorted(FIX_ISSUE_NUMBERS)]
    assert origin["base_commit"] == fix_dataset.candidate["base_commit"]
    assert origin["merge_commit"] == fix_dataset.candidate["merge_commit"]


def test_fix_reward_keys_match_template() -> None:
    """`emit.FIX_REWARD_KEYS` equals `FIX_REWARD_KEYS` of the template `aggregate.py`, read by path.

    The tuple is read from the file's syntax tree, not imported: the template imports `bedrock` from its
    own directory and reads verifier paths at import time.
    """
    tree = ast.parse((_FIX_TEMPLATE_DIR / "lib" / "aggregate.py").read_text(encoding="utf-8"))
    [assignment] = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "FIX_REWARD_KEYS"
    ]
    assert assignment.value is not None

    assert tuple(ast.literal_eval(assignment.value)) == FIX_REWARD_KEYS


# ───────────────────────────── input refusals (exit 3) ─────────────────────────────


def test_unfilled_slot_refused(fix_dataset: FixDataset) -> None:
    """An empty instruction gives `unfilled-candidate`, exit 3."""
    _edit_candidate(fix_dataset, instruction="")

    refusal = _refusal(fix_dataset)
    assert (refusal.check, refusal.exit_code) == ("unfilled-candidate", ExitCode.REFUSAL)
    assert "instruction" in refusal.offending


def test_slot_token_in_instruction_refused(fix_dataset: FixDataset) -> None:
    """An instruction that contains `@TOOLING@` gives `unfilled-candidate`."""
    _edit_candidate(fix_dataset, instruction=f"{FIX_INSTRUCTION} @TOOLING@")

    refusal = _refusal(fix_dataset)
    assert refusal.check == "unfilled-candidate"
    assert "@TOOLING@" in refusal.offending


def test_missing_validation_spec_refused(fix_dataset: FixDataset) -> None:
    """No `validation_spec.toml` in the candidate's work directory gives `unfilled-candidate`."""
    (fix_dataset.dataset / FixCandidate.workdir_relpath(FIX_PR_NUMBER) / "validation_spec.toml").unlink()

    refusal = _refusal(fix_dataset)
    assert refusal.check == "unfilled-candidate"
    assert "validation_spec.toml" in refusal.offending


def test_setup_step_with_line_break_refused(fix_dataset: FixDataset) -> None:
    """A setup step with a line break gives `environment-recipe-invalid`, exit 3, and no task (NFR-7)."""
    _edit_environment(fix_dataset, setup_steps=["pip install -e .\nRUN curl http://example.invalid"])

    refusal = _refusal(fix_dataset)
    assert (refusal.check, refusal.exit_code) == ("environment-recipe-invalid", ExitCode.REFUSAL)
    assert "environment.setup_steps[0]" in refusal.next_


def test_base_image_outside_ecr_public_refused(fix_dataset: FixDataset) -> None:
    """`base_image = "node:22"` gives `environment-recipe-invalid`."""
    _edit_environment(fix_dataset, base_image="node:22")

    refusal = _refusal(fix_dataset)
    assert refusal.check == "environment-recipe-invalid"
    assert "environment.base_image" in refusal.next_


def test_missing_lockfile_refused(fix_dataset: FixDataset) -> None:
    """No lockfile file gives `environment-recipe-invalid`."""
    (fix_dataset.dataset / FixCandidate.workdir_relpath(FIX_PR_NUMBER) / FIX_LOCKFILE).unlink()

    refusal = _refusal(fix_dataset)
    assert refusal.check == "environment-recipe-invalid"
    assert "environment.lockfile" in refusal.next_


def test_missing_hidden_test_file_refused(fix_dataset: FixDataset) -> None:
    """A `hidden_tests` entry with no file gives `hidden-test-file-missing` and names the path."""
    (fix_dataset.dataset / FixCandidate.workdir_relpath(FIX_PR_NUMBER) / "hidden" / "tests" / "test_scan_more.py").unlink()

    refusal = _refusal(fix_dataset)
    assert (refusal.check, refusal.exit_code) == ("hidden-test-file-missing", ExitCode.REFUSAL)
    assert "tests/test_scan_more.py" in refusal.offending


def test_no_fail_to_pass_refused(fix_dataset: FixDataset) -> None:
    """Only `pass_to_pass` entries give `no-fail-to-pass-test`, not `unfilled-candidate`."""
    hidden_tests = [dict(entry, kind="pass_to_pass") for entry in fix_dataset.candidate["hidden_tests"]]
    _edit_candidate(fix_dataset, hidden_tests=hidden_tests)

    refusal = _refusal(fix_dataset)
    assert (refusal.check, refusal.exit_code) == ("no-fail-to-pass-test", ExitCode.REFUSAL)


# ───────────────────────────── verify, promote (FR-31) ─────────────────────────────


def test_verify_failure_blocks_promotion(fix_dataset: FixDataset) -> None:
    """A hook with one failure gives exit 4 and no task directory; `--override` of that check promotes it."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    with pytest.raises(EmitRefusalError) as caught:
        FixEmit.emit_datapoint(candidate, dataset_dir=fix_dataset.dataset, verify_hook=_failing_hook("answer-misplaced"))
    assert (caught.value.check, caught.value.exit_code) == ("answer-misplaced", ExitCode.VERIFICATION)
    assert not (fix_dataset.dataset / "tasks").exists()

    task_dir = _emit(fix_dataset, overrides=("answer-misplaced",), verify_hook=_failing_hook("answer-misplaced"))
    assert _task_toml(task_dir)["metadata"]["harvest"]["overrides"] == ["answer-misplaced"]


def test_unresolved_proof_does_not_block(fix_dataset: FixDataset) -> None:
    """The real hook with no clone promotes the task; `hidden-tests-proven` is unresolved, never passed (NFR-8)."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    result = FixEmit.emit_datapoint(candidate, dataset_dir=fix_dataset.dataset)

    assert result.path.is_dir()
    unresolved = {check.check: check.reason for check in result.unresolved}
    assert unresolved["hidden-tests-proven"] == "no valid proof file"
    assert "hidden-tests-proven" not in result.passed
    assert set(unresolved) == {"base-and-patch", "hidden-tests-proven", "git-channel-absence"}
    assert result.selection == (f"base {candidate['base_commit'][:12]} (first parent of merge {candidate['merge_commit'][:12]})")
    assert result.iteration_index == 0


def test_real_verify_with_clone_passes_static_checks(fix_dataset: FixDataset) -> None:
    """With the clone, every static coding check passes; only the proof and the git channels stay unresolved."""
    candidate = FixCandidate.load(fix_dataset.candidate_path)
    result = FixEmit.emit_datapoint(candidate, dataset_dir=fix_dataset.dataset, clone=fix_dataset.clone)

    assert sorted(check.check for check in result.unresolved) == ["git-channel-absence", "hidden-tests-proven"]
    assert {"base-and-patch", "content-absence", "instruction-names-fix", "answer-placement", "spec-coherent"} <= set(
        result.passed
    )


# ───────────────────────────── the CLI ─────────────────────────────


def test_cli_emit_kind_fix_reports_runner_line(fix_dataset: FixDataset, capsys: pytest.CaptureFixture[str]) -> None:
    """`emit fix-101.json --kind fix` exits 0, names the unresolved checks, and prints the §7.2 runner line."""
    code = Cli.run(["emit", str(fix_dataset.candidate_path), "--kind", "fix", "--clone", str(fix_dataset.clone)])
    out = capsys.readouterr().out

    assert code == ExitCode.SUCCESS
    assert "hidden-tests-proven — no valid proof file" in out
    assert "git-channel-absence — no container runtime" in out
    assert "hidden tests not proven: run the task's oracle and nop trials with a runner; it writes proofs/<task-dir>.json" in out


def test_cli_emit_kind_fix_refusal_is_logged_as_coding(fix_dataset: FixDataset, capsys: pytest.CaptureFixture[str]) -> None:
    """A coding emit refusal is filed under `refusals/coding/`, and the next successful emit clears it."""
    _edit_environment(fix_dataset, base_image="node:22")
    assert Cli.run(["--json", "emit", str(fix_dataset.candidate_path), "--kind", "fix"]) == ExitCode.REFUSAL
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"][0]["check"] == "environment-recipe-invalid"
    records = list((fix_dataset.dataset / "refusals" / "coding").glob("*.json"))
    assert len(records) == 1
    assert not (fix_dataset.dataset / "refusals" / "review").exists()

    _edit_environment(fix_dataset, base_image="public.ecr.aws/docker/library/python:3.12-slim")
    assert Cli.run(["emit", str(fix_dataset.candidate_path), "--kind", "fix"]) == ExitCode.SUCCESS
    assert not list((fix_dataset.dataset / "refusals" / "coding").glob("*.json"))


def test_cli_emit_all_kind_fix_reads_fix_candidates_only(fix_dataset: FixDataset, capsys: pytest.CaptureFixture[str]) -> None:
    """`emit --all --kind fix` emits each `fix-*.json` and ignores a `pr-*.json` next to it."""
    (fix_dataset.dataset / "candidates" / "pr-7.json").write_text("{}", encoding="utf-8")

    code = Cli.run(["--json", "emit", "--all", "--kind", "fix", "--dataset", str(fix_dataset.dataset)])
    payload = json.loads(capsys.readouterr().out)

    assert code == ExitCode.SUCCESS
    assert [item["pr_number"] for item in payload["items"]] == [FIX_PR_NUMBER]
    assert payload["summary"]["attempted"] == 1, "the pr-7.json review candidate was read by a fix batch"
