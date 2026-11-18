---
id: G-2
title: "[Eval] Add the coding kill-criterion objective"
summary: "Add one coding objective to the eval harness, so an agent with only the help can try to build a coding datapoint."
feature: coding-eval-harvest
workstream: Eval
status: todo
complexity: M
merges: [G-1]
implements: [FR-4]
user_story: US-1
blocked_by: [F-1]
blocks: []
---

# G-2: [Eval] Add the coding kill-criterion objective

**In short:** Give the eval harness one real fix PR to work from. An agent that reads only the
`eval-harvest` help must build a coding datapoint from it. The report then shows if the static
checks pass.

## Context

US-1 says that the help is the method: an agent with only the help output must make a coding
datapoint. This is the kill-criterion test that the PRD takes from pr-eval-harvest. The eval
harness in `eval/` runs this test for review datapoints today, but it knows only the `reject` and
`approve` kinds. This task adds the `fix` kind and one objective. Without it, nobody measures if
the coding help is sufficient.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-4  ·  **User story:** US-1

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, FR-4: `eval/objectives/<repo>-fix.toml` loads through `eval/catalog.py`, and the result counts as a kill-criterion run.
- **Tech plan:** §5, last paragraph "Requirements that this solution does not satisfy": FR-4 cannot show the fail-to-pass proof until a runner exists.
- **ADR-2** in §11: the CLI does not run the code of the repository, so `hidden-tests-proven` stays unresolved. This decision is settled. Do not add a runner here.
- **PRD:** `../prd.md` US-1, third acceptance criterion; §8 "Sufficient for v1".
- **Existing harness:** `eval/README.md` (layout table) and `eval/objectives/pydantic-13611-reject.toml` (the record format).

## What To Build

1. In `eval/models.py`:
   - Change `DatapointKind` (line 23) to `Literal["reject", "approve", "fix"]`.
   - Add `FixExpectedProperties(BaseModel)`, frozen, with `minimum_fail_to_pass: int = Field(ge=1)` and `must_touch_paths: tuple[str, ...] = ()`. `must_touch_paths` names source paths that the oracle patch must change. It is data for a reader of the report; the harness does not check it.
   - Change `Objective.expected_properties` to `ExpectedProperties | FixExpectedProperties`. Add a `model_validator(mode="after")` that refuses a `fix` objective with `ExpectedProperties`, and a `reject`/`approve` objective with `FixExpectedProperties`.
2. Add `eval/objectives/strands-evals-338-fix.toml`. Use the record format of `pydantic-13611-reject.toml`:
   ```toml
   # Fix objective: strands-agents/evals PR #338 fixed issue #337. Notebook 03 built a coding
   # datapoint from this PR by hand, and it passed the oracle and no-op calibration (PRD §4).
   id = "strands-evals-338-fix"
   repo_url = "https://github.com/strands-agents/evals"
   commit_sha = "<the full SHA of the main branch after the merge of PR #338>"
   pr = 338
   kind = "fix"

   [expected_properties]
   minimum_fail_to_pass = 1
   must_touch_paths = ["src/strands_evals/extractors/trace_extractor.py"]
   ```
   Find `commit_sha` with `git ls-remote` or the GitHub UI, and write the full 40-character SHA. `[TODO: verify the SHA, that the repository is public, and the changed path]`
3. In `eval/harbor_task.py`:
   - Add `_FIX_INSTRUCTION_TEMPLATE` next to `_INSTRUCTION_TEMPLATE` (line 58). Copy its structure. Change the heading and the task sentence to `Build coding evaluation datapoints` and `Build coding evaluation datapoints from this repository's merged fixes with \`eval-harvest\``. Keep the sentence that sends the agent to `--help` and the man pages. Do not add any methodology.
   - In `instruction_text` (line 195), select the template by `objective.kind`.
   - In `task_config_document`, use the keyword `"coding"` in place of `"pr-review"` for a `fix` objective (line 167).
4. In `eval/report.py`:
   - For a `fix` objective, the verify gate is "no check failed". The unresolved checks `hidden-tests-proven` and `git-channel-absence` are permitted, because the CLI cannot resolve them without a runner (ADR-2). Add `default_verify_fix(task_dir) -> bool` that returns `not Verify.verify_task(task_dir).failures` (the `failures` property is at `src/eval_harvest/verify.py:159`), and select it in `ScoringSeams` by kind.
   - For a `fix` objective, skip the quality judge. The judge in `eval/judge/` grades review datapoints only. Make `_failure_reason` (line 445) return `None` for a `fix` trial that was produced and passed the fix verify gate.
   - In the rendered report, add the text `static checks only; hidden tests unproven until a runner writes a proof file` next to each `fix` objective row.
5. Update `eval/README.md`: add the `fix` kind to the objective description, and add one paragraph that says what a `fix` pass means and what it does not prove.
6. Do not change `src/eval_harvest/`. `eval/` imports the package; the package never imports `eval/` (CLAUDE.md).

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `eval/models.py` | Modify | `DatapointKind`, `FixExpectedProperties`, the `Objective` validator. |
| `eval/objectives/strands-evals-338-fix.toml` | Create | The objective record. |
| `eval/harbor_task.py` | Modify | Coding instruction template, keyword. |
| `eval/report.py` | Modify | Fix verify gate, no judge for `fix`, report text. |
| `eval/README.md` | Modify | Document the `fix` kind. |
| `tests/integration/test_eval_harness.py` | Modify | Update `test_catalog_loads_two_objectives_one_reject_one_approve` (line 89) and add the tests below. |

## Schemas & Contracts

**Before** (`eval/models.py:23`, `:26`, `:50`):
```python
DatapointKind = Literal["reject", "approve"]


class ExpectedProperties(BaseModel):
    expected_verdict: Literal["block", "approve"]
    minimum_findings: int = Field(ge=0)
    must_mention: tuple[str, ...] = ()


class Objective(BaseModel):
    ...
    kind: DatapointKind
    expected_properties: ExpectedProperties
```

**After:**
```python
DatapointKind = Literal["reject", "approve", "fix"]


class FixExpectedProperties(BaseModel):
    model_config = ConfigDict(frozen=True)
    minimum_fail_to_pass: int = Field(ge=1)
    must_touch_paths: tuple[str, ...] = ()


class Objective(BaseModel):
    ...
    kind: DatapointKind
    expected_properties: ExpectedProperties | FixExpectedProperties
    # model_validator: kind "fix" <=> FixExpectedProperties
```

**Migration:** None. The two review objectives load as before.
**Backward compatibility:** Review objectives, their instruction text, and their report rows do not change.

## How To Verify

```bash
uv run pytest tests/integration/test_eval_harness.py tests/integration/test_eval_runner.py
mise run lint
mise run typecheck
mise run check
```

Then the live run, outside `check` (needs Bedrock and the eval infrastructure; it skips cleanly without them):

```bash
mise run eval -- --objectives eval/objectives
```

`[TODO: verify the exact flag that runs one objective only; eval/run.py:102 has --objectives <dir>]`

Look at the report in `eval/reports/`. The `strands-evals-338-fix` row shows the produced/verify result and the "static checks only" text.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_catalog_loads_three_objectives` (replaces the line 89 test) | The catalog has 3 objectives with kinds `{reject, approve, fix}`, each with a full SHA. | The new file is not loaded, or its SHA is short. |
| `test_fix_objective_requires_fix_properties` | A `fix` objective with `expected_verdict` fails validation. A `reject` objective with `minimum_fail_to_pass` fails validation. | A kind and its properties do not match, and the report reads the wrong fields. |
| `test_fix_instruction_has_no_methodology` | The `fix` instruction says "coding" and sends the agent to `--help`. It contains none of the methodology terms that `test_eval_harbor_task_is_harbor_valid_and_leaks_no_methodology` (line 250) forbids, and none of `fail-to-pass`, `pass-to-pass`, `oracle`, `hidden test`. | The instruction teaches the method, so the test no longer measures the help. |
| `test_fix_trial_passes_with_only_unresolved_checks` | A recorded `fix` trial whose datapoint has only `hidden-tests-proven` and `git-channel-absence` unresolved counts as passed, and the judge seam is not called. | Every `fix` trial fails because `VerifyReport.ok` is false when anything is unresolved. |
| `test_fix_trial_with_a_failed_check_fails` | A recorded `fix` trial with one failed check (for example `instruction-names-fix`) has the failure reason `failed-verify`. | The fix gate also ignores real failures. |
| `test_review_objectives_unchanged` | The two review objectives give the same instruction bytes and the same `task.toml` as before. | The kind switch changes the review eval. |

## Acceptance Criteria

- [ ] `ObjectiveCatalog.load_all()` loads 3 objectives, and the new one has `kind = "fix"`.
- [ ] The `fix` instruction teaches no methodology and sends the agent to the help.
- [ ] A `fix` trial passes when it produced a datapoint and no verify check failed. The report says that the hidden tests are not proven.
- [ ] One live `mise run eval` run exists for the `fix` objective, and its result is recorded in `../decisions-tech-plan.md` as the first coding kill-criterion run.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9). The package does not import `eval/` (`test_package_does_not_import_the_eval_harness`, line 274).

## Out Of Scope

- A runner that writes proof files, and the fail-to-pass proof for the objective: a separate project (ADR-2).
- A quality judge for coding datapoints. The PRD does not ask for one, and the reward stack in the task (D-1) grades the agent under test, not the datapoint.
- More coding objectives. Adding one is dropping one more file into `eval/objectives/`.

## Notes & Gotchas

- **The plan rated this task S.** It is M, because the harness is typed for review only: `DatapointKind`, `ExpectedProperties`, the instruction template, and the report pass rule all assume a review datapoint.
- **`VerifyReport.ok` is false when any check is unresolved** (`src/eval_harvest/verify.py:169`). A coding datapoint always has `hidden-tests-proven` unresolved without a proof file (FR-23). If you use `default_verify` (`eval/report.py:137`) for `fix`, every trial fails.
- **The agent under test may have seen this PR.** strands-agents/evals is public, and notebook 03 used PR #338. The PRD v1 bar asks for "one real repository that the agent did not see before". Add a second objective from a repository that is new to the agent before you claim v1.
- **The objective pins the SHA after the merge.** The agent must find the fix PR in history, so the checkout must contain it. A SHA before the merge gives an agent nothing to find.
- **The eval is outside `check`.** `mise run eval` needs AWS credentials and Bedrock (`mise.toml`, task `eval`). The offline tests use the recorded agent (`RecordedAgent` in `eval/agent.py`).

## Dependencies

**Blocked by:** [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md) (the coding help text that the agent reads)
**Blocks:** nothing
