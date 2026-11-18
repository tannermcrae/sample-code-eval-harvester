# Decisions — coding-eval-harvest (technical plan)

A running log of the decisions that shaped the technical plan of this feature, newest session at the
bottom. One paragraph each: what was decided, and why it was decided that way. This exists so a later
session does not re-argue something already settled, and so the reasoning survives when the tech plan
gets rewritten. Where a decision reverses an earlier one, the reversal says so. Requirements-level
decisions live in `decisions-prd.md`.

## 2026-11-17 — technical plan

**The PRD and the tech plan use ASD-STE100 Simplified Technical English.** The user asked for this
after the first questions of the session. Command names, file names, field names, and product names
are technical names. Customer quotes stay verbatim. The PRD rewrite changed the language, not the
requirements. The only content additions are two tech-plan notes on US-5 and US-6 and one deferred
item in §10. They record the runner decision below.

**The CLI does not run the code of the repository. `hidden-tests-proven` is specified and always
reported unresolved (ADR-2).** This reverses the PRD-session decision "running hidden tests against
base and oracle is verification", in the part that says `verify` does the run. We asked where the
runs occur: local Docker, a Lambda MicroVMs runner, or agent-run Harbor trials. The user answered:
*"Don't build a new runner for this. That can be separate. Just focus on getting the datpoints."*
Thus the base-build check (US-5) and the fail-to-pass and pass-to-pass proof (US-6) report exit 5,
never a pass. `dataset` counts these datapoints as unproven. Two PRD acceptance criteria are not
satisfied in this feature, and the spec says so.

**`verify` for coding is the existing `verify` with a different patch and more tokens. It is not a
new verifier.** We first sketched a `verify --trial-results` option that reads Harbor oracle and nop
trials. The user corrected this: *"Why is verify any different/ We're just using a git patch and
ifferetn metric calculator. Otherwise It's the same isn't it?"* The review checks map directly to
coding: `base-and-patch` runs on `solution/oracle.patch`, the content scan with its control token
gets the coding tokens, and `git-channel-absence` does not change. `finding-lines-present` does not
apply. The only additions are static: a scan of `instruction.md` for changed paths and symbols, a
uniform-instruction check, an answer-placement check, and a spec check. We removed trial ingestion,
because the runner project owns that contract.

**The instruction scan for paths and symbols does not use the base-tree suppression.** The existing
content scan suppresses a token that exists at the base (`verify.py:459`), so that it does not flag a
token that the datapoint did not add. Changed file paths and symbols always exist at the base. With
suppression, the scan would hide each leak that it must find. Thus the new check scans only
`instruction.md`, with no suppression and with its own control token.

**Coding is a second type on the same verbs: `--type coding` and `--kind fix` (ADR-1).** The user
selected this over a verb namespace. It agrees with the PRD ("from the same tool, the same way").
`--kind fix` matches the `pr338-fix` task names of notebook 03. The coding schema and assembly are in
new modules (`fix_survey.py`, `fix_candidate.py`, `fix_emit.py`), because the review candidate and
task shape are pinned by contract entries in the pr-eval-harvest log. A polymorphic candidate would
change those contracts.

**The agent writes the environment recipe, for any language (ADR-3).** The user selected this over a
Python-only lock that the CLI makes. A CLI-made lock would need `uv` and the network during `emit`,
and this breaks the offline rule. The CLI supplies `merge_date` and wraps the recipe after the sealing
steps. Setup steps must be single lines from an ECR Public base image, because a line break can add a
Dockerfile instruction.

**Coding datapoints use separate verifier mode, with the agent tree as a Harbor artifact (ADR-4).**
The user selected this over the shared mode of notebook 03. Shared mode would relax pr-eval-harvest
FR-32, because there the agent can change what scores it. Harbor 0.22.0 `ArtifactConfig` has an
`exclude` field (`harbor/models/task/config.py:639`). Thus `{source = "/work/repo", exclude =
[".git"]}` carries the working tree without the history. The verifier builds a pristine base and
calculates the patch of the agent itself. The cost is a second image build for each trial.

**The reward stack is the notebook 03 stack, ported, with `fix_` keys and per-type means (ADR-5).**
Notebook 03 calibrated it on 4 of 4 runs, so we port it and do not write a new one. Harbor's
`aggregate_reward_dicts` gives 0 to a missing key (`harbor/metrics/base.py:14`). We read this in the
source, not in the documentation. In a mixed dataset, Harbor's `Mean()` thus dilutes each key with the
tasks of the other type. The namespaced keys stop the two meanings from mixing. The dataset
`metric.py` calculates per-type means. When an LLM stage fails, the coding stack writes no reward and
the trial is invalid. The review `test.sh` does a different thing (a zero reward). Both are deliberate.

**The survey writes a ranked cache and prints pages from it. The rank is a fixed sort key, not a
weighted score (ADR-6).** A page number is the cursor. Thus a page needs no network, and all pages
come from one snapshot (FR-10). A weighted score was rejected, because a weight is a judgment with no
evidence, and a number does not tell the agent why. The size limits (3–400 source lines, 12 files)
are straw-man values. G-2 calibrates them on real repositories.

**The base of a coding datapoint is the first parent of the integration commit, not the merge-base of
an iteration tip.** The review capture uses the merge-base (`forge.py:286`), because it reviews what a
PR put up at one iteration. A coding datapoint needs the mainline state just before the fix landed,
and the PRD defines this as "the parent of the fix".

**The emitted coding tasks cannot run on Lambda MicroVMs today. The customer uses Harbor's Docker
environment.** `harvest_env` rejects `no-network` and allowlist policies (`lambda_microvms.py:235`),
and every emitted task uses `no-network` for the agent. This applies to the review tasks also. It is a
constraint on the runner project, not a change in this feature.

**Refusals are written to `<dataset>/refusals/<type>/` so that `dataset` can report reasons for each
type.** US-7 requires refusal reasons by type. Today refusals go only to stderr and to the batch
summary, so `dataset` cannot see them. A successful emit removes the old refusal of that datapoint, so
the report shows only current refusals.

**A discrepancy in the existing survey stays out of scope.** `no-test-change` is a blocker for review
PRs, and `Survey._harvestable` (`survey.py:568`) excludes every PR with a blocker. But the remedy
text of this blocker says that such a PR is "harvestable for review". This feature uses
`no-test-change` as a true exclusion for coding. It does not change the review behavior, because that
change would alter the pinned review survey. A separate fix must decide which behavior is correct.

## 2026-11-17 — follow-up on the open points

**A runner proves the hidden tests through a proof file that `verify` reads (ADR-2, FR-35).** The
earlier entry made `hidden-tests-proven` always unresolved. This entry keeps the rule "the CLI runs
no repository code", and adds a contract for the runner. A runner does one `nop` trial and one
`oracle` trial in Harbor, and writes `<dataset>/proofs/<task-dir>.json`. `verify` passes the check
when the rules hold, fails it when a rule fails, and reports it unresolved when no proof exists or
the digest is different. We selected this over "no contract", because without a contract the
runner project must change the CLI later. It is different from the removed `--trial-results`
option: the CLI reads a small fixed schema, not the Harbor trial layout. The digest stops a stale
proof. It does not stop a false proof from a defective runner. We accept this.

**Reverses the earlier entry "A discrepancy in the existing survey stays out of scope": the review
survey stops blocking PRs for `no-test-change` (TP-7).** The user asked why the behavior is
contradictory. The blocker came in with commit `572a3c9`, and no PRD or tech plan asks for it. A
review datapoint does not need tests. Thus the remedy text ("harvestable for review") is correct,
and the code is incorrect. The fix is a separate commit (task A-0) before the coding work, because
it changes the pinned review survey. It must have its own entry in the pr-eval-harvest log.

**`registry.json` becomes a JSON list with one entry for each type and one combined entry (TP-8,
FR-34).** We read Harbor 0.22.0 and ran a probe. `Registry.from_path`
(`harbor/models/registry.py:102`) reads a list of entries. It refuses the single object that
`dataset.py:287` writes today, with a `ValidationError`. A list with two entries loads. Thus the
current file is an existing defect: `harbor run --registry-path` cannot use it. The existing test
(`test_dataset.py:138`) reads the object and never loads the file through Harbor, so it did not find
the defect. The fix is a separate commit (task A-6). The per-type entries also correct the mean
values: Harbor gives 0 to a missing key, so only a run on one type gives correct means. The report
prints one `harbor run` command for each type. We selected this over one entry with a correction in
the report, because the numbers in `harbor run` would stay wrong.

**The network limit of `harvest_env` stays as it is.** The user said: *"the network thing is fine
for now."* The customer runs the tasks in Harbor's Docker environment.

**All communication with the user is in ASD-STE100.** The user said: *"Can you talk to me in ASD
STE100. I can't follow you"*. This applies to chat replies, not only to the documents.

## 2026-11-18 — task A-2, the refusal log

**`capture` and `emit` now also write `refusals/review/<datapoint>.json` on a per-datapoint
refusal (TP-5, FR-33).** This is a contract change: the pr-eval-harvest log pins "the files each
verb writes" (`docs/features/pr-eval-harvest/decisions-tech-plan.md:346`). The new file is one JSON
object with the four refusal fields plus `verb`. `/` in the datapoint becomes `__` in the file name,
so `emit`'s `pr-<n>/<kind>` is `pr-<n>__<kind>.json`. A later success for the same datapoint removes
the file, so the directory holds the current reasons only. stdout, stderr, and exit codes do not
change. Refusals that are not about one datapoint (`unknown-pr`, `no-clean-pr`, `no-candidates`, the
risk-map refusals) are not logged. A forge failure in single-PR `capture` is exit 5, not a refusal,
so it is not logged either; in a batch the same failure is the item refusal `forge-unavailable`, and
it is logged. `dataset` reads `tasks/` only and `emit --all` reads `candidates/*.json` only, so
neither trips on the new directory.
