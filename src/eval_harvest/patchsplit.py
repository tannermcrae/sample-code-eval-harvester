"""Which changed path is a test — the single implementation, deliberately.

Three verbs ask the same question of a merged fix: the coding survey (does this PR change a test at
all), the coding capture (which part of the fix is the oracle ``source.patch`` and which is the hidden
``tests.patch``), and the instruction scan (does an instruction name a test file). If each answers it
with its own copy of the rule, a file can be "source" in the survey and "test" in the capture, and the
oracle patch then ships test files to the agent. This module is the one rule; the review survey reads
it too, so the review and coding sides cannot drift apart either.

Stdlib only, no project imports, so the offline modules can depend on it without pulling in the
online (``gh``/``git``) layer — the same rule ``diffspan.py`` follows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Path fragments that mark a changed file as test content. Deliberately broad: over-reporting a
#: test costs one glance, under-reporting hides the only PRs that can become a task with hidden tests.
TEST_MARKERS: Final = (
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

#: The line that opens one file's section of a ``git diff``. ``re.M`` anchors at ``\n`` only, so a
#: lone ``\r`` inside a hunk cannot start a section.
_SECTION_START: Final = re.compile(r"^diff --git ", re.M)

#: The ``b/`` side of a quoted header (``"b/caf\\303\\251.py"``) and of a plain one (``b/x.py``).
_QUOTED_NEW_SIDE: Final = ' "b/'
_PLAIN_NEW_SIDE: Final = " b/"


@dataclass(frozen=True, slots=True)
class SplitPatch:
    """One unified diff cut in two: the sections that touch source, and the sections that touch tests."""

    source_patch: str
    test_patch: str


class PatchSplit:
    """Classifies paths and diff sections as source or test. Holds no state."""

    @staticmethod
    def is_test_path(path: str) -> bool:
        """True when ``path`` matches a test marker.

        The ``/`` prefix lets a root-level ``tests/x.py`` match ``/tests/``. The markers match
        substrings, so ``src/latest_value.py`` is a test (``la`` + ``test_``); that is today's review
        survey behaviour, kept on purpose.
        """
        return any(marker in f"/{path}" for marker in TEST_MARKERS)

    @staticmethod
    def file_sections(unified_diff: str) -> list[str]:
        """Cut ``unified_diff`` into one string per ``diff --git`` section, byte-exact, in order.

        Each section runs from its ``diff --git`` line to the next one or the end of the text. Text
        before the first section is not part of any file, so it raises ``ValueError`` rather than
        being dropped.
        """
        if not unified_diff:
            return []
        starts = [match.start() for match in _SECTION_START.finditer(unified_diff)]
        if not starts or starts[0] != 0:
            first_line = unified_diff.split("\n", 1)[0]
            raise ValueError(f"diff text before the first 'diff --git' line: {first_line!r}")
        ends = [*starts[1:], len(unified_diff)]
        return [unified_diff[start:end] for start, end in zip(starts, ends, strict=True)]

    @staticmethod
    def section_path(section: str) -> str:
        """The ``b/`` path from a section's ``diff --git`` header, still git-quoted when git quoted it.

        Git writes the ``b/`` path for a deletion too, so ``+++ /dev/null`` never needs reading. A
        quoted path keeps its octal escapes: every marker is ASCII, so classification does not need
        them decoded.
        """
        header = section.split("\n", 1)[0].rstrip("\r")
        if header.endswith('"') and _QUOTED_NEW_SIDE in header:
            return header[header.rindex(_QUOTED_NEW_SIDE) + len(_QUOTED_NEW_SIDE) : -1]
        return header.rsplit(_PLAIN_NEW_SIDE, 1)[-1]

    @classmethod
    def split(cls, unified_diff: str) -> SplitPatch:
        """Put each file section of ``unified_diff`` into exactly one of the two patches.

        A section goes to ``test_patch`` when its ``b/`` path is a test path, else to
        ``source_patch``. Sections keep their bytes and their original order, so each half still
        applies with ``git apply``.
        """
        sections = cls.file_sections(unified_diff)
        test_sections = [section for section in sections if cls.is_test_path(cls.section_path(section))]
        source_sections = [section for section in sections if not cls.is_test_path(cls.section_path(section))]
        return SplitPatch(source_patch="".join(source_sections), test_patch="".join(test_sections))
