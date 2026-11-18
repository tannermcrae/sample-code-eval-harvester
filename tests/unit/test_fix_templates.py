"""The fixed template files of a coding task (D-1): the two Dockerfiles, the instruction, and the oracle.

The templates are data the CLI never imports (§3). D-2 fills their slots and E-1 compares the instruction
with the template bytes, so these tests pin the parts that later tasks and the sealing guarantee rely on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import eval_harvest

TEMPLATE_DIR = Path(eval_harvest.__file__).parent / "verifier_tpl"
FIX_TEMPLATE_DIR = TEMPLATE_DIR / "fix"
REVIEW_DOCKERFILE = TEMPLATE_DIR / "Dockerfile.tmpl"
AGENT_DOCKERFILE = FIX_TEMPLATE_DIR / "Dockerfile.tmpl"
VERIFIER_DOCKERFILE = FIX_TEMPLATE_DIR / "verifier.Dockerfile.tmpl"
INSTRUCTION = FIX_TEMPLATE_DIR / "instruction.md"
SOLVE_SCRIPT = FIX_TEMPLATE_DIR / "solve.sh"

BOTH_DOCKERFILES = pytest.mark.parametrize("dockerfile", [AGENT_DOCKERFILE, VERIFIER_DOCKERFILE], ids=["agent", "verifier"])


def read_template_lines(path: Path) -> list[str]:
    """The lines of a template file, without their line endings."""
    return path.read_text(encoding="utf-8").splitlines()


def index_of_line_containing(lines: list[str], fragment: str) -> int:
    """The index of the only line that contains `fragment`; fails the test when there is not exactly one."""
    matching_indexes = [index for index, line in enumerate(lines) if fragment in line]
    assert len(matching_indexes) == 1, f"expected exactly one line with {fragment!r}, found {len(matching_indexes)}"
    return matching_indexes[0]


def lines_from_base_image_to_setup_steps(path: Path) -> list[str]:
    """The lines from `FROM @BASE_IMAGE@` up to and including the `@SETUP_STEPS@` slot."""
    lines = read_template_lines(path)
    start = index_of_line_containing(lines, "FROM @BASE_IMAGE@")
    end = index_of_line_containing(lines, "@SETUP_STEPS@")
    return lines[start : end + 1]


def test_fix_dockerfile_seal_matches_review_seal() -> None:
    """Lines 15-34 of the review template (FROM to the seal) occur byte-identical and in sequence in the agent image."""
    review_seal = "".join(REVIEW_DOCKERFILE.read_text(encoding="utf-8").splitlines(keepends=True)[14:34])
    assert review_seal.startswith("FROM @BASE_IMAGE@\n")
    assert review_seal.endswith(" && git gc --prune=now --aggressive\n")
    assert review_seal in AGENT_DOCKERFILE.read_text(encoding="utf-8")


def test_fix_dockerfile_applies_no_change() -> None:
    """The agent image starts from the base with no change: no patch is copied or applied."""
    text = AGENT_DOCKERFILE.read_text(encoding="utf-8")
    assert "change.patch" not in text
    assert "git apply" not in text


@BOTH_DOCKERFILES
def test_setup_steps_come_after_the_seal(dockerfile: Path) -> None:
    """The setup steps run after the seal, so they cannot fetch history (FR-20)."""
    lines = read_template_lines(dockerfile)
    assert index_of_line_containing(lines, "@SETUP_STEPS@") > index_of_line_containing(lines, "git gc --prune=now")


def test_setup_steps_slot_is_alone_on_its_line() -> None:
    """D-2 replaces the whole slot line with RUN lines, so nothing else may share it."""
    for dockerfile in (AGENT_DOCKERFILE, VERIFIER_DOCKERFILE):
        lines = read_template_lines(dockerfile)
        assert lines[index_of_line_containing(lines, "@SETUP_STEPS@")] == "@SETUP_STEPS@"


def test_verifier_dockerfile_shares_the_agent_prefix() -> None:
    """From the base image to the setup steps, the verifier builds exactly what the agent image builds."""
    assert lines_from_base_image_to_setup_steps(VERIFIER_DOCKERFILE) == lines_from_base_image_to_setup_steps(AGENT_DOCKERFILE)


def test_verifier_base_is_copied_after_setup() -> None:
    """`/work/base` is copied after the setup steps, so the clean base has the installed dependencies."""
    lines = read_template_lines(VERIFIER_DOCKERFILE)
    assert index_of_line_containing(lines, "cp -a /work/repo /work/base") > index_of_line_containing(lines, "@SETUP_STEPS@")


def test_verifier_installs_boto3_outside_the_repository_environment() -> None:
    """The reward stack's boto3 goes into its own --target directory, never the repository's environment."""
    lines = read_template_lines(VERIFIER_DOCKERFILE)
    install_line = lines[index_of_line_containing(lines, "@VERIFIER_BOTO3_PIN@")]
    assert "--target /opt/eval-harvest-verifier" in install_line


def test_verifier_copies_the_test_directories_in_order() -> None:
    """The verifier image carries the reward stack under /tests, in the order the task names."""
    copy_lines = [
        line for line in read_template_lines(VERIFIER_DOCKERFILE) if line.startswith("COPY ") and "@LOCKFILE@" not in line
    ]
    assert copy_lines == [
        "COPY --chmod=755 test.sh /tests/test.sh",
        "COPY verify /tests/verify",
        "COPY validate /tests/validate",
        "COPY judge /tests/judge",
        "COPY lib /tests/lib",
    ]


@BOTH_DOCKERFILES
def test_lockfile_is_copied_outside_the_repository(dockerfile: Path) -> None:
    """The lockfile lands outside /work/repo, so `git status` in the repository stays clean."""
    assert "COPY @LOCKFILE@ /opt/eval-harvest/@LOCKFILE@\n" in dockerfile.read_text(encoding="utf-8")


@BOTH_DOCKERFILES
def test_dockerfiles_copy_nothing_from_outside_ecr_public(dockerfile: Path) -> None:
    """No `COPY --from=` an image: every image the build pulls must come from ECR Public (Notes & Gotchas)."""
    assert "--from=" not in dockerfile.read_text(encoding="utf-8")


@pytest.mark.parametrize("slot", ["@TASK@", "@TOOLING@"])
def test_instruction_has_each_slot_once(slot: str) -> None:
    """Each instruction slot occurs exactly once, so D-2 and E-1 can find and replace it unambiguously."""
    assert INSTRUCTION.read_text(encoding="utf-8").count(slot) == 1


def test_instruction_keeps_general_heading() -> None:
    """The judges cut the instruction at this heading; without it they send the general text as task text."""
    assert INSTRUCTION.read_text(encoding="utf-8").count("## General instructions") == 1


def test_instruction_ends_with_one_newline() -> None:
    """The template ends with exactly one newline, so the rendered bytes E-1 compares are stable."""
    text = INSTRUCTION.read_text(encoding="utf-8")
    assert text.endswith("code alone.\n")
    assert not text.endswith("\n\n")


@pytest.mark.parametrize("tooling_word", ["pytest", "Python", "pip", "npm"])
def test_instruction_is_language_neutral(tooling_word: str) -> None:
    """Tooling text in the shared part would give away the type of task, and FR-27 would not be uniform."""
    assert tooling_word.lower() not in INSTRUCTION.read_text(encoding="utf-8").lower()


def test_fix_solve_applies_oracle_patch() -> None:
    """The oracle applies /solution/oracle.patch in the agent image, and reads nothing under /tests."""
    text = SOLVE_SCRIPT.read_text(encoding="utf-8")
    assert "git apply --whitespace=nowarn /solution/oracle.patch" in text
    assert "/tests" not in text
    assert text.startswith("#!/bin/sh\n")


def test_fix_templates_have_no_slot_from_another_template() -> None:
    """Every slot occurs only in the templates the slot table names for it (D-2's render contract)."""
    slots_by_template = {
        AGENT_DOCKERFILE: {"@BASE_IMAGE@", "@REPO_URL@", "@BASE_COMMIT@", "@LOCKFILE@", "@SETUP_STEPS@"},
        VERIFIER_DOCKERFILE: {
            "@BASE_IMAGE@",
            "@REPO_URL@",
            "@BASE_COMMIT@",
            "@LOCKFILE@",
            "@SETUP_STEPS@",
            "@VERIFIER_BOTO3_PIN@",
        },
        INSTRUCTION: {"@TASK@", "@TOOLING@"},
        SOLVE_SCRIPT: set(),
    }
    every_slot = set().union(*slots_by_template.values())
    for template, expected_slots in slots_by_template.items():
        text = template.read_text(encoding="utf-8")
        present_slots = {slot for slot in every_slot if slot in text}
        assert present_slots == expected_slots, template.name


def test_every_python_stage_runs_with_the_verifier_python() -> None:
    """test.sh runs each reward-stack stage through `verifier_python`, never the repository's `python` on PATH."""
    test_script = (FIX_TEMPLATE_DIR / "test.sh").read_text(encoding="utf-8")
    stage_lines = [line for line in test_script.splitlines() if ".py" in line and not line.lstrip().startswith("#")]
    assert len(stage_lines) == 7
    assert all(line.startswith("verifier_python ") for line in stage_lines), stage_lines
    assert 'verifier_python() { PYTHONPATH=/opt/eval-harvest-verifier "$VERIFIER_PYTHON" "$@"; }' in test_script
    assert "VERIFIER_PYTHON=/usr/bin/python3" in test_script
