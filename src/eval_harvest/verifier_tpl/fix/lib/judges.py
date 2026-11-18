"""LLM judges and patch bloat. Usage: python3 judges.py rubric|taste|bloat

- rubric: the task rubric (tests/judge/rubric.toml). Each criterion is fail_to_pass (the change must do
  this) or pass_to_pass (the change must not break this). The judge reads the patch and the code, and
  scores each criterion 0 or 1. Writes rubric.json.
- taste: the global taste rubric. Five dimensions relative to the reference patch, and five dimensions of
  alignment with the practices of the codebase. Each is scored 1-5. Writes taste.json.
- bloat: source lines that the agent's patch changes, divided by the source lines of the reference patch.
  Tests, documentation, and changelogs are not counted. A test path is one that matches a
  `protected_markers` entry of tests/verify/tests.toml: the same rule as the source/test split of the
  patch (A-2). Writes bloat.json.

Ported from notebook 03 (``tests/lib/judges.py``). This file is data that eval-harvest copies into each
coding task; the CLI never imports it.
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Sequence
from typing import Any  # the judges' submitted scores are arbitrary JSON from the model.

from bedrock import LOGS, REPO, TESTS, Conversation, read_only_tools, write_json

REFERENCE = TESTS / "judge" / "reference.patch"
TESTS_TOML = TESTS / "verify" / "tests.toml"
DOCUMENTATION_SUFFIXES = (".md", ".rst", ".txt")
CHANGELOG_MARKER = "changelog"


def agent_patch() -> str:
    """The agent's change, as test.sh wrote it."""
    patch: str = (LOGS / "agent.patch").read_text()
    return patch


def instruction() -> str:
    """The task part of the instruction: everything before the general instructions."""
    text: str = (TESTS / "judge" / "instruction.md").read_text()
    return text.split("## General instructions", maxsplit=1)[0].strip()


def protected_markers() -> list[str]:
    """The test-path markers of this task, from tests/verify/tests.toml."""
    markers: list[str] = tomllib.loads(TESTS_TOML.read_text())["protected_markers"]
    return markers


def is_counted_source_path(path: str, markers: Sequence[str]) -> bool:
    """A path whose lines count as source: not a test path (the A-2 rule) and not documentation or a changelog."""
    if any(marker in f"/{path}" for marker in markers):
        return False
    lowered = path.lower()
    return not lowered.endswith(DOCUMENTATION_SUFFIXES) and CHANGELOG_MARKER not in lowered


def mean_score_of_kind(scored: Sequence[dict[str, Any]], kind: str) -> float | None:
    """The mean score of the criteria of one kind, or None when the rubric has none of that kind."""
    scores = [criterion["score"] for criterion in scored if criterion["kind"] == kind]
    return round(sum(scores) / len(scores), 4) if scores else None


# ── Rubric ──────────────────────────────────────────────────────────────────

RUBRIC_SYSTEM = """You grade a code change against a task rubric. You get the task instruction, the change (a diff),
and criteria. Read the code in /work/repo as you need: the change is applied there. For each criterion,
decide if the change fulfills it. Score 1 (fulfills) or 0 (does not), with a one-sentence reason. Judge the
behavior and the design that the criterion describes, not the names or the layout of the code. Different
correct designs fulfill a criterion. An empty change fulfills no fail_to_pass criterion. Read only the code
that you need to decide; most criteria need the changed functions and their callers only."""


def rubric() -> None:
    """Score the task rubric and write rubric.json."""
    criteria: list[dict[str, Any]] = tomllib.loads((TESTS / "judge" / "rubric.toml").read_text())["criteria"]
    schema = {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "score": {"type": "integer", "enum": [0, 1]},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "score", "reason"],
                },
            }
        },
        "required": ["scores"],
    }
    listing = "\n".join(f"- `{criterion['id']}` ({criterion['kind']}): {criterion['text'].strip()}" for criterion in criteria)
    patch = agent_patch()
    conversation = Conversation("rubric_judge", RUBRIC_SYSTEM, read_only_tools([REPO, LOGS]), "submit_rubric_scores", schema)
    result = conversation.ask(
        f"# Task\n{instruction()}\n\n# Change\n```diff\n{patch or '(empty: no change)'}\n```\n\n"
        f"# Criteria\n{listing}\n\nScore every criterion."
    )
    by_id = {score["id"]: score for score in result["scores"]}
    scored = [{**criterion, **by_id.get(criterion["id"], {"score": 0, "reason": "not scored"})} for criterion in criteria]
    write_json(
        "rubric.json",
        {
            "rubric_score": round(sum(criterion["score"] for criterion in scored) / len(scored), 4),
            "fail_to_pass_score": mean_score_of_kind(scored, "fail_to_pass"),
            "pass_to_pass_score": mean_score_of_kind(scored, "pass_to_pass"),
            "criteria": scored,
            "usage": conversation.usage,
        },
    )


# ── Taste ───────────────────────────────────────────────────────────────────

TASTE_SYSTEM = """You are a senior code reviewer. You grade the quality of a coding agent's change, not whether it
works (other checks test that). Before you grade, learn the conventions of the project: read its
contributing or style guides (for example AGENTS.md, CONTRIBUTING.md, STYLE_GUIDE.md), the code around the
change, and how the codebase handles the same concerns elsewhere. The change is applied in /work/repo.

Score each dimension as an integer 1-5, with a short reason.

Relative taste: compare with the reference change, which a maintainer merged. 4 = on par with the
reference. 5 = clearly better than the reference. 3 = acceptable but weaker. 2 = a reviewer would ask for
significant changes. 1 = a reviewer would reject it.
- minimality: focused on the task, no unrelated changes or scope creep.
- approach_quality: the right kind of solution, for example a root-cause fix, not a symptom patch.
- hygiene: no shortcuts, hard-coded values, dead code, or workarounds.
- fluency: correct use of the language, libraries, and domain concepts.
- craftsmanship: makes later work easier, as a senior reviewer would approve.

Codebase practice alignment (absolute): 5 = reads as if a regular contributor wrote it. 1 = foreign to
the codebase.
- style_consistency: formatting, naming, and structure match the surrounding code.
- pattern_adherence: uses the established patterns and idioms of the project.
- library_usage: reuses what the project already has, no needless new dependency or hand-rolled helper.
- abstraction_level: the right level of abstraction for this part of the code.
- documentation_fit: comments and docstrings match the style and density of the project. Fewer comments is
  better in a codebase that documents little."""

RELATIVE = ("minimality", "approach_quality", "hygiene", "fluency", "craftsmanship")
PRACTICE = ("style_consistency", "pattern_adherence", "library_usage", "abstraction_level", "documentation_fit")


def mean_of_dimensions(result: dict[str, Any], dimensions: Sequence[str]) -> float:
    """The mean score of the named taste dimensions, rounded to two places."""
    scores: list[int] = [result[dimension]["score"] for dimension in dimensions]
    return round(sum(scores) / len(scores), 2)


def taste() -> None:
    """Score the taste rubric against the reference patch and write taste.json."""
    patch = agent_patch()
    if not patch.strip():
        write_json("taste.json", {"status": "skipped", "reason": "empty patch", "relative_taste": 0, "practice_alignment": 0})
        return
    dimension_schema = {
        "type": "object",
        "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5}, "reason": {"type": "string"}},
        "required": ["score", "reason"],
    }
    schema = {
        "type": "object",
        "properties": dict.fromkeys(RELATIVE + PRACTICE, dimension_schema),
        "required": list(RELATIVE + PRACTICE),
    }
    conversation = Conversation("taste_judge", TASTE_SYSTEM, read_only_tools([REPO, LOGS]), "submit_taste_scores", schema)
    result = conversation.ask(
        f"# Task\n{instruction()}\n\n# Agent change\n```diff\n{patch}\n```\n\n"
        f"# Reference change (merged by a maintainer)\n```diff\n{REFERENCE.read_text()}\n```\n\n"
        "Explore the codebase, then submit scores for all ten dimensions."
    )
    write_json(
        "taste.json",
        {
            "status": "scored",
            "relative_taste": mean_of_dimensions(result, RELATIVE),
            "practice_alignment": mean_of_dimensions(result, PRACTICE),
            "dimensions": result,
            "usage": conversation.usage,
        },
    )


# ── Patch bloat ─────────────────────────────────────────────────────────────


def source_sloc(patch: str, markers: Sequence[str]) -> int:
    """Changed source lines: added or removed, not blank, not a comment, not in tests or docs."""
    count, counted = 0, False
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            path = line.split(" b/")[-1]
            counted = is_counted_source_path(path, markers)
        elif counted and line[:1] in "+-" and not line.startswith(("+++", "---")):
            body = line[1:].strip()
            if body and not body.startswith("#"):
                count += 1
    return count


def bloat() -> None:
    """Compare the agent's source lines with the reference's and write bloat.json."""
    markers = protected_markers()
    agent, reference = source_sloc(agent_patch(), markers), source_sloc(REFERENCE.read_text(), markers)
    write_json(
        "bloat.json",
        {
            "agent_sloc": agent,
            "reference_sloc": reference,
            "patch_bloat": round(agent / reference, 3) if reference and agent else None,
        },
    )


STAGES: dict[str, Callable[[], None]] = {"rubric": rubric, "taste": taste, "bloat": bloat}


if __name__ == "__main__":
    stage = sys.argv[1]
    try:
        STAGES[stage]()
        print(f"{stage}: done")
    except Exception as error:  # noqa: BLE001 - an infrastructure failure: the trial is invalid, not a fail for the agent
        write_json(f"{stage}.json", {"status": "error", "error": repr(error)})
        print(f"{stage} error: {error!r}")
