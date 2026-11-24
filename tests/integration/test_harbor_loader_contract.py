"""Real Harbor loads what `emit` writes — the only test module in the suite that imports `harbor`.

Every other module in `tests/` validates the emitter against `src/eval_harvest/harbor.py`, this
project's own port of Harbor's task-config and layout validators. A port built by reading a third
party's source is exactly the artefact that drifts when that source moves, and until this module
existed nothing noticed: if the port were wrong, the emitter and its tests would be wrong together
and green would mean nothing.

**What this proves is that Harbor *loads* an emitted task — not that Harbor *grades* it.** Measured
against Harbor 0.22.0 by copying one emitted task and breaking each copy:

| break                                                             | rejected by `Task.is_valid_dir`? |
|-------------------------------------------------------------------|----------------------------------|
| delete `task.toml`                                                | yes                              |
| delete `instruction.md`                                           | yes                              |
| delete `environment/`                                             | yes                              |
| replace `task.toml` with `verifier = { mode = 'not-a-real-mode' }` | **no — accepted**                |
| delete `tests/` entirely                                          | **no — accepted**                |

`TaskConfig` is permissive: that garbage config validates with `task=None` and every other field
defaulted, and `Task.is_valid_dir` never looks for `tests/` unless the config declares verifier tests
it can name. So a green run here says a required file has not been renamed and the manifest schema has
not changed underneath the emitter. It says nothing about whether the verifier runs or the reward is
computed — the oracle/nop gradeability guard is what covers grading. Reading this module as a
working-datapoint guarantee is precisely the assumption that lets an ungradeable datapoint ship.

The port stays deliberately *stricter* than Harbor's loader — `emit` refuses the garbage `task.toml`
that Harbor accepts — so this is a second, independent check, not a replacement for `harbor.py`.

Harbor comes from the `eval` dependency group, which `mise run test` does not sync, so the
Harbor-dependent tests below skip with a named reason. `mise run test-harbor` is the task that syncs
`eval` and runs them; `-rs` in pytest's `addopts` is what keeps the skip legible in the default run,
so "not run" cannot be misread as "passed".
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pydantic
import pytest
from fixtures.coding_task import CodingTaskBuilder
from fixtures.fix_dataset import FixDatasetBuilder
from fixtures.repo_builder import RepoBuilder

from eval_harvest.candidate import Candidate, CandidateDict, FindingDict
from eval_harvest.dataset import Dataset
from eval_harvest.emit import Emit
from eval_harvest.fix_candidate import FixCandidate
from eval_harvest.fix_emit import FixEmit
from eval_harvest.forge import Forge
from eval_harvest.riskmap import RiskMap
from eval_harvest.tomlw import emit_document

# Attempted, not probed. `importlib.util.find_spec("harbor")` is a false positive here: `uv sync`
# prunes the `eval` group's *files* but leaves empty `harbor/cli/` and `harbor/leaderboard/`
# directories in site-packages, so `harbor` still resolves — as a namespace package with no
# `harbor.models` — and the module would error at collection instead of skipping. Importing what the
# tests actually use is the only probe that answers the question being asked.
#
# `harbor` ships no `py.typed`, so mypy is told to treat it as `Any` in `pyproject.toml`'s
# `[[tool.mypy.overrides]]`; that keeps the type check identical whether or not it is installed.
try:
    from harbor.models.dataset.manifest import DatasetManifest
    from harbor.models.registry import Registry
    from harbor.models.task.task import Task
except ImportError:
    HARBOR_IS_INSTALLED = False
else:
    HARBOR_IS_INSTALLED = True

_REPO = "our-org/our-repo"

#: Why the Harbor-dependent tests are not running, named so a reader of the test output can tell
#: "not run" from "passed" — and told how to run them. The tech plan's §4 early-warning sign for the
#: unproven-runtime risk is exactly someone reading a skipped check as a passing one.
HARBOR_SKIP_REASON = (
    "harbor is not installed: it comes from the `eval` dependency group, and `mise run test` syncs "
    "`dev` only. Run `mise run test-harbor` to sync `eval` and run this module."
)

requires_real_harbor = pytest.mark.skipif(not HARBOR_IS_INSTALLED, reason=HARBOR_SKIP_REASON)


def _substantive_findings() -> list[FindingDict]:
    """A reject oracle: one high defect and one medium, each bound to a captured comment."""
    return [
        {
            "comment_ids": [9001],
            "statement": "`add` subtracts instead of adds",
            "severity": "high",
            "severity_evidence": "return a - b in the round-1 diff",
            "severity_rationale": "wrong result for every caller",
            "reference_only": False,
        },
        {
            "comment_ids": [9002],
            "statement": "loop runs one index past the end",
            "severity": "medium",
            "severity_evidence": "range(len(items) + 1)",
            "severity_rationale": "IndexError on the last iteration",
            "reference_only": False,
        },
    ]


def _emit_one_reject_dataset(root: Path) -> Path:
    """Reconstruct the offline squash-merge fixture, emit one reject datapoint, build the manifest.

    The same offline path `tests/test_dataset.py` uses — real `capture`, real `emit`, real `dataset`,
    no forge and no network. What Harbor is handed below is therefore the shipped artefact, not a
    hand-written approximation of one.
    """
    fixture = RepoBuilder.build_squash_merged_pull_request(root)
    facts = Forge.reconstruct_facts(
        fixture.pr_number,
        fixture.clone,
        fixture.base_ref,
        reviews=fixture.reviews,
        comments=fixture.comments,
        timeline=fixture.timeline,
    )
    dataset_dir = root / "ds"
    dataset_dir.mkdir()
    risk_map: dict[str, Any] = {"version": "v1", "default": "medium", "rule": [{"prefix": "code.py", "risk": "high"}]}
    (dataset_dir / "risk-map.toml").write_bytes(emit_document(risk_map))
    (dataset_dir / "rubric.md").write_text("---\nversion: v1\n---\n# Review rubric\n", encoding="utf-8")
    risk_structural, rule = RiskMap.structural_risk(Candidate.changed_paths(facts), risk_map)
    Candidate.write_to_dataset(
        facts,
        repo=_REPO,
        pr_url=f"https://github.com/{_REPO}/pull/{fixture.pr_number}",
        dataset_dir=dataset_dir,
        risk_structural=risk_structural,
        risk_structural_rule=rule,
    )
    candidate_path = dataset_dir / "candidates" / f"pr-{fixture.pr_number}.json"
    Candidate.dump(_fill(Candidate.load(candidate_path)), candidate_path)
    Emit.emit_datapoint(Candidate.load(candidate_path), kind="reject", dataset_dir=dataset_dir)
    Dataset.build_dataset(dataset_dir)
    return dataset_dir


def _fill(candidate: CandidateDict) -> CandidateDict:
    """Classify every comment, set the reject findings, classify the risk, pin the rubric version."""
    for comment in candidate["comments"]:
        comment["classification"] = "defect"
        comment["classification_rationale"] = "a substantive problem a reviewer would block on"
    candidate["findings"] = _substantive_findings()
    candidate["change_risk"]["risk_classified"] = "low"
    candidate["change_risk"]["risk_classified_rationale"] = "isolated helper, well covered by tests"
    candidate["rubric_version"] = "v1"
    return candidate


@pytest.fixture(scope="module")
def emitted_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One emitted dataset, shared by the module: the fixture build runs `git`, so pay for it once."""
    return _emit_one_reject_dataset(tmp_path_factory.mktemp("harbor-contract"))


def _only_task_dir(dataset_dir: Path) -> Path:
    """The single emitted task directory under `tasks/`."""
    task_dirs = sorted((dataset_dir / "tasks").iterdir())
    assert len(task_dirs) == 1, f"expected exactly one emitted task, found {[d.name for d in task_dirs]}"
    return task_dirs[0]


def _recorded_task_name(task_dir: Path) -> str:
    """The `[task] name` `emit` wrote into `task.toml` — what Harbor's `Task.name` must agree with.

    `Task.__init__` takes its `name` from `config.task.name` and falls back to the *directory* name
    when the `[task]` table is absent (`task.py:77-79`), so asserting the two are equal is what pins
    that the config was read at all — the directory name is `our-org__our-repo__…`, not `our-org/…`.
    """
    name: str = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))["task"]["name"]
    return name


# ───────────────────────────── Harbor loads what emit writes ─────────────────────────────


@requires_real_harbor
def test_real_harbor_loads_an_emitted_task(emitted_dataset: Path) -> None:
    """`Task.is_valid_dir` accepts an emitted task and `Task(...)` constructs from it (FR-28)."""
    task_dir = _only_task_dir(emitted_dataset)

    assert Task.is_valid_dir(task_dir), f"real Harbor rejects the emitted task at {task_dir}"
    task = Task(task_dir)
    assert task.name == _recorded_task_name(task_dir), "Harbor's task name disagrees with the emitted task.toml"


@requires_real_harbor
def test_coding_task_loads_in_harbor(tmp_path: Path) -> None:
    """Harbor 0.22.0 loads an emitted coding task and keeps its verifier allowlist, env, and artifact (D-2).

    Our validator ignores `[verifier.env]` and the artifact `exclude`, so only Harbor itself can say
    that it reads them under these names rather than dropping them as unknown keys.
    """
    fix_dataset = FixDatasetBuilder.build(FixDatasetBuilder.fix_history(tmp_path / "history"), tmp_path / "ds")
    task_dir = FixEmit.emit_datapoint(FixCandidate.load(fix_dataset.candidate_path), dataset_dir=fix_dataset.dataset).path

    assert Task.is_valid_dir(task_dir), f"real Harbor rejects the emitted coding task at {task_dir}"
    task = Task(task_dir)
    assert task.name == _recorded_task_name(task_dir)
    assert task.instruction == (task_dir / "instruction.md").read_text(encoding="utf-8")
    verifier = task.config.verifier
    assert verifier.environment_mode == "separate"
    assert verifier.environment is not None
    assert verifier.environment.network_mode == "allowlist"
    assert verifier.environment.allowed_hosts == ["bedrock-runtime.us-east-1.amazonaws.com"]
    assert verifier.env["AWS_REGION"] == "${AWS_REGION:-us-east-1}"
    assert len(verifier.env) == 6
    assert task.config.environment.network_mode == "no-network"
    [artifact] = task.config.artifacts
    assert (artifact.source, artifact.exclude) == ("/work/repo", [".git"])


@requires_real_harbor
def test_real_harbor_reads_the_emitted_instruction(emitted_dataset: Path) -> None:
    """The instruction Harbor hands the agent is byte-for-byte the `instruction.md` `emit` wrote."""
    task_dir = _only_task_dir(emitted_dataset)

    assert Task(task_dir).instruction == (task_dir / "instruction.md").read_text(encoding="utf-8")


@requires_real_harbor
def test_real_harbor_validates_the_emitted_manifest(emitted_dataset: Path) -> None:
    """`DatasetManifest` accepts the emitted `dataset.toml`, with the digest `emit` stamped (FR-32)."""
    task_dir = _only_task_dir(emitted_dataset)
    document = tomllib.loads((emitted_dataset / "dataset.toml").read_text(encoding="utf-8"))

    manifest = DatasetManifest.model_validate(document)

    assert len(manifest.tasks) == 1
    assert manifest.tasks[0].name == _recorded_task_name(task_dir)
    recorded = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))["metadata"]["harvest"]["content_digest"]
    assert manifest.tasks[0].digest == recorded, "the manifest digest is not the one emit stamped into task.toml"


@requires_real_harbor
def test_real_harbor_loads_the_written_registry(emitted_dataset: Path) -> None:
    """`Registry.from_path` loads the written `registry.json` — the `harbor run --registry-path` input (TP-8).

    A review-only dataset has the combined entry and the review entry (FR-34), each with the one task.
    """
    task_dir = _only_task_dir(emitted_dataset)

    registry = Registry.from_path(emitted_dataset / "registry.json")

    assert [dataset.name for dataset in registry.datasets] == ["our-repo", "our-repo-review"]
    for dataset in registry.datasets:
        assert len(dataset.tasks) == 1
        assert dataset.tasks[0].name == task_dir.name, "the registry task is not the emitted directory"


@requires_real_harbor
def test_real_harbor_loads_a_mixed_registry(emitted_dataset: Path, tmp_path: Path) -> None:
    """Harbor 0.22.0 loads a mixed-type `registry.json` and lists all three entries, each with its own tasks (FR-34)."""
    mixed = tmp_path / "mixed"
    shutil.copytree(emitted_dataset, mixed)
    review_task = _only_task_dir(emitted_dataset).name
    coding_fixture = CodingTaskBuilder.build(tmp_path / "coding")
    shutil.copytree(coding_fixture.task_dir, mixed / "tasks" / coding_fixture.task_dir.name)
    Dataset.build_dataset(mixed)

    registry = Registry.from_path(mixed / "registry.json")

    tasks_by_entry = {dataset.name: sorted(task.name for task in dataset.tasks) for dataset in registry.datasets}
    assert list(tasks_by_entry) == ["our-repo", "our-repo-review", "our-repo-coding"]
    assert tasks_by_entry["our-repo"] == sorted([review_task, coding_fixture.task_dir.name])
    assert tasks_by_entry["our-repo-review"] == [review_task]
    assert tasks_by_entry["our-repo-coding"] == [coding_fixture.task_dir.name]


# ───────────────────────────── the assertions above have teeth ─────────────────────────────
#
# A scanner that finds nothing must prove it scanned something (CLAUDE.md). The checks above are
# "does a third party accept our bytes", which passes just as readily when the check has quietly
# stopped looking. These break the artefact deliberately and require the red — the standing
# version of the FR-37 breaks.


@requires_real_harbor
def test_harbor_loader_rejects_a_task_missing_its_instruction(emitted_dataset: Path, tmp_path: Path) -> None:
    """A copy of the emitted task with `instruction.md` removed is rejected — the loader check bites."""
    broken = tmp_path / "no-instruction"
    shutil.copytree(_only_task_dir(emitted_dataset), broken)
    (broken / "instruction.md").unlink()

    assert not Task.is_valid_dir(broken), "Task.is_valid_dir accepted a task with no instruction.md"


@requires_real_harbor
def test_harbor_manifest_validation_rejects_a_corrupt_digest(emitted_dataset: Path) -> None:
    """A manifest whose task digest is not `sha256:<64 hex>` is rejected — the manifest check bites."""
    document = tomllib.loads((emitted_dataset / "dataset.toml").read_text(encoding="utf-8"))
    document["tasks"][0]["digest"] = "not-a-digest"

    with pytest.raises(pydantic.ValidationError):
        DatasetManifest.model_validate(document)


@requires_real_harbor
def test_harbor_registry_loader_rejects_a_bare_object(emitted_dataset: Path, tmp_path: Path) -> None:
    """The old object form of `registry.json` is rejected — the registry loader check bites.

    A dict is iterable: Harbor iterates its key strings and `DatasetSpec` refuses each one, so the
    error is a `pydantic.ValidationError`, not a generic failure.
    """
    entry, *_ = json.loads((emitted_dataset / "registry.json").read_text(encoding="utf-8"))
    bare_object = tmp_path / "registry.json"
    bare_object.write_text(json.dumps(entry), encoding="utf-8")

    with pytest.raises(pydantic.ValidationError):
        Registry.from_path(bare_object)


# ───────────────────────────── the skip is visible, not silent ─────────────────────────────


def test_harbor_contract_module_skips_visibly_without_harbor() -> None:
    """The skip carries a named reason and the remedy — and this test never skips, so it always says so.

    Deliberately unmarked. A guard about a vanishing check that vanishes with it guards nothing, so
    this one runs on every `mise run test`, with or without the `eval` group synced.
    """
    assert "harbor is not installed" in HARBOR_SKIP_REASON
    assert "mise run test-harbor" in HARBOR_SKIP_REASON, "the skip reason must name the task that runs the module"
    marker_reason = requires_real_harbor.kwargs["reason"]
    assert marker_reason == HARBOR_SKIP_REASON, "the skipif marker must carry the named reason, not a bare condition"
