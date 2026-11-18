# Decisions — coding-eval-harvest (PRD)

A running log of the decisions that shaped this feature's requirements, newest session at the
bottom. One paragraph each: what was decided, and why it was decided that way. This exists so a
later session does not re-argue something already settled, and so the reasoning survives when the
PRD gets rewritten. Where a decision reverses an earlier one, the reversal says so. The product-level
decisions this feature builds on live in
[`../pr-eval-harvest/decisions-prd.md`](../pr-eval-harvest/decisions-prd.md).

## 2026-11-17 — discovery session that produced `prd.md`

**Reverses pr-eval-harvest's cut of "PR authoring evaluation and coding-agent benchmarks
generally": coding-agent datapoints are now in scope as a second datapoint type.** That PRD cut
them outright and separately deferred "eval types other than PR review". The customer now wants
the CLI to produce *"code eval datapoints. Both for PRs and for actual code eval datapoints."* It is
the same CLI, not a new product.

**Same customer, same everything; only the datapoint type is new.** *"Same customer same
everything. Just new types of datapoints."* Every pr-eval-harvest product decision carries over
unchanged: an agent-native CLI like `gh`, no LLM inside the CLI, scope ends at a verified task
directory, Harbor format on Lambda MicroVMs, no human verification step, the docs are the product,
the kill criterion of an agent producing garbage unsupervised, and the ban on "load-bearing".
Re-argue none of these for coding datapoints.

**The problem is mining at scale, not datapoint construction.** The customer built two coding
datapoints with a coding agent and examples, so construction is solvable without the CLI. What
fails is finding more: *"I just wanted two datapoints. It picked the top two. When I asked for more
the context window got big because of the git history. You're trying to bring heuristics to the
table to find the datapoints."* So the CLI's main contribution is a bounded, ranked candidate survey
with the heuristics built in, the same value proposition as for PR datapoints (*"just different
mining"*). Construction help (the capture bundle, verification) supports it.

**The datapoint shape mimics Senior SWE-Bench, with notebook 03 as the working example.** That
means an issue-style instruction, the base commit, the merged fix as oracle, fail-to-pass and
pass-to-pass hidden tests, a validation spec, a task rubric, a taste judge, and patch bloat. The
pr-eval-harvest caution applies: inspiration, not a template. The customer only cares about
generating datapoints: *"We just care about generating the datapoints though."*

**v1 is Investigate-and-Fix only; Design-and-Build is deferred (PM's call, confirmed below by the
customer).** Notebook 03's working datapoints are both bug fixes. Their instruction is a symptom
report and the interfaces do not change, so the hidden tests run against the agent's code without
guessing at new signatures. Design-and-Build needs PM-style user stories and interface-adapting
validation, which nothing has proven yet. The datapoint records its task type, so adding the second
type later changes no format.

**Hidden tests may be agent-written, but every one is proven against base and oracle.** The PR's
own tests are the preferred starting point. The driving agent may also write tests, as in notebook
03. This does not violate pr-eval-harvest's cut of synthetic defects: the bug and the fix are still
real, and only the test that observes them is written. A fail-to-pass test must fail at the base and
pass with the oracle, and a pass-to-pass test must pass at both. A test that does neither is refused.

**Running hidden tests against base and oracle is verification, not running an eval — consistent
with pr-eval-harvest, not a reversal.** pr-eval-harvest rejected a 3× oracle / 3× no-op gate because
*"You have to verify the eval is testing the right things. You're not responsible for running the
eval."* For a coding datapoint, the test that proves a fail-to-pass test tests the right thing is
to run it at the base and with the fix. That is deterministic, involves no agent and no LLM, and
covers the no-op case, since a no-op leaves the base unchanged. Harbor trials, LLM-judge
calibration, and pass^3 remain the customer's job.

**The driving agent writes the instruction; the CLI checks it for leaks mechanically.** Senior
SWE-Bench instructions read like a teammate's message and never name files, functions, or the
cause. Rewriting a PR or issue into that form is judgment, so it belongs to the agent (no LLM in
the CLI). The leakage scan therefore extends beyond pr-eval-harvest's to cover file paths and
symbols that the fix introduces or changes, named in the instruction.

**Coding and PR-review datapoints share one dataset and one report.** One repository yields one
eval that the customer hands to `harbor run`, from the tool they already use.

**Deferred, with reasons:** guided variants, grouping related PRs into one task, repeated
oracle/no-op trials, and reward-hacking trajectory analysis. Guided variants and grouping are not
needed to mine the first dataset. Repeated trials and trajectory analysis are eval runs, which the
customer does.

**File naming follows the repo's convention, `decisions-prd.md`, not `decisions.md`.** The other
features keep requirements decisions and tech-plan decisions in separate files, and the tech plan
will add `decisions-tech-plan.md`.

**The customer accepted the PRD as written, including the PM's calls.** Reviewing the draft, the
customer said *"This is fine."* This settles the PM's four calls: v1 is Investigate-and-Fix only,
agent-written hidden tests are proven against base and oracle, `verify` runs the hidden tests, and
the leakage scan is extended. It also confirms the inferred purpose of validating and tuning the
coding agent before trusting it with real work. These are no longer unconfirmed. The measurement
`[TODO]`s in the PRD remain open.

## 2026-11-17 — technical plan session

**The PRD is now in ASD-STE100 Simplified Technical English.** The user asked for this. The rewrite
changed the language, not the requirements. Customer quotes stay verbatim, and every `[TODO]` stays
as it was.

**Reverses the entry above, "Running hidden tests against base and oracle is verification", in one
part: `verify` does not do the run in this feature.** The user said: *"Don't build a new runner for
this. That can be separate. Just focus on getting the datpoints."* The check stays a requirement and
the reasoning of that entry stays correct: the run is verification, not an eval. A separate runner
will do the run. Until then `verify` reports the check as unresolved, never as passed. The PRD now
has a tech-plan note on US-5 and US-6 and a deferred item in §10. Refer to
`decisions-tech-plan.md` and ADR-2 of the tech plan.
