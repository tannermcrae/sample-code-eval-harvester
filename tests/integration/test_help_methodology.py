"""FR-1: the CLI's help and man pages carry the *methodology*, not just flags (task F-2).

The documentation is the product: an unsupervised coding agent learns the whole workflow from
``--help`` and the man pages alone (PRD US-1, tech plan §5.1). These tests pin the specific
methodology sentences a reviewer must be able to point to — so a future edit that trims the help
back to a bare flag list fails here, catching the kill-criterion regression (PRD §9) directly.
"""

from __future__ import annotations

import re
from pathlib import Path

from eval_harvest.candidate import CommentDict, FindingDict
from eval_harvest.cli import Cli

#: Phrases each verb's ``--help`` must teach — the "reviewer can point to the sentence" contract
#: (FR-1). ``None`` is the top-level ``eval-harvest --help``. Matching is case-insensitive so the
#: contract pins the *idea*, not the exact casing of the surrounding sentence.
METHODOLOGY_PHRASES: dict[str | None, tuple[str, ...]] = {
    None: ("review datapoint", "local clone", "rubric", "risk map"),
    "init": ("git clone", "local clone", "rubric", "overlay"),
    # survey fetches the pull-head namespace a plain clone lacks, offers --no-fetch, and refuses
    # no-harvestable-pr (with a blocker histogram) instead of exiting 0 on an empty table.
    "survey": ("review iteration", "harvestable", "refs/pull/*/head", "--no-fetch", "no-harvestable-pr"),
    "capture": (
        "base commit must precede the change",
        "defect",
        "nit",
        "mechanical facts",
        # Three rules a reader would otherwise have to dig out of `candidate.py`: the `bot`
        # classification, and the severity_evidence / reference_only refusals — cheaper to teach here
        # than to hit as a validation error.
        "bot",
        "severity_evidence",
        "reference_only",
        # capture refuses a PR whose reviewed states were force-pushed away, and for one that
        # survives says which kinds remain buildable.
        "which kinds remain buildable",
        # batch mode teaches --from-survey (payload order), --limit/--pr, and the per-item
        # containment rule with its partial-batch exit code.
        "--from-survey",
        "payload order",
        "--limit",
        "--pr",
        "partial batch exits 3",
    ),
    # `brief` teaches the per-PR, fresh-context hand-off; that it prints no diff bytes (that is
    # `show`'s job); and that it suggests no classification (FR-9) — keeping a single large context
    # from ballooning token cost.
    "brief": ("self-contained brief", "no diff bytes", "suggests no classification"),
    # `show` teaches classifying from the lines a comment points at, and that whole-patch reads
    # are never needed — where most token cost otherwise goes.
    "show": ("the lines it points at", "reading whole patch files is never necessary"),
    # `annotate` teaches append-only fill, that overwrites are reported, that no judgment is
    # ever inferred, and the `--check` loop — the workflow that replaces whole-file rewrites.
    "annotate": ("append-only", "overwrite", "never infers a judgment", "reference_only", "--check"),
    # The iteration-selection sentence: the reject default and the `--iteration` escape hatch.
    "emit": (
        "must not name the outcome",
        "--kind",
        "follows the human review",
        "separate",
        "reports which checks ran",
        "first reviewed state",
        "--iteration",
        # batch mode teaches --all, the per-item continue rule, and the partial-batch exit code.
        "--all",
        "per-item refusal",
        "partial batch exits 3",
    ),
    # a token that already exists at the base commit predates the change and is not a leak;
    # telling it apart from a genuine leak needs a `--clone`.
    "verify": ("must be absent", "control token", "scan-did-not-run", "predates the change"),
    "dataset": ("risk distribution", "low-risk", "automat"),
}

MAN_DIR = Path(__file__).resolve().parents[2] / "man"


def _man_page_for(verb: str | None) -> Path:
    """Path to the man page for a verb (``None`` → the top-level page)."""
    return MAN_DIR / ("eval-harvest.md" if verb is None else f"eval-harvest-{verb}.md")


#: The `###` sub-headings of `capture`'s CANDIDATE FILE section whose tables document a schema, keyed
#: by the TypedDict each one must match field for field. The tables are the machine-readable half of
#: that section: one row per field, the field name backticked in the first column.
_DOCUMENTED_SCHEMA_TABLES: dict[str, type] = {"comments[]": CommentDict, "findings[]": FindingDict}

_FIELD_IN_FIRST_COLUMN = "| `"


def _documented_fields(verb: str, heading_marker: str) -> set[str]:
    """The field names a verb's man page documents in the table under the `###` heading naming `heading_marker`.

    Reads the first column of that one table and stops at the next heading of any level, so a field
    documented under a *different* heading does not count as documenting this one — the drift this
    guards against is a field added to the TypedDict and written down nowhere.
    """
    fields: set[str] = set()
    inside = False
    for line in _man_page_for(verb).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            inside = line.startswith("###") and heading_marker in line
            continue
        if inside and line.startswith(_FIELD_IN_FIRST_COLUMN):
            fields.add(line.split("`")[1])
    return fields


class TestHelpMethodology:
    """Every verb's help teaches its methodology items, not just its flags (FR-1)."""

    def test_each_verb_help_has_methodology(self) -> None:
        for verb, phrases in METHODOLOGY_PHRASES.items():
            help_text = Cli.help_for(verb).lower()
            assert "methodology" in help_text, f"{verb or 'top-level'} help has no methodology section"
            for phrase in phrases:
                assert phrase.lower() in help_text, f"{verb or 'top-level'} help is missing methodology phrase {phrase!r}"

    def test_help_teaches_base_precedes_change(self) -> None:
        help_text = Cli.help_for("capture").lower()
        assert "base commit must precede the change" in help_text

    def test_help_teaches_defect_vs_nit(self) -> None:
        for verb in ("capture", "emit"):
            help_text = Cli.help_for(verb).lower()
            assert "defect" in help_text and "nit" in help_text, f"{verb} help does not distinguish defect from nit"

    def test_help_teaches_instruction_hides_outcome(self) -> None:
        help_text = Cli.help_for("emit").lower()
        assert "must not name the outcome" in help_text


class TestManPages:
    """A man page exists for every verb and carries the same methodology as its help (FR-1)."""

    def test_man_page_exists_for_every_verb(self) -> None:
        for verb in METHODOLOGY_PHRASES:
            page = _man_page_for(verb)
            assert page.is_file(), f"missing man page {page.name}"

    def test_man_pages_carry_methodology(self) -> None:
        for verb, phrases in METHODOLOGY_PHRASES.items():
            man_text = _man_page_for(verb).read_text(encoding="utf-8").lower()
            for phrase in phrases:
                assert phrase.lower() in man_text, f"{_man_page_for(verb).name} is missing methodology phrase {phrase!r}"


class TestCandidateFileDocumented:
    """`capture`'s man page documents the file it writes, field for field (FR-1, FR-13, FR-15, FR-16).

    Without it, learning what a finding looks like means opening hundreds of lines of `candidate.py`
    — the single largest avoidable read for the filling agent. These tests pin the documentation that
    replaces that read, and — via `test_finding_field_docs_match_typeddict` — keep it from decaying
    the first time a field is added.
    """

    def test_capture_man_page_documents_every_finding_field(self) -> None:
        documented = _documented_fields("capture", "findings[]")
        for field in FindingDict.__annotations__:
            assert field in documented, f"`findings[].{field}` is in the schema and not in the man page"

    def test_capture_man_page_documents_every_comment_field(self) -> None:
        documented = _documented_fields("capture", "comments[]")
        for field in CommentDict.__annotations__:
            assert field in documented, f"`comments[].{field}` is in the schema and not in the man page"

    def test_finding_field_docs_match_typeddict(self) -> None:
        """The documented field set *equals* the TypedDict's, in both directions.

        The forward direction catches a new field nobody wrote down; the reverse catches a field
        renamed or removed in code while the prose keeps promising it, which sends the filling agent
        looking for a slot that no longer exists.
        """
        for marker, schema in _DOCUMENTED_SCHEMA_TABLES.items():
            assert _documented_fields("capture", marker) == set(schema.__annotations__), (
                f"the man page's `{marker}` table and {schema.__name__} have drifted apart"
            )

    def test_all_five_classifications_documented(self) -> None:
        """All five values, in the help *and* the man page. `bot` is the one that was missing."""
        help_text = Cli.help_for("capture").lower()
        man_text = _man_page_for("capture").read_text(encoding="utf-8").lower()
        for value in ("defect", "nit", "question", "approval", "bot"):
            assert value in help_text, f"classification {value!r} is not in `capture --help`"
            assert value in man_text, f"classification {value!r} is not in the capture man page"

    def test_severity_evidence_requirement_documented(self) -> None:
        man_text = _man_page_for("capture").read_text(encoding="utf-8").lower()
        assert "required unless" in man_text and "reference_only" in man_text, (
            "the man page must state that severity_evidence is required unless the finding is reference_only"
        )

    def test_reference_only_rule_documented(self) -> None:
        """The exemption *and* its limit. Getting this half-right is worse than not documenting it.

        `reference_only` exempts a finding from the severity-evidence gate and from being required to
        block. It does **not** exempt it from needing a location inside the emitted diff, because
        `verifier_tpl/score.py` matches reference-only findings into `coverage_all` by location
        overlap — so an unreachable location silently deflates every graded agent's coverage.
        """
        man_text = _man_page_for("capture").read_text(encoding="utf-8").lower()
        assert "not required to block" in man_text, "the man page must say what reference_only exempts"
        assert "does not exempt" in man_text and "location inside the emitted diff" in man_text, (
            "the man page must say that reference_only does NOT exempt the location requirement"
        )

    def test_emit_and_verify_cross_reference_the_section(self) -> None:
        """`emit` and `verify` point at the section rather than restating it — one home for the contract."""
        for verb in ("emit", "verify"):
            man_text = _man_page_for(verb).read_text(encoding="utf-8").lower()
            assert "candidate file" in man_text and "eval-harvest-capture(1)" in man_text, (
                f"the {verb} man page must cross-reference capture's CANDIDATE FILE section"
            )


# ───────────────────────────── coding datapoints (FR-1, FR-2, FR-3, FR-22) ─────────────────────────────

#: The heading of each coding verb's coding section, after its review methodology (task F-1 step 15).
#: `brief` takes no `--type` flag (it reads the type from the candidate), so it has the plain heading.
CODING_SECTION_HEADINGS: dict[str, str] = {
    "survey": "Coding datapoints (--type coding):",
    "capture": "Coding datapoints (--type coding):",
    "brief": "Coding datapoints:",
    "show": "Coding datapoints:",
    "annotate": "Coding datapoints:",
    "emit": "Coding datapoints (--kind fix):",
    "verify": "Coding datapoints:",
    "dataset": "Coding datapoints:",
}

#: The FR-3 sentence, pinned exactly: hidden tests that pin oracle details fail correct patches.
BEHAVIORAL_TESTS_SENTENCE = (
    "Hidden tests must test behavior through public interfaces; they must not pin implementation details of the oracle fix."
)

#: The FR-1 task-shape items, one sentence each, in `capture --help`, `emit --help`, and the coding man page.
TASK_SHAPE_SENTENCES: tuple[str, ...] = (
    "an instruction in the style of an issue that names the symptom, not the file or the symbol",
    "the first parent of the merge commit",
    "the source part of the merged fix, only in `solution/`",
    "a test that fails at the base and passes with the oracle",
    "a test that passes at the base and with the oracle",
    "`tests/validate/validation_spec.toml`, stories with cases",
    "criteria of kind `fail_to_pass` or `pass_to_pass`",
    "an LLM judge that compares the patch to the reference and to good practice",
    "a penalty for changes that the fix does not need",
)

#: The FR-1 properties that make a coding datapoint worthless, in `verify --help` and the coding man page.
WORTHLESS_PROPERTIES: tuple[str, ...] = (
    "a fail-to-pass test passes at the base",
    "the instruction names a changed path or symbol",
    "the general instructions are not the same in all tasks",
    "the answer is in a file that the agent can read",
    "the hidden tests pin details of the oracle",
)

#: The FR-22 dependency-pin and test-command teaching.
ENVIRONMENT_RECIPE_PHRASES: tuple[str, ...] = (
    "Pin dependencies to merge_date",
    "uv pip compile --exclude-newer <merge_date>",
    "one line for each setup step",
    "public.ecr.aws/",
    "test_command must write JUnit XML to `$JUNIT_PATH`",
    "-o junit_family=xunit1",
)

#: The §15 unproven-datapoint teaching, in `verify --help` and `dataset --help`.
UNPROVEN_PHRASES: tuple[str, ...] = ("`hidden-tests-proven` stays unresolved", "`proofs/<task-dir>.json`", "--proof")

#: Phrases each verb's coding section must teach, in its `--help` *and* its man page. Matched
#: case-insensitively with whitespace collapsed, because the help is hand-wrapped at 100 columns and
#: the man page wraps differently — the contract pins the words, not the line breaks.
CODING_METHODOLOGY_PHRASES: dict[str, tuple[str, ...]] = {
    "survey": (
        "--type coding",
        "--page <n>",
        "--page-size <k>",
        "--excluded",
        "no-harvestable-fix",
        "no forge call and no git call",
    ),
    "capture": ("--type coding", "fix-base-unrecoverable", "no-source-change", *TASK_SHAPE_SENTENCES),
    "brief": ("coding brief", "no patch bytes", "show --path"),
    "show": ("--path <file>", "one repository path", "unknown-path"),
    "annotate": (BEHAVIORAL_TESTS_SENTENCE, "hidden_test", "rubric_criterion", "--replace-hidden-tests", "pass-to-pass"),
    "emit": ("--kind fix", *TASK_SHAPE_SENTENCES, *ENVIRONMENT_RECIPE_PHRASES, "environment-recipe-invalid"),
    "verify": ("worthless", *WORTHLESS_PROPERTIES, *UNPROVEN_PHRASES, "unproven until a runner writes a valid proof file"),
    "dataset": (*UNPROVEN_PHRASES, "counts as unproven", "per-type `harbor run` commands", "missing reward key as 0"),
}

#: The flags each coding section teaches; every one must exist in that verb's parser.
CODING_FLAGS_BY_VERB: dict[str, tuple[str, ...]] = {
    "survey": ("--type", "--page", "--page-size", "--excluded", "--clone", "--repo", "--dataset"),
    "capture": ("--type", "--from-survey", "--clone", "--dataset"),
    "brief": (),
    "show": ("--path", "--comment"),
    "annotate": ("--replace-hidden-tests", "--check"),
    "emit": ("--kind", "--all", "--dataset", "--iteration"),
    "verify": ("--proof",),
    "dataset": (),
}

CODING_MAN_PAGE = MAN_DIR / "eval-harvest-coding.md"

_FLAG_PATTERN = re.compile(r"(?<![\w-])--[a-z][a-z-]*")

#: Flags of other tools that a coding section quotes in a command line (`uv pip compile --exclude-newer`).
_OTHER_TOOL_FLAGS: frozenset[str] = frozenset({"--exclude-newer"})


def _normalized(text: str) -> str:
    """`text` lowercased with every whitespace run collapsed to one space (hand-wrapped prose matches)."""
    return " ".join(text.split()).lower()


def _coding_section(verb: str) -> str:
    """The coding section of a verb's `--help`: from its heading to the next blank line or the end."""
    help_text = Cli.help_for(verb)
    heading = CODING_SECTION_HEADINGS[verb]
    assert heading in help_text, f"{verb} --help has no {heading!r} section"
    return help_text.split(heading, 1)[1].split("\n\n", 1)[0]


def _parser_flags(verb: str | None) -> set[str]:
    """The flags a parser accepts, read from its rendered help before the methodology (no argparse internals)."""
    options = Cli.help_for(verb).split("Methodology:", 1)[0]
    return set(_FLAG_PATTERN.findall(options))


class TestCodingHelp:
    """Each coding verb's help teaches the coding method after the review methodology (FR-1, FR-2, FR-3, FR-22)."""

    def test_each_coding_verb_help_has_coding_section(self) -> None:
        for verb, phrases in CODING_METHODOLOGY_PHRASES.items():
            help_text = Cli.help_for(verb)
            heading = CODING_SECTION_HEADINGS[verb]
            assert heading in help_text, f"{verb} --help has no {heading!r} section"
            assert help_text.index("Methodology:") < help_text.index(heading), (
                f"{verb}: the coding section is before the review text"
            )
            section = _normalized(_coding_section(verb))
            for phrase in phrases:
                assert _normalized(phrase) in section, f"{verb} --help coding section is missing {phrase!r}"

    def test_top_level_help_has_type_table(self) -> None:
        help_lines = Cli.help_for(None).splitlines()
        rows = {line.split()[0]: line for line in help_lines if line.startswith(("  review  ", "  coding  "))}
        assert set(rows) == {"review", "coding"}, "the top-level help has no review/coding type table"
        assert "a PR change at one review round" in rows["review"]
        assert "approve or reject" in rows["review"]
        assert "an issue-style instruction at" in rows["coding"]
        assert "a patch that fixes the" in rows["coding"]
        table = _normalized(Cli.help_for(None).split("Datapoint types:", 1)[1].split("Sample input restriction", 1)[0])
        for header in ("the agent under test receives", "it must produce", "what scores it"):
            assert header in table
        for scorer in ("hidden tests", "validation", "rubric", "taste judge", "patch bloat", "the human verdict"):
            assert scorer in table, f"the type table does not name {scorer!r}"

    def test_help_requires_behavioral_tests(self) -> None:
        assert _normalized(BEHAVIORAL_TESTS_SENTENCE) in _normalized(_coding_section("annotate"))
        assert BEHAVIORAL_TESTS_SENTENCE in " ".join(CODING_MAN_PAGE.read_text(encoding="utf-8").replace("**", "").split())

    def test_help_gives_exclude_newer_example(self) -> None:
        assert "uv pip compile --exclude-newer <merge_date>" in Cli.help_for("emit")

    def test_help_says_coding_is_unproven_without_proof(self) -> None:
        for verb in ("verify", "dataset"):
            section = _normalized(_coding_section(verb))
            assert "`hidden-tests-proven` stays unresolved" in section, f"{verb} --help does not say coding is unproven"
            assert "unproven" in section

    def test_coding_flags_in_help_exist_in_parser(self) -> None:
        every_flag = set().union(*(_parser_flags(verb) for verb in Cli.VERBS), _parser_flags(None))
        for verb, flags in CODING_FLAGS_BY_VERB.items():
            section = _coding_section(verb)
            own_flags = _parser_flags(verb)
            for flag in flags:
                assert flag in section, f"{verb} coding section does not teach {flag}"
                assert flag in own_flags, f"{verb} coding section teaches {flag}, which its parser does not have"
            for flag in set(_FLAG_PATTERN.findall(section)) - _OTHER_TOOL_FLAGS:
                assert flag in every_flag, f"{verb} coding section names {flag}, which no parser has"


class TestCodingManPages:
    """The man pages carry the same coding method as the help, and the coding page teaches the task shape."""

    def test_coding_man_pages_carry_coding_phrases(self) -> None:
        for verb, phrases in CODING_METHODOLOGY_PHRASES.items():
            man_text = _normalized(_man_page_for(verb).read_text(encoding="utf-8"))
            assert "## coding datapoints" in man_text, f"{_man_page_for(verb).name} has no CODING DATAPOINTS section"
            assert "eval-harvest-coding(7)" in man_text, f"{_man_page_for(verb).name} does not point at eval-harvest-coding(7)"
            for phrase in phrases:
                assert _normalized(phrase) in man_text, f"{_man_page_for(verb).name} is missing coding phrase {phrase!r}"

    def test_coding_man_page_teaches_task_shape(self) -> None:
        man_text = _normalized(CODING_MAN_PAGE.read_text(encoding="utf-8").replace("**", ""))
        # The page formats names as code where the help leaves them plain, so the items match without backticks.
        unformatted = man_text.replace("`", "")
        for sentence in (*TASK_SHAPE_SENTENCES, *WORTHLESS_PROPERTIES, *ENVIRONMENT_RECIPE_PHRASES):
            assert _normalized(sentence).replace("`", "") in unformatted, f"eval-harvest-coding.md is missing {sentence!r}"
        for section in ("## synopsis", "## description", "## methodology", "## task shape", "## exit status", "## see also"):
            assert section in man_text, f"eval-harvest-coding.md has no {section!r} section"
        # E-2's section stays, word for word in its key sentences: runner authors read it.
        assert "## proof file (for runner authors)" in man_text
        assert "`verify` trusts the runner: the digest stops a stale proof, not a false one." in man_text

    def test_top_level_man_page_has_type_table(self) -> None:
        man_text = _man_page_for(None).read_text(encoding="utf-8")
        assert "| review | a PR change at one review round | approve or reject, plus findings |" in man_text
        assert "| coding | an issue-style instruction at the base commit | a patch that fixes the issue |" in man_text
        assert "PR-review and coding" in man_text
        assert "`eval-harvest-coding(7)`" in man_text
