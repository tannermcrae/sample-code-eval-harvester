"""A dataset holding one filled coding candidate, ready for `emit --kind fix` (D-2).

The candidate is written by the real capture core (`FixCandidate.write_to_dataset`) from PR 101 of the
coding fix history (`RepoBuilder.build_fix_history`), then filled the way an agent fills it with
`annotate`: the instruction and tooling note, two hidden test files with fail-to-pass and pass-to-pass
entries (listed out of order, so the emitter must sort them), a rubric, and an environment recipe with
its lockfile and validation spec under `candidates/fix-101/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from eval_harvest.fix_candidate import (
    EnvironmentDict,
    FixCandidate,
    FixCandidateDict,
    FixFacts,
    FixIssue,
    HiddenTestDict,
    RubricCriterionDict,
)
from eval_harvest.forge_host import ForgeHost
from fixtures.repo_builder import FixHistoryFixture, RepoBuilder

FIX_PR_NUMBER: Final = 101
FIX_ISSUE_NUMBERS: Final = (100, 99)

#: The filled instruction slots. They name no changed path (`code.py`, `src/scan.py`) and no changed symbol.
FIX_INSTRUCTION: Final = "The calculator gives a wrong number for some inputs. Make it give the right number."
FIX_TOOLING_NOTE: Final = "Python 3.12 and pytest are installed."

FIX_LOCKFILE: Final = "requirements.txt"
FIX_LOCKFILE_TEXT: Final = "pytest==8.4.2\n"

#: Two hidden test files; the entries are listed out of `(repo_path, name)` order on purpose.
_HIDDEN_FILES: Final = {
    "tests/test_scan_more.py": "from src.scan import scan\n\n\ndef test_scan_empty_is_zero():\n    assert scan([]) == 0\n",
    "tests/test_code_more.py": (
        "from code import add\n\n\ndef test_add_two_numbers():\n    assert add(2, 3) == 5\n\n\n"
        "def test_add_keeps_zero():\n    assert add(0, 0) == 0\n"
    ),
}

#: The validation spec the agent writes (the shape the `spec-coherent` check reads).
FIX_VALIDATION_SPEC: Final = '''instructions = """Check that adding works."""

[[stories]]
id = "sum_of_two_numbers"
description = """A caller adds two numbers."""
procedure = ["Call the calculator with 2 and 3.", "Compare with 5."]

[[stories.cases]]
input = "2, 3"
expected = "5"
'''


@dataclass(frozen=True, slots=True)
class FixDataset:
    """A dataset with one filled coding candidate, the candidate path, and the clone holding its base."""

    dataset: Path
    candidate_path: Path
    candidate: FixCandidateDict
    clone: Path


class FixDatasetBuilder:
    """Writes one filled coding candidate into a fresh dataset. Holds no state."""

    @staticmethod
    def fix_history(root: Path) -> FixHistoryFixture:
        """The coding fix history the candidate is captured from (shared per module: it is git work)."""
        return RepoBuilder.build_fix_history(root)

    @classmethod
    def build(cls, history: FixHistoryFixture, dataset: Path) -> FixDataset:
        """Capture PR 101 into `dataset` with the real capture core, then fill every coding slot."""
        repo = ForgeHost.from_clone(history.clone).project_path
        facts = FixFacts(
            pr_number=FIX_PR_NUMBER,
            pr_url=f"https://github.com/{repo}/pull/{FIX_PR_NUMBER}",
            pr_title="Fix the calculator",
            pr_body="Fixes the wrong total.",
            merge_commit=history.substitutions[f"MERGE_{FIX_PR_NUMBER}"],
            merged_at="2026-08-01T09:00:00Z",
            issues=tuple(
                FixIssue(
                    number=number, url=f"https://github.com/{repo}/issues/{number}", title="Wrong total", body="It is wrong."
                )
                for number in FIX_ISSUE_NUMBERS
            ),
            author_commits=1,
        )
        candidate_path = FixCandidate.write_to_dataset(facts, repo=repo, clone=history.clone, dataset_dir=dataset)
        candidate = cls.fill(FixCandidate.load(candidate_path))
        FixCandidate.dump(candidate, candidate_path)
        cls.write_workdir(dataset)
        return FixDataset(dataset=dataset, candidate_path=candidate_path, candidate=candidate, clone=history.clone)

    @staticmethod
    def fill(candidate: FixCandidateDict) -> FixCandidateDict:
        """Every coding slot filled with a valid value."""
        candidate["instruction"] = FIX_INSTRUCTION
        candidate["instruction_rationale"] = "It gives the symptom and the expected result, and no cause."
        candidate["tooling_note"] = FIX_TOOLING_NOTE
        candidate["hidden_tests"] = [
            HiddenTestDict(
                repo_path="tests/test_scan_more.py",
                name="test_scan_empty_is_zero",
                kind="pass_to_pass",
                origin="agent",
                evidence="the empty case already worked",
            ),
            HiddenTestDict(
                repo_path="tests/test_code_more.py",
                name="test_add_two_numbers",
                kind="fail_to_pass",
                origin="agent",
                evidence="the bug gives the wrong sum",
            ),
            HiddenTestDict(
                repo_path="tests/test_code_more.py",
                name="test_add_keeps_zero",
                kind="pass_to_pass",
                origin="agent",
                evidence="zero was right before the fix",
            ),
        ]
        candidate["rubric_criteria"] = [
            RubricCriterionDict(id="sum_is_right", kind="fail_to_pass", text="Adding two numbers gives their sum."),
            RubricCriterionDict(id="zero_stays_zero", kind="pass_to_pass", text="Adding zero to zero gives zero."),
        ]
        candidate["environment"] = EnvironmentDict(
            base_image="public.ecr.aws/docker/library/python:3.12-slim",
            setup_steps=[f"pip install --no-cache-dir -r /opt/eval-harvest/{FIX_LOCKFILE}", "pip install -e ."],
            lockfile=FIX_LOCKFILE,
            test_command="python -m pytest -q --junitxml=$JUNIT_PATH",
            rationale="pytest pinned to a release before the merge date",
        )
        return candidate

    @staticmethod
    def write_workdir(dataset: Path) -> None:
        """The files the agent writes in `candidates/fix-101/`: hidden tests, the validation spec, the lockfile."""
        workdir = dataset / FixCandidate.workdir_relpath(FIX_PR_NUMBER)
        for repo_path, text in _HIDDEN_FILES.items():
            path = workdir / "hidden" / repo_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        (workdir / "validation_spec.toml").write_bytes(FIX_VALIDATION_SPEC.encode("utf-8"))
        (workdir / FIX_LOCKFILE).write_bytes(FIX_LOCKFILE_TEXT.encode("utf-8"))
