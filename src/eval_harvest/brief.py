"""The ``brief`` verb: one self-contained hand-off document for filling one PR's candidate.

Building ten datapoints in one context window is structurally expensive: the workflow the help
teaches is a single agent walking ``init → survey → capture ×10 → fill → emit ×10``, accumulating
every PR's comments and re-reads along the way. Ten
datapoints are ten *independent* units of judgment — nothing about one PR informs another — so they
should be ten small contexts, not one large one.

``brief`` makes that possible: one command that prints everything needed to fill one candidate and
nothing else — identity, the change's risk, the recoverable iterations, which kinds are buildable,
every comment with the in-diff markers, the fill contract, and the exact next commands. A driver
hands one PR's brief to a fresh subagent and the fill work stops accumulating (tech plan §5.1, §7.1).

``brief`` is a *view* of the candidate, never a second source of truth (§5.2): it renders facts the
candidate already holds and the fill contract ``capture`` already prints, and suggests no
classification (FR-9). It prints **no diff bytes** — that is ``show``'s job, per comment, on demand;
ten briefs must stay small, which a patch dump would defeat. This module is pure rendering: data in,
string out, no filesystem or forge access (the rubric version is read by the CLI and passed in).
"""

from __future__ import annotations

import shlex
from typing import Any, Final

from eval_harvest.candidate import FILL_CONTRACT_GUIDE, CandidateDict, CommentDict, Emittability
from eval_harvest.fix_candidate import BODY_CHAR_LIMIT, FIX_FILL_CONTRACT_GUIDE, FixCandidate, FixCandidateDict

#: Printed for the rubric version when the dataset has no ``rubric.md`` (or it declares none). The
#: brief still renders — an unpinned rubric is the agent's to resolve at fill time, not a refusal.
_RUBRIC_UNSET: Final = "(unset — author rubric.md and pin its version)"

#: Column width the ``reject``/``approve`` labels are padded to, so the two emittable rows line up.
_KIND_WIDTH: Final = 8

#: Indent for a section's lines, and the deeper indent a comment block's continuation lines sit at so
#: they align under the ``N. `` of the numbered header.
_SECTION_INDENT: Final = "  "
_COMMENT_CONT_INDENT: Final = "     "

#: The length of the short commit id the coding brief prints for the base and the merge.
_SHORT_SHA: Final = 12

#: The line after a PR or issue body that ``capture`` cut, so the agent never takes it for the full text.
_BODY_CUT_MARKER: Final = f"(body cut at {BODY_CHAR_LIMIT} characters)"


class Brief:
    """Render one PR's candidate into a self-contained fill brief (human or ``--json``). Holds no state."""

    @classmethod
    def render(
        cls,
        candidate: CandidateDict,
        emittability: Emittability,
        *,
        rubric_version: str,
        candidate_display_path: str,
        body_chars: int,
    ) -> str:
        """The human hand-off: identity, risk, iterations, emittable kinds, comments, fill contract, next commands.

        Byte-identical for a human and an agent (FR-4): no colour, no terminal detection, no re-wrap.
        ``body_chars`` truncates each comment body when positive; ``0`` prints every body in full.
        """
        sections = [
            "\n".join(cls._identity_lines(candidate, rubric_version)),
            cls._simple_section("iterations", cls._iteration_lines(candidate)),
            cls._simple_section("emittable", cls._emittable_lines(emittability)),
            cls._comments_section(candidate, emittability, body_chars),
            cls._simple_section("you fill", list(FILL_CONTRACT_GUIDE)),
            cls._simple_section("next", cls._next_commands(candidate, emittability, candidate_display_path)),
        ]
        return "\n\n".join(sections) + "\n"

    @classmethod
    def as_json(
        cls,
        candidate: CandidateDict,
        emittability: Emittability,
        *,
        rubric_version: str,
        candidate_display_path: str,
    ) -> dict[str, Any]:
        """The ``--json`` payload: the same facts as the human rendering, under documented keys, bodies in full.

        Bodies are always full here — a machine consumer wants the exact text — so ``body_chars`` (a
        human-readability knob) does not apply.
        """
        return {
            "repo": candidate["repo"],
            "pr_number": candidate["pr_number"],
            "pr_url": candidate["pr_url"],
            "rubric_version": rubric_version,
            "change_risk": candidate["change_risk"],
            "iterations": [cls._iteration_json(candidate, index) for index in range(len(candidate["iterations"]))],
            "emittable": emittability.as_json(),
            "comments": [cls._comment_json(candidate, comment, emittability) for comment in candidate["comments"]],
            "fill_contract": list(FILL_CONTRACT_GUIDE),
            "next_commands": cls._next_commands(candidate, emittability, candidate_display_path),
        }

    # ───────────────────────────── identity ─────────────────────────────

    @staticmethod
    def _identity_lines(candidate: CandidateDict, rubric_version: str) -> list[str]:
        """The two header lines: repo/PR/url, then the rubric version and the CLI-computed structural risk."""
        risk = candidate["change_risk"]
        version = rubric_version or _RUBRIC_UNSET
        return [
            f"pr-{candidate['pr_number']} · {candidate['repo']} · {candidate['pr_url']}",
            f"rubric: {version} · change risk (structural): {risk['risk_structural']} — rule `{risk['risk_structural_rule']}`",
        ]

    # ───────────────────────────── iterations ─────────────────────────────

    @classmethod
    def _iteration_lines(cls, candidate: CandidateDict) -> list[str]:
        """One line per iteration: index, short tip, recoverability, and how many comments its diff covers."""
        total = len(candidate["comments"])
        return [cls._iteration_line(candidate, index, total) for index in range(len(candidate["iterations"]))]

    @classmethod
    def _iteration_line(cls, candidate: CandidateDict, index: int, total: int) -> str:
        iteration = candidate["iterations"][index]
        recoverable = "recoverable" if iteration["patch_path"] else "unrecoverable"
        inside = cls._in_diff_count(candidate, index)
        return f"{index}  {iteration['tip_sha'][:7]}  {recoverable}   {inside} of {total} comment(s) inside its diff"

    @staticmethod
    def _in_diff_count(candidate: CandidateDict, index: int) -> int:
        """How many comments name ``index`` in their ``in_diff_iterations`` — geometric membership."""
        return sum(1 for comment in candidate["comments"] if index in comment.get("in_diff_iterations", []))

    @classmethod
    def _iteration_json(cls, candidate: CandidateDict, index: int) -> dict[str, Any]:
        iteration = candidate["iterations"][index]
        return {
            "index": index,
            "tip_sha": iteration["tip_sha"],
            "recoverable": bool(iteration["patch_path"]),
            "in_diff_comment_count": cls._in_diff_count(candidate, index),
        }

    # ───────────────────────────── emittable ─────────────────────────────

    @classmethod
    def _emittable_lines(cls, emittability: Emittability) -> list[str]:
        """The reject and approve rows, each naming its iteration and how many comments can anchor a finding.

        When both kinds resolve to the same iteration the PR has only one recoverable review round, so
        the reject/approve split is nominal — stated plainly rather than left to read as "both kinds
        fine". Reached only with at least one recoverable iteration (the empty case is a CLI refusal),
        so both indices are set."""
        lines = [
            cls._emittable_line("reject", emittability.reject_index, emittability.reject_in_diff_comment_ids),
            cls._emittable_line("approve", emittability.approve_index, emittability.approve_in_diff_comment_ids),
        ]
        if emittability.reject_index is not None and emittability.reject_index == emittability.approve_index:
            lines.append(
                f"note: only one recoverable review round (iteration {emittability.reject_index}) — "
                "reject and approve build from the same state"
            )
        return lines

    @staticmethod
    def _emittable_line(kind: str, iteration_index: int | None, in_diff_comment_ids: list[int]) -> str:
        label = kind.ljust(_KIND_WIDTH)
        return f"{label}iteration {iteration_index} — {len(in_diff_comment_ids)} comment(s) can anchor a gradeable finding"

    # ───────────────────────────── comments ─────────────────────────────

    @classmethod
    def _comments_section(cls, candidate: CandidateDict, emittability: Emittability, body_chars: int) -> str:
        """The ``comments (N)`` heading over one numbered block per comment, blank-line separated."""
        blocks = [
            cls._comment_block(number, comment, emittability, body_chars)
            for number, comment in enumerate(candidate["comments"], start=1)
        ]
        body = "\n\n".join(blocks) if blocks else f"{_SECTION_INDENT}(no inline comments)"
        return f"comments ({len(candidate['comments'])})\n{body}"

    @classmethod
    def _comment_block(cls, number: int, comment: CommentDict, emittability: Emittability, body_chars: int) -> str:
        """One comment: a numbered identity line, the in-diff markers, then the body (FR-9: no verdict of ours)."""
        location = f"{comment['path']}:{comment['line_start']}-{comment['line_end']}"
        header = f"{_SECTION_INDENT}{number}. id {comment['id']}  {location}  {comment['author_role']}  {comment['created_at']}"
        membership = (
            f"{_COMMENT_CONT_INDENT}written on iteration {comment['iteration_index']} · "
            f"in diffs {comment['in_diff_iterations']} · {cls._reject_marker(comment, emittability)}"
        )
        body_lines = cls._body_text(comment, body_chars).splitlines() or [""]
        body = "\n".join(f"{_COMMENT_CONT_INDENT}{line}" for line in body_lines)
        return f"{header}\n{membership}\n{body}"

    @staticmethod
    def _reject_marker(comment: CommentDict, emittability: Emittability) -> str:
        """``[in reject diff]`` when the reject iteration's diff covers this comment, else ``[not in reject diff]``.

        The reject datapoint is the one that grades against named defects, so whether the reject diff
        covers a comment is what decides if a finding built on it can anchor a gradeable location."""
        in_reject = emittability.reject_index is not None and emittability.reject_index in comment.get("in_diff_iterations", [])
        return "[in reject diff]" if in_reject else "[not in reject diff]"

    @staticmethod
    def _body_text(comment: CommentDict, body_chars: int) -> str:
        """The comment body verbatim, or truncated to ``body_chars`` with a pointer to ``show`` for the rest.

        Full by default (``body_chars == 0``): a truncated review comment cannot be classified
        honestly, and the brief is meant to be sufficient on its own. Printed verbatim — the exact
        wording is what the classification turns on (FR-4)."""
        body = comment["body"]
        if body_chars <= 0 or len(body) <= body_chars:
            return body
        return f"{body[:body_chars]}… (truncated — run `eval-harvest show` --comment {comment['id']} for the rest)"

    @classmethod
    def _comment_json(cls, candidate: CandidateDict, comment: CommentDict, emittability: Emittability) -> dict[str, Any]:
        return {
            "id": comment["id"],
            "path": comment["path"],
            "line_start": comment["line_start"],
            "line_end": comment["line_end"],
            "author_role": comment["author_role"],
            "created_at": comment["created_at"],
            "iteration_index": comment["iteration_index"],
            "in_diff_iterations": comment["in_diff_iterations"],
            "in_reject_diff": cls._reject_marker(comment, emittability) == "[in reject diff]",
            "body": comment["body"],
        }

    # ───────────────────────────── next commands ─────────────────────────────

    @staticmethod
    def _next_commands(candidate: CandidateDict, emittability: Emittability, candidate_display_path: str) -> list[str]:
        """The exact, runnable next commands for *this* candidate — no placeholders to guess at.

        ``show`` (a concrete comment id), ``annotate`` (the append-only fill), then one ``emit`` per
        buildable kind. Offering both kinds rather than picking one keeps the CLI out of the verdict
        (FR-9): which state becomes the datapoint is the agent's call, not the brief's."""
        commands: list[str] = []
        if candidate["comments"]:
            commands.append(f"eval-harvest show {candidate_display_path} --comment {candidate['comments'][0]['id']}")
        commands.append(f"eval-harvest annotate {candidate_display_path} --stdin")
        if emittability.reject_index is not None:
            commands.append(f"eval-harvest emit {candidate_display_path} --kind reject")
        if emittability.approve_index is not None:
            commands.append(f"eval-harvest emit {candidate_display_path} --kind approve")
        return commands

    # ───────────────────────────── section framing ─────────────────────────────

    @staticmethod
    def _simple_section(heading: str, lines: list[str]) -> str:
        """A heading over its lines, each indented one level — the shape of every non-comment section."""
        indented = "\n".join(f"{_SECTION_INDENT}{line}" for line in lines) if lines else f"{_SECTION_INDENT}(none)"
        return f"{heading}\n{indented}"

    # ───────────────────────────── coding candidate (fix-<n>.json) ─────────────────────────────

    @classmethod
    def render_fix(cls, candidate: FixCandidateDict, *, candidate_display_path: str) -> str:
        """The human brief for a coding candidate: identity, PR and issue text, sizes, paths, slots, next commands.

        No patch bytes (FR-15): the fix is read one file at a time with ``show --path``. The bodies are
        printed as ``capture`` stored them — already cut to 4000 characters, with a marker when cut.
        """
        sections = [
            "\n".join(cls._fix_identity_lines(candidate)),
            cls._simple_section("pr", cls._fix_pr_lines(candidate)),
            cls._simple_section("issues", cls._fix_issue_lines(candidate)),
            cls._simple_section("size", [cls._fix_size_line(candidate)]),
            cls._simple_section("source paths", list(candidate["source_paths"])),
            cls._simple_section("test paths", list(candidate["test_paths"])),
            cls._simple_section("you fill", [*FIX_FILL_CONTRACT_GUIDE, *cls._fix_unfilled_lines(candidate)]),
            cls._simple_section("next", cls._fix_next_commands(candidate, candidate_display_path)),
        ]
        return "\n\n".join(sections) + "\n"

    @classmethod
    def fix_as_json(cls, candidate: FixCandidateDict, *, candidate_display_path: str) -> dict[str, Any]:
        """The ``--json`` brief for a coding candidate: the same facts as :meth:`render_fix`, under documented keys."""
        return {
            "datapoint_type": candidate["datapoint_type"],
            "repo": candidate["repo"],
            "pr_number": candidate["pr_number"],
            "pr_url": candidate["pr_url"],
            "base_commit": candidate["base_commit"],
            "base_commit_date": candidate["base_commit_date"],
            "merge_commit": candidate["merge_commit"],
            "merge_date": candidate["merge_date"],
            "pr": {
                "title": candidate["pr_title"],
                "body": candidate["pr_body"],
                "body_truncated": candidate["pr_body_truncated"],
            },
            "issues": [dict(issue) for issue in candidate["issues"]],
            "size": {
                "source_files": len(candidate["source_paths"]),
                "source_lines_added": candidate["source_lines_added"],
                "source_lines_deleted": candidate["source_lines_deleted"],
                "test_files": len(candidate["test_paths"]),
                "author_commits": candidate["author_commits"],
            },
            "source_paths": list(candidate["source_paths"]),
            "test_paths": list(candidate["test_paths"]),
            "fill_contract": list(FIX_FILL_CONTRACT_GUIDE),
            "unfilled": [violation.pointer for violation in FixCandidate.validate_filled(candidate)],
            "next_commands": cls._fix_next_commands(candidate, candidate_display_path),
        }

    @staticmethod
    def _fix_identity_lines(candidate: FixCandidateDict) -> list[str]:
        """The two header lines: the datapoint, repository, and PR page; then the base and the merge."""
        return [
            f"fix-{candidate['pr_number']} · {candidate['repo']} · {candidate['pr_url']}",
            f"base {candidate['base_commit'][:_SHORT_SHA]} ({candidate['base_commit_date']}) · "
            f"merge {candidate['merge_commit'][:_SHORT_SHA]} · merge_date {candidate['merge_date']}",
        ]

    @classmethod
    def _fix_pr_lines(cls, candidate: FixCandidateDict) -> list[str]:
        """The PR title, then its body, then the cut marker when ``capture`` cut the body."""
        return [candidate["pr_title"], *cls._body_lines(candidate["pr_body"], truncated=candidate["pr_body_truncated"])]

    @classmethod
    def _fix_issue_lines(cls, candidate: FixCandidateDict) -> list[str]:
        """Each linked issue: one identity line, then its body (with the cut marker); empty for no issue."""
        lines: list[str] = []
        for issue in candidate["issues"]:
            lines.append(f"#{issue['number']} {issue['title']} · {issue['url']}")
            lines += cls._body_lines(issue["body"], truncated=issue["body_truncated"])
        return lines

    @staticmethod
    def _body_lines(body: str, *, truncated: bool) -> list[str]:
        """A stored body as lines, verbatim, with :data:`_BODY_CUT_MARKER` after it when it was cut."""
        lines = body.splitlines()
        return [*lines, _BODY_CUT_MARKER] if truncated else lines

    @staticmethod
    def _fix_size_line(candidate: FixCandidateDict) -> str:
        """The fix's size: source files and lines, test files, and the author's commit count."""
        return (
            f"source: {len(candidate['source_paths'])} file(s), "
            f"+{candidate['source_lines_added']} -{candidate['source_lines_deleted']} · "
            f"tests: {len(candidate['test_paths'])} file(s) · author_commits: {candidate['author_commits']}"
        )

    @staticmethod
    def _fix_unfilled_lines(candidate: FixCandidateDict) -> list[str]:
        """One line for each slot :meth:`FixCandidate.validate_filled` still reports, so the fill state is current."""
        return [f"unfilled: {violation.pointer} — {violation.detail}" for violation in FixCandidate.validate_filled(candidate)]

    @staticmethod
    def _fix_next_commands(candidate: FixCandidateDict, candidate_display_path: str) -> list[str]:
        """The exact next commands: ``show`` the first source file, ``annotate`` the slots, ``emit --kind fix``."""
        commands: list[str] = []
        if candidate["source_paths"]:
            commands.append(f"eval-harvest show {candidate_display_path} --path {shlex.quote(candidate['source_paths'][0])}")
        commands.append(f"eval-harvest annotate {candidate_display_path} --stdin")
        commands.append(f"eval-harvest emit {candidate_display_path} --kind fix")
        return commands
