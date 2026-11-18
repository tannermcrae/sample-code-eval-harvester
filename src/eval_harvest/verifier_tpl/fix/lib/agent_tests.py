"""Tests that the coding agent wrote. Not gating: a senior engineer adds a regression test without being told.

The agent's test files are the test_*.py files under tests/ that the agent added or changed. They run two
times: on the agent's code, where they must pass, and on the base code with the same tests, where at least
one must fail (the tests reproduce the bug). Writes agent_tests.json. The working tree is the agent's
tree again at the end.

This stage needs pytest in the repository's environment. When `python -m pytest --version` fails there
(for example, in a repository that is not Python), the stage writes `"status": "skipped"`. It only
reports, so the reward does not change.

Usage: python3 agent_tests.py <base commit> <agent commit>

Ported from notebook 03 (``tests/lib/agent_tests.py``). This file is data that eval-harvest copies into
each coding task; the CLI never imports it.
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs pytest on the agent's own test files, argv-only
import sys
from xml.etree import ElementTree  # nosec B405 - parses only the JUnit files that this stage's own pytest runs wrote

from bedrock import LOGS, REPO, git, repository_environment, write_json

#: Scratch JUnit files of the two runs, in the verifier container's own temporary directory.
ON_FIX_JUNIT = "/tmp/agent_tests_fix.xml"  # nosec B108 - the verifier container's own scratch file
ON_BASE_JUNIT = "/tmp/agent_tests_base.xml"  # nosec B108 - the verifier container's own scratch file


def pytest_is_available() -> bool:
    """True when the repository's `python` can run pytest."""
    try:
        completed = subprocess.run(  # nosec B607 - the repository's python from PATH, on purpose; argv-only
            ["python", "-m", "pytest", "--version"],
            cwd=REPO,
            env=repository_environment(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def count_junit_outcomes(junit: str) -> dict[str, int]:
    """Count the passed and failed testcases of a JUnit file. A skipped testcase counts as neither."""
    passed = failed = 0
    try:
        # This stage's own test run wrote the JUnit file. The agent's code runs during that run and could rewrite it,
        # but the stdlib expat parser resolves no external entity (no XXE), and an entity bomb only stops this trial.
        # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse
        testcases = ElementTree.parse(junit).getroot().iter("testcase")  # nosec B314
        for testcase in testcases:
            if testcase.find("skipped") is not None:
                continue
            if testcase.find("failure") is None and testcase.find("error") is None:
                passed += 1
            else:
                failed += 1
    except (FileNotFoundError, ElementTree.ParseError):
        pass
    return {"passed": passed, "failed": failed}


def run(files: list[str], junit: str) -> dict[str, int]:
    """Run the agent's test files with pytest and count the outcomes."""
    subprocess.run(  # nosec B607 - the repository's python from PATH, on purpose; argv-only
        ["python", "-m", "pytest", *files, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=REPO,
        env=repository_environment(),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    return count_junit_outcomes(junit)


def put_base_source_back(base: str, agent: str) -> None:
    """Put every changed path outside tests/ back to the base commit, so the agent's tests run on the base code."""
    for status_line in git("diff", "--name-status", base, agent).splitlines():
        status, path = status_line.split("\t")[0], status_line.split("\t")[-1]
        if path.startswith("tests/"):
            continue
        if status.startswith("A"):
            (REPO / path).unlink(missing_ok=True)
        else:
            git("checkout", base, "--", path, check=False)


def main(base: str, agent: str) -> None:
    """Run the agent's tests on its fix and on the base, and write agent_tests.json."""
    if not pytest_is_available():
        write_json("agent_tests.json", {"status": "skipped", "reason": "pytest is not available"})
        return
    changed = git("diff", "--name-only", "--diff-filter=AM", base, agent, "--", "tests").split()
    files = [path for path in changed if path.split("/")[-1].startswith("test_") and path.endswith(".py")]
    if not files:
        write_json("agent_tests.json", {"files": [], "tests_added": 0, "pass_on_fix": 0, "reproduces_bug": 0})
        return
    on_fix = run(files, ON_FIX_JUNIT)
    put_base_source_back(base, agent)
    on_base = run(files, ON_BASE_JUNIT)
    git("checkout", agent, "--", ".", check=False)
    write_json(
        "agent_tests.json",
        {
            "files": files,
            "tests_added": on_fix["passed"] + on_fix["failed"],
            "on_fix": on_fix,
            "on_base": on_base,
            "pass_on_fix": int(on_fix["failed"] == 0 and on_fix["passed"] > 0),
            "reproduces_bug": int(on_fix["failed"] == 0 and on_base["failed"] > 0),
        },
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except Exception as error:  # noqa: BLE001 - a reported-only stage: record the error and go on
        write_json("agent_tests.json", {"status": "error", "error": repr(error)})
    print((LOGS / "agent_tests.json").read_text())
