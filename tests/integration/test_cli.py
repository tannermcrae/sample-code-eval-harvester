"""Tests for the eval-harvest CLI dispatcher, refusal helper, and exit codes (task A-1)."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fixtures.repo_builder import PullRequestFixture, RepoBuilder

from eval_harvest.candidate import Candidate
from eval_harvest.cli import Cli, ExitCode
from eval_harvest.forge import Forge
from eval_harvest.tomlw import emit_document


class TestCliDispatch:
    """The top-level parser lists the verbs and rejects an unknown one distinctly."""

    def test_help_lists_every_verb(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            Cli.run(["--help"])
        assert exit_info.value.code == ExitCode.SUCCESS
        help_text = capsys.readouterr().out
        for verb in ("init", "survey", "capture", "show", "emit", "verify", "dataset"):
            assert verb in help_text

    def test_unknown_verb_exits_2(self) -> None:
        with pytest.raises(SystemExit) as exit_info:
            Cli.run(["not-a-verb"])
        assert exit_info.value.code == ExitCode.USAGE


class TestRefusalHelper:
    """refuse() emits the FR-2 four-field shape in both human and --json renderings."""

    def test_refuse_prints_four_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = Cli.refuse(
            check="no-review-iteration",
            datapoint="pr-42",
            offending="0 review rounds",
            next_="pick a PR that went through review",
            exit_code=ExitCode.REFUSAL,
        )
        assert exit_code == ExitCode.REFUSAL
        stderr = capsys.readouterr().err
        assert "no-review-iteration" in stderr
        assert "pr-42" in stderr
        assert "0 review rounds" in stderr
        assert "pick a PR that went through review" in stderr

    def test_refuse_json_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = Cli.refuse(
            check="empty-oracle",
            datapoint="pr-7",
            offending="no defect findings",
            next_="use --kind approve or add a finding",
            exit_code=ExitCode.REFUSAL,
            json_mode=True,
        )
        assert exit_code == ExitCode.REFUSAL
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "ok": False,
            "failures": [
                {
                    "check": "empty-oracle",
                    "datapoint": "pr-7",
                    "offending": "no defect findings",
                    "next": "use --kind approve or add a finding",
                }
            ],
        }


class TestBatchMutualExclusion:
    """Batch and single inputs are mutually exclusive; both or neither is a usage error (exit 2).

    An ambiguous invocation silently preferring one input is the class of bug that makes a shell loop
    hard to reason about; the CLI refuses it before any forge or filesystem work.
    """

    def test_capture_rejects_pr_number_with_from_survey(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`capture <pr> --from-survey <file>` names two inputs at once — a usage error, not a silent choice."""
        survey = tmp_path / "s.json"
        survey.write_text('{"prs": []}', encoding="utf-8")
        code = Cli.run(["capture", "42", "--from-survey", str(survey), "--clone", str(tmp_path)])
        assert code == ExitCode.USAGE
        assert "exactly one" in capsys.readouterr().err

    def test_capture_rejects_neither_pr_nor_from_survey(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`capture` with neither a PR nor `--from-survey` is a usage error — there is nothing to capture."""
        code = Cli.run(["capture", "--clone", str(tmp_path)])
        assert code == ExitCode.USAGE
        assert "exactly one" in capsys.readouterr().err

    def test_emit_rejects_candidate_with_all(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`emit <candidate> --all` names two inputs at once — a usage error, not a silent choice."""
        candidate = tmp_path / "pr-1.json"
        candidate.write_text("{}", encoding="utf-8")
        code = Cli.run(["emit", str(candidate), "--all", "--kind", "reject"])
        assert code == ExitCode.USAGE
        assert "exactly one" in capsys.readouterr().err

    def test_emit_rejects_neither_candidate_nor_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`emit` with neither a candidate nor `--all` is a usage error — there is nothing to emit."""
        code = Cli.run(["emit", "--kind", "reject"])
        assert code == ExitCode.USAGE
        assert "exactly one" in capsys.readouterr().err

    def test_iteration_with_kind_fix_is_usage_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`emit fix-1.json --kind fix --iteration 0` exits 2: a review flag must not change a coding task silently."""
        candidate = tmp_path / "fix-1.json"
        candidate.write_text("{}", encoding="utf-8")
        code = Cli.run(["emit", str(candidate), "--kind", "fix", "--iteration", "0"])
        assert code == ExitCode.USAGE
        assert "eval-harvest emit: --iteration does not apply to --kind fix" in capsys.readouterr().err

    def test_kind_fix_on_review_candidate_is_usage_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`emit pr-1.json --kind fix` exits 2 and names the review kinds."""
        candidate = tmp_path / "pr-1.json"
        candidate.write_text("{}", encoding="utf-8")
        assert Cli.run(["emit", str(candidate), "--kind", "fix"]) == ExitCode.USAGE
        assert "--kind reject|approve" in capsys.readouterr().err

    def test_review_kind_on_fix_candidate_is_usage_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`emit fix-1.json --kind reject` exits 2 and names `--kind fix`."""
        candidate = tmp_path / "fix-1.json"
        candidate.write_text("{}", encoding="utf-8")
        assert Cli.run(["emit", str(candidate), "--kind", "reject"]) == ExitCode.USAGE
        assert "--kind fix" in capsys.readouterr().err


class TestExitCodes:
    """The exit-code classes are distinct and returned exactly as declared (tech plan §9)."""

    def test_exit_codes_distinct_per_class(self) -> None:
        codes = [
            ExitCode.SUCCESS,
            ExitCode.USAGE,
            ExitCode.REFUSAL,
            ExitCode.VERIFICATION,
            ExitCode.RUNTIME_UNAVAILABLE,
        ]
        assert [int(code) for code in codes] == [0, 2, 3, 4, 5]
        assert len(set(codes)) == len(codes)
        for code in (ExitCode.REFUSAL, ExitCode.VERIFICATION, ExitCode.RUNTIME_UNAVAILABLE):
            assert Cli.refuse("check", "datapoint", "offending", "next", exit_code=code) == code


# ───────────────────────── the per-datapoint refusal log (coding-eval-harvest A-2, Part 4) ─────────────────────────

#: The same risk map the batch tests use: the fixture's one changed file is high risk.
_RISK_MAP: dict[str, Any] = {"version": "v1", "default": "medium", "rule": [{"prefix": "code.py", "risk": "high"}]}


def _refusal_dataset(tmp_path: Path) -> Path:
    """A dataset with a risk map and a rubric, ready for `capture` and `emit`."""
    dataset = tmp_path / "ds"
    dataset.mkdir()
    (dataset / "risk-map.toml").write_bytes(emit_document(_RISK_MAP))
    (dataset / "rubric.md").write_text("---\nversion: v1\n---\n# Review rubric\n", encoding="utf-8")
    return dataset


def _stub_capture(monkeypatch: pytest.MonkeyPatch, fixture: PullRequestFixture, *, recoverable: bool) -> None:
    """Replay `Forge.capture` from the fixture; with ``recoverable=False`` every iteration is force-pushed away.

    `build_pull_request_with_unrecoverable_iteration` keeps its approved round recoverable, so `capture`
    succeeds on it; the all-unrecoverable bundle is what makes `capture` refuse `no-emittable-iteration`.
    """
    facts = Forge.reconstruct_facts(
        fixture.pr_number,
        fixture.clone,
        fixture.base_ref,
        reviews=fixture.reviews,
        comments=fixture.comments,
        timeline=fixture.timeline,
    )
    if not recoverable:
        lost = tuple(replace(iteration, base_sha="", diff="", recoverable=False) for iteration in facts.iterations)
        facts = replace(facts, iterations=lost)
    monkeypatch.setattr(Forge, "capture", staticmethod(lambda repo, pr_number, clone, *, remote="origin": facts))


def _fill_reject(candidate_path: Path) -> None:
    """Fill every judgment slot so the candidate is `reject`-ready (one high and one medium finding)."""
    candidate = Candidate.load(candidate_path)
    for comment in candidate["comments"]:
        comment["classification"] = "defect"
        comment["classification_rationale"] = "a substantive problem a reviewer would block on"
    candidate["findings"] = [
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
    candidate["change_risk"]["risk_classified"] = "low"
    candidate["change_risk"]["risk_classified_rationale"] = "isolated helper, well covered by tests"
    candidate["rubric_version"] = "v1"
    Candidate.dump(candidate, candidate_path)


class TestRefusalLogWiring:
    """`capture` and `emit` file each per-datapoint refusal under `refusals/review/` and clear it on success."""

    def test_capture_refusal_writes_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A refused capture exits 3 as before and leaves `refusals/review/pr-<n>.json` behind."""
        fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
        _stub_capture(monkeypatch, fixture, recoverable=False)
        dataset = _refusal_dataset(tmp_path)

        code = Cli.run(["capture", str(fixture.pr_number), "--clone", str(fixture.clone), "--dataset", str(dataset)])

        assert code == ExitCode.REFUSAL
        assert "refused: no-emittable-iteration" in capsys.readouterr().err
        record = json.loads((dataset / "refusals" / "review" / f"pr-{fixture.pr_number}.json").read_text(encoding="utf-8"))
        assert record["check"] == "no-emittable-iteration"
        assert record["verb"] == "capture"
        assert record["datapoint"] == f"pr-{fixture.pr_number}"

    def test_capture_success_clears_earlier_refusal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A later successful capture of the same PR removes its refusal record."""
        fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
        dataset = _refusal_dataset(tmp_path)
        argv = ["capture", str(fixture.pr_number), "--clone", str(fixture.clone), "--dataset", str(dataset)]
        _stub_capture(monkeypatch, fixture, recoverable=False)
        assert Cli.run(argv) == ExitCode.REFUSAL
        record_path = dataset / "refusals" / "review" / f"pr-{fixture.pr_number}.json"
        assert record_path.is_file()

        _stub_capture(monkeypatch, fixture, recoverable=True)
        assert Cli.run(argv) == ExitCode.SUCCESS
        assert not record_path.exists()

    def test_emit_success_clears_earlier_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unfilled candidate's emit refusal is filed under `pr-<n>__<kind>`, and the filled emit removes it."""
        fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
        _stub_capture(monkeypatch, fixture, recoverable=True)
        dataset = _refusal_dataset(tmp_path)
        assert Cli.run(["capture", str(fixture.pr_number), "--clone", str(fixture.clone), "--dataset", str(dataset)]) == 0
        candidate_path = dataset / "candidates" / f"pr-{fixture.pr_number}.json"
        capsys.readouterr()

        assert Cli.run(["emit", str(candidate_path), "--kind", "reject"]) == ExitCode.REFUSAL
        stderr = capsys.readouterr().err
        record_path = dataset / "refusals" / "review" / f"pr-{fixture.pr_number}__reject.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["verb"] == "emit"
        assert record["datapoint"] == f"pr-{fixture.pr_number}/reject"
        assert f"refused: {record['check']}" in stderr

        _fill_reject(candidate_path)
        assert Cli.run(["emit", str(candidate_path), "--kind", "reject"]) == ExitCode.SUCCESS
        assert not record_path.exists()
        assert (dataset / "refusals" / "review").is_dir(), "the type directory stays; only the record goes"
        assert Cli.run(["dataset", "--dataset", str(dataset)]) == ExitCode.SUCCESS, "`dataset` must not trip on refusals/"

    def test_emit_all_reject_skips_fix_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A dataset with `pr-<n>.json` and `fix-2.json`: `emit --all --kind reject` emits one task, never the fix file."""
        fixture = RepoBuilder.build_squash_merged_pull_request(tmp_path)
        _stub_capture(monkeypatch, fixture, recoverable=True)
        dataset = _refusal_dataset(tmp_path)
        assert Cli.run(["capture", str(fixture.pr_number), "--clone", str(fixture.clone), "--dataset", str(dataset)]) == 0
        _fill_reject(dataset / "candidates" / f"pr-{fixture.pr_number}.json")
        (dataset / "candidates" / "fix-2.json").write_text('{"datapoint_type": "coding"}', encoding="utf-8")
        capsys.readouterr()

        code = Cli.run(["--json", "emit", "--all", "--kind", "reject", "--dataset", str(dataset)])
        payload = json.loads(capsys.readouterr().out)

        assert code == ExitCode.SUCCESS
        assert payload["summary"]["attempted"] == 1
        assert [item["pr_number"] for item in payload["items"]] == [fixture.pr_number]
        assert len(list((dataset / "tasks").iterdir())) == 1
