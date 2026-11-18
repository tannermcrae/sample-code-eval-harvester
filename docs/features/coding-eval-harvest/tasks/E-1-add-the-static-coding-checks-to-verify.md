---
id: E-1
title: "[Verify] Add the static coding checks to verify"
summary: "Make verify read the datapoint type and add every coding check that reads files only."
feature: coding-eval-harvest
workstream: Verify
status: done
complexity: L
merges: [E-1, E-2, E-3]
implements: [FR-23, FR-24, FR-25, FR-26, FR-27, FR-28, FR-29, FR-30, NFR-8, NFR-9]
user_story: US-6
blocked_by: [A-2, D-1]
blocks: [D-2, E-2]
---

# E-1: [Verify] Add the static coding checks to verify

**In short:** Teach `verify` that a coding task is different from a review task. For a coding
task, `verify` checks the oracle patch and scans for leaked answers. It also checks that the
instruction does not name the fix and matches the template, that the answers are only in verifier
files, and that the test lists are not empty.

## Context

Today `Verify.verify_task` knows only the review task shape. It reads `environment/change.patch`,
`tests/oracle.json`, and `tests/rubric.md`. A coding task has none of these files. Thus `verify`
crashes with `FileNotFoundError` on a coding task, and `emit --kind fix` (D-2) cannot promote any
task.

Several defects make a coding datapoint worthless, and no current check finds them:

1. The instruction, or another file that the agent can read, contains the issue number, the PR URL, the merge SHA, or a hidden test name.
2. The instruction names the file or the function that the fix changes. Then the task tests typing, not investigation. The existing content scan cannot find this, because it suppresses tokens that exist at the base (`Verify._classify_hit`, `verify.py:562`). Every changed path and every changed symbol exists at the base.
3. The general instructions change from task to task. Then the instruction tells the agent the type of task, and the dataset is not uniform.
4. A copy of the oracle patch or of a hidden test is in `environment/`, the Docker build context. Then the agent can read the answer.
5. The task has no fail-to-pass test, no rubric criterion, or no validation story. Then the reward stack scores nothing.

This task adds the dispatch on `[metadata.harvest].datapoint_type` and one check for each defect.
It also adds `hidden-tests-proven` as an unresolved check. E-2 replaces that check with the proof
reader.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-23, FR-24, FR-25, FR-26, FR-27, FR-28, FR-29, FR-30, NFR-8  ·  **User story:** US-6

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, FR-23 to FR-30: the requirements of this task and their acceptance criteria.
- **Tech plan:** §5, "Verify (FR-23 to FR-30)": which review checks stay and which do not apply.
- **Tech plan:** §6, "The coding task directory": the file tree that the checks read.
- **Tech plan:** §7.1, last list: why the base-tree suppression hides paths and symbols.
- **Tech plan:** §7.2, "Coding emit and verify": the sequence of calls in `verify_task` for a coding task.
- **Tech plan:** §7.3, "Emit refuses an instruction that names the fix": the refusal text and the retry with `annotate`. Also "Verify cannot prove the hidden tests": the exit 5 text.
- **Tech plan:** §4, risk row "The leakage scan for symbols gives false positives": the 4-character minimum, the symbol sources, and `--override instruction-names-fix`.
- **Tech plan:** §8, "`task.toml` additions for a coding task": the `[metadata.origin]` and `[metadata.harvest]` fields that you read. Also `FixCandidateDict`: `hidden_tests` (`repo_path`, `name`, `kind`) and `rubric_criteria` (`id`, `kind`, `text`).
- **Tech plan:** §9, the `verify` row: the failure names `instruction-names-fix`, `instruction-not-uniform`, `answer-misplaced`, `spec-incoherent`.
- **ADR-1** in §11: coding is a second type on the same verbs, and `verify` has one implementation. Do not make a separate verify module.
- **ADR-2** in §11: the CLI does not run the code of the repository. `hidden-tests-proven` is unresolved in this task. Do not try to run tests.
- **ADR-4** in §11: separate verifier mode. `tests/` and `solution/` are verifier-only. Do not re-open it.
- **PRD:** `../prd.md` US-6: "Verify that the datapoint tests the correct thing", "Uniform instruction", and "Oracle only in `solution/`, reference patch only in `tests/`".

## What To Build

The work has four parts. Each part can be a separate commit, in this order. Each commit must keep
`mise run check` green.

### Part 1: Dispatch on the type, the oracle patch, and the coding tokens (FR-23, FR-24, FR-25)

1. In `src/eval_harvest/verify.py`, add `ORACLE_PATCH_RELPATH: Final = "solution/oracle.patch"` next to `CHANGE_PATCH_RELPATH` (line 56). D-2 imports this name, as `emit.py` imports `CHANGE_PATCH_RELPATH`. Thus there is one spelling of the path.
2. Add `REFERENCE_PATCH_RELPATH: Final = "tests/judge/reference.patch"` and `HIDDEN_TESTS_RELDIR: Final = "tests/verify"`.
3. Add `CODING_DATAPOINT_TYPE: Final = "coding"` to `verify.py`. A-2 writes the literal `datapoint_type = "coding"` in `Harbor._harvest_table` and adds no constant, so this is the first named constant. A-2 also adds `DATAPOINT_TYPES = ("coding", "review")` in `refusal_log.py`. Do not import it here: `verify.py` must not depend on the refusal log.
4. Add `CODING_CHECK_NAMES: Final` after `CHECK_NAMES` (line 134), with this final value:
   `("base-and-patch", "content-absence", "instruction-names-fix", "instruction-uniform", "answer-placement", "spec-coherent", "hidden-tests-proven", "git-channel-absence")`.
   Add each name in the commit that adds its check. Do not change `CHECK_NAMES`. The review report must stay byte-identical (NFR-9).
5. Add a static method `_datapoint_type(harvest) -> str`. It returns `harvest.get("datapoint_type")` when it is a string, and `"review"` when the key is absent.
6. In `verify_task` (line 216), read the type after `_read_task_config`. Then call one of two new class methods:
   - `_review_checks(...)`: the current `by_check` list (lines 243–249), moved without change.
   - `_coding_checks(...)`: the new list, in `CODING_CHECK_NAMES` order.
   A type that is not `review` and not `coding` gives one failure: check `unknown-datapoint-type`, with the value in `offending`, and `next` = "re-emit the task with a current eval-harvest". Run no other check in this case.
7. Make `_check_base_and_patch` (line 292) take the patch path as a parameter. The review branch passes `task_dir / CHANGE_PATCH_RELPATH`. The coding branch passes `task_dir / ORACLE_PATCH_RELPATH`. For a coding task, the `patch-does-not-apply` text names `oracle.patch`, and `next` is "re-run `capture --type coding` so the fix matches its base, then emit again".
8. Make `_changed_paths` (line 489) and `_build_base_tree_oracle` (line 459) take the patch path too. Today they read `CHANGE_PATCH_RELPATH`, and they crash on a coding task.
9. Add `_coding_answer_tokens(origin, task_dir) -> list[str]`. It returns, in this sequence, with duplicates removed:
   - `#<n>` for each `pr_numbers` value (reuse `_pr_number_tokens`, line 282).
   - `#<n>` for each `issue_numbers` value.
   - `pr_url` and each value of `issue_urls`, when present.
   - `merge_commit` in full, and its first 12 characters.
   - The `name` of each `[[hidden_tests]]` table in `tests/verify/tests.toml`. D-1 defines this schema: each table has `id` (`<repo_path>::<name>`), `repo_path`, `name`, and `kind`. D-2 writes the file.
10. Read `pr_url` (str) and `issue_urls` (list of str) as **optional** keys of `[metadata.origin]`. FR-25 needs them, but the §8 example has no such keys, and the `FixTaskOrigin` dataclass of A-2 has no such fields. See Notes & Gotchas.
11. In `src/eval_harvest/seal.py`, add a keyword parameter `answer_sources: tuple[str, ...] | None = None` to `Seal.scan_agent_visible` (line 405). `None` keeps the current sources (`_ANSWER_SOURCE_PREFIXES` and `_ANSWER_SOURCE_FILES`, lines 190–191). The coding branch passes `("solution/oracle.patch", "tests/judge/reference.patch", "tests/verify/")`. A value that ends with `/` is a directory prefix. Other values are files.
12. For a source that ends with `.patch`, mine only the added lines: lines that start with `+` and not with `+++`, with the `+` removed. Then apply the `_MIN_ANSWER_FRAGMENT_LEN` rule (line 195). Context lines exist at the base, and the `diff --git` headers are paths. Part 2 handles paths.
13. Make `_check_content_absence` (line 497) take the token list and the answer sources as parameters. The coding branch passes `_coding_answer_tokens(...)` and the coding sources.
14. Add `_check_hidden_tests_unresolved(datapoint) -> list[VerifyRefusal]`. It returns one `_unresolved` (line 674) refusal: check `hidden-tests-proven`, `offending` = "no valid proof: fail-to-pass and pass-to-pass need an oracle trial and a nop trial", `next` = "a runner writes proofs/<task-dir>.json; then run `eval-harvest verify <task-dir>` again". E-2 replaces this method with the proof reader.
15. Do not run `_check_findings_reference_real_lines` or `_check_rubric_coherent` for a coding task. They read `tests/oracle.json` and `tests/rubric.md`, which a coding task does not have.
16. In `src/eval_harvest/cli.py`, do not change `handle_verify` (line 1642). It already maps `report.exit_code`. E-2 adds `--proof`.
17. Create `tests/fixtures/coding_task.py` with one class `CodingTaskBuilder` and one class method `build(root: Path) -> CodingTaskFixture`. It uses `RepoBuilder.build_squash_merged_pull_request` (`tests/fixtures/repo_builder.py:232`). It writes a coding task directory by hand:
    - `solution/oracle.patch` = `git diff <base_sha> <round2_tip>` from the fixture clone, and `tests/judge/reference.patch` with the same bytes.
    - `task.toml` from `eval_harvest.tomlw.emit_document` (`tomlw.py:222`), with the §8 coding fields, plus `pr_url` and `issue_urls`. Use `round2_tip` as `merge_commit`.
    - `instruction.md` from the real template `verifier_tpl/fix/instruction.md` (D-1), with slot values. Also `environment/Dockerfile`, `tests/verify/tests.toml`, one hidden test file, `tests/validate/validation_spec.toml`, and `tests/judge/rubric.toml`, with small valid content.
    `CodingTaskFixture` is a frozen dataclass with `task_dir`, `clone`, `base_sha`, `merge_sha`. E-2 uses this fixture too. D-2 does not exist yet, so this task cannot use `FixEmit`. Do not use `RepoBuilder.build_fix_history` (B-1) here: this task does not wait for B-1, and the fixture needs only one diff.

### Part 2: The `instruction-names-fix` scan (FR-26)

18. In `src/eval_harvest/patchsplit.py` (created by A-2), add `PatchSplit.section_paths(unified_diff: str) -> tuple[str, ...]`. It returns the `b/` path of each `diff --git` section, sorted and unique. Reuse the header parsing that `PatchSplit.split` uses. Do not write a second parser.
19. In `verify.py`, add `_fix_names(task_dir) -> list[str]`. It reads `solution/oracle.patch` (`ORACLE_PATCH_RELPATH`) and returns, sorted and unique:
    - Each path from `PatchSplit.section_paths`.
    - The basename of each path, except `__init__.py`. `__init__.py` names no specific file.
    - Each symbol from step 20 with 4 or more characters.
20. Symbols come from two kinds of lines in `oracle.patch`:
    - Added and removed lines (`+` or `-`, not `+++` or `---`).
    - The text after the second `@@` of a hunk header (`@@ -85,7 +93,11 @@ class TraceExtractor:`).
    In both, apply this regex: `\b(?:def|class|function|func|fn|interface|struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)`. Take group 1. Put the regex in a `Final` constant with a comment that tells the languages it covers.
21. In `src/eval_harvest/seal.py`, add `Seal.scan_instruction(task_dir: Path, tokens: list[str], control_token: str) -> Report`. It reads only `task_dir / "instruction.md"`.
    - When `control_token` is not in the file, add `scan-did-not-run` and return. Use the same pattern as `scan_agent_visible` (line 405).
    - For each token, find the first line where the token occurs with no identifier character on either side: `(?<![A-Za-z0-9_])` + `re.escape(token)` + `(?![A-Za-z0-9_])`. Add `instruction-names:<token>` with `passed=False` and detail `'<token>' at instruction.md:<line>`. Without the boundary, the symbol `scan` matches `scanner`.
22. In `verify.py`, add `_INSTRUCTION_CONTROL_TOKEN: Final = "EVAL-HARVEST-INSTRUCTION-SCAN-CONTROL-TOKEN"`. It is different from `_CONTROL_TOKEN` (line 62). Thus a test can remove one plant and keep the other.
23. Add `_check_instruction_names_fix(task_dir, datapoint, *, plant: bool) -> list[VerifyRefusal]`:
    - Copy `instruction.md` into a temporary directory. Do not change the task (§9: `verify` writes nothing).
    - When `plant` is true, append the line `<_INSTRUCTION_CONTROL_TOKEN>\n` to the copy.
    - Call `Seal.scan_instruction` on the copy.
    - `scan-did-not-run` gives `_scan_did_not_run(datapoint)` (line 591).
    - Each hit gives `_fail("instruction-names-fix", datapoint, offending, next_)`. `offending` = `'<token>' at instruction.md:<line>`. `next_` = "describe the symptom, not the file — edit the instruction slot with `annotate` and re-run emit; if the name is a common word, re-emit with --override instruction-names-fix".
    - Report every hit, not only the first (FR-39 of pr-eval-harvest: never stop at the first).
24. Add `plant_instruction_control_token: bool = True` to `verify_task` as a keyword parameter, and pass it as `plant`. Do not reuse `plant_control_token`. E-2 must remove one plant and keep the other, to prove that each scan has its own guard.
25. Do not use the base-tree oracle in this check. Do not suppress any token.

### Part 3: The `instruction-uniform` check (FR-27)

26. Add `_FIX_TEMPLATE_DIR: Final = Path(__file__).parent / "verifier_tpl" / "fix"`. D-1 creates `verifier_tpl/fix/instruction.md` with the two slots `@TASK@` and `@TOOLING@`.
27. Add `_check_instruction_uniform(task_dir, datapoint) -> list[VerifyRefusal]`:
    - Read the template bytes and decode them as UTF-8.
    - Split the template on the two slots. You get three fixed parts. Build one regex: `re.escape(part0) + "(.*?)" + re.escape(part1) + "(.*?)" + re.escape(part2)`, with `re.DOTALL`. The slot order comes from the template, not from this code.
    - Apply `fullmatch` to `task_dir / "instruction.md"`.
    - No match gives `_fail("instruction-not-uniform", ...)`. `offending` names the first line where the instruction is different from the template, as `instruction.md:<line>: '<text>'`. Find it with `difflib.SequenceMatcher` on the lines. `next_` = "do not edit instruction.md by hand; put the task text in the `instruction` slot and the tooling note in the `tooling_note` slot with `annotate`, then re-run emit".
28. A template without exactly one `@TASK@` and one `@TOOLING@` is a defect in the package. Raise `RuntimeError`. Do not return a refusal.

### Part 4: The `answer-placement` and `spec-coherent` checks (FR-28, FR-29)

29. Add `_check_answer_placement(task_dir, datapoint) -> list[VerifyRefusal]`. Use `ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, and `HIDDEN_TESTS_RELDIR`.
30. Required files: `solution/oracle.patch` and `tests/judge/reference.patch`. A missing file gives `answer-misplaced` with `offending` = "<path> is missing".
31. Collect the answer files: `solution/oracle.patch`, `tests/judge/reference.patch`, and each file under `tests/verify/` except `tests/verify/tests.toml`. Compute the SHA-256 of each. Skip files with zero bytes.
32. Walk every file in the task, in sorted order. Fail `answer-misplaced` for each file that has the same digest as an answer file and that is in a place that is not permitted:
    - `oracle.patch` bytes: permitted at `solution/oracle.patch` and at `tests/judge/reference.patch` only.
    - Hidden test bytes: permitted under `tests/verify/` only.
    - Any answer bytes: never permitted outside `solution/` and `tests/`. This includes `environment/`, `instruction.md`, and the task root.
33. Also fail for a file named `oracle.patch` or `reference.patch` in a place that is not permitted, even when its bytes are different.
34. `offending` = "<answer path> has a copy at <path>". `next_` = "remove <path>; the answer must be only in solution/ and tests/ — re-run emit".
35. Add `_check_spec_coherent(task_dir, datapoint) -> list[VerifyRefusal]`. Parse each file with `tomllib`. A parse error is a failure with the error text.
36. `tests/validate/validation_spec.toml`: it must have at least one `[[stories]]` table that has at least one `[[stories.cases]]` table. This is the notebook 03 format.
37. `tests/judge/rubric.toml`: it must have at least one `[[criteria]]` table with `kind = "fail_to_pass"`. This is the notebook 03 format.
38. `tests/verify/tests.toml`: it must have at least one `[[hidden_tests]]` table with `kind = "fail_to_pass"`. The file of each listed test must exist at `tests/verify/<repo_path>`.
39. Each missing item is one `spec-incoherent` failure. Report all items, not only the first. `offending` names the file and the missing item. `next_` names the `annotate` record or the candidate file to change (for example "add a hidden_test record with kind fail_to_pass with `annotate`, then re-run emit").

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/verify.py` | Modify | Constants near line 56 and 134 (`ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, `HIDDEN_TESTS_RELDIR`, `CODING_DATAPOINT_TYPE`, `CODING_CHECK_NAMES`, `_INSTRUCTION_CONTROL_TOKEN`, `_FIX_TEMPLATE_DIR`). Dispatch in `verify_task` (line 216). Patch path parameter on `_check_base_and_patch` (292), `_build_base_tree_oracle` (459), `_changed_paths` (489), `_check_content_absence` (497). New `_coding_answer_tokens`, `_check_hidden_tests_unresolved`, `_fix_names`, `_check_instruction_names_fix`, `_check_instruction_uniform`, `_check_answer_placement`, `_check_spec_coherent`. |
| `src/eval_harvest/seal.py` | Modify | `answer_sources` parameter on `scan_agent_visible` (line 405). Added-line mining for `.patch` sources in `_answer_tokens` (line 458). New `scan_instruction` after `scan_agent_visible`. |
| `src/eval_harvest/patchsplit.py` | Modify | Add `section_paths`. File created by A-2. |
| `src/eval_harvest/verifier_tpl/fix/instruction.md` | Read only | Created by D-1. Do not change it in this task. |
| `tests/fixtures/coding_task.py` | Create | `CodingTaskBuilder`, `CodingTaskFixture`. Follow the shape of `_emit_reject_task` in `tests/integration/test_verify.py:77`. |
| `tests/integration/test_verify_coding.py` | Create | The tests below. Follow the style of `tests/integration/test_verify.py`. |
| `tests/integration/test_seal.py` | Modify | `scan_instruction` cases and `answer_sources` cases. |
| `tests/unit/test_patchsplit.py` | Modify | `section_paths` cases. File created by A-2. |

## Schemas & Contracts

**Before:**
```python
# verify.py:216
def verify_task(cls, task_dir, *, clone=None, materialized_seal=None, plant_control_token=True, base_oracle=None) -> VerifyReport
# One check list for every task: base-and-patch (change.patch), finding-lines-present, rubric-coherent,
# content-absence (tokens = "#<pr>"), git-channel-absence.

# seal.py:405
def scan_agent_visible(cls, task_dir: Path, tokens: list[str], control_token: str) -> Report
# Seal has check_repo and scan_agent_visible only. No instruction scan.
```

**After:**
```python
ORACLE_PATCH_RELPATH: Final = "solution/oracle.patch"
REFERENCE_PATCH_RELPATH: Final = "tests/judge/reference.patch"
HIDDEN_TESTS_RELDIR: Final = "tests/verify"
CODING_DATAPOINT_TYPE: Final = "coding"
CODING_CHECK_NAMES: Final = ("base-and-patch", "content-absence", "instruction-names-fix", "instruction-uniform",
                             "answer-placement", "spec-coherent", "hidden-tests-proven", "git-channel-absence")

# verify_task: datapoint_type absent or "review" -> the review list, unchanged.
# "coding" -> the CODING_CHECK_NAMES list. hidden-tests-proven is unresolved (E-2 resolves it).
# other    -> one failure: unknown-datapoint-type.
def verify_task(..., plant_control_token: bool = True, plant_instruction_control_token: bool = True, ...) -> VerifyReport

# seal.py
def scan_agent_visible(cls, task_dir: Path, tokens: list[str], control_token: str,
                       *, answer_sources: tuple[str, ...] | None = None) -> Report
@classmethod
def scan_instruction(cls, task_dir: Path, tokens: list[str], control_token: str) -> Report
# findings: "scan-did-not-run" | "instruction-names:<token>" (detail "'<token>' at instruction.md:<line>")

# patchsplit.py
@staticmethod
def section_paths(unified_diff: str) -> tuple[str, ...]   # sorted, unique b/ paths
```

New failure names (exit 4): `unknown-datapoint-type`, `instruction-names-fix`,
`instruction-not-uniform`, `answer-misplaced`, `spec-incoherent`. New unresolved (exit 5):
`hidden-tests-proven`.

Refusal example (exit 4):
```
check:     instruction-names-fix
datapoint: pr-338/fix
offending: 'src/strands_evals/extractors/trace_extractor.py' at instruction.md:3
next:      describe the symptom, not the file — edit the instruction slot with `annotate` and re-run emit; ...
```

`task.toml` fields read for a coding task: `[metadata.harvest].datapoint_type`, `kind`;
`[metadata.origin].base_commit`, `merge_commit`, `pr_numbers`, `issue_numbers`, `pr_url` (optional),
`issue_urls` (optional).

The spec files the checks read (notebook 03 format, confirmed in
`sample-gen-ai-evaluations-workshop/.../strands-evals-coding/tasks/strands-agents__evals__pr338-fix/`;
`tests.toml` is the D-1 schema):

```toml
# tests/validate/validation_spec.toml
instructions = """..."""
[[stories]]
id = "parameter_judge_sees_earlier_results"
description = """..."""
procedure = ["...", "..."]
[[stories.cases]]
# ...

# tests/judge/rubric.toml
[[criteria]]
id = "earlier_results_in_same_turn"
kind = "fail_to_pass"
text = "..."

# tests/verify/tests.toml
test_command = "..."
timeout_sec = 900
protected_markers = ["/test/", "..."]
[[hidden_tests]]
id = "tests/test_x.py::test_f2p_a"
repo_path = "tests/test_x.py"
name = "test_f2p_a"
kind = "fail_to_pass"
```

**Migration:** None. A task without `datapoint_type` is a review task.
**Backward compatibility:** The review report, the `--json` payload, and `CHECK_NAMES` do not change. Review tasks run none of the new checks.

## How To Verify

```bash
uv run pytest tests/integration/test_verify_coding.py tests/integration/test_verify.py tests/integration/test_guards.py tests/integration/test_seal.py tests/unit/test_patchsplit.py
mise run lint
mise run typecheck
mise run check
```

Manual checks:
1. Run `uv run pytest tests/integration/test_verify_coding.py -k exit_5 -s` and read the printed report. It must show `unresolved: hidden-tests-proven` and `unresolved: git-channel-absence`, and no failure.
2. In the fixture, put the changed file name into `instruction.md`. Run the test with `-s`. The report shows `refused: instruction-names-fix` with the line number.
3. In the fixture, copy `solution/oracle.patch` to `environment/fix.diff`. The report shows `refused: answer-misplaced` and names `environment/fix.diff`.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_coding_task_without_proof_exits_5` | A correct coding fixture with `--clone` gives no failure, and the unresolved checks are `hidden-tests-proven` and `git-channel-absence`. Exit code 5. | A crash on a coding task, or `hidden-tests-proven` in `passed` (NFR-8). |
| `test_coding_hidden_tests_never_passed` | `"hidden-tests-proven"` is not in `report.passed`. | A later change reports the check as passed without a proof. |
| `test_broken_oracle_patch_fails_patch_does_not_apply` | Change one context line of `solution/oracle.patch`. `patch-does-not-apply` fires and names `oracle.patch`. | `base-and-patch` still reads `environment/change.patch` (§12 FR-24 row). |
| `test_coding_skips_review_only_checks` | No `finding-line-absent` and no `rubric-incoherent` on a coding task without `tests/oracle.json` and `tests/rubric.md`. | A `FileNotFoundError` crash, or review checks on a coding task. |
| `test_each_coding_token_leak_fails_answer_present` | Parametrized over: `#<issue>`, PR URL, issue URL, full merge SHA, 12-character merge SHA, hidden test name. Each one, written into `instruction.md`, fails `answer-present`. | One token type is missing from the scan (§12 FR-25 row). |
| `test_oracle_added_line_in_environment_fails` | Copy one added line (12+ characters) of `oracle.patch` into `environment/Dockerfile`. `answer-present` fires. | The coding answer sources are not mined. |
| `test_oracle_context_line_is_not_mined` | A context line of `oracle.patch` in `instruction.md` does not fire. | Context lines and headers mined as answer fragments: false positives on every task. |
| `test_unknown_datapoint_type_fails` | `datapoint_type = "design"` gives one failure `unknown-datapoint-type`. | An unknown type runs the review checks and crashes. |
| `test_review_report_unchanged` | The existing review tests in `test_verify.py` pass without change. | The refactor changes review bytes (NFR-9). |
| `test_instruction_with_changed_path_fails` | The full path of a changed file in `instruction.md` fails, and `offending` names the path and the line. | The scan does not read paths. |
| `test_instruction_with_changed_basename_fails` | `code.py` alone in the instruction fails. | Only full paths are scanned, and an agent writes the basename. |
| `test_instruction_with_changed_symbol_fails` | A function name from a `+def` line of the oracle fails. | Symbols are not scanned. |
| `test_instruction_with_hunk_header_symbol_fails` | A class name that occurs only in a hunk header fails. | Hunk-header symbols are lost (§4 risk row). |
| `test_path_present_at_base_still_fails` | A changed path that exists at the base fails, with `--clone`. | The base-tree suppression hides the path (§12 FR-26 row). |
| `test_short_symbol_not_scanned` | A 3-character symbol (`add`) in the instruction does not fail. | False positives on common short words (§12 edge case). |
| `test_symbol_match_needs_word_boundary` | Symbol `scan` with the word `scanner` in the instruction does not fail. | Substring matches give false positives. |
| `test_clean_instruction_passes` | The fixture instruction passes `instruction-names-fix`. | The check fires on every task. |
| `test_instruction_scan_without_control_token_refuses` | `plant_instruction_control_token=False` gives `scan-did-not-run`. | A scan that reads nothing reports clean. |
| `test_all_instruction_hits_reported` | Two names in the instruction give two refusals. | The scan stops at the first hit. |
| `test_instruction_with_unicode_text` | An instruction with non-ASCII text and no names passes. | A decode error on Unicode text. |
| `test_section_paths_sorted_unique` | A diff with two sections for one path (rename) gives one path. | Duplicate tokens and unstable order (NFR-1). |
| `test_one_word_change_in_general_instructions_fails` | Change one word in the "General instructions" part. `instruction-not-uniform` fires and names the line. | Instructions drift and show the type of task (§12 FR-27 row). |
| `test_slot_text_does_not_affect_uniformity` | Two different `@TASK@` values both pass. | The check compares the slot text too, and fails every task. |
| `test_missing_slot_section_fails` | An instruction with the tooling part removed fails. | A partial match passes. |
| `test_oracle_copy_in_environment_fails` | A copy of `oracle.patch` at `environment/fix.diff` fails `answer-misplaced`. | The answer is in the Docker build context (§12 FR-28 row). |
| `test_hidden_test_copy_in_solution_fails` | A copy of the hidden test file under `solution/` fails. | Hidden tests outside `tests/`. |
| `test_reference_patch_same_bytes_as_oracle_passes` | The fixture has the same bytes in both patch files. It passes. | The check fails every correct task. |
| `test_empty_files_do_not_match` | An empty hidden test and an empty `environment/.keep` give no failure. | Equal digests of empty files give a false failure. |
| `test_misnamed_oracle_patch_fails` | A file `environment/oracle.patch` with different bytes fails. | Only byte copies are found. |
| `test_validation_spec_without_cases_fails` | A story with no `[[stories.cases]]` fails `spec-incoherent`. | A validation stage with nothing to run. |
| `test_rubric_without_fail_to_pass_fails` | A rubric with only `pass_to_pass` criteria fails. | A rubric that cannot score the fix. |
| `test_tests_toml_without_fail_to_pass_fails` | `tests.toml` with only `pass_to_pass` tests fails. | A task with no fail-to-pass test is emitted (§12 FR-29 row). |
| `test_listed_hidden_test_file_missing_fails` | A listed test whose file is absent fails and names the path. | A test that the verifier cannot run. |
| `test_all_spec_failures_reported` | Two missing items give two refusals. | The check stops at the first item. |

## Acceptance Criteria

- [x] `verify` on a correct coding task exits 5, with only `hidden-tests-proven` and `git-channel-absence` unresolved, and no failure.
- [x] `verify` on a coding task never puts `hidden-tests-proven` in `passed`.
- [x] A planted leak of each FR-25 token type fails `answer-present`.
- [x] An instruction that names a changed path, basename, or symbol (4+ characters) fails `instruction-names-fix`, with the file and line in `offending`. An instruction with no names passes.
- [x] Without the planted instruction control token, `instruction-names-fix` refuses `scan-did-not-run`. The check does not use the base-tree suppression.
- [x] A one-word change in the general instructions fails `instruction-not-uniform`.
- [x] A copy of `oracle.patch` in `environment/` fails `answer-misplaced`.
- [x] Each missing spec item fails `spec-incoherent`.
- [x] Each coding failure has the four fields, and its `next` names a command or a file (FR-30).
- [x] `ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, `HIDDEN_TESTS_RELDIR`, and `CODING_DATAPOINT_TYPE` are defined once, in `verify.py`.
- [x] All commands in "How To Verify" pass.
- [x] No regressions in the existing suite (NFR-9). `tests/unit/test_no_model_client.py` stays green (FR-19). `[project].dependencies` stays empty (NFR-6).

## Out Of Scope

- Reading the proof file, and the `--proof` flag: E-2.
- `GuardSpec` entries and the watched-red guard tests for these checks: E-2.
- Writing `pr_url`, `issue_urls`, `tests.toml`, `validation_spec.toml`, and `rubric.toml` into a task: D-2.
- Writing the template `verifier_tpl/fix/instruction.md`: D-1.
- Checking the content of a story or a criterion (quality): no task. The CLI makes no judgment (FR-19).
- The coding help text that tells the agent to describe the symptom: F-1.
- Recording an override in `task.toml`, and the end-to-end test of `--override instruction-names-fix` on `emit`: D-2.

## Notes & Gotchas

- **`pr_url` and `issue_urls` have no writer yet.** FR-25 scans for the PR and issue URLs. The §8 `[metadata.origin]` example and the `FixTaskOrigin` dataclass of A-2 have no fields for them. This task reads them as optional keys, so a task without them still passes. Ask the D-2 assignee in review to write `pr_url` (from the candidate) and `issue_urls` (from `issues[].url`) into `[metadata.origin]`. Until D-2 writes them, the URL part of the scan finds nothing on a real task.
- **`tests/Dockerfile` and `environment/Dockerfile` share many lines.** The verifier image repeats the seal and the setup steps (§6 task tree). Do not mine `tests/Dockerfile`, `tests/lib/`, `tests/test.sh`, or `tests/verify/tests.toml` as answer sources. If you do, each emit fails `answer-present` on the shared lines. Use only the three sources in step 11.
- **`oracle.patch` and `reference.patch` have the same bytes.** This is true in both notebook 03 tasks (`cmp` gives no difference). Mining both is not an error: it gives the same fragments. The plan text "oracle only in `solution/`" does not mean "the bytes only in `solution/`". Thus `answer-placement` permits the copy at `tests/judge/reference.patch`.
- **`tests/verify/tests.toml` is not a hidden test.** Do not put it in the answer set. It is not secret, and it has no copy elsewhere.
- **The lockfile can contain a line of the fix.** When the fix changes a dependency pin, the added line can be in `environment/<lockfile>` too. This is a real `answer-present` hit. The agent uses `--override answer-present` (handled in `Emit._run_verify_hook`, `emit.py:610`).
- **The base-tree suppression stays in the content scan.** `_classify_hit` (line 562) suppresses an `answer-absent:` token that exists at the base. The scope is the paths of `oracle.patch`. Hidden test names are in test files, which are not in that scope, so they are not suppressed. The instruction scan (Part 2) uses no suppression.
- **The control tokens are not suppressible.** Keep the `scan-did-not-run` short-circuit in `_map_content_report` (line 524) before the oracle.
- **Two control tokens.** Keep `_INSTRUCTION_CONTROL_TOKEN` different from `_CONTROL_TOKEN`. The content scan plants its token in a new file (`_CONTROL_FILENAME`, line 65), not in `instruction.md`. If the two tokens had the same text, the content scan could see the instruction plant and hide a broken content scan.
- **A symptom often names a public class.** In notebook 03, task `pr338` names `ToolParameterAccuracyEvaluator` in its instruction. Its oracle hunk headers name `TraceExtractor` and `TraceLevelInput`, so that instruction passes. But an instruction that names the enclosing class of a changed method fails. This is the accepted risk in §4. The remedy is `--override instruction-names-fix`. Do not remove hunk-header symbols to avoid it.
- **Go methods.** `func (r *T) Name(` does not match the symbol regex. This is a known gap. Write it in the constant's comment.
- **Do not change the task directory.** Scan copies. `emit` calls `verify` on a temporary directory, and `verify <task-dir>` on a promoted task must not change its `content_digest`.
- **Template line endings.** Read the template and the instruction as bytes, and decode both. Do not use text mode on Windows, because it changes `\r\n` (NFR-10).
- **The template is package data.** `_read_template` in `emit.py:767` reads review templates from `_TEMPLATE_DIR`. Use the same `Path(__file__).parent` method. The CLI never imports templates.
- **Determinism.** Walk the task files in sorted order, so the refusals have a fixed order (NFR-1).
- **`_datapoint` (line 273) already gives `pr-338/fix`** for `kind = "fix"`. Do not change it.
- Match the refusal shape of `_content_leak_refusal` (line 604): `offending` names the file and line, and `next` is runnable.
- §3 constraint: one stateless class for each file. `verify.py` has `Verify` only. Put new helpers in `Verify` as class or static methods.

## Dependencies

**Blocked by:** [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md), [D-1](D-1-write-the-coding-task-templates-and-the-reward-stack.md)
**Blocks:** [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md), [E-2](E-2-resolve-hidden-tests-proven-and-add-the-guard-tests.md)

## Completion Log

**Completed:** 2026-11-18  ·  **Branch:** `static-coding-checks-verify-e-1`  ·  **Commit:** the one `[E-1]` commit on `main` (squashed)

**What was implemented:** `verify` now reads `[metadata.harvest].datapoint_type` and runs one of two
check lists: the review list as before, or `CODING_CHECK_NAMES`. A type it does not know fails
`unknown-datapoint-type` and runs nothing else. The coding list applies `solution/oracle.patch` at the
base, scans for the FR-25 tokens with the coding answer sources (only the added lines of a patch are
mined), and adds four checks that only read files: `instruction-names-fix` (paths, basenames, 4+
character symbols, whole-word match, own control token, no base suppression), `instruction-uniform`
(a full match against the D-1 template, naming the first line that drifted), `answer-placement`
(SHA-256 copies and patch file names out of place), and `spec-coherent` (every missing item reported).
`hidden-tests-proven` is always unresolved.

**Files changed:**
- `src/eval_harvest/verify.py` — path and type constants, `CODING_CHECK_NAMES`, the dispatch (`_review_checks` / `_coding_checks`), a patch-path parameter on base-and-patch / base-tree oracle / changed paths, and type-specific tokens and sources on the content scan, plus the four new coding checks and the unresolved `hidden-tests-proven`.
- `src/eval_harvest/seal.py` — `answer_sources=` on `scan_agent_visible` (a value ending in `/` is a directory prefix), added-line mining for `.patch` sources, and the new `scan_instruction`.
- `src/eval_harvest/patchsplit.py` — `PatchSplit.section_paths` (sorted, unique; reuses `section_path`).
- `tests/fixtures/coding_task.py` — `CodingTaskBuilder` / `CodingTaskFixture`: a correct coding task from the squash-merge fixture.
- `tests/integration/test_verify_coding.py` — the 34 tests the task lists (the token-leak test has 6 parameters) plus 6 more (template defect, text after the template, missing reference patch, unparsable spec, four fields, context lines).
- `tests/integration/test_seal.py`, `tests/unit/test_patchsplit.py` — `answer_sources`, `scan_instruction`, and `section_paths` cases.

**Acceptance criteria:** each is met by a named test in `test_verify_coding.py`: exit 5 with only the two unresolved checks (`test_coding_task_without_proof_exits_5`, also through `Cli.run`), never passed (`test_coding_hidden_tests_never_passed`), each token type (`test_each_coding_token_leak_fails_answer_present`, 6 cases), path/basename/symbol/hunk-header names with file and line (`test_instruction_with_*`), the control token and no suppression (`test_instruction_scan_without_control_token_refuses`, `test_path_present_at_base_still_fails`), one-word drift (`test_one_word_change_in_general_instructions_fails`), the oracle copy in `environment/` (`test_oracle_copy_in_environment_fails`), each spec item (`test_*_spec*`, `test_all_spec_failures_reported`), and the four fields (`test_each_coding_failure_has_four_actionable_fields`). The constants are defined only in `verify.py`. `mise run check` is green (694 passed). Every new guard was broken on purpose (29 breaks) and watched go red, then restored.

**Deviations from the task:**
- The coding answer sources list each hidden test file one by one, not the `tests/verify/` prefix (step 11). That way `tests/verify/tests.toml` is not mined, which Notes & Gotchas requires. The prefix form still works in `Seal` and has its own test.
- The task's Notes say that `pr_url` and `issue_urls` have no writer. That is no longer true: A-2 put both in `FixTaskOrigin` and `Harbor._origin_table`. `verify` still reads them as optional keys. The fixture builds its `task.toml` with `Harbor.task_config_document`, so the coding fields are the real §8 shape, not a copy written by hand.
- `_check_base_and_patch`, `_build_base_tree_oracle`, and `_changed_paths` now take the patch path instead of `task_dir`.
- How To Verify's manual check `-k exit_5` matches no test: the name the task gives is `..._exits_5`. Use `-k exits_5`.

**Follow-ups noticed but not done:**
- D-2 should import `ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, and `HIDDEN_TESTS_RELDIR` from `verify.py`.
- E-2 replaces `_check_hidden_tests_unresolved` with the proof reader, and adds the `GuardSpec` entries for these checks.
- `uv run pytest` reports Python 3.14.7 in this environment, but CLAUDE.md pins 3.12. I did not look into it.
