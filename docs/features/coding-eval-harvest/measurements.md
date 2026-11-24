# Measurements — coding-eval-harvest (task G-1)

The raw results of the coding survey on two real repositories and of the NFR-5 benchmark. The tech
plan's NFR-3, NFR-5, §4, and ADR-6 values come from these numbers. The decision is recorded in
`decisions-tech-plan.md` (2026-11-24). Measured on 2026-11-24 by an agent run of task G-1.

## Repositories

| Repository | Cloned (UTC) | Clone `HEAD` | PR list | Merged PRs read | Merged between | Mainline commits |
|------------|--------------|--------------|---------|-----------------|----------------|------------------|
| `strands-agents/evals` | 2026-11-24T18:14:58Z | `aec44b7ac5b5ff550565816b24013f47f3e89c73` | `--state merged --limit 1000` (the repository has only 305 merged PRs) | 305 | 2025-08-04 → 2026-11-24 | 245 |
| `pydantic/pydantic` | 2026-11-24T18:15:57Z | `2b555b79a603ab7eb27a23a9b1b9598897cdfef8` | `--state merged --limit 1000` (the 1000 newest) | 1000 | 2025-02-25 → 2026-11-24 | 4426 |

**How the survey ran.** `survey --type coding --repo <org/name>` failed with exit 5 two times on each
repository: GitHub GraphQL gave `HTTP 504` and `HTTP 502` on `strands-agents/evals`, and "Something
went wrong while executing your query … (`repository.pullRequests.nodes.91.reviews.nodes.1`)" on
`pydantic/pydantic`. The pydantic failure is in the `reviews` field, and it occurred again on the
second attempt. The coding survey does not read `reviews`. Thus the PR list was fetched by hand with
the survey's `GH_FIELDS` without `reviews`, and given to the survey with `--from-json`:

```bash
env -u GITHUB_TOKEN gh pr list --repo <org>/<name> --state merged --limit 1000 \
  --json number,title,body,state,createdAt,closedAt,mergedAt,mergeCommit,headRefName,headRefOid,baseRefName,baseRefOid,additions,deletions,changedFiles,labels,url,author \
  > /tmp/g2/<name>-prs.json
uv run eval-harvest --json survey --type coding --clone /tmp/g2/<name> --repo <org>/<name> \
  --from-json /tmp/g2/<name>-prs.json --dataset /tmp/g2/ds-<name>
```

`--json` is a global flag. It goes before the verb (`eval-harvest --json survey …`), not after it as
the task file wrote. No partial result is counted here: each number is from one complete run.

## Yield

Yield = ranked / merged PRs read. A PR can have more than one exclusion reason, so the reason counts
add to more than the excluded count. "Only reason" counts the PRs that have that reason and no other.

| Repository | Merged | Ranked | Excluded | Yield |
|------------|--------|--------|----------|-------|
| `strands-agents/evals` | 305 | 123 | 182 | **40.3%** |
| `pydantic/pydantic` | 1000 | 289 | 711 | **28.9%** |

| Exclusion reason | `evals` PRs (% of merged) | `evals` only reason | `pydantic` PRs (% of merged) | `pydantic` only reason |
|------------------|---------------------------|---------------------|------------------------------|------------------------|
| `no-test-change` | 92 (30.2%) | 47 | 590 (59.0%) | 342 |
| `source-too-large` | 70 (23.0%) | 24 | 48 (4.8%) | 9 |
| `too-many-files` | 56 (18.4%) | 14 | 57 (5.7%) | 15 |
| `bot-author` | 28 (9.2%) | 0 | 124 (12.4%) | 0 |
| `source-too-small` | 25 (8.2%) | 2 | 142 (14.2%) | 24 |
| `no-source-change` | 9 (3.0%) | 9 | 57 (5.7%) | 48 |
| `no-integration-commit` | 1 (0.3%) | 1 | 2 (0.2%) | 2 |
| `not-merged` | 0 | 0 | 0 | 0 |

## Hand check of the top 10 ranked PRs

For each PR: does the test change contain a test that fails before the fix? The check reads the test
diff of the integration commit (`git show <merge> -- <test paths>`) and the PR description. It does
not run the tests, so "yes" means "the diff adds a test that exercises the fixed behavior". It is a
sample, not a proof (ADR-2: only a runner proves fail-to-pass).

**`strands-agents/evals`** — 9 of 10 usable, 1 of 10 weak:

1. #383 `Experiment.to_file()` rejects non-strict JSON — yes: 5 new tests (NaN and unpaired-surrogate tools, no corrupt file).
2. #379 skip non-serializable tools in `OutputEvaluator` — yes: 5 new tests (non-serializable and circular tools).
3. #313 tool parsing from a list — yes: 6 new tests for list input to the Langfuse provider.
4. #240 join all `toolResult.content` blocks — yes: 31 new tests over three mappers (multi-block joins).
5. #214 `run_evaluations_async` keeps the input order — yes: 1 new test for the order.
6. #534 configurable difficulty distribution — yes: 6 new tests; partly a feature (114 source lines).
7. #521 cancel the workers on cancellation — yes: 1 new test that the workers stop.
8. #377 `parse_timestamp` returns aware UTC — yes: 14 new tests; the old test file is rewritten (35 lines removed).
9. #371 root agent span by earliest `start_time` — yes: 7 new tests for multi-agent traces.
10. #26 `Evaluator.evaluate()` returns a list — weak: no new test; existing asserts change to `result[0]`. They fail before the fix, but they test a new API shape, not a defect.

**`pydantic/pydantic`** — 10 of 10 usable:

1. #13953 JSON Schema for the `arguments` schema — yes: 3 new tests (unpacked `TypedDict` kwargs).
2. #14176 `ByteSize.human_readable()` unit boundary — yes: `test_bytesize_human_readable_unit_boundary`; the tests also move to `tests/types/test_bytesize.py` (86 lines removed).
3. #14164 deeply nested data reported as cyclic — yes: 3 new depth tests. The fix is in `pydantic-core` (Rust), so its environment must build the Rust extension.
4. #14160 quadratic serialization with per-index `include`/`exclude` — yes: 4 new tests; 9 source files, 135 lines, part in `pydantic-core`.
5. #14065 `Self` in parametrized generics — yes: 3 new tests.
6. #14054 forward annotations with type-variable substitution — yes: 7 new tests.
7. #14051 pickling `PydanticCustomError` and `ValidationError` — yes: 2 new tests.
8. #14028 infinite recursion on an invalid default — yes: 3 new tests.
9. #13980 JSON Schema cache key with nested `json_schema_mode_override` — yes: 2 new tests.
10. #13954 `arguments-v3` schema with variadic keywords — yes: 1 new test.

Usable fail-to-pass share in the sample: 19 of 20 (95%). A ranked PR in these two repositories is
usually a real regression-test fix. The loss is before the ranking: 30–59% of merged PRs have no
test change at all.

## Page sizes (NFR-3)

UTF-8 bytes of every page (default page size 20), for the ranked list and for `--excluded`, in the
human form and the `--json` form. Measured with `wc -c` on stdout.

| Repository | List | Pages | Largest human page | Largest `--json` page |
|------------|------|-------|--------------------|-----------------------|
| `strands-agents/evals` | ranked | 7 | 5 500 (page 2) | **14 003** (page 2) |
| `strands-agents/evals` | excluded | 10 | 2 309 (page 2) | 4 116 (page 6) |
| `pydantic/pydantic` | ranked | 15 | 5 102 (page 10) | 13 569 (page 10) |
| `pydantic/pydantic` | excluded | 36 | 2 199 (page 14) | 4 126 (page 14) |

The `--json` page is about 2.5 times the human page: each JSON row carries the field names and the
full `rank_reasons` list. The B-1 test `test_page_bytes_below_budget` measures the human page only.

## Brief sizes (NFR-3)

`capture <pr> --type coding` on the rank-1 PR of each repository, then `brief`:

| Candidate | Human `brief` | `--json` `brief` |
|-----------|---------------|------------------|
| `strands-agents/evals` `fix-383` | **8 611** | 8 460 |
| `pydantic/pydantic` `fix-13953` | 6 222 | 6 048 |

C-2's synthetic worst case (a 4001-character PR body and one 4001-character issue body) is 10 509
bytes. A PR that links three long issues can go above 12 KiB (C-2, Notes & Gotchas).

## Size thresholds (ADR-6)

The rule: keep the thresholds (3, 400, 12) unless one size reason excludes more than 30% of the
merged PRs. The largest share for one size reason is `source-too-large` at 23.0% on
`strands-agents/evals`. Thus the thresholds stay. No new values are proposed.

## NFR-5 benchmark

`mise run bench-fix-survey` (`scripts/bench_fix_survey.py`): 1000 squash-merged PRs from one
`git fast-import`, each with 1–3 new source files of 2–41 lines and 0–1 test file, fixed authors and
dates. The survey runs in-process with `--from-json`. The time is `Cli.run` only, not the fixture.

| Run | Result line |
|-----|-------------|
| 1 | `prs=1000 seconds=90.75 max_git_calls_per_pr=3 mainline_passes=1` |
| 2 | `prs=1000 seconds=86.39 max_git_calls_per_pr=3 mainline_passes=1` |
| 3 | `prs=1000 seconds=87.00 max_git_calls_per_pr=3 mainline_passes=1` |

**Median: 87.00 s** (about 87 ms for each PR, 3 git calls each). Machine: Apple M2 Pro, 12 cores,
32 GiB, macOS 26.7.3, git 2.54.0 (Apple Git-157), Python 3.12.8. Most of the time is git process
start-up through the macOS `/usr/bin/git` shim (see the `-n auto` note in `pyproject.toml`). The real
`pydantic/pydantic` survey (1000 PRs, 4426 mainline commits) took 98 s wall time, including `uv`
start-up.

The self-check was seen to fail: with every PR above the source-line limit the script printed
`FAILED: the survey exited 3, not 0`, and with 10 rows removed from the payload it printed
`FAILED: the cache holds 990 rows in ranked + excluded, fewer than 1000`. Both exited 1.

## Targets set from these numbers

| Value | Rule | Measured | Target |
|-------|------|----------|--------|
| NFR-3, human survey page | largest + 25%, next KiB; keep the straw man if smaller | 5 500 → 6 875 → 7 KiB | **8 KiB** (straw man kept) |
| NFR-3, `--json` survey page | same rule | 14 003 → 17 504 → 18 KiB | **18 KiB** (new; the straw man did not cover it) |
| NFR-3, `brief` | same rule | 8 611 → 10 764 → 11 KiB | **12 KiB** (straw man kept) |
| NFR-5, 1000 PRs | 2 × median, next second | 2 × 87.00 s | **174 s** |
| §4 early warning, `no-test-change` | above both measured repositories | 30.2%, 59.0% | **70%** |
| ADR-6 thresholds | change only above 30% for one size reason | largest 23.0% | **3, 400, 12** (kept) |
