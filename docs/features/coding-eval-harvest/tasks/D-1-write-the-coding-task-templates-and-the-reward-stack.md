---
id: D-1
title: "[Emit] Write the coding task templates and the reward stack"
summary: "Write the fixed files of every coding task: two Dockerfiles, the instruction, the oracle script, and the ported reward stack."
feature: coding-eval-harvest
workstream: Emit
status: todo
complexity: L
merges: [D-1, D-2]
implements: [FR-20, FR-27, FR-34, NFR-8]
user_story: US-5
blocked_by: []
blocks: [D-2, E-1, E-2]
---

# D-1: [Emit] Write the coding task templates and the reward stack

**In short:** Write the template files that every coding task starts from. Then copy the working
reward stack from notebook 03 and change it so it runs in its own verifier image, for any test
runner, with `fix_` reward keys. The templates are data. No CLI code reads them in this task.

## Context

A coding task needs a sealed agent image, a verifier image with a clean copy of the base, one
instruction that is the same for every task, an oracle script that applies the fix, and a reward
stack that scores the agent's fix. These files are the fixed part of each coding task. D-2 fills
their slots and copies them into each task. E-1 compares `instruction.md` with the template bytes to
prove that the general instructions did not change (FR-27).

Notebook 03 has a reward stack that works: the oracle gets a tasteful solve and the no-op agent
scores 0, in 4 of 4 runs (PRD §8). That stack runs in `shared` mode, calls `pytest` directly, and
finds fail-to-pass tests by the name prefix `test_f2p_`. This project refuses `shared` mode
(`harbor.py:336`), and a coding task can use any language. This task ports the stack. It also makes
the verifier write the result of each hidden test, so a runner can make the proof file that E-2
reads. This task also owns the `tests/verify/tests.toml` schema, which D-2, E-1, and E-2 all use.

Without this task, D-2 has nothing to render, E-1 has no template to compare with, and E-2 has no
per-test ids to read.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-20, FR-27, ADR-4, ADR-5 (and the reward-key part of FR-34)  ·  **User story:** US-5, US-6, US-7

## Design References

- **Tech plan:** `../tech-plan.md` §5, "Emit (FR-20 to FR-22, FR-31)": what each part of the task contains.
- **Tech plan:** §6, "The coding task directory": the target layout, including `tests/verify/tests.toml`. The templates go in `src/eval_harvest/verifier_tpl/fix/` (§6 module table).
- **Tech plan:** §2.1 FR-20 (seal first, then the setup steps), FR-21 (setup-step limits, enforced by C-1 and D-2, not here), FR-27 (the two slots).
- **Tech plan:** §8, `proofs/<task-dir>.json`: the test-id format `<repo_path>::<name>`, and "The emitted verifier writes the per-test results to `/logs/verifier/verifiers.json`".
- **Tech plan:** §4 Risks, rows "Reward hacking through the agent tree" and "The notebook 03 reward stack is specific to Python and pytest".
- **ADR-3** in §11: the agent writes the setup steps and the lockfile. Do not add a language-specific install step to the templates.
- **ADR-4** in §11: separate verifier mode, the `/work/repo` artifact, the clean base at `/work/base`, and the restore of base test paths. The decision is settled.
- **ADR-5** in §11: which files to copy, the `fix_` key names, and the invalid-trial rule. The decision is settled. Do not re-open it.
- **Source of the port (notebook 03):** it is **not** in this repository. On the author's machine it is at `../sample-gen-ai-evaluations-workshop/Workload Specific Evaluations/Coding Assistant/datasets/strands-evals-coding/tasks/strands-agents__evals__pr338-fix/`. Read its `environment/Dockerfile`, `instruction.md`, `solution/solve.sh`, `task.toml`, `tests/test.sh` (53 lines), `tests/lib/aggregate.py` (117), `tests/lib/agent_tests.py` (68), `tests/lib/bedrock.py` (184), `tests/lib/judges.py` (139), `tests/lib/validate.py` (228), and `tests/judge/models.toml`. Ask the feature owner for a copy if you do not have that checkout.

## What To Build

All new files go in `src/eval_harvest/verifier_tpl/fix/`. Part 1 and Part 2 can be separate
commits. In Part 2, make the first commit a plain copy of the notebook files, so the review diff
shows only the port changes.

### Part 1 — Task templates (old plan D-1)

1. Create the directory `src/eval_harvest/verifier_tpl/fix/`.
2. Create `fix/Dockerfile.tmpl` (the agent image):
   1. Write a new header comment of 3–6 lines. It says: this is the sealed agent image of a coding task; the agent gets the base commit with no change; the hidden tests and the fix are not in this image. Do not copy lines 1–7 of the review template, because they describe a change under review.
   2. Then copy lines 8–34 of `verifier_tpl/Dockerfile.tmpl` byte-for-byte. These are the Harbor-stanza note, the three `checkov:skip` lines, `FROM @BASE_IMAGE@`, the clone, the fetch and reset, and the seal.
   3. Do **not** copy lines 36–40 (`COPY change.patch` and `git apply`). A coding task gives the agent the base with no change.
   4. Add `COPY @LOCKFILE@ /opt/eval-harvest/@LOCKFILE@`. The lockfile goes outside `/work/repo`, so `git status` in the repository stays clean. Notebook 03 does the same with `/opt/requirements.lock`.
   5. Add one line with the slot `@SETUP_STEPS@` and nothing else on it. D-2 replaces it with one `RUN <step>` line for each setup step.
3. Create `fix/verifier.Dockerfile.tmpl` (the verifier image, built from `tests/`):
   1. Write its own header comment (this is the verifier image of a coding task). Then use the same lines as `fix/Dockerfile.tmpl`, in the same order, from the Harbor-stanza note (review line 8) up to and including `@SETUP_STEPS@`. Thus the repository is at `/work/repo` with the same setup. This is important: an editable install (for example `uv pip install -e .`) records the path `/work/repo`. At run time, Harbor empties `/work/repo` and puts the agent's tree there, so the installed package points at the agent's code.
   2. Add `RUN cp -a /work/repo /work/base`. `/work/base` is the clean base, with its `.git`, for `test.sh` (Part 2).
   3. Add `RUN python3 -m pip install --no-cache-dir --target /opt/eval-harvest-verifier @VERIFIER_BOTO3_PIN@`. The reward stack imports `boto3` from this directory only. It must not go into the environment of the repository.
   4. Add these `COPY` lines, in this order: `COPY --chmod=755 test.sh /tests/test.sh`, `COPY verify /tests/verify`, `COPY validate /tests/validate`, `COPY judge /tests/judge`, `COPY lib /tests/lib`.
4. Create `fix/instruction.md`. Use this text exactly. Keep the heading `## General instructions` exactly, because the judges cut the instruction at that heading (notebook `tests/lib/judges.py`, function `instruction()`):

   ```markdown
   ## Task

   @TASK@

   ## General instructions

   - The code repo is at `/work/repo`.
   - You are inside of a Docker container. @TOOLING@ The network is not available, so you cannot install packages.
   - You are expected to act autonomously as a software engineer to complete tasks you are given.
   - Do not stop until you feel you have completed the task and your code changes can be merged.
   - You may need to use software engineering skills like analyzing the codebase, running the tests, and
     building small reproductions to complete the task. Not all tasks will be solvable by reading source
     code alone.
   ```

   The file ends with one `\n`. Each slot occurs one time only.
5. Create `fix/solve.sh`:

   ```sh
   #!/bin/sh
   # Oracle solution for a coding task. Generated by eval-harvest emit; do not edit by hand.
   # Harbor's oracle agent uploads solution/ to /solution and runs this script in the agent image.
   # It applies the source part of the merged fix. The tests of the fix are not in this patch.
   set -eu
   cd /work/repo
   git apply --whitespace=nowarn /solution/oracle.patch
   ```

   It has no slot. D-2 copies it as it is.
6. Do not change any file directly in `verifier_tpl/`. The review templates are pinned, and the review golden tests read them.

### Part 2 — Reward stack port (old plan D-2)

7. **Copy the files.** Copy `lib/{aggregate,agent_tests,bedrock,judges,validate}.py` to `fix/lib/`, and `judge/models.toml` to `fix/judge/models.toml`.
8. **Make the paths configurable.** In `fix/lib/bedrock.py`, replace the constants `TESTS`, `REPO`, and `LOGS` (notebook lines 23–25) with environment reads that keep the container paths as the defaults:
   `TESTS = Path(os.environ.get("EVAL_HARVEST_TESTS_DIR", "/tests"))`, and the same for `EVAL_HARVEST_REPO_DIR` (`/work/repo`), `EVAL_HARVEST_BASE_DIR` (`/work/base`), and `EVAL_HARVEST_VERIFIER_LOGS` (`/logs/verifier`). The other modules import these names from `bedrock`. The tests set the variables. The review `score.py` uses the same idea with `EVAL_HARVEST_AGENT_FINDINGS`.
9. **Add `fix/lib/hidden_tests.py`.** This module is new. It replaces stage 1 of the notebook `test.sh` and the junit part of `aggregate.py`. It reads `tests/verify/tests.toml` (schema in "Schemas & Contracts") and does these steps in `REPO`:
   1. For each tracked path in `BASE_COMMIT` whose path matches a `protected_markers` entry (the same substring rule as `PatchSplit.is_test_path`, A-2), run `git checkout <BASE_COMMIT> -- <path>`.
   2. Delete each path that the agent added (in the agent commit, not in the base commit) and that matches a `protected_markers` entry.
   3. Copy each hidden test file from `TESTS/verify/<repo_path>` to `REPO/<repo_path>`.
   4. Run `test_command` with `bash -c`, in `REPO`, with `JUNIT_PATH=<LOGS>/junit.xml` in the environment, and a timeout of `timeout_sec` (default 900).
   5. Parse `junit.xml`. Map each `<testcase>` to an id with the rule in "Notes & Gotchas". Write `LOGS/verifiers.json` (schema below).
   6. Put the agent's tree back: `git reset -q --hard <agent commit>`, then `git clean -fdq`.
10. **Change `fix/lib/aggregate.py`.**
    - Delete the function `verifiers(hidden)` and the `junit.xml` parsing. Read `LOGS/verifiers.json` instead. The fail-to-pass rate is the passed fraction of the `fail_to_pass` ids. The same applies to `pass_to_pass`. `existing_suite` is 1.0 when its value is `"passed"`, else 0.0.
    - The `verifiers` gate passes when every hidden id is `"passed"` and `existing_suite` is `"passed"`.
    - The trial is invalid (no `reward.json`) when `verifiers.json` is missing or has `"status": "not-run"`. Keep the notebook rule for the LLM stages (lines 66–74).
    - Write `reward.json` with exactly these 10 keys and no others, in this order: `fix_reward`, `fix_correct`, `fix_fail_to_pass`, `fix_pass_to_pass`, `fix_existing_suite`, `fix_validation`, `fix_rubric`, `fix_bloat`, `fix_taste_relative`, `fix_taste_practice`. The mapping from the notebook `flat` dict: `reward`→`fix_reward`, `correct`→`fix_correct`, `fail_to_pass`→`fix_fail_to_pass`, `pass_to_pass`→`fix_pass_to_pass`, `existing_suite`→`fix_existing_suite`, `validation_score`→`fix_validation`, `rubric_score`→`fix_rubric`, `patch_bloat`→`fix_bloat`, `relative_taste`→`fix_taste_relative`, `practice_alignment`→`fix_taste_practice`.
    - Move the other notebook values (`tasteful_solve`, `verifiers_pass`, `agent_tests_added`, `agent_tests_reproduce_bug`) into `reward_details.json` only. They are not in ADR-5, and a key in `reward.json` that is not in the metric list gets a mean of 0 for the review tasks.
    - Add a module constant `FIX_REWARD_KEYS: tuple[str, ...]` with the 10 names. A-2 adds the CLI copy of this constant to `emit.py`. D-2 adds a test that pins the two to be equal (the same pattern as `test_reward_keys_match_between_emitter_and_template`, `tests/integration/test_emit.py:1114`).
    - The command-line argument (`<hidden test module name>`) goes away. `aggregate.py` takes no argument.
11. **Change `fix/lib/judges.py`.** Replace the regex `NOT_SOURCE` (notebook line 21) with a function that uses `protected_markers` from `tests.toml`, plus the documentation rule (`.md`, `.rst`, `.txt`, `CHANGELOG`). Thus bloat uses the same test rule as A-2. Keep `instruction()`: it cuts at `## General instructions`, and Part 1 keeps that heading.
12. **Change `fix/lib/agent_tests.py`.** This stage is reported only. It does not change the reward. Keep its `python -m pytest` call. At the start, run `python -m pytest --version` in `REPO`. If it fails, write `agent_tests.json` with `{"status": "skipped", "reason": "pytest is not available"}` and stop. Take `<base>` and `<agent>` from the command line, as now.
13. **Keep `fix/lib/validate.py`** as in the notebook, with the path change of step 8. Do not try to make it language-neutral (see "Out Of Scope").
14. **Write `fix/test.sh`.** Start from the notebook `test.sh`. Keep its stage comments, `set -u`, the `stage` function, and `exit 0` at the end. Make these changes:
    1. If `/work/repo` does not exist, write `reward.json` with all 10 `fix_` keys at `0` and exit 0. (Harbor skips the upload when the agent deleted the tree, `artifact_handler.py:232-233`. That is a failure of the agent, not an invalid trial.)
    2. `BASE=$(git -C /work/base rev-parse HEAD)`. Do not read `/var/lib/task_base_ref`. The coding agent image does not write it.
    3. `cp -a /work/base/.git /work/repo/.git`. The artifact has no `.git` (`exclude = [".git"]`, A-2).
    4. In `/work/repo`: `git add -A`, commit with hooks off (keep the notebook flags), `AGENT=$(git rev-parse HEAD)`, and write `agent.patch` and `agent.stat` as now.
    5. Stage order: `agent_tests.py "$BASE" "$AGENT"`, then `hidden_tests.py "$BASE" "$AGENT"`, then `validate.py`, then `judges.py rubric`, `judges.py taste`, `judges.py bloat`, then `aggregate.py`. The agent tests run first, on the agent's own tree, before the restore step.
    6. Run every Python stage with `PYTHONPATH=/opt/eval-harvest-verifier python3`, from `/tests/lib`. Do not use the `python` of the repository environment.
15. **Write `fix/judge/models.toml`.** Keep the notebook file. Keep the `[gates]` values (`rubric_min = 0.5`, `bloat_max = 2.0`, `taste_min = 2.0`).
16. **Make `mise run check` green for the new Python files.** `verifier_tpl/` is not excluded from ruff, strict mypy, bandit, or semgrep (`pyproject.toml:69`, `:98`, `:134`). The notebook code has no type hints, uses `lambda` assignments, `subprocess`, and `xml.etree`. Add full type hints. Turn each `lambda` into a named function. For each `subprocess.run`, follow the pattern and the comment in `gitcmd.py:155-176`: bandit B603 is skipped project-wide in `[tool.bandit]`, and B607 gets a per-call `# nosec B607` with a reason. The `bash -c <test_command>` call in `hidden_tests.py` runs agent text on purpose, inside the verifier sandbox. `[TODO: verify]` whether semgrep flags it. If it does, add a `# nosemgrep: <rule-id>` with the reason "the task's own test command, run in the verifier container". Mark the `xml.etree` parse with `# nosec B314` and the reason "the verifier's own test run wrote this file". Use `CODING_STANDARDS.md` names (verbose and descriptive). Keep the logic of each function the same.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/verifier_tpl/fix/Dockerfile.tmpl` | Create | A new header, lines 8–34 of `verifier_tpl/Dockerfile.tmpl`, then the lockfile `COPY` and `@SETUP_STEPS@`. |
| `src/eval_harvest/verifier_tpl/fix/verifier.Dockerfile.tmpl` | Create | The same first part, then `/work/base`, `boto3`, and the `/tests` copies. |
| `src/eval_harvest/verifier_tpl/fix/instruction.md` | Create | The canonical text above, with `@TASK@` and `@TOOLING@`. |
| `src/eval_harvest/verifier_tpl/fix/solve.sh` | Create | Applies `/solution/oracle.patch`. |
| `src/eval_harvest/verifier_tpl/fix/test.sh` | Create | From notebook `tests/test.sh`, changed as in step 14. |
| `src/eval_harvest/verifier_tpl/fix/lib/bedrock.py` | Create | Copy, then step 8. |
| `src/eval_harvest/verifier_tpl/fix/lib/hidden_tests.py` | Create | New module, step 9. It is a sixth module; §6 lists five. |
| `src/eval_harvest/verifier_tpl/fix/lib/aggregate.py` | Create | Copy, then step 10. |
| `src/eval_harvest/verifier_tpl/fix/lib/judges.py` | Create | Copy, then step 11. |
| `src/eval_harvest/verifier_tpl/fix/lib/agent_tests.py` | Create | Copy, then step 12. |
| `src/eval_harvest/verifier_tpl/fix/lib/validate.py` | Create | Copy, then step 8 only. |
| `src/eval_harvest/verifier_tpl/fix/judge/models.toml` | Create | Copy. |
| `pyproject.toml` | Modify | Only if mypy cannot resolve the sibling imports (see "Notes & Gotchas"). |
| `tests/unit/test_fix_templates.py` | Create | The template tests below. Load the files by path, as `_load_score_module` in `tests/integration/test_emit.py:972` does for `score.py`. |
| `tests/integration/test_fix_reward_stack.py` | Create | The reward-stack tests below. Load the modules by path in the same way. |

## Schemas & Contracts

**Before:** The directory `verifier_tpl/fix/` does not exist. The review agent template ends with:
```dockerfile
COPY change.patch /work/change.patch
RUN git apply --whitespace=nowarn /work/change.patch \
 && rm -f /work/change.patch
```
The notebook `tests/` has no `tests.toml`. `aggregate.py` finds the hidden tests by the module name
on its command line, and it splits fail-to-pass from pass-to-pass by the prefix `test_f2p_`.
`reward.json` has 14 keys without a prefix (`reward`, `tasteful_solve`, `correct`, …).

**After — the template slot contract** (D-2 must satisfy it):

| Template | Slot | Value from D-2 |
|----------|------|----------------|
| `fix/Dockerfile.tmpl`, `fix/verifier.Dockerfile.tmpl` | `@BASE_IMAGE@` | `environment.base_image` (an ECR Public reference) |
| both Dockerfiles | `@REPO_URL@` | `ForgeHost.clone_url_from_pull_request_url(pr_url, repo)` (`forge_host.py:211`) |
| both Dockerfiles | `@BASE_COMMIT@` | `base_commit` (first parent of the merge commit) |
| both Dockerfiles | `@LOCKFILE@` | the base name of `environment.lockfile` |
| both Dockerfiles | `@SETUP_STEPS@` | one `RUN <step>` line for each `environment.setup_steps` entry, joined with `\n` |
| `fix/verifier.Dockerfile.tmpl` | `@VERIFIER_BOTO3_PIN@` | `_VERIFIER_BOTO3_PIN` (`emit.py:89`, today `boto3==1.43.89`) |
| `fix/instruction.md` | `@TASK@` | `instruction` |
| `fix/instruction.md` | `@TOOLING@` | `tooling_note` (one line, at most 200 characters) |

**After — `tests/verify/tests.toml`.** This task owns this schema. D-2 writes the file. The verifier
(`hidden_tests.py`, `judges.py`) reads it. E-1 (`spec-coherent`, the hidden-test-name tokens) and
E-2 (the proof rules) read it too. Do not change a key name without changing all four.

```toml
test_command = "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml=$JUNIT_PATH"
timeout_sec = 900
protected_markers = ["/test/", "/tests/", "/spec/", "/__tests__/", "test_", "_test.", ".test.", ".spec.", "Test.java", "conftest.py", "sitecustomize.py", "usercustomize.py", "pytest.ini"]

[[hidden_tests]]
id = "tests/test_x.py::test_f2p_same_turn"   # always f"{repo_path}::{name}"
repo_path = "tests/test_x.py"                # file at tests/verify/<repo_path> in the task
name = "test_f2p_same_turn"
kind = "fail_to_pass"                        # or "pass_to_pass"
```

| Key | Type | Rule |
|-----|------|------|
| `test_command` | str | `environment.test_command`. Must write JUnit XML to `$JUNIT_PATH`. |
| `timeout_sec` | int | Default 900. |
| `protected_markers` | list[str] | `TEST_MARKERS` (A-2) plus `sitecustomize.py`, `usercustomize.py`, `pytest.ini`. D-2 writes the list. The verifier does not hard-code it. |
| `[[hidden_tests]]` | array of tables | Sorted by `(repo_path, name)`. At least one with `kind = "fail_to_pass"`. |
| `hidden_tests.id` | str | `<repo_path>::<name>`, the same id as in the proof file (§8). |
| `hidden_tests.repo_path`, `name` | str | From the candidate's `hidden_tests` entry. |
| `hidden_tests.kind` | str | `fail_to_pass` or `pass_to_pass`. |

**After — `/logs/verifier/verifiers.json`** (the verifier writes it; a runner copies it into the proof file, §8):

```json
{
  "status": "completed",
  "tests": {"tests/test_x.py::test_f2p_same_turn": "failed", "tests/test_x.py::test_p2p_b": "passed"},
  "existing_suite": "passed",
  "unmatched_hidden": []
}
```

`status` is `completed` or `not-run` (no `junit.xml` after the command). Each test value is
`passed`, `failed`, or `missing` (no testcase matched the id). `existing_suite` is `failed` when any
testcase that is not a hidden test failed or had an error. Keys are sorted (NFR-1).

**After — `/logs/verifier/reward.json`:** exactly the 10 `fix_` keys of step 10, each a float.

**Migration:** None. New files only.
**Backward compatibility:** No review file changes. The review emit output stays byte-identical. The review reward keys (`emit.py:58`) do not change.

## How To Verify

```bash
uv run pytest tests/unit/test_fix_templates.py tests/integration/test_fix_reward_stack.py
uv run pytest tests/integration/test_emit.py tests/unit/test_no_model_client.py
mise run lint
mise run typecheck
mise run security
mise run check
```

Manual checks:
1. Open the two Dockerfiles side by side. The lines up to `@SETUP_STEPS@` must be the same in each.
2. On a machine with Docker and the notebook 03 task: replace the notebook `tests/` with the files of this task, add a `tests/verify/tests.toml` for PR 338, and build the verifier image. This is a sanity check only. The real proof is the oracle and nop trials of the Definition of Done (§15). The runner project does them.

## Tests To Write

No test runs a container (§12). The reward-stack tests run the Python stages with the
`EVAL_HARVEST_*` variables set to temporary directories, on a fixture repository from
`tests/fixtures/repo_builder.py`, with `sys.executable -m pytest` as the `test_command`.

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_fix_dockerfile_seal_matches_review_seal` | Lines 15–34 of `verifier_tpl/Dockerfile.tmpl` occur, byte-identical and in sequence, in `fix/Dockerfile.tmpl`. | The coding seal drifts from the review seal, and a sealing step is lost. |
| `test_fix_dockerfile_applies_no_change` | `fix/Dockerfile.tmpl` contains no `change.patch` and no `git apply`. | The agent image starts with a change applied. |
| `test_setup_steps_come_after_the_seal` | In both Dockerfiles, `@SETUP_STEPS@` comes after `git gc --prune=now`. | Setup steps run before the seal and can fetch history (§12, FR-20 row). |
| `test_verifier_dockerfile_shares_the_agent_prefix` | From the line `FROM @BASE_IMAGE@` to `@SETUP_STEPS@`, the two templates have the same lines. | The verifier builds a different base from the agent image. |
| `test_verifier_base_is_copied_after_setup` | `cp -a /work/repo /work/base` comes after `@SETUP_STEPS@`. | `/work/base` has no installed dependencies. |
| `test_instruction_has_each_slot_once` | `@TASK@` and `@TOOLING@` each occur exactly one time. | E-1 and D-2 cannot find or replace a slot unambiguously. |
| `test_instruction_keeps_general_heading` | The text `## General instructions` occurs exactly one time. | The judges send the general instructions as task text. |
| `test_instruction_is_language_neutral` | The template contains no `pytest`, `Python`, `pip`, or `npm`. | Tooling text in the shared part gives the type of task away, and FR-27 is not uniform. |
| `test_fix_solve_applies_oracle_patch` | `fix/solve.sh` runs `git apply` on `/solution/oracle.patch` and reads nothing under `/tests`. | The oracle reads a path that does not exist in the agent image. |
| `test_hidden_tests_restores_base_test_files` | The agent changed `tests/test_a.py` to `assert True`. After `hidden_tests.py`, the run used the base version, and the test fails. | The agent passes by editing a base test (§4 reward-hacking row). |
| `test_hidden_tests_deletes_agent_conftest` | The agent added a root `conftest.py` that skips every test. The hidden tests still run and report `failed`. | A new `conftest.py` outside `tests/` hides failures. |
| `test_hidden_tests_reports_each_id` | One fail-to-pass test fails and one pass-to-pass test passes. `verifiers.json` has both ids with those values. | The per-test result that the proof needs is missing or wrong. |
| `test_hidden_test_not_run_is_missing` | A hidden id with no matching testcase gives `"missing"`, and the gate fails. | A deleted or renamed hidden test counts as passed. |
| `test_no_junit_is_not_run` | A `test_command` that writes no JUnit file gives `"status": "not-run"`, and `aggregate.py` writes no `reward.json`. | A broken test command scores the agent as 0, not as an invalid trial. |
| `test_agent_tree_restored_after_hidden_tests` | After the stage, `git status --porcelain` in the repository is empty and `HEAD` is the agent commit. | The later judges read the base tests or the hidden tests as the agent's change. |
| `test_aggregate_writes_only_fix_keys` | With all stage files present and passing, `reward.json` has exactly `FIX_REWARD_KEYS`. | An extra key gets a diluted mean, or a `fix_` key is missing. |
| `test_aggregate_invalid_when_llm_stage_errors` | `rubric.json` with `"status": "error"` gives no `reward.json` and an `invalid` entry in `reward_details.json`. | Bedrock throttling scores the agent as 0 (ADR-5 rule). |
| `test_aggregate_nop_scores_zero` | Fail-to-pass all `failed`, other stages present: `fix_reward == 0` and `fix_correct == 0`. | The no-op agent gets credit. |
| `test_bloat_uses_protected_markers` | A patch that changes `src/a.py` and `tests/conftest.py` counts only the `src/a.py` lines. | The bloat rule disagrees with the source/test split of A-2. |
| `test_agent_tests_skips_without_pytest` | With `python` that has no `pytest`, `agent_tests.json` has `"status": "skipped"`. | A non-Python repository crashes a stage that does not gate. |
| `test_junit_id_matching` | Parametrized: pytest xunit1 with `file`; pytest without `file`; a parametrized name `test_a[1]`; a Java-style `classname`. Each maps to the expected id. | One JUnit dialect matches no hidden test, and every test is `missing`. |

## Acceptance Criteria

- [ ] All files in "Files Affected" exist in `src/eval_harvest/verifier_tpl/fix/`.
- [ ] The sealing lines are byte-identical to the review template.
- [ ] Every slot in the slot table occurs in the template that it names, and in no other template.
- [ ] No file in `verifier_tpl/` (outside `fix/`) changes.
- [ ] The `tests/verify/tests.toml` schema in this file is the one that the verifier reads.
- [ ] `reward.json` has exactly the 10 `fix_` keys of ADR-5.
- [ ] `verifiers.json` gives one value for each hidden id, in the §8 id format.
- [ ] A base test file that the agent changed is restored before the hidden tests run.
- [ ] No Python stage runs with the repository's Python environment.
- [ ] `tests/unit/test_no_model_client.py` stays green. It skips `verifier_tpl/` by design (its line 33).
- [ ] All commands in "How To Verify" pass, including `mise run security`.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6).

## Out Of Scope

- Rendering the slots, writing `tests.toml`, the `RUN` line rules for setup steps, and copying the files into a task: D-2. The refusal of a bad step (`environment-recipe-invalid`, FR-21): C-1 (`validate_recipe`) and D-2.
- The `instruction-uniform` check: E-1.
- Reading `verifiers.json` or writing the proof file: the runner project (ADR-2). Reading the proof: E-2.
- Per-type means in `metric.py`: A-2 and F-1.
- A build-time run of the existing test suite (notebook 03 does `RUN python -m pytest -q tests`). Do not add it. The suite runs in the verifier, and the runner proof records it (FR-35, ADR-2).
- A language-neutral validation agent. `validate.py` asks the model for pytest files and runs them with `python -m pytest`. For a repository that is not Python, the validation stage fails, and the trial is invalid. This is a known limit of the port. F-1 records it in the help text. Do not fix it here.

## Notes & Gotchas

- **Harbor empties `/work/repo` in the verifier.** `ArtifactHandler.upload_artifacts` in Harbor 0.22.0 calls `empty_dirs([target])` and then uploads the agent's tree to the same `source` path (`harbor/trial/artifact_handler.py:240-245`). Thus the verifier image can build at `/work/repo`. It must keep its clean copy at `/work/base`.
- **JUnit id rule.** A `<testcase>` matches a hidden test when both are true: (a) its `name` equals the hidden `name`, or starts with `name + "["` (pytest parametrize); (b) its `file` attribute equals `repo_path`, or, when there is no `file`, its `classname` with `.` changed to `/` ends with `repo_path` without its extension. Every other testcase belongs to the existing suite. Pytest writes `file` only with `-o junit_family=xunit1`. That is why the example `test_command` sets it. F-1 tells the agent to do this.
- **The artifact comes back without ignored files in the commit.** `git add -A` respects the base `.gitignore`. Thus build output in the agent tree is not in `agent.patch`. This is the notebook behavior. Keep it.
- **`pip --target` and PEP 668.** Debian-based images mark the system Python as externally managed. `[TODO: verify]` that `python3 -m pip install --target …` works on `public.ecr.aws/docker/library/buildpack-deps:bookworm` without `--break-system-packages`. If it does not, add `--break-system-packages` to the template. It is safe with `--target`, because nothing goes into the system site-packages.
- **The verifier needs `python3` and `pip`.** The reward stack runs with `python3` (3.11 or later, for `tomllib`). A base image without them fails the build. F-1 must tell the agent this. The runner proof then records `base-does-not-build` (FR-35).
- **The base image must come from ECR Public.** Notebook 03 copies `uv` from `ghcr.io/astral-sh/uv`. That is not ECR Public. Do not put a `COPY --from=ghcr.io/...` in the template. The agent can install `uv` in a setup step.
- **Keep the `checkov:skip` comments.** `mise run security` runs checkov on Dockerfiles. Without the three skip lines, the scan fails on `@BASE_IMAGE@` (CKV_DOCKER_7) and on the missing `USER` (CKV_DOCKER_3). `[TODO: verify]` that checkov scans files named `*.tmpl` at all; the review template has the skips, so treat them as necessary.
- **mypy and the sibling imports.** The modules run as scripts from `/tests/lib` and use `from bedrock import ...`. `verifier_tpl/` has no `__init__.py`, so mypy can fail with `import-not-found`. `[TODO: verify]` the module name that mypy gives these files. If mypy fails, add a `[[tool.mypy.overrides]]` for the module names `bedrock`, `hidden_tests` with `ignore_missing_imports = true`, and write a comment that tells why, in the style of the `boto3` override at `pyproject.toml:108`.
- **`boto3` for mypy.** `pyproject.toml:108-116` already treats `boto3` and `botocore` as `Any`. You do not need a new override for them.
- **The verifier needs network for Bedrock.** The validation agent and the judges call Bedrock. The review `task.toml` has `network_mode = "no-network"` for the verifier. D-2 sets the coding verifier to `allowlist` with the Bedrock runtime host, and passes the AWS variables. This task does not write `task.toml`, but the stack cannot work without that change.
- **Bedrock cost.** Each trial calls Bedrock four times. The customer pays (ADR-5, consequences).
- Notebook `aggregate.py` writes `reward.txt` as an empty file on an invalid trial. Keep that behavior.
- §3 constraint: the CLI never imports the files in `verifier_tpl/`. They are data.

## Dependencies

**Blocked by:** nothing. You can start now.
**Blocks:** [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md), [E-1](E-1-add-the-static-coding-checks-to-verify.md), [E-2](E-2-resolve-hidden-tests-proven-and-add-the-guard-tests.md)
