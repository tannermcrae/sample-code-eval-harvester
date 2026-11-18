---
id: D-2
title: "[Emit] Assemble and promote coding tasks with emit --kind fix"
summary: "Turn a filled coding candidate into a complete Harbor task, verify it, and move it into the dataset."
feature: coding-eval-harvest
workstream: Emit
status: todo
complexity: L
merges: [D-3]
implements: [FR-20, FR-21, FR-22, FR-31]
user_story: US-5
blocked_by: [A-2, C-1, D-1, E-1]
blocks: [F-1]
---

# D-2: [Emit] Assemble and promote coding tasks with emit --kind fix

**In short:** Add `emit --kind fix`. It reads a filled `fix-<n>.json`, builds the full coding task
in a temporary directory, runs `verify`, and moves the task into `tasks/` when no check fails.

## Context

This is the integration point of the coding feature (§14). It joins the candidate (C-1), the
templates and the reward stack (D-1), the Harbor fields and the reward keys (A-2), and the coding
`verify` (E-1). Without it, the agent can capture and fill a coding candidate, but it cannot get a
task that Harbor runs. The emitter must keep the rule of the review emitter: build in a temporary
directory, verify, and promote only when no check fails. An unresolved check, such as
`hidden-tests-proven` without a proof, does not stop the promotion (FR-31).

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-20, FR-21, FR-22, FR-31  ·  **User story:** US-5, US-6

## Design References

- **Tech plan:** `../tech-plan.md` §5, "Emit (FR-20 to FR-22, FR-31)": the list of task parts.
- **Tech plan:** §6, "The coding task directory": the exact file layout to write.
- **Tech plan:** §7.2, "Coding emit and verify": the call sequence, and the last line that the CLI prints.
- **Tech plan:** §7.3, "Emit refuses an instruction that names the fix": the refusal and retry path.
- **Tech plan:** §8, "`task.toml` additions for a coding task": the `[[artifacts]]`, `[metadata.origin]`, and `[metadata.harvest]` tables.
- **Tech plan:** §9, the `emit` row: the flags, the output, and the refusals with their exit codes.
- **ADR-1** (one verb, a new module), **ADR-3** (the agent writes the recipe), **ADR-4** (separate verifier, artifact) in §11. These decisions are settled.
- **D-1:** the slot table and the `tests/verify/tests.toml` schema in its "Schemas & Contracts".
- **A-2:** `FixTaskOrigin`, `FixHarvestFields`, the `artifacts` parameter of `Harbor.task_config_document`, `FIX_REWARD_KEYS` in `emit.py`, `TEST_MARKERS` in `patchsplit.py`, and the refusal log.
- **E-1:** `ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, and `HIDDEN_TESTS_RELDIR` in `verify.py`, and the coding branch of `Verify.verify_task`.

## What To Build

Steps 1–11 (the emitter) and step 12 (the CLI wiring) can be separate commits. Merge them together,
because the CLI has no use for the emitter alone.

1. **Create `src/eval_harvest/fix_emit.py`** with one class, `FixEmit`, with class methods and static methods only. Follow the shape of `Emit` in `emit.py:226`.
2. **Add `FixEmit.emit_datapoint(candidate, *, dataset_dir, overrides=(), clone=None, verify_hook=None) -> EmitResult`.** The `datapoint` string is `f"pr-{n}/fix"` (the §7.3 example uses `pr-338/fix`). Do these steps in this order. Each refusal raises `EmitRefusalError` (`emit.py:212`) with exit code 3:
   1. `unfilled-candidate`: `FixCandidate.validate_facts(candidate) + FixCandidate.validate_filled(candidate)` is not empty. Also refuse when `instruction` or `tooling_note` contains the text `@TASK@` or `@TOOLING@`, because the slot replacement would then put text in the wrong slot. Also refuse when `candidates/fix-<n>/validation_spec.toml` does not exist. `next`: "fill every coding slot with `annotate` and re-run emit".
   2. `environment-recipe-invalid`: `FixCandidate.validate_recipe(candidate)` is not empty, or the lockfile `candidates/fix-<n>/<environment.lockfile>` does not exist. `next` names the slot to change.
   3. `hidden-test-file-missing`: for each `hidden_tests` entry, `candidates/fix-<n>/hidden/<repo_path>` does not exist. The `offending` field names each missing path.
   4. `no-fail-to-pass-test`: no `hidden_tests` entry has `kind == "fail_to_pass"`.
3. **Build a `_FixTaskPlan`** frozen dataclass (`slots=True`), as `_TaskPlan` in `emit.py:834`. It holds the candidate, the source patch bytes (read from `source_patch_path`), the hidden test bytes by `repo_path`, the lockfile bytes, the validation spec bytes, and the overrides. Its `directory_name` is `f"{repo.replace('/', '__')}__pr{n}-fix"`. Its `task_name` is `f"{ForgeHost.harbor_org_name(repo)}__pr{n}-fix"`. These are the same patterns as `emit.py:868-875`.
4. **Write the task files** in this order, as `_TaskFile` objects (`emit.py:773`). Use the path constants from E-1 (`ORACLE_PATCH_RELPATH`, `REFERENCE_PATCH_RELPATH`, `HIDDEN_TESTS_RELDIR`) for those three paths. Do not spell them again.

   | Path | Source | Executable |
   |------|--------|-----------|
   | `task.toml` | step 6 | no |
   | `instruction.md` | `fix/instruction.md`, with `@TOOLING@` replaced by `tooling_note`, then `@TASK@` replaced by `instruction` | no |
   | `environment/Dockerfile` | `fix/Dockerfile.tmpl`, rendered (step 5) | no |
   | `environment/<lockfile>` | the lockfile bytes | no |
   | `solution/solve.sh` | `fix/solve.sh`, as it is | yes |
   | `solution/oracle.patch` | the source patch bytes | no |
   | `tests/Dockerfile` | `fix/verifier.Dockerfile.tmpl`, rendered (step 5) | no |
   | `tests/<lockfile>` | the lockfile bytes (the same bytes again: `tests/` is the build context) | no |
   | `tests/test.sh` | `fix/test.sh` | yes |
   | `tests/verify/tests.toml` | step 7 | no |
   | `tests/verify/<repo_path>` | each hidden test file, sorted by `repo_path` | no |
   | `tests/validate/validation_spec.toml` | the validation spec bytes | no |
   | `tests/judge/reference.patch` | the source patch bytes (the same bytes as `oracle.patch`, as in notebook 03) | no |
   | `tests/judge/instruction.md` | the rendered `instruction.md` | no |
   | `tests/judge/rubric.toml` | `rubric_criteria`, as `[[criteria]]` tables with `id`, `kind`, `text`, through `tomlw.emit_document` | no |
   | `tests/judge/models.toml` | `fix/judge/models.toml` | no |
   | `tests/lib/<name>.py` | each file in `fix/lib/`, sorted by name | no |

   Read each template with `Emit._read_template("fix/<name>")` (`emit.py:767`). Do not import any template.
5. **Render the Dockerfiles** with `str.replace`, as `Emit._sealing_dockerfile` does (`emit.py:678`): `@BASE_IMAGE@` → `environment.base_image`; `@REPO_URL@` → `ForgeHost.clone_url_from_pull_request_url(pr_url, repo)`; `@BASE_COMMIT@` → `base_commit`; `@LOCKFILE@` → the lockfile base name; `@SETUP_STEPS@` → `"\n".join(f"RUN {step}" for step in setup_steps)`; `@VERIFIER_BOTO3_PIN@` → `_VERIFIER_BOTO3_PIN` (`emit.py:89`). Import that constant from `emit`. Do not copy it.
6. **Build `task.toml`** with `Harbor.task_config_document` (`harbor.py:178`) and the A-2 additions:
   - `name` = `task_name`. `description` = a module constant `_FIX_TASK_DESCRIPTION = "Fix the bug in the issue report. Hidden tests, a validation agent, a rubric, and a taste judge grade the fix."`. This is the notebook 03 text, and it is the same for every coding task.
   - `keywords` = `("coding-agent", "bug-fix", "eval-harvest")`.
   - `artifacts` = `[{"source": "/work/repo", "exclude": [".git"]}]` (A-2 parameter).
   - Agent network: `no-network` (the default).
   - Verifier network: `allowlist`, with `allowed_hosts = ["bedrock-runtime.us-east-1.amazonaws.com"]`. The reward stack calls Bedrock (D-1). Add a `[verifier.env]` table with the six AWS entries of notebook 03 `task.toml` (`AWS_ACCESS_KEY_ID = "${AWS_ACCESS_KEY_ID:-}"`, and the same for `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` with default `us-east-1`, `AWS_DEFAULT_REGION`, and `VERIFIER_MODEL_ID` with default `us.anthropic.claude-haiku-5-5`). These are references to the host shell, not secrets. A-2 does not add parameters for these. `task_config_document` already has `verifier_network_mode` (`harbor.py:190`, default `"no-network"`). Pass `"allowlist"` to it from `FixEmit`. Add the two new keyword parameters `verifier_allowed_hosts` and `verifier_env`, with empty defaults that keep the review output byte-identical.
   - `[metadata.origin]`: use `FixTaskOrigin` (A-2): `repo`, `pr_numbers = [n]`, `issue_numbers` (sorted, from `issues`), `base_commit`, `merge_commit`, `base_commit_date`, `merged_at`. **Also write `pr_url` and `issue_urls`** (the `url` of each issue, sorted by issue number). The §8 example does not show them, but FR-25 needs them: E-1 adds them to the content-scan tokens and reads them as optional keys. A-2 adds these two fields to `FixTaskOrigin`, after `merged_at`. Do not add fields to `TaskOrigin`: the review `task.toml` bytes are pinned.
   - `[metadata.harvest]`: `FixHarvestFields` (A-2) with `datapoint_type = "coding"`, `kind = "fix"`, `task_type`, `fail_to_pass` and `pass_to_pass` (the counts of `hidden_tests` by kind), `overrides`, and `content_digest`.
7. **Build `tests/verify/tests.toml`** with `tomlw.emit_document`, in the schema that D-1 owns: `test_command` = `environment.test_command`; `timeout_sec = 900`; `protected_markers` = `list(TEST_MARKERS) + ["sitecustomize.py", "usercustomize.py", "pytest.ini"]` (import `TEST_MARKERS` from `patchsplit`, A-2); then one `[[hidden_tests]]` table for each entry, sorted by `(repo_path, name)`, with `id = f"{repo_path}::{name}"`, `repo_path`, `name`, `kind`.
8. **Assemble, verify, promote.** Reuse the steps of `Emit._assemble_verify_promote` (`emit.py:543`) in a `FixEmit._assemble_verify_promote` of the same shape. Call the static helpers of `Emit` and do not copy them: `_materialize`, `_content_digest`, `_compiled_task`, `_parse_task_config`, `_promote`. Write `task.toml` with an empty digest, compute the digest over all files, then write `task.toml` again with the digest (`emit.py:568-576`). Refuse `task-schema-invalid` (exit 4) on any `Harbor.validate_task_layout` or `validate_task_config` violation, as `Emit._refuse_invalid_layout` does (`emit.py:595`). Run the hook. Block only on failures that are not in `overrides` (`emit.py:610-627`). Promote to `<dataset>/tasks/<directory_name>/`.
9. **Do not write `metric.py` from `FixEmit`.** `Emit._ship_metric_script` (`emit.py:638`) writes the review keys only. If `FixEmit` called it, the file would have no coding keys. If `FixEmit` wrote a coding-only file, a later review emit would overwrite it. `dataset` (F-1) writes the per-type `metric.py`. The report tells the agent to run `dataset`.
10. **Return `EmitResult`** (`emit.py:192`) so the CLI report code does not change. Set `selection = f"base {base_commit[:12]} (first parent of merge {merge_commit[:12]})"` and `iteration_index = 0`.
11. **Add `"hidden-tests-proven": "no valid proof file"`** to `_UNRESOLVED_REASONS` in `emit.py:139`. Without it the report prints the raw `offending` text for this check.
12. **Wire the CLI** in `src/eval_harvest/cli.py`:
    1. `add_emit_arguments` (`cli.py:535`): change `choices=("reject", "approve")` to `("reject", "approve", "fix")`. Change the help of `--kind` to name the three values.
    2. `handle_emit` (`cli.py:1425`): when `kind == "fix"` and `--iteration` is given, print `eval-harvest emit: --iteration does not apply to --kind fix` to stderr and return `ExitCode.USAGE`. When `kind == "fix"` and the candidate file name does not start with `fix-`, or `kind != "fix"` and it starts with `fix-`, print a usage error that names the right kind, and return `ExitCode.USAGE`.
    3. For `kind == "fix"`, load with `FixCandidate.load` (C-1) and call `FixEmit.emit_datapoint`. Use the same `except EmitRefusalError` branch, so `Cli.refuse` and the refusal log (A-2) apply with no new code.
    4. `_emit_batch` (`cli.py:1479`): glob `fix-*.json` for `kind == "fix"` and `pr-*.json` for the other kinds. Today it globs `*.json`, so `emit --all --kind reject` would load each `fix-<n>.json` as a review candidate and fail.
    5. `_pr_number_from_stem` (`cli.py:1546`): accept `fix-<n>` too: `re.fullmatch(r"(?:pr|fix)-(\d+)", stem)`.
    6. `_report_emit` (`cli.py:1560`): when `hidden-tests-proven` is in `unresolved`, print one more line: `hidden tests not proven: run the task's oracle and nop trials with a runner; it writes proofs/<task-dir>.json` (§7.2, last line).

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/fix_emit.py` | Create | `FixEmit`, `_FixTaskPlan`, `_FIX_TASK_DESCRIPTION`. It imports `FIX_REWARD_KEYS` from `emit` (A-2); it does not define it. |
| `src/eval_harvest/emit.py` | Modify | Add `hidden-tests-proven` to `_UNRESOLVED_REASONS` (line 139). Nothing else. |
| `src/eval_harvest/harbor.py` | Modify | New `verifier_allowed_hosts` and `verifier_env` parameters on `task_config_document` (line 178). `verifier_network_mode` exists already (line 190). A-2 adds `pr_url` and `issue_urls` to `FixTaskOrigin`. |
| `src/eval_harvest/cli.py` | Modify | Lines 535–590 (`add_emit_arguments`), 1425–1477 (`handle_emit`), 1479–1521 (`_emit_batch`), 1546 (`_pr_number_from_stem`), 1560 (`_report_emit`). |
| `tests/integration/test_fix_emit.py` | Create | The tests below. Build the candidate with C-1's `FixCandidate.scaffold` on `RepoBuilder.build_fix_history` (B-1 creates it), then fill it, as `_prepare` and `_fill` do in `tests/integration/test_emit.py:92-142`. |
| `tests/integration/test_cli.py` | Modify | The usage-error cases for `--kind fix`. |
| `tests/integration/test_harbor_loader_contract.py` | Modify | One case that loads a coding task with Harbor 0.22.0. |

## Schemas & Contracts

**Before — `emit --kind`:** `choices=("reject", "approve")` (`cli.py:563`). `_emit_batch` reads `candidates/*.json`.

**After — `emit --kind`:** `choices=("reject", "approve", "fix")`. `--iteration` with `fix` exits 2. `--all --kind fix` reads `candidates/fix-*.json` only. `--all --kind reject|approve` reads `candidates/pr-*.json` only.

**After — `task.toml` of a coding task** (the shape that the D-2 tests pin; values from the §8 example):

```toml
schema_version = "1.4"

[task]
name = "strands-agents/evals__pr338-fix"
version = "1.0.0"
description = "Fix the bug in the issue report. Hidden tests, a validation agent, a rubric, and a taste judge grade the fix."
keywords = ["coding-agent", "bug-fix", "eval-harvest"]

[agent]
timeout_sec = 1800.0

[verifier]
timeout_sec = 1800.0
environment_mode = "separate"

[verifier.environment]
network_mode = "allowlist"
os = "linux"
allowed_hosts = ["bedrock-runtime.us-east-1.amazonaws.com"]

[verifier.env]
VERIFIER_MODEL_ID = "${VERIFIER_MODEL_ID:-us.anthropic.claude-haiku-5-5}"
AWS_ACCESS_KEY_ID = "${AWS_ACCESS_KEY_ID:-}"
AWS_SECRET_ACCESS_KEY = "${AWS_SECRET_ACCESS_KEY:-}"
AWS_SESSION_TOKEN = "${AWS_SESSION_TOKEN:-}"
AWS_REGION = "${AWS_REGION:-us-east-1}"
AWS_DEFAULT_REGION = "${AWS_REGION:-us-east-1}"

[environment]
network_mode = "no-network"
os = "linux"

[[artifacts]]
source = "/work/repo"
exclude = [".git"]

[metadata.origin]
repo = "strands-agents/evals"
pr_numbers = [338]
issue_numbers = [337]
base_commit = "0bf1252…"
merge_commit = "cc0a391…"
base_commit_date = "2026-07-29"
merged_at = "2026-07-31"
pr_url = "https://github.com/strands-agents/evals/pull/338"
issue_urls = ["https://github.com/strands-agents/evals/issues/337"]

[metadata.harvest]
datapoint_type = "coding"
kind = "fix"
task_type = "investigate-and-fix"
fail_to_pass = 7
pass_to_pass = 6
overrides = []
content_digest = "sha256:…"
```

The table order is the insertion order of the dict that `Harbor.task_config_document` builds
(`harbor.py:201-220`); `tomlw.emit_document` does not sort. A-2 puts `artifacts` after
`environment` and before `metadata`. The test pins the bytes that the code writes, not this sketch.

**`tests/verify/tests.toml`:** the schema in D-1. This task writes it and does not change it.

**Migration:** None. Existing review tasks do not change.
**Backward compatibility:** `emit --kind reject|approve` gives the same bytes as before. One visible change: `emit --all` for review now skips files that are not `pr-*.json`. Before this feature no other file name existed in `candidates/`.

## How To Verify

```bash
uv run pytest tests/integration/test_fix_emit.py tests/integration/test_cli.py
uv run pytest tests/integration/test_emit.py tests/integration/test_emit_verify_wiring.py
mise run test-harbor
mise run lint
mise run typecheck
mise run check
```

`mise run test-harbor` is not part of `check`. Run it, because this task changes the `task.toml` schema (`mise.toml`, `test-harbor` description).

Then, by hand: run the §14 end-to-end chain on a fixture up to `emit --kind fix`. The report must name `hidden-tests-proven` and `git-channel-absence` as unresolved, and print the runner line.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_fix_task_has_every_file` | The promoted directory has exactly the §6 file list. | A missing lockfile copy in `tests/`, and the verifier image does not build. |
| `test_fix_task_is_byte_identical_across_emits` | Two emits of one candidate give the same bytes and the same `content_digest`. | Unsorted hidden tests or dict order in `tests.toml` (NFR-1). |
| `test_fix_dockerfile_seals_then_runs_setup` | `environment/Dockerfile` has the seal steps, then one `RUN` line for each setup step, in order. | The recipe runs before the seal (FR-20). |
| `test_fix_dockerfile_copy_sources_exist` | Each `COPY` source in `environment/Dockerfile` and in `tests/Dockerfile` is a file or directory in its build context. Use `_copy_sources` from `test_emit.py:303`. | `COPY` of a file outside the build context. |
| `test_setup_step_with_line_break_refused` | A setup step with `\n` gives `environment-recipe-invalid`, exit 3, and no task directory. | One line break adds a Dockerfile instruction (NFR-7). |
| `test_base_image_outside_ecr_public_refused` | `base_image = "node:22"` gives `environment-recipe-invalid`. | A non-ECR image is emitted (§3 security). |
| `test_missing_lockfile_refused` | No lockfile file gives `environment-recipe-invalid`. | A task that cannot build is promoted. |
| `test_missing_hidden_test_file_refused` | A `hidden_tests` entry with no file gives `hidden-test-file-missing` and names the path. | A verifier runs with a test that does not exist. |
| `test_no_fail_to_pass_refused` | Only `pass_to_pass` entries give `no-fail-to-pass-test`. | A task that cannot tell a fix from no change is promoted. |
| `test_slot_token_in_instruction_refused` | An instruction that contains `@TOOLING@` gives `unfilled-candidate`. | The tooling note goes into the task text. |
| `test_oracle_patch_only_in_solution` | `oracle.patch` bytes occur in `solution/` and `tests/judge/reference.patch` only, never in `environment/` or `instruction.md`. | The answer is in the agent build context. |
| `test_tests_toml_lists_hidden_ids` | `tests/verify/tests.toml` has one entry per hidden test with `id = "<repo_path>::<name>"`, and `protected_markers` starts with `TEST_MARKERS`. | The verifier and the proof use different ids (§8). |
| `test_origin_has_pr_and_issue_urls` | `[metadata.origin]` has `pr_url` and a sorted `issue_urls` list. | The FR-25 scan has no URL tokens, and a leaked URL in the instruction is not found. |
| `test_verify_failure_blocks_promotion` | A hook with one failure gives exit 4 and no task directory. | A broken task is promoted (FR-31). |
| `test_unresolved_proof_does_not_block` | The real hook with no clone and no proof promotes the task. `hidden-tests-proven` is in `unresolved`, never in `passed`. | The emitter blocks every coding task, or reports the proof as passed (NFR-8). |
| `test_fix_reward_keys_match_template` | `emit.FIX_REWARD_KEYS` equals `FIX_REWARD_KEYS` in `verifier_tpl/fix/lib/aggregate.py`, loaded by path. | The emitter and the verifier disagree on keys. |
| `test_coding_task_loads_in_harbor` | In `test_harbor_loader_contract.py`, Harbor 0.22.0 loads an emitted coding task. | A `task.toml` field that our validator accepts and Harbor refuses. |
| `test_iteration_with_kind_fix_is_usage_error` | `emit fix-1.json --kind fix --iteration 0` exits 2. | A review flag changes a coding task silently. |
| `test_emit_all_reject_skips_fix_candidates` | A dataset with `pr-1.json` and `fix-2.json`: `emit --all --kind reject` emits one task and does not try `fix-2.json`. | The review batch breaks on any coding candidate. |
| `test_review_emit_bytes_unchanged` | The existing `test_emit.py` golden assertions pass with no change. | A shared helper changed the review task (NFR-9). |

## Acceptance Criteria

- [ ] `emit fix-<n>.json --kind fix` writes `tasks/<org>__<name>__pr<n>-fix/` with the §6 layout.
- [ ] Every refusal in §9's `emit` row for `--kind fix` has a test, and each uses the four-field shape (FR-30).
- [ ] `[metadata.origin]` contains `pr_url` and `issue_urls`.
- [ ] An unresolved `hidden-tests-proven` does not block promotion, and the report prints the runner line.
- [ ] A verify failure that is not overridden blocks promotion, with exit 4.
- [ ] Harbor 0.22.0 loads an emitted coding task (`mise run test-harbor`).
- [ ] The review emit output is byte-identical.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6). `tests/unit/test_no_model_client.py` stays green (FR-19).

## Out Of Scope

- The coding `verify` checks (`instruction-names-fix`, `instruction-uniform`, `answer-placement`, `spec-coherent`): E-1. The proof reader: E-2. This task only calls `verify`.
- The templates and the reward stack themselves: D-1. This task only renders and copies them.
- The per-type `metric.py` and `registry.json`: F-1.
- The coding help text for `emit`: F-1. This task changes only the `--kind` help line.
- The refusal log itself: A-2. This task only routes the coding refusals through the existing branch.
- A configurable Bedrock region. The host is fixed to `us-east-1`, as in notebook 03. If a customer needs a different region, that is a new decision.

## Notes & Gotchas

- **The verifier needs network.** `Harbor.task_config_document` defaults the verifier to `no-network` (`harbor.py:190`). The review verifier gets Bedrock through the MicroVM role. The coding tasks run in Harbor's Docker environment (§3, Infrastructure), where `no-network` blocks Bedrock. Then every trial is invalid (D-1, ADR-5 rule). The tech plan does not state the coding verifier network. This task file decides it: `allowlist` with the Bedrock runtime host. `[TODO: verify]` that Harbor 0.22.0's Docker environment applies `allowed_hosts` to a separate verifier environment.
- **`[verifier.env]` must pass the validator.** `Harbor._check_verifier_section` (`harbor.py:324`) reads only `environment_mode` and `environment`. It ignores `env`. `[TODO: verify]` that Harbor's own `VerifierConfig` names this field `env`. Notebook 03 used `[verifier.env]` with schema 1.4, so it is probably correct.
- **`oracle.patch` and `reference.patch` have the same bytes.** This is correct, as in notebook 03. E-1's `answer-placement` check allows this one copy. Do not make the two files different to pass a check.
- **Do not change `Emit._task_files` or `_TaskPlan`.** They belong to the review path. `FixEmit` calls only the pure static helpers of `Emit`.
- **`metric_script` does not change.** A-2 keeps `Harbor.metric_script(reward_keys)` as it is and adds `metric_script_by_type`. `Emit._ship_metric_script` (`emit.py:638`) still calls `metric_script` with `REWARD_KEYS`. Do not change what it writes in this task.
- **Binary sections in the source patch.** `git apply` cannot apply a `Binary files … differ` section. If the source patch has one, `verify` fails `patch-does-not-apply` (E-1) when a clone is given. Do not add a new refusal for it here.
- §3 constraint: frozen dataclasses with `slots=True`, not Pydantic, on the CLI path. Every git call goes through `GitCommandRunner`, but `emit` makes no git call at all (NFR-2).

## Dependencies

**Blocked by:** [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md), [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md), [D-1](D-1-write-the-coding-task-templates-and-the-reward-stack.md), [E-1](E-1-add-the-static-coding-checks-to-verify.md)
**Blocks:** [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)
