---
id: C-2
title: "[Capture] Add coding views to brief, show, and annotate"
summary: "Show the agent a short coding brief, the fix of one file, and let it fill coding slots with small records."
feature: coding-eval-harvest
workstream: Capture
status: todo
complexity: M
merges: [C-3, C-4]
implements: [FR-15, FR-16, FR-17, FR-18, FR-19, NFR-2, NFR-3]
user_story: US-3
blocked_by: [C-1]
blocks: [F-1]
---

# C-2: [Capture] Add coding views to brief, show, and annotate

**In short:** `brief fix-<n>.json` prints everything the agent needs to fill a coding candidate, but
no patch text. `show fix-<n>.json --path <file>` prints the fix for one file only. The agent sends
small JSON records to `annotate` to fill the slots, and the CLI refuses a record for a slot that
does not exist.

## Context

The agent must not read whole patches. Many patches in one context are the problem that the north
star removes. The agent also must not rewrite the whole candidate file for each change, because it
then reads and writes kilobytes again after each `emit` refusal. The review path already solves
both problems with `brief`, `show` (pr-eval-harvest ADR-6), and `annotate` (pr-eval-harvest tech
plan §5.3). This task gives the same three verbs for a coding candidate. Without it, the agent
opens `source.patch` and `tests.patch` directly, and it edits `fix-<n>.json` by hand. Then the
context for each datapoint grows with the size of the fix, a typo in a slot name is not found, and
the audit record of US-4 is not reliable.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-15, FR-18 (with the FR-16 and FR-17 slots from C-1), FR-19 (no judgment), NFR-2 (all three verbs are offline), NFR-3 (brief size)  ·  **User story:** US-3, US-4

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, FR-15: `brief` has no `@@` hunk header, and `show --path` gives only the hunks of that path.
- **Tech plan:** §2.1, FR-18: "`annotate` merges records for the coding slots. It does not infer a value. A record for an unknown slot is refused."
- **Tech plan:** §9, the `brief`, `show`, and `annotate` rows: the type comes from the file, `--path` is new, the four record shapes, and the refusals `unknown-path` (3) and `unknown-slot` (3).
- **Tech plan:** §8, the `candidates/fix-<n>.json` table: the fields that the brief prints, the slot fields, and the sort rule for `hidden_tests`.
- **Tech plan:** §2.2, NFR-3: the brief size target (straw man 12 KiB, `[TODO]` in the plan).
- **Tech plan:** §7.3, "Emit refuses an instruction that names the fix" and "The proof shows that a fail-to-pass test passes at the base": the agent corrects one slot with `annotate` and emits again.
- **ADR-1** in §11: the shared views read the type and keep one implementation for each verb.
- **PRD:** `../prd.md` §6, US-3 ("one compact bundle") and US-4 ("The CLI makes no judgment of its own.").

## What To Build

The work has three parts. Each part can be a separate commit, in this order. Each commit must pass
`mise run check`.

### Part 1: `brief` for a coding candidate

1. **Dispatch on the type in `handle_brief`** (`cli.py:1093`). After the "is not a file" usage guard, add: `if FixCandidate.datapoint_type_of(candidate_path) == "coding": return cls._handle_brief_coding(arguments)`. Do not add a `--type` flag to `brief`. The type comes from the file (§9). The review path below does not change.
2. **Add `FIX_FILL_CONTRACT_GUIDE: Final` to `fix_candidate.py`** (C-1 created the file). It is a tuple of lines, one for each slot, in the same style as `FILL_CONTRACT_GUIDE` (`candidate.py:280`):
   - `instruction` — the symptom and the expected behavior; no file paths, function names, or cause
   - `instruction_rationale` — why this instruction gives enough information and does not give the answer
   - `tooling_note` — one line, at most 200 characters, how to run the tests in this repository
   - `hidden_tests[]` — each with `repo_path`, `name`, `kind` (`fail_to_pass` or `pass_to_pass`), `origin` (`pr` or `agent`), and `evidence`; put each file in `candidates/fix-<n>/hidden/<repo_path>`
   - `rubric_criteria[]` — each with `id`, `kind`, and `text`
   - `environment` — `base_image` (from `public.ecr.aws/`), `setup_steps` (single lines), `lockfile` (a file in `candidates/fix-<n>/`), `test_command` (writes JUnit XML to `$JUNIT_PATH`), and `rationale`; pin dependencies to `merge_date`
   F-1 writes the full method text. Keep each line here short.
3. **Add the coding rendering to `brief.py`.** Keep one class `Brief`. Add `Brief.render_fix(candidate: FixCandidateDict, *, candidate_display_path: str) -> str` and `Brief.fix_as_json(candidate, *, candidate_display_path) -> dict[str, Any]`. The human form has these sections, in this order, built with `_simple_section` (`brief.py:246`):
   1. Identity: `fix-<n> · <repo> · <pr_url>`, then `base <base_commit[:12]> (<base_commit_date>) · merge <merge_commit[:12]> · merge_date <merge_date>`.
   2. `pr`: the title, then the body. When `pr_body_truncated` is true, add the line `(body cut at 4000 characters)`.
   3. `issues`: for each issue, `#<number> <title> · <url>`, then the body, with the same cut line. `(none)` when the list is empty.
   4. `size`: `source: <len(source_paths)> file(s), +<source_lines_added> -<source_lines_deleted> · tests: <len(test_paths)> file(s) · author_commits: <n>`.
   5. `source paths` and `test paths`: one path for each line.
   6. `you fill`: `FIX_FILL_CONTRACT_GUIDE`, then one line for each entry of `FixCandidate.validate_filled(candidate)`, so the agent sees what is still empty.
   7. `next`: `eval-harvest show <path> --path <source_paths[0]>`, `eval-harvest annotate <path> --stdin`, `eval-harvest emit <path> --kind fix`.
   The JSON form has the same facts under these keys: `datapoint_type`, `repo`, `pr_number`, `pr_url`, `base_commit`, `base_commit_date`, `merge_commit`, `merge_date`, `pr`, `issues`, `size`, `source_paths`, `test_paths`, `fill_contract`, `unfilled`, `next_commands`.
4. **The brief prints no patch bytes.** It does not read `source.patch` or `tests.patch`. It is pure rendering: data in, string out, as the module docstring of `brief.py` says.
5. **Add `Cli._handle_brief_coding(arguments) -> int`.** Load with `FixCandidate.load`. If `FixCandidate.validate_facts` returns violations, refuse `malformed-candidate` (exit 3). `offending` lists the violations. `next_ = "capture the PR again with `capture <n> --type coding`"`. Otherwise print `render_fix` or `fix_as_json` and exit 0. `--body-chars` does not apply to coding, because capture already cut each body to 4000 characters.

### Part 2: `show --path` for a coding candidate

6. **Change the `show` arguments** in `Cli.add_show_arguments` (`cli.py:473`):
   - `--comment`: change `required=True` to `default=None`.
   - Add `--path` (`type=str`, `default=None`, `metavar="<file>"`, help: "a coding candidate: print the fix for this one repository path").
   - Update the docstring to the new usage line.
7. **Dispatch on the type in `handle_show`** (`cli.py:1164`). After the "is not a file" guard:
   - Coding candidate: `--path` is required and `--comment` and `--iteration` are not allowed. A wrong combination is a usage error (exit 2) with one line that names the correct flag. Then call `cls._handle_show_coding(arguments)`.
   - Review candidate: `--comment` is required and `--path` is not allowed. A wrong combination is a usage error (exit 2). The review path below is otherwise the same.
8. **Add the file-section helper to `patchsplit.py`** (A-2 created it). Add `PatchSplit.file_sections(unified_diff: str) -> list[tuple[str, str]]`. It returns `(b_path, section_text)` for each `diff --git` section, in order, with the same cut rule and path rule that `split` uses. Change `split` to call it, so the two cannot disagree. The `split` tests of A-2 must stay green without change.
9. **Add `Show.fix_sections_for_path(source_diff: str, test_diff: str, path: str) -> list[str]`** to `show.py`. It returns the section text of each section whose path equals `path`, first from the source patch and then from the test patch. Print the whole section: the `diff --git` header, the `---`/`+++` lines, and every hunk. Do not use `Show._hunk_blocks`. It keys on `+++ b/` and drops the hunks of a deleted file, because a deleted file has `+++ /dev/null`.
10. **Add `Cli._handle_show_coding(arguments) -> int`.** Read both patches from `dataset_dir / candidate["source_patch_path"]` and `dataset_dir / candidate["test_patch_path"]`. Use `_dataset_dir_for_candidate` (`cli.py:1552`). Decode with `errors="replace"`, as the review path does (`cli.py:1205`).
    - A patch file that is missing refuses `missing-patch` (exit 3), with the same `next_` as the review path.
    - A path that is in neither `source_paths` nor `test_paths` refuses `unknown-path` (exit 3). `offending = f"{path} is not changed by this fix"`. `next_` lists the paths. If there are more than 20 paths, list the first 20 and the total count.
    - Human output: a header `<path> · source` or `<path> · tests`, then the section text. `--json`: `{"path": …, "part": "source"|"tests", "sections": [...]}`.
11. Do not change `Brief.render`, `Brief.as_json`, `Show.hunks_for_location`, or `Show.render`.

### Part 3: `annotate` records for a coding candidate

12. **Dispatch on the type in `handle_annotate`** (`cli.py:1277`). After the existing usage guards and the record parse (`_parse_annotate_input`), add: `if FixCandidate.datapoint_type_of(candidate_path) == "coding": return cls._handle_annotate_coding(arguments, records)`. The parse stays shared, so `--stdin` (JSONL) and `--from-json` (JSON array) work the same for the two types. The review path below does not change.
13. **Add the coding merge to `annotate.py`.** Keep one class `Annotate`. Add `Annotate.apply_fix_records(candidate: FixCandidateDict, records, *, replace_hidden_tests: bool = False) -> tuple[FixCandidateDict, list[Violation]]`. It is pure, as `apply_records` (`annotate.py:114`) is: deep-copy the candidate, merge, return the copy and the violations. No filesystem access.
14. **The four record shapes.** Select the shape by its discriminator key. Add the discriminator constants next to `_COMMENT_KEY` (`annotate.py:60`). A record with none of them is a violation `annotate.unrecognised-record`, never a skip:
    - **Text slot:** `{"slot": "<name>", "value": "<text>"}`. `<name>` is one of `instruction`, `instruction_rationale`, `tooling_note`. Set the field to `str(value)`. Any other `<name>` gives `annotate.unknown-slot`. A record without `value` gives `annotate.slot-missing-field`. Do not default it to `""`.
    - **Hidden test:** `{"hidden_test": {"repo_path", "name", "kind", "origin", "evidence"}}`. All five keys are required. A missing key gives `annotate.slot-missing-field` and names the key. The key of an entry is `(repo_path, name)`. A record with an existing key replaces that entry. A record with a new key adds an entry. After the merge, sort `hidden_tests` by `(repo_path, name)` (tech plan §8).
    - **Rubric criterion:** `{"rubric_criterion": {"id", "kind", "text"}}`. All three keys are required. The key is `id`. A record with an existing `id` replaces that entry in place. A new `id` is added at the end.
    - **Environment:** `{"environment": {"base_image", "setup_steps", "lockfile", "test_command", "rationale"}}`. All five keys are required. `setup_steps` must be a JSON list of strings. The record replaces the whole `environment` object. Build the new dict in the §8 field order.
    A value that is not a JSON object where an object is required gives `annotate.unrecognised-record`.
15. **Do not check the values here.** `kind` and `origin` enums, the `tooling_note` length, the evidence, and the recipe form belong to `FixCandidate.validate_filled` and `FixCandidate.validate_recipe` (C-1). This is the same split as the review path: `annotate` sees only record shape problems, and the validators own the value rules (`apply_records` docstring, `annotate.py:117`). One copy of each rule.
16. **Add `--replace-hidden-tests`** to `Cli.add_annotate_arguments` (`cli.py:509`). Help: "a coding candidate: clear hidden_tests[] before applying, to restate the hidden test list". On a review candidate this flag is a usage error (exit 2). `--replace-findings` on a coding candidate is also a usage error (exit 2).
17. **Add `Cli._handle_annotate_coding(arguments, records) -> int`:**
    1. Load with `FixCandidate.load`. Merge with `Annotate.apply_fix_records`.
    2. `--check`: compute `violations = apply_violations + FixCandidate.validate_filled(merged) + FixCandidate.validate_hidden_test_files(merged, dataset_dir) + FixCandidate.validate_recipe(merged, dataset_dir)`. Use `_dataset_dir_for_candidate` (`cli.py:1552`) for `dataset_dir`. Print the remaining slots and the violations. Write nothing. Exit 0 when the list is empty, else exit 3. Follow `_report_annotate_check` (`cli.py:1389`). Then a driver can loop until the candidate is ready for `emit`.
    3. Write mode: `fatal = apply_violations + [v for v in FixCandidate.validate_filled(merged) if v.code not in ("fix.slot-empty", "fix.no-fail-to-pass-test")]`. The two excluded codes mean "not filled yet". They are normal while the agent fills the candidate one record at a time. Every other code is a wrong value.
    4. If `fatal` is not empty, call `_refuse_annotate` (`cli.py:1341`). Write nothing.
    5. Else write with `FixCandidate.dump(merged, candidate_path)` and print the report (step 18). Exit 0.
18. **Add the coding report.** Add a frozen dataclass `FixAnnotateStats` to `annotate.py` with `slots_set: list[str]`, `hidden_tests_added: int`, `hidden_tests_replaced: list[str]`, `rubric_criteria_added: int`, `rubric_criteria_replaced: list[str]`, and `environment_set: bool`. Add `Annotate.summarize_fix(original, records) -> FixAnnotateStats`. A "replaced" entry is one whose key existed before this run. Report it, so an overwrite is never silent (the same reason as `overwritten` in `summarize`, `annotate.py:229`).
    - Human: the candidate path, one summary line (for example `set instruction, tooling_note · hidden tests: 2 added, 1 replaced (tests/test_x.py::test_a) · rubric: 1 added · environment set`), then `remaining slots:` from the `fix.slot-empty` pointers of `validate_filled`.
    - `--json`: `{"candidate", "slots_set", "hidden_tests_added", "hidden_tests_replaced", "rubric_criteria_added", "rubric_criteria_replaced", "environment_set", "remaining_slots", "ok": true}`.
19. Do not change `Annotate.apply_records`, `summarize`, `remaining_slots`, or `AnnotateStats`.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/brief.py` | Modify | Part 1. Add `render_fix`, `fix_as_json`, and their private helpers. |
| `src/eval_harvest/fix_candidate.py` | Modify | Part 1. Add `FIX_FILL_CONTRACT_GUIDE`. File created by C-1. |
| `src/eval_harvest/show.py` | Modify | Part 2. Add `fix_sections_for_path`, `render_fix`, `fix_as_json`. |
| `src/eval_harvest/patchsplit.py` | Modify | Part 2. Add `PatchSplit.file_sections`. `split` calls it. File created by A-2. |
| `src/eval_harvest/annotate.py` | Modify | Part 3. Add `apply_fix_records`, its private `_apply_fix_*` helpers, the discriminator constants, `FixAnnotateStats`, and `summarize_fix`. |
| `src/eval_harvest/cli.py` | Modify | `handle_brief` (line 1093), `add_show_arguments` (line 473), `handle_show` (line 1164), `add_annotate_arguments` (line 509), `handle_annotate` (line 1277). New `_handle_brief_coding`, `_handle_show_coding`, `_handle_annotate_coding`, `_report_annotate_coding`, `_report_annotate_coding_check`. |
| `tests/integration/test_brief_coding.py` | Create | Part 1. Mirror `tests/integration/test_brief.py`. |
| `tests/integration/test_show_coding.py` | Create | Part 2. Mirror `tests/integration/test_show.py`. |
| `tests/unit/test_patchsplit.py` | Modify | Part 2. Add the `file_sections` cases. |
| `tests/integration/test_annotate_coding.py` | Create | Part 3. Mirror `tests/integration/test_annotate.py`. Use a candidate written by `FixCandidate.write_to_dataset` on `RepoBuilder.build_fix_history` (B-1, extended by C-1). |

## Schemas & Contracts

**Before:**
```
brief <candidate> [--dataset <dir>] [--body-chars <n>]
show <candidate> --comment <id> [--iteration <n>] [--context <lines>] [--dataset <dir>]
```

The record kinds for a review candidate (`annotate.py:60-63`):
```jsonl
{"comment": 17, "classification": "defect", "rationale": "…"}
{"finding": {"comment_ids": [17], "statement": "…", "severity": "high", "severity_evidence": "…", "severity_rationale": "…", "reference_only": false}}
{"risk_classified": "medium", "rationale": "…"}
{"rubric_version": "v3"}
```

**After:**
```
brief <candidate> [--dataset <dir>] [--body-chars <n>]          # type from the file; --body-chars only for review
show <candidate> (--comment <id> [--iteration <n>] [--context <lines>] | --path <file>) [--dataset <dir>]
annotate <candidate> (--stdin | --from-json <file>) [--check] [--replace-findings | --replace-hidden-tests]
```

`brief --json` for a coding candidate:
```json
{"datapoint_type": "coding", "repo": "…", "pr_number": 338, "pr_url": "…",
 "base_commit": "…", "base_commit_date": "2026-07-29", "merge_commit": "…", "merge_date": "2026-07-31",
 "pr": {"title": "…", "body": "…", "body_truncated": false},
 "issues": [{"number": 337, "url": "…", "title": "…", "body": "…", "body_truncated": false}],
 "size": {"source_files": 1, "source_lines_added": 12, "source_lines_deleted": 3, "test_files": 1, "author_commits": 41},
 "source_paths": ["…"], "test_paths": ["…"],
 "fill_contract": ["…"], "unfilled": ["instruction", "…"], "next_commands": ["…"]}
```

`show --path --json` for a coding candidate:
```json
{"path": "src/calc.py", "part": "source", "sections": ["diff --git a/src/calc.py b/src/calc.py\n…"]}
```

The review records do not change. A coding candidate accepts only these records:
```jsonl
{"slot": "instruction", "value": "Calling extract() on a trace with two tool calls in the same turn drops the second call. …"}
{"slot": "instruction_rationale", "value": "…"}
{"slot": "tooling_note", "value": "Run the tests with `uv run pytest`."}
{"hidden_test": {"repo_path": "tests/test_x.py", "name": "test_f2p_same_turn", "kind": "fail_to_pass", "origin": "pr", "evidence": "fails at base with AssertionError: 1 != 2"}}
{"rubric_criterion": {"id": "R1", "kind": "fail_to_pass", "text": "Both tool calls of one turn are extracted."}}
{"environment": {"base_image": "public.ecr.aws/docker/library/python:3.12-slim", "setup_steps": ["pip install uv", "uv pip install --system -r requirements.lock"], "lockfile": "requirements.lock", "test_command": "uv run pytest --junitxml=$JUNIT_PATH", "rationale": "…"}}
```

A review record (for example `{"comment": 17, …}`) on a coding candidate gives `annotate.unrecognised-record`. A coding record on a review candidate gives the existing `annotate.unrecognised-record`.

**Migration:** None.
**Backward compatibility:** Every existing `brief`, `show --comment`, and `annotate` command on a review candidate gives the same bytes and the same exit code. `show` without `--comment` on a review candidate was an argparse error (exit 2), and it stays exit 2.

## How To Verify

```bash
uv run pytest tests/integration/test_brief_coding.py tests/integration/test_show_coding.py tests/integration/test_annotate_coding.py tests/unit/test_patchsplit.py
uv run pytest tests/integration/test_brief.py tests/integration/test_show.py tests/integration/test_annotate.py
mise run lint
mise run typecheck
mise run check
```

Manual checks on a captured fixture:
1. Run `eval-harvest brief <ds>/candidates/fix-<n>.json | grep -c '^@@'`. The count must be 0.
2. Run `eval-harvest show <ds>/candidates/fix-<n>.json --path <a source path>`. The output must contain only that path in its `diff --git` lines.
3. Pipe the six records above into `eval-harvest annotate <ds>/candidates/fix-<n>.json --stdin`. Then run `annotate … --check`. It must list only the files that are still missing (the hidden test file and the lockfile). Add those files, and `--check` must exit 0.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_brief_coding_has_no_hunk_header` | The human and the JSON brief contain no line that starts with `@@` and no `diff --git`. | Patch bytes leak into the brief (FR-15). |
| `test_brief_coding_shows_issue_text` | The brief has the title and the body of each linked issue. | The agent must call the forge to read the issue. |
| `test_brief_coding_marks_truncated_body` | A candidate with `pr_body_truncated = true` prints `(body cut at 4000 characters)`. | The agent thinks that a cut body is the full text. |
| `test_brief_coding_lists_unfilled_slots` | A fresh candidate lists every slot. After one slot is filled, that slot is not listed. | The brief shows a stale fill state. |
| `test_brief_coding_next_commands_use_real_path` | `next` names the real candidate path and `--kind fix`. | A placeholder that the agent must guess. |
| `test_brief_coding_size_bound` | A candidate with a 4000-character PR body and one 4000-character issue body gives a human brief below the NFR-3 straw man (12 KiB). | A section without a limit fills the context (NFR-3). |
| `test_brief_coding_refuses_malformed_candidate` | A file with `datapoint_type = "coding"` and no `merge_commit` refuses `malformed-candidate` (exit 3). | A crash with `KeyError`. |
| `test_brief_review_unchanged` | The existing `tests/integration/test_brief.py` passes without change. | The dispatch changes the review path (NFR-9). |
| `test_show_path_prints_only_that_file` | For a fix with three files, `--path src/a.py` prints one section, and its `diff --git` line names `src/a.py`. | Other files leak into the output (FR-15). |
| `test_show_path_finds_test_file` | `--path tests/test_a.py` prints the section from `tests.patch` with the label `tests`. | The view reads only the source patch. |
| `test_show_path_deleted_file` | A fix that deletes `src/old.py` prints its section for `--path src/old.py`. | The `+++ b/` rule drops the hunks of a deleted file. |
| `test_show_path_unknown_path` | `--path nope.py` refuses `unknown-path` (exit 3) and lists the changed paths. | An empty output with exit 0. |
| `test_show_coding_requires_path` | A coding candidate with `--comment 1` and no `--path` exits 2. | The review path runs on a coding file and crashes. |
| `test_show_review_rejects_path` | A review candidate with `--path x` exits 2. | `--path` is ignored without a message. |
| `test_show_review_without_comment_exits_2` | A review candidate with neither `--comment` nor `--path` exits 2. | `--comment` is optional now, and the review path crashes without it. |
| `test_show_coding_missing_patch` | A deleted `source.patch` refuses `missing-patch` (exit 3). | A crash with `FileNotFoundError`. |
| `test_file_sections_agrees_with_split` | For a mixed diff, the sections that `file_sections` gives, put into two parts with `is_test_path`, are equal to `split`. | Two copies of the section rule disagree. |
| `test_annotate_coding_sets_text_slots` | The three text-slot records set `instruction`, `instruction_rationale`, and `tooling_note` exactly. | A record goes to the wrong field. |
| `test_annotate_coding_unknown_slot_refused` | `{"slot": "instructions", "value": "x"}` refuses with check `annotate.unknown-slot` (exit 3). The file bytes do not change. | A typo is accepted silently (FR-18). |
| `test_annotate_coding_missing_value_refused` | `{"slot": "instruction"}` refuses `annotate.slot-missing-field`. | The CLI writes `""` for the agent (FR-19). |
| `test_annotate_coding_hidden_test_upsert` | A second record with the same `(repo_path, name)` and `kind = "pass_to_pass"` replaces the first. The list has one entry. The report names it as replaced. | Duplicate entries, or a silent overwrite (§7.3 correction path). |
| `test_annotate_coding_hidden_tests_sorted` | Records for `b.py::t`, `a.py::z`, `a.py::a` give the order `a.py::a`, `a.py::z`, `b.py::t`. | Output that depends on record order (NFR-1). |
| `test_annotate_coding_hidden_test_missing_key` | A hidden test without `evidence` refuses `annotate.slot-missing-field` and names `evidence`. | The CLI infers evidence (FR-17, FR-19). |
| `test_annotate_coding_rubric_upsert_by_id` | Two records with `id = "R1"` give one criterion with the second text, at the same position. | Duplicate criterion ids. |
| `test_annotate_coding_environment_requires_all_keys` | An environment record without `test_command` refuses. | A partial recipe reaches `emit`. |
| `test_annotate_coding_bad_kind_is_fatal` | A hidden test with `kind = "flaky"` refuses at write time and the file does not change. | A wrong value is stored and found only at `emit`. |
| `test_annotate_coding_partial_fill_is_allowed` | One `instruction` record on a fresh candidate writes the file and exits 0, and the report lists the other slots as remaining. | The "not filled yet" codes block the first record. |
| `test_annotate_coding_check_exits_3_until_ready` | `--check` exits 3 while a hidden test file or the lockfile is missing, and exits 0 after both files exist. | A driver loop that never ends, or that ends too early. |
| `test_annotate_coding_check_writes_nothing` | `--check` with records does not change the file bytes. | `--check` writes. |
| `test_annotate_coding_replace_hidden_tests` | `--replace-hidden-tests` with one record gives a list with only that record. | No way to remove a wrong hidden test. |
| `test_annotate_flag_type_mismatch` | `--replace-findings` on a coding candidate, and `--replace-hidden-tests` on a review candidate, exit 2. | A flag that does nothing without a message. |
| `test_annotate_review_record_on_coding_candidate` | `{"comment": 1, "classification": "defect"}` on a coding candidate refuses `annotate.unrecognised-record`. | A review record changes a coding file. |
| `test_annotate_coding_byte_identical_to_dump` | After a merge, the file bytes equal `FixCandidate.dumps(merged)`. | A second JSON writer with other formatting (NFR-1). |
| `test_annotate_review_unchanged` | The existing `tests/integration/test_annotate.py` passes without change. | The dispatch changes the review path (NFR-9). |
| `test_coding_views_offline` | With `gh`, `glab`, and `git` stubbed to fail, `brief`, `show --path`, and `annotate` succeed. | A view makes a forge or git call (NFR-2). |

## Acceptance Criteria

- [ ] `brief fix-<n>.json` prints identity, PR text, issue text, sizes, paths, the fill contract, the unfilled slots, and the next commands, and it prints no `@@` line.
- [ ] `brief --json` gives the keys in "Schemas & Contracts".
- [ ] `show fix-<n>.json --path <file>` prints only the sections of that file, from the source patch and the test patch.
- [ ] `unknown-path`, `missing-patch`, and `malformed-candidate` refuse with the four fields and exit 3.
- [ ] `annotate` on a coding candidate accepts the four record shapes in "Schemas & Contracts" and merges them as the steps say.
- [ ] A record for an unknown slot, or with a missing key, refuses with exit 3 and the file does not change.
- [ ] `hidden_tests` is sorted by `(repo_path, name)` after each merge.
- [ ] `annotate --check` on a coding candidate exits 0 only when `validate_filled`, `validate_hidden_test_files`, and `validate_recipe` all return no violation.
- [ ] The CLI never writes a value that the agent did not send (FR-19).
- [ ] Every review `brief`, `show --comment`, and `annotate` command gives the same bytes as before.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6). `tests/unit/test_no_model_client.py` stays green.

## Out Of Scope

- The full coding method text in `--help` and the man page: F-1.
- The value rules (enums, lengths, recipe form, file existence): C-1 owns them. This task only calls them.
- Writing the hidden test files, the validation spec, or the lockfile into `candidates/fix-<n>/`. The agent writes them with its own file tools. The CLI does not copy them.
- The instruction leak scan: E-1. `annotate` does not scan the instruction.
- A size limit for `show --path`. A file section can be large. The agent asks for one file at a time. NFR-3 limits only the brief and the survey page.
- Cutting the issue bodies shorter than 4000 characters. C-1 owns that limit.

## Notes & Gotchas

- **NFR-3 can fail with many issues.** Each body can have 4000 characters. A PR body plus three issue bodies is approximately 16 000 characters, and that is above the 12 KiB straw man. This task does not cut the bodies more. Pin the size test to one PR body and one issue body, and report the measured size to G-1, which sets the real target.
- **`Show._hunk_blocks` keys on `+++ b/`** (`show.py:58`). A deleted file has `+++ /dev/null`, so its hunks get the path `""` and are dropped. That is correct for review comments (a comment cannot point at a deleted line), but wrong for `--path`. This is why step 9 uses `PatchSplit.file_sections`. C-1 adds the deleted-file scenario to the fixture.
- **The `--comment` argument changes from required to optional.** argparse then no longer gives exit 2 for a review `show` without `--comment`. Step 7 must give exit 2 itself. The existing test `test_show_missing_candidate_is_usage_error` (`test_show.py:253`) covers only the missing file. `test_show_review_without_comment_exits_2` adds the missing-flag case.
- **Brief is pure rendering.** Read the file in `cli.py`, not in `brief.py`, as the review path does (`brief.py` module docstring).
- Byte-identical output for a human and an agent: no colour and no terminal detection (`Brief.render` docstring, FR-4 of pr-eval-harvest).
- **The check name.** Tech plan §9 calls the refusal `unknown-slot`. `_refuse_annotate` uses the code of the first violation as the `check` (`cli.py:1344`), and the review codes have the prefix `annotate.` (for example `annotate.unknown-comment`). Use `annotate.unknown-slot`, so the two types report in the same style.
- **The record parse is shared.** `Annotate.parse_records` gives `malformed-record` for a broken JSON line before the type dispatch. Do not copy it.
- **Field order.** `json.dumps(sort_keys=False)` writes the insertion order. When you replace `environment`, build the new dict in the §8 order, not in the order of the agent's record. Otherwise two agents that send the same values make different bytes.
- **Replace in place.** For a rubric criterion with an existing `id`, change the existing dict or put the new dict at the same index. Do not remove and append, because that changes the order.
- **Untrusted text.** The instruction and the setup steps are text from the agent. `annotate` stores them only. The recipe form check (C-1) and the Dockerfile writer (D-2) are the guards against injection (NFR-7).

## Dependencies

**Blocked by:** [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md)
**Blocks:** [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)
