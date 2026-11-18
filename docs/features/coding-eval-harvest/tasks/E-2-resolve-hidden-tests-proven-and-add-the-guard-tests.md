---
id: E-2
title: "[Verify] Resolve hidden-tests-proven and add the guard tests"
summary: "Read the runner's proof file in verify, then break each new coding check on purpose and watch it fail."
feature: coding-eval-harvest
workstream: Verify
status: todo
complexity: M
merges: [E-4, E-5]
implements: [FR-23, FR-25, FR-26, FR-27, FR-28, FR-29, FR-35, NFR-8]
user_story: US-6
blocked_by: [D-1, E-1]
blocks: [F-1]
---

# E-2: [Verify] Resolve hidden-tests-proven and add the guard tests

**In short:** A separate runner runs each coding task two times: with no change, and with the
fix. It writes the results to a proof file. `verify` reads that file and decides if the hidden
tests are correct. Then, for each new coding check, a guard test breaks the rule, sees the check go
red, and sees it go green again.

## Context

The CLI does not run the code of the repository (ADR-2). Thus `verify` cannot prove that each
fail-to-pass test fails at the base and passes with the fix. After E-1, `hidden-tests-proven` is
always unresolved. This task adds the contract that lets a runner outside the CLI resolve the
check: the file `<dataset>/proofs/<task-dir>.json`. Without it, no coding datapoint can ever be
proven, and `dataset` (F-1) counts every coding datapoint as unproven.

CLAUDE.md also says: "A guard nobody has watched fail is decorative." E-1 and this task add the
coding checks. This task puts each check into the guard registry (`tests/guard_registry.py`). Then
the coverage test proves that every check has a watched-red and watched-green test, and the guard
report cannot claim coverage that the suite does not have. Without the guards, a later change can
make a coding check always pass, and only the review guards stay protected.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-35, FR-23, NFR-8 (guards for FR-25 to FR-29 and FR-35)  ·  **User story:** US-5, US-6

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, FR-35 and FR-23: the rules and the results (pass, failure, unresolved).
- **Tech plan:** §8, "`proofs/<task-dir>.json`": the proof schema. Use it exactly.
- **Tech plan:** §7.2, "A separate runner proves the hidden tests": the runner contract.
- **Tech plan:** §7.3, last two diagrams: the exit 5 text, and the exit 4 text when a fail-to-pass test passes at the base.
- **Tech plan:** §9, the `verify` row: the `--proof` flag.
- **Tech plan:** §12, "Guard verification": the list of guards and the break-then-fix steps for each.
- **Tech plan:** §2.2, NFR-8: a coding datapoint never reports `hidden-tests-proven` as passed without a proof, and every scan proves that it ran.
- **Tech plan:** §15, Definition of Done: "The guard report (`scripts/gen_guard_report.py`) lists the new guards", and "The proof-file schema is in `man/eval-harvest-coding.md`".
- **ADR-2** in §11: why the CLI reads a proof and does not run tests. Do not re-open it.
- **CLAUDE.md**, "Conventions worth keeping": the two rules about guards and control tokens.
- **PRD:** `../prd.md` US-5 and US-6, criterion 1 of each.

## What To Build

The work has two parts. Do Part 1 first, because the proof guards in Part 2 need it. Each part can
be a separate commit.

### Part 1: Resolve `hidden-tests-proven` from the proof file (FR-35, FR-23)

1. In `src/eval_harvest/verify.py`, add the keyword parameter `proof_path: Path | None = None` to `Verify.verify_task` (line 216).
2. Add the public class method `Verify.check_hidden_tests_proven(task_dir: Path, harvest: dict[str, object], proof_path: Path | None, datapoint: str) -> list[VerifyRefusal]`. It is public because F-1 calls it to count `coding_unproven`. An empty list means "proven". Replace the call to `_check_hidden_tests_unresolved` (E-1) in the coding list with this method. Delete `_check_hidden_tests_unresolved`.
3. The method returns:
   - **Unresolved** (`_unresolved`, line 674) when `proof_path` is `None`, when the file does not exist, when the JSON does not parse, when a required field is missing or has the wrong type, or when `content_digest` is not equal to `[metadata.harvest].content_digest` in `task.toml`. `offending` tells which of these occurred. For a parse error, it includes the error text.
   - **Failure** `base-does-not-build` when the `nop` trial or the `oracle` trial has `status = "build-failed"`. `offending` = "<trial> trial: <build_error>". `next_` = "fix the environment recipe with `annotate` (pin dependencies to merge_date), emit again, and run the trials again".
   - **Failure** `hidden-tests-proven` for each rule that does not hold. One refusal for each test that breaks a rule. `offending` names the test id and the rule. Example: "'tests/test_x.py::test_f2p_same_turn' passes at the base". `next_` = "the test does not observe the bug — change it or record it as pass_to_pass with `annotate`, emit again, and run the trials again".
   - **No refusal** (pass) when all rules hold.
4. The rules (FR-35). Read the test ids and kinds from the `[[hidden_tests]]` tables of `tests/verify/tests.toml` (D-1 schema: `id`, `repo_path`, `name`, `kind`). A test id is `<repo_path>::<name>`, as in §8.
   - `nop` trial: each `fail_to_pass` test is not `"passed"`. Each `pass_to_pass` test is `"passed"`. `existing_suite` is `"passed"`.
   - `oracle` trial: each listed test is `"passed"`. `existing_suite` is `"passed"`.
   - A listed test id with no entry in a trial's `tests` map is a failure: "'<id>' has no result in the <trial> trial".
   - A test id in the proof that `tests.toml` does not list is ignored. Do not fail it.
5. The default proof path. In `src/eval_harvest/cli.py`, in `handle_verify` (line 1642):
   - When `--proof <file>` is given, use it.
   - Otherwise, when `task_dir.resolve().parent.name == "tasks"`, use `task_dir.resolve().parent.parent / "proofs" / f"{task_dir.resolve().name}.json"`.
   - Otherwise, pass `None`.
   Pass the value to `Verify.verify_task(task_dir, clone=clone, proof_path=proof_path)`.
6. In `Cli.add_verify_arguments` (line 595), add `--proof <file>`, `type=Path`, `default=None`. Help text: "proof file from a runner (default: <dataset>/proofs/<task-dir>.json)".
7. `emit` calls `verify` on a temporary directory. It passes no `proof_path`. Thus `hidden-tests-proven` is unresolved at emit, and `Emit._run_verify_hook` (`emit.py:610`) does not block on it. Do not change `emit.py`.
8. **Create** `man/eval-harvest-coding.md` with only one section: "PROOF FILE (for runner authors)". Copy the §8 JSON example, the field rules, the test id form, and the three results. Put a NAME line at the top (`eval-harvest-coding - coding datapoints`), so the page is valid. F-1 writes the rest of the page and keeps this section.
9. In `tests/fixtures/coding_task.py` (E-1), add `CodingTaskBuilder.write_valid_proof(fixture, path)`. It writes a proof that obeys every rule, with the fixture's `content_digest`.

### Part 2: Guard tests for every new coding check (NFR-8)

10. In `tests/guard_registry.py`, add one `GuardSpec` to `GUARDS` for each row below, after the existing nine. Keep the field order: `guard_id`, `check`, `scenario`, `invariant`, `test`. Use `scenario="US-6"` for all coding guards. The existing `S-n` labels belong to the pr-eval-harvest PRD.

    | `guard_id` | `check` | `invariant` | `test` |
    |-----------|---------|-------------|--------|
    | `coding-content-leak` | `answer-present` | the issue number, PR and issue URLs, merge SHA, and hidden test names are absent from agent-visible files of a coding task | `test_guard_coding_content_leak` |
    | `instruction-names-fix` | `instruction-names-fix` | the instruction names no path or symbol that the oracle patch changes | `test_guard_instruction_names_fix` |
    | `instruction-scan-control-token` | `scan-did-not-run` | the planted instruction control token is present in the scanned instruction | `test_guard_instruction_scan_control_token` |
    | `instruction-uniform` | `instruction-not-uniform` | the instruction outside its two slots is the canonical template | `test_guard_instruction_uniform` |
    | `answer-placement` | `answer-misplaced` | the oracle patch and the hidden tests are only in solution/ and tests/ | `test_guard_answer_placement` |
    | `spec-coherent` | `spec-incoherent` | the task has a validation story with a case, a fail_to_pass criterion, and a fail_to_pass test with a file | `test_guard_spec_coherent` |
    | `hidden-tests-proven-rule` | `hidden-tests-proven` | in the proof, each fail_to_pass test fails at nop and each listed test passes at oracle | `test_guard_hidden_tests_proven_rule` |
    | `hidden-tests-proven-stale` | `hidden-tests-proven` | a proof whose content_digest is not the task digest never passes | `test_guard_hidden_tests_proven_stale` |
    | `hidden-tests-proven-no-proof` | `hidden-tests-proven` | without a proof the check is unresolved, never passed | `test_guard_hidden_tests_proven_no_proof` |
    | `base-does-not-build` | `base-does-not-build` | a proof trial with a build failure fails the datapoint | `test_guard_base_does_not_build` |

11. In `tests/integration/test_guards.py`, add the ten new `guard_id` values to `_CANONICAL_GUARD_IDS` (line 51). This set is stated independently of the registry on purpose. Do not build it from `GUARDS`.
12. In `test_guards.py`, write the ten test functions. Each one:
    - Builds a correct coding task with `CodingTaskBuilder.build` (`tests/fixtures/coding_task.py`, from E-1).
    - Copies the task with `shutil.copytree` and breaks the copy. Keep the original for the green half. This is the pattern of `test_guard_content_leak` (line 198).
    - Has a comment `# RED:` at the assertion on the broken copy, and `# GREEN:` at the assertion on the original. `test_guard_report_covers_every_us7_check` (line 371) checks that both labels are in the source.
13. The break for each guard:
    - `coding-content-leak`: write the full merge SHA into `instruction.md`. Red: `answer-present`. Use a parametrized helper for the other token types only if the helper keeps one red and one green assertion in this function.
    - `instruction-names-fix`: write the changed path into `instruction.md`. Red: `instruction-names-fix`.
    - `instruction-scan-control-token`: call `Verify.verify_task(..., plant_instruction_control_token=False)`. Red: `scan-did-not-run`. Also assert that `plant_control_token=True` stays, so the red comes from the instruction scan alone.
    - `instruction-uniform`: change one word in the general instructions part. Red: `instruction-not-uniform`.
    - `answer-placement`: copy `solution/oracle.patch` to `environment/fix.diff`. Red: `answer-misplaced`.
    - `spec-coherent`: remove every `fail_to_pass` criterion from `tests/judge/rubric.toml`. Red: `spec-incoherent`.
    - `hidden-tests-proven-rule`: in a valid proof (`write_valid_proof`, step 9), change one fail-to-pass result at nop from `"failed"` to `"passed"`. Red: a failure `hidden-tests-proven` that names the test. Green: the valid proof, and the check is in `report.passed`.
    - `hidden-tests-proven-stale`: change one character of `content_digest` in a valid proof. Red: `hidden-tests-proven` is in `report.unresolved` and not in `report.passed`. Green: the valid proof passes.
    - `hidden-tests-proven-no-proof`: give no proof. Red: `hidden-tests-proven` is in `report.unresolved` and not in `report.passed`. Green: a valid proof passes.
    - `base-does-not-build`: set `status = "build-failed"` and `build_error = "RUN uv pip sync"` in the nop trial. Red: `base-does-not-build`, and `offending` has the step. Green: the valid proof.
14. Regenerate the report with `uv run scripts/gen_guard_report.py`. It writes `docs/features/pr-eval-harvest/guard-report.md` (`REPORT_RELATIVE_PATH`). Commit the regenerated file. The coverage test compares the committed bytes with `render_guard_report()`.
15. Do not change `render_guard_report` or the report path. The coding guards go into the same registry and the same report. One report is the single source of truth (module docstring of `guard_registry.py`).
16. After the tests are green, break each check in the source one time by hand (for example, make `_check_answer_placement` return `[]`). Watch the guard test go red. Revert. Write the list of checks that you broke in the PR description.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/verify.py` | Modify | `proof_path` keyword on `verify_task`. New public `check_hidden_tests_proven`, and private helpers for parse, digest, and rules. Delete `_check_hidden_tests_unresolved` (E-1). |
| `src/eval_harvest/cli.py` | Modify | `--proof` in `add_verify_arguments` (line 595). Default path in `handle_verify` (line 1642). |
| `man/eval-harvest-coding.md` | Create | NAME line and the "PROOF FILE (for runner authors)" section only. F-1 writes the rest. |
| `tests/fixtures/coding_task.py` | Modify | Add `CodingTaskBuilder.write_valid_proof`. File created by E-1. |
| `tests/integration/test_verify_coding.py` | Modify | The Part 1 tests below. File created by E-1. |
| `tests/guard_registry.py` | Modify | Ten new `GuardSpec` entries in `GUARDS`. |
| `tests/integration/test_guards.py` | Modify | Ten ids in `_CANONICAL_GUARD_IDS` (line 51). Ten new test functions with `# RED:` and `# GREEN:` labels. |
| `docs/features/pr-eval-harvest/guard-report.md` | Regenerate | Run `uv run scripts/gen_guard_report.py`. Do not edit by hand. |

## Schemas & Contracts

**Before:** after E-1, `hidden-tests-proven` is always unresolved. `verify` has no `--proof` flag.
`GUARDS` has nine review guards. `_CANONICAL_GUARD_IDS` has the same nine ids.

**After:** the proof file (§8; the CLI reads it and never writes it):
```json
{
  "content_digest": "sha256:…",
  "runner": "<name and version of the runner>",
  "nop":    {"status": "completed", "build_error": "", "tests": {"tests/test_x.py::test_f2p_a": "failed", "tests/test_x.py::test_p2p_b": "passed"}, "existing_suite": "passed"},
  "oracle": {"status": "completed", "build_error": "", "tests": {"tests/test_x.py::test_f2p_a": "passed", "tests/test_x.py::test_p2p_b": "passed"}, "existing_suite": "passed"}
}
```
- `status`: `"completed"` or `"build-failed"`. Another value is unresolved.
- `build_error`: the failed step, when `status` is `"build-failed"`.
- `runner`: a non-empty string.

```python
def verify_task(..., proof_path: Path | None = None) -> VerifyReport
@classmethod
def check_hidden_tests_proven(cls, task_dir: Path, harvest: dict[str, object], proof_path: Path | None, datapoint: str) -> list[VerifyRefusal]
# [] = proven. One _unresolved refusal = no valid proof. Failures = a rule or the build failed.
```

CLI: `eval-harvest verify <task-dir> [--clone <dir>] [--proof <file>] [--json]`.

Results: pass → exit 0 (when no other check fails or is unresolved). A rule fails → exit 4.
No valid proof → exit 5.

Guards: `GUARDS` and `_CANONICAL_GUARD_IDS` each have nineteen ids. The guard report has ten more
rows, in registry order, for example:
```
| `instruction-names-fix` | US-6 | the instruction names no path or symbol that the oracle patch changes | `test_guard_instruction_names_fix` |
```

**Migration:** None.
**Backward compatibility:** `--proof` is a new optional flag. Review tasks do not run this check. The nine review guards and their tests do not change.

## How To Verify

```bash
uv run pytest tests/integration/test_verify_coding.py tests/integration/test_verify.py tests/integration/test_cli.py
uv run scripts/gen_guard_report.py
uv run pytest tests/integration/test_guards.py
mise run lint
mise run typecheck
mise run check
```

Manual checks:
1. In the fixture, write a valid proof and run the test with `-s`. The report shows `hidden-tests-proven` in `passed`, and only `git-channel-absence` is unresolved. Change one character of the digest. The check is unresolved again.
2. Open `docs/features/pr-eval-harvest/guard-report.md`. It lists the ten coding guards.
3. Do step 16 for each check.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_valid_proof_passes` | A valid proof puts `hidden-tests-proven` in `passed`. | The check can never pass. |
| `test_f2p_passing_at_nop_fails_by_name` | One fail-to-pass test `"passed"` at nop gives a failure that names the test. | A test that does not observe the bug is accepted. |
| `test_f2p_error_at_nop_counts_as_failing` | A fail-to-pass test with `"error"` at nop passes the rule. | A test that imports a symbol that the fix adds is refused. This is normal for fail-to-pass tests. |
| `test_p2p_failing_at_oracle_fails_by_name` | One pass-to-pass test `"failed"` at oracle gives a failure that names the test. | A broken pass-to-pass test is accepted. |
| `test_existing_suite_failing_at_nop_fails` | `existing_suite = "failed"` at nop fails. | A base whose suite fails is accepted (US-5). |
| `test_build_failed_gives_base_does_not_build` | `status = "build-failed"` gives `base-does-not-build` with the `build_error` text. | The build step is lost from the refusal. |
| `test_missing_test_result_fails` | A listed test with no entry in the oracle `tests` map fails. | A proof with one test missing passes (§12 FR-35 row). |
| `test_stale_digest_is_unresolved` | A digest with one changed character gives unresolved, not pass and not failure. | A stale proof passes a changed task. |
| `test_malformed_proof_is_unresolved` | A file with invalid JSON gives unresolved, and `offending` has the parse error. | A crash on a malformed file. |
| `test_no_proof_is_unresolved_exit_5` | No proof file gives exit 5. | The FR-23 behavior is lost. |
| `test_cli_default_proof_path` | `verify <dataset>/tasks/<dir>` reads `<dataset>/proofs/<dir>.json`. | The CLI looks in the wrong place. |
| `test_cli_proof_flag_overrides_default` | `--proof <file>` reads that file. | The flag is ignored. |
| `test_guard_coding_content_leak` | A merge SHA in the instruction fails `answer-present`. The clean task does not. | The coding tokens are lost from the content scan. |
| `test_guard_instruction_names_fix` | A changed path in the instruction fails. The clean instruction passes. | The instruction scan always passes. |
| `test_guard_instruction_scan_control_token` | No instruction plant gives `scan-did-not-run`. With the plant, no refusal. | A scan that reads nothing reports clean. |
| `test_guard_instruction_uniform` | A one-word change fails. The template passes. | Instruction drift is not found. |
| `test_guard_answer_placement` | A copy of the oracle in `environment/` fails. The clean task passes. | The answer in the build context is not found. |
| `test_guard_spec_coherent` | A rubric with no fail_to_pass criterion fails. The clean task passes. | A task that scores nothing is emitted. |
| `test_guard_hidden_tests_proven_rule` | A fail-to-pass test that passes at nop fails. The valid proof passes. | The rules are not applied. |
| `test_guard_hidden_tests_proven_stale` | A changed digest is unresolved. The valid proof passes. | A stale proof passes a changed task. |
| `test_guard_hidden_tests_proven_no_proof` | No proof is unresolved and never in `passed`. A valid proof passes. | The check reports passed without a proof (NFR-8). |
| `test_guard_base_does_not_build` | A build failure fails with the step. The valid proof passes. | A base that does not build is accepted. |
| `test_guard_report_covers_every_us7_check` (existing) | The registry has all nineteen ids, each test has both labels, and the report has no drift. | A guard is added without a test, or the report is not regenerated. |

## Acceptance Criteria

- [ ] A valid proof makes `hidden-tests-proven` pass.
- [ ] Each rule failure names the test and the rule. A build failure gives `base-does-not-build` with the step.
- [ ] A missing, malformed, or stale proof gives unresolved (exit 5), never pass.
- [ ] `verify` finds the proof at the default path, and `--proof` overrides it.
- [ ] `man/eval-harvest-coding.md` exists and contains the proof schema, the test id form, and the three results.
- [ ] `Verify.check_hidden_tests_proven` is public, so F-1 can call it.
- [ ] `GUARDS` and `_CANONICAL_GUARD_IDS` contain the ten coding guard ids.
- [ ] Each of the ten guard tests has a `# RED:` and a `# GREEN:` assertion, and both pass.
- [ ] `docs/features/pr-eval-harvest/guard-report.md` lists the ten coding guards and equals `render_guard_report()`.
- [ ] Each check was broken by hand one time, its guard test went red, and the change was reverted. The PR description lists them.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9).

## Out Of Scope

- The runner that writes the proof. ADR-2 puts it in a separate project.
- A `schema_version` field in the proof. §8 has none. ADR-2 says a schema change needs a version field or a coordinated change. Add it only with that change.
- New checks or changes to the behavior of the E-1 checks. If a guard test shows a defect in an E-1 check, fix it in a follow-up commit and say so in the PR.
- Counting `coding_unproven` in the dataset report: F-1.
- The rest of the coding man page (task shape, method, worthless properties): F-1.
- A separate guard report for coding. One report is the design (`guard_registry.py` docstring).
- Guards for `emit` refusals such as `environment-recipe-invalid`: C-1 and D-2 own those tests.

## Notes & Gotchas

- **Which digest to compare.** `Emit._content_digest` (`emit.py:741`) hashes the task files with `content_digest = ""` in `task.toml`. Do not compute the digest again in `verify`. Compare the proof digest with the value in `[metadata.harvest].content_digest`. `dataset.py` uses the same value (line 184). A hand edit of a task file does not change this value. This is the same trust model as `dataset`.
- **`verify` trusts the runner.** A false proof gives a false pass (ADR-2 consequences). The digest stops a stale proof, not a false one. Do not try to detect a false proof.
- **D-1 defines the test ids.** The verifier writes per-test results to `/logs/verifier/verifiers.json` with the same ids as `tests/verify/tests.toml`. Read the ids from `tests.toml`, not from the proof. Then a proof cannot remove a test by omission.
- **Exit code priority.** `VerifyReport.exit_code` (line 174) puts a failure before an unresolved check. A proof failure gives exit 4 even when `git-channel-absence` is unresolved. This is correct.
- **The emit path.** At emit, `task_dir` is a temporary directory, not under `tasks/`. The default-path rule in step 5 gives `None`. Keep this rule in the CLI, not in `verify_task`.
- **The man page has two writers.** This task creates `man/eval-harvest-coding.md` with the PROOF FILE section. F-1 adds the other sections later and must keep this one. Write the schema from the code that you merge, so F-1 can copy it without a change.
- **One check name, many guards.** `hidden-tests-proven` has three guards, and `answer-present` already has two (`content-leak`, `git-channel-leak`). This is why `guard_id` exists (`GuardSpec` docstring). Make each `guard_id` unique.
- **Unresolved is a red result too.** For the stale and no-proof guards, red means "in `report.unresolved` and not in `report.passed`". `VerifyReport.unresolved` (`verify.py:164`) gives the list. Do not assert on `report.failures` for these two.
- **Isolate each break.** For the instruction control-token guard, keep the content-scan plant on. Otherwise the red can come from the wrong scan, and the test proves nothing about the instruction scan.
- **Do not edit `guard-report.md` by hand.** Its first line says it is generated. The coverage test fails on any hand edit.
- **Run the script through `uv`.** CLAUDE.md does not permit bare `python`.

## Dependencies

**Blocked by:** [D-1](D-1-write-the-coding-task-templates-and-the-reward-stack.md), [E-1](E-1-add-the-static-coding-checks-to-verify.md)
**Blocks:** [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)
