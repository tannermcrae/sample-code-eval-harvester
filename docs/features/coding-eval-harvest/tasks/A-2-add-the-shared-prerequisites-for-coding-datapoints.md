---
id: A-2
title: "[Prerequisites] Add the shared prerequisites for coding datapoints"
summary: "Add the small shared parts that the coding verbs need: author email, test-file rule, Harbor fields, refusal log, issue fetch."
feature: coding-eval-harvest
workstream: Prerequisites
status: todo
complexity: L
merges: [A-1, A-2, A-3, A-4, A-5]
implements: [TP-1, TP-2, TP-3, TP-5, TP-6, FR-5, FR-6, FR-12, FR-13, FR-33, FR-34, NFR-4, NFR-5]
user_story: [US-2, US-3, US-7]
blocked_by: [A-1]
blocks: [B-1, C-1, D-2, E-1, F-1]
---

# A-2: [Prerequisites] Add the shared prerequisites for coding datapoints

**In short:** Before the coding verbs can exist, the code needs five small shared parts. The
survey must record the author email. One function must tell if a file is a test file. `harbor.py`
must write coding fields. Refusals must be saved to files. The forges must read issue text.

## Context

Each later workstream uses one or more of these five parts. If each workstream adds its own copy,
the copies disagree. Each part is a prerequisite from tech plan §2.3:

1. **Author email (TP-1).** The coding survey (B-1) reports `author_commits` for each PR. Today
   `Survey.mainline` (`src/eval_harvest/survey.py:295`) does not read the email. Without it, B-1
   needs one more git call for each PR, and NFR-5 (at most 6 git calls for each PR) fails.
2. **Patch splitter (TP-2).** The survey (B-1), the capture (C-1), and the instruction scan (E-1)
   must agree on which path is a test. Today the rule is one list comprehension in
   `Survey.survey_one` (`survey.py:345`). If each caller copies it, a file can be "source" in the
   survey and "test" in the capture, and the oracle patch can contain test files.
3. **Harbor coding fields (TP-3).** The coding emitter (D-2) needs the artifact
   `{source = "/work/repo", exclude = [".git"]}` (ADR-4), coding provenance in `task.toml` (§8),
   and a `metric.py` that does not mix review and coding rewards (ADR-5). `verify` (E-1) and
   `dataset` (F-1) read the same field names.
4. **Refusal log (TP-5).** FR-33 wants the `dataset` report to show the refusal reasons for each
   type. Today a refusal goes to stderr (`Cli.refuse`, `src/eval_harvest/cli.py:1775`) or into the
   batch summary (`ItemResult.refuse`, `src/eval_harvest/batch.py:76`), and nothing keeps it.
5. **Issue fetch (TP-6).** The coding capture (C-1) puts the text of each linked issue into the
   candidate (FR-13). Neither forge module reads an issue today. Without the issue text, the agent
   must guess the symptom from the diff, and that gives instructions that leak the fix.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** TP-1, TP-2, TP-3, TP-5, TP-6 (prerequisites for FR-5, FR-6, FR-12, FR-13, FR-33, FR-34, NFR-4, NFR-5)  ·  **User story:** US-2, US-3, US-7

## Design References

- **Tech plan:** `../tech-plan.md` §2.3, TP-1, TP-2, TP-3, TP-5, TP-6: why each part is necessary.
- **Tech plan:** §3, Performance: "keep one `git log --first-parent` pass for the full mainline".
- **Tech plan:** §3, Constraints: one stateless class for each file. `TypedDict` and frozen dataclasses, not Pydantic, on the CLI path. Every `git`, `gh`, and `glab` call goes through `GitCommandRunner`. The review task shape is pinned. Harbor gives 0 to a missing reward key.
- **Tech plan:** §6, module table, rows `survey.py`, `patchsplit.py`, `harbor.py`, `refusal_log.py`, `forge.py`/`gitlab.py`.
- **Tech plan:** §7.2, "Coding capture": the loop that calls `fetch_issue(n)` one time for each linked issue.
- **Tech plan:** §7.3, "The forge cannot give the issue during capture": a `ForgeError` becomes an item refusal in a batch. C-1 does that. This task only raises.
- **Tech plan:** §8, "`task.toml` additions for a coding task" and "`refusals/<type>/<datapoint>.json`": the exact TOML and the refusal record rules.
- **Tech plan:** §2.2, NFR-4 (one call for each linked issue) and NFR-5 (at most 6 git calls for each PR).
- **Tech plan:** §12, "Existing tests affected": Part 1 does not change the review golden payload.
- **ADR-4** in §11: separate verifier mode, with the agent tree as an artifact. The decision is settled.
- **ADR-5** in §11: the `fix_` reward keys and the per-type means. The decision is settled.
- **PRD:** `../prd.md` US-3 ("fix split into source and tests") and US-7 (refusal reasons for each type).

## What To Build

Make one commit for each part. Part 1 and Part 2 both change `survey.py`, so do Part 1 first and
Part 2 second. Parts 3, 4, and 5 are independent of the other parts and of each other. Each part is
complete when its own tests pass. A later task can start when the part that it needs is merged:
B-1 needs Parts 1 and 2. C-1 needs Parts 2, 4, and 5. E-1 and D-2 need Part 3. F-1 needs Parts 3
and 4.

### Part 1: Record the author email in the mainline pass (TP-1)

1. Add the field `author_email: str` to the frozen dataclass `Commit` (`survey.py:122-131`). Put it after `at`.
2. In `Survey.mainline` (`survey.py:302-304`), change the format string from `f"--format={_RS}%H{_FS}%at{_FS}%s{_FS}%b{_FS}"` to `f"--format={_RS}%H{_FS}%at{_FS}%ae{_FS}%s{_FS}%b{_FS}"`. Put `%ae` before `%s`, so the free-text fields (subject and body) stay last.
3. Change `_LOG_RECORD_FIELDS` (`survey.py:100`) from `5` to `6`. Update its comment: "sha, author-time, author-email, subject, body, names".
4. In `Survey._parse_commit_record` (`survey.py:310-324`), read the six fields in the new order. Set `author_email=email.strip().lower()`. Lowercase the email, because git keeps the case that each commit wrote, and B-1 compares emails.
5. Do not add `author_email` to the survey row or to the `--json` payload. `_assemble_row` does not read `Commit` fields directly. Confirm that the golden test passes with no change to `expected.json`.
6. Do not add a second git call.

### Part 2: Add the source/test patch splitter (TP-2)

1. Create `src/eval_harvest/patchsplit.py`. It has one class, `PatchSplit`, with static methods only. It imports from the standard library only, and it imports no project module. This lets the offline modules (`fix_candidate.py`, `seal.py`) import it without the online layer. `diffspan.py` follows the same rule (see its docstring).
2. Move the `TEST_MARKERS` tuple and its comment from `survey.py:78-91` into `patchsplit.py`. Keep the same values and the same order.
3. In `survey.py`, delete the tuple and add `from eval_harvest.patchsplit import TEST_MARKERS, PatchSplit`.
4. Add `PatchSplit.is_test_path(path: str) -> bool`. It returns `any(marker in f"/{path}" for marker in TEST_MARKERS)`. This is the same expression as `survey.py:345`. Keep the `/` prefix, because it lets `tests/x.py` at the repository root match `/tests/`.
5. Replace the comprehension at `survey.py:345` with `[path for path in ranges.changed_paths if PatchSplit.is_test_path(path)]`.
6. Add a frozen dataclass `SplitPatch` with two fields: `source_patch: str` and `test_patch: str`.
7. Add `PatchSplit.split(unified_diff: str) -> SplitPatch`:
   - Cut the diff into file sections. A section starts at each line that starts with `diff --git `, and it continues until the next such line or the end of the text.
   - Take the path from the `b/` side of the `diff --git` header. Git writes the `b/` path for deletions too (`diff --git a/x b/x`), so you do not need to read `+++ /dev/null`.
   - Put each section into `test_patch` if `is_test_path(path)` is true. Otherwise put it into `source_patch`. Keep the original order of the sections in each output.
   - Keep the bytes of each section exactly. Do not strip it and do not normalize line endings. `git apply` needs the bytes as they are.
   - Text before the first `diff --git` line is not valid input. Raise `ValueError` that names the first line.
8. Do not change `diffspan.py`. It answers a different question (hunk spans), and it already has one caller contract.

### Part 3: Extend `harbor.py` for coding tasks (TP-3, FR-34 metric half)

1. **Artifact `exclude` check.** `Harbor._artifact_fields` (`harbor.py:472`) reads only `source`, `destination`, and `service`, and it ignores other keys. Thus `exclude` already passes, but nothing checks it. Add `_check_artifact_exclude(index, table)` and call it from `_check_one_artifact` (line 456). The rules come from Harbor 0.22.0 `ArtifactConfig` (`harbor/models/task/config.py:642`, `exclude: list[str]`, "passed as tar --exclude flags"):
   - `exclude` is absent, or a list.
   - Each item is a non-empty string.
   - No item contains `..` as a path part (the same rule as `_check_artifact_source`, line 488).
   - Use the violation code `compile.schema-invalid` and the pointer `artifacts/<index>/exclude`, as the other artifact checks do.
2. **`artifacts` parameter.** Add `artifacts: Sequence[Mapping[str, TomlValue]] = ()` to `Harbor.task_config_document` (line 178). When it is not empty, add the key `"artifacts": [dict(a) for a in artifacts]` after `"environment"` and before `"metadata"`. When it is empty, add no key. Thus the review `task.toml` is byte-identical. `tomlw.emit_document` already writes a list of tables as `[[artifacts]]` (`tomlw.py:215`).
3. **Coding provenance.** Add a frozen dataclass `FixTaskOrigin` next to `TaskOrigin` (line 146), with the fields in this order: `repo: str`, `pr_numbers: tuple[int, ...]`, `pr_url: str`, `issue_numbers: tuple[int, ...]`, `issue_urls: tuple[str, ...]`, `base_commit: str`, `merge_commit: str`, `base_commit_date: str`, `merged_at: str`. Change `_origin_table` (line 223) to accept `TaskOrigin | FixTaskOrigin`. Write `pr_url`, `issue_numbers`, `issue_urls`, and `merge_commit` only for `FixTaskOrigin`, in this order. `pr_url` and `issue_urls` are not in the §8 example, but the FR-25 content scan (E-1) reads them from `[metadata.origin]` as leak tokens. `issue_urls` has the same order as `issue_numbers`.
4. **Coding harvest fields.** Add a frozen dataclass `FixHarvestFields` next to `HarvestFields` (line 158): `kind: str` (always `"fix"`), `task_type: str` (`"investigate-and-fix"`), `fail_to_pass: int`, `pass_to_pass: int`, `overrides: tuple[str, ...]`. Change `_harvest_table` (line 234) to accept `HarvestFields | FixHarvestFields`. For `FixHarvestFields`, write `datapoint_type = "coding"` first, then `kind`, `task_type`, `fail_to_pass`, `pass_to_pass`, `overrides`. Do not write `content_digest`. The emitter adds it later, as `Emit._task_config_bytes` does (`emit.py:653`).
5. Change the type of the `origin` and `harvest` parameters of `task_config_document` to the two unions. Do not add `datapoint_type` to the review table. A review task has no `datapoint_type`, and every reader treats a missing value as `review` (§8 Migration).
6. **Coding reward keys.** Add `FIX_REWARD_KEYS: Final[tuple[str, ...]]` to `src/eval_harvest/emit.py`, next to `REWARD_KEYS` (line 58). Do not put it in `harbor.py` or in a new module. Use the ADR-5 list: `fix_reward`, `fix_correct`, `fix_fail_to_pass`, `fix_pass_to_pass`, `fix_existing_suite`, `fix_validation`, `fix_rubric`, `fix_bloat`, `fix_taste_relative`, `fix_taste_practice`. Add a comment: it must stay equal to the keys that `verifier_tpl/fix/lib/aggregate.py` writes (D-1). D-2 and F-1 import it from `emit`, as `dataset.py:45` imports `REWARD_KEYS`.
7. **Do not add verifier network or env parameters.** D-2 owns the verifier network parameters of `task_config_document`: `verifier_network_mode` (it exists today, at `harbor.py:190`, with the default `"no-network"`), and the new `verifier_allowed_hosts` and `verifier_env`. Only the coding verifier needs Bedrock access. This part adds only `artifacts`.
8. **Per-type metric.** Do not change `Harbor.metric_script(reward_keys)` (line 663). Review-only datasets keep it, and its bytes stay the same. Add a new static method `Harbor.metric_script_by_type(key_groups: Mapping[str, Sequence[str]]) -> bytes`. The generated script:
   - Has `GROUPS = {"coding": (...), "review": (...)}`, with groups sorted by name and keys sorted in each group (NFR-1).
   - For each group, selects the rows that have at least one key of that group. It calculates `mean_<key>` over those rows only. A missing key in a selected row counts as 0.
   - Counts a `None` row (a failed trial) as 0 in every group. State in the docstring that the type of a failed trial is unknown, so it lowers every mean. This keeps the "a failed trial does not raise the mean" rule of the current script.
   - Writes `trials` (all rows) and `trials_<group>` (selected rows) as extra keys.
   - Keeps the same `# /// script` header and the same `-i`/`-o` arguments as `metric_script`.
9. Update the docstrings of the changed functions. Keep the existing references to Harbor source lines.

### Part 4: Add the persistent refusal log (TP-5, FR-33 write half)

1. Create `src/eval_harvest/refusal_log.py` with one stateless class `RefusalLog` and a `TypedDict` `RefusalRecordDict` with the keys `check`, `datapoint`, `offending`, `next`, `verb` (all `str`). It imports only the standard library.
2. Add module constants: `REFUSALS_DIRNAME: Final = "refusals"` and `DATAPOINT_TYPES: Final = ("coding", "review")`.
3. Add `RefusalLog.path_for(dataset_dir: Path, datapoint_type: str, datapoint: str) -> Path`:
   - Raise `ValueError` if `datapoint_type` is not in `DATAPOINT_TYPES`.
   - Make the file name from `datapoint`: replace each `/` with `__`. Raise `ValueError` if the result is empty, starts with `.`, or contains `\` or `..`. Example: `pr-338/reject` → `refusals/review/pr-338__reject.json`. The `__` rule matches the task directory names (`org__name__pr<n>`). A repository name also works: `acme/widgets` → `acme__widgets.json` (B-1 uses this for `no-harvestable-fix`).
4. Add `RefusalLog.write(dataset_dir, datapoint_type, record: RefusalRecordDict) -> Path`. Create the directory. Write `json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"` as UTF-8. This replaces an older file for the same datapoint (§8). Use the same JSON style as `Dataset._write_registry` (`dataset.py:296`).
5. Add `RefusalLog.clear(dataset_dir, datapoint_type, datapoint) -> None`. Remove the file if it exists. Do nothing if it does not exist.
6. Add `RefusalLog.read_all(dataset_dir) -> dict[str, list[RefusalRecordDict]]`. Return one key for each type in `DATAPOINT_TYPES`, with the records sorted by `datapoint`. A missing directory gives empty lists. A file that is not a JSON object with the five string keys raises `ValueError` that names the file. Do not skip it silently. F-1 turns the error into a refusal.
7. Wire the review refusals in `cli.py`. In each case the datapoint type is `"review"`, and you have the dataset directory:
   - `Cli._capture_single` (line 881): before the `no-emittable-iteration` refusal at line 900, call `RefusalLog.write(..., verb="capture")`. After a successful capture, call `RefusalLog.clear(dataset, "review", f"pr-{pr_number}")`.
   - `Cli._capture_item` (line 994): write a record for `forge-unavailable` (line 1002) and `no-emittable-iteration` (line 1014). Clear on success (line 1015).
   - `Cli.handle_emit` (line 1425): in the `except EmitRefusalError` branch (line 1466), write a record with `datapoint=refusal.datapoint` and `verb="emit"`. After success, clear `f"pr-{candidate['pr_number']}/{kind}"`. That is the datapoint string that `Emit.emit_datapoint` uses (`emit.py:265`).
   - `Cli._emit_item` (line 1523): the same, in its `except` branch (line 1541) and on success. Use `refusal.datapoint` (`pr-<n>/<kind>`) for the record, not the batch key (`pr-<n>`).
   - Put the write and the clear in two small helpers on `Cli` (`_log_refusal`, `_clear_refusal`). Keep the handlers flat (`CODING_STANDARDS.md` §3).
8. Do not log refusals that are not about one datapoint: `unknown-pr`, `no-clean-pr`, `no-candidates`, and the risk-map refusals of `_load_risk_map_or_refuse`. Their `datapoint` is a file path, not a datapoint.
9. Do not wire `survey`. The review survey has no `--dataset` argument (`add_survey_arguments`, line 339). B-1 adds `--dataset` for the coding survey and logs `no-harvestable-fix` there.
10. Add a dated entry (2026-11-18) to `docs/features/coding-eval-harvest/decisions-tech-plan.md`: `capture` and `emit` now also write `refusals/review/<datapoint>.json` on a per-datapoint refusal. The pr-eval-harvest log pins "the files each verb writes" (`docs/features/pr-eval-harvest/decisions-tech-plan.md:346`), so this new file is a contract change.

### Part 5: Add `fetch_issue` to the GitHub and GitLab forges (TP-6, FR-13 fetch half, NFR-4)

1. In `forge.py`, add a frozen dataclass `IssueFacts` next to `PullRequestFacts` (line 102): `number: int`, `url: str`, `title: str`, `body: str`, `is_pull_request: bool`.
2. Add `Forge.fetch_issue(cls, repo: str, issue_number: int) -> IssueFacts`:
   - Call `GitCommandRunner.gh(["api", f"repos/{repo}/issues/{issue_number}"])`. This is one call.
   - On a non-zero exit, raise `ForgeError(f"gh api repos/{repo}/issues/{issue_number} failed ({code}): {err}")`. Use the same message shape as `_fetch_api_list` (line 159).
   - Parse the JSON. If it is not a JSON object, raise `ForgeError` that names the type, as `_fetch_api_list` does for a non-list.
   - Map the fields: `number` ← `number`, `url` ← `html_url`, `title` ← `title`, `body` ← `body or ""`, `is_pull_request` ← `"pull_request" in payload`.
   - Add a private helper `_fetch_api_object(api_path)` that is the object twin of `_fetch_api_list`. Keep `fetch_issue` shallow (`CODING_STANDARDS.md` §3).
3. In `gitlab.py`, add a module constant `ISSUE_QUERY` next to `CAPTURE_QUERY` (line 70):
   ```graphql
   query($fullPath: ID!, $iid: String!) {
     project(fullPath: $fullPath) {
       issue(iid: $iid) { iid title description webUrl }
     }
   }
   ```
4. Add `GitLab.fetch_issue(cls, remote: ForgeRemote, issue_iid: int) -> IssueFacts`:
   - Call `cls._run_graphql(remote, ISSUE_QUERY, ["-f", f"fullPath={remote.project_path}", "-f", f"iid={issue_iid}"])`. `_run_graphql` (line 279) already raises `ForgeError` for a failed call, a non-JSON body, GraphQL errors, and a missing project.
   - If `project["issue"]` is `None`, raise `ForgeError(f"{remote.host} has no issue #{issue_iid} in {remote.project_path}")`. Use the shape of the MR message at line 275.
   - Map: `number` ← `int(iid)`, `url` ← `webUrl`, `title` ← `title`, `body` ← `description or ""`, `is_pull_request` ← `False`.
5. Do not truncate the body here. The 4000-character limit and the `truncated` flag are in C-1 (`FixCandidate.scaffold`). The forge layer returns facts as they are.
6. Do not call `fetch_issue` from any verb. C-1 wires it.
7. Record three payloads for the tests:
   - `tests/fixtures/gh/issue/issue.json`: one `gh api repos/<r>/issues/<n>` response. Remove every field that the code does not read.
   - `tests/fixtures/gh/issue/pull_request_as_issue.json`: a PR number read through the issues endpoint. Keep its `pull_request` key.
   - `tests/fixtures/glab/recorded/issue.json`: one `glab api graphql` response for `ISSUE_QUERY` (`{"data": {"project": {"issue": {...}}}}`).
   The docstring of `tests/integration/test_gitlab.py:15` says that the GitLab payloads are verbatim gitlab.com responses. Keep that rule: record from a real public project, then remove only unused fields.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/survey.py` | Modify | Part 1: `Commit` (line 122), `_LOG_RECORD_FIELDS` (line 100), `mainline` (line 295), `_parse_commit_record` (line 310). Part 2: delete `TEST_MARKERS` (lines 78–91), import it, use `PatchSplit.is_test_path` at line 345. |
| `src/eval_harvest/patchsplit.py` | Create | Part 2. Follow the layout of `diffspan.py`: a module docstring that tells why there is one copy, `Final` constants, no project imports. |
| `src/eval_harvest/harbor.py` | Modify | Part 3: `TaskOrigin`/`HarvestFields` neighbors (146–170), `task_config_document` (178), `_origin_table` (223), `_harvest_table` (234), `_check_one_artifact` (456), new `_check_artifact_exclude`, new `metric_script_by_type` (after 663). |
| `src/eval_harvest/emit.py` | Modify | Part 3: add `FIX_REWARD_KEYS` after `REWARD_KEYS` (line 58). |
| `src/eval_harvest/refusal_log.py` | Create | Part 4. Same module style as `diffspan.py`: a docstring that tells why, `Final` constants, stdlib only. |
| `src/eval_harvest/cli.py` | Modify | Part 4: `_capture_single` (881), `_capture_item` (994), `handle_emit` (1425), `_emit_item` (1523), two new helpers. |
| `src/eval_harvest/forge.py` | Modify | Part 5: add `IssueFacts`, `Forge.fetch_issue`, `Forge._fetch_api_object`. |
| `src/eval_harvest/gitlab.py` | Modify | Part 5: add `ISSUE_QUERY`, `GitLab.fetch_issue`. Import `IssueFacts` from `forge` (line 44 already imports from there). |
| `tests/integration/test_survey.py` | Modify | Part 1 tests. |
| `tests/unit/test_patchsplit.py` | Create | Part 2. Mirror `tests/unit/test_diffspan.py`: hand-written diffs as module constants, each with a comment that names the defect. |
| `tests/unit/test_harbor.py` | Modify | Part 3: add cases to `TestTaskConfigDocument` (line 62) and `TestValidateTaskConfig` (line 96). Add `TestFixMetricScript`. |
| `tests/unit/test_refusal_log.py` | Create | Part 4 unit tests. |
| `tests/integration/test_cli.py`, `tests/integration/test_batch.py` | Modify | Part 4. Single-verb refusal tests go in `test_cli.py`. Batch refusal tests go in `test_batch.py`, next to the existing capture and emit batch tests. |
| `tests/integration/test_forge.py` | Modify | Part 5 GitHub tests. Stub `GitCommandRunner.gh` with `monkeypatch`, as line 167 does. |
| `tests/integration/test_gitlab.py` | Modify | Part 5 GitLab tests. Stub `GitCommandRunner.glab`, as line 376 does. |
| `tests/fixtures/gh/issue/issue.json`, `tests/fixtures/gh/issue/pull_request_as_issue.json`, `tests/fixtures/glab/recorded/issue.json` | Create | Part 5 recorded payloads. |
| `docs/features/coding-eval-harvest/decisions-tech-plan.md` | Modify | Part 4: add one entry. |

## Schemas & Contracts

**Part 1, `Commit` before:**
```python
@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    at: int
    subject: str
    body: str
    paths: frozenset[str]


# git log --first-parent --format=<RS>%H<FS>%at<FS>%s<FS>%b<FS> --name-only <branch>
_LOG_RECORD_FIELDS = 5
```

**Part 1, after:**
```python
@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    at: int
    author_email: str  # lowercased, "" when git gives none
    subject: str
    body: str
    paths: frozenset[str]


# git log --first-parent --format=<RS>%H<FS>%at<FS>%ae<FS>%s<FS>%b<FS> --name-only <branch>
_LOG_RECORD_FIELDS = 6
```

**Part 2, before:**
```python
# survey.py:80
TEST_MARKERS = ("/test/", "/tests/", "/spec/", "/__tests__/", "test_", "_test.", ".test.", ".spec.", "Test.java", "conftest.py")
# survey.py:345
tests = [path for path in ranges.changed_paths if any(marker in f"/{path}" for marker in TEST_MARKERS)]
```

**Part 2, after:**
```python
# patchsplit.py
TEST_MARKERS: Final = (...same ten values, same order...)

@dataclass(frozen=True)
class SplitPatch:
    source_patch: str
    test_patch: str

class PatchSplit:
    @staticmethod
    def is_test_path(path: str) -> bool: ...
    @staticmethod
    def split(unified_diff: str) -> SplitPatch: ...   # raises ValueError on a preamble
```

**Part 3, `Harbor.task_config_document` before:**
```python
def task_config_document(cls, *, name, description, origin: TaskOrigin, harvest: HarvestFields,
                         version=..., keywords=..., agent_timeout_sec=..., verifier_timeout_sec=...,
                         agent_network_mode="no-network", verifier_network_mode="no-network") -> dict[str, TomlValue]
```

**Part 3, after:**
```python
def task_config_document(cls, *, name, description, origin: TaskOrigin | FixTaskOrigin,
                         harvest: HarvestFields | FixHarvestFields,
                         artifacts: Sequence[Mapping[str, TomlValue]] = (), ...same keywords...) -> dict[str, TomlValue]
```

**Part 3, coding `task.toml` output** (the §8 target plus `pr_url` and `issue_urls`, before the emitter adds `content_digest`):
```toml
[[artifacts]]
source = "/work/repo"
exclude = [".git"]

[metadata.origin]
repo = "strands-agents/evals"
pr_numbers = [338]
pr_url = "https://github.com/strands-agents/evals/pull/338"
issue_numbers = [337]
issue_urls = ["https://github.com/strands-agents/evals/issues/337"]
base_commit = "0bf1252…"
merge_commit = "cc0a391…"
base_commit_date = "2026-07-29"
merged_at = "2026-07-31"

[metadata.harvest]
datapoint_type = "coding"
kind = "fix"
task_type = "investigate-and-fix"
fail_to_pass = 7
pass_to_pass = 6
overrides = []
```

**Part 3, metric output** (`metric_script_by_type`, one line of `-o`): `{"mean_coverage_all": …, "mean_fix_reward": …, "trials": 5, "trials_coding": 3, "trials_review": 2}`.

**Part 4, before:** No file. A refusal is printed only:
```
refused: no-emittable-iteration
  datapoint: pr-338
  offending: ...
  next:      ...
```

**Part 4, after:** The same output, plus `<dataset>/refusals/review/pr-338.json`:
```json
{
  "check": "no-emittable-iteration",
  "datapoint": "pr-338",
  "next": "...",
  "offending": "...",
  "verb": "capture"
}
```

```python
class RefusalRecordDict(TypedDict):
    check: str
    datapoint: str
    offending: str
    next: str
    verb: str  # "survey" | "capture" | "emit"


class RefusalLog:
    @staticmethod
    def path_for(dataset_dir: Path, datapoint_type: str, datapoint: str) -> Path: ...
    @classmethod
    def write(cls, dataset_dir: Path, datapoint_type: str, record: RefusalRecordDict) -> Path: ...
    @classmethod
    def clear(cls, dataset_dir: Path, datapoint_type: str, datapoint: str) -> None: ...
    @classmethod
    def read_all(cls, dataset_dir: Path) -> dict[str, list[RefusalRecordDict]]: ...
```

**Part 5, before:** No issue function exists in either forge module.

**Part 5, after:**
```python
# forge.py
@dataclass(frozen=True, slots=True)
class IssueFacts:
    number: int
    url: str
    title: str
    body: str  # "" when the forge gives null
    is_pull_request: bool  # True when a GitHub "issue" number is a PR


class Forge:
    @classmethod
    def fetch_issue(cls, repo: str, issue_number: int) -> IssueFacts: ...  # raises ForgeError


# gitlab.py
class GitLab:
    @classmethod
    def fetch_issue(cls, remote: ForgeRemote, issue_iid: int) -> IssueFacts: ...  # raises ForgeError
```

The candidate stores `{number, url, title, body, body_truncated}` (tech plan §8). `is_pull_request` is not stored. C-1 uses it to drop a link that is a PR.

**Migration:** None for any part. `Commit` is in memory only. Old datasets have no `refusals/` directory, and `read_all` gives empty lists.
**Backward compatibility:**
- The `survey --json` payload is byte-identical. `Commit` has one constructor call in `src/` (`survey.py:318`) and none in `tests/`.
- With the default `artifacts=()` and the review dataclasses, the output of `task_config_document` is byte-identical. `metric_script` does not change.
- stdout, stderr, and the exit codes of `capture` and `emit` do not change. The only change is one more file in the dataset on a refusal.
- `fetch_issue` is additive. No existing function changes.

## How To Verify

```bash
uv run pytest tests/integration/test_survey.py tests/unit/test_patchsplit.py
uv run pytest tests/unit/test_harbor.py tests/integration/test_emit.py tests/integration/test_dataset.py
uv run pytest tests/unit/test_refusal_log.py tests/integration/test_cli.py tests/integration/test_batch.py
uv run pytest tests/integration/test_forge.py tests/integration/test_gitlab.py
mise run test-harbor
mise run lint
mise run typecheck
mise run check
```

Manual checks:
- Part 1: `git diff tests/fixtures/gh/survey/expected.json` is empty.
- Part 2: `grep -rn "TEST_MARKERS" src/` shows the definition in `patchsplit.py` only.
- Part 3: run the script from `metric_script_by_type({"review": REWARD_KEYS, "coding": FIX_REWARD_KEYS})` with `uv run python metric.py -i rows.jsonl -o out.json` on a five-row file. Read `out.json`.
- Part 4: run `capture` on a PR with no recoverable iteration (the fixture from `RepoBuilder.build_pull_request_with_unrecoverable_iteration`). Then `cat <dataset>/refusals/review/pr-<n>.json`.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_mainline_records_author_email` | On the squash-merged fixture (`RepoBuilder.build_squash_merged_pull_request`), each `Commit` from `Survey.mainline(clone, "main")` has the email of the fixed fixture identity, lowercased. | The format string and the parse order disagree, so the email field holds the subject. |
| `test_mainline_subject_and_body_survive_the_new_field` | The fixture's squash commit has the expected `subject` and `paths`. | A shifted field index puts the email into `subject` and the subject into `body`. |
| `test_mainline_is_one_git_call` | Count calls to `GitCommandRunner.git` with `monkeypatch` during `Survey.mainline`. The count is 1. | A later change adds a per-commit `git show` for the email. |
| `test_survey_matches_golden` (existing) | The review payload is byte-identical after Parts 1 and 2. | The email leaks into the review payload, or a marker is lost in the move. |
| `test_split_puts_each_section_in_one_part` | A diff with `src/a.py`, `tests/test_a.py`, `src/b.py` gives source = a + b sections, test = test_a section, each byte-exact. | One section in both parts, or in neither part. |
| `test_split_keeps_section_order` | Interleaved source/test sections keep their original order in each part. | Sorting changes the patch, so it no longer matches `git diff`. |
| `test_split_classifies_conftest_as_test` | `tests/conftest.py` and root `conftest.py` go to `test_patch`. | The §12 FR-5 defect: `conftest.py` in the oracle patch. |
| `test_split_uses_b_path_for_rename` | A section with `rename from src/x.py` / `rename to tests/test_x.py` goes to `test_patch`. | Classification by the old name. |
| `test_split_deleted_file` | A section with `+++ /dev/null` is classified by its `diff --git` b-path. | A crash, or a deleted test file goes to source. |
| `test_split_quoted_header` | A section with `diff --git "a/caf\303\251.py" "b/caf\303\251.py"` is classified, and its bytes do not change. | A crash or a wrong path on a non-ASCII file name. |
| `test_split_rejects_preamble` | Text before the first `diff --git` raises `ValueError` that names the line. | Silent loss of bytes that are not in a section. |
| `test_split_empty_diff` | `""` gives two empty strings. | A crash on a PR with no changed files. |
| `test_is_test_path_matches_survey_rule` | Each marker gives `True` for one path. `src/app.py` gives `False`. `src/latest_value.py` gives `True` (pins the substring behavior). | A marker is lost in the move, or the rule changes silently. |
| `test_coding_config_matches_section_8` | `task_config_document` with `FixTaskOrigin`, `FixHarvestFields`, and the artifact, passed through `emit_document`, gives the §8 TOML (parse it with `tomllib` and compare tables). `validate_task_config` gives `[]`. | A field in the wrong table, or a validator that refuses our own coding config. |
| `test_coding_origin_carries_urls` | The coding `[metadata.origin]` has `pr_url` and `issue_urls`, in the same order as `issue_numbers`. The review origin table has neither key. | E-1's FR-25 scan finds no URL token, so a URL in the instruction is not found. |
| `test_review_config_bytes_unchanged` | The review config from the existing helper (`tests/unit/test_harbor.py:34`) is byte-identical to the bytes before this task (pin them in the test). | The new `artifacts` key or `datapoint_type` leaks into review tasks. |
| `test_artifact_exclude_must_be_list_of_strings` | `exclude = ".git"` (a string) and `exclude = [1]` each give a `compile.schema-invalid` violation at `artifacts/0/exclude`. | A bad `exclude` passes our validator and fails only in Harbor at run time. |
| `test_artifact_exclude_rejects_dotdot` | `exclude = ["../x"]` gives a violation. | A path escape in an artifact pattern. |
| `test_metric_by_type_separates_means` | Run the generated script with `subprocess` on rows: 2 review rows with `coverage_all = 1`, 3 coding rows with `fix_reward = 1`. `mean_coverage_all == 1.0` and `mean_fix_reward == 1.0`. | The ADR-5 defect: each mean is diluted by the other type (Harbor `Mean()` gives 0.4 and 0.6). |
| `test_metric_by_type_failed_trial_lowers_every_mean` | Add one `null` row. Both means fall. | A failed trial raises a mean because it is dropped. |
| `test_metric_by_type_is_deterministic` | Two calls with groups in a different order give the same bytes. | Dict order leaks into `metric.py`, and the dataset digest changes. |
| `test_write_then_read_all_round_trips` | One review and one coding record come back from `read_all` under their own type. | A record filed under the wrong type. |
| `test_write_replaces_older_record` | Two writes for the same datapoint leave one file with the second record. | Old reasons pile up, and the report counts one datapoint twice. |
| `test_clear_removes_and_tolerates_missing` | `clear` removes the file. A second `clear` does not raise. | A successful emit crashes because there was no earlier refusal. |
| `test_path_for_maps_slash_to_double_underscore` | `pr-338/reject` → `pr-338__reject.json`. `acme/widgets` → `acme__widgets.json`. | `/` makes a subdirectory, and `read_all` does not find the file. |
| `test_path_for_rejects_escape` | `../x`, `.hidden`, `a\b`, `""`, and the type `"other"` each raise `ValueError`. | A datapoint string writes outside `refusals/`. |
| `test_read_all_rejects_malformed_file` | A file with `[]` raises `ValueError` that names the file. | A corrupt file hides a refusal from the report. |
| `test_read_all_is_sorted` | Records written in the order 9, 1, 5 come back as 1, 5, 9. | Directory order leaks into the report (NFR-1). |
| `test_capture_refusal_writes_record` | The unrecoverable-iteration fixture: `capture` exits 3, and `refusals/review/pr-<n>.json` has `check == "no-emittable-iteration"` and `verb == "capture"`. | The wiring is missing on one path. |
| `test_emit_success_clears_earlier_refusal` | Emit a candidate that is refused, fill it, emit again. After the success, the refusal file is gone. | The report shows a refusal for a datapoint that exists. |
| `test_batch_emit_record_uses_kind_datapoint` | A refused `emit --all` item writes `pr-<n>__<kind>.json`, not `pr-<n>.json`. | The batch path and the single path write different names for the same refusal. |
| `test_fetch_issue_reads_recorded_github_issue` | The recorded payload gives the expected `IssueFacts`. `url` is the `html_url`, not the API `url`. | The API URL goes into the candidate, and the agent cannot open it. |
| `test_fetch_issue_makes_one_gh_call` | A counting stub sees exactly one `gh` call, with argv `["api", "repos/<r>/issues/<n>"]`. | NFR-4: a second call (for example, comments) is added. |
| `test_fetch_issue_null_body_is_empty_string` | A payload with `"body": null` gives `body == ""`. | `None` reaches the candidate and fails the `TypedDict` type. |
| `test_fetch_issue_flags_a_pull_request` | `pull_request_as_issue.json` gives `is_pull_request == True`. | C-1 puts a PR body into the issue list. |
| `test_fetch_issue_gh_failure_raises_forge_error` | A stub that returns exit 1 raises `ForgeError` with `gh`'s stderr in the message. | A crash with `JSONDecodeError` instead of a refusal. |
| `test_fetch_issue_non_object_raises_forge_error` | A stub that returns `[]` raises `ForgeError`. | A list is read as an issue. |
| `test_gitlab_fetch_issue_reads_recorded_issue` | `recorded/issue.json` gives the expected `IssueFacts`. | Wrong GraphQL field names (`description`, `webUrl`). |
| `test_gitlab_fetch_issue_missing_issue_raises` | A payload with `"issue": null` raises `ForgeError` that names the iid and the project. | A missing issue gives an empty issue. |
| `test_gitlab_fetch_issue_never_calls_gh` | Stub `gh` with `pytest.fail`, as `test_gitlab.py:375` does. | The GitLab path calls `gh`. |

## Acceptance Criteria

- [ ] Each `Commit` has `author_email`, lowercased. `Survey.mainline` still makes exactly one git call.
- [ ] `TEST_MARKERS` is defined in `patchsplit.py` only, and `survey.py` imports it.
- [ ] `PatchSplit.split` puts each `diff --git` section in exactly one part, byte-exact, in the original order.
- [ ] `tests/fixtures/gh/survey/expected.json` does not change.
- [ ] A coding `task.toml` from `task_config_document` matches tech plan §8, has `pr_url` and `issue_urls` in `[metadata.origin]`, and passes `validate_task_config`.
- [ ] A bad `exclude` value gives a `compile.schema-invalid` violation.
- [ ] The review `task.toml` bytes and the `metric_script` bytes do not change.
- [ ] `metric_script_by_type` gives a mean for each key over the rows of its own type only.
- [ ] `FIX_REWARD_KEYS` exists in `emit.py` with the ten ADR-5 keys.
- [ ] Each per-datapoint refusal of review `capture` and `emit` writes one file under `refusals/review/`. A later success for the same datapoint removes the file.
- [ ] `read_all` returns the records by type, sorted, and refuses a malformed file.
- [ ] stdout, stderr, and exit codes of `capture` and `emit` do not change.
- [ ] `Forge.fetch_issue` and `GitLab.fetch_issue` each make exactly one forge call. Each failure is a `ForgeError` with a message that names the issue and the cause.
- [ ] A GitHub PR number read as an issue is flagged with `is_pull_request`.
- [ ] The recorded payloads are real forge responses with unused fields removed.
- [ ] `patchsplit.py` and `refusal_log.py` import no `eval_harvest` module.
- [ ] Each part is its own commit.
- [ ] All commands in "How To Verify" pass, including `mise run test-harbor`.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6).

## Out Of Scope

- Counting `author_commits` for each PR, `git diff --numstat`, and the line counts: B-1.
- Finding the linked issue numbers of a PR (`closingIssuesReferences` or the text fallback): B-1 and C-1.
- Writing `source.patch` and `tests.patch` to disk, the 4000-character body limit, the `no-source-change` refusal, and the `forge-unavailable` item refusal: C-1.
- Logging coding refusals: B-1 (`no-harvestable-fix`), C-1 (capture), D-2 (emit).
- Writing a coding task directory, and validating the `tests/` layout of a coding task (`validate_task_layout`): D-2.
- The reward stack that writes the `fix_` keys: D-1.
- Reading the refusal log in the `dataset` report, choosing between `metric_script` and `metric_script_by_type`, and the per-type registry entries: F-1.
- A refusal for a whole repository from the review survey. The review survey has no dataset.
- Changing the marker list. It is deliberately broad (comment at `survey.py:78`). A change here changes the review survey golden output. Do it as its own decision.
- Cross-project issue references (`group/other#12`). The plan does not ask for them.
- Any change to the review survey row or payload.

## Notes & Gotchas

**Part 1 (author email):**
- **Get the fixture identity from the builder.** `tests/fixtures/repo_builder.py` pins the author identity so that SHAs are deterministic (see the docstring of `test_survey.py:5-8`). The email is `_ISOLATED_ENV["GIT_AUTHOR_EMAIL"]` (`"eval@harvest.test"`, `repo_builder.py:87`). Read it from there, or add a public constant to the builder. Do not type the email into the test a second time.
- **The record separators are load-bearing.** `_FS` (`\x1f`) and `_RS` (`\x1e`) cannot appear in a commit message (`survey.py:94-97`). An email cannot contain them either. Keep `%ae` between two `_FS` separators.
- **`%ae` vs. `%aE`.** `%ae` is the raw author email. `%aE` applies `.mailmap`. Use `%ae`, because a mailmap file in the repository can change between two runs, and NFR-1 needs the same output for the same clone.
- **A-1 also changes `survey.py`.** Start from the A-1 commits to avoid a conflict. After A-1, the line numbers in `survey.py` move a little. Search for the function names.

**Part 2 (patch splitter):**
- **The markers match substrings.** `"test_"` matches `src/latest_value.py` (`la` + `test_` + `value`). The review survey has this behavior today. Keep it, and pin it in a test, so a later change to the rule is a visible decision.
- **Quoted paths.** Git quotes a path that has non-ASCII bytes or `"`: `diff --git "a/caf\303\251.py" "b/caf\303\251.py"`. All markers are ASCII. Thus, when you remove the quotes and the `a/` and `b/` prefixes, you classify the path correctly without decoding the escapes. Do not decode them.
- **Binary files.** `git diff` without `--binary` writes `Binary files a/x and b/x differ` inside the section. Copy the section as it is. C-1 decides what to do with it.
- **C-2 extends this module later.** It adds `PatchSplit.file_sections` for `show --path`. Keep the section cutter in its own static method, so C-2 can reuse it.

**Part 3 (harbor.py):**
- **`exclude` already passes today, silently.** `_artifact_fields` drops every key that it does not know. So a test that only says "a coding config validates" proves nothing about `exclude`. The two refusal tests are the guard. Write them first and watch them fail (CLAUDE.md: a guard that nobody watched fail is decorative).
- **Harbor ignores `metric.py` on the local path.** The docstring of `metric_script` (line 670) says that Harbor runs `Mean()` for `--registry-path`. The per-type script is still correct to ship: it states the aggregation. The per-type registry entries (F-1) give correct means in `harbor run`.
- **Section order.** `task_config_document` builds the dict in §8 order, and `emit_document` does not sort (docstring at line 193). Put `artifacts` where §8 shows it, or the golden bytes of D-2 change later.
- **Mypy and unions.** `_origin_table` and `_harvest_table` are static methods. Use `isinstance` on the dataclass to choose the branch. Do not add a `type` string field to tell them apart.
- **The verifier network belongs to D-2.** Do not add `verifier_allowed_hosts` or `verifier_env` here, even if a test looks easier with them. Two tasks that add the same keyword parameter make a merge conflict and two docstrings.
- **`TaskOrigin` has no coding fields.** It has no `pr_url`, `issue_numbers`, `issue_urls`, or `merge_commit`. That is why `FixTaskOrigin` is a new dataclass and not new optional fields on `TaskOrigin`. New optional fields would change the review bytes.

**Part 4 (refusal log):**
- **Two datapoint names for one PR.** `capture` uses `pr-<n>`. `emit` uses `pr-<n>/<kind>` (`emit.py:265`). They are two different datapoints in the log, and each verb clears only its own. A capture success does not clear an emit refusal.
- **The emit batch key is not the datapoint.** `_emit_item` uses `candidate_path.stem` (`pr-<n>`) as the batch key (line 1534), and `ItemResult.refuse` copies the key into `datapoint` (`batch.py:81`). Use `refusal.datapoint` from the `EmitRefusalError` for the log.
- **A forge failure in `_capture_single` is not a refusal.** It prints and returns exit 5 (line 891). Do not log it. In a batch, the same failure is the item refusal `forge-unavailable`, and you do log it.
- **The `dataset` verb must not trip on the new directory.** `Dataset._read_task_records` (`dataset.py:158`) reads only `tasks/`. `emit --all` globs only `candidates/*.json` (`cli.py:1497`). Confirm both with a test run on a dataset that has a `refusals/` directory.

**Part 5 (issue fetch):**
- **GitHub returns PRs from the issues endpoint.** `repos/<r>/issues/<n>` answers for a PR number too, with a `pull_request` key. "Fixes #12" can name a PR. That is why `is_pull_request` exists.
- **`url` vs. `html_url`.** The GitHub issue object has both. `url` is the API URL. Use `html_url`. The leakage scan (E-1) also uses this URL as a token, so it must be the URL that a person writes.
- **GitLab `iid` is a string in GraphQL.** Pass it with `-f` (string), not `-F`. `CAPTURE_QUERY` does the same at line 268. Convert the result with `int(...)`.
- **The GitLab field names come from the GitLab GraphQL `Issue` type.** `[TODO: verify]` `title`, `description`, and `webUrl` against the recorded response before you write the mapping.
- **The `--repo` form of `gh`.** `GitCommandRunner.gh(argv, repo=...)` appends `--repo`. `gh api` does not take `--repo`. Put the repo in the path, as `Forge.capture` does at line 129.

**All parts:**
- §3 constraint: one stateless class for each file, and `TypedDict` or frozen dataclasses (not Pydantic) on the CLI path (`CODING_STANDARDS.md` §1).
- Every git and forge call goes through `GitCommandRunner` (§3 constraint). Do not call `subprocess` directly.
- `CODING_STANDARDS.md` §7: write each test first and watch it fail.

## Dependencies

**Blocked by:** [A-1](A-1-fix-the-two-existing-survey-and-registry-defects.md) (Parts 1 and 2 change `survey.py` after A-1's survey fix). Parts 3, 4, and 5 do not need A-1, and you can start them now.
**Blocks:** [B-1](B-1-add-the-paged-coding-survey.md), [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md), [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md), [E-1](E-1-add-the-static-coding-checks-to-verify.md), [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)
