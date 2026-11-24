# eval-harvest-coding(7)

## NAME

**eval-harvest-coding** - coding datapoints

## SYNOPSIS

```
eval-harvest survey --type coding --clone <dir> --repo <org/name> --dataset <dir>
eval-harvest survey --type coding --dataset <dir> --page <n> [--page-size <k>] [--excluded]
eval-harvest capture <pr> --type coding --clone <dir> --dataset <dir>
eval-harvest capture --type coding --from-survey <dataset>/survey/fix-survey.json --clone <dir> --dataset <dir>
eval-harvest brief <dataset>/candidates/fix-<n>.json
eval-harvest show <dataset>/candidates/fix-<n>.json --path <file>
eval-harvest annotate <dataset>/candidates/fix-<n>.json (--stdin | --from-json <file> | --check)
eval-harvest emit <dataset>/candidates/fix-<n>.json --kind fix [--clone <dir>]
eval-harvest verify <dataset>/tasks/<task-dir> [--clone <dir>] [--proof <file>]
eval-harvest dataset --dataset <dir>
```

## DESCRIPTION

A **coding datapoint** is a Harbor task built from one merged fix of your repository. The coding
agent under test receives an issue-style instruction at the base commit, and it must produce a
patch that fixes the issue. Hidden tests, the validation spec, the task rubric, the taste judge,
and patch bloat score the patch. A review datapoint, by contrast, scores a review agent's verdict on
one PR review round; `eval-harvest(1)` compares the two types.

The coding verbs are the review verbs with `--type coding` (`survey`, `capture`) or `--kind fix`
(`emit`); `brief`, `show`, `annotate`, `verify`, and `dataset` read the type from their input. As for
review, the CLI vends facts and never calls a model: you (the driving agent) write the instruction,
pick the hidden tests, write the rubric, and write the environment recipe.

## METHODOLOGY

1. `survey --type coding` reads the merged PRs once, excludes each fix that cannot become a task,
   ranks the rest, and saves `survey/fix-survey.json`. Read it a page at a time with `--page <n>`:
   a page request makes no forge call and no git call, so a long history never fills your context.
   When no fix is harvestable it refuses `no-harvestable-fix` with the most common reason.
2. `capture --type coding` writes `candidates/fix-<n>.json`: the merge and base commits, the linked
   issues, the fix split into a source patch and a test patch, and blank slots. It refuses
   `fix-base-unrecoverable` and `no-source-change`.
3. `brief` prints the coding brief, and `show --path <file>` prints the fix for one file. Hand each
   fix to a fresh context.
4. `annotate` fills the slots: the instruction, the hidden tests, the rubric criteria, and the
   environment recipe. Run `annotate --check` until it is clean.
5. `emit --kind fix` builds the task and runs `verify`.
6. A runner proves the hidden tests and writes `proofs/<task-dir>.json` (see PROOF FILE).
7. `dataset` counts the types apart and gives one `harbor run` command for each type.

**Hidden tests must test behavior through public interfaces; they must not pin implementation
details of the oracle fix.** A hidden test that pins a helper name or an internal call fails a
correct patch that differs from the oracle. Keep pass-to-pass tests next to the fail-to-pass tests:
they catch a patch that fixes the issue and breaks something else.

**The environment recipe is yours, for any language.** `base_image` must be in `public.ecr.aws/`.
`setup_steps` is one line for each setup step; each becomes one `RUN` line. `lockfile` names a file
in `candidates/fix-<n>/`. Pin dependencies to `merge_date`, so that an old base still builds; for
Python: `uv pip compile --exclude-newer <merge_date>`. `test_command` must write JUnit XML to
`$JUNIT_PATH`, and pytest needs `-o junit_family=xunit1` so that each `<testcase>` has a `file`
attribute: the verifier matches hidden tests by it.

**A coding datapoint is unproven until a runner writes a valid proof file.** The CLI never runs the
repository's code, so `hidden-tests-proven` stays unresolved (exit 5) in `verify` until a runner
writes `proofs/<task-dir>.json`; `verify --proof <file>` reads another file. `dataset` counts these
datapoints as unproven, and a missing, stale, or failing proof counts the same way.

**A worthless datapoint.** A coding datapoint is worthless when any of these is true:

- a fail-to-pass test passes at the base (it proves nothing about the fix);
- the instruction names a changed path or symbol (`instruction-names-fix`);
- the general instructions are not the same in all tasks (`instruction-not-uniform`);
- the answer is in a file that the agent can read (`answer-misplaced`);
- the hidden tests pin details of the oracle, so a correct, different fix fails them.

`verify` checks the first four (the first through the runner's proof); the last is yours to prevent.

**Use the per-type means.** `registry.json` has one entry for all tasks (`<name>`) and one for each
type that has tasks (`<name>-review`, `<name>-coding`). Harbor counts a missing reward key as 0, so a
mixed run dilutes every mean. Run each type alone with the `harbor run` command `dataset` prints.

## TASK SHAPE

```
tasks/<org>__<name>__pr<n>-fix/
  task.toml
  instruction.md                   # canonical template + @TASK@ + @TOOLING@
  environment/Dockerfile           # seal at base, then the agent's setup steps
  environment/<lockfile>
  solution/solve.sh                # git apply /solution/oracle.patch
  solution/oracle.patch            # the source part of the merged fix
  tests/Dockerfile                 # verifier image: pristine base at /work/base + same setup + boto3
  tests/<lockfile>                 # copy of the environment lockfile (tests/ is the build context)
  tests/test.sh
  tests/verify/tests.toml          # test_command, junit_path, protected test markers, F2P/P2P ids
  tests/verify/<hidden test files>
  tests/validate/validation_spec.toml
  tests/judge/{reference.patch, instruction.md, rubric.toml, models.toml}
  tests/lib/{aggregate,agent_tests,validate,judges,bedrock}.py
```

Each item of the task, in one sentence:

- **instruction style:** an instruction in the style of an issue that names the symptom, not the
  file or the symbol.
- **base commit:** the first parent of the merge commit, the code before the fix.
- **oracle fix:** the source part of the merged fix, only in `solution/`, which the agent cannot
  read.
- **fail-to-pass:** a test that fails at the base and passes with the oracle.
- **pass-to-pass:** a test that passes at the base and with the oracle.
- **validation spec:** `tests/validate/validation_spec.toml`, stories with cases.
- **task rubric:** criteria of kind `fail_to_pass` or `pass_to_pass`.
- **taste judge:** an LLM judge that compares the patch to the reference and to good practice.
- **patch bloat:** a penalty for changes that the fix does not need.

## PROOF FILE (for runner authors)

The CLI does not run the repository's code, so `eval-harvest verify` cannot prove on its own that
each hidden test fails at the base and passes with the fix. A **runner** outside the CLI proves it:
it runs the emitted task in Harbor two times — with the `nop` agent (no change: the base) and with
the `oracle` agent (the fix applied) — and writes the results to

```
<dataset>/proofs/<task-dir>.json
```

`verify <dataset>/tasks/<task-dir>` reads that file; `--proof <file>` reads another one. The CLI
reads the proof and never writes it. The emitted verifier writes the per-test results to
`/logs/verifier/verifiers.json`, so a runner can copy them from each trial directory.

### Schema

```json
{
  "content_digest": "sha256:…",
  "runner": "<name and version of the runner>",
  "nop":    {"status": "completed", "build_error": "", "tests": {"tests/test_x.py::test_f2p_a": "failed", "tests/test_x.py::test_p2p_b": "passed"}, "existing_suite": "passed"},
  "oracle": {"status": "completed", "build_error": "", "tests": {"tests/test_x.py::test_f2p_a": "passed", "tests/test_x.py::test_p2p_b": "passed"}, "existing_suite": "passed"}
}
```

| Field | Type | Rule |
|-------|------|------|
| `content_digest` | non-empty string | Equal to `[metadata.harvest].content_digest` in the task's `task.toml`. Another value is a stale proof. |
| `runner` | non-empty string | The name and version of the runner. |
| `nop`, `oracle` | object | One trial each, with the four fields below. |
| `<trial>.status` | `"completed"` or `"build-failed"` | Any other value makes the proof invalid. |
| `<trial>.build_error` | string | The failed build step when `status` is `"build-failed"`; else `""`. |
| `<trial>.tests` | object of string results | Test id to result. `"passed"` is a pass; any other value (`"failed"`, `"error"`, …) is not. |
| `<trial>.existing_suite` | string | The result of the repository's own suite. |

### Test ids

A test id is `<repo_path>::<name>`, the same id as in the task's `tests/verify/tests.toml`
(`tests/test_x.py::test_f2p_a`). `verify` reads the ids and their kinds (`fail_to_pass`,
`pass_to_pass`) from `tests.toml`, not from the proof, so a proof cannot drop a test by leaving it
out. A listed test with no entry in a trial's `tests` is a failure. A test id in the proof that
`tests.toml` does not list is ignored.

### The rules

- **nop trial:** each `fail_to_pass` test is not `"passed"`; each `pass_to_pass` test is
  `"passed"`; `existing_suite` is `"passed"`.
- **oracle trial:** each listed test is `"passed"`; `existing_suite` is `"passed"`.

### The three results

- **Pass** (exit 0, when no other check fails or is unresolved): the proof is valid, its digest is
  the task digest, and every rule holds. `hidden-tests-proven` is in the passed checks.
- **Failure** (exit 4): `base-does-not-build` when a trial has `status = "build-failed"`, with the
  failed step (`nop trial: RUN uv pip sync`); otherwise one `hidden-tests-proven` failure for each
  test or suite that breaks a rule, naming the test and the rule
  (`'tests/test_x.py::test_f2p_a' passes at the base`).
- **Unresolved** (exit 5), never a pass: no proof file, a file that does not parse, a missing or
  mistyped field, or a `content_digest` that is not the task digest. `offending` says which.

`verify` trusts the runner: the digest stops a stale proof, not a false one.

## EXIT STATUS

As for every verb: `0` success · `2` usage error · `3` refusal (`no-harvestable-fix`,
`fix-base-unrecoverable`, `no-source-change`, `environment-recipe-invalid`, …) · `4` verification
failure (`instruction-names-fix`, `hidden-tests-proven`, `base-does-not-build`, …) · `5` unresolved
(`hidden-tests-proven` with no valid proof).

## SEE ALSO

`eval-harvest(1)`, `eval-harvest-survey(1)`, `eval-harvest-capture(1)`, `eval-harvest-brief(1)`,
`eval-harvest-show(1)`, `eval-harvest-annotate(1)`, `eval-harvest-emit(1)`, `eval-harvest-verify(1)`,
`eval-harvest-dataset(1)`.
