# eval-harvest-dataset(1)

## NAME

**eval-harvest dataset** — assemble the Harbor manifest and report the risk and severity
distribution.

## SYNOPSIS

```
eval-harvest dataset --dataset <dir> [--json]
```

## DESCRIPTION

Offline. Reads every `tasks/*` and its `task.toml`, writes `dataset.toml` (the Harbor manifest) and
a local `registry.json` (a JSON list of dataset entries, the shape `harbor run --registry-path` reads),
and prints the distribution counts. Refuses `no-datapoints` on an empty
dataset.

## METHODOLOGY

**The risk distribution is the point, not a summary.** You only switch **automated** approval on for
**low-risk** changes, so the dataset has to hold enough low-risk datapoints to measure the agent
there. `dataset` reports counts per change-risk level and per finding-severity level, so you can see
whether the low-risk slice is large enough to justify turning automation on.

## CODING DATAPOINTS

`dataset` counts review and coding datapoints apart; the risk and severity counts are over the
review datapoints only, because a coding task has no change risk. It reports how many coding
datapoints are unproven: `hidden-tests-proven` stays unresolved until a runner writes
`proofs/<task-dir>.json` (`verify --proof` reads another file), and a missing, stale, or failing
proof counts as unproven. It lists the current refusal reasons of each type. `registry.json` has one
entry for all tasks and one entry for each type. Harbor counts a missing reward key as 0, so a mixed
run dilutes every mean: use the per-type `harbor run` commands for means.

## EXIT STATUS

`3` (refusal) with `no-datapoints` on an empty dataset, `malformed-task` on a task.toml it cannot read
(an unknown `datapoint_type` included), and `malformed-refusal` on a broken refusal record.

## SEE ALSO

`eval-harvest(1)`, `eval-harvest-emit(1)`, `eval-harvest-coding(7)`.
