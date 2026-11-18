---
id: C-1
title: "[Capture] Add the coding candidate and capture --type coding"
summary: "Make the coding candidate file and its checks, and let `capture --type coding` write it from one PR or a survey."
feature: coding-eval-harvest
workstream: Capture
status: done
complexity: L
merges: [C-1, C-2]
implements: [FR-12, FR-13, FR-14, FR-16, FR-17, FR-21, FR-22, FR-33, NFR-2, NFR-4]
user_story: US-3
blocked_by: [A-2, B-1]
blocks: [C-2, D-2, F-1]
---

# C-1: [Capture] Add the coding candidate and capture --type coding

**In short:** Make a new module that defines the coding candidate file `candidates/fix-<n>.json`.
The module writes the file and the two fix patches, and it checks the facts and the agent's slots.
Then connect it to `capture --type coding`, for one PR or for the ranked PRs of a coding survey.

## Context

A coding datapoint starts as a candidate file. `capture` fills the facts, and the agent fills the
judgment slots. The review candidate (`candidate.py`) has a pinned schema. We must not change it
(tech plan §3, Compatibility). Thus the coding candidate needs its own module. This task also
connects that module to the `capture` verb. It adds the one forge call for the PR and one call for
each linked issue. Without this task, the agent cannot get the material for a coding datapoint
(US-3). The agent then must read the history by hand, and that is the problem that the north star
removes. Also, `brief` and `annotate` (C-2) have nothing to read, and `emit --kind fix` (D-2) has
no validators to call.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-12, FR-13, FR-14, FR-16, FR-17, FR-21, FR-22 (the `merge_date` fact), FR-33 (capture part), NFR-2, NFR-4  ·  **User story:** US-3, US-4, US-5, US-7

## Design References

- **Tech plan:** `../tech-plan.md` §8, the `candidates/fix-<n>.json` table: every field, its type, and who writes it. This task implements that table exactly.
- **Tech plan:** §8, "Agent-written files": `candidates/fix-<n>/hidden/<repo_path>`, `validation_spec.toml`, and the lockfile.
- **Tech plan:** §2.1, FR-12, FR-13, FR-14, FR-16, FR-17, FR-21: the acceptance criteria for the tests below.
- **Tech plan:** §2.2, NFR-4: one PR call plus one call for each linked issue.
- **Tech plan:** §6, module table, row `src/eval_harvest/fix_candidate.py`.
- **Tech plan:** §7.2, "Coding capture" diagram: the sequence of calls. The base is the first parent of the merge commit, and the diff `base..merge` is split.
- **Tech plan:** §7.3, "The forge cannot give the issue during capture": the batch continues after a `forge-unavailable` item.
- **Tech plan:** §9, the `capture` row: the flags, the output, and each refusal with its exit code.
- **Tech plan:** §4, the risk row "`gh` or `glab` does not give linked issues in the expected field": the text fallback.
- **ADR-1** in §11: coding is a second type on the same verb, with its own candidate module. Do not add a new verb. Do not merge the schema into `candidate.py`.
- **ADR-3** in §11: the agent writes the environment recipe. The CLI only checks its form.
- **PRD:** `../prd.md` §6, US-3 ("The CLI refuses a candidate if it cannot recover the base or the fix. The CLI tells why.") and US-4.

## What To Build

The work has three parts. Each part can be a separate commit, in this order. Each commit must pass
`mise run check`.

### Part 1: The `FixCandidate` module

1. Create `src/eval_harvest/fix_candidate.py`. It has one class, `FixCandidate`, with class methods and static methods only. Follow the layout of `candidate.py`: module docstring, `Final` constants, `TypedDict` schemas, then the class.
2. Import `Violation` from `eval_harvest.candidate`. Do not make a second violation type. Import `PatchSplit` from `eval_harvest.patchsplit` (A-2). Import `GitCommandRunner` from `eval_harvest.gitcmd`.
3. Add these constants:
   - `BODY_CHAR_LIMIT: Final = 4000`
   - `TOOLING_NOTE_MAX_CHARS: Final = 200`
   - `HIDDEN_TEST_KINDS: Final = ("fail_to_pass", "pass_to_pass")`
   - `HIDDEN_TEST_ORIGINS: Final = ("pr", "agent")`
   - `RUBRIC_CRITERION_KINDS: Final = ("fail_to_pass", "pass_to_pass")`
   - `ALLOWED_BASE_IMAGE_PREFIX: Final = "public.ecr.aws/"` (the same registry rule as `_AGENT_BASE_IMAGE` in `emit.py:84`)
4. Add the `TypedDict` schemas. Keep the field order of tech plan §8, because `json.dumps(sort_keys=False)` writes the insertion order:
   - `FixIssueDict`: `number: int`, `url: str`, `title: str`, `body: str`, `body_truncated: bool`
   - `HiddenTestDict`: `repo_path: str`, `name: str`, `kind: str`, `origin: str`, `evidence: str`
   - `RubricCriterionDict`: `id: str`, `kind: str`, `text: str`
   - `EnvironmentDict`: `base_image: str`, `setup_steps: list[str]`, `lockfile: str`, `test_command: str`, `rationale: str`
   - `FixCandidateDict`: `datapoint_type`, `task_type`, `repo`, `pr_number`, `pr_url`, `pr_title`, `pr_body`, `pr_body_truncated`, `issues`, `merge_commit`, `base_commit`, `merged_at`, `merge_date`, `base_commit_date`, `source_patch_path`, `test_patch_path`, `source_paths`, `test_paths`, `source_lines_added`, `source_lines_deleted`, `author_commits`, `instruction`, `instruction_rationale`, `tooling_note`, `hidden_tests`, `rubric_criteria`, `environment`.
5. Add the input bundle as frozen dataclasses (`@dataclass(frozen=True, slots=True)`, as `PullRequestFacts` in `forge.py:101`):
   - `FixIssue`: `number: int`, `url: str`, `title: str`, `body: str`
   - `FixFacts`: `pr_number: int`, `pr_url: str`, `pr_title: str`, `pr_body: str`, `merge_commit: str`, `merged_at: str`, `issues: tuple[FixIssue, ...]`, `author_commits: int`
   Part 3 builds `FixFacts` from the forge. This module makes no forge call.
6. Add `FixCaptureError(Exception)` with two attributes: `check: str` and `offending: str`. `write_to_dataset` raises it for the two capture refusals. Part 3 turns it into the four-field refusal.
7. Add the path helpers. They are pure functions of the PR number, as `patch_relpath` in `candidate.py:178` is:
   - `FixCandidate.candidate_relpath(n)` → `"candidates/fix-<n>.json"`
   - `FixCandidate.workdir_relpath(n)` → `"candidates/fix-<n>"` (the directory for hidden tests, the validation spec, and the lockfile)
   - `FixCandidate.source_patch_relpath(n)` → `"patches/fix-<n>/source.patch"`
   - `FixCandidate.test_patch_relpath(n)` → `"patches/fix-<n>/tests.patch"`
8. Add `FixCandidate.write_to_dataset(facts: FixFacts, *, repo: str, clone: Path, dataset_dir: Path) -> Path`. It does these steps in this order:
   1. Make sure that the merge commit is in the clone: `GitCommandRunner.git(clone, "rev-parse", "--verify", "--quiet", f"{merge}^{{commit}}")`. If the exit code is not 0, or `facts.merge_commit` is empty, raise `FixCaptureError("fix-base-unrecoverable", ...)`. The `offending` text names the SHA and says that the clone does not have it.
   2. Find the parents: `git rev-list --parents -n 1 <merge>`. The output is `<merge> <parent1> [<parent2> …]`. The base is `<parent1>`. If there is no parent (a root commit), raise `fix-base-unrecoverable`.
   3. Read the base date: `git show -s --format=%cs <base>`. This gives `YYYY-MM-DD`.
   4. Read the diff: `git diff <base> <merge>`. Split it with `PatchSplit.split`.
   5. If `source_patch` is empty, raise `FixCaptureError("no-source-change", ...)`. Write no file.
   6. Read the line counts: `git diff --numstat -z <base> <merge>`. Use `-z`, as B-1 does, so a path with special characters is not quoted. A record is `<added>\t<deleted>\t<path>\0`. A rename is `<added>\t<deleted>\t\0<old>\0<new>\0`; use `<new>`. Sum `added` and `deleted` only for the paths where `PatchSplit.is_test_path(path)` is false. A binary file shows `-\t-\t<path>`. Count it as 0 lines, and keep its path in `source_paths` or `test_paths`.
   7. Write `source.patch` and `tests.patch` to the two paths from step 7, newline-terminated (see Notes). Write `tests.patch` also when it is empty. Then a reader never needs to check if the file exists.
   8. Build the dict with `scaffold` (step 9) and write it with `dump` (step 11). Return the candidate path.
   Make no `git fetch` and no forge call. All calls go through `GitCommandRunner.git` (tech plan §3).
9. Add `FixCandidate.scaffold(facts, *, repo, base_commit, base_commit_date, source_paths, test_paths, lines_added, lines_deleted) -> FixCandidateDict`:
   - `datapoint_type = "coding"`, `task_type = "investigate-and-fix"`.
   - `pr_body` and each issue `body`: cut to `BODY_CHAR_LIMIT` characters. Set the `*_truncated` flag to `True` only when the CLI cut the text.
   - `issues`: sorted by `number`. Empty list when no issue is linked.
   - `merge_date = merged_at[:10]`. `merged_at` is ISO 8601 from the forge, for example `2026-07-31T12:00:00Z`.
   - `source_paths` and `test_paths`: sorted, from the numstat paths.
   - Judgment slots: `instruction = ""`, `instruction_rationale = ""`, `tooling_note = ""`, `hidden_tests = []`, `rubric_criteria = []`, and `environment = {"base_image": "", "setup_steps": [], "lockfile": "", "test_command": "", "rationale": ""}`.
10. Add `FixCandidate.validate_facts(candidate) -> list[Violation]`. It never raises. It checks: `datapoint_type == "coding"`, `repo`, `pr_number > 0`, `pr_url`, `merge_commit`, `base_commit`, `merge_date` (10 characters), `source_patch_path`, and `source_paths` not empty. Use the code `"fix.facts-missing"`.
11. Add `dumps`, `dump`, and `load`. Copy the body of `Candidate.dumps` (`candidate.py:521`): `json.dumps(candidate, indent=2, ensure_ascii=False, sort_keys=False) + "\n"`, UTF-8, LF. Add `FixCandidate.datapoint_type_of(path: Path) -> str`. It reads the JSON and returns `candidate.get("datapoint_type", "review")`. C-2 uses it to dispatch `brief`, `show`, and `annotate`.
12. Add `FixCandidate.validate_filled(candidate) -> list[Violation]`. It never raises. It returns one `"fix.slot-empty"` violation for each empty slot: `instruction`, `instruction_rationale`, `tooling_note`, `hidden_tests` (empty list), `rubric_criteria` (empty list), and each of `environment.base_image`, `environment.setup_steps`, `environment.lockfile`, `environment.test_command`, and `environment.rationale`. It also checks the values:
    - `tooling_note`: one line, at most `TOOLING_NOTE_MAX_CHARS` characters. Code `"fix.slot-invalid"`.
    - each hidden test: `kind` in `HIDDEN_TEST_KINDS`, `origin` in `HIDDEN_TEST_ORIGINS`, `evidence` not empty. Code `"fix.hidden-test-invalid"`.
    - `hidden_tests` has at least one `fail_to_pass` entry. Code `"fix.no-fail-to-pass-test"` (the `no-fail-to-pass-test` refusal in §9).
    - each rubric criterion: `id` not empty and unique, `kind` in `RUBRIC_CRITERION_KINDS`, `text` not empty. Code `"fix.rubric-criterion-invalid"`.
    - Use `"fix.slot-empty"` only for a slot that is still empty. C-2 (`annotate`) treats `fix.slot-empty` and `fix.no-fail-to-pass-test` as "not filled yet", and every other code as a wrong value.
13. Add `FixCandidate.validate_hidden_test_files(candidate, dataset_dir) -> list[Violation]`. For each entry, the file `dataset_dir / workdir_relpath(n) / "hidden" / repo_path` must exist. Code `"fix.hidden-test-file-missing"`. Refuse a `repo_path` that is absolute or that contains a `..` part, before you look at the disk. Code `"fix.hidden-test-invalid"`.
14. Add `FixCandidate.validate_recipe(candidate, dataset_dir) -> list[Violation]`. Code `"fix.environment-recipe-invalid"` for each of these:
    - A setup step that contains `"\n"` or `"\r"`, or that ends with `"\\"` after you remove trailing spaces.
    - `base_image` that does not start with `ALLOWED_BASE_IMAGE_PREFIX`.
    - `lockfile` that is not a plain file name (it contains `/`, `\`, or is `.` or `..`), or a lockfile that is not present at `dataset_dir / workdir_relpath(n) / lockfile`.
    - `test_command` that contains `"\n"` or `"\r"`.
15. In `candidate.py`, rename `_newline_terminated` (line 188) to `newline_terminated` and update its one caller in `Candidate._materialize_patches` (line 431). No byte change. Do not change `CandidateDict` or any review validator.
16. Extend the shared fixture `RepoBuilder.build_fix_history` (B-1 creates it in `tests/fixtures/repo_builder.py`). Do not add a second builder. The B-1 fixture already has a clean squash fix, a true-merge PR (`git merge --no-ff`), and a tests-only fix (the `no-source-change` scenario). Use those scenarios. Add a scenario only when a test below needs it and B-1 does not have it: a fix that deletes a source file, and a fix with interleaved source and test files. Add each new scenario at the end, so the B-1 scenario SHAs and the B-1 golden output do not change. The root-commit test builds its own small repository inside the test.

### Part 2: The forge read for the PR

17. Add `Forge.fetch_fix_pull_request(repo: str, pr_number: int) -> dict[str, Any]` next to `Forge.capture`. It runs `GitCommandRunner.gh(["pr", "view", str(pr_number), "--json", "number,title,body,url,mergeCommit,mergedAt,headRefName,closingIssuesReferences"], repo=repo)`. A non-zero exit or a body that is not a JSON object raises `ForgeError`. `[TODO: verify]` that `gh pr view --json closingIssuesReferences` exists in the pinned `gh` version.
18. Add `GitLab.fetch_fix_merge_request(remote: ForgeRemote, iid: int) -> dict[str, Any]` next to `GitLab.capture`. Add a constant `FIX_CAPTURE_QUERY` next to `CAPTURE_QUERY` (`gitlab.py:70`) with `iid title description webUrl state mergedAt mergeCommitSha squashOnMerge diffHeadSha sourceBranch`. Run it with `GitLab._run_graphql` (`gitlab.py:279`). Reshape it to the same keys as the GitHub dict (see Schemas & Contracts). Use `GitLab._integration_revision` (`gitlab.py:212`) for the merge commit. `[TODO: verify]` the GitLab GraphQL field for closing issues. Until it is verified, give an empty list and use the text fallback (step 20.2).

### Part 3: The CLI wiring

19. In `Cli.add_capture_arguments` (`cli.py:385`), add `--type` with `choices=("review", "coding")` and `default="review"`. Help text: "the datapoint type: review (default) or coding (a merged fix becomes a coding task)". Also add `--branch` (`default="main"`, `metavar="<name>"`), with the same meaning as `survey --branch` (`cli.py:371`). Only `--type coding` reads it (step 20.4).
20. Add `Cli._fix_facts(remote: ForgeRemote, pr_number: int, clone: Path) -> FixFacts`. It is the one place that branches on the forge, as `_capture_facts` (`cli.py:1020`) does:
    1. Read the PR with the function from Part 2.
    2. Find the linked issue numbers: the numbers in `closing_issue_numbers`. If that list is empty, use `Survey.issue_candidates(title, body, headRefName)` (`survey.py:542`) and convert each value to `int`. Remove duplicates. Sort.
    3. For each number, call `Forge.fetch_issue(repo, issue_number)` or `GitLab.fetch_issue(remote, issue_iid)` (A-2). Both return `IssueFacts` (`number`, `url`, `title`, `body`, `is_pull_request`). Skip an entry with `is_pull_request = True`: a `#n` reference to another PR is not an issue. Map each other entry to `FixIssue`.
    4. Get `author_commits`. In batch mode, and in single mode when the PR is a `ranked` row of `<dataset>/survey/fix-survey.json`, take `author_commits` from that row. Then the batch makes no `git log` for each PR. Otherwise compute it: move the author logic of `FixSurvey.survey_one` (B-1, the "Author commits" step) into a new static method `FixSurvey.author_commits(clone: Path, commits: list[Commit], integration: str, parents: list[str]) -> int` with no change in behavior, make `survey_one` call it, and call it here with `Survey.mainline(clone, arguments.branch)` (`survey.py:295`). The B-1 tests must stay green without change.
    5. Return `FixFacts`. Make no `git fetch`. The review capture fetches the pull head (`Forge.fetch_pull_request_head`, `forge.py:135`). The coding capture does not need it, because the merge commit is on the mainline of the clone.
21. In `Cli.handle_capture` (`cli.py:841`), after the `<pr>`/`--from-survey` usage guard and before `_load_risk_map_or_refuse`, add: `if arguments.type == "coding": return cls._handle_capture_coding(arguments)`. A coding capture does not need `risk-map.toml` (§9: "No risk map is necessary for coding"). The review path below does not change.
22. Add `Cli._handle_capture_coding(arguments) -> int`. Resolve the remote with `_forge_remote_or_report(clone, arguments, verb="capture")` (`cli.py:1729`). Then call `_capture_coding_single` or `_capture_coding_batch`.
23. Add `Cli._capture_coding_single(pr_number, *, remote, clone, dataset, json_mode) -> int`:
    - A `ForgeError` gives `cls.refuse(check="forge-unavailable", datapoint=f"fix-{n}", offending=str(error), next_="the forge call failed; check `gh auth status` (or `glab auth status`) and run capture again", exit_code=ExitCode.RUNTIME_UNAVAILABLE)`.
    - Call `FixCandidate.write_to_dataset(facts, repo=remote.project_path, clone=clone, dataset_dir=dataset)`.
    - `FixCaptureError("fix-base-unrecoverable")` gives exit 3, `next_ = f"run `git fetch {remote.git_remote}` in {clone} so the clone has the merge commit, then capture again; or pick another PR from `survey --type coding`"`.
    - `FixCaptureError("no-source-change")` gives exit 3, `next_ = "this fix changes only test files; there is no source change for an agent to make — pick another PR from `survey --type coding`"`.
    - On success, print the result (step 26). Exit 0.
24. Add `Cli._capture_coding_batch(from_survey, arguments, *, remote, clone, dataset, json_mode) -> int` and its per-item function `_capture_coding_item`:
    - `--from-survey` must name `<dataset>/survey/fix-survey.json` (compare the resolved paths). Load it with `FixSurvey.read_cache(dataset)` (B-1). A different path, or a `FixSurveyCacheError` (for example a review `survey --json` payload has no `ranked` list), is a usage error, exit 2, with the message "`--type coding --from-survey` needs `<dataset>/survey/fix-survey.json` from `survey --type coding`".
    - Targets: the `ranked` rows in rank order. Never an `excluded` row.
    - `--pr`: validate against the numbers of `ranked` and `excluded` together. An unknown number refuses `unknown-pr` (exit 3). A number that is only in `excluded` is not a target.
    - `--limit`: keep the first *n* targets.
    - No targets: refuse `no-clean-pr` (exit 3), `next_ = "run `eval-harvest survey --type coding --dataset <dir> --excluded` to see why each PR is excluded, or widen --pr/--limit"`.
    - Run `Batch.run(...)` (`batch.py`), as `_capture_batch` does (`cli.py:912`). Each item gives `ItemResult.success` or `ItemResult.refuse` with `forge-unavailable`, `fix-base-unrecoverable`, or `no-source-change`. The batch continues after a refused item. Exit 0 only when every item succeeded. Otherwise exit 3.
25. Write each coding refusal to the refusal log: `RefusalLog.write(dataset, "coding", {"check": …, "datapoint": f"fix-{n}", "offending": …, "next": …, "verb": "capture"})` (A-2). This applies to single mode and to each refused batch item. On a successful capture, call `RefusalLog.clear(dataset, "coding", f"fix-{n}")`. This is the same pattern as the review wiring that A-2 adds to `_capture_single` and `_capture_item`.
26. Add `Cli._report_fix_capture(candidate_path, candidate, *, json_mode)`:
    - Human: the candidate path, then `slots to fill:` with one line for each entry of `FixCandidate.validate_filled(candidate)`, then `next: eval-harvest brief <candidate_path>`.
    - `--json`: `{"candidate": str(path), "pr_number": n, "base_commit": …, "merge_commit": …, "source_paths": […], "test_paths": […], "issues": [numbers], "unfilled": [pointers], "next": "eval-harvest brief <path>"}`.
27. Do not change `_capture_single`, `_capture_batch`, `_capture_item`, or `_write_capture_candidate`. The review bytes must not change (NFR-9).

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/fix_candidate.py` | Create | Part 1. Mirror the layout of `candidate.py`. |
| `src/eval_harvest/candidate.py` | Modify | Part 1. Rename `_newline_terminated` (line 188) to `newline_terminated` and update its caller (line 431). No byte change. |
| `src/eval_harvest/forge.py` | Modify | Part 2. Add `Forge.fetch_fix_pull_request`. |
| `src/eval_harvest/gitlab.py` | Modify | Part 2. Add `FIX_CAPTURE_QUERY` and `GitLab.fetch_fix_merge_request`. |
| `src/eval_harvest/fix_survey.py` | Modify | Part 3. Extract `FixSurvey.author_commits` from `survey_one` (B-1 created the file). No behavior change. |
| `src/eval_harvest/cli.py` | Modify | Part 3. `--type` and `--branch` in `add_capture_arguments` (line 385). Dispatch in `handle_capture` (line 841). New `_handle_capture_coding`, `_capture_coding_single`, `_capture_coding_batch`, `_capture_coding_item`, `_fix_facts`, `_report_fix_capture`. |
| `tests/fixtures/repo_builder.py` | Modify | Part 1. Extend `RepoBuilder.build_fix_history` (B-1) only with the scenarios that step 16 names. |
| `tests/fixtures/gh/fix_capture/` | Create | Part 3. Recorded `gh pr view` payload and two `gh issue` payloads. |
| `tests/fixtures/glab/recorded/` | Modify | Part 2. Add a recorded `FIX_CAPTURE_QUERY` response. |
| `tests/integration/test_fix_candidate.py` | Create | Part 1. Mirror `tests/integration/test_candidate.py`: real git on the fixture repository. |
| `tests/integration/test_capture_coding.py` | Create | Part 3. CLI tests. Stub `GitCommandRunner.gh` and `glab` as `tests/integration/test_forge.py:151` does. Real git on `RepoBuilder.build_fix_history`. |

## Schemas & Contracts

**Before:** There is no coding candidate. The review candidate is `CandidateDict` (`candidate.py:110`):
```python
class CandidateDict(TypedDict):
    repo: str
    pr_number: int
    pr_url: str
    iterations: list[IterationDict]
    review_verdicts: list[VerdictDict]
    comments: list[CommentDict]
    findings: list[FindingDict]
    change_risk: ChangeRiskDict
    rubric_version: str
```

`capture` usage (`cli.py:386`):
```
capture (<pr> | --from-survey <file> [--limit <n>] [--pr <n>…]) --clone <dir> [--dataset <dir>] [--forge auto|github|gitlab]
```

**After:** `CandidateDict` does not change. New file `candidates/fix-<n>.json`, right after `capture`:
```json
{
  "datapoint_type": "coding",
  "task_type": "investigate-and-fix",
  "repo": "strands-agents/evals",
  "pr_number": 338,
  "pr_url": "https://github.com/strands-agents/evals/pull/338",
  "pr_title": "…",
  "pr_body": "…(at most 4000 characters)",
  "pr_body_truncated": false,
  "issues": [{"number": 337, "url": "…", "title": "…", "body": "…", "body_truncated": false}],
  "merge_commit": "cc0a391…",
  "base_commit": "0bf1252…",
  "merged_at": "2026-07-31T12:00:00Z",
  "merge_date": "2026-07-31",
  "base_commit_date": "2026-07-29",
  "source_patch_path": "patches/fix-338/source.patch",
  "test_patch_path": "patches/fix-338/tests.patch",
  "source_paths": ["src/strands_evals/extractors/trace_extractor.py"],
  "test_paths": ["tests/test_trace_extractor.py"],
  "source_lines_added": 12,
  "source_lines_deleted": 3,
  "author_commits": 41,
  "instruction": "",
  "instruction_rationale": "",
  "tooling_note": "",
  "hidden_tests": [],
  "rubric_criteria": [],
  "environment": {"base_image": "", "setup_steps": [], "lockfile": "", "test_command": "", "rationale": ""}
}
```

A filled `hidden_tests` entry: `{"repo_path": "tests/test_x.py", "name": "test_f2p_same_turn", "kind": "fail_to_pass", "origin": "pr", "evidence": "…"}`.

The forge dict that `fetch_fix_pull_request` and `fetch_fix_merge_request` both return:
```python
{
    "number": int,
    "title": str,
    "body": str,
    "url": str,
    "merge_commit": str,
    "merged_at": str,
    "head_ref_name": str,
    "closing_issue_numbers": list[int],
}
```

New `capture` usage:
```
capture (<pr> | --from-survey <file> [--limit <n>] [--pr <n>…]) --clone <dir> [--dataset <dir>] [--forge …] [--type review|coding] [--branch <name>]
```

With `--type coding`:

| Outcome | Exit | `check` |
|---------|------|---------|
| Candidate written | 0 | — |
| Merge commit or its first parent not in the clone | 3 | `fix-base-unrecoverable` |
| The fix changes only test files | 3 | `no-source-change` |
| `--pr` names a number that is not in the cache | 3 | `unknown-pr` |
| The batch has no target | 3 | `no-clean-pr` |
| Forge call failed, single mode | 5 | `forge-unavailable` |
| Forge call failed, batch item | item refusal, batch exit 3 | `forge-unavailable` |
| `--from-survey` file is not a coding cache | 2 | usage |

**Migration:** None. This is a new file type. Existing review candidates have no `datapoint_type`, and `datapoint_type_of` returns `"review"` for them.
**Backward compatibility:** `--type` defaults to `review`. Every existing `capture` command gives the same bytes. The review candidate bytes do not change. The rename in `candidate.py` changes no output.

## How To Verify

```bash
uv run pytest tests/integration/test_fix_candidate.py tests/integration/test_capture_coding.py
uv run pytest tests/integration/test_candidate.py tests/integration/test_batch.py tests/integration/test_fix_survey.py
mise run lint
mise run typecheck
mise run check
```

Manual checks:
1. In a test, write one candidate two times into two directories. `cmp` the two `fix-<n>.json` files and the two `source.patch` files. They must be the same.
2. On a fixture, run `eval-harvest capture <n> --type coding --clone <clone> --dataset <ds>`. Make sure that `<ds>/candidates/fix-<n>.json`, `<ds>/patches/fix-<n>/source.patch`, and `<ds>/patches/fix-<n>/tests.patch` exist, and that stdout ends with `next: eval-harvest brief …`.

## Tests To Write

Use `RepoBuilder.build_fix_history` (B-1, extended in step 16).

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_write_splits_fix_into_two_patches` | The sections of `source.patch` and `tests.patch` together are the sections of `git diff base merge`. Each path is in one patch only. | A path in two patches, or a lost path (FR-12). |
| `test_base_is_first_parent_of_true_merge` | For the true-merge PR, `base_commit` equals `git rev-parse <merge>^1`. | The base is taken from the merge-base or from the second parent (§12, FR-12 row). |
| `test_base_is_parent_of_squash_commit` | For a squash commit, `base_commit` is its only parent. | A crash on a single-parent commit. |
| `test_scaffold_leaves_every_slot_empty` | After `write_to_dataset`, `validate_filled` lists every slot from step 12, and each slot value is empty. | The CLI fills a judgment slot (FR-16, FR-19). |
| `test_body_truncated_at_4000` | An issue body with 10 000 characters gives 4000 characters and `body_truncated = true`. A body with 4000 characters gives `false`. | A long body fills the brief, or an off-by-one in the limit (FR-13). |
| `test_issues_sorted_by_number` | Issues 12, 3, 7 are written as 3, 7, 12. | Output that depends on forge order (NFR-1). |
| `test_refuses_merge_commit_not_in_clone` | An unknown SHA raises `FixCaptureError` with `check == "fix-base-unrecoverable"`. No file is written. | A crash, or a candidate with an empty base (FR-14). |
| `test_refuses_root_commit` | A merge commit with no parent raises `fix-base-unrecoverable`. | An index error on `rev-list` output. |
| `test_refuses_fix_with_only_test_changes` | The tests-only scenario raises `no-source-change`. No file is written. | An empty oracle patch (FR-14). |
| `test_source_line_counts_exclude_tests` | `source_lines_added` and `source_lines_deleted` count only source paths. | Test lines are counted as source lines. |
| `test_merge_date_from_merged_at` | `merged_at = "2026-07-31T23:59:59Z"` gives `merge_date = "2026-07-31"`. | A wall-clock date or a time-zone shift (FR-22). |
| `test_write_is_byte_identical_twice` | Two writes give the same candidate and patch bytes. | Unordered sets in the output (NFR-1). |
| `test_patches_are_newline_terminated` | Both patch files end with `\n`. | `git apply` fails with `corrupt patch at line N`. |
| `test_hidden_test_kind_and_evidence_checked` | An entry with `kind = "flaky"`, or with empty `evidence`, gives `fix.hidden-test-invalid`. | An unknown kind passes (FR-17). |
| `test_no_fail_to_pass_test_is_reported` | Only `pass_to_pass` entries give `fix.no-fail-to-pass-test`. | A task that cannot fail at the base is emitted. |
| `test_hidden_test_file_missing` | An entry with no file in `candidates/fix-<n>/hidden/` gives `fix.hidden-test-file-missing`. | `emit` copies a file that does not exist (FR-17). |
| `test_hidden_test_path_traversal_refused` | `repo_path = "../../etc/x"` gives `fix.hidden-test-invalid`, and no file outside the dataset is read. | A path outside the candidate directory. |
| `test_recipe_step_with_line_break_refused` | `"pip install x\nCOPY / /leak"` gives `fix.environment-recipe-invalid`. | Dockerfile injection (FR-21, NFR-7). |
| `test_recipe_trailing_backslash_refused` | `"pip install x \\"` gives `fix.environment-recipe-invalid`. | A continuation line joins the next Dockerfile line (FR-21). |
| `test_recipe_base_image_must_be_ecr_public` | `"python:3.12"` is refused. `"public.ecr.aws/docker/library/python:3.12-slim"` passes. | A Docker Hub image (FR-21). |
| `test_recipe_lockfile_missing_or_not_a_name` | A missing lockfile, and `lockfile = "../x.lock"`, are refused. | Emit fails late, or reads outside the candidate. |
| `test_validators_never_raise` | `validate_facts` and `validate_filled` on `{}` return violations and do not raise. | A `KeyError` stops `emit` at the first problem (ADR-2 of pr-eval-harvest). |
| `test_capture_coding_writes_candidate_and_patches` | Exit 0. The three files exist. Stdout names `brief`. | The dispatch goes to the review path. |
| `test_capture_coding_needs_no_risk_map` | A dataset without `risk-map.toml` captures with exit 0. | The coding path runs the review risk-map guard. |
| `test_capture_coding_forge_call_count` | A counting `gh` stub sees one `pr view` call plus one call for each of the two linked issues, and no `api …/reviews`, `…/comments`, or `…/timeline` call. | Extra forge calls (NFR-4). |
| `test_capture_coding_makes_no_git_fetch` | A `git_with_stderr` stub that fails on `fetch` does not fail the capture. | A pull-head fetch is copied from the review path. |
| `test_capture_coding_issue_fallback_from_text` | `closingIssuesReferences` is empty and the body has "Fixes #12". The candidate has issue 12. | Issues are lost when the forge field is empty (§4 risk). |
| `test_capture_coding_refuses_base_unrecoverable` | A PR whose merge commit is not in the clone exits 3 with `fix-base-unrecoverable`, all four fields, and a `git fetch` command in `next`. | A crash, or a refusal without a next step (FR-14, FR-30). |
| `test_capture_coding_refuses_no_source_change` | A tests-only fix exits 3 with `no-source-change`. No candidate is written. | An empty oracle patch reaches `emit`. |
| `test_capture_coding_forge_unavailable_single` | `gh` exits 1. The capture exits 5 with `forge-unavailable`. | A forge failure is reported as a refusal of the PR. |
| `test_capture_coding_batch_continues_after_issue_failure` | Three ranked PRs; the issue call fails for the second one. The first and third candidates exist. The summary names `forge-unavailable` for the second. Exit 3. | One forge failure stops the batch (§7.3). |
| `test_capture_coding_batch_never_takes_excluded` | A cache with two ranked and one excluded PR captures only the two ranked PRs. | An excluded PR is captured. |
| `test_capture_coding_batch_unknown_pr` | `--pr 9999` refuses `unknown-pr` before a forge call. | A typo causes a forge call. |
| `test_capture_coding_batch_rejects_review_payload` | A review `survey --json` file with `--type coding` exits 2. | A review payload is read as a coding cache. |
| `test_capture_coding_writes_refusal_log` | After a `no-source-change` refusal, `<ds>/refusals/coding/fix-<n>.json` has the four fields and `verb = "capture"`. After a later successful capture, the file is gone. | Refusals are not counted by `dataset` (FR-33). |
| `test_capture_coding_gitlab_squash_refuses` | A GitLab MR with `squashOnMerge = true` and no `mergeCommitSha` refuses `fix-base-unrecoverable`. | A capture from a SHA that never landed. |
| `test_capture_coding_author_commits_matches_survey` | Single mode without a cache gives the same `author_commits` as the survey row for the same PR. | Two definitions of `author_commits` (§8: "Same values as the survey row"). |
| `test_capture_coding_skips_pull_request_reference` | "Fixes #12" where #12 is a PR (`is_pull_request = True`) gives no issue 12 in the candidate. | PR text is stored as issue text. |
| `test_capture_coding_output_byte_identical_twice` | Two captures give the same candidate bytes. | Non-deterministic output (NFR-1). |
| `test_capture_review_unchanged` | The existing `test_capture_output_byte_identical_twice` (`test_candidate.py:704`) still passes without change. | The dispatch changes the review path (NFR-9). |

## Acceptance Criteria

- [x] `src/eval_harvest/fix_candidate.py` defines the schema of tech plan §8 in the same field order.
- [x] `write_to_dataset` writes `candidates/fix-<n>.json`, `patches/fix-<n>/source.patch`, and `patches/fix-<n>/tests.patch`, and the two patches together have each section of `git diff <base> <merge>` one time.
- [x] The base is the first parent of the merge commit.
- [x] After a capture, every judgment slot is empty and `validate_filled` lists each one.
- [x] `validate_recipe` refuses each form in step 14.
- [x] `fix_candidate.py` imports no forge module and makes no network call.
- [x] `capture <pr> --type coding` writes the coding candidate and the two patches, and prints `next: eval-harvest brief <path>`.
- [x] `capture --type coding --from-survey survey/fix-survey.json` captures the ranked PRs in rank order, with `--pr` and `--limit`.
- [x] Each refusal in the table above has the four fields and the exit code in the table. No candidate is written on `fix-base-unrecoverable` or `no-source-change`.
- [x] A coding capture makes one PR call plus one call for each linked issue, and no `git fetch`.
- [x] Each coding refusal is in `<dataset>/refusals/coding/`.
- [x] Every existing `capture` command without `--type` gives the same bytes as before.
- [x] All commands in "How To Verify" pass.
- [x] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6). `tests/unit/test_no_model_client.py` stays green (FR-19).

## Out Of Scope

- The `fetch_issue` functions and the refusal log module: A-2. This task only calls them.
- The survey cache format, its loader, and the definition of `author_commits`: B-1. This task only moves the author logic into a function.
- `brief`, `show --path`, and `annotate` for coding candidates: C-2.
- Writing the Dockerfile from the recipe: D-2. This task checks only the form of the recipe.
- The coding help text for `capture`: F-1. Keep the new `--type` help to one line here.

## Notes & Gotchas

- **Order of the guards in `handle_capture`.** The coding dispatch must come after the `<pr>`/`--from-survey` usage guard (exit 2) and before `_load_risk_map_or_refuse`. If it comes after the risk-map guard, every coding capture in a dataset without a risk map refuses.
- **Stripped output.** `GitCommandRunner.git` returns stripped stdout (`gitcmd.py:178`). Thus the diff loses its last newline. Use `newline_terminated` (after the rename) on each patch. Read its docstring (`candidate.py:188`): it explains why the fix is at the write site and not in the reader.
- **A test that compares the patches with `git diff`** must compare sections, not one string. The section order changes when source and test files are interleaved (A-2 keeps the order inside each part).
- **First parent, not merge-base.** The review capture uses the merge-base of an iteration tip (`Forge._base_for_tip`, `forge.py:286`). Do not reuse it. A coding datapoint needs the mainline state just before the fix landed.
- **No candidate on a refusal.** The review capture writes the candidate before its refusal "for the record" (`test_capture_still_writes_candidate_on_refusal`). The coding capture writes nothing, because a candidate with no base or no source patch can never be emitted, and `emit --all --kind fix` would then refuse it on every run.
- **GitLab squash merges.** `GitLab._integration_revision` returns `""` for a squash merge, because GraphQL does not expose the squash SHA (`gitlab.py:212` docstring). Then `FixFacts.merge_commit` is empty, and `write_to_dataset` raises `fix-base-unrecoverable`. Use an `offending` text that says this: "GitLab does not expose the squash commit of this MR".
- **`gh` returns `body: null`** for an empty PR body. Convert `None` to `""` before you build `FixFacts`.
- **`Survey.issue_candidates` returns strings** (`survey.py:542`). Convert them to `int`.
- **The refusal `datapoint`** for a coding capture is `fix-<n>`, the candidate stem. The review capture uses `pr-<n>`.
- **Same `Violation` type.** Reuse `Violation` from `candidate.py:125`, so `emit` reports both types in one refusal shape (FR-30).
- **One fixture.** Extend `RepoBuilder.build_fix_history`. Do not add a second builder, and do not change the B-1 scenarios, or the B-1 golden output changes.
- Follow the four-field refusal shape (`Cli.refuse`, `cli.py:1775`) and the exit codes in `ExitCode` (`cli.py:242`). Tech plan §3.
- §3 constraint: `TypedDict` and frozen dataclasses on the CLI path, not Pydantic (pr-eval-harvest ADR-2, `candidate.py:12`). This is a known deviation from `CODING_STANDARDS.md` §5. Follow the existing code.
- NFR-7: the PR body and the issue bodies are untrusted text. They go only into the JSON file. They never go into an argv or a shell.
- `git show --format=%cs` needs git 2.21 or later. `[TODO: verify]` the minimum git version that the CI matrix uses.

## Dependencies

**Blocked by:** [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md), [B-1](B-1-add-the-paged-coding-survey.md)
**Blocks:** [C-2](C-2-add-coding-views-to-brief-show-and-annotate.md), [D-2](D-2-assemble-and-promote-coding-tasks-with-emit-kind-fix.md), [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md)

## Completion Log

**Completed:** 2026-11-18  ·  **Branch:** `coding-candidate-capture-c-1`  ·  **Commit:** the `[C-1]` commit on `main` (squashed; this log ships inside it)

**What was implemented:** A new `FixCandidate` module writes `candidates/fix-<n>.json` with the §8 fields in the §8 order, plus `patches/fix-<n>/source.patch` and `tests.patch`. The base is the first parent of the merge commit. The module also has the four never-raising validators: facts, filled slots, hidden-test files, and the recipe. `capture --type coding` reads one PR (or MR) and each linked issue from the forge, then writes the candidate. It works for a single PR or for the ranked rows of `survey/fix-survey.json`. It needs no risk map and makes no `git fetch`. Every refusal is written to `refusals/coding/`.

**Files changed:**
- `src/eval_harvest/fix_candidate.py` — new: schema, `FixFacts`/`FixIssue`, `FixCaptureError`, path helpers, `write_to_dataset`, `scaffold`, JSON round-trip, `datapoint_type_of`, and the four validators.
- `src/eval_harvest/candidate.py` — `_newline_terminated` renamed to `newline_terminated` (no byte change); `verify.py` docstring reference updated.
- `src/eval_harvest/forge.py` — `Forge.fetch_fix_pull_request` (one `gh pr view` call).
- `src/eval_harvest/gitlab.py` — `FIX_CAPTURE_QUERY` and `GitLab.fetch_fix_merge_request` (one GraphQL call).
- `src/eval_harvest/fix_survey.py` — `FixSurvey.author_commits` extracted from `survey_one`, with no behavior change.
- `src/eval_harvest/cli.py` — `--type`/`--branch` on `capture`; the coding dispatch before the risk-map guard; the single, batch, item, facts, and report methods.
- `tests/fixtures/repo_builder.py` — `build_fix_history(capture_scenarios=True)` appends PR 115 (deletes a source file) and PR 116 (interleaved source/test files).
- `tests/fixtures/gh/fix_capture/` — the recorded `gh pr view` of strands-agents/evals#338, a PR-101 template, and two issue payloads. `tests/fixtures/glab/recorded/mr-3978-fix.json` — a live gitlab.com `FIX_CAPTURE_QUERY` response.
- `tests/integration/test_fix_candidate.py` (42 tests) and `tests/integration/test_capture_coding.py` (27 tests).

**Acceptance criteria:** Each one is met by a named test, and each guard was broken on purpose and seen to go red, then reverted. The guards tested this way: first parent → `parents[-1]`; the trailing-backslash check; the path-traversal check; `>` → `>=` in the body cut; the issue sort; the fail-to-pass check; the `is_pull_request` filter; the text fallback; the refusal clear; the computed `author_commits`; the `unknown-pr` check; excluded rows as targets; the cache-path check. The "no forge module" criterion is an AST test of the direct imports. "Same bytes without `--type`" is met because the review path is unchanged and `test_capture_output_byte_identical_twice` and the full suite pass. `mise run check` is green: 712 passed, 86 skipped (Harbor, e2e). E2E: the built CLI (`uv build`, `uv run eval-harvest capture 338 --type coding`) on a real clone of strands-agents/evals exited 0. Its base was `0bf1252…` (the first parent of `cc0a391…`), issue #337 came out with `body_truncated: true`, and both patches pass `git apply --check` at the base.

**Deviations from the task:**
- Both `[TODO: verify]` items were checked against the live services. gh 2.92.0 lists `closingIssuesReferences`, and each entry carries `number`, `url`, and `repository {name, owner {login}}`. GitLab GraphQL `MergeRequest` has no closing-issues field: both `closingIssues` and `closesIssues` were refused as undefined, so GitLab always uses the text fallback. A git CI matrix version was not found, so it was not checked; local git is 2.54.
- `fetch_fix_pull_request` keeps only the closing references that point to the same repository. Without that filter, a cross-repository "Fixes other/repo#5" would fetch issue #5 of *this* repository.
- `write_to_dataset` also checks that the first parent itself is in the clone (one extra `rev-parse`), so a shallow clone gives `fix-base-unrecoverable` and not a git error. A failed `git diff` gives the same refusal.
- `build_fix_history` takes a `capture_scenarios` flag instead of always appending PRs 115 and 116. One more mainline commit would change the B-1 golden's `mainline_commits`, which the task forbids.
- `_capture_coding_single` and `_fix_facts` take a `branch` argument (and `_fix_facts` an `author_commits` argument), because single mode must compute `author_commits` over `--branch`. A missing branch (`SurveyError`) is a usage error (exit 2). The task table does not list this case.
- `fix_candidate.py` has its own short `-z` numstat parser. `FixSurvey.parse_numstat` is in `fix_survey`, which imports `survey` → `forge`, and the task allows only the `candidate`/`patchsplit`/`gitcmd` imports.
- `cli.py` now imports `GitCommandRunner` (one `rev-list --parents` for the computed `author_commits`).

**Follow-ups noticed but not done:**
- Move the numstat parser into `patchsplit.py` so that `fix_survey` and `fix_candidate` share one copy.
- `git diff` is run without `--binary`, so a fix that changes a binary source file (fixture PR 112) gives a `source.patch` that `git apply` cannot apply. D-2 or E-1 should decide whether to refuse such a fix or to use `--binary`.
