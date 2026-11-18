"""The declared registry of US-7 guards and the guard-report renderer (E-3, FR-37).

"A guard nobody has watched fail is decorative" (CLAUDE.md). This module is the single source of
truth for *which* US-7 checks exist and *which* watched-to-fail test exercises each — shared by
``tests/test_guards.py`` (which runs each guard break-then-fix and asserts this registry stays
complete and honest) and ``scripts/gen_guard_report.py`` (which renders ``guard-report.md`` from it).
Because the report is a pure function of :data:`GUARDS`, it cannot drift from the guards: adding a
check without a guard, or naming a test that does not exist, is caught by ``test_guards.py``.

Kept free of ``pytest`` and the fixture builder on purpose, so the generator's import surface is the
registry alone — no test machinery, no git, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class GuardSpec:
    """One US-7 guard: the invariant it protects, the check that fires, and the test that watched it fail.

    ``guard_id`` is the stable slug the report and the coverage test key on — distinct even when two
    guards share a ``check`` name (the content leak and the git-channel leak both fire
    ``answer-present``, but protect different channels, so each is its own row). ``test`` is the name
    of the ``test_guards.py`` function that constructs the violation and asserts both red and green.
    """

    guard_id: str
    check: str
    scenario: str
    invariant: str
    test: str


#: Every US-7 check, one :class:`GuardSpec` per invariant (S-2, S-3, S-4, S-8, S-9, S-12). The four
#: preconditions (S-12) share one test (``test_guard_preconditions``) because they are one scenario —
#: four broken candidates, each its own specific failure. ``test_guards.py`` asserts this set is
#: exactly the canonical US-7 set and that every named test exists and asserts red *and* green.
GUARDS: Final[tuple[GuardSpec, ...]] = (
    GuardSpec(
        guard_id="content-leak",
        check="answer-present",
        scenario="S-2",
        invariant="the PR number is absent from agent-visible files",
        test="test_guard_content_leak",
    ),
    GuardSpec(
        guard_id="git-channel-leak",
        check="answer-present",
        scenario="S-3",
        invariant="no git channel (alternates, packed-refs, commit-graph, refs/replace, "
        "reflog, include.path, bare repo) reaches otherwise-hidden content",
        test="test_guard_git_channel_leak",
    ),
    GuardSpec(
        guard_id="missing-control-token",
        check="scan-did-not-run",
        scenario="S-4",
        invariant="the planted control token is present in the scanned corpus",
        test="test_guard_missing_control_token",
    ),
    GuardSpec(
        guard_id="verdict-coherence",
        check="approve-with-high-finding",
        scenario="S-8",
        invariant="an approve datapoint carries no high-severity finding",
        test="test_guard_verdict_coherence",
    ),
    GuardSpec(
        guard_id="risk-disagreement",
        check="change_risk_disagreement",
        scenario="S-9",
        invariant="a structural/classified risk disagreement is recorded, never silently resolved",
        test="test_guard_risk_disagreement",
    ),
    GuardSpec(
        guard_id="precondition-base",
        check="base-missing",
        scenario="S-12",
        invariant="the base commit exists in the clone",
        test="test_guard_preconditions",
    ),
    GuardSpec(
        guard_id="precondition-patch",
        check="patch-does-not-apply",
        scenario="S-12",
        invariant="change.patch applies cleanly at the base commit",
        test="test_guard_preconditions",
    ),
    GuardSpec(
        guard_id="precondition-finding-line",
        check="finding-line-absent",
        scenario="S-12",
        invariant="every oracle finding references a line the change touches",
        test="test_guard_preconditions",
    ),
    GuardSpec(
        guard_id="precondition-rubric",
        check="rubric-incoherent",
        scenario="S-12",
        invariant="the rubric is present, non-empty, and version-pinned",
        test="test_guard_preconditions",
    ),
    # The coding-datapoint guards (coding-eval-harvest, US-6). `hidden-tests-proven` has three guards,
    # one per way it must not pass: a broken rule, a stale proof, and no proof at all.
    GuardSpec(
        guard_id="coding-content-leak",
        check="answer-present",
        scenario="US-6",
        invariant="the issue number, PR and issue URLs, merge SHA, and hidden test names are absent "
        "from agent-visible files of a coding task",
        test="test_guard_coding_content_leak",
    ),
    GuardSpec(
        guard_id="instruction-names-fix",
        check="instruction-names-fix",
        scenario="US-6",
        invariant="the instruction names no path or symbol that the oracle patch changes",
        test="test_guard_instruction_names_fix",
    ),
    GuardSpec(
        guard_id="instruction-scan-control-token",
        check="scan-did-not-run",
        scenario="US-6",
        invariant="the planted instruction control token is present in the scanned instruction",
        test="test_guard_instruction_scan_control_token",
    ),
    GuardSpec(
        guard_id="instruction-uniform",
        check="instruction-not-uniform",
        scenario="US-6",
        invariant="the instruction outside its two slots is the canonical template",
        test="test_guard_instruction_uniform",
    ),
    GuardSpec(
        guard_id="answer-placement",
        check="answer-misplaced",
        scenario="US-6",
        invariant="the oracle patch and the hidden tests are only in solution/ and tests/",
        test="test_guard_answer_placement",
    ),
    GuardSpec(
        guard_id="spec-coherent",
        check="spec-incoherent",
        scenario="US-6",
        invariant="the task has a validation story with a case, a fail_to_pass criterion, and a fail_to_pass test with a file",
        test="test_guard_spec_coherent",
    ),
    GuardSpec(
        guard_id="hidden-tests-proven-rule",
        check="hidden-tests-proven",
        scenario="US-6",
        invariant="in the proof, each fail_to_pass test fails at nop and each listed test passes at oracle",
        test="test_guard_hidden_tests_proven_rule",
    ),
    GuardSpec(
        guard_id="hidden-tests-proven-stale",
        check="hidden-tests-proven",
        scenario="US-6",
        invariant="a proof whose content_digest is not the task digest never passes",
        test="test_guard_hidden_tests_proven_stale",
    ),
    GuardSpec(
        guard_id="hidden-tests-proven-no-proof",
        check="hidden-tests-proven",
        scenario="US-6",
        invariant="without a proof the check is unresolved, never passed",
        test="test_guard_hidden_tests_proven_no_proof",
    ),
    GuardSpec(
        guard_id="base-does-not-build",
        check="base-does-not-build",
        scenario="US-6",
        invariant="a proof trial with a build failure fails the datapoint",
        test="test_guard_base_does_not_build",
    ),
)

#: The report's path relative to the repository root. ``test_guards.py`` reads the committed file and
#: asserts it equals :func:`render_guard_report`; ``gen_guard_report.py`` writes it.
REPORT_RELATIVE_PATH: Final = Path("docs/features/pr-eval-harvest/guard-report.md")


def repo_root() -> Path:
    """The repository root: this file lives at ``<root>/tests/guard_registry.py``."""
    return Path(__file__).resolve().parents[1]


def render_guard_report() -> str:
    """Render the guard report as markdown — a pure function of :data:`GUARDS`, so it cannot drift.

    Lists every US-7 check with the invariant it protects and the test that watched it go red. The
    content is deterministic (registry order, LF line endings, trailing newline) so a byte-comparison
    in ``test_guards.py`` is a stable drift check.
    """
    lines = [
        "<!-- Generated by scripts/gen_guard_report.py from tests/guard_registry.py — do not edit by hand. -->",
        "# US-7 guard verification report",
        "",
        "Every US-7 check has been *watched to fail*: the invariant is broken deliberately, the check goes",
        "red, and a corrected input passes (FR-37). This table is generated from `tests/guard_registry.py`,",
        "so it cannot claim coverage the suite does not have — `tests/test_guards.py` asserts the registry is",
        "complete and that every test below asserts both the red (violation) and green (correction) case.",
        "",
        "| Check | Scenario | Invariant it protects | Watched-to-fail test |",
        "|-------|----------|-----------------------|----------------------|",
    ]
    lines.extend(f"| `{guard.check}` | {guard.scenario} | {guard.invariant} | `{guard.test}` |" for guard in GUARDS)
    lines.append("")
    return "\n".join(lines)
