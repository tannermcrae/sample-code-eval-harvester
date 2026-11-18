---
id: F-1
title: "[Dataset] Add the per-type dataset report and the coding help"
summary: "Count review and coding tasks apart in the dataset report, and teach the coding method in the help."
feature: coding-eval-harvest
workstream: Dataset
status: todo
complexity: L
merges: [F-1, F-2]
implements: [FR-1, FR-2, FR-3, FR-22, FR-32, FR-33, FR-34, TP-4]
user_story: US-7
blocked_by: [A-1, A-2, B-1, C-1, C-2, D-2, E-2]
blocks: [G-2]
---

# F-1: [Dataset] Add the per-type dataset report and the coding help

**In short:** The `dataset` verb must accept coding tasks and count them apart from review tasks.
It tells the SDE how many coding tasks have no proof, why datapoints were refused, and which
`harbor run` command to use for each type. Then the help and the man pages teach the coding method,
because the help is the only place where the coding agent learns it.

## Context

Today `dataset` reads every `tasks/*/task.toml` and requires the `change_risk_*` fields. A coding
task has no change-risk fields, so `dataset` refuses every coding task with `malformed-task`
(`Dataset._require_risk`, `src/eval_harvest/dataset.py:209`). This task removes that block (TP-4).
It also adds the counts for each type, the count of unproven coding tasks, and the refusal reasons
for each type. Harbor gives 0 to a reward key that a task does not write. Thus one mixed run gives
wrong means. This task writes one registry entry for each type, so the SDE can run each type alone
and get correct means (ADR-5).

The coding agent has no method in its prompt. It learns the method from `--help` and the man pages
only (PRD US-1). Today the help teaches only review datapoints. For example, the top-level
description says "Build Harbor PR-review eval datapoints" (`src/eval_harvest/cli.py:297`). Without
the coding help, an agent can confuse the two types. It can also write hidden tests that pin the
oracle's internal details, or forget pass-to-pass tests. The kill-criterion objective (G-2)
measures whether an agent with only this help can make a coding datapoint, so G-2 cannot start
before this task.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-32, FR-33 (the read side), FR-34, TP-4, FR-1, FR-2, FR-3, FR-22  ·  **User story:** US-7, US-1

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, rows FR-32, FR-33, FR-34: the dataset acceptance criteria. Rows FR-1, FR-2, FR-3, FR-22: the items that the help must teach, and their tests.
- **Tech plan:** §2.3, TP-4: why the change-risk fields must become optional for coding tasks.
- **Tech plan:** §6, the coding task directory tree: the task shape that the man page describes.
- **Tech plan:** §7.2, the diagram "Coding dataset": the read order (tasks, refusals, proofs) and the outputs.
- **Tech plan:** §8, the `registry.json` paragraph: the entry names and their fixed order. Also `refusals/<type>/<datapoint>.json`, `proofs/<task-dir>.json`, and the `candidates/fix-<n>.json` table.
- **Tech plan:** §9, the `dataset` row: the new `--json` keys `by_type` and `run_commands`. Also the flags and refusals of each coding verb. Copy the flags from the code, not from §9 (see Notes).
- **Tech plan:** §15, Definition of Done: the help must say that a coding datapoint is unproven until a runner writes a valid proof file.
- **ADR-2** in §11: the CLI reads a proof file and never runs tests. **ADR-3**: the agent writes the environment recipe. **ADR-5**: one registry entry for each type, a per-type `metric.py`, and the `fix_` reward keys. These decisions are settled. Explain them in the help. Do not re-open them.
- **PRD:** `../prd.md` §6, US-1 (lines 91–106): the task shape and the "worthless datapoint" rule. US-6 (line 188): the properties that make a datapoint worthless. US-7: "One dataset for the two datapoint types".

## What To Build

The work has two parts. Do Part 1 first, because the `dataset` help in Part 2 describes it. Each
part can be a separate commit.

### Part 1: The per-type dataset report (FR-32, FR-33, FR-34, TP-4)

1. **Read the type.** In `Dataset._read_one_task_record` (`dataset.py:172`), read `harvest.get("datapoint_type")`. A missing value is `"review"`. A value that is not `"review"` or `"coding"` is a `malformed-task` refusal. Use the same `DatasetRefusalError` shape as `_require_risk` (`dataset.py:209`).
2. **Make the risk fields review-only.** Add `datapoint_type: str` to `_TaskRecord` (`dataset.py:82`). For a coding task, do not call `_require_risk`. Set `change_risk_structural` and `change_risk_classified` to `""`, `change_risk_disagreement` to `False`, and `finding_severities` to `()`. For a review task, keep the current calls exactly. Also keep the parsed `[metadata.harvest]` table and the datapoint label on the record, for step 4.
3. **Count risk over review tasks only.** In `Dataset._report` (`dataset.py:307`), skip records whose `datapoint_type` is `"coding"` when you count risk, disagreements, and severities. Do this first, because `risk_counts[""]` raises `KeyError` for a coding record.
4. **Count unproven coding tasks.** For each coding record, call `Verify.check_hidden_tests_proven(task_dir, harvest, proof_path, datapoint)` (E-2), with `proof_path = <dataset>/proofs/<task-dir>.json`. It returns `list[VerifyRefusal]`. An empty list means proven. A coding task is unproven when the list is not empty. Thus a missing proof, a stale proof, and a proof that fails all count as unproven. Do not parse the proof file in `dataset.py`. One parser is in E-2, and a second copy can disagree.
5. **Read the refusal log.** Call `RefusalLog.read_all(dataset_dir) -> dict[str, list[RefusalRecordDict]]` from `src/eval_harvest/refusal_log.py` (A-2). It returns one key for each type in `DATAPOINT_TYPES`. A malformed refusal file raises `ValueError` that names the file. Turn it into a `DatasetRefusalError` with check `malformed-refusal`, the file in `offending`, and `next` = "delete or fix <file>, then run dataset again". Count the refusals for each type by `check`. Sort the keys, so the output is deterministic (NFR-1).
6. **Extend `DatasetReport`** (`dataset.py:101`) with `type_counts: dict[str, int]`, `coding_unproven: int`, `refusal_counts: dict[str, dict[str, int]]`, and `run_commands: dict[str, str]`. Always include both keys `"review"` and `"coding"`, also when a count is 0.
7. **Extend `DatasetReport.as_json`** (`dataset.py:117`). Keep the three existing keys with the same values. Add `by_type` and `run_commands`, with the shape in "Schemas & Contracts".
8. **Extend `DatasetReport.render_human`** (`dataset.py:127`). Keep the current lines first and in the same sequence. Then add the type counts, the line `coding datapoints without a valid proof: <n>`, the refusal reasons for each type, and one `harbor run` line for each type that has tasks. Next to the combined command, print: `the combined entry mixes reward keys; Harbor counts a missing key as 0, so use the per-type commands for means`.
9. **Write per-type registry entries.** A-1 changes `Dataset._write_registry` (`dataset.py:287`) to write a JSON list with one entry. Extend that list. Call `Harbor.local_registry_document` (`harbor.py:842`) once for each entry. The entries, in this fixed order, are:
   - `<short>`: all tasks.
   - `<short>-review`: only when review tasks exist.
   - `<short>-coding`: only when coding tasks exist.
   `<short>` is the part after the slash of the dataset name. `local_registry_document` takes an `org/name` and returns the short name. Thus pass `f"{dataset_name}-review"` and `f"{dataset_name}-coding"` as the `name`.
10. **Write the per-type `metric.py`.** In `Dataset._write_metric` (`dataset.py:300`), call `Harbor.metric_script_by_type({"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS})` (A-2). Import `REWARD_KEYS` (`emit.py:58`) and `FIX_REWARD_KEYS` (A-2 adds it next to `REWARD_KEYS`) from `emit`, as `dataset.py:45` imports `REWARD_KEYS` today. Do not call the old `Harbor.metric_script`.
11. **Build the run commands.** One command for each type that has tasks, and one for the combined entry. The form is `harbor run --registry-path <dataset>/registry.json --dataset <entry-name>` `[TODO: verify the Harbor 0.22.0 flags for a local registry entry]`. Use the dataset path as the CLI received it. Do not resolve it, so the output stays the same on macOS and Windows (NFR-10).
12. **Update the `no-datapoints` text** at `dataset.py:167`. Change `next_` to name both kinds: `--kind reject|approve|fix`.
13. **Update `Cli.handle_dataset`** (`cli.py:1700`) only if the new report needs it. The handler prints `report.as_json()` or `report.render_human()`, so you probably do not need to change it.
14. Do not change `_write_manifest` (`dataset.py:280`). `dataset.toml` already lists every task by name and digest, and a type is not a manifest field.

### Part 2: The coding help text and the coding man page (FR-1, FR-2, FR-3, FR-22)

15. **Decide the form.** argparse help does not change when a flag is present. Thus each verb has one help text for the two types. Add a second section to the epilog of each coding verb, with the heading `Coding datapoints (--type coding):` for `survey`, `capture`, and `brief`, and `Coding datapoints (--kind fix):` for `emit`. For `show`, `annotate`, `verify`, and `dataset`, use `Coding datapoints:`. Put the section after the current `Methodology:` text in `VERB_METHODOLOGY` (`cli.py:59`). Do not change the current review text.
16. **Top-level help (FR-2).** In `TOP_LEVEL_METHODOLOGY` (`cli.py:209`), add a short paragraph that names the two datapoint types. Add this table, as plain text with fixed columns:

    | Type | The agent under test receives | It must produce | What scores it |
    |------|------|------|------|
    | review | a PR change at one review round | approve or reject, plus findings | the human verdict and the named defects |
    | coding | an issue-style instruction at the base commit | a patch that fixes the issue | hidden tests, the validation spec, the task rubric, the taste judge, and patch bloat |

    Change the parser description at `cli.py:297` to "Build Harbor eval datapoints (PR-review and coding) from a repository's PR history." Change the man page `man/eval-harvest.md` (NAME section, line 5) to match.
17. **The task shape (FR-1).** Write one sentence for each item. Put the sentences in the `capture` and `emit` coding sections, and in the man page:
    - instruction style: an instruction in the style of an issue that names the symptom, not the file or the symbol.
    - base commit: the first parent of the merge commit.
    - oracle fix: the source part of the merged fix, only in `solution/`.
    - fail-to-pass: a test that fails at the base and passes with the oracle.
    - pass-to-pass: a test that passes at the base and with the oracle.
    - validation spec: `tests/validate/validation_spec.toml`, stories with cases.
    - task rubric: criteria of kind `fail_to_pass` or `pass_to_pass`.
    - taste judge: an LLM judge that compares the patch to the reference and to good practice.
    - patch bloat: a penalty for changes that the fix does not need.
18. **Worthless datapoints (FR-1).** In the `verify` coding section and the man page, list the properties that make a datapoint worthless: a fail-to-pass test that passes at the base; an instruction that names a changed path or symbol; general instructions that are not the same in all tasks; the answer in a file that the agent can read; hidden tests that pin details of the oracle.
19. **Behavioral tests (FR-3).** In the `annotate` coding section and the man page, add this sentence: "Hidden tests must test behavior through public interfaces; they must not pin implementation details of the oracle fix." Pin this exact sentence.
20. **Dependency pin and test command (FR-22).** In the `emit` coding section and the man page, tell the agent to pin dependencies to `merge_date`. Give the Python example `uv pip compile --exclude-newer <merge_date>`. Say that the recipe is one line for each setup step, and that the base image must be in `public.ecr.aws/`. Also say that `test_command` must write JUnit XML to `$JUNIT_PATH`, and that pytest needs `-o junit_family=xunit1` so that each `<testcase>` has a `file` attribute (D-1 matches hidden tests by this attribute).
21. **Unproven datapoints (§15).** In the `verify` and `dataset` coding sections, say that `hidden-tests-proven` stays unresolved (exit 5) until a runner writes `proofs/<task-dir>.json`, and that `dataset` counts these datapoints as unproven. Name the `--proof` flag. Tell the SDE to use the per-type `harbor run` commands for means.
22. **Survey pages.** In the `survey` coding section, teach `--page`, `--page-size`, `--excluded`, and the `no-harvestable-fix` refusal. Say that a page request makes no forge call and no git call.
23. **Extend `man/eval-harvest-coding.md`.** E-2 creates this file with a NAME line and a PROOF FILE section. Add the other sections, in the layout of `man/eval-harvest-dataset.md`: NAME, SYNOPSIS, DESCRIPTION, METHODOLOGY, TASK SHAPE (the task directory tree from §6), PROOF FILE (keep E-2's section), EXIT STATUS, SEE ALSO.
24. **Update the verb man pages.** For each verb in step 15, add the same coding section to `man/eval-harvest-<verb>.md`, after METHODOLOGY. Add `eval-harvest-coding(1)` to SEE ALSO in each page and in `man/eval-harvest.md` (line 70).
25. **Phrase-contract tests.** In `tests/integration/test_help_methodology.py`, add a dict `CODING_METHODOLOGY_PHRASES` next to `METHODOLOGY_PHRASES` (line 19). Add a test that checks each phrase in the `--help` of each verb and in its man page. Add a test that checks each FR-1 item in `man/eval-harvest-coding.md`. `_man_page_for` (line 80) maps a verb name to a page, so read the coding page by its path.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/dataset.py` | Modify | `_TaskRecord`, `_read_one_task_record`, `_report`, `DatasetReport`, `_write_registry`, `_write_metric`, the `no-datapoints` text. |
| `src/eval_harvest/cli.py` | Modify | `VERB_METHODOLOGY` (line 59): add coding sections. `TOP_LEVEL_METHODOLOGY` (line 209): add the type table. Parser description (line 297). Maybe `VERB_SUMMARIES` (line 44) for `survey`, `capture`, `emit`, `dataset`. Maybe `handle_dataset` (line 1700). |
| `man/eval-harvest-coding.md` | Modify | Created by E-2 with the PROOF FILE section. Add the other sections. |
| `man/eval-harvest.md` | Modify | NAME (line 5), METHODOLOGY (the type table), SEE ALSO (line 70). |
| `man/eval-harvest-{survey,capture,brief,show,annotate,emit,verify,dataset}.md` | Modify | Add the coding section and the SEE ALSO entry. |
| `tests/integration/test_dataset.py` | Modify | Add a `datapoint_type` parameter to `_write_task` (line 37). Add the coding cases. Update `test_registry_written_and_points_at_tasks` (line 138) and `test_metric_script_shipped` (line 150). |
| `tests/integration/test_harbor_loader_contract.py` | Modify | Add a case: Harbor 0.22.0 `Registry.from_path` loads a mixed-type `registry.json` and lists all three entries. |
| `tests/integration/test_help_methodology.py` | Modify | Add `CODING_METHODOLOGY_PHRASES` and the help tests in the table below. |

## Schemas & Contracts

**Before** (`DatasetReport.as_json`, `dataset.py:117`):
```json
{"risk_distribution": {"low": 3, "medium": 1, "high": 1, "disagreements": 2},
 "severity_distribution": {"low": 2, "medium": 2, "high": 3},
 "datapoints": 5}
```

**After:**
```json
{"risk_distribution": {"low": 2, "medium": 0, "high": 0, "disagreements": 0},
 "severity_distribution": {"low": 0, "medium": 1, "high": 1},
 "datapoints": 5,
 "by_type": {
   "review": {"datapoints": 2, "refusals": {"no-emittable-iteration": 1}},
   "coding": {"datapoints": 3, "unproven": 3, "refusals": {"fix-base-unrecoverable": 1}}
 },
 "run_commands": {
   "all": "harbor run --registry-path <dataset>/registry.json --dataset our-repo",
   "review": "harbor run --registry-path <dataset>/registry.json --dataset our-repo-review",
   "coding": "harbor run --registry-path <dataset>/registry.json --dataset our-repo-coding"
 }}
```
`risk_distribution` and `severity_distribution` count review tasks only. `run_commands` has a key only for a type that has tasks. `"all"` is always present.

**`registry.json` before** (after A-1, one entry):
```json
[{"name": "our-repo", "version": "1.0", "description": "", "tasks": [{"name": "...", "path": "./tasks/..."}]}]
```

**`registry.json` after** (mixed dataset):
```json
[{"name": "our-repo",        "version": "1.0", "description": "", "tasks": [/* all tasks */]},
 {"name": "our-repo-review", "version": "1.0", "description": "", "tasks": [/* review tasks */]},
 {"name": "our-repo-coding", "version": "1.0", "description": "", "tasks": [/* coding tasks */]}]
```

**Help text before** (`cli.py`, shape only):
```python
VERB_SUMMARIES: dict[str, str] = {...}          # line 44
VERB_METHODOLOGY: dict[str, str] = {            # line 59
    "survey": ("Methodology:\n" "  ...review text..."),
    ...
}
TOP_LEVEL_METHODOLOGY: str = ("Methodology:\n" "  A review datapoint is ...")   # line 209
```

**Help text after:**
```python
VERB_METHODOLOGY: dict[str, str] = {
    "survey": (
        "Methodology:\n" "  ...review text, unchanged..."
        "\n\n"
        "Coding datapoints (--type coding):\n" "  ...coding text..."
    ),
    ...
}
TOP_LEVEL_METHODOLOGY: str = ("Methodology:\n" "  ...review text, unchanged..." "\n\nDatapoint types:\n" "  ...table...")
```

**Migration:** None. A task without `datapoint_type` is a review task.
**Backward compatibility:** For a review-only dataset, the three existing `--json` keys keep the same values. The two new keys are additions. `registry.json` gets a `<short>-review` entry after the combined entry. Human output gets new lines after the current lines. The help gets more text. The existing review phrases stay, so `TestHelpMethodology` and `TestManPages` stay green.

## How To Verify

```bash
uv run pytest tests/integration/test_dataset.py
mise run test-harbor
uv run pytest tests/integration/test_help_methodology.py tests/integration/test_human_parity.py
mise run lint
mise run typecheck
mise run check
```

Manual checks:
1. Make a fixture dataset with 2 review tasks and 3 coding tasks. Run `uv run eval-harvest dataset --dataset <dir> --json`. The output has `"review": {"datapoints": 2, ...}` and `"coding": {"datapoints": 3, "unproven": 3, ...}`. `registry.json` has three entries in the order `<short>`, `<short>-review`, `<short>-coding`.
2. Run `uv run eval-harvest survey --help`, `uv run eval-harvest emit --help`, and `uv run eval-harvest --help`. Each shows the review `Methodology:` text first and the coding section after it. Lines are at most 100 characters (`MethodologyHelpFormatter`, `cli.py:231`).

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_coding_task_without_risk_fields_is_accepted` | A coding `task.toml` with no `change_risk_*` gives a report, not a refusal. | TP-4: every coding task is refused `malformed-task`. |
| `test_review_task_without_risk_fields_still_refuses` | A review task with no `change_risk_classified` still refuses `malformed-task`. | The review guard is lost when the coding branch is added. |
| `test_unknown_datapoint_type_refuses` | `datapoint_type = "codnig"` refuses `malformed-task` and names the value. | A typo counts silently as review. |
| `test_counts_by_type` | 2 review + 3 coding tasks give `review: 2, coding: 3, coding_unproven: 3` (the FR-32 case). | The type is read from the wrong table, or a missing type is not `review`. |
| `test_risk_counts_skip_coding_tasks` | The risk distribution of the mixed dataset is equal to that of the 2 review tasks alone. | A coding task is counted in a risk bucket, or `KeyError` on `""`. |
| `test_valid_proof_is_not_unproven` | A coding task with a valid proof fixture (`CodingTaskBuilder.write_valid_proof`, E-2) is not counted as unproven. | `unproven` is always equal to `coding`. |
| `test_stale_proof_is_unproven` | A proof whose `content_digest` is different counts as unproven. | A stale proof hides an unproven task. |
| `test_refusal_reasons_by_type` | One coding refusal file and one review refusal file give one reason under each type (FR-33). | Refusals of one type are reported under the other type. |
| `test_malformed_refusal_file_refuses` | A refusal file that is not a JSON object gives `malformed-refusal` and names the file. | A crash, or a silent skip that hides a refusal. |
| `test_registry_lists_entry_for_each_type` | The mixed dataset gives three entries in the fixed order. Each type entry lists only its tasks. | A coding task is in the review entry. The order changes between runs. |
| `test_registry_omits_empty_type` | A review-only dataset has no `<short>-coding` entry. | An empty entry that `harbor run` cannot use. |
| `test_metric_gives_per_type_means` | Run the written `metric.py` on a JSONL with review rows and coding rows. Each `fix_` mean is over the coding rows only (FR-34). | The coding means are diluted by the review rows. |
| `test_run_commands_one_per_type` | `run_commands` has `all`, plus one key for each type that has tasks. | The SDE gets only the combined command, and the means are wrong. |
| `test_dataset_deterministic` (existing, line 193) | Extend it to a mixed dataset with refusal files and proof files. | Unsorted dict keys or file order in the report. |
| Harbor loader: `test_real_harbor_loads_a_mixed_registry` | Harbor 0.22.0 `Registry.from_path` loads the mixed file and lists the three names. | A per-type entry that Harbor refuses. |
| `test_each_coding_verb_help_has_coding_section` | Each coding verb's `--help` has its coding heading and each phrase in `CODING_METHODOLOGY_PHRASES`. | A later trim removes the coding method, and the kill-criterion run fails. |
| `test_coding_man_pages_carry_coding_phrases` | Each verb man page has the same coding phrases. | The help and the man page drift apart. |
| `test_coding_man_page_teaches_task_shape` | `man/eval-harvest-coding.md` has a sentence for each FR-1 item and each worthless property, and still has the PROOF FILE section. | One item of the task shape is lost (for example pass-to-pass), or E-2's section is overwritten. |
| `test_top_level_help_has_type_table` | The top-level help has the rows `review` and `coding` with their three columns (FR-2). | The agent confuses the two types. |
| `test_help_requires_behavioral_tests` | `annotate --help` and the coding man page have the FR-3 sentence. | Hidden tests that pin oracle details are not discouraged. |
| `test_help_gives_exclude_newer_example` | `emit --help` has `uv pip compile --exclude-newer <merge_date>` (FR-22). | Old bases do not build because dependencies are not pinned. |
| `test_help_says_coding_is_unproven_without_proof` | `verify --help` and `dataset --help` say that `hidden-tests-proven` is unresolved without a proof file. | The SDE trusts an unproven datapoint. |
| `test_coding_flags_in_help_exist_in_parser` | Each coding flag that the coding text names (`--type`, `--kind fix`, `--page`, `--page-size`, `--excluded`, `--proof`, `--path`) is in that verb's parser. | The help teaches a flag that does not exist. |

## Acceptance Criteria

- [ ] A dataset with 2 review tasks and 3 coding tasks (no proof files) reports `review: 2, coding: 3, coding_unproven: 3`.
- [ ] A coding task with no `change_risk_*` fields does not cause a refusal. A review task without them still causes `malformed-task`.
- [ ] After one coding refusal and one review refusal, the report lists one reason under each type.
- [ ] `registry.json` has the entries `<short>`, `<short>-review`, `<short>-coding` in that order, and Harbor 0.22.0 loads them all.
- [ ] The written `metric.py` calculates each mean over the tasks of its own type only.
- [ ] The report prints one `harbor run` command for each type, and the dilution warning next to the combined command.
- [ ] For a review-only dataset, the existing `--json` keys have the same values as before.
- [ ] `--help` for `survey`, `capture`, `brief`, `show`, `annotate`, `emit`, `verify`, and `dataset` has a coding section after the review methodology.
- [ ] The top-level help and `man/eval-harvest.md` have the table of the two types.
- [ ] `man/eval-harvest-coding.md` has a sentence for each FR-1 item and each worthless property, and keeps the PROOF FILE section from E-2.
- [ ] The FR-3 sentence and the FR-22 `uv` example are in the help and in the man pages.
- [ ] The help says that a coding datapoint is unproven until a runner writes a valid proof file.
- [ ] Each existing review phrase test passes without change.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6).

## Out Of Scope

- Writing refusal files: A-2 and the verbs that call it.
- The proof-file parser and its rules: E-2. This task only calls `Verify.check_hidden_tests_proven`.
- `Harbor.metric_script_by_type` and `FIX_REWARD_KEYS`: A-2. This task only calls them.
- The change from one JSON object to a JSON list in `registry.json`: A-1.
- The PROOF FILE section of `man/eval-harvest-coding.md`: E-2.
- Any change to verb behavior or flags in Part 2. Part 2 writes text only. The flags come from B-1, C-1, C-2, D-2, E-2, and Part 1.
- Changing `Emit._ship_metric_script` (`emit.py:638`). See the gotcha below.
- The eval objective that uses this help: G-2.
- A rewrite of the review methodology. Keep each existing sentence as it is.

## Notes & Gotchas

- **The help describes behavior that already exists.** The verbs that the help describes (`survey`, `capture`, `brief`, `show`, `annotate`, `emit`, `verify`, `dataset`) all get their coding behavior in B-1, C-1, C-2, D-2, E-2, and Part 1. This task waits for all of them, so the phrase tests pin text for behavior that exists.
- **`emit` also writes `metric.py`.** `Emit._ship_metric_script` (`emit.py:638`, called at `emit.py:557`) writes the review-only script with `REWARD_KEYS` after each emit. Thus after a review `emit`, the dataset `metric.py` is review-only until `dataset` runs again. Do not change `emit.py` in this task. `test_metric_script_shipped` (line 150) says the two files are "identical". Change that test to compare with the per-type bytes, and update its docstring to say that `dataset` owns the final `metric.py`. D-2 must not add a third writer of this file.
- **`KeyError` trap.** `_report` indexes `risk_counts[record.change_risk_classified]`. A coding record has `""`. Skip coding records before you index.
- **`dataset.py` cannot import `cli.py`.** `cli.py` imports this module (see the comment at `dataset.py:49`). The import of `verify.py` and `refusal_log.py` is safe: `verify.py` imports only `diffspan`, `gitcmd`, and `seal` (`verify.py:40-42`).
- **The registry short name.** `local_registry_document` refuses a name that is not `org/name` and returns the part after the slash (`harbor.py:862-868`). Pass `org/name-review`, not `name-review`.
- **Deterministic output (NFR-1).** Sort the refusal check names. Build the per-type task lists from the records, which `_read_task_records` already sorts by directory (`dataset.py:160`).
- **Mixed origin repos.** `_dataset_name` (`dataset.py:258`) refuses a dataset from two repositories. Coding tasks carry `[metadata.origin].repo` too (§8), so this rule applies to them without change.
- **The coding man page has two writers.** E-2 created `man/eval-harvest-coding.md` with the PROOF FILE section. Add your sections around it. Do not overwrite or reword that section. It describes the code that E-2 merged, and runner authors read it.
- **Copy the flags from the parser, not from the tech plan.** §9 lists the planned flags. The merged code can be different. Run each verb's `--help` and copy the real flag names.
- **Help bytes are a contract.** `test_human_parity.py` checks that the shell and the agent get the same help bytes. `MethodologyHelpFormatter` (`cli.py:231`) fixes the width at 100 and does not re-wrap the text. Thus you must wrap the lines by hand, at most 100 characters, with two spaces of indent, as in the existing text.
- **Phrase matching ignores case** (`test_each_verb_help_has_methodology`, line 114). Pin a phrase that is unique to the idea, not a common word.
- **The comments in `cli.py` (line 40) and in `test_help_methodology.py` say "kept in sync with man/".** Each phrase must be in both places, or `test_man_pages_carry_methodology` fails.
- §3 constraint: one stateless class for each file, and frozen dataclasses (not Pydantic) on the CLI path.

## Dependencies

**Blocked by:** [A-1](A-1-fix-the-two-existing-survey-and-registry-defects.md), [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md), [B-1](B-1-add-the-paged-coding-survey.md), [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md), [C-2](C-2-add-coding-views-to-brief-show-and-annotate.md), [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md), [E-2](E-2-resolve-hidden-tests-proven-and-add-the-guard-tests.md)
**Blocks:** [G-2](G-2-add-the-coding-kill-criterion-objective.md)
