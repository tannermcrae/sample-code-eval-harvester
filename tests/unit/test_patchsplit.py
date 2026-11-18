"""Tests for `patchsplit` — the single source/test rule and the patch splitter built on it.

The survey, the coding capture, and the instruction scan all ask which changed path is a test. If
they disagree, a file is "source" in one and "test" in another, and the oracle patch ships test
files. These tests pin the rule and the cut directly, on hand-written diffs, so a failure names the
section rather than the caller.
"""

from __future__ import annotations

import pytest

from eval_harvest.patchsplit import TEST_MARKERS, PatchSplit

#: Three sections: source, test, source. Catches a section landing in both halves or in neither.
_SRC_A = """diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-a = 1
+a = 2
"""
_TEST_A = """diff --git a/tests/test_a.py b/tests/test_a.py
index 3333333..4444444 100644
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1,2 @@
 def test_a(): ...
+def test_b(): ...
"""
_SRC_B = """diff --git a/src/b.py b/src/b.py
new file mode 100644
index 0000000..5555555
--- /dev/null
+++ b/src/b.py
@@ -0,0 +1 @@
+b = 1
"""

#: Two conftest files — one nested under tests/, one at the root. Catches the §12 FR-5 defect: a
#: root `conftest.py` (no `/tests/` in its path) leaking into the oracle patch.
_CONFTESTS = """diff --git a/tests/conftest.py b/tests/conftest.py
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -1 +1 @@
-x
+y
diff --git a/conftest.py b/conftest.py
--- a/conftest.py
+++ b/conftest.py
@@ -1 +1 @@
-x
+y
"""

#: A source file renamed into the test tree. Catches classification by the old (`a/`) name.
_RENAME_INTO_TESTS = """diff --git a/src/x.py b/tests/test_x.py
similarity index 100%
rename from src/x.py
rename to tests/test_x.py
"""

#: A deleted test file. Catches a crash on `+++ /dev/null`, or a deleted test filed as source.
_DELETED_TEST = """diff --git a/tests/test_gone.py b/tests/test_gone.py
deleted file mode 100644
index 6666666..0000000
--- a/tests/test_gone.py
+++ /dev/null
@@ -1 +0,0 @@
-def test_gone(): ...
"""

#: Git quotes a non-ASCII path and keeps its octal escapes. Catches a crash or a wrong path on the
#: quoted header, and any rewriting of the section's bytes.
_QUOTED_SOURCE = """diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -1 +1 @@
-x
+y
"""
_QUOTED_TEST = """diff --git "a/tests/caf\\303\\251.py" "b/tests/caf\\303\\251.py"
--- "a/tests/caf\\303\\251.py"
+++ "b/tests/caf\\303\\251.py"
@@ -1 +1 @@
-x\r
+y\r
"""


def test_split_puts_each_section_in_one_part() -> None:
    """Source sections go to source, the test section to test, each byte-exact and none twice."""
    split = PatchSplit.split(_SRC_A + _TEST_A + _SRC_B)

    assert split.source_patch == _SRC_A + _SRC_B
    assert split.test_patch == _TEST_A


def test_split_keeps_section_order() -> None:
    """Interleaved sections keep their original order in each half, so each half matches `git diff`."""
    split = PatchSplit.split(_SRC_B + _TEST_A + _SRC_A + _DELETED_TEST)

    assert split.source_patch == _SRC_B + _SRC_A
    assert split.test_patch == _TEST_A + _DELETED_TEST


def test_split_classifies_conftest_as_test() -> None:
    """Both a nested and a root-level `conftest.py` are test content."""
    split = PatchSplit.split(_CONFTESTS)

    assert split.source_patch == ""
    assert split.test_patch == _CONFTESTS


def test_split_uses_b_path_for_rename() -> None:
    """A rename is classified by where the file ends up, not where it came from."""
    split = PatchSplit.split(_RENAME_INTO_TESTS)

    assert split.test_patch == _RENAME_INTO_TESTS
    assert split.source_patch == ""


def test_split_deleted_file() -> None:
    """A deletion is classified by its `diff --git` b-path, not by `+++ /dev/null`."""
    split = PatchSplit.split(_SRC_A + _DELETED_TEST)

    assert split.test_patch == _DELETED_TEST
    assert split.source_patch == _SRC_A


def test_split_quoted_header() -> None:
    """Quoted non-ASCII paths are classified without decoding, and their bytes (CRs included) survive."""
    split = PatchSplit.split(_QUOTED_SOURCE + _QUOTED_TEST)

    assert split.source_patch == _QUOTED_SOURCE
    assert split.test_patch == _QUOTED_TEST
    assert PatchSplit.section_path(_QUOTED_SOURCE) == "caf\\303\\251.py"


def test_split_rejects_preamble() -> None:
    """Text before the first `diff --git` is refused by name, not silently dropped."""
    with pytest.raises(ValueError, match="From 1234abcd"):
        PatchSplit.split("From 1234abcd Mon Sep 17 00:00:00 2001\n" + _SRC_A)
    with pytest.raises(ValueError, match="not a diff"):
        PatchSplit.split("not a diff\n")


def test_split_empty_diff() -> None:
    """A PR with no changed files gives two empty patches."""
    split = PatchSplit.split("")

    assert (split.source_patch, split.test_patch) == ("", "")


@pytest.mark.parametrize(
    ("marker", "path"),
    [
        ("/test/", "pkg/test/helpers.py"),
        ("/tests/", "tests/helpers.py"),
        ("/spec/", "spec/models.rb"),
        ("/__tests__/", "web/__tests__/App.jsx"),
        ("test_", "pkg/test_widget.py"),
        ("_test.", "pkg/widget_test.go"),
        (".test.", "web/widget.test.ts"),
        (".spec.", "web/widget.spec.ts"),
        ("Test.java", "src/main/WidgetTest.java"),
        ("conftest.py", "conftest.py"),
    ],
)
def test_is_test_path_matches_survey_rule(marker: str, path: str) -> None:
    """Each marker classifies one path as a test; the list is the ten markers, in their order."""
    assert marker in f"/{path}"
    assert PatchSplit.is_test_path(path)


def test_is_test_path_pins_marker_list_and_substring_rule() -> None:
    """The marker list is unchanged by the move, source is source, and substring matching is kept."""
    assert TEST_MARKERS == (
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
    )
    assert not PatchSplit.is_test_path("src/app.py")
    assert PatchSplit.is_test_path("src/latest_value.py")
