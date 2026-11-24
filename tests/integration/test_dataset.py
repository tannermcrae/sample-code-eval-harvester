"""Tests for the `dataset` verb (task F-1): the Harbor manifest, the registry, and the distribution.

`dataset` is a read-only aggregator over the emitted `tasks/` — it makes no forge/git call and no
judgment. These scenarios pin the properties the customer's automation decision rests on (US-4): the
risk distribution counts each datapoint by its classified change risk and surfaces the count where
structural and classified disagree (FR-23), the severity distribution counts every finding, the
emitted `dataset.toml` is a manifest Harbor would accept (FR-29), an empty dataset refuses rather
than shipping a meaningless manifest, a datapoint missing its `content_digest` fails loudly rather
than getting one invented, and the whole assembly is byte-stable (NFR-1). A closing end-to-end case
runs `dataset` over a *real* `emit` output so the manifest, the recorded digest, and the shipped
`metric.py` are exercised against the bytes `emit` actually writes, not a hand-built fixture.
"""

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404  # runs the written metric.py with this interpreter, one fixed argv
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fixtures.coding_task import CodingTaskBuilder, default_proof_path
from fixtures.repo_builder import RepoBuilder

from eval_harvest.candidate import Candidate, CandidateDict, FindingDict
from eval_harvest.cli import Cli, ExitCode
from eval_harvest.dataset import Dataset, DatasetRefusalError
from eval_harvest.emit import FIX_REWARD_KEYS, REWARD_KEYS, Emit
from eval_harvest.forge import Forge
from eval_harvest.harbor import Harbor
from eval_harvest.refusal_log import RefusalLog, RefusalRecordDict
from eval_harvest.riskmap import RiskMap
from eval_harvest.tomlw import emit_document

_REPO = "our-org/our-repo"


def _write_task(  # noqa: PLR0913 — a fixture writer; each argument is one field of the task it fakes
    dataset_dir: Path,
    slug: str,
    *,
    classified: str,
    structural: str,
    severities: tuple[str, ...],
    repo: str = _REPO,
    digest: str | None = None,
    disagreement: bool | None = None,
    datapoint_type: str | None = None,
) -> None:
    """Write one review `tasks/<slug>/task.toml` carrying only the sections `dataset` reads.

    `digest` defaults to a real `sha256:`-prefixed digest derived from the slug (so the manifest
    validates); `disagreement` defaults to `structural != classified`, matching what `emit` records.
    `datapoint_type` is left out of the table by default, as `emit` writes a review task; pass it to
    write an explicit (or a deliberately wrong) value.
    """
    harvest: dict[str, Any] = {
        "kind": "reject",
        "content_digest": digest if digest is not None else _digest_for(slug),
        "change_risk_structural": structural,
        "change_risk_classified": classified,
        "change_risk_disagreement": disagreement if disagreement is not None else structural != classified,
        "finding_severities": list(severities),
    }
    if datapoint_type is not None:
        harvest = {"datapoint_type": datapoint_type, **harvest}
    _write_task_document(dataset_dir, slug, repo=repo, harvest=harvest)


def _write_coding_task(dataset_dir: Path, slug: str, *, repo: str = _REPO) -> None:
    """Write one coding `tasks/<slug>/task.toml`: the §8 coding harvest fields, and no `change_risk_*` at all."""
    harvest: dict[str, Any] = {
        "datapoint_type": "coding",
        "kind": "fix",
        "task_type": "investigate-and-fix",
        "fail_to_pass": 1,
        "pass_to_pass": 0,
        "overrides": [],
        "content_digest": _digest_for(slug),
    }
    _write_task_document(dataset_dir, slug, repo=repo, harvest=harvest)


def _digest_for(slug: str) -> str:
    """A real `sha256:`-prefixed digest derived from the slug, so the manifest validates."""
    return "sha256:" + hashlib.sha256(slug.encode()).hexdigest()


def _write_task_document(dataset_dir: Path, slug: str, *, repo: str, harvest: dict[str, Any]) -> None:
    """Write `tasks/<slug>/task.toml` with the given `[metadata.harvest]` table."""
    task_name = f"{repo}__{slug}"
    document: dict[str, Any] = {
        "schema_version": "1.4",
        "task": {"name": task_name, "version": "1.0.0"},
        "metadata": {"origin": {"repo": repo, "pr_numbers": [1]}, "harvest": harvest},
    }
    task_dir = dataset_dir / "tasks" / slug
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.toml").write_bytes(emit_document(document))


def _mixed_dataset(dataset_dir: Path) -> None:
    """A dataset of five datapoints whose classified and structural distributions deliberately differ.

    Classified risk buckets to {low: 3, medium: 1, high: 1}; structural to {low: 3, medium: 2, high: 0}
    — so a report that counted the wrong field would produce visibly different numbers, and the
    disagreement count (pr2 low/medium, pr5 high/low) is exercised independently of either bucketing.
    """
    _write_task(dataset_dir, "pr1-reject", classified="low", structural="low", severities=("high", "medium"))
    _write_task(dataset_dir, "pr2-reject", classified="low", structural="medium", severities=("medium",))
    _write_task(dataset_dir, "pr3-approve", classified="low", structural="low", severities=("low",))
    _write_task(dataset_dir, "pr4-reject", classified="medium", structural="medium", severities=("high",))
    _write_task(dataset_dir, "pr5-reject", classified="high", structural="low", severities=("high", "low"))


# ───────────────────────────── distributions (FR-23) ─────────────────────────────


def test_risk_distribution_counts_correct(tmp_path: Path) -> None:
    """Counts per change-risk level match the tasks' classified-risk metadata (FR-23)."""
    _mixed_dataset(tmp_path)

    report = Dataset.build_dataset(tmp_path)

    assert report.datapoints == 5
    assert report.risk_counts == {"low": 3, "medium": 1, "high": 1}


def test_severity_distribution_counts_correct(tmp_path: Path) -> None:
    """Counts per finding-severity level are the flattened finding counts across every task (FR-23)."""
    _mixed_dataset(tmp_path)

    report = Dataset.build_dataset(tmp_path)

    # findings: high×3 (pr1,pr4,pr5), medium×2 (pr1,pr2), low×2 (pr3,pr5).
    assert report.severity_counts == {"low": 2, "medium": 2, "high": 3}


def test_disagreement_count_surfaced(tmp_path: Path) -> None:
    """Datapoints where structural and classified disagree are counted, not collapsed (US-4)."""
    _mixed_dataset(tmp_path)

    report = Dataset.build_dataset(tmp_path)

    # pr2 (low vs high) and pr5 (high vs low) disagree; the other three agree.
    assert report.disagreements == 2
    assert report.as_json()["risk_distribution"] == {"low": 3, "medium": 1, "high": 1, "disagreements": 2}


# ───────────────────────────── manifest + registry (FR-29) ─────────────────────────────


def test_manifest_validates(tmp_path: Path) -> None:
    """The emitted `dataset.toml` passes `Harbor.validate_dataset_manifest` (a manifest `harbor run` accepts)."""
    _mixed_dataset(tmp_path)

    Dataset.build_dataset(tmp_path)

    document = tomllib.loads((tmp_path / "dataset.toml").read_text(encoding="utf-8"))
    assert Harbor.validate_dataset_manifest(document) == []
    # Every emitted task is referenced by its org/name id and its recorded sha256 digest.
    names = {entry["name"] for entry in document["tasks"]}
    assert names == {f"{_REPO}__pr{n}-{'approve' if n == 3 else 'reject'}" for n in (1, 2, 3, 4, 5)}


def test_registry_written_and_points_at_tasks(tmp_path: Path) -> None:
    """`registry.json` is a list whose combined entry names the short dataset and points at each task directory."""
    _mixed_dataset(tmp_path)

    Dataset.build_dataset(tmp_path)

    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert isinstance(registry, list), "Harbor's `Registry.from_path` reads a list of dataset entries"
    combined, review = registry
    assert (combined["name"], review["name"]) == ("our-repo", "our-repo-review")
    paths = {task["path"] for task in combined["tasks"]}
    assert paths == {f"./tasks/pr{n}-{'approve' if n == 3 else 'reject'}" for n in (1, 2, 3, 4, 5)}
    assert review["tasks"] == combined["tasks"], "a review-only dataset's review entry lists every task"


def test_metric_script_shipped(tmp_path: Path) -> None:
    """`dataset` ships the per-type `metric.py` (ADR-5); `dataset` owns the final file, not `emit`.

    `emit` writes a review-only `metric.py` after each review emit; the next `dataset` run replaces it
    with the per-type script, so each mean is taken over its own type's rows.
    """
    _mixed_dataset(tmp_path)

    Dataset.build_dataset(tmp_path)

    assert (tmp_path / "metric.py").read_bytes() == Harbor.metric_script_by_type(
        {"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS}
    )


# ───────────────────────────── refusals ─────────────────────────────


def test_empty_dataset_refuses_no_datapoints(tmp_path: Path) -> None:
    """An empty dataset refuses `no-datapoints` (exit 3) and writes no manifest."""
    (tmp_path / "tasks").mkdir()

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert refusal.value.check == "no-datapoints"
    assert refusal.value.exit_code == ExitCode.REFUSAL
    assert not (tmp_path / "dataset.toml").exists(), "no manifest may be written on a refusal"


def test_empty_dataset_cli_exit_code(tmp_path: Path) -> None:
    """The `dataset` verb maps `no-datapoints` onto exit 3 end-to-end (§9)."""
    (tmp_path / "tasks").mkdir()

    assert Cli.run(["dataset", "--dataset", str(tmp_path)]) == ExitCode.REFUSAL


def test_missing_content_digest_refuses(tmp_path: Path) -> None:
    """A task with no recorded `content_digest` fails loudly rather than getting one invented."""
    _write_task(tmp_path, "pr1-reject", classified="low", structural="low", severities=("high",), digest="")

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert refusal.value.check == "missing-content-digest"
    assert refusal.value.exit_code == ExitCode.REFUSAL


# ───────────────────────────── determinism (NFR-1) ─────────────────────────────


def test_dataset_deterministic(tmp_path: Path) -> None:
    """The manifest, registry, metric, and report are byte-stable for the same set of tasks (NFR-1).

    A mixed dataset with refusal files and a proof file, so the per-type lists, the refusal counts, and
    the proof read are all in the stable set, not only the review distribution.
    """
    _mixed_dataset(tmp_path)
    _two_coding_tasks(tmp_path)
    _write_refusal(tmp_path, "coding", "fix-9", "fix-base-unrecoverable")
    _write_refusal(tmp_path, "coding", "fix-8", "no-source-change")
    _write_refusal(tmp_path, "review", "pr-7", "no-emittable-iteration")
    (tmp_path / "proofs").mkdir()
    (tmp_path / "proofs" / "fix-11.json").write_text("{}\n", encoding="utf-8")

    first_report = Dataset.build_dataset(tmp_path)
    artefacts = {name: (tmp_path / name).read_bytes() for name in ("dataset.toml", "registry.json", "metric.py")}
    second_report = Dataset.build_dataset(tmp_path)

    for name, first_bytes in artefacts.items():
        assert (tmp_path / name).read_bytes() == first_bytes, f"{name} is not byte-stable across two runs"
    assert first_report == second_report
    assert json.dumps(first_report.as_json()) == json.dumps(second_report.as_json())
    assert first_report.render_human() == second_report.render_human()


# ───────────────────────────── end-to-end over a real emit ─────────────────────────────


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


def _emit_one_reject_datapoint(tmp_path: Path) -> Path:
    """Reconstruct the offline squash-merge fixture, fill the candidate, and emit one reject datapoint."""
    fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
    facts = Forge.reconstruct_facts(
        fixture.pr_number,
        fixture.clone,
        fixture.base_ref,
        reviews=fixture.reviews,
        comments=fixture.comments,
        timeline=fixture.timeline,
    )
    dataset = tmp_path / "ds"
    dataset.mkdir()
    risk_map: dict[str, Any] = {"version": "v1", "default": "medium", "rule": [{"prefix": "code.py", "risk": "high"}]}
    (dataset / "risk-map.toml").write_bytes(emit_document(risk_map))
    (dataset / "rubric.md").write_text("---\nversion: v1\n---\n# Review rubric\n", encoding="utf-8")
    risk_structural, rule = RiskMap.structural_risk(Candidate.changed_paths(facts), risk_map)
    Candidate.write_to_dataset(
        facts,
        repo=_REPO,
        pr_url=f"https://github.com/{_REPO}/pull/{fixture.pr_number}",
        dataset_dir=dataset,
        risk_structural=risk_structural,
        risk_structural_rule=rule,
    )
    candidate_path = dataset / "candidates" / f"pr-{fixture.pr_number}.json"
    candidate: CandidateDict = Candidate.load(candidate_path)
    for comment in candidate["comments"]:
        comment["classification"] = "defect"
        comment["classification_rationale"] = "a substantive problem a reviewer would block on"
    candidate["findings"] = _substantive_findings()
    candidate["change_risk"]["risk_classified"] = "low"
    candidate["change_risk"]["risk_classified_rationale"] = "isolated helper, well covered by tests"
    candidate["rubric_version"] = "v1"
    Candidate.dump(candidate, candidate_path)
    Emit.emit_datapoint(candidate, kind="reject", dataset_dir=dataset)
    return dataset


def test_dataset_over_real_emit_output(tmp_path: Path) -> None:
    """`dataset` reads a real `emit` output: the manifest validates against the digest `emit` recorded."""
    dataset = _emit_one_reject_datapoint(tmp_path)

    report = Dataset.build_dataset(dataset)

    assert report.datapoints == 1
    # The high/medium reject oracle lands in the severity distribution; the classified risk is `low`.
    assert report.risk_counts == {"low": 1, "medium": 0, "high": 0}
    assert report.severity_counts == {"low": 0, "medium": 1, "high": 1}

    manifest = tomllib.loads((dataset / "dataset.toml").read_text(encoding="utf-8"))
    assert Harbor.validate_dataset_manifest(manifest) == []
    # The manifest's digest is exactly the one emit stamped into the task's task.toml (not invented).
    task_toml = next((dataset / "tasks").glob("*/task.toml"))
    recorded_digest = tomllib.loads(task_toml.read_text(encoding="utf-8"))["metadata"]["harvest"]["content_digest"]
    assert manifest["tasks"][0]["digest"] == recorded_digest


# ───────────────────────────── coding datapoints (FR-32, FR-33, FR-34, TP-4) ─────────────────────────────


def _two_coding_tasks(dataset_dir: Path) -> None:
    """Two hand-written coding tasks with no proof file."""
    _write_coding_task(dataset_dir, "fix-11")
    _write_coding_task(dataset_dir, "fix-12")


def _two_review_three_coding(dataset_dir: Path) -> None:
    """The FR-32 case: two review tasks (one low, one medium risk) and three coding tasks, no proof files."""
    _write_task(dataset_dir, "pr1-reject", classified="low", structural="low", severities=("high",))
    _write_task(dataset_dir, "pr2-reject", classified="medium", structural="low", severities=("medium",))
    _write_coding_task(dataset_dir, "fix-11")
    _write_coding_task(dataset_dir, "fix-12")
    _write_coding_task(dataset_dir, "fix-13")


def _write_refusal(dataset_dir: Path, datapoint_type: str, datapoint: str, check: str) -> None:
    """Record one refusal the way a verb does, through the real refusal log."""
    record = RefusalRecordDict(check=check, datapoint=datapoint, offending="fixture", next="fixture", verb="capture")
    RefusalLog.write(dataset_dir, datapoint_type, record)


def test_coding_task_without_risk_fields_is_accepted(tmp_path: Path) -> None:
    """A coding `task.toml` with no `change_risk_*` gives a report, not a `malformed-task` refusal (TP-4)."""
    _write_coding_task(tmp_path, "fix-11")

    report = Dataset.build_dataset(tmp_path)

    assert report.type_counts == {"review": 0, "coding": 1}


def test_review_task_without_risk_fields_still_refuses(tmp_path: Path) -> None:
    """The review guard survives the coding branch: a review task with no classified risk refuses `malformed-task`."""
    _write_task(tmp_path, "pr1-reject", classified="", structural="low", severities=())

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert refusal.value.check == "malformed-task"
    assert "change_risk_classified" in refusal.value.offending


def test_explicit_review_type_is_a_review_task(tmp_path: Path) -> None:
    """`datapoint_type = "review"` reads exactly as a missing value does."""
    _write_task(tmp_path, "pr1-reject", classified="low", structural="low", severities=("high",), datapoint_type="review")

    report = Dataset.build_dataset(tmp_path)

    assert report.type_counts == {"review": 1, "coding": 0}
    assert report.risk_counts == {"low": 1, "medium": 0, "high": 0}


def test_unknown_datapoint_type_refuses(tmp_path: Path) -> None:
    """A typo in `datapoint_type` refuses `malformed-task` and names the value, rather than counting as review."""
    _write_task(tmp_path, "pr1-reject", classified="low", structural="low", severities=(), datapoint_type="codnig")

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert refusal.value.check == "malformed-task"
    assert "'codnig'" in refusal.value.offending
    assert refusal.value.datapoint == "pr1-reject"


def test_counts_by_type(tmp_path: Path) -> None:
    """Two review and three coding tasks with no proofs give review 2, coding 3, coding unproven 3 (FR-32)."""
    _two_review_three_coding(tmp_path)

    report = Dataset.build_dataset(tmp_path)

    assert report.datapoints == 5
    assert report.type_counts == {"review": 2, "coding": 3}
    assert report.coding_unproven == 3
    by_type = report.as_json()["by_type"]
    assert by_type == {
        "review": {"datapoints": 2, "refusals": {}},
        "coding": {"datapoints": 3, "unproven": 3, "refusals": {}},
    }


def test_risk_counts_skip_coding_tasks(tmp_path: Path) -> None:
    """The mixed dataset's risk and severity distributions equal those of its two review tasks alone."""
    review_only = tmp_path / "review-only"
    _write_task(review_only, "pr1-reject", classified="low", structural="low", severities=("high",))
    _write_task(review_only, "pr2-reject", classified="medium", structural="low", severities=("medium",))
    mixed = tmp_path / "mixed"
    _two_review_three_coding(mixed)

    review_report = Dataset.build_dataset(review_only)
    mixed_report = Dataset.build_dataset(mixed)

    for key in ("risk_distribution", "severity_distribution"):
        assert mixed_report.as_json()[key] == review_report.as_json()[key], f"{key} counted a coding task"


def test_review_only_json_keys_unchanged(tmp_path: Path) -> None:
    """For a review-only dataset the three pre-existing `--json` keys keep their values (backward compatibility)."""
    _mixed_dataset(tmp_path)

    payload = Dataset.build_dataset(tmp_path).as_json()

    assert payload["risk_distribution"] == {"low": 3, "medium": 1, "high": 1, "disagreements": 2}
    assert payload["severity_distribution"] == {"low": 2, "medium": 2, "high": 3}
    assert payload["datapoints"] == 5
    assert list(payload) == ["risk_distribution", "severity_distribution", "datapoints", "by_type", "run_commands"]


def test_valid_proof_is_not_unproven(tmp_path: Path) -> None:
    """A coding task with a valid runner proof is proven, so `unproven` is not simply the coding count."""
    fixture = CodingTaskBuilder.build(tmp_path)
    CodingTaskBuilder.write_valid_proof(fixture)
    dataset = fixture.task_dir.parent.parent

    report = Dataset.build_dataset(dataset)

    assert report.type_counts == {"review": 0, "coding": 1}
    assert report.coding_unproven == 0


def test_stale_proof_is_unproven(tmp_path: Path) -> None:
    """A proof whose `content_digest` is not the task digest counts as unproven — a stale proof hides nothing."""
    fixture = CodingTaskBuilder.build(tmp_path)
    stale = CodingTaskBuilder.valid_proof()
    stale["content_digest"] = "sha256:" + "f" * 64
    CodingTaskBuilder.write_proof(default_proof_path(fixture), stale)
    dataset = fixture.task_dir.parent.parent

    report = Dataset.build_dataset(dataset)

    assert report.coding_unproven == 1


def test_refusal_reasons_by_type(tmp_path: Path) -> None:
    """One coding refusal and one review refusal give one reason under each type, never the other (FR-33)."""
    _two_review_three_coding(tmp_path)
    _write_refusal(tmp_path, "coding", "fix-9", "fix-base-unrecoverable")
    _write_refusal(tmp_path, "review", "pr-7", "no-emittable-iteration")

    report = Dataset.build_dataset(tmp_path)

    assert report.refusal_counts == {
        "review": {"no-emittable-iteration": 1},
        "coding": {"fix-base-unrecoverable": 1},
    }
    by_type = report.as_json()["by_type"]
    assert isinstance(by_type, dict)
    assert by_type["coding"]["refusals"] == {"fix-base-unrecoverable": 1}
    assert by_type["review"]["refusals"] == {"no-emittable-iteration": 1}


def test_refusal_counts_are_sorted_and_summed(tmp_path: Path) -> None:
    """Two refusals with one check are summed, and the check names come out sorted (NFR-1)."""
    _two_coding_tasks(tmp_path)
    _write_refusal(tmp_path, "coding", "fix-9", "no-source-change")
    _write_refusal(tmp_path, "coding", "fix-8", "fix-base-unrecoverable")
    _write_refusal(tmp_path, "coding", "fix-7", "no-source-change")

    report = Dataset.build_dataset(tmp_path)

    assert list(report.refusal_counts["coding"].items()) == [("fix-base-unrecoverable", 1), ("no-source-change", 2)]


def test_malformed_refusal_file_refuses(tmp_path: Path) -> None:
    """A refusal file that is not a JSON object refuses `malformed-refusal` and names the file — no crash, no skip."""
    _two_coding_tasks(tmp_path)
    broken = tmp_path / "refusals" / "coding" / "fix-9.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("[1, 2]\n", encoding="utf-8")

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert refusal.value.check == "malformed-refusal"
    assert str(broken) in refusal.value.offending
    assert str(broken) in refusal.value.next_


def test_registry_lists_entry_for_each_type(tmp_path: Path) -> None:
    """A mixed dataset gives `<short>`, `<short>-review`, `<short>-coding`, in that order, each with only its tasks."""
    _two_review_three_coding(tmp_path)

    Dataset.build_dataset(tmp_path)

    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in registry] == ["our-repo", "our-repo-review", "our-repo-coding"]
    combined, review, coding = ({task["name"] for task in entry["tasks"]} for entry in registry)
    assert combined == {"pr1-reject", "pr2-reject", "fix-11", "fix-12", "fix-13"}
    assert review == {"pr1-reject", "pr2-reject"}
    assert coding == {"fix-11", "fix-12", "fix-13"}


def test_registry_omits_empty_type(tmp_path: Path) -> None:
    """A review-only dataset has no `<short>-coding` entry, and a coding-only one no `<short>-review`."""
    review_only = tmp_path / "review-only"
    _mixed_dataset(review_only)
    coding_only = tmp_path / "coding-only"
    _two_coding_tasks(coding_only)

    Dataset.build_dataset(review_only)
    Dataset.build_dataset(coding_only)

    review_names = [entry["name"] for entry in json.loads((review_only / "registry.json").read_text(encoding="utf-8"))]
    coding_names = [entry["name"] for entry in json.loads((coding_only / "registry.json").read_text(encoding="utf-8"))]
    assert review_names == ["our-repo", "our-repo-review"]
    assert coding_names == ["our-repo", "our-repo-coding"]


def test_metric_gives_per_type_means(tmp_path: Path) -> None:
    """The written `metric.py`, run on mixed rows, takes each `fix_` mean over the coding rows only (FR-34)."""
    _two_review_three_coding(tmp_path)
    Dataset.build_dataset(tmp_path)
    rows = [{"coverage_all": 1.0}, {"coverage_all": 1.0}, {"fix_reward": 1.0}, {"fix_reward": 0.5}, {"fix_reward": 0.0}]
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    out_path = tmp_path / "out.json"

    subprocess.run(  # nosec B603,B607  # literal argv[0], real interpreter via executable=, no shell
        ["python", str(tmp_path / "metric.py"), "-i", str(rows_path), "-o", str(out_path)],
        executable=sys.executable,
        check=True,
    )

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert out["mean_fix_reward"] == 0.5, "the coding mean is diluted by the review rows"
    assert out["mean_coverage_all"] == 1.0, "the review mean is diluted by the coding rows"
    assert (out["trials_review"], out["trials_coding"]) == (2, 3)


def test_run_commands_one_per_type(tmp_path: Path) -> None:
    """`run_commands` has `all` and one key for each type with tasks; the human report warns next to `all`."""
    mixed = tmp_path / "mixed"
    _two_review_three_coding(mixed)
    coding_only = tmp_path / "coding-only"
    _two_coding_tasks(coding_only)

    mixed_report = Dataset.build_dataset(mixed)
    coding_report = Dataset.build_dataset(coding_only)

    registry = (mixed / "registry.json").as_posix()
    assert mixed_report.run_commands == {
        "all": f"harbor run --registry-path {registry} --dataset our-repo",
        "review": f"harbor run --registry-path {registry} --dataset our-repo-review",
        "coding": f"harbor run --registry-path {registry} --dataset our-repo-coding",
    }
    assert mixed_report.as_json()["run_commands"] == mixed_report.run_commands
    assert list(coding_report.run_commands) == ["all", "coding"]
    human = mixed_report.render_human()
    assert "Harbor counts a missing key as 0, so use the per-type commands for means" in human
    for command in mixed_report.run_commands.values():
        assert command in human


def test_human_report_keeps_the_existing_lines_first(tmp_path: Path) -> None:
    """The existing review lines come first and unchanged; the per-type lines follow them."""
    _two_review_three_coding(tmp_path)
    _write_refusal(tmp_path, "coding", "fix-9", "fix-base-unrecoverable")

    lines = Dataset.build_dataset(tmp_path).render_human().splitlines()

    assert lines[:10] == [
        "datapoints: 5",
        "change-risk distribution (by classified risk):",
        "  low: 1",
        "  medium: 1",
        "  high: 0",
        "  structural/classified disagreements: 1",
        "finding-severity distribution:",
        "  low: 0",
        "  medium: 1",
        "  high: 1",
    ]
    assert "coding datapoints without a valid proof: 3" in lines
    assert "    fix-base-unrecoverable: 1" in lines


def test_dataset_cli_json_by_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`eval-harvest dataset --json` prints `by_type` and `run_commands` with the path as received (end to end)."""
    _two_review_three_coding(tmp_path)

    assert Cli.run(["--json", "dataset", "--dataset", str(tmp_path)]) == ExitCode.SUCCESS

    payload = json.loads(capsys.readouterr().out)
    assert payload["by_type"]["coding"] == {"datapoints": 3, "unproven": 3, "refusals": {}}
    assert payload["run_commands"]["coding"].endswith("--dataset our-repo-coding")


def test_no_datapoints_names_every_kind(tmp_path: Path) -> None:
    """The empty-dataset refusal points at both kinds of datapoint, so a coding user is not sent to review emit."""
    (tmp_path / "tasks").mkdir()

    with pytest.raises(DatasetRefusalError) as refusal:
        Dataset.build_dataset(tmp_path)
    assert "--kind reject|approve|fix" in refusal.value.next_
