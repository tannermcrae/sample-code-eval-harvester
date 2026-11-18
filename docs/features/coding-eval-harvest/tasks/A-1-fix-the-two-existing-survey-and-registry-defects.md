---
id: A-1
title: "[Prerequisites] Fix the two existing survey and registry defects"
summary: "Stop the review survey from blocking PRs without tests, and write registry.json as a list that Harbor loads."
feature: coding-eval-harvest
workstream: Prerequisites
status: todo
complexity: M
merges: [A-0, A-6]
implements: [TP-7, TP-8, FR-34]
user_story: US-7
blocked_by: []
blocks: [A-2, F-1]
---

# A-1: [Prerequisites] Fix the two existing survey and registry defects

**In short:** The code has two defects today. First, the review survey blocks PRs that change no
tests, but a review datapoint does not need tests. Second, `dataset` writes `registry.json` as one
JSON object, but Harbor reads only a list. Correct both, in two separate commits.

## Context

This task corrects two existing defects. Each fix is a separate commit, before the coding work
(tech plan §13, §14).

**Defect 1: the `no-test-change` blocker (TP-7).** `Survey._blockers`
(`src/eval_harvest/survey.py:398`) adds `no-test-change` to each PR that changes no test file.
`Survey._harvestable` (`survey.py:568`) then removes every PR that has a blocker. But the remedy text
of this blocker (`survey.py:50`) says that such a PR is "harvestable for review". The message is
correct and the code is incorrect. Thus the review survey drops valid PRs. Later, the coding survey
(B-1) must own the `no-test-change` exclusion alone. If both surveys exclude for this reason, the
two survey types disagree about what the rule means.

**Defect 2: `registry.json` is an object (TP-8).** `Dataset._write_registry`
(`src/eval_harvest/dataset.py:287`) writes one JSON object. Harbor 0.22.0 `Registry.from_path`
(`harbor/models/registry.py:102`) runs `DatasetSpec.model_validate(row) for row in json.loads(...)`.
It iterates the top-level value, so it needs a list. Thus `harbor run --registry-path
<dataset>/registry.json` fails on every dataset that this CLI writes. F-1 later adds one entry for
each datapoint type. That also needs a list.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** TP-7, TP-8 (prerequisite for FR-34)  ·  **User story:** US-7

## Design References

- **Tech plan:** `../tech-plan.md` §2.3, TP-7: the survey defect, its origin (commit `572a3c9`), and why it is a separate commit.
- **Tech plan:** §2.3, TP-8: the registry defect and the probe result (an object fails, a list with two entries loads).
- **Tech plan:** §7.1, "Where the current flows break down": the first bullet (survey) and the last bullet (registry).
- **Tech plan:** §8, "`registry.json`": the list shape and the entry order. This task writes the first entry only.
- **Tech plan:** §9, "Backward compatibility", exceptions (1) and (2): more PRs become harvestable, and `registry.json` is a list. Both are deliberate.
- **Tech plan:** §12, rows TP-7 and TP-8: the tests to write.
- **ADR-5** in §11: why per-type registry entries exist. That work is F-1. Do not re-open it here.

## What To Build

Make two commits. Part 1 and Part 2 are independent. You can do them in either order, but do not
put them in one commit.

### Part 1: Remove `no-test-change` from the review blockers (commit 1, TP-7)

1. In `Survey._blockers` (`survey.py:398-413`), delete the two lines `if not has_tests:` and `blockers.append("no-test-change")` (lines 410–411).
2. Remove the `has_tests` keyword parameter from `_blockers`. Update the one caller at `survey.py:354` (`cls._blockers(..., has_tests=bool(tests))`). Do not leave an unused parameter: ruff flags it.
3. Delete the `"no-test-change"` entry from `_STATIC_BLOCKER_REMEDIES` (`survey.py:47-51`). The comment above it says the set is "pinned to `_blockers`' output". An entry for a blocker that never fires breaks that rule.
4. Keep `test_paths` and `has_test_change` in the row (`_assemble_row`, `survey.py:461-462`). They are facts. Do not change them.
5. Regenerate the golden payload `tests/fixtures/gh/survey/expected.json`. Use the procedure in the docstring of `tests/integration/test_survey.py:18-19`: run `survey --json` over the reviewed fixture and write stdout to the file. The expected diff is: the row's `"blockers"` list becomes `[]`, and the top-level `"blockers"` object loses its `"no-test-change"` key. Read the diff. No other line may change.
6. Update the tests that use the reviewed fixture as an "every PR is blocked" fixture. After this part, the fixture PR has no blocker:
   - `test_survey_refuses_when_every_pr_is_blocked` (`tests/integration/test_survey.py:250`): build the fixture with `RepoBuilder.build_squash_merged_pull_request(tmp_path, pull_head_fetched=False)` and keep `--no-fetch`. Then the PR is blocked by `no-pull-head-ref` and `squash-without-change-tip`. Assert the new dominant blocker and its remedy (the `git fetch` command).
   - `test_survey_prints_blocker_histogram_with_remedies` (line 290) and `test_survey_json_carries_blocker_counts` (line 328): use the same `pull_head_fetched=False` fixture, and assert a blocker that still exists.
   - `test_survey_verb_fetches_merge_request_heads_with_the_gitlab_refspec` (`tests/integration/test_gitlab.py:359`): the MR now has no blocker, so the survey exits 0. Change the assertions: the exit code is `ExitCode.SUCCESS`, and the pull head is still fetched (keep the `rev-parse` assertion). Update its docstring.
   - The synthetic rows with `blockers=["no-test-change"]` in `test_survey.py:279`, `:342` and `tests/integration/test_batch.py:215`, `:231` are hand-built. They still pass. Change the string to `"no-changed-paths"`, so no test names a blocker that the code cannot produce.
7. Add the TP-7 tests (see "Tests To Write"). Write them first and watch them fail (`CODING_STANDARDS.md` §7).
8. Update `docs/features/pr-eval-harvest/decisions-tech-plan.md`. The entry at line 307 lists `no-test-change` in the blocker taxonomy. Add a new dated entry (2026-11-18) after it. The entry says: `no-test-change` is no longer a blocker; `has_test_change` stays a row fact; the coding survey owns the test-change exclusion (coding-eval-harvest TP-7). Do not edit the old entry. The log is a history.

### Part 2: Write `registry.json` as a list (commit 2, TP-8)

1. In `Dataset._write_registry` (`dataset.py:287-297`), wrap the document in a list: `document = [Harbor.local_registry_document(...)]`. Keep `json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"`.
2. Do not change `Harbor.local_registry_document` (`harbor.py:842`). It builds one entry, and that is correct. F-1 calls it one time for each entry.
3. Update the docstring of `_write_registry` and of the module (`dataset.py:15-16`): `registry.json` is a list of entries.
4. Update `man/eval-harvest-dataset.md:17`: "a local `registry.json` (a JSON list of dataset entries, the shape `harbor run --registry-path` reads)".
5. Update `tests/integration/test_dataset.py:138` (`test_registry_written_and_points_at_tasks`). Read `registry[0]`, and assert that the list has exactly one entry.
6. Add a Harbor loader test to `tests/integration/test_harbor_loader_contract.py`. Add `from harbor.models.registry import Registry` to the `try:` import block at line 62. The `emitted_dataset` fixture already runs `Dataset.build_dataset` (line 138).
7. Add an entry to `docs/features/pr-eval-harvest/decisions-tech-plan.md`, after the contract entry that ends at line 375 (it pins "writes `dataset.toml` + `registry.json`"). Date it 2026-11-18. It says: `registry.json` is a JSON list of `DatasetSpec` entries, because Harbor 0.22.0 `Registry.from_path` reads a list; the old object form did not load.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/survey.py` | Modify | Part 1. Lines 47–51 (remedy table), 354 (caller), 398–413 (`_blockers`). |
| `tests/fixtures/gh/survey/expected.json` | Modify | Part 1. Regenerate. Only the blocker lines change. |
| `tests/integration/test_survey.py` | Modify | Part 1. Three tests get a fixture that is still blocked. Add the TP-7 tests. |
| `tests/integration/test_gitlab.py` | Modify | Part 1. One test: exit 0 instead of the refusal. |
| `tests/integration/test_batch.py` | Modify | Part 1. Two synthetic blocker strings. |
| `src/eval_harvest/dataset.py` | Modify | Part 2. `_write_registry` (line 287) and the module docstring. |
| `tests/integration/test_dataset.py` | Modify | Part 2. The line 138 test reads a list. |
| `tests/integration/test_harbor_loader_contract.py` | Modify | Part 2. Import `Registry`. Add two tests. |
| `man/eval-harvest-dataset.md` | Modify | Part 2. Line 17. |
| `docs/features/pr-eval-harvest/decisions-tech-plan.md` | Modify | One new entry in each part. |

## Schemas & Contracts

**Part 1, before** (one row of the `survey --json` payload, `expected.json`):
```json
"test_paths": [],
"has_test_change": false,
"blockers": ["no-test-change"]
...
"blockers": { "no-test-change": { "count": 1, "remedy": "the change touched no test files; harvestable for review, but not for a hidden-test task" } }
```

**Part 1, after:**
```json
"test_paths": [],
"has_test_change": false,
"blockers": []
...
"blockers": {}
```

The blocker set becomes: `no-integration-commit`, `no-pull-head-ref`, `no-changed-paths`, `squash-without-change-tip`.

**Part 2, before** (`<dataset>/registry.json`):
```json
{
  "description": "",
  "name": "our-repo",
  "tasks": [{"name": "...", "path": "./tasks/..."}],
  "version": "1.0"
}
```

**Part 2, after:**
```json
[
  {
    "description": "",
    "name": "our-repo",
    "tasks": [{"name": "...", "path": "./tasks/..."}],
    "version": "1.0"
  }
]
```

Each entry must validate as Harbor's `DatasetSpec` (`harbor/models/registry.py:66`): `name: str`, `version: str`, `description: str`, `tasks: list[RegistryTaskId]` (each `{name, path}`).

**Migration:** None for either part. A saved `survey --json` file from before Part 1 still loads, and `capture --from-survey` reads its `blockers` lists as they are. The next `dataset` run replaces `registry.json`.
**Backward compatibility:** Both changes are deliberate (tech plan §9). Part 1: more PRs become harvestable, and the field names do not change. Part 2: the shape breaks, but the old shape did not load in Harbor, so no working consumer breaks.

## How To Verify

```bash
uv run pytest tests/integration/test_survey.py tests/integration/test_gitlab.py tests/integration/test_batch.py
uv run pytest tests/integration/test_dataset.py tests/unit/test_harbor.py
mise run test-harbor
mise run lint
mise run typecheck
mise run check
```

`mise run test-harbor` syncs the `eval` group and runs the Harbor tests. `mise run check` does not
run them. You must run `test-harbor` for Part 2, or the new test only skips.

Manual checks:
- Part 1: `grep -rn "no-test-change" src/ tests/` returns nothing. `git diff tests/fixtures/gh/survey/expected.json` shows only the blocker changes.
- Part 2: run `eval-harvest dataset --dataset <dir>` on a fixture dataset. Then `uv run --group eval python -c "from pathlib import Path; from harbor.models.registry import Registry; print(Registry.from_path(Path('<dir>/registry.json')).datasets[0].name)"` prints the short dataset name.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_merged_pr_without_test_change_is_harvestable_for_review` | On the reviewed fixture (no test files changed), `survey --json` gives the row with `blockers == []`, `has_test_change == false`, and `Survey.harvestable_numbers(payload)` contains the PR. | The review survey drops PRs without tests again. |
| `test_survey_refuses_when_every_pr_is_blocked` (changed) | With `pull_head_fetched=False` and `--no-fetch`, the survey refuses `no-harvestable-pr` and names the `git fetch` remedy. | The all-blocked refusal stops working, now that the old fixture is not blocked. |
| `test_blocker_remedies_cover_only_real_blockers` | Every key of `_STATIC_BLOCKER_REMEDIES` is a name that `_blockers` can return. Build one row for each blocker to prove it. | A remedy entry for a blocker that the code never produces. |
| `test_registry_written_and_points_at_tasks` (changed) | `registry.json` is a list with one entry. The entry has the short name and the task paths. | The writer goes back to one object. |
| `test_real_harbor_loads_the_written_registry` | `Registry.from_path(emitted_dataset / "registry.json")` succeeds. `datasets[0].tasks` has one task, with the emitted directory name. | `harbor run --registry-path` fails on our output, as it does today. |
| `test_harbor_registry_loader_rejects_a_bare_object` | Write `json.dumps(entry)` (the old object form) to a temp file. `Registry.from_path` raises `pydantic.ValidationError`. | The loader test passes for any input, so it proves nothing. This is the "watch it go red" guard (CLAUDE.md). |

Mark the two Harbor tests with `@requires_real_harbor`, as the other tests in that module are.
The coding half of the TP-7 test (the coding survey excludes the same PR) belongs to B-1.

## Acceptance Criteria

- [ ] `Survey._blockers` never returns `no-test-change`.
- [ ] A merged PR with no test change and no other blocker is harvestable in the review survey.
- [ ] `has_test_change` and `test_paths` are still in each row.
- [ ] The golden survey diff shows only the blocker changes.
- [ ] No test or source file contains the string `no-test-change`.
- [ ] `registry.json` is a JSON list with one entry.
- [ ] Harbor 0.22.0 `Registry.from_path` loads it (`mise run test-harbor` passes, and the new test does not skip).
- [ ] Harbor refuses the old object form in the guard test.
- [ ] The man page says that `registry.json` is a list.
- [ ] The pr-eval-harvest decisions log has one new dated entry for each fix.
- [ ] Part 1 and Part 2 are two commits, separate from each other and from the coding work.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9).

## Out Of Scope

- The `no-test-change` exclusion of the coding survey: B-1.
- The author email in the mainline pass, and the move of `TEST_MARKERS`: A-2. A-2 also changes `survey.py`, and it starts from this task's commits.
- The registry entries `<name>-review` and `<name>-coding`, and the per-type `harbor run` commands: F-1.
- The per-type `metric.py`: A-2 (the generator) and F-1 (the choice for each dataset).

## Notes & Gotchas

- **Many tests use the reviewed fixture as "blocked".** The fixture changes `code.py` only. Several tests depend on it being blocked. When you run the suite after Part 1 step 1, expect the four tests in step 6 to fail. Each failure is expected. Do not weaken an assertion to make it pass. Give the test a fixture that is really blocked.
- **The GitLab test asserts the refusal through `["failures"]`.** After Part 1 there is no refusal, so `json.loads(...)["failures"]` raises `KeyError`. Rewrite the test. Do not catch the error.
- **A dict is iterable.** `json.loads` of the old registry object gives a dict. Harbor iterates it and gets the key strings, then `DatasetSpec.model_validate("description")` raises a `ValidationError`. The guard test must assert that error type (`pydantic.ValidationError`, already imported at line 44 of the loader test), not a generic `Exception`.
- **The byte-stability test still passes.** `tests/integration/test_dataset.py:194` compares two runs. Both runs write a list, so they agree.
- **The e2e tests need no change.** `tests/e2e/test_contract_flow.py:47` asserts only that `blockers` is a list. `tests/e2e/test_contract_flow.py:135` checks only that `registry.json` exists.
- **Harbor is not installed by `mise run test`.** If the new Harbor tests show "skipped", that is not a pass. Read the `-rs` output.

## Dependencies

**Blocked by:** nothing. You can start now.
**Blocks:** [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md), [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)
