---
id: G-1
title: "[Eval] Measure the coding survey yield on real repositories"
summary: "Run the coding survey on 1–2 real repositories and a 1000-PR benchmark, then set the open size and speed targets."
feature: coding-eval-harvest
workstream: Eval
status: in-progress
complexity: M
merges: [G-2]
implements: [NFR-3, NFR-5]
user_story: US-2
blocked_by: [B-1]
blocks: []
---

# G-1: [Eval] Measure the coding survey yield on real repositories

**In short:** Run the new coding survey on real repositories, and count how many PRs can become
coding datapoints. Also time the survey on 1000 PRs. Use the numbers to replace the straw-man
limits in the tech plan.

## Context

The PRD and the tech plan have open `[TODO]` values that only a real run can set: the yield
(merged PRs → usable candidates), the page byte target (NFR-3), the survey time on 1000 PRs
(NFR-5), and the size thresholds (ADR-6). The largest risk in the plan is low yield: few merged
fixes include tests that fail before the fix (§4). If the yield is too low, the scope can change
(PRD §9). Thus run this task as soon as B-1 merges, before workstreams D and E finish.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** NFR-3 and NFR-5 targets, ADR-6 thresholds, PRD yield `[TODO]`  ·  **User story:** US-2

## Design References

- **Tech plan:** `../tech-plan.md` §2.2, NFR-3 (`[TODO: set target — straw man 8 KiB]` for a page, 12 KiB for a `brief`) and NFR-5 (`[TODO: set target]` for 1000 PRs, ≤ 6 git calls for each PR).
- **Tech plan:** §4, risk "Low yield", with its early warning sign `[TODO: set]`%.
- **Tech plan:** §14, last paragraph: "Run G-2 early, because its yield result can change the scope." (Old plan ID G-2 is this task, G-1.)
- **ADR-6** in §11, consequences: the size limits are straw-man values `[TODO: calibrate on 1–2 real repositories]`.
- **PRD:** `../prd.md` §9 open questions: `[TODO: measure the yield on 1–2 real repositories — merged PRs → candidates with a usable fail-to-pass test]`.

## What To Build

1. **Pick the repositories.** Use `strands-agents/evals` (the notebook 03 repository, PRD §4) and one more public Python repository with more than 300 merged PRs. Use `pydantic/pydantic`, because `eval/objectives/` already uses it.
2. **Run the survey on each repository** (needs `gh auth status` to pass):
   ```bash
   git clone https://github.com/<org>/<name> /tmp/g2/<name>
   uv run eval-harvest survey --type coding --clone /tmp/g2/<name> --repo <org>/<name> --dataset /tmp/g2/ds-<name> --limit 1000 --json > /tmp/g2/<name>-page1.json
   uv run eval-harvest survey --type coding --dataset /tmp/g2/ds-<name> --page 1 --excluded --json > /tmp/g2/<name>-excluded.json
   ```
   If the survey refuses `no-harvestable-fix`, that is a result. Record it.
3. **Record the yield** from `<dataset>/survey/fix-survey.json`: merged PRs, ranked PRs, and the count for each exclusion reason. Yield = ranked / merged.
4. **Check the top 10 ranked PRs by hand** for each repository. For each PR, write one line: does the test change contain a test that fails before the fix? (Read the PR on GitHub.) This gives the "usable fail-to-pass" share that the PRD asks for. It is a sample, not a proof.
5. **Measure the page sizes.** For each page of each repository, count the UTF-8 bytes of the human page and of the `--json` page. Record the largest page. Also run `uv run eval-harvest brief` on one coding candidate if C-2 has merged; otherwise write `[TODO: measure after C-2]` for the brief target.
6. **Add the NFR-5 benchmark.** Create `scripts/bench_fix_survey.py`. It:
   - builds a repository with 1000 squash-merged PRs in a temporary directory, with `git fast-import` in one process (fast and deterministic). Each PR changes 1–3 source files and 0–1 test files. Use fixed author emails and fixed dates.
   - writes a matching `gh pr list` payload file.
   - runs `Cli.run(["survey", "--type", "coding", "--clone", ..., "--from-json", ..., "--dataset", ...])` in-process, and records the wall time with `time.perf_counter()`.
   - counts git calls with a wrapper on `GitCommandRunner.git_with_stderr`, and prints the maximum for one PR.
   - prints one result line: `prs=1000 seconds=<s> max_git_calls_per_pr=<n> mainline_passes=<n>`.
   Add a `mise` task `bench-fix-survey` that runs `uv run python scripts/bench_fix_survey.py`. Do not add it to `check`.
7. **Run the benchmark** three times on your machine. Record the median time and the machine (OS, CPU).
8. **Set the targets.** Write the results to a new file `docs/features/coding-eval-harvest/measurements.md`: the repositories, dates, SHAs, yields, reason counts, hand-check lines, page byte sizes, and benchmark results. Then:
   - Set NFR-3 targets to the largest measured page size plus 25%, rounded up to the next KiB. Keep the straw man if the measurement is smaller.
   - Set the NFR-5 target to two times the median benchmark time, rounded up to the next second.
   - Keep the size thresholds (3, 400, 12) unless more than 30% of the merged PRs are excluded by one size reason. In that case, propose new values in `measurements.md` and ask the team. Do not change the defaults in this task.
   - Set the §4 early-warning value for `no-test-change`.
9. **Update the documents.** Replace the `[TODO]` values in `../tech-plan.md` §2.2 (NFR-3, NFR-5) and §4, and in the B-1 test constant `8192` if the target changed. Add one entry to `../decisions-tech-plan.md` that records the measured values and the targets. If the yield is below 5% on both repositories, write this in the entry, and tell the PM, because PRD §9 says the scope can change.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `scripts/bench_fix_survey.py` | Create | The NFR-5 benchmark. Runs through `uv run`. |
| `mise.toml` | Modify | Add task `bench-fix-survey`. Do not add it to `check`. Follow the comment style of `[tasks.test-e2e]`, which says why it is outside `check`. |
| `docs/features/coding-eval-harvest/measurements.md` | Create | The raw results. |
| `docs/features/coding-eval-harvest/tech-plan.md` | Modify | Replace the NFR-3, NFR-5, and §4 `[TODO]` values. |
| `docs/features/coding-eval-harvest/decisions-tech-plan.md` | Modify | One entry with the targets and why. |
| `tests/unit/test_fix_survey_pages.py` | Modify | Only if the NFR-3 target changes: update the byte-budget constant. File created by B-1. |

## Schemas & Contracts

None. This task changes no stored data, CLI surface, or contract. The benchmark output line is for a person to read; nothing parses it.

## How To Verify

```bash
mise run bench-fix-survey
mise run lint
mise run typecheck
mise run check
```

Then, by hand: `measurements.md` has a row for each repository and for the benchmark. `grep -n "TODO: set target" docs/features/coding-eval-harvest/tech-plan.md` shows no NFR-3 or NFR-5 line.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| None in `check` | The benchmark is a measurement, not a test. The git-call limit already has a test in B-1 (`test_git_calls_per_pr_at_most_six`), and the page byte limit has a test in B-1 (`test_page_bytes_below_budget`). | — |
| Benchmark self-check (inside `bench_fix_survey.py`) | The script fails (non-zero exit) when the survey ranks 0 PRs or when the cache has fewer than 1000 rows in `ranked` + `excluded`. | A benchmark that times a survey which did nothing gives a false fast result. "A scanner that finds nothing must prove it scanned something" (CLAUDE.md). |

## Acceptance Criteria

- [ ] `measurements.md` records the yield, the reason counts, and 10 hand-check lines for each of 2 repositories.
- [ ] `measurements.md` records the largest page size (human and `--json`) for each repository.
- [ ] `mise run bench-fix-survey` runs, ranks PRs, and prints the time and the maximum git calls for one PR (≤ 6).
- [ ] NFR-3 and NFR-5 in the tech plan have numeric targets, not `[TODO]`.
- [ ] `decisions-tech-plan.md` has one entry with the measured values and the targets.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9).

## Out Of Scope

- Changing the default size thresholds in `cli.py` or `FixSurveyThresholds`: a team decision after this task, recorded as a proposal in `measurements.md`.
- Building coding datapoints from the ranked PRs: G-2 and the customer.
- The build success rate of old bases (PRD `[TODO: measure the build success rate]`): needs the runner (ADR-2).

## Notes & Gotchas

- **`--state` for coding is `merged` by default** (B-1). Do not pass `--state all`, or the result fills with `not-merged` rows and the yield is wrong.
- **`--limit` caps the PR list.** The default is 500 (`cli.py:367`). Use `--limit 1000` for the large repository, and record the value you used.
- **`gh` rate limits.** One survey makes one paged `gh pr list` query (NFR-4). If `gh` fails, the CLI exits 5. Wait and run it again; do not count a partial result.
- **Never run `python` directly** (CLAUDE.md). Use `uv run python scripts/bench_fix_survey.py`.
- **The benchmark imports the package in-process.** It must not import `eval/` and must not need network. Use `--from-json`, not `--repo`.
- **Record the SHA of each clone** (`git rev-parse HEAD`). The yield changes as the repository grows, so a number without a SHA cannot be checked again.

## Dependencies

**Blocked by:** [B-1](B-1-add-the-paged-coding-survey.md) (the `survey --type coding` command)
**Blocks:** nothing
