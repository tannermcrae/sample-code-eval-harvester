"""Combine the stages into reward.json, after the "tasteful solve" of Senior SWE-Bench. Usage: python3 aggregate.py

Gates (all must hold for `fix_reward`):
- verifiers: every hidden fail_to_pass and pass_to_pass test passes, and the existing test suite passes
  (verifiers.json, from hidden_tests.py).
- validation: the validation judge accepted the tests, and every story passes.
- rubric_score > 0.5, patch_bloat < 2, practice_alignment > 2, relative_taste > 2.

`fix_correct` is the first two gates only: the change works. `fix_reward` is the tasteful solve.
The agent-test values are reported in reward_details.json, but they do not gate.

If the hidden tests did not run, an LLM stage failed (for example, Bedrock throttling), or the
validation judge discarded the trial, the trial is invalid: there is no reward.json, so Harbor reports
a reward-file error and the trial is not counted as a fail of the agent. reward_details.json has the
reason. (The review verifier writes a zero reward on every path instead; that difference is on purpose,
ADR-5.)

Ported from notebook 03 (``tests/lib/aggregate.py``). This file is data that eval-harvest copies into
each coding task; the CLI never imports it.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from typing import Any  # the stage files are arbitrary JSON written by the other stages.

from bedrock import LOGS, MODELS, TESTS

#: The keys of reward.json, in order (ADR-5). It must stay equal to `emit.FIX_REWARD_KEYS`, which the
#: dataset metric averages; D-2 pins the two together.
FIX_REWARD_KEYS: tuple[str, ...] = (
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

GATES: dict[str, Any] = MODELS["gates"]
TESTS_TOML = TESTS / "verify" / "tests.toml"
STAGE_FILE_NAMES = ("verifiers.json", "validation.json", "rubric.json", "taste.json", "bloat.json", "agent_tests.json")


def read_stage(name: str) -> dict[str, Any]:
    """One stage file, or `{"status": "missing"}` when the stage wrote nothing."""
    path = LOGS / name
    loaded: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {"status": "missing"}
    return loaded


def fraction_passed(test_ids: Sequence[str], results: Mapping[str, str]) -> float:
    """The passed fraction of `test_ids`, rounded to four places; 0.0 when there are none (the notebook rule)."""
    if not test_ids:
        return 0.0
    return round(sum(results.get(test_id) == "passed" for test_id in test_ids) / len(test_ids), 4)


def hidden_test_ids_by_kind(hidden_tests: Sequence[Mapping[str, str]], kind: str) -> list[str]:
    """The ids of the hidden tests of one kind (`fail_to_pass` or `pass_to_pass`), in tests.toml order."""
    return [hidden["id"] for hidden in hidden_tests if hidden["kind"] == kind]


def summarize_verifiers(verifiers: Mapping[str, Any], hidden_tests: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """The rates and the gate of the pre-written verifiers, from verifiers.json."""
    results: Mapping[str, str] = verifiers.get("tests", {})
    every_hidden_passed = all(results.get(hidden["id"]) == "passed" for hidden in hidden_tests)
    existing_suite_passed = verifiers.get("existing_suite") == "passed"
    return {
        "fail_to_pass": fraction_passed(hidden_test_ids_by_kind(hidden_tests, "fail_to_pass"), results),
        "pass_to_pass": fraction_passed(hidden_test_ids_by_kind(hidden_tests, "pass_to_pass"), results),
        "existing_suite": 1.0 if existing_suite_passed else 0.0,
        "pass": verifiers.get("status") == "completed" and every_hidden_passed and existing_suite_passed,
        "complete": verifiers.get("status") == "completed",
    }


def failed_verifier_lines(verifiers: Mapping[str, Any]) -> list[str]:
    """One line for each hidden test that did not pass, and one for a failed existing suite."""
    lines = [f"{result}: {test_id}" for test_id, result in sorted(verifiers.get("tests", {}).items()) if result != "passed"]
    if verifiers.get("existing_suite") == "failed":
        lines.append("failed: the existing test suite (see test_command.log)")
    return lines


def invalid_reasons(
    verify: Mapping[str, Any], validation: Mapping[str, Any], rubric: Mapping[str, Any], taste: Mapping[str, Any]
) -> list[str]:
    """Why the trial is invalid, or an empty list. The LLM-stage rules are the notebook's."""
    invalid: list[str] = []
    if not verify["complete"]:
        invalid.append("the verifier tests did not run (see test_command.log and verifiers.json)")
    if validation.get("status") != "accepted":
        invalid.append(
            f"validation {validation.get('status')}: {validation.get('discard_reason') or validation.get('error', '')}"
        )
    for name, stage in (("rubric", rubric), ("taste", taste)):
        if stage.get("status") in ("error", "missing"):
            invalid.append(f"{name} judge {stage.get('status')}: {stage.get('error', '')}")
    return invalid


def main() -> None:
    """Read every stage file, write reward_details.json, and write reward.json when the trial is valid."""
    hidden_tests: list[Mapping[str, str]] = tomllib.loads(TESTS_TOML.read_text())["hidden_tests"]
    verifiers, validation, rubric, taste, bloat, agent_tests = (read_stage(name) for name in STAGE_FILE_NAMES)
    verify = summarize_verifiers(verifiers, hidden_tests)
    failed = failed_verifier_lines(verifiers)
    (LOGS / "failed_tests.txt").write_text("".join(f"{line}\n" for line in failed))

    invalid = invalid_reasons(verify, validation, rubric, taste)
    validation_pass = validation.get("status") == "accepted" and validation.get("passed_stories") == validation.get(
        "total_stories"
    )
    correct = verify["pass"] and validation_pass
    bloat_ratio = bloat.get("patch_bloat")
    checks = {
        "verifiers": verify["pass"],
        "validation": validation_pass,
        "rubric": (rubric.get("rubric_score") or 0) > GATES["rubric_min"],
        "patch_bloat": bloat_ratio is not None and bloat_ratio < GATES["bloat_max"],
        "practice_alignment": (taste.get("practice_alignment") or 0) > GATES["taste_min"],
        "relative_taste": (taste.get("relative_taste") or 0) > GATES["taste_min"],
    }
    tasteful = all(checks.values())

    reward = {
        "fix_reward": float(tasteful),
        "fix_correct": float(correct),
        "fix_fail_to_pass": verify["fail_to_pass"],
        "fix_pass_to_pass": verify["pass_to_pass"],
        "fix_existing_suite": verify["existing_suite"],
        "fix_validation": float(validation.get("validation_score") or 0),
        "fix_rubric": float(rubric.get("rubric_score") or 0),
        "fix_bloat": float(bloat_ratio or 0),
        "fix_taste_relative": float(taste.get("relative_taste") or 0),
        "fix_taste_practice": float(taste.get("practice_alignment") or 0),
    }
    # Not in ADR-5, so not in reward.json: a key there that is not in the metric list gets a diluted mean.
    reported_only = {
        "tasteful_solve": float(tasteful),
        "verifiers_pass": float(verify["pass"]),
        "agent_tests_added": float(agent_tests.get("tests_added") or 0),
        "agent_tests_reproduce_bug": float(agent_tests.get("reproduces_bug") or 0),
    }
    models = {
        role: os.environ.get("VERIFIER_MODEL_ID") or MODELS[role]["model"]
        for role in ("validation_agent", "validation_judge", "rubric_judge", "taste_judge")
    }
    details = {
        "invalid": invalid,
        "models": models,
        "checks": checks,
        "reward": reward,
        "reported_only": reported_only,
        "verifiers": verify,
        "failed_tests": failed,
        "validation": validation,
        "rubric": rubric,
        "taste": taste,
        "bloat": bloat,
        "agent_tests": agent_tests,
    }
    (LOGS / "reward_details.json").write_text(json.dumps(details, indent=2))

    if invalid:
        (LOGS / "reward.json").unlink(missing_ok=True)
        (LOGS / "reward.txt").write_text("")
        print("INVALID TRIAL:", "; ".join(invalid))
    else:
        (LOGS / "reward.json").write_text(json.dumps(reward))
        print(json.dumps(reward))
    print("checks:", json.dumps(checks))


if __name__ == "__main__":
    main()
