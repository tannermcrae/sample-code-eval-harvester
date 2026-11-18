# PRD: coding-eval-harvest — coding-agent eval datapoints from the merged fixes of a repository

> **Language:** This document uses ASD-STE100 Simplified Technical English. Command names, file
> names, field names, and product names are technical names. Customer quotes are verbatim.

> **Status: The customer's own work validates the problem. The size of the demand is not known.**
> The customer used a coding agent to make two coding datapoints by hand. These are the
> strands-agents/evals datapoints in notebook 03 of `sample-gen-ai-evaluations-workshop`. The agent
> selected the two most obvious candidates. The customer then asked for more datapoints. The git
> history filled the context window of the agent. The teams that told us about PR pileup also ask
> for coding-agent evals. We did not measure how frequently they ask (§4).

This document adds a second datapoint type to the eval-harvest CLI. It is not a new product. The
customer, the constraints, and the product principles come from
[`../pr-eval-harvest/prd.md`](../pr-eval-harvest/prd.md) and its decisions log. This document
records only the differences.

## 1. North Star

> An SDE who owns a repository points a coding agent at this CLI. The SDE gets Harbor coding-eval
> datapoints from the merged fixes of their own repository. The history of the repository does not
> fill the context of the agent. The SDE uses the datapoints to validate and tune the coding agent
> before they trust it with real work.

## 2. Problem Statement

A coding agent can make a coding datapoint in the style of Senior SWE-Bench when it has a good
candidate. A good candidate has an issue report, the commit before the fix, the merged fix, and
tests that prove the fix. The customer did this two times.

The agent cannot *find* candidates at scale. To select a datapoint, the agent must read the merged
history. It must find which changes were fixes, which changes included tests, which tests fail
before the fix, and which bases still build. On a large repository, this reading fills the context
window of the agent after `[TODO: measure — N candidates or N commits read]`. Thus the customer
gets the 2 most obvious datapoints, and then the work stops.

To compare or tune agents, the customer needs a dataset of `[TODO: set — e.g. 20]` coding
datapoints. Without manual curation, the customer cannot get a dataset of this size.

The CLI has the same function as for PR datapoints. It supplies compact mechanical facts and
selection rules. Thus the agent uses its context for judgment, not for raw history.

## 3. Customer

- **Primary customer:** No change. The customer is the SDE who is responsible for a repository.
  This SDE has no data science background. The SDE wants to use coding agents to automate work, but
  does not trust the agent yet. At the start, the SDE does not know what an eval is. Refer to
  pr-eval-harvest §3.
- **Key characteristics:** The SDE uses the CLI (or will use it) for PR-review datapoints on the
  same repository. The SDE wants coding datapoints from the same tool, in the same procedure.
- **Non-customers:** No change. Public benchmark builders, model vendors, and persons who select
  agents for a market comparison are not customers.

## 4. Evidence & Validation

| Evidence | Source | Status |
|----------|--------|--------|
| A coding agent can make a valid coding datapoint from a selected candidate | The customer made 2 datapoints (strands-agents/evals PR #371, PR #338) with AI. Both pass the oracle and no-op calibration in notebook 03 | Validated |
| When the customer asks the same agent for more datapoints, git history fills its context | The customer's direct experience: *"I just wanted two datapoints. It picked the top two. When I asked for more the context window got big because of the git history."* | Validated (one repository) |
| Customers ask for coding-agent evals | Customer: *"I get asked for this a lot and comes up a lot when talking about this repo"*. The teams that told us about PR pileup also ask | Validated qualitatively. `[TODO: count requests — N asks over the last N months]` |
| Real repositories have sufficient merged fixes with tests that fail before the fix | — | `[TODO: measure the yield on 1–2 real repositories — merged PRs → candidates with a usable fail-to-pass test]` |
| When the dependencies are pinned to the merge date, an old base builds and its suite passes | Notebook 03 did this by hand for 2 tasks | `[TODO: measure the build success rate across a real candidate set]` |

**Assumptions that are not validated:**
- Most of the context cost occurs when the agent *finds and screens* candidates. A smaller part
  occurs when it makes a datapoint from a selected candidate. (The experience of the customer
  supports this. The evidence comes from one repository.)
- An agent can change a PR or an issue into an instruction in the style of Senior SWE-Bench. This
  instruction gives symptoms and no file names or function names. We assume that these instructions
  do not show the answer and do not give too little information for the task.

## 5. Current State & Pain

1. The SDE tells a coding agent to make coding-eval datapoints from their repository. The SDE gives
   the Senior SWE-Bench post or notebook 03 as the example.
2. The agent reads the merged history to find fixes. It selects the 1–2 most obvious fixes.
3. For each fix, the agent finds the base commit and writes an instruction in the style of an issue.
   It separates the fix from its tests, writes hidden tests, and pins the dependencies. It writes
   the Dockerfile, the validation spec, and the rubric. It examines the result for leaks.
4. **Breakdown:** When the SDE asks for more datapoints, the agent reads farther back in the
   history, and its context becomes full. The agent loses earlier candidates or earlier work. The
   dataset stops at the first small set.
5. No mechanical check examines the result. The agent must remember to make sure that hidden tests
   fail at the base. It must also remember to make sure that the instruction does not show the fix.

Cost: `[TODO: measure — time per hand-built datapoint, context resets per datapoint]`. The dataset
size that the customer can get today is 2.

## 6. User Stories

**US-1: Learn the coding-datapoint method from the CLI**
As the coding agent of the SDE, I want `--help` and the man pages to teach what a coding datapoint
is and how to make one. Then an agent with no supervision can make datapoints without a method in
its prompt.

**Acceptance criteria:**
- [ ] The documentation gives the task shape. The task shape is: an instruction in the style of an
      issue, the base commit, the oracle fix, fail-to-pass tests, pass-to-pass tests, the validation
      spec, the task rubric, the taste judge, and patch bloat. The documentation also tells which
      properties make a datapoint worthless.
- [ ] The documentation tells how a coding datapoint is different from a PR-review datapoint. Thus
      the agent does not confuse the two types.
- [ ] An agent that has only the help output makes a datapoint. This datapoint passes all US-6
      checks. (This is the kill-criterion test from pr-eval-harvest.)

**North star link:** There is no human review step. Thus the documentation is the method.

---

**US-2: Survey candidates without reading history**
As the coding agent of the SDE, I want the CLI to list candidate fixes in a sequence that
mechanical facts set. I want the output to have a limited size and to be in pages. Then I can
screen hundreds of merged changes, and git history does not fill my context.

**Acceptance criteria:**
- [ ] For each candidate, the CLI gives these facts, and the agent does not read a diff: a linked
      issue is present or not; the change touches tests and source separately or not; the size of
      the source change; the files that the change touches; and the number of commits that the
      author has in the repository.
- [ ] The CLI applies selection rules that come from the selection of Senior SWE-Bench (real fixes,
      authors with experience, the change includes tests, the size is not trivial but has a
      limit). The CLI tells why each candidate has its position. It also tells why it excluded
      each excluded change.
- [ ] The size of each page has a limit. The size of the repository does not change this limit.
      The agent can continue a survey and does not read again the pages that it has.
- [ ] The same forge snapshot gives the same candidate list. (This is the determinism rule from
      pr-eval-harvest.)

**North star link:** This is the north star: the agent finds datapoints at scale, and history does
not fill its context.

---

**US-3: Capture the material of one candidate**
As the coding agent of the SDE, I want the CLI to give me all the material for one datapoint. Then
I make the datapoint from one compact bundle and I do not explore the repository.

**Acceptance criteria:**
- [ ] The bundle contains the base commit (the parent of the fix), the fix in two parts (source
      changes and test changes), the text of the PR and of the linked issue, and the tests that the
      PR adds or changes.
- [ ] The CLI refuses a candidate if it cannot recover the base or the fix. The CLI tells why.

**North star link:** The context for each datapoint stays small. Thus the agent can make many
datapoints.

---

**US-4: Record the judgment of the agent with its evidence**
As the coding agent of the SDE, I want the CLI to record these items with my reasons: the
instruction that I wrote, the hidden tests that I selected or wrote, the validation spec, and the
task rubric. Then each bad datapoint has a cause that I can find.

**Acceptance criteria:**
- [ ] The agent writes the instruction in the style of Senior SWE-Bench. The instruction gives the
      symptoms and the expected behavior. It does not give file paths, function names, or the cause.
- [ ] The hidden tests can start from the tests of the PR, or the agent can write them. In the two
      conditions, the CLI records each test as fail-to-pass or pass-to-pass, with its evidence.
- [ ] The CLI makes no judgment of its own. (There is no LLM in the CLI. This rule comes from
      pr-eval-harvest.)

**North star link:** The agent supplies the judgment, and the CLI keeps an audit record of it. This
division makes it safe to find datapoints without supervision.

---

**US-5: A base that builds and stays built**
As the SDE, I want the environment of each datapoint to build at the base commit, with the
dependencies pinned to the merge date of the fix. I want the existing suite to pass. Then a trial
failure tells me that the agent failed, not the environment.

**Acceptance criteria:**
- [ ] The CLI refuses a datapoint if its base does not build, or if its existing suite does not pass
      at the base. The refusal gives the name of the step that failed.
- [ ] The environment is sealed: one commit that the agent can reach, no remotes, no hidden tests,
      and no fix.

**North star link:** A datapoint that fails because of its environment measures nothing.

> **Tech-plan note (2026-11-17):** The first criterion needs a runner that builds the environment
> and runs the suite. A separate project will supply this runner. The runner writes a proof file,
> and `verify` reads it. Until a valid proof file exists, `verify` reports this check as
> *unresolved* (exit 5). It never reports the check as passed without a proof. Refer to the tech
> plan, ADR-2 and FR-35.

---

**US-6: Verify that the datapoint tests the correct thing**
As the coding agent of the SDE, I want the CLI to prove that each datapoint has the correct form
before the dataset counts it. Then an agent without supervision cannot emit a worthless task that
looks correct.

**Acceptance criteria:**
- [ ] Each fail-to-pass test fails at the base and passes with the oracle fix. Each pass-to-pass
      test passes at the base and with the oracle fix. The CLI refuses each test that does not obey
      these rules, and it gives the name of the test.
- [ ] A leakage scan examines all the files that the agent can read. The scan looks for these
      items: PR numbers and issue numbers, the merge commit, the names of the hidden tests, the
      lines that the fix adds, and the file paths and symbols that the fix adds or changes when the
      instruction names them. A planted canary proves that the scan ran.
- [ ] The general-instructions part of `instruction.md` is the same in all coding datapoints. Only
      the declared slots are different. (This is the structural-identity rule from pr-eval-harvest.)
- [ ] The oracle fix is only in `solution/`, and the reference patch is only in `tests/`. Neither is
      in a location that the agent under evaluation can read.
- [ ] Each failure comes back as an instruction that the agent can do.

**North star link:** The customer cannot audit an eval. Thus these checks are the only quality gate.

> **Tech-plan note (2026-11-17):** The first criterion also needs the runner from US-5. `verify`
> reads the proof file of the runner. Until a valid proof file exists, `verify` reports this check
> as *unresolved*. All the other US-6 checks are static and the CLI does them. Refer to the tech
> plan, ADR-2 and FR-35.

---

**US-7: One dataset for the two datapoint types**
As the SDE, I want the CLI to emit coding datapoints into the same dataset and report as my
PR-review datapoints. Then one repository gives one eval, and I give that eval to `harbor run`.

**Acceptance criteria:**
- [ ] The dataset report counts the datapoints of each type. It gives the refusal reasons for each
      type.
- [ ] If a repository cannot give coding datapoints, the refusal tells why in words that the SDE
      can use. Example: "fixes in this repository ship without tests that fail before the fix."

**North star link:** The customer gets the dataset that they asked for, from the tool that they
use now.

## 7. Proposed Solution (User Experience)

1. **The SDE asks for datapoints.** "Build me coding-eval datapoints from `our-org/our-repo`."
   *(US-7)*
2. **The agent reads the help of the CLI** and learns the method for coding datapoints. *(US-1)*
3. **The agent surveys the candidates.** It reads a compact list of fixes, with facts and reasons,
   one page at a time. It does not read the raw history. It uses its own judgment to select
   candidates. *(US-2)*
4. **For each candidate, the agent captures the bundle.** The agent writes the instruction, the
   hidden tests, the validation spec, and the rubric. The CLI records each judgment with its
   evidence. *(US-3, US-4)*
5. **The agent emits the datapoint, and the CLI verifies it.** The CLI examines the seal, the
   placement of the answer, and the leakage scan. A separate runner examines the build and the
   tests at the base and with the oracle. The CLI sends each failure back as an instruction. The
   agent corrects the problem and tries again. *(US-5, US-6)*
6. **The SDE gets a dataset.** The dataset contains coding datapoints and PR-review datapoints,
   with counts and refusal reasons. *(US-7)*

### Constraints the customer brings

All the constraints come from pr-eval-harvest §7: Harbor is the output format. Lambda MicroVMs is
the execution substrate. There is no LLM in the CLI (LLM judges in the task are permitted). The CLI
does not run evals or score agents. The CLI supports the forges that it supports now (GitHub,
GitLab). New: the shape of a datapoint copies
[Senior SWE-Bench](https://senior-swe-bench.snorkel.ai/blog/2026-06-16-how-it-works). The datapoints
of notebook 03 are the working example.

## 8. Success Criteria

| Metric | Current | Target |
|---|---|---|
| The number of coding datapoints that an agent without supervision makes from one repository before it stops | 2 | `[TODO: set — straw man ≥20 in one session, no context reset]` |
| Datapoints that pass all US-6 checks: on the first attempt / after the agent uses the feedback of the CLI | no mechanical check | `[TODO: set — straw man 80% / 100%, the inherited kill criterion]` |
| Agent context that each surveyed candidate uses | not measured, no limit | `[TODO: measure and set a bound]` |
| Fail-to-pass tests that fail at the base and pass with the oracle | a person examines them, if at all | 100% |
| Datapoints that the CLI emits with an answer that leaks | unknown | 0 |
| In Harbor, the oracle trial is a tasteful solve, and the no-op trial scores 0 | 4 of 4 runs in notebook 03 | 100% on a sample of `[TODO: N]` datapoints. The customer runs this. The CLI does not |

**Sufficient for v1:** We use one real repository that the agent did not see before. An agent
without supervision uses only the help output and makes `[TODO: N]` coding datapoints from start to
end. Each US-6 check exists, and we saw each check fail. A sample passes the oracle and no-op
calibration in Harbor.

## 9. Open Questions & Risks

- **Yield risk.** The repository can have few fixes with tests that fail before the fix. Measure
  the yield on 1–2 real repositories early. If the yield is low, the refusal message (US-7) is the
  product for those repositories.
- **Environment drift.** Old bases can fail to build, also with pinned dependencies. We did not
  measure the build success rate. The test runs at the base and with the oracle need a build that
  works. Thus this risk controls US-6.
- **Instruction quality.** The instruction that the agent writes can show the answer (too much
  detail) or can give too little information (the task cannot be solved). The leakage scan finds the
  first problem mechanically. Only the oracle runs of the customer and a guided variant find the
  second problem. The guided variant is deferred.
- **Hidden tests that the agent writes can be incorrect, and still pass the base/oracle check.** For
  example, a test that pins an implementation detail of the oracle rejects a different valid fix.
  Senior SWE-Bench decreases this risk with behavioral tests through system interfaces. The
  documentation must require this (US-1). No mechanical check finds this problem.
- **The test runs are new work for `verify`.** PR-review verification never runs the code of the
  repository. The tech plan decides where the runs occur. (Decision: a separate runner does them.
  Refer to the tech plan, ADR-2.)

## 10. Out of Scope

**Deferred, with a seam kept:**
- **Design-and-Build tasks** (features and migrations, instructions as PM user stories, changes to
  interfaces). v1 does Investigate-and-Fix only. The datapoint records its task type. Thus a second
  type does not change the format.
- **Guided variants** (more hints for each task; Senior SWE-Bench uses them to make sure that a task
  can be solved).
- **One task from a group of related PRs.**
- **Repeated oracle/no-op trials (pass^3) and analysis of reward hacking in trajectories.** These
  are eval runs. The customer does them in Harbor.
- **The runner that builds each environment and runs the hidden tests at the base and with the
  oracle.** The customer said: *"Don't build a new runner for this. That can be separate. Just focus
  on getting the datpoints."* The CLI defines the proof file that the runner writes. The `verify`
  checks that need this runner report *unresolved* until a valid proof file exists (tech plan
  ADR-2, FR-35).

**Cut:**
- **Eval runs, scores, and ranks.** This comes from pr-eval-harvest.
- **An LLM in the CLI.** This comes from pr-eval-harvest.
- **Synthetic bugs.** Fixes come from the merged history of the repository.

## 11. Discovery: The 5 Customer Questions

**Q1: Who is the customer?**
*"Yes. Same customer."* Then: *"Same customer same everything. Just new types of datapoints."* The
customer wants the CLI to harvest *"code eval datapoints. Both for PRs and for actual code eval
datapoints. We want to mimic the approach senior swe bench works."* The customer referred to
notebook 03 for examples. The customer said that notebook 03 uses Codex as the agent under test,
and said *"We just care about generating the datapoints though."*

**Q2: What is the problem or opportunity?**
*"I built it. I used AI. It's not so much you can't point a coding agent at a repo and have it
figure it out with examples. It's that looking through big repos is tough and it blows up the
context window. The CLI's value prop is the same. Just different mining."* Later: *"I just wanted
two datapoints. It picked the top two. When I asked for more the context window got big because of
the git history. You're trying to bring heuristics to the table to find the datapoints."* The north
star is to find datapoints at scale. The customer did not name the construction of datapoints as a
pain.

**Q3: What is the most important customer benefit?**
The customer did not answer directly. The customer said that the value proposition is the same as
for PR datapoints. Thus the benefit from pr-eval-harvest applies: speed (an eval exists today). The
customer did not explicitly accept the proposed north star. The customer asked the PM to *"do your
best answering this and making sense of it all."*

**Q4: How do you know what your customer wants or needs?**
The customer made 2 datapoints, and the context window failed when the customer asked for more. The
PR-pileup teams also ask for coding evals: *"yes."* The customer said that the work has value
because *"I get asked for this a lot and comes up a lot when talking about this repo."* The customer
did not give a count. The customer did not give a kill criterion for coding. Thus the kill criterion
from pr-eval-harvest applies.

**Q5: What does the experience look like?**
We did not discuss this. §7 is an extrapolation by the PM from the PR-review experience and from
notebook 03. The scope cuts in §10 (Design-and-Build, guided variants, PR groups) are decisions of
the PM, not of the customer.

**Discovery gaps:** We did not get confirmation of the decision that this feature makes possible
("validate and tune the coding agent before trusting it with real work"). We inferred it from the
PR purpose chain. We did not count the requests. We did not discuss the Q5 experience or the v1
limit to Investigate-and-Fix. We did not measure the yield or the build success rate on real
repositories.
