"""The ported reward stack of a coding task (D-1): hidden tests, bloat, agent tests, and aggregate.

No test runs a container (§12). Each test points the stack's ``EVAL_HARVEST_*`` variables at temporary
directories, builds a real git repository with :class:`RepoBuilder`, and runs the stages in-process. The
test command is ``sys.executable -m pytest`` with JUnit output, so the stage parses what a real runner
wrote. The modules are data the CLI never imports, so they are loaded by path, as ``score.py`` is.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess  # nosec B404 - reads the fixture repository's git state after a stage
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.etree import ElementTree

import pytest
from fixtures.repo_builder import RepoBuilder

import eval_harvest
from eval_harvest.gitcmd import GIT_EXECUTABLE

FIX_TEMPLATE_DIR = Path(eval_harvest.__file__).parent / "verifier_tpl" / "fix"
LIB_DIR = FIX_TEMPLATE_DIR / "lib"
STAGE_MODULE_NAMES = ("bedrock", "hidden_tests", "aggregate", "judges", "agent_tests", "validate")

HIDDEN_TEST_PATH = "tests/test_calc_hidden.py"
HIDDEN_TEST_SOURCE = (
    "from src.calc import add, double\n\n\n"
    "def test_f2p_add():\n    assert add(2, 3) == 5\n\n\n"
    "def test_p2p_double():\n    assert double(3) == 6\n"
)
FAIL_TO_PASS_ID = f"{HIDDEN_TEST_PATH}::test_f2p_add"
PASS_TO_PASS_ID = f"{HIDDEN_TEST_PATH}::test_p2p_double"
FIXED_SOURCE = RepoBuilder.CODING_BASE_SOURCE.replace("return a - b", "return a + b")
PROTECTED_MARKERS = [
    "/test/",
    "/tests/",
    "/spec/",
    "/__tests__/",
    "test_",
    "_test.",
    ".test.",
    ".spec.",
    "Test.java",
    "conftest.py",
    "sitecustomize.py",
    "usercustomize.py",
    "pytest.ini",
]
PYTEST_TEST_COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml=$JUNIT_PATH"

#: A root conftest.py that turns every failing test into a pass: what an agent would add to game the run.
FORCE_PASS_CONFTEST = (
    "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
    "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n"
)


@dataclass(frozen=True)
class VerifierLayout:
    """The verifier's directories for one test, and the fixture repository's base and agent commits."""

    tests_dir: Path
    repo_dir: Path
    logs_dir: Path
    base_commit: str


def toml_string_list(values: list[str]) -> str:
    """A TOML array of basic strings (the values here have no characters that need escapes)."""
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def write_tests_toml(layout: VerifierLayout, *, test_command: str = PYTEST_TEST_COMMAND, extra_hidden: str = "") -> None:
    """Write tests/verify/tests.toml with the fixture's two hidden tests, in the schema D-1 owns."""
    hidden_tests = [
        (HIDDEN_TEST_PATH, "test_f2p_add", "fail_to_pass"),
        (HIDDEN_TEST_PATH, "test_p2p_double", "pass_to_pass"),
    ]
    tables = "".join(
        f'\n[[hidden_tests]]\nid = "{path}::{name}"\nrepo_path = "{path}"\nname = "{name}"\nkind = "{kind}"\n'
        for path, name, kind in hidden_tests
    )
    (layout.tests_dir / "verify" / "tests.toml").write_text(
        f"test_command = '{test_command}'\ntimeout_sec = 120\nprotected_markers = {toml_string_list(PROTECTED_MARKERS)}\n"
        + tables
        + extra_hidden,
        encoding="utf-8",
    )


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VerifierLayout:
    """A verifier layout with the fixture repository, the template models.toml, the hidden test, and tests.toml."""
    tests_dir, logs_dir = tmp_path / "tests", tmp_path / "logs"
    (tests_dir / "judge").mkdir(parents=True)
    (tests_dir / "verify" / "tests").mkdir(parents=True)
    logs_dir.mkdir()
    shutil.copyfile(FIX_TEMPLATE_DIR / "judge" / "models.toml", tests_dir / "judge" / "models.toml")
    (tests_dir / "verify" / HIDDEN_TEST_PATH).write_text(HIDDEN_TEST_SOURCE, encoding="utf-8")
    repo_dir, base_commit = RepoBuilder.build_coding_base_repository(tmp_path)
    for name, value in RepoBuilder.isolated_git_variables().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVAL_HARVEST_TESTS_DIR", str(tests_dir))
    monkeypatch.setenv("EVAL_HARVEST_REPO_DIR", str(repo_dir))
    monkeypatch.setenv("EVAL_HARVEST_BASE_DIR", str(tmp_path / "base"))
    monkeypatch.setenv("EVAL_HARVEST_VERIFIER_LOGS", str(logs_dir))
    monkeypatch.delenv("VERIFIER_MODEL_ID", raising=False)
    built = VerifierLayout(tests_dir=tests_dir, repo_dir=repo_dir, logs_dir=logs_dir, base_commit=base_commit)
    write_tests_toml(built)
    return built


@pytest.fixture
def load_stage(monkeypatch: pytest.MonkeyPatch, layout: VerifierLayout) -> Any:  # noqa: ANN401 - returns the loader below
    """Load a stage module fresh, after `layout` set the variables its module-level paths read."""
    monkeypatch.syspath_prepend(str(LIB_DIR))

    def load(module_name: str) -> ModuleType:
        for cached_name in STAGE_MODULE_NAMES:
            monkeypatch.delitem(sys.modules, cached_name, raising=False)
        return importlib.import_module(module_name)

    del layout
    return load


def commit_agent_change(layout: VerifierLayout, files: dict[str, str]) -> str:
    """Write the agent's files into the repository and commit them, as test.sh does. Returns the agent commit."""
    for relative_path, content in files.items():
        path = layout.repo_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return RepoBuilder.commit_working_tree(layout.repo_dir, "agent changes")


def run_hidden_tests(load_stage: Any, layout: VerifierLayout, agent_commit: str) -> dict[str, Any]:  # noqa: ANN401
    """Run the hidden-tests stage and return verifiers.json."""
    load_stage("hidden_tests").main(layout.base_commit, agent_commit)
    loaded: dict[str, Any] = json.loads((layout.logs_dir / "verifiers.json").read_text())
    return loaded


def repository_git(layout: VerifierLayout, *arguments: str) -> str:
    """Run git in the fixture repository and return its output."""
    return subprocess.run(  # noqa: S603 - literal argv[0], resolved binary via executable=  # nosec B603,B607
        ["git", "-C", str(layout.repo_dir), *arguments], executable=GIT_EXECUTABLE, capture_output=True, text=True, check=True
    ).stdout


def write_passing_llm_stages(layout: VerifierLayout) -> None:
    """Write validation, rubric, taste, bloat, and agent-test files that pass every gate."""
    stages = {
        "validation.json": {"status": "accepted", "validation_score": 1.0, "passed_stories": 2, "total_stories": 2},
        "rubric.json": {"rubric_score": 1.0},
        "taste.json": {"status": "scored", "relative_taste": 4.0, "practice_alignment": 4.0},
        "bloat.json": {"patch_bloat": 1.0},
        "agent_tests.json": {"tests_added": 1, "reproduces_bug": 1},
    }
    for name, record in stages.items():
        (layout.logs_dir / name).write_text(json.dumps(record))


def write_verifiers(layout: VerifierLayout, fail_to_pass: str, pass_to_pass: str, *, status: str = "completed") -> None:
    """Write a verifiers.json with the given results for the two hidden tests."""
    record = {
        "status": status,
        "tests": {FAIL_TO_PASS_ID: fail_to_pass, PASS_TO_PASS_ID: pass_to_pass},
        "existing_suite": "passed",
        "unmatched_hidden": [],
    }
    (layout.logs_dir / "verifiers.json").write_text(json.dumps(record))


# ───────────────────────────── stage 2: the hidden tests ─────────────────────────────


def test_hidden_tests_reports_each_id(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """The no-op agent: the fail-to-pass test fails and the pass-to-pass test passes, each under its own id."""
    agent_commit = commit_agent_change(layout, {})
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["status"] == "completed"
    assert verifiers["tests"] == {FAIL_TO_PASS_ID: "failed", PASS_TO_PASS_ID: "passed"}
    assert verifiers["existing_suite"] == "passed"


def test_hidden_tests_pass_on_the_fix(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """The oracle-like agent: with `add` fixed, every hidden test and the existing suite pass."""
    agent_commit = commit_agent_change(layout, {"src/calc.py": FIXED_SOURCE})
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["tests"] == {FAIL_TO_PASS_ID: "passed", PASS_TO_PASS_ID: "passed"}
    assert verifiers["existing_suite"] == "passed"


def test_hidden_tests_restores_base_test_files(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """The agent breaks `double` and rewrites the base test to `assert True`; the run uses the base test, which fails."""
    agent_commit = commit_agent_change(
        layout,
        {
            "src/calc.py": FIXED_SOURCE.replace("return a * 2", "return a * 3"),
            "tests/test_calc.py": "def test_double():\n    assert True\n",
        },
    )
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["existing_suite"] == "failed"


def test_hidden_tests_deletes_agent_conftest(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """A root conftest.py the agent added (one that turns every failure into a pass) is deleted before the run."""
    agent_commit = commit_agent_change(layout, {"conftest.py": FORCE_PASS_CONFTEST})
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["tests"][FAIL_TO_PASS_ID] == "failed"


def test_hidden_test_not_run_is_missing(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """A hidden id with no matching testcase is `missing`, is listed as unmatched, and fails the gate."""
    gone_id = f"{HIDDEN_TEST_PATH}::test_f2p_gone"
    write_tests_toml(
        layout,
        extra_hidden=(
            f'\n[[hidden_tests]]\nid = "{gone_id}"\nrepo_path = "{HIDDEN_TEST_PATH}"\n'
            'name = "test_f2p_gone"\nkind = "fail_to_pass"\n'
        ),
    )
    agent_commit = commit_agent_change(layout, {"src/calc.py": FIXED_SOURCE})
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["tests"][gone_id] == "missing"
    assert verifiers["unmatched_hidden"] == [gone_id]
    aggregate = load_stage("aggregate")
    hidden_tests = aggregate.tomllib.loads((layout.tests_dir / "verify" / "tests.toml").read_text())["hidden_tests"]
    assert aggregate.summarize_verifiers(verifiers, hidden_tests)["pass"] is False


def test_no_junit_is_not_run(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """A test command that writes no JUnit file gives `not-run`, and aggregate writes no reward.json."""
    write_tests_toml(layout, test_command="true")
    agent_commit = commit_agent_change(layout, {"src/calc.py": FIXED_SOURCE})
    verifiers = run_hidden_tests(load_stage, layout, agent_commit)
    assert verifiers["status"] == "not-run"
    write_passing_llm_stages(layout)
    load_stage("aggregate").main()
    assert not (layout.logs_dir / "reward.json").exists()
    assert json.loads((layout.logs_dir / "reward_details.json").read_text())["invalid"]


def test_agent_tree_restored_after_hidden_tests(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """After the stage, the tree is the agent's commit exactly: no base tests, no hidden tests, no leftovers."""
    agent_commit = commit_agent_change(
        layout, {"src/calc.py": FIXED_SOURCE, "tests/test_calc.py": "def test_double():\n    assert True\n", "conftest.py": "\n"}
    )
    run_hidden_tests(load_stage, layout, agent_commit)
    assert repository_git(layout, "status", "--porcelain", "--untracked-files=all") == ""
    assert repository_git(layout, "rev-parse", "HEAD").strip() == agent_commit


def test_hidden_tests_refuses_a_path_outside_the_repository(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """A hidden repo_path that climbs out of the repository is refused, not copied."""
    hidden_tests = load_stage("hidden_tests")
    with pytest.raises(ValueError, match="not a relative path inside the repository"):
        hidden_tests.checked_repo_path("../outside.py")


def junit_testcase(**attributes: str) -> ElementTree.Element:
    """A JUnit <testcase> element with the given attributes."""
    return ElementTree.Element("testcase", attributes)


@pytest.mark.parametrize(
    ("attributes", "hidden", "expected_match"),
    [
        pytest.param(
            {"name": "test_f2p_add", "classname": "tests.test_x", "file": "tests/test_x.py"},
            {"name": "test_f2p_add", "repo_path": "tests/test_x.py"},
            True,
            id="pytest-xunit1-with-file",
        ),
        pytest.param(
            {"name": "test_f2p_add", "classname": "tests.test_x", "file": "tests/test_other.py"},
            {"name": "test_f2p_add", "repo_path": "tests/test_x.py"},
            False,
            id="pytest-xunit1-other-file",
        ),
        pytest.param(
            {"name": "test_f2p_add", "classname": "tests.test_x"},
            {"name": "test_f2p_add", "repo_path": "tests/test_x.py"},
            True,
            id="pytest-without-file",
        ),
        pytest.param(
            {"name": "test_a[1]", "classname": "tests.test_x", "file": "tests/test_x.py"},
            {"name": "test_a", "repo_path": "tests/test_x.py"},
            True,
            id="pytest-parametrized",
        ),
        pytest.param(
            {"name": "test_ab", "classname": "tests.test_x", "file": "tests/test_x.py"},
            {"name": "test_a", "repo_path": "tests/test_x.py"},
            False,
            id="name-prefix-is-not-a-match",
        ),
        pytest.param(
            {"name": "addsNumbers", "classname": "com.example.CalcTest"},
            {"name": "addsNumbers", "repo_path": "src/test/java/com/example/CalcTest.java"},
            True,
            id="java-classname",
        ),
        pytest.param(
            {"name": "addsNumbers", "classname": "com.example.OtherCalcTest"},
            {"name": "addsNumbers", "repo_path": "src/test/java/com/example/CalcTest.java"},
            False,
            id="java-classname-is-matched-at-a-path-boundary",
        ),
    ],
)
def test_junit_id_matching(
    load_stage: Any,  # noqa: ANN401
    attributes: dict[str, str],
    hidden: dict[str, str],
    expected_match: bool,
) -> None:
    """Each JUnit dialect maps to the hidden test it ran, and only to that one."""
    hidden_tests = load_stage("hidden_tests")
    assert hidden_tests.testcase_matches_hidden_test(junit_testcase(**attributes), hidden) is expected_match


# ───────────────────────────── stage 5: aggregate ─────────────────────────────


def test_aggregate_writes_only_fix_keys(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """With every stage present and passing, reward.json has exactly FIX_REWARD_KEYS, in order, and a full reward."""
    write_verifiers(layout, "passed", "passed")
    write_passing_llm_stages(layout)
    aggregate = load_stage("aggregate")
    aggregate.main()
    reward = json.loads((layout.logs_dir / "reward.json").read_text())
    assert list(reward) == list(aggregate.FIX_REWARD_KEYS)
    assert len(aggregate.FIX_REWARD_KEYS) == 10
    assert all(isinstance(value, float) for value in reward.values())
    assert reward["fix_reward"] == 1.0
    assert reward["fix_correct"] == 1.0


def test_aggregate_invalid_when_llm_stage_errors(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """A rubric judge error (Bedrock throttling) makes the trial invalid: no reward.json, a reason in the details."""
    write_verifiers(layout, "passed", "passed")
    write_passing_llm_stages(layout)
    (layout.logs_dir / "rubric.json").write_text(json.dumps({"status": "error", "error": "ThrottlingException"}))
    load_stage("aggregate").main()
    assert not (layout.logs_dir / "reward.json").exists()
    assert (layout.logs_dir / "reward.txt").read_text() == ""
    details = json.loads((layout.logs_dir / "reward_details.json").read_text())
    assert any("rubric judge error" in reason for reason in details["invalid"])


def test_aggregate_nop_scores_zero(load_stage: Any, layout: VerifierLayout) -> None:  # noqa: ANN401
    """Every fail-to-pass test failed: the no-op agent gets no reward and is not correct."""
    write_verifiers(layout, "failed", "passed")
    write_passing_llm_stages(layout)
    load_stage("aggregate").main()
    reward = json.loads((layout.logs_dir / "reward.json").read_text())
    assert reward["fix_reward"] == 0.0
    assert reward["fix_correct"] == 0.0
    assert reward["fix_fail_to_pass"] == 0.0


def test_test_sh_zero_reward_has_every_fix_key(load_stage: Any) -> None:  # noqa: ANN401
    """The reward test.sh writes when the agent deleted its tree has exactly FIX_REWARD_KEYS, each 0."""
    zero_lines = [line for line in (FIX_TEMPLATE_DIR / "test.sh").read_text().splitlines() if '"fix_reward": 0.0' in line]
    assert len(zero_lines) == 1
    zero_reward = json.loads(zero_lines[0].split("'")[3])
    assert list(zero_reward) == list(load_stage("aggregate").FIX_REWARD_KEYS)
    assert set(zero_reward.values()) == {0.0}


# ───────────────────────────── stage 4: patch bloat ─────────────────────────────


def test_bloat_uses_protected_markers(load_stage: Any) -> None:  # noqa: ANN401
    """Only source lines count: the tests/conftest.py lines are excluded by the same markers as the A-2 split."""
    patch = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-x = 1\n+x = 2\n+y = 3\n"
        "diff --git a/tests/conftest.py b/tests/conftest.py\n--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1 +1,2 @@\n-import os\n+import sys\n+import json\n"
    )
    judges = load_stage("judges")
    assert judges.source_sloc(patch, judges.protected_markers()) == 3


def test_bloat_skips_documentation_and_changelog(load_stage: Any) -> None:  # noqa: ANN401
    """Documentation and changelog lines do not count as source."""
    judges = load_stage("judges")
    markers = judges.protected_markers()
    assert not judges.is_counted_source_path("README.md", markers)
    assert not judges.is_counted_source_path("docs/guide.rst", markers)
    assert not judges.is_counted_source_path("CHANGELOG", markers)
    assert judges.is_counted_source_path("src/a.py", markers)


# ───────────────────────────── stage 1: the agent's own tests ─────────────────────────────


def test_agent_tests_skips_without_pytest(
    load_stage: Any, layout: VerifierLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN401
    """With a `python` that cannot run pytest (a repository that is not Python), the stage is skipped, not crashed."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nexit 1\n")
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    agent_commit = commit_agent_change(layout, {"tests/test_new.py": "def test_new():\n    assert True\n"})
    load_stage("agent_tests").main(layout.base_commit, agent_commit)
    record = json.loads((layout.logs_dir / "agent_tests.json").read_text())
    assert record == {"status": "skipped", "reason": "pytest is not available"}
