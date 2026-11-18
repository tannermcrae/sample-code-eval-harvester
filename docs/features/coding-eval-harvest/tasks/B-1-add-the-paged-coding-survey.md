---
id: B-1
title: "[Survey] Add the paged coding survey"
summary: "Add survey --type coding: facts and exclusion reasons for each merged PR, a fixed rank, a saved result, and small pages."
feature: coding-eval-harvest
workstream: Survey
status: todo
complexity: L
merges: [B-1, B-2, B-3]
implements: [FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-33, NFR-1, NFR-2, NFR-3, NFR-4, NFR-5]
user_story: [US-2, US-7]
blocked_by: [A-2]
blocks: [C-1, F-1, G-1]
---

# B-1: [Survey] Add the paged coding survey

**In short:** Add `eval-harvest survey --type coding`. For each merged PR, it writes simple facts
and tells why a PR cannot be used. It puts the usable PRs in a fixed order, saves the full result to
one file, and shows the agent one small page at a time. When no PR is usable, it stops with a clear
reason.

## Context

A coding agent cannot find good fix candidates at scale, because the merged history fills its
context. The coding survey gives the agent facts for each PR, without diff text. The agent tries
the best candidates first, because the rows are in a fixed order with reasons. It reads small
pages, so that 500 PRs do not fill its context. A page request reads only the saved file, so it
makes no forge call and no git call. When every PR is excluded, the command refuses
`no-harvestable-fix` with the most frequent reason in words that an SDE can use. C-1 reads the saved
file in `capture --type coding --from-survey`. Without this task, no coding survey exists, and the
agent must read history.

**North star:** An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval datapoints from the merged fixes of their own repository. The history of the repository does not fill the context of the agent.
**Implements:** FR-5 to FR-11, FR-33 (survey part), NFR-1, NFR-2, NFR-3, NFR-4, NFR-5 (git-call limit)  ·  **User story:** US-2, US-7

## Design References

- **Tech plan:** `../tech-plan.md` §2.1, FR-5 to FR-11: the fact fields, the eight exclusion reasons, the sort key, the cache, the page limits, determinism, and the plain-words refusal (with the example text).
- **Tech plan:** §2.2, NFR-2 (no network on a page request), NFR-3 (page byte budget, straw man 8 KiB for 20 rows), NFR-4 (one paged forge query), NFR-5 (at most 6 git calls for each PR).
- **Tech plan:** §5, paragraph "Survey (FR-5 to FR-11)": how `FixSurvey` uses the existing survey facts.
- **Tech plan:** §7.2, diagram "Coding survey with pages": the loop for each merged PR, the first run, and the page run.
- **Tech plan:** §8, `survey/fix-survey.json`: the top-level keys and the ranked and excluded row fields. §8, `refusals/<type>/<datapoint>.json`: the refusal record.
- **Tech plan:** §9, row `survey --type coding`: the full flag list, the default thresholds (`--min-source-lines 3`, `--max-source-lines 400`, `--max-files 12`), the `--json` page shape `{page, pages, rows, excluded_counts, next}`, and the refusals.
- **ADR-1** in §11: coding is a second type with its own modules. The decision is settled.
- **ADR-6** in §11: a fixed sort key, not a weighted score. The page number is the cursor. A refresh is a new `survey --repo` run. The decision is settled.
- **PRD:** `../prd.md` US-2: "Facts for each candidate without a diff. Ranks and exclusions have reasons. Pages have a size limit and the agent can continue a survey. The output is deterministic." US-7: "A repository without coding datapoints gets a refusal that the SDE can use."

## What To Build

Do the three parts in sequence. Each part can be its own commit, because each part has its own
tests. Part 1 makes the facts, Part 2 makes the order and the pages, and Part 3 connects them to the
CLI. Every function in Parts 1 and 2 lives in `src/eval_harvest/fix_survey.py`.

### Part 1: Facts and exclusions for each merged PR (FR-5, FR-6, NFR-5)

1. Create `src/eval_harvest/fix_survey.py` with one stateless class `FixSurvey` (class methods and static methods only). It is an online-layer module. It may import `eval_harvest.gitcmd`, `eval_harvest.survey`, and `eval_harvest.patchsplit`.
2. Add a frozen dataclass `FixSurveyThresholds` with three `int` fields and these defaults: `min_source_lines = 3`, `max_source_lines = 400`, `max_files = 12`. Part 3 fills it from the CLI flags.
3. Add a `TypedDict` `FixFactsDict` with the fields in "Schemas & Contracts" below. Use a `TypedDict`, not Pydantic (§3 constraint).
4. Add the exclusion reason names as one `Final` tuple, in this fixed order: `not-merged`, `no-integration-commit`, `bot-author`, `no-source-change`, `no-test-change`, `source-too-small`, `source-too-large`, `too-many-files`. `excluded_by` always lists reasons in this order.
5. Add `FixSurvey.survey_one(pull_request, clone, commits, thresholds) -> FixFactsDict`. Use these steps, each in its own small static method (`CODING_STANDARDS.md` §3):
   1. **Merged gate.** When `pull_request["state"] != "MERGED"`, return a row with `excluded_by = ["not-merged"]` and empty facts. Make no git call.
   2. **Integration gate.** Read `integration = (pull_request.get("mergeCommit") or {}).get("oid") or ""`. This is the same read as `Survey.survey_one` (`survey.py:339`). Call `Survey._range_facts(clone, "", integration, have_tip=False)` (`survey.py:361`). With `have_tip=False` it makes two git calls only: `rev-list --parents` and `diff --name-only <first-parent>..<integration>`. When `integration` is empty or `ranges.parents` is empty, add `no-integration-commit`, add `bot-author` if it applies, and return. Do not compute size reasons.
   3. **Line counts.** Run `GitCommandRunner.git(clone, "diff", "--numstat", "-z", ranges.parents[0], integration)`. Parse the `-z` records (see Notes). For each path, use `PatchSplit.is_test_path(path)` (A-2) to put it into `source_paths` or `test_paths`. Sum the added and deleted lines for each group.
   4. **Author commits.** Find the author email of the change. When `len(ranges.parents) == 1` (squash or rebase), use `author_email` of the `Commit` in `commits` whose `sha == integration` (A-2 adds `Commit.author_email`). When there are two or more parents (a true merge), run `git log -1 --format=%ae <ranges.parents[1]>`, because the merge commit author is the person who merged, not the person who wrote the fix. `author_commits` is the count of `commits` with that `author_email` and with `at` not later than the `at` of the integration commit. Use 0 when the email is empty.
   5. **Fix signal.** `fix_signal` is `True` when the PR title matches `_FIX_SUBJECT` (`survey.py:106`) or when a label name is `bug` (case-insensitive). Add a public static method `Survey.is_fix_subject(subject: str) -> bool` in `survey.py` that returns `bool(_FIX_SUBJECT.search(subject))`, and call it. Do not import the private regex from another module.
   6. **Linked issues.** `linked_issues` is `[int(n) for n in Survey.issue_candidates(title, body, branch)]` (`survey.py:542`). Do not add a forge call.
   7. **Content reasons.** Add `bot-author` when the PR author is a bot (see Notes). Add `no-source-change` when `source_paths` is empty. Add `no-test-change` when `test_paths` is empty or `test_lines_added == 0`. Only when `source_paths` is not empty, compare `source_lines_added + source_lines_deleted` with the thresholds: add `source-too-small` when it is less than `min_source_lines`, and `source-too-large` when it is more than `max_source_lines`. Add `too-many-files` when `files_touched` is more than `max_files`. A value equal to a threshold is not excluded.
6. Add `FixSurvey.survey(clone, pull_requests, commits, thresholds) -> list[FixFactsDict]`. It calls `survey_one` for each PR and sorts the rows by `number`, in descending sequence. It does not call `Survey.mainline`. Part 3 calls `Survey.mainline` one time and gives the result to this function.
7. Add `RepoBuilder.build_fix_history(root) -> FixHistoryFixture` in `tests/fixtures/repo_builder.py`. It makes an origin and a `--no-local` clone (use `_init_origin` and `_clone`), and one squash commit on `main` for each scenario in the test table. Each commit writes the files that it needs, with fixed dates (use `_commit_index`, and write each file with `newline="\n"`). Also add one true-merge PR (`git merge --no-ff`) with a different author email. Add a payload template `tests/fixtures/gh/fix_survey/prs.json` with one `gh pr list` row for each scenario. Use `{{TOKEN}}` placeholders for SHAs, as `tests/fixtures/gh/survey/reviewed.json` does. C-1 and C-2 reuse this fixture. It is the one shared coding fixture; do not add a second builder with another name.
8. Do not change `Survey.survey_one`, `Survey._blockers`, `Survey._assemble_row`, or the review payload. The review golden test must stay green.

### Part 2: Rank, cache, and pages (FR-7 to FR-10, NFR-1, NFR-3)

1. Add the constants `FIX_SURVEY_RELATIVE_PATH: Final = Path("survey") / "fix-survey.json"`, `DEFAULT_PAGE_SIZE: Final = 20`, `MAX_PAGE_SIZE: Final = 50`, and `TITLE_LIMIT: Final = 80`.
2. Add `FixSurvey.rank(rows: list[FixFactsDict]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]`. It returns `(ranked, excluded)`:
   - `ranked` holds the rows with `excluded_by == []`. Sort them with the key `(bool(linked_issues), fix_signal, author_commits, number)`, all in descending sequence. Use one `sorted(..., key=..., reverse=True)` call. Give each row `rank` (1 is first) and `rank_reasons`.
   - `excluded` holds the other rows, sorted by `number` in descending sequence. Each excluded row has only `number`, `title`, and `excluded_by` (§8).
3. Build `rank_reasons` as a list of short strings, one for each key, in key order:
   - `"links issue #42"` (all numbers, comma-separated) or `"no linked issue"`.
   - `"title or label marks a fix"` or `"no fix signal"`.
   - `"author has 7 mainline commits"`.
   - `"tie broken by PR number"`. Always add this last item, so the agent knows the final key.
4. Build each ranked row in this fixed key order (§8): `number`, `title`, `url`, `merged_at`, `rank`, `linked_issues`, `fix_signal`, `source_files`, `source_lines_added`, `source_lines_deleted`, `test_files`, `files_touched`, `author_commits`, `rank_reasons`, then `source_paths` and `test_paths`. `source_files` is `len(source_paths)` and `test_files` is `len(test_paths)`. Keep the two path lists in the cache, because C-1 and the agent need them. The pages do not print the lists.
5. Add `FixSurvey.result_document(repo, mainline_commits, thresholds, ranked, excluded) -> dict[str, Any]`. It returns `{repo, mainline_commits, thresholds: {min_source_lines, max_source_lines, max_files}, ranked, excluded}` in that key order.
6. Add `FixSurvey.write_cache(dataset: Path, document) -> Path`. It writes `json.dumps(document, indent=2, ensure_ascii=False) + "\n"` with `encoding="utf-8"` and `newline="\n"` to `dataset / FIX_SURVEY_RELATIVE_PATH`. It makes the `survey/` directory when necessary. It replaces an old file. Write to a temporary file in the same directory and then `os.replace` it, so that a failed run does not leave half a file.
7. Add `FixSurvey.read_cache(dataset: Path) -> dict[str, Any]`. It raises a new `FixSurveyCacheError(RuntimeError)` when the file does not exist, is not JSON, or has no `ranked` list. Part 3 maps this error to the `survey-cache-missing` refusal.
8. Add `FixSurvey.page_count(total_rows: int, page_size: int) -> int`. It returns at least 1, so that an empty list has one empty page.
9. Add `FixSurvey.render_page(document, *, page: int, page_size: int, excluded: bool, dataset: Path) -> str` for the human output. Raise `ValueError` when `page < 1`, when `page > page_count`, or when `page_size` is not in `1..MAX_PAGE_SIZE`. The text is:
   - Line 1: `fix survey: <R> ranked, <E> excluded, page <p> of <P>`.
   - One line for each row: `#<number>  rank <rank>  src +<a>/-<d> in <source_files> file(s)  tests <test_files>  issues <list or ->  <title cut to 80>`. Then one indented line: `    why: <rank_reasons joined with "; ">`.
   - With `excluded=True`, print the excluded rows instead: `#<number>  <excluded_by joined with ",">  <title cut to 80>`.
   - An `excluded (<E>)` section with one line for each reason and its count, most common first, then by name. This is the same order rule as `Survey.blocker_histogram` (`survey.py:586`).
   - The last line is one `next:` line. When a next page exists: `next: eval-harvest survey --type coding --dataset <dataset> --page <p+1>` (add ` --excluded` when `excluded` is true). On the last ranked page: `next: eval-harvest capture --type coding --from-survey <dataset>/survey/fix-survey.json`.
10. Add `FixSurvey.render_page_json(document, *, page, page_size, excluded, dataset) -> str`. It returns `json.dumps({"page", "pages", "rows", "excluded_counts", "next"}, indent=2) + "\n"`. `rows` holds the page rows without `source_paths` and `test_paths`. The titles are cut to 80 characters. `excluded_counts` is `{reason: count}` in the same order as the human histogram. `next` is the same command as the human `next:` line, without the `next: ` prefix.
11. Add one private helper `_cut_title(title: str) -> str`. It replaces each run of whitespace (including line breaks) with one space, and then cuts the result to 80 characters. Use it in both renderers.
12. Do not call git or the forge in any function of this part. All functions take data from Part 1 or from the cache.

### Part 3: Connect the coding survey to the CLI (FR-8, FR-11, FR-33, NFR-2, NFR-4)

1. In `Cli.add_survey_arguments` (`src/eval_harvest/cli.py:339`), add these flags:
   - `--type`, `choices=("review", "coding")`, `default="review"`.
   - `--dataset <dir>` (`type=Path`, default `None`). The review survey has no `--dataset` today, and A-2 does not add one. This task adds it.
   - `--page <n>` (`type=int`, default `None`), `--page-size <k>` (`type=int`, default `DEFAULT_PAGE_SIZE`), and `--excluded` (`store_true`).
   - `--min-source-lines` (default 3), `--max-source-lines` (default 400), `--max-files` (default 12). Take the defaults from `FixSurveyThresholds`, so the values are in one place.
   - Give each new flag a one-line `help` string that starts with `coding only:`. Do not write methodology prose. F-1 does that.
2. Change `--clone` from `required=True` to `required=False`. In `Cli.handle_survey` (`cli.py:718`), print the old argparse-style message and return `ExitCode.USAGE` when `--clone` is missing, except for a coding page request (step 5.2). The review path must behave the same as before.
3. Change `--state` to `default=None`. Resolve it in the handler: `"all"` for review (the old default), `"merged"` for coding. A coding survey with `--state all` would fill the result with `not-merged` rows. An explicit `--state` value always wins.
4. At the start of `handle_survey`, when `arguments.type == "coding"`, call a new `Cli._handle_fix_survey(arguments)` and return its exit code. Keep the review code below it unchanged.
5. `_handle_fix_survey` does these steps. Put each step in its own small method:
   1. **Validate.** `--dataset` is required: usage error (exit 2) when it is missing. `--page-size` must be in `1..MAX_PAGE_SIZE`, and `--page` must be at least 1: usage error otherwise. `--excluded` without `--page` means page 1.
   2. **Page request.** When `--page` is given and neither `--repo` nor `--from-json` is given, call `FixSurvey.read_cache(dataset)`. On `FixSurveyCacheError`, refuse `survey-cache-missing` (exit 3). `next_`: `run \`eval-harvest survey --type coding --clone <dir> --repo <org/name> --dataset <dataset>\` first`. Then print the page (step 5.5). Do not read the clone, the forge, or git.
   3. **Fresh run.** Require `--clone`. Call `Cli._survey_clone_remote_or_report` (`cli.py:783`) and `Cli._load_survey_pull_requests` (`cli.py:812`), as the review path does. Do **not** call `_fetch_pull_heads_or_refuse`. The coding survey does not use pull-head refs (§9). Call `Survey.mainline(clone, arguments.branch)` one time. Call `FixSurvey.survey`, `FixSurvey.rank`, `FixSurvey.result_document`, and `FixSurvey.write_cache`. Write the cache before the refusal check, so `--page 1 --excluded` can show the reasons after a refusal.
   4. **Refusal.** When `ranked` is empty, call `FixSurvey.no_harvestable_fix_offending(document)` and `FixSurvey.no_harvestable_fix_next(document)` (step 6). Write the refusal with `RefusalLog.write(dataset, "coding", record)` (A-2), with `verb="survey"` and `datapoint` = the `--repo` value, or the clone path when only `--from-json` is given. Then call `Cli.refuse(check="no-harvestable-fix", ..., exit_code=ExitCode.REFUSAL, json_mode=...)` (`cli.py:1775`). Refuse on the `--from-json` path too. The review survey refuses only online, but a coding survey always writes a cache, so it has no "re-render" case.
   5. **Print.** Print `FixSurvey.render_page_json(...)` when `--json` is set, or `FixSurvey.render_page(...)` otherwise. The page is `--page` or 1. When the page is past the last page, print `eval-harvest survey: page <n> does not exist; the survey has <P> page(s)` to stderr and return exit 2.
6. In `fix_survey.py`, add the refusal text:
   - A `Final` dict `_REASON_PLAIN_WORDS` from each reason to one sentence. For `no-test-change` the sentence is exactly `fixes in this repository ship without tests that fail before the fix` (FR-11). Write a sentence for each of the other seven reasons. For the size reasons, put the threshold value in the sentence.
   - `no_harvestable_fix_offending(document) -> str`. With no rows at all: `the forge returned no pull request to survey`. Otherwise: `all <N> pull request(s) are excluded; the most common reason is <reason> (<k> of <N>): <plain words>`. "Most common" uses the same tie rule as the Part 2 histogram (count, then name).
   - `no_harvestable_fix_next(document) -> str`, one command or action for the dominant reason. For example: size reasons → re-run with the matching `--min-source-lines`, `--max-source-lines`, or `--max-files` flag. `no-test-change` → `pick a repository whose fixes add tests; see the excluded list with \`eval-harvest survey --type coding --dataset <d> --page 1 --excluded\``.
7. Do not change `Survey.build_payload`, `Survey.render_json`, `Survey.render_summary`, or the review refusals.

## Files Affected

| Path | Change | Notes |
|------|--------|-------|
| `src/eval_harvest/fix_survey.py` | Create | Part 1: `FixSurvey`, `FixSurveyThresholds`, `FixFactsDict`, the reason tuple. Part 2: the constants, `FixSurveyCacheError`, `rank`, `result_document`, `write_cache`, `read_cache`, `page_count`, `render_page`, `render_page_json`. Part 3: `_REASON_PLAIN_WORDS`, `no_harvestable_fix_offending`, `no_harvestable_fix_next`. Follow the section-comment style of `survey.py` (`# ─── per-PR facts ───`). |
| `src/eval_harvest/survey.py` | Modify | Part 1: add `Survey.is_fix_subject` only. No other change. |
| `src/eval_harvest/cli.py` | Modify | Part 3: new flags in `add_survey_arguments` (line 339). `--clone` and `--state` defaults. Dispatch to `_handle_fix_survey` at the top of `handle_survey` (line 718). |
| `tests/fixtures/repo_builder.py` | Modify | Part 1: add `FixHistoryFixture` (frozen dataclass) and `RepoBuilder.build_fix_history`. |
| `tests/fixtures/gh/fix_survey/prs.json` | Create | Part 1: one recorded PR row for each scenario, with SHA tokens. |
| `tests/fixtures/gh/fix_survey/expected-fix-survey.json` | Create | Part 2: the golden cache file for the Part 1 fixture. |
| `tests/integration/test_fix_survey.py` | Create | Parts 1 and 2: real git on the fixture. Mirror the helpers of `tests/integration/test_survey.py:46` (`_write_payload`). |
| `tests/unit/test_fix_survey_pages.py` | Create | Part 2: pure-data tests. Build rows as `FixFactsDict` literals with a small helper. No git. |
| `tests/integration/test_fix_survey_cli.py` | Create | Part 3: CLI tests with `Cli.run([...])` in-process, as `tests/integration/test_survey.py:63` does. Use the Part 1 fixture and its payload with `--from-json`. |

## Schemas & Contracts

**Before:** No coding survey exists. The review row is built by `Survey._assemble_row` (`survey.py:419`). It has `test_paths` and `has_test_change`, but no line counts for each group, no `author_commits`, and no exclusion reasons. The review survey prints all rows in one output (`Survey.render_summary`, `survey.py:640`), with no cache and no pages.

**After, Part 1:** `FixSurvey.survey_one` returns this `TypedDict`. The field order is fixed:

```python
class FixFactsDict(TypedDict):
    number: int
    title: str                 # verbatim from the forge row; Part 2 cuts it for pages
    url: str
    merged_at: str             # "" when not merged
    author: str                # login, "" when absent
    integration_revision: str  # "" when unknown
    integration_parents: int
    linked_issues: list[int]   # sorted ascending
    fix_signal: bool
    source_paths: list[str]    # sorted
    test_paths: list[str]      # sorted
    source_lines_added: int
    source_lines_deleted: int
    test_lines_added: int
    files_touched: int         # len(source_paths) + len(test_paths)
    author_commits: int
    excluded_by: list[str]     # [] means "rankable"; order = the reason tuple

@dataclass(frozen=True, slots=True)
class FixSurveyThresholds:
    min_source_lines: int = 3
    max_source_lines: int = 400
    max_files: int = 12
```

**After, Part 2:** `<dataset>/survey/fix-survey.json`:

```json
{
  "repo": "acme/widgets",
  "mainline_commits": 14,
  "thresholds": {"min_source_lines": 3, "max_source_lines": 400, "max_files": 12},
  "ranked": [
    {
      "number": 1240, "title": "fix: ...", "url": "https://...", "merged_at": "2026-08-03T09:00:00Z",
      "rank": 1, "linked_issues": [42], "fix_signal": true,
      "source_files": 2, "source_lines_added": 9, "source_lines_deleted": 3,
      "test_files": 1, "files_touched": 3, "author_commits": 4,
      "rank_reasons": ["links issue #42", "title or label marks a fix", "author has 4 mainline commits", "tie broken by PR number"],
      "source_paths": ["src/a.py", "src/b.py"], "test_paths": ["tests/test_a.py"]
    }
  ],
  "excluded": [
    {"number": 1239, "title": "docs: ...", "excluded_by": ["no-source-change", "no-test-change"]}
  ]
}
```

`--json` page:

```json
{"page": 1, "pages": 3, "rows": [ /* ranked rows without source_paths/test_paths */ ],
 "excluded_counts": {"no-test-change": 120, "bot-author": 9},
 "next": "eval-harvest survey --type coding --dataset /d --page 2"}
```

**After, Part 3** (`eval-harvest survey --help`). Before:
```
eval-harvest survey --clone <dir> (--repo <org/name> | --from-json <file>) [--no-fetch] [--forge auto|github|gitlab] [--state ...] [--summary]
```

After:
```
eval-harvest survey --type coding --clone <dir> (--repo <r> | --from-json <f>) --dataset <dir>
                    [--page <n>] [--page-size <k>] [--excluded]
                    [--min-source-lines 3] [--max-source-lines 400] [--max-files 12] [--json]
eval-harvest survey --type coding --dataset <dir> --page <n> [--page-size <k>] [--excluded] [--json]   # cache only
```

Refusals (four-field shape, `Cli.refuse`):

| check | exit | when |
|-------|------|------|
| `no-harvestable-fix` | 3 | The fresh run ranks no PR. Also writes `<dataset>/refusals/coding/<repo with / → __>.json`. |
| `survey-cache-missing` | 3 | A page request finds no valid cache. |

**Migration:** None. New module, new file.
**Backward compatibility:** Every review invocation gives the same bytes and the same exit codes. Two parser changes are visible in `--help`: `--clone` shows as optional, and `--state` shows no default. The review handler still requires `--clone` and still uses `all`. C-1 reads `ranked[*].number` from the cache file. Do not rename the cache keys after C-1 starts.

## How To Verify

```bash
uv run pytest tests/integration/test_fix_survey.py tests/unit/test_fix_survey_pages.py tests/integration/test_fix_survey_cli.py
uv run pytest tests/integration/test_survey.py tests/integration/test_help_methodology.py
mise run lint
mise run typecheck
mise run check
```

Manual checks:
- Part 1: in a Python shell (`uv run python`), build the fixture with `RepoBuilder.build_fix_history(Path(tmp))`, call `FixSurvey.survey(...)`, and read the rows. Each scenario row has exactly the reason that its name tells.
- Part 2: write a cache from the fixture, then call `render_page` for page 1 and for the last page. Page 1 ends with a `--page 2` command. The last page ends with the `capture --type coding --from-survey` command.
- Part 3, on a real public repository (`gh auth status` must pass):
  ```bash
  uv run eval-harvest survey --type coding --clone <clone> --repo <org/name> --dataset /tmp/ds
  uv run eval-harvest survey --type coding --dataset /tmp/ds --page 2
  ```
  Page 1 ends with a `next:` line for page 2. The second command works with the network off.

## Tests To Write

| Test | What it verifies | Defect it catches |
|------|-----------------|------------------|
| `test_clean_fix_facts` | The clean PR (2 source files, 1 test file, `Fixes #42` in the body, title `fix: ...`) gives the expected `source_paths`, `test_paths`, line counts, `linked_issues == [42]`, `fix_signal is True`, `excluded_by == []`. | The splitter is not used, so `tests/conftest.py` counts as source. |
| `test_each_reason_fires_alone` (parametrized, one case for each of the 8 reasons) | Each scenario PR gives exactly `[reason]`. | A reason that fires for the wrong PR, or a gate that also adds size reasons. |
| `test_threshold_boundaries` | Source lines equal to 3 and to 400, and files equal to 12, are not excluded. 2, 401, and 13 are excluded. | `<` in place of `<=` (§12 FR-6 row). |
| `test_test_change_that_only_deletes_is_no_test_change` | A PR whose only test change deletes a test gives `no-test-change`. | A PR with no test that can fail before the fix is ranked. |
| `test_review_harvestable_pr_is_excluded_by_coding_survey` | The PR from A-1's TP-7 test (merged, reviewed, no test change) is harvestable in the review survey and has `excluded_by == ["no-test-change"]` in the coding survey. | The coding half of TP-7 (§12 TP-7 row): the coding survey ranks a PR with no tests. |
| `test_bot_author_is_excluded` | A row with `author.is_bot = true`, and a row with login `dependabot[bot]`, give `bot-author`. | A bot PR is ranked. |
| `test_true_merge_author_is_second_parent_author` | For the true-merge PR, `author_commits` counts the commits of the PR author, not of the person who merged. | `author_commits` uses the merge commit author. |
| `test_author_commits_counts_by_email` | Two commits with the same email and different names count as 2. | Counting by name or by forge login (§12 FR-5 row). |
| `test_git_calls_per_pr_at_most_six` | Wrap `GitCommandRunner.git_with_stderr` (as `test_survey.py:221` does) and count calls inside one `survey_one`. A squash PR and a true-merge PR each make ≤ 6 calls. A non-merged PR makes 0. | NFR-5: an extra git call for each PR (for example, one `git log` for each PR). |
| `test_rename_into_tests_counts_as_test` | A PR that renames `src/x_check.py` to `tests/test_x.py` puts the new path in `test_paths`. | The survey and capture (C-1) disagree on a rename. |
| `test_binary_file_counts_zero_lines` | A changed binary file is in `source_paths` with 0 lines. | A crash on the `-\t-` numstat record. |
| `test_sort_key_one_key_at_a_time` (parametrized) | Pairs of rows that are different in one key only (issue, fix signal, author commits, number) come out in the expected order. | A key with the wrong sequence direction (§12 FR-7 row). |
| `test_rank_reasons_name_each_key` | Each ranked row has four reasons, in key order, with the real issue numbers and commit count. | A rank that the agent cannot explain. |
| `test_excluded_rows_have_three_fields` | Excluded rows have only `number`, `title`, `excluded_by`. | Facts of excluded PRs fill the cache and the pages. |
| `test_cache_is_byte_identical_twice` | Two `survey` + `write_cache` runs on the fixture give byte-identical files, equal to the golden. | Set iteration order or a timestamp in the output (§12 FR-10 row). |
| `test_page_bytes_below_budget` | 500 synthetic rows with 200-character titles and 30 linked issues: every page of 20 rows is ≤ `8192` bytes (UTF-8). `[TODO: set target — straw man 8 KiB; G-1 sets the final value]` | A field without a limit fills the context (§12 FR-9 row). |
| `test_title_newlines_do_not_break_rows` | A title with `\n` prints on one line. | Untrusted text adds fake rows or a fake `next:` line. |
| `test_page_size_limits` | `page_size` 0 and 51 raise `ValueError`. 50 works. | The page limit is not enforced. |
| `test_page_zero_and_past_end` | Page 0 and page `pages + 1` raise `ValueError`. | An empty page with exit 0. |
| `test_next_line_on_last_page_points_at_capture` | The last ranked page has the `capture --type coding --from-survey` command. The other pages have the next `--page` command. | The agent does not know how to continue. |
| `test_excluded_pages` | `excluded=True` pages list the excluded rows, and the `next:` line keeps `--excluded`. | `--excluded` loses its flag on page 2. |
| `test_empty_ranked_list_has_one_page` | 0 ranked rows give `pages == 1` and an empty `rows` list. | Division by zero, or page 1 out of range. |
| `test_read_cache_errors` | A missing file, a non-JSON file, and a JSON object without `ranked` each raise `FixSurveyCacheError`. | A traceback in place of the `survey-cache-missing` refusal. |
| `test_page_two_is_offline` | After a fresh run, `--page 2` succeeds with `GitCommandRunner.git_with_stderr`, `.gh`, and `.glab` replaced by functions that raise. | A page request calls the forge or git again (FR-8, NFR-2). |
| `test_page_request_without_clone` | `survey --type coding --dataset D --page 1` with no `--clone` works. | The printed `next:` command fails with a usage error. |
| `test_review_still_requires_clone` | `survey --from-json f` with no `--clone` exits 2. | Making `--clone` optional breaks the review contract. |
| `test_cache_missing_refuses` | `--page 1` on an empty dataset refuses `survey-cache-missing`, exit 3, with all four fields. | A traceback on a missing file. |
| `test_all_no_test_change_refuses_plain_words` | A payload where every merged PR has no test change refuses `no-harvestable-fix`, exit 3. `offending` contains `fixes in this repository ship without tests that fail before the fix`. | An empty page with exit 0 (§12 FR-11 row). |
| `test_refusal_is_recorded` | The same run writes the refusal file under `<dataset>/refusals/coding/`, with a file name that has no `/`. | FR-33: the refusal goes only to stderr. |
| `test_cache_written_before_refusal` | After the refusal, `--page 1 --excluded` lists the excluded PRs. | The SDE cannot see why every PR was excluded. |
| `test_no_pull_head_fetch_for_coding` | Spy `Forge.fetch_refs` (as `test_survey.py:172` does). A coding `--repo` run with a replayed `gh` makes no fetch. | The coding survey fails on clones that cannot fetch `refs/pull/*`. |
| `test_one_forge_query_and_one_mainline_pass` | Count `GitCommandRunner.gh` calls and `git log --first-parent` calls on a `--repo` run with a replayed `gh`. Each is 1. | NFR-4 and the §3 performance constraint: one call for each PR. |
| `test_state_defaults` | Coding passes `--state merged` to `gh`. Review still passes `--state all`. An explicit `--state open` wins for both. | The default change leaks into the review path. |
| `test_page_size_out_of_range_is_usage` | `--page-size 0` and `--page-size 51` exit 2. | The page limit is not enforced (FR-9). |
| `test_json_page_shape` | `--json` prints `{page, pages, rows, excluded_counts, next}`. | A driver cannot parse the page. |
| `test_survey_matches_golden` (existing) | The review golden payload passes after all three parts. | A change to a shared function changes the review bytes (NFR-9). |

## Acceptance Criteria

- [ ] `FixSurvey.survey_one` returns every field of `FixFactsDict` for a merged PR, with values from the clone. The output contains no diff text.
- [ ] Each of the eight reasons fires alone on its fixture PR, and `excluded_by` is in the fixed order.
- [ ] A non-merged PR makes no git call. A merged PR makes at most 6 git calls.
- [ ] The ranked order follows the fixed key, and each row has `rank` and four `rank_reasons`.
- [ ] `fix-survey.json` has the §8 keys in a fixed order, and two runs give the same bytes.
- [ ] Each page has at most `page_size` rows (default 20, maximum 50), titles of at most 80 characters, and exactly one `next:` line. Every page of the 500-row test is within the byte budget.
- [ ] `survey --type coding` writes `<dataset>/survey/fix-survey.json` and prints page 1.
- [ ] `--page <n>` without `--repo` or `--from-json` prints page *n* from the cache, and makes no forge call and no git call.
- [ ] When every PR is excluded, the command refuses `no-harvestable-fix` (exit 3), names the most frequent reason in plain words, and writes a refusal record.
- [ ] The coding survey makes one forge list query and one mainline pass, and no pull-head fetch.
- [ ] Every review survey test passes without change, and the review golden output does not change.
- [ ] All commands in "How To Verify" pass.
- [ ] No regressions in the existing suite (NFR-9). `[project].dependencies` stays empty (NFR-6). `tests/unit/test_no_model_client.py` stays green (FR-19).

## Out Of Scope

- Methodology text in `--help`, in `man/eval-harvest-survey.md`, and in `man/eval-harvest-coding.md`: F-1.
- `--type coding` on `capture`, and `capture --type coding --from-survey`, which reads the cache: C-1.
- The coding view of `brief`: C-2.
- The `RefusalLog` module itself: A-2.
- `closingIssuesReferences` from `gh`, or GitLab closing issues. Use the text fallback only. `[TODO: verify the gh pr list --json closingIssuesReferences field]` (tech plan §4 risk table).
- Fetching pull-head refs. The coding survey does not need them (§9: `pull-head-fetch-failed` is not used).
- A weighted score, or a change to the sort key: settled by ADR-6.
- Setting the final NFR-3 byte targets and running the NFR-5 benchmark on 1000 PRs: G-1.

## Notes & Gotchas

**Facts (Part 1):**
- **`git diff --numstat -z` format.** A normal record is `added\tdeleted\tpath\0`. A rename or copy record is `added\tdeleted\t\0old_path\0new_path\0`. Use the new path. A binary file has `-` for both counts. Count it as 0 lines. `GitCommandRunner.git` strips whitespace from stdout but not NUL bytes, so split on `"\0"` and drop empty items.
- **Rename detection must match capture.** Use the default rename detection (no `--no-renames`). C-1 splits `git diff base merge` with `PatchSplit` (A-2), and `PatchSplit` classifies a rename by its `b/` path. If this survey used `--no-renames`, a rename into `tests/` would be "source delete + test add" here and "test only" in capture.
- **`_range_facts` is private.** The repository has no lint rule against private access across classes (`pyproject.toml` `select` has no `SLF`). Tech plan §5 tells you to reuse it, so the first-parent rule stays in one place. Add a short comment at the call.
- **Bot detection.** `gh pr list --json author` gives `{login, is_bot, ...}`. GitLab rows from `GitLab.fetch_merge_requests` (`gitlab.py:208`) have `login` only. Treat a PR as a bot PR when `author.get("is_bot")` is true, or the login ends with `[bot]`, or the login starts with `app/`. `[TODO: verify the GitLab bot user naming, for example project_<id>_bot_<hash>]`.
- **`Survey.issue_candidates` also matches PR numbers.** `#123` in a body can be a PR, not an issue. This is a rank signal only. C-1 fetches the issue and finds out.
- **Line numbers move.** A-1 and A-2 change `survey.py` before this task. Search for the function names. Do not trust the `survey.py` line numbers in this file.
- **Fixture SHAs must be stable.** `RepoBuilder` pins `TZ`, the author, and the dates (`repo_builder.py:84`). For the second author, set `GIT_AUTHOR_EMAIL` in the env of that one commit only.

**Rank and pages (Part 2):**
- **Sort with one key tuple.** `True > False` in Python, so `reverse=True` puts rows with a linked issue first. Do not sort in several passes. That is easy to get wrong.
- **`ensure_ascii=False` and `encoding="utf-8"`.** Titles can have any character. `Cli.encode_output_as_utf8` (`cli.py:268`) makes stdout UTF-8, but a file write must name its encoding. Write with `newline="\n"`, so the file has the same bytes on Windows (NFR-10).
- **The byte budget counts UTF-8 bytes,** not characters. A title of 80 non-ASCII characters can be 320 bytes. Measure with `len(text.encode("utf-8"))`.
- **The cache is overwritten.** A new `survey --repo` run replaces the file (ADR-6 consequences). Do not merge with an old file.
- **The `next:` command is printed for an agent to run.** Quote the dataset path when it has a space. Use `shlex.quote` on the path.

**CLI (Part 3):**
- **`--clone` is required today** (`cli.py:346`). The `next:` line from Part 2 has no `--clone`, so the parser must accept a page request without it. Do the check in the handler, not in argparse.
- **`_load_survey_pull_requests` returns `None` on failure** and already printed the reason. Map `None` the same way as the review path: exit 5 with `--repo`, exit 2 without it (`cli.py:731-733`).
- **GitLab.** `_load_survey_pull_requests` already reshapes GitLab merge requests into `gh` rows (`gitlab.py:160`). Part 1 reads the same row keys, so no GitLab branch is needed.
- **Refusal datapoint names have a `/`.** `acme/widgets` is not a safe file name. `RefusalLog.path_for` (A-2) replaces each `/` with `__`, so the file is `refusals/coding/acme__widgets.json`. Assert this in `test_refusal_is_recorded`.
- **`--from-json` is the test path.** Each test uses a recorded payload, as `test_survey.py` does. No test calls GitHub.

**All parts:**
- §3 constraint: every git call goes through `GitCommandRunner` (`gitcmd.py:130`). Use argv fragments, never a shell string.
- §3 constraint: every refusal uses the four fields and an `ExitCode` value (`cli.py:242`).
- §3 constraint: the filesystem is the bus between verbs. C-1 reads the cache file. It must not call the survey again.

## Dependencies

**Blocked by:** [A-2](A-2-add-the-shared-prerequisites-for-coding-datapoints.md) (`Commit.author_email`, `PatchSplit.is_test_path`, `RefusalLog`)
**Blocks:** [C-1](C-1-add-the-coding-candidate-and-capture-type-coding.md), [F-1](F-1-add-the-per-type-dataset-report-and-the-coding-help.md), [G-1](G-1-measure-the-coding-survey-yield-on-real-repositories.md)
