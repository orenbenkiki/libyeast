#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
The checkers that read prose, and the refusals they give.

`_CHECKERS` names them. `CALLED` comes off that list. `refusals_for_edit` runs the checkers over an edit, and a caller
reaches the checkers through that function. `prose_gate` calls `refusals_for_edit` on an edit a writer makes, and
`.claude/settings.json` registers `prose_gate` on an edit. `check_prose` calls that function on an edit that
`collect_fragments.whole` makes of a file of the tree. `apply_prose` calls that function on a settling write before the
write lands. `record_run` reaches that function through `apply_prose.text_refusals` on a proposal bound for the pending
proposals file. A checker added to `_CHECKERS` reaches those callers at once.

A checker left out of `_CHECKERS` reaches none of those callers.

A `_PROSE` checker reads what the edit touched. A prose checker reads prose with the line wrapping taken out, and
`collect_fragments.flattened` takes it out. `collect_fragments.prose_digest` flattens through the same function.
Rewrapping a file then moves no `_PROSE` or `_DOCUMENT` refusal and no digest. A checker asking about the file reads
`_Measured`, and `_measured` fills that from the file the edit would leave.

An entry says which text its checker reads. `_PROSE` names the prose of a fragment the edit touches. `_DOCUMENT` names
the whole text of a markdown file, and the touched prose and literals of a source. `_FILE` names the readings in
`_Measured`, and a checker there reads no text. `_ANY` names the whole new text of any file.

A literal takes a separate pass through the `_PROSE` checkers. The shape rules read a literal through
`literal_shape_faults`, more narrowly than `prose_faults` reads a comment.

The pending proposals file holds a bullet that `settling_state.WHY` opens. That bullet states a reason, and the WHY
rules pass it. The WHY rules read the other prose of the tree in full.

**Usage:** `import checkers`, then `checkers.refusals_for_edit(edit, path)`.
"""

import dataclasses
import os

from typing import Any

import altitude
import ascii_only
import collect_fragments
import design_copies
import document_rules
import gate
import kind_dispatches
import marker_lines
import nested_docstrings
import no_tree_numbers
import plan_pointers
import prose_citations
import prose_rewrite
import prose_words
import public_citations
import settling_state
import short_comments
import step_arguments
import unread_files

# The prose of a fragment the edit touches, or a literal of that edit. `prose_words` and `prose_rewrite` read that text.
_PROSE = "prose"

# The text of a markdown file whole, or the touched fragments and literals of a source joined. `no_tree_numbers` reads a
# document that way. `numbers_in_prose` counts a numeral in a heading.
_DOCUMENT = "document"

# The readings of the file that `_Measured` holds. `short_comments.runs_of` counts the lines a comment run takes.
_FILE = "file"

# The whole new text of a file, whether or not `is_prose_of_the_project` takes the file.
_ANY = "any"

# The pending proposals file, as a caller names it.
_PENDING = os.path.relpath(settling_state.PENDING, gate.TREE)


@dataclasses.dataclass(frozen=True)
class _Measured:
    """
    The readings a checker takes of the file rather than of the words.

    `short_comments.runs_of` fills `comment_runs`. `nested_docstrings.long_ones_of` fills `nested_docstrings`.
    `prose_citations.minted_by` fills `minted`. `design_copies.copies_of` fills `copied`.
    `kind_dispatches.dispatches_of` fills `dispatches`. `step_arguments.unargued_of` fills `unargued`.
    """

    comment_runs: tuple[tuple[str, int], ...] = ()
    nested_docstrings: tuple[tuple[int, str, int], ...] = ()
    minted: frozenset[str] = frozenset()
    copied: tuple[str, ...] = ()
    dispatches: tuple[str, ...] = ()
    unargued: tuple[str, ...] = ()


def _measured(source: str, path: str) -> _Measured:
    """
    The readings `_CHECKERS` takes of the file the edit would leave.

    A `_FILE` checker reads these rather than the lines of a text. A question about a comment run is a question about
    the file. Unwrapping a source with `collect_fragments.flattened` would take the code apart. A reading covers the
    whole file. A checker such as `short_comments.refusal_for` therefore refuses a fault the file held before the edit.
    """
    if not path.endswith(".py"):
        return _Measured(copied=design_copies.copies_of(source, path))
    return _Measured(
        short_comments.runs_of(source),
        nested_docstrings.long_ones_of(source),
        prose_citations.minted_by(source),
        dispatches=kind_dispatches.dispatches_of(source, path),
        unargued=step_arguments.unargued_of(source, path),
    )


@dataclasses.dataclass(frozen=True)
class _Prose:
    """
    A text as a checker reads it.

    `said` is the text a checker reads. The `_PROSE` and `_DOCUMENT` checkers read `said` with the wrapping taken out.
    `path` is the file holding the text. `is_literal` marks a string literal. `prose_rewrite.refusal_for` checks a
    literal with `literal_shape_faults`. `literal_shape_faults` blanks the speaker and the place a literal opens on.

    `is_stating_why` marks the `why` bullet of a pending proposal. `prose_rewrite.refusal_for` passes that bullet under
    the WHY rules.

    `measured` holds the readings a checker would otherwise take of the file.
    """

    said: str
    path: str
    is_literal: bool = False
    is_stating_why: bool = False
    measured: _Measured = _Measured()


# The checkers that `_refusals` runs. A row names the module of the checker. A row holds the call that gives the
# refusal. The last field names the text the checker reads. A module naming a checker reads this list rather than
# writing its own.
_CHECKERS = (
    ("prose_words.py", lambda one: prose_words.refusal_for(one.said, one.path), _PROSE),
    (
        "prose_rewrite.py",
        lambda one: prose_rewrite.refusal_for(one.said, one.path, one.is_stating_why, one.is_literal),
        _PROSE,
    ),
    ("no_tree_numbers.py", lambda one: no_tree_numbers.refusal_for(one.said, one.path), _DOCUMENT),
    (
        "prose_citations.py",
        lambda one: prose_citations.refusal_for(one.said, one.path, one.measured.minted),
        _DOCUMENT,
    ),
    ("altitude.py", lambda one: altitude.refusal_for(one.said, one.path), _DOCUMENT),
    ("public_citations.py", lambda one: public_citations.refusal_for(one.said, one.path), _DOCUMENT),
    (
        "document_rules.py",
        lambda one: document_rules.refusal_for(one.said, one.path),
        _DOCUMENT,
    ),
    ("ascii_only.py", lambda one: ascii_only.refusal_for(one.said, one.path), _ANY),
    ("plan_pointers.py", lambda one: plan_pointers.refusal_for(one.said, one.path), _ANY),
    ("marker_lines.py", lambda one: marker_lines.refusal_for(one.said, one.path), _ANY),
    (
        "short_comments.py",
        lambda one: short_comments.refusal_for(one.said, one.path, one.measured.comment_runs),
        _FILE,
    ),
    (
        "nested_docstrings.py",
        lambda one: nested_docstrings.refusal_for(one.said, one.path, one.measured.nested_docstrings),
        _FILE,
    ),
    ("design_copies.py", lambda one: design_copies.refusal_for(one.said, one.path, one.measured.copied), _FILE),
    (
        "kind_dispatches.py",
        lambda one: kind_dispatches.refusal_for(one.said, one.path, one.measured.dispatches),
        _FILE,
    ),
    ("step_arguments.py", lambda one: step_arguments.refusal_for(one.said, one.path, one.measured.unargued), _FILE),
    ("unread_files.py", lambda one: unread_files.refusal_for(one.said, one.path), _ANY),
)

# `.claude/settings.json` registers this hook on an edit. A checker runs through this hook. `check_hooks` fires the
# fixture pair of a checker at this hook.
THE_HOOK = "prose_gate.py"

# The modules `refusals_for_edit` runs. `check_hooks` reads these modules beside the roster.
CALLED = tuple(name for name, _call, _reads in _CHECKERS)

# The modules of the `_FILE` entries. `check_hooks` wants `tests/hooks.json` to hold a pair for such a module. The pair
# probes a fault the file held before the edit.
READS_THE_FILE = tuple(name for name, _call, reads in _CHECKERS if reads == _FILE)


def _refusals(one: _Prose, reads: str) -> list[str]:
    """Run the checkers that read `reads` over `one`, and give back their refusals."""
    found = [call(one) for _name, call, wants in _CHECKERS if wants == reads]
    return [said for said in found if said]


def _prose_refusals(prose: str, path: str) -> list[str]:
    """
    The `_PROSE` refusals for the prose of a fragment at `path`.

    The pending proposals file hands its `settling_state.WHY` bullet over apart from the other lines. That bullet runs
    to the next bullet, and it states a reason.
    """
    lines = prose.split("\n")
    opens = f"- {settling_state.WHY}"
    at = next((index for index, line in enumerate(lines) if line.lstrip().startswith(opens)), None)
    if path != _PENDING or at is None:
        return _refusals(_Prose(collect_fragments.flattened(prose), path), _PROSE)
    ends = next((end for end in range(at + 1, len(lines)) if lines[end].lstrip().startswith("- ")), len(lines))
    others = "\n".join(lines[:at] + lines[ends:])
    found = _refusals(_Prose(collect_fragments.flattened("\n".join(lines[at:ends])), path, is_stating_why=True), _PROSE)
    if not others.strip():
        return found
    return found + _refusals(_Prose(collect_fragments.flattened(others), path), _PROSE)


def refusals_for_edit(edit: Any, path: str) -> list[str]:
    """
    The refusals `_CHECKERS` gives for an edit. `path` names the file relative to the tree.

    A fragment the edit touches takes a separate pass through the `_PROSE` checkers where its prose is not blank. So
    does a literal of that edit. A markdown file goes to the `_DOCUMENT` checkers whole. A source hands the `_DOCUMENT`
    checkers the touched fragments and literals joined together. `is_prose_of_the_project` decides whether a file
    reaches the prose checkers at all. The `_ANY` checkers read the file either way.

    A `_PROSE` checker and a `_DOCUMENT` checker read the text with the wrapping taken out. A `_FILE` checker reads
    `_measured` and no text.
    """
    found = _refusals(_Prose(edit.now, path), _ANY)
    if not gate.is_prose_of_the_project(path):
        return found
    taken = _measured(edit.now, path)
    touched = [one.prose.content for one in collect_fragments.fragments_touched(edit)]
    for prose in touched:
        if prose.strip():
            found += _prose_refusals(prose, path)
    literals = [literal for _at, literal in collect_fragments.literals_touched(edit)]
    for text in literals:
        found += _refusals(_Prose(collect_fragments.flattened(text), path, is_literal=True), _PROSE)
    # Run the `_DOCUMENT` checkers over the text read, then the `_FILE` checkers over `_measured`.
    read = edit.now if path.endswith(".md") else "\n\n".join([*touched, *literals])
    found += _refusals(_Prose(collect_fragments.flattened(read), path, measured=taken), _DOCUMENT)
    return found + _refusals(_Prose("", path, measured=taken), _FILE)
