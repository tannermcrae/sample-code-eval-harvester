#!/usr/bin/env bash
#
# ralph-tasks.sh — burn down a checked-in task board, one task per headless Claude Code run.
#
# Each iteration:
#   1. Checks that the checkout is on `main` with a clean tree (the skill claims tasks on `main`).
#   2. Picks the next ready task from docs/features/<feature>/tasks/*.md: `status: todo`, every
#      `blocked_by` entry `done`. Ties go to the task that unblocks the most work, then the smaller
#      complexity, then the lowest ID — the same rule as the execute-local-task-harvest skill.
#   3. Runs `claude -p "/execute-local-task-harvest <id>"` and saves the output to a log.
#   4. Reads the task file again. If its status is not `done`, the loop stops. A failed task never
#      lets the loop go on to the next one.
#
# The loop stops when no task is ready, when a task does not finish, or after MAX_ITERATIONS.
#
# Usage:
#   scripts/ralph-tasks.sh <feature> [--dry-run]
#
#   --dry-run   Print the next ready task and exit. Does not call claude.
#
# Environment:
#   MAX_ITERATIONS   Maximum tasks in one run (default 12).
#   PERMISSIONS      `allowlist` (default): edits are accepted, and Bash is limited to the tools
#                    the skill uses. A command outside the list is denied, the task fails, and the
#                    loop stops. `bypass`: --dangerously-skip-permissions. Use it only when you
#                    accept that an unattended agent runs any command, including `git push`.
#   CLAUDE_MODEL     Optional --model value.
#
# Logs: work/ralph/<feature>/<timestamp>-<task-id>.log (work/ is gitignored).
#
# The skill pushes `main` to origin after each task. Run this only where that is what you want.

set -euo pipefail

usage() {
  echo "usage: $0 <feature> [--dry-run]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
FEATURE="$1"
DRY_RUN="${2:-}"
[[ -z "$DRY_RUN" || "$DRY_RUN" == "--dry-run" ]] || usage

MAX_ITERATIONS="${MAX_ITERATIONS:-12}"
PERMISSIONS="${PERMISSIONS:-allowlist}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
TASK_DIR="docs/features/$FEATURE/tasks"
LOG_DIR="work/ralph/$FEATURE"

[[ -d "$TASK_DIR" ]] || { echo "error: no task board at $TASK_DIR" >&2; exit 2; }

# Print "<id>" of the next ready task, or nothing. Also prints a one-line board summary to stderr.
next_ready_task() {
  uv run --no-project python -I - "$TASK_DIR" <<'PY'
import re
import sys
from pathlib import Path

task_dir = Path(sys.argv[1])
tasks: dict[str, dict[str, object]] = {}
for path in sorted(task_dir.glob("[A-Z]-*.md")):
    text = path.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]

    def field(name: str) -> str:
        match = re.search(rf"^{name}:\s*(.*)$", front, re.M)
        return match.group(1).strip() if match else ""

    def id_list(name: str) -> list[str]:
        return [item.strip() for item in field(name).strip("[]").split(",") if item.strip()]

    tasks[field("id")] = {
        "status": field("status"),
        "complexity": field("complexity"),
        "blocked_by": id_list("blocked_by"),
        "blocks": id_list("blocks"),
    }

def downstream(task_id: str, seen: set[str]) -> set[str]:
    for child in tasks[task_id]["blocks"]:  # type: ignore[union-attr]
        if child in tasks and child not in seen:
            seen.add(child)
            downstream(child, seen)
    return seen

counts = {state: sum(1 for t in tasks.values() if t["status"] == state) for state in ("todo", "in-progress", "done")}
print(f"board: {counts['done']} done, {counts['in-progress']} in-progress, {counts['todo']} todo", file=sys.stderr)

in_progress = [task_id for task_id, t in tasks.items() if t["status"] == "in-progress"]
if in_progress:
    print(f"in-progress (claimed, not finished): {', '.join(in_progress)}", file=sys.stderr)

ready = [
    task_id
    for task_id, t in tasks.items()
    if t["status"] == "todo" and all(tasks.get(b, {}).get("status") == "done" for b in t["blocked_by"])  # type: ignore[union-attr]
]
size = {"S": 0, "M": 1, "L": 2}
ready.sort(key=lambda task_id: (-len(downstream(task_id, set())), size.get(str(tasks[task_id]["complexity"]), 3), task_id))
if ready:
    print(ready[0])
PY
}

task_status() {
  local file
  file="$(ls "$TASK_DIR"/"$1"-*.md)"
  sed -n 's/^status:[[:space:]]*//p' "$file" | head -n 1
}

claude_args=(-p --output-format text)
[[ -n "${CLAUDE_MODEL:-}" ]] && claude_args+=(--model "$CLAUDE_MODEL")
case "$PERMISSIONS" in
  allowlist)
    # The skill makes its worktree in the parent directory (../<slug>), so that directory must be
    # reachable for edits.
    claude_args+=(
      --permission-mode acceptEdits
      --add-dir "$(dirname "$REPO_ROOT")"
      --allowedTools
      "Read" "Edit" "Write" "Glob" "Grep" "Skill"
      "Bash(git:*)" "Bash(mise:*)" "Bash(uv:*)"
      "Bash(ls:*)" "Bash(cat:*)" "Bash(head:*)" "Bash(tail:*)" "Bash(sed:*)" "Bash(grep:*)"
      "Bash(find:*)" "Bash(wc:*)" "Bash(diff:*)" "Bash(mkdir:*)" "Bash(cd:*)" "Bash(pwd)"
      "Bash(date:*)" "Bash(eval-harvest:*)"
    )
    ;;
  bypass)
    claude_args+=(--dangerously-skip-permissions)
    ;;
  *)
    echo "error: PERMISSIONS must be 'allowlist' or 'bypass', not '$PERMISSIONS'" >&2
    exit 2
    ;;
esac

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  next="$(next_ready_task)"
  echo "next ready task: ${next:-none}"
  exit 0
fi

mkdir -p "$LOG_DIR"

for ((iteration = 1; iteration <= MAX_ITERATIONS; iteration++)); do
  branch="$(git branch --show-current)"
  if [[ "$branch" != "main" ]]; then
    echo "stop: the checkout is on '$branch'. The skill claims tasks on main. Run 'git switch main'." >&2
    exit 1
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "stop: the working tree is not clean. Commit or stash the changes first." >&2
    git status --short >&2
    exit 1
  fi

  task_id="$(next_ready_task)"
  if [[ -z "$task_id" ]]; then
    echo "stop: no task is ready. The board is done, or the remaining tasks are blocked or in-progress."
    exit 0
  fi

  log="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$task_id.log"
  echo "=== [$iteration/$MAX_ITERATIONS] $task_id → $log"

  prompt="/execute-local-task-harvest $task_id

You run headless in a loop (claude -p). No person can answer a question during this run.
Do not wait for confirmation: the loop already selected $task_id, and you have approval to claim it,
implement it, run the gate, merge it, and push it, as the skill says.
If the skill tells you to stop and ask the user, follow the skill's failure path instead: revert the
claim on main, write the reason in the task's Completion Log, remove the worktree, and end the run."

  set +e
  claude "${claude_args[@]}" "$prompt" 2>&1 | tee "$log"
  exit_code="${PIPESTATUS[0]}"
  set -e

  status="$(task_status "$task_id")"
  if [[ "$status" != "done" ]]; then
    echo "stop: $task_id has status '$status' after the run (claude exit $exit_code). See $log." >&2
    exit 1
  fi
  echo "=== $task_id done"
done

echo "stop: reached MAX_ITERATIONS=$MAX_ITERATIONS."
