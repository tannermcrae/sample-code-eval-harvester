# Tasks: coding-eval-harvest

**Tech plan:** [../tech-plan.md](../tech-plan.md)  ·  **PRD:** [../prd.md](../prd.md)
**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor
coding-eval datapoints from the merged fixes of their own repository. The history of the repository
does not fill the context of the agent.

> **Language:** These tasks use ASD-STE100 Simplified Technical English. Code names, file names,
> and quotes are verbatim.

These 12 tasks merge the 26 tasks of tech plan §13 into larger units. The `merges:` field in each
task file gives the §13 IDs that it contains. The IDs here are new. Thus `G-1` here is §13 `G-2`,
and `G-2` here is §13 `G-1`.

## Status

| ID | Title | What it does | Status | Blocked By | Complexity |
|----|-------|--------------|--------|-----------|-----------|
| [A-1](A-1-fix-the-two-existing-survey-and-registry-defects.md) | [Prerequisites] Fix the two existing survey and registry defects | Stop the review survey from blocking PRs that have no tests. Write `registry.json` as a list that Harbor can load. Two separate commits. | todo | — | M |
| [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md) | [Prerequisites] Add the shared prerequisites for coding datapoints | Add the small shared parts: author email in the survey, one test-file rule, Harbor fields for coding tasks, a refusal log, and issue fetch. | todo | A-1 | L |
| [B-1](B-1-add-the-paged-coding-survey.md) | [Survey] Add the paged coding survey | Add `survey --type coding`. It gives facts and exclusion reasons for each merged PR, a fixed rank, a saved result, and small pages. | todo | A-2 | L |
| [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md) | [Capture] Add the coding candidate and capture --type coding | Make the coding candidate file and its checks. `capture --type coding` writes it from one PR or from the survey. | todo | A-2, B-1 | L |
| [C-2](C-2-add-coding-views-to-brief-show-and-annotate.md) | [Capture] Add coding views to brief, show, and annotate | Show the agent a short coding brief and the fix of one file. Let the agent fill the coding slots. | todo | C-1 | M |
| [D-1](D-1-write-the-coding-task-templates-and-the-reward-stack.md) | [Emit] Write the coding task templates and the reward stack | Write the fixed files of each coding task: two Dockerfiles, the instruction, the oracle script, and the reward stack from notebook 03. | todo | — | L |
| [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md) | [Emit] Assemble and promote coding tasks with emit --kind fix | Make a complete Harbor task from a filled coding candidate. Verify it, then move it into the dataset. | todo | A-2, C-1, D-1, E-1 | L |
| [E-1](E-1-add-the-static-coding-checks-to-verify.md) | [Verify] Add the static coding checks to verify | Make `verify` read the datapoint type. Add each coding check that reads files only: leak scans, uniform instruction, answer placement, spec checks. | todo | A-2, D-1 | L |
| [E-2](E-2-resolve-hidden-tests-proven-and-add-the-guard-tests.md) | [Verify] Resolve hidden-tests-proven and add the guard tests | Read the proof file of the runner in `verify`. Then break each new coding check on purpose and watch it fail. | todo | D-1, E-1 | M |
| [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md) | [Dataset] Add the per-type dataset report and the coding help | Count review tasks and coding tasks separately in the dataset report. Teach the coding method in the help and the man page. | todo | A-1, A-2, B-1, C-1, C-2, D-2, E-2 | L |
| [G-1](G-1-measure-the-coding-survey-yield-on-real-repositories.md) | [Eval] Measure the coding survey yield on real repositories | Run the coding survey on 1–2 real repositories and on a 1000-PR benchmark. Set the open size and speed targets. | todo | B-1 | M |
| [G-2](G-2-add-the-coding-kill-criterion-objective.md) | [Eval] Add the coding kill-criterion objective | Add one coding objective to the eval harness. An agent with only the help tries to build a coding datapoint. | todo | F-1 | M |

## Dependency Graph

```
A-1 ──► A-2 ──┬──► B-1 ──┬──► C-1 ──┬──► C-2
              │          │          └──► D-2
              │          └──► G-1
              └──► E-1 ──┬──► D-2
                         └──► E-2
D-1 ──┬──► E-1
      ├──► E-2
      └──► D-2

F-1 ◄── A-1, A-2, B-1, C-1, C-2, D-2, E-2
F-1 ──► G-2
```

One valid order: A-1, D-1, A-2, B-1, E-1, C-1, G-1, C-2, E-2, D-2, F-1, G-2.

## Ready To Start

Tasks with no unmet blockers:
- **A-1**: [Prerequisites] Fix the two existing survey and registry defects
- **D-1**: [Emit] Write the coding task templates and the reward stack

## Parallel Workstreams

- **From the start:** A-1 → A-2 and D-1 are independent.
- **After A-2:** B-1 and E-1 are independent (E-1 also needs D-1).
- **After B-1:** C-1 and G-1 are independent. Run G-1 early. Its yield result can change the scope
  (tech plan §14, PRD §9).
- **Integration points:** D-2 joins capture, templates, and verify. F-1 joins all verbs in the help
  and the dataset report. The end-to-end test is in tech plan §14: `survey --type coding` →
  `capture --type coding` → `annotate` → `emit --kind fix` → `verify` (exit 5) → put a valid proof
  file → `verify` → `dataset`.

## Requirement Coverage

Each requirement in tech plan §2 has at least one task.

| Requirement | Tasks |
|------------|-------|
| FR-1, FR-2, FR-3 | F-1 |
| FR-4 | G-2 (passes only the static checks until a runner exists, ADR-2) |
| FR-5, FR-6 | A-2, B-1 |
| FR-7, FR-8, FR-9, FR-10, FR-11 | B-1 |
| FR-12 | A-2, C-1 |
| FR-13 | A-2, C-1 |
| FR-14 | C-1 |
| FR-15 | C-2 |
| FR-16, FR-17 | C-1, C-2 |
| FR-18 | C-2 |
| FR-19 | C-2, and the existing `test_no_model_client.py` in every task |
| FR-20 | D-1, D-2 |
| FR-21 | C-1, D-2 |
| FR-22 | C-1, D-2, F-1 |
| FR-23 | E-1, E-2 |
| FR-24 | E-1 |
| FR-25, FR-26, FR-27, FR-28, FR-29 | E-1, E-2 (FR-27 also D-1) |
| FR-30 | E-1 |
| FR-31 | D-2 |
| FR-32 | F-1 |
| FR-33 | A-2, B-1, C-1, F-1 |
| FR-34 | A-1, A-2, D-1, F-1 |
| FR-35 | E-2 |
| NFR-1 | B-1, C-1, D-2, and golden tests in the other tasks |
| NFR-2 | B-1, C-1, C-2 |
| NFR-3 | B-1, C-2; target set in G-1 |
| NFR-4 | A-2, B-1, C-1 |
| NFR-5 | A-2, B-1 (max 6 git calls for each PR); benchmark and target in G-1 |
| NFR-6 | Existing dependency audit test, in the acceptance criteria of each task |
| NFR-7 | C-1, C-2, D-2 |
| NFR-8 | D-1, E-1, E-2 |
| NFR-9 | Existing suite, in the acceptance criteria of each task |
| NFR-10 | Existing CI matrix; B-1, E-1, F-1 name it |
| TP-1, TP-2, TP-3, TP-5, TP-6 | A-2 |
| TP-4 | F-1 |
| TP-7, TP-8 | A-1 |

**Not satisfied by any task:** The PRD criteria "each fail-to-pass test fails at the base and passes
with the oracle" (US-6) and "refuse a base that does not build" (US-5). The plan moves the runner to
a separate project (ADR-2). E-2 reads the proof file of that runner. Until the runner exists, these
two criteria stay unresolved.

## Changes From Tech Plan §13

- The 26 tasks are merged into 12. The `merges:` field in each task gives the original IDs.
- **F-1 waits for C-2 and E-2.** In §13, the help text (old F-2) could start before the verbs that it
  describes. This new dependency prevents help tests that pin text for behavior that does not exist.
- **The yield measurement stays a separate task (G-1).** If it was in the same task as G-2, it would
  wait for F-1. The plan says to run it early.
- **NFR-5 has an owner.** §13 gave it no task. B-1 counts the git calls, and G-1 runs the benchmark.

## Conventions

- When you start or finish a task, change its `status` in the frontmatter and in the table above.
- `todo` → `in-progress` → `done`.
- A task is done when each box in its Acceptance Criteria is checked.
- Before each commit, `mise run check` must pass (CLAUDE.md).
