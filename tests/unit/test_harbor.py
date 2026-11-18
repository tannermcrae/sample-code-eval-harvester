"""Tests for the Harbor serializer: constants, the §8 task.toml builder, and the validators (D-2).

Every case here defends one Harbor-source fact that a compiler re-derived from Harbor's docs gets
wrong (see reference/harbor_emitter.py's module docstring). The document builder is held to the
tech-plan §8 shape; the validators are held to the constraints Harbor genuinely enforces, plus the
two it accepts-and-erases (which are worse because they are silent).
"""

import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from eval_harvest.emit import FIX_REWARD_KEYS, REWARD_KEYS
from eval_harvest.harbor import (
    SCHEMA_MIRROR_SOURCE,
    CompiledTask,
    EmittedFile,
    FixHarvestFields,
    FixTaskOrigin,
    Harbor,
    HarvestFields,
    TaskOrigin,
)
from eval_harvest.tomlw import TomlValue, emit_document


def _origin() -> TaskOrigin:
    return TaskOrigin(
        repo="our-org/our-repo",
        pr_numbers=(1234,),
        base_commit="abc123def4567890abc123def4567890abc123de",
        base_commit_date="2026-08-01T14:22:00Z",
        merged_at="2026-08-03T09:10:00Z",
    )


def _harvest() -> HarvestFields:
    return HarvestFields(
        kind="reject",
        expected_verdict="block",
        blocking_severity="high",
        finding_severities=("high", "medium", "medium"),
        change_risk_structural="high",
        change_risk_classified="low",
        change_risk_disagreement=True,
        rubric_version="v1",
        overrides=(),
    )


def _valid_document() -> dict[str, Any]:
    """A correct §8 task.toml, round-tripped through tomlw + tomllib.

    A parsed TOML document is genuinely ``dict[str, Any]`` (that is tomllib's return type), so the
    ``Any`` here is honest, not a shortcut: the validators exist precisely to check these values.
    """
    document = Harbor.task_config_document(
        name="our-org/our-repo__pr1234-reject",
        description="Review the change on this pull-request iteration.",
        origin=_origin(),
        harvest=_harvest(),
    )
    return tomllib.loads(emit_document(document).decode())


#: The review ``task.toml`` bytes for ``_origin()``/``_harvest()``, pinned before coding-eval-harvest
#: A-2 added the coding fields: the sha256 of ``emit_document`` over the document ``_valid_document``
#: builds. A change here means the new ``artifacts`` key or ``datapoint_type`` leaked into review tasks.
_REVIEW_CONFIG_SHA256 = "b5bfa23fd85c37e91765e9b0f14f7674655da1fc1dd546bc4c1d24f654bb7cc1"

#: The sha256 of ``Harbor.metric_script(REWARD_KEYS)`` before A-2; review-only datasets keep these bytes.
_REVIEW_METRIC_SHA256 = "85c5605fb729f56e7aef737729823835a18e5e00750362c8f5c72e5507289256"

#: The one coding artifact (ADR-4): the agent's tree, without its git history.
_CODING_ARTIFACT: dict[str, TomlValue] = {"source": "/work/repo", "exclude": [".git"]}


def _fix_origin() -> FixTaskOrigin:
    return FixTaskOrigin(
        repo="strands-agents/evals",
        pr_numbers=(338,),
        pr_url="https://github.com/strands-agents/evals/pull/338",
        issue_numbers=(337, 336),
        issue_urls=(
            "https://github.com/strands-agents/evals/issues/337",
            "https://github.com/strands-agents/evals/issues/336",
        ),
        base_commit="0bf1252aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        merge_commit="cc0a391bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        base_commit_date="2026-07-29",
        merged_at="2026-07-31",
    )


def _fix_harvest() -> FixHarvestFields:
    return FixHarvestFields(kind="fix", task_type="investigate-and-fix", fail_to_pass=7, pass_to_pass=6, overrides=())


def _coding_document(artifacts: list[dict[str, TomlValue]] | None = None) -> dict[str, Any]:
    """A coding task.toml round-tripped through tomlw + tomllib (``Any``: tomllib's honest return type)."""
    document = Harbor.task_config_document(
        name="strands-agents/evals__pr338-fix",
        description="Investigate and fix the reported defect.",
        origin=_fix_origin(),
        harvest=_fix_harvest(),
        artifacts=[_CODING_ARTIFACT] if artifacts is None else artifacts,
    )
    return tomllib.loads(emit_document(document).decode())


def _run_metric(script: bytes, rows: list[dict[str, float] | None], tmp_path: Path) -> dict[str, Any]:
    """Run a generated ``metric.py`` the way Harbor would (``-i rows.jsonl -o out.json``) and read its output."""
    script_path = tmp_path / "metric.py"
    script_path.write_bytes(script)
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    out_path = tmp_path / "out.json"
    subprocess.run(  # nosec B603,B607  # literal argv[0], real interpreter via executable=, no shell
        ["python", str(script_path), "-i", str(rows_path), "-o", str(out_path)],
        executable=sys.executable,
        check=True,
    )
    result: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8"))
    return result


class TestTaskConfigDocument:
    """The §8 shape the builder emits, and that it survives a tomlw + tomllib round trip."""

    def test_valid_config_has_no_violations(self) -> None:
        # A false-positive validator that blocked a correct task would make the whole pipeline
        # unusable, so the happy path is pinned first.
        assert Harbor.validate_task_config(_valid_document()) == []

    def test_timeout_is_float(self) -> None:
        # Harbor's timeout_sec is a float; an int (1800) fails its schema. The wrapper must keep
        # the decimal point through emission.
        document = Harbor.task_config_document(
            name="our-org/our-repo__pr1234-reject",
            description="d",
            origin=_origin(),
            harvest=_harvest(),
        )
        emitted = emit_document(document)
        assert b"timeout_sec = 1800.0\n" in emitted
        parsed = tomllib.loads(emitted.decode())
        assert isinstance(parsed["agent"]["timeout_sec"], float)
        assert isinstance(parsed["verifier"]["timeout_sec"], float)

    def test_metadata_preserved_under_metadata_namespace(self) -> None:
        # Harbor's TaskConfig has extra="ignore": an unknown top-level key loads then vanishes on
        # any rewrite. Provenance must therefore live under [metadata.*], never at the top level.
        document = _valid_document()
        assert document["metadata"]["origin"]["repo"] == "our-org/our-repo"
        assert document["metadata"]["harvest"]["kind"] == "reject"
        # None of the provenance fields may leak to a top-level key Harbor would drop.
        for dropped_key in ("repo", "pr_numbers", "base_commit", "kind", "expected_verdict"):
            assert dropped_key not in document

    def test_coding_config_matches_section_8(self) -> None:
        # The §8 coding target: the artifact, the coding origin, the coding harvest fields — each in
        # its own table — and a validator that accepts our own coding config.
        document = _coding_document()
        assert document["artifacts"] == [{"source": "/work/repo", "exclude": [".git"]}]
        assert document["metadata"]["origin"] == {
            "repo": "strands-agents/evals",
            "pr_numbers": [338],
            "pr_url": "https://github.com/strands-agents/evals/pull/338",
            "issue_numbers": [337, 336],
            "issue_urls": [
                "https://github.com/strands-agents/evals/issues/337",
                "https://github.com/strands-agents/evals/issues/336",
            ],
            "base_commit": "0bf1252aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "merge_commit": "cc0a391bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "base_commit_date": "2026-07-29",
            "merged_at": "2026-07-31",
        }
        assert document["metadata"]["harvest"] == {
            "datapoint_type": "coding",
            "kind": "fix",
            "task_type": "investigate-and-fix",
            "fail_to_pass": 7,
            "pass_to_pass": 6,
            "overrides": [],
        }
        assert list(document) == ["schema_version", "task", "agent", "verifier", "environment", "artifacts", "metadata"]
        assert Harbor.validate_task_config(document) == []

    def test_coding_origin_carries_urls(self) -> None:
        # E-1's FR-25 scan reads pr_url and issue_urls as leak tokens; issue_urls pairs with
        # issue_numbers by position. The review origin carries neither key.
        origin = _coding_document()["metadata"]["origin"]
        assert list(origin) == [
            "repo",
            "pr_numbers",
            "pr_url",
            "issue_numbers",
            "issue_urls",
            "base_commit",
            "merge_commit",
            "base_commit_date",
            "merged_at",
        ]
        assert [url.rsplit("/", 1)[-1] for url in origin["issue_urls"]] == [str(n) for n in origin["issue_numbers"]]
        review_origin = _valid_document()["metadata"]["origin"]
        assert "pr_url" not in review_origin
        assert "issue_urls" not in review_origin

    def test_review_config_bytes_unchanged(self) -> None:
        # The review shape is pinned (§3): default artifacts=() and the review dataclasses must give
        # the exact pre-A-2 bytes — no artifacts key, no datapoint_type.
        document = Harbor.task_config_document(
            name="our-org/our-repo__pr1234-reject",
            description="Review the change on this pull-request iteration.",
            origin=_origin(),
            harvest=_harvest(),
        )
        emitted = emit_document(document)
        assert hashlib.sha256(emitted).hexdigest() == _REVIEW_CONFIG_SHA256
        assert b"artifacts" not in emitted
        assert b"datapoint_type" not in emitted


class TestValidateTaskConfig:
    """Every way a task.toml value tree fails — the schema Harbor enforces, mirrored structurally."""

    def test_shared_mode_rejected(self) -> None:
        # shared verifier mode lets the evaluated agent pre-plant a passing reward file into
        # /logs/verifier (config.py:599-610). It must never validate clean (FR-32).
        document = _valid_document()
        document["verifier"]["environment_mode"] = "shared"
        violations = Harbor.validate_task_config(document)
        assert any(violation.pointer == "verifier.environment_mode" for violation in violations)

    def test_unknown_top_level_key_flagged(self) -> None:
        # A top-level key that is not a TaskConfig field loads and then silently vanishes; the
        # validator surfaces it rather than letting provenance disappear.
        document = _valid_document()
        document["repo"] = "our-org/our-repo"
        violations = Harbor.validate_task_config(document)
        assert any(violation.pointer == "repo" for violation in violations)

    def test_deprecated_version_alias_flagged(self) -> None:
        document = _valid_document()
        document["version"] = "1.0"
        violations = Harbor.validate_task_config(document)
        assert any(violation.pointer == "version" for violation in violations)

    def test_artifact_exclude_must_be_list_of_strings(self) -> None:
        # _artifact_fields drops unknown keys, so without its own check a bad exclude would pass
        # here and fail only inside Harbor at run time (ArtifactConfig.exclude: list[str]).
        bad_excludes: tuple[TomlValue, ...] = (".git", [1], [""])
        for bad_exclude in bad_excludes:
            document = _coding_document([{"source": "/work/repo", "exclude": bad_exclude}])
            violations = Harbor.validate_task_config(document)
            assert [(v.code, v.pointer) for v in violations] == [("compile.schema-invalid", "artifacts/0/exclude")], bad_exclude

    def test_artifact_exclude_rejects_dotdot(self) -> None:
        # An exclude pattern with a '..' part escapes the artifact root, like a '..' source does.
        document = _coding_document([{"source": "/work/repo", "exclude": [".git", "../x"]}])
        violations = Harbor.validate_task_config(document)
        assert [(v.code, v.pointer) for v in violations] == [("compile.schema-invalid", "artifacts/0/exclude")]


class TestFixMetricScript:
    """The per-type metric (ADR-5): each mean over its own type's rows only, deterministic bytes."""

    def test_metric_by_type_separates_means(self, tmp_path: Path) -> None:
        # Harbor's Mean() over the mixed rows would give coverage_all 0.4 and fix_reward 0.6 — each
        # diluted by the other type. The per-type script gives each type its own mean.
        script = Harbor.metric_script_by_type({"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS})
        rows: list[dict[str, float] | None] = [
            {"coverage_all": 1},
            {"coverage_all": 1},
            {"fix_reward": 1},
            {"fix_reward": 1},
            {"fix_reward": 1},
        ]
        out = _run_metric(script, rows, tmp_path)
        assert out["mean_coverage_all"] == 1.0
        assert out["mean_fix_reward"] == 1.0
        assert out["mean_coverage_required"] == 0.0
        assert (out["trials"], out["trials_coding"], out["trials_review"]) == (5, 3, 2)

    def test_metric_by_type_failed_trial_lowers_every_mean(self, tmp_path: Path) -> None:
        # A failed trial (None) has no known type, so it counts as 0 in every group: dropping it
        # would raise the mean of the trials that ran.
        script = Harbor.metric_script_by_type({"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS})
        rows: list[dict[str, float] | None] = [{"coverage_all": 1}, {"fix_reward": 1}, None]
        out = _run_metric(script, rows, tmp_path)
        assert out["mean_coverage_all"] == 0.5
        assert out["mean_fix_reward"] == 0.5
        assert (out["trials"], out["trials_coding"], out["trials_review"]) == (3, 2, 2)

    def test_metric_by_type_is_deterministic(self) -> None:
        # Groups and keys are sorted, so the bytes (and the dataset digest) do not depend on the
        # caller's order (NFR-1).
        forward = Harbor.metric_script_by_type({"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS})
        backward = Harbor.metric_script_by_type(
            {"coding": tuple(reversed(FIX_REWARD_KEYS)), "review": tuple(reversed(REWARD_KEYS))}
        )
        assert forward == backward

    def test_review_metric_script_bytes_unchanged(self) -> None:
        # Review-only datasets keep metric_script; its bytes are pinned to the pre-A-2 output.
        assert hashlib.sha256(Harbor.metric_script(REWARD_KEYS)).hexdigest() == _REVIEW_METRIC_SHA256

    def test_fix_reward_keys_are_the_adr5_list(self) -> None:
        assert FIX_REWARD_KEYS == (
            "fix_reward",
            "fix_correct",
            "fix_fail_to_pass",
            "fix_pass_to_pass",
            "fix_existing_suite",
            "fix_validation",
            "fix_rubric",
            "fix_bloat",
            "fix_taste_relative",
            "fix_taste_practice",
        )


class TestValidateTaskLayout:
    """A task directory that only fails at `harbor run` must fail here instead (FR-28)."""

    def test_layout_requires_all_parts(self) -> None:
        # Only task.toml is present: instruction.md, environment/, and the separate-mode
        # tests/Dockerfile are all missing and each must be named.
        task = CompiledTask(
            name="our-org/our-repo__pr1234-reject",
            files=(EmittedFile("task.toml"),),
            directory="our-repo__pr1234-reject",
            verifier_mode="separate",
            required_directories=(),
        )
        violations = Harbor.validate_task_layout(task)
        missing = {violation.pointer for violation in violations}
        assert "instruction.md" in missing
        assert "environment/" in missing
        assert "tests/Dockerfile" in missing

    def test_complete_layout_has_no_violations(self) -> None:
        task = CompiledTask(
            name="our-org/our-repo__pr1234-reject",
            files=(
                EmittedFile("task.toml"),
                EmittedFile("instruction.md"),
                EmittedFile("environment/Dockerfile"),
                EmittedFile("tests/Dockerfile"),
            ),
            directory="our-repo__pr1234-reject",
            verifier_mode="separate",
            required_directories=("environment",),
        )
        assert Harbor.validate_task_layout(task) == []


class TestSchemaMirror:
    """The drift anchor: an untraceable mirror is one nobody can compare to a real Harbor."""

    def test_schema_mirror_source_recorded(self) -> None:
        assert isinstance(SCHEMA_MIRROR_SOURCE, str)
        assert SCHEMA_MIRROR_SOURCE


class TestDatasetManifest:
    """The dataset.toml builder and validator F-1 builds on, and Harbor's content hash."""

    def test_manifest_round_trips_and_validates(self) -> None:
        manifest = Harbor.dataset_manifest_document(
            "our-org/pr-review-eval",
            {"our-org/our-repo__pr1234-reject": f"sha256:{'a' * 64}"},
        )
        parsed = tomllib.loads(emit_document(manifest).decode())
        assert Harbor.validate_dataset_manifest(parsed) == []

    def test_manifest_rejects_bare_hex_digest(self) -> None:
        # Harbor rejects a bare-hex digest; only the sha256:-prefixed form is accepted
        # (manifest.py:31,44-53).
        manifest = Harbor.dataset_manifest_document(
            "our-org/pr-review-eval",
            {"our-org/our-repo__pr1234-reject": "a" * 64},
        )
        violations = Harbor.validate_dataset_manifest(manifest)
        assert any("digest" in violation.pointer for violation in violations)

    def test_content_hash_is_order_insensitive_and_duplicate_sensitive(self) -> None:
        one = {"a/x": f"sha256:{'1' * 64}", "b/y": f"sha256:{'2' * 64}"}
        reordered = {"b/y": f"sha256:{'2' * 64}", "a/x": f"sha256:{'1' * 64}"}
        assert Harbor.dataset_content_hash(one, {}) == Harbor.dataset_content_hash(reordered, {})
        duplicated = {"a/x": f"sha256:{'1' * 64}", "b/y": f"sha256:{'1' * 64}"}
        assert Harbor.dataset_content_hash(one, {}) != Harbor.dataset_content_hash(duplicated, {})


class TestLocalRegistry:
    """The registry.json entry Harbor's --registry-path path consumes (no hub, no network)."""

    def test_registry_uses_short_name_and_task_paths(self) -> None:
        # A dataset name with a slash is unreachable through --registry-path (it routes to the
        # hosted client), so the registry entry uses the short name.
        entry = Harbor.local_registry_document(
            name="our-org/pr-review-eval",
            version="1.0",
            description="pr-review eval",
            task_directories=("our-repo__pr1234-reject", "our-repo__pr99-approve"),
        )
        assert entry["name"] == "pr-review-eval"
        assert entry["tasks"] == [
            {"name": "our-repo__pr1234-reject", "path": "./our-repo__pr1234-reject"},
            {"name": "our-repo__pr99-approve", "path": "./our-repo__pr99-approve"},
        ]
