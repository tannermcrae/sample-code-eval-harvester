# eval-harvest-coding(7)

## NAME

**eval-harvest-coding** - coding datapoints

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
