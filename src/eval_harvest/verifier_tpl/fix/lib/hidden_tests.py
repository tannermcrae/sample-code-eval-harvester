"""Stage 2 of the reward stack: the pre-written verifiers. Usage: python3 hidden_tests.py <base commit> <agent commit>

The hidden tests and the existing test suite run on the agent's code, with the base test files put back:

1. Restore every base path that matches a protected marker, so that the agent cannot change a test
   that passes on the base (or a conftest.py, sitecustomize.py, or pytest.ini that steers the run).
2. Delete every protected path that the agent added, for the same reason.
3. Copy the hidden tests from tests/verify/ into the repository.
4. Run the task's test command. It writes JUnit XML to $JUNIT_PATH.
5. Map each JUnit testcase to a hidden test id, and write verifiers.json (one value for each id).
6. Put the agent's tree back, so the later stages read the agent's change only.

tests/verify/tests.toml gives the test command, the protected markers, and the hidden tests. The test
command can be any runner that writes JUnit XML; this stage does not depend on pytest.

This file is data that eval-harvest copies into each coding task; the CLI never imports it.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the task's own test command in the verifier container
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any  # tests.toml and verifiers.json are arbitrary TOML/JSON until read field by field.
from xml.etree import ElementTree  # nosec B405 - parses only the JUnit file that this stage's own test run wrote

from bedrock import LOGS, REPO, TESTS, git, repository_environment, write_json

TESTS_TOML = TESTS / "verify" / "tests.toml"
DEFAULT_TIMEOUT_SEC = 900
JUNIT_FILE_NAME = "junit.xml"
TEST_COMMAND_LOG_NAME = "test_command.log"
VERIFIERS_FILE_NAME = "verifiers.json"

PASSED, FAILED, MISSING = "passed", "failed", "missing"


def load_test_specification() -> dict[str, Any]:
    """Read tests/verify/tests.toml."""
    return tomllib.loads(TESTS_TOML.read_text())


def is_protected_path(path: str, protected_markers: Sequence[str]) -> bool:
    """True when `path` matches a protected marker: the substring rule of PatchSplit.is_test_path (A-2).

    The `/` prefix lets a root-level `tests/x.py` match `/tests/`.
    """
    return any(marker in f"/{path}" for marker in protected_markers)


def tracked_paths(commit: str) -> set[str]:
    """Every path tracked in `commit`."""
    return set(git("ls-tree", "-r", "-z", "--name-only", commit).split("\0")) - {""}


def restore_base_protected_paths(base: str, protected_markers: Sequence[str]) -> None:
    """Check out every protected path of the base commit, over whatever the agent did to it."""
    base_protected = sorted(path for path in tracked_paths(base) if is_protected_path(path, protected_markers))
    if base_protected:
        git("--literal-pathspecs", "checkout", base, "--", *base_protected)


def delete_agent_added_protected_paths(base: str, agent: str, protected_markers: Sequence[str]) -> None:
    """Delete every protected path that the agent's commit has and the base commit does not."""
    for path in sorted(tracked_paths(agent) - tracked_paths(base)):
        if is_protected_path(path, protected_markers):
            (REPO / path).unlink(missing_ok=True)


def checked_repo_path(repo_path: str) -> PurePosixPath:
    """A hidden test's repository path, refused when it is absolute or climbs out of the repository."""
    path = PurePosixPath(repo_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"hidden test path {repo_path!r} is not a relative path inside the repository")
    return path


def copy_hidden_test_files(hidden_tests: Iterable[Mapping[str, str]]) -> None:
    """Copy each hidden test file from tests/verify/<repo_path> to <repo>/<repo_path>."""
    for repo_path in sorted({hidden["repo_path"] for hidden in hidden_tests}):
        relative = checked_repo_path(repo_path)
        destination = REPO / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TESTS / "verify" / relative, destination)


def run_test_command(test_command: str, timeout_sec: int, junit_path: Path) -> None:
    """Run the task's test command with bash in the repository. Its output goes to test_command.log."""
    junit_path.unlink(missing_ok=True)
    environment = {**repository_environment(), "JUNIT_PATH": str(junit_path)}
    log_path = LOGS / TEST_COMMAND_LOG_NAME
    try:
        # The task's own test command (tests.toml, written by eval-harvest from the candidate), run in
        # the verifier container on purpose: running it is what this stage is for.
        completed = subprocess.run(  # nosec B607 - bash from the verifier image's PATH; the command is the task's own
            ["bash", "-c", test_command],
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        log_path.write_text(f"exit {completed.returncode}\n{completed.stdout}{completed.stderr}")
    except subprocess.TimeoutExpired:
        log_path.write_text(f"the test command did not finish in {timeout_sec} seconds\n")


def junit_testcases(junit_path: Path) -> list[ElementTree.Element] | None:
    """The testcases of the JUnit file, or None when the file is missing or is not XML."""
    try:
        # This stage's own test run wrote the JUnit file. The agent's code runs during that run and could rewrite it,
        # but the stdlib expat parser resolves no external entity (no XXE), and an entity bomb only stops this trial.
        # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse
        root = ElementTree.parse(junit_path).getroot()  # nosec B314
    except (FileNotFoundError, ElementTree.ParseError):
        return None
    return list(root.iter("testcase"))


def testcase_name_matches(testcase_name: str, hidden_name: str) -> bool:
    """The testcase is the hidden test, or one case of it when the runner parametrized it (`name[...]`)."""
    return testcase_name == hidden_name or testcase_name.startswith(hidden_name + "[")


def testcase_location_matches(testcase: ElementTree.Element, repo_path: str) -> bool:
    """The testcase comes from the hidden test's file.

    With a `file` attribute (pytest writes it with junit_family=xunit1), it must equal the repository
    path. Without one, the classname with `.` changed to `/` and the repository path without its
    extension must agree at a path boundary: one ends with `/` plus the other, or they are equal. Thus
    pytest's `tests.test_x` matches `tests/test_x.py`, and a Java classname `com.example.FooTest`
    matches `src/test/java/com/example/FooTest.java`.
    """
    file_attribute = testcase.get("file")
    if file_attribute is not None:
        return file_attribute == repo_path
    classname_path = testcase.get("classname", "").replace(".", "/")
    path_without_extension = str(PurePosixPath(repo_path).with_suffix(""))
    if not classname_path:
        return False
    return (
        classname_path == path_without_extension
        or classname_path.endswith("/" + path_without_extension)
        or path_without_extension.endswith("/" + classname_path)
    )


def testcase_matches_hidden_test(testcase: ElementTree.Element, hidden: Mapping[str, str]) -> bool:
    """The JUnit id rule (D-1, Notes & Gotchas): the name and the location both match."""
    return testcase_name_matches(testcase.get("name", ""), hidden["name"]) and testcase_location_matches(
        testcase, hidden["repo_path"]
    )


def testcase_passed(testcase: ElementTree.Element) -> bool:
    """A testcase passed when it has no failure, no error, and was not skipped."""
    return all(testcase.find(outcome) is None for outcome in ("failure", "error", "skipped"))


def testcase_failed(testcase: ElementTree.Element) -> bool:
    """A testcase failed when it has a failure or an error. A skipped testcase did not fail."""
    return testcase.find("failure") is not None or testcase.find("error") is not None


def hidden_test_result(hidden: Mapping[str, str], testcases: Sequence[ElementTree.Element]) -> str:
    """`passed` when every matching testcase passed, `missing` when none matched, else `failed`."""
    matching = [testcase for testcase in testcases if testcase_matches_hidden_test(testcase, hidden)]
    if not matching:
        return MISSING
    return PASSED if all(testcase_passed(testcase) for testcase in matching) else FAILED


def existing_suite_result(hidden_tests: Sequence[Mapping[str, str]], testcases: Sequence[ElementTree.Element]) -> str:
    """`failed` when any testcase that is not a hidden test failed or had an error, else `passed`."""
    for testcase in testcases:
        is_hidden = any(testcase_matches_hidden_test(testcase, hidden) for hidden in hidden_tests)
        if not is_hidden and testcase_failed(testcase):
            return FAILED
    return PASSED


def verifiers_record(
    hidden_tests: Sequence[Mapping[str, str]], testcases: Sequence[ElementTree.Element] | None
) -> dict[str, Any]:
    """The verifiers.json record: one value for each hidden id, and the result of the existing suite."""
    if testcases is None:
        return {
            "status": "not-run",
            "tests": dict.fromkeys(sorted(hidden["id"] for hidden in hidden_tests), MISSING),
            "existing_suite": MISSING,
            "unmatched_hidden": sorted(hidden["id"] for hidden in hidden_tests),
        }
    results = {hidden["id"]: hidden_test_result(hidden, testcases) for hidden in hidden_tests}
    return {
        "status": "completed",
        "tests": dict(sorted(results.items())),
        "existing_suite": existing_suite_result(hidden_tests, testcases),
        "unmatched_hidden": sorted(test_id for test_id, result in results.items() if result == MISSING),
    }


def restore_agent_tree(agent: str) -> None:
    """Put the agent's committed tree back, and remove the hidden tests and every other untracked file."""
    git("reset", "-q", "--hard", agent, check=False)
    git("clean", "-fdq", check=False)


def main(base: str, agent: str) -> None:
    """Run the stage and write verifiers.json."""
    specification = load_test_specification()
    hidden_tests: list[Mapping[str, str]] = specification["hidden_tests"]
    protected_markers: list[str] = specification["protected_markers"]
    junit_path = LOGS / JUNIT_FILE_NAME
    try:
        restore_base_protected_paths(base, protected_markers)
        delete_agent_added_protected_paths(base, agent, protected_markers)
        copy_hidden_test_files(hidden_tests)
        run_test_command(specification["test_command"], int(specification.get("timeout_sec", DEFAULT_TIMEOUT_SEC)), junit_path)
    finally:
        restore_agent_tree(agent)
    record = verifiers_record(hidden_tests, junit_testcases(junit_path))
    (LOGS / VERIFIERS_FILE_NAME).write_text(json.dumps(record, indent=2, sort_keys=True))
    passed = sum(result == PASSED for result in record["tests"].values())
    print(f"hidden tests: {record['status']}, {passed}/{len(record['tests'])} passed, existing suite {record['existing_suite']}")


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except Exception as error:  # noqa: BLE001 - an infrastructure failure: the trial is invalid, not a fail for the agent
        write_json(VERIFIERS_FILE_NAME, {"status": "not-run", "error": repr(error)})
        print(f"hidden tests error: {error!r}")
