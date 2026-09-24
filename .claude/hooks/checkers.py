#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
The checkers that read prose, and the refusals they give.

`_CHECKERS` names them. `CALLED` comes off that list. `refusals_for_edit` runs the checkers over an edit, and a caller
reaches the checkers through that function. `prose_gate` calls `refusals_for_edit` on an edit a writer makes, and
`.claude/settings.json` registers `prose_gate` on an edit. `check_prose` calls that function on an edit that
`collect_fragments.whole` makes of a file of the tree. `apply_prose` calls that function on a settling write before the
write lands. `record_run` calls that function on a proposal bound for the pending proposals file. A checker added to
`_CHECKERS` reaches those callers at once.

A checker left out of `_CHECKERS` reaches none of those callers.

A checker reads what the edit touched. A prose checker reads prose with the line wrapping taken out, and
`collect_fragments.flattened` takes it out. `collect_fragments.prose_digest` flattens through the same function.
Rewrapping a file then moves no refusal and no digest. A checker asking about the file reads `_Measured`, and
`_measured` fills that by reading the file the edit would leave against the file before it.

An entry says which text its checker reads. `_PROSE` names the prose of a fragment the edit touches. `_DOCUMENT` names
the prose of the file whole. `_FILE` names the readings in `_Measured`, and a checker there reads no text. `_ANY` names
the whole new text of a file the prose rules leave alone.

A literal takes a separate pass through the `_PROSE` checkers. The shape rules read a literal more narrowly than a
comment.

The pending proposals file holds a bullet that `settling_state.WHY` opens. That bullet states a reason, and the WHY
rules pass it. The WHY rules read the other prose of the tree in full.

**Usage:** `import checkers`, then `checkers.refusals_for_edit(edit, path)`.
"""

import dataclasses
import os

from typing import Any

import altitude
import ascii_only
import check_documents
import collect_fragments
import document_rules
import gate
import nested_docstrings
import no_tree_numbers
import prose_citations
import prose_rewrite
import prose_words
import settling_state
import short_comments

# The prose of a fragment the edit touches. `prose_words` reads that prose.
_PROSE = "prose"

# The prose of a document whole. `no_tree_numbers` reads a document that way. A count in a heading counts too.
_DOCUMENT = "document"

# The readings of the file that `_Measured` holds. `short_comments` asks how many lines a comment run takes.
_FILE = "file"

# The whole new text of a file that `is_prose_of_the_project` turns down. `ascii_only` reads that file.
_ANY = "any"

# The pending proposals file, as a caller names it.
_PENDING = os.path.relpath(settling_state.PENDING, gate.TREE)


@dataclasses.dataclass(frozen=True)
class _Measured:
    """
    The readings a checker takes of the file rather than of the words.

    `short_comments.runs_of` fills `comment_runs`. `nested_docstrings.long_ones_of` fills `nested_docstrings`.
    `prose_citations.minted_by` fills `minted`. `document_rules.roster_faults_of` fills `roster_faults`.
    """

    comment_runs: tuple[tuple[str, int], ...] = ()
    nested_docstrings: tuple[tuple[int, str, int], ...] = ()
    minted: frozenset[str] = frozenset()
    roster_faults: tuple[str, ...] = ()


def _measured(source: str, path: str, was: str | None = None) -> _Measured:
    """
    The readings `_CHECKERS` takes of the file the edit would leave.

    A checker reads these rather than the lines of a text. A question about a comment run is a question about the file.
    Unwrapping a source would take the code apart.

    `was` is the file before the edit. A reading the file already held drops out, the way `fragments_touched` drops a
    fragment the edit left alone. A checker then refuses no fault the edit did not write. `minted` keeps the whole file.
    A docstring may cite a name written elsewhere in that file.
    """
    roster = document_rules.roster_faults_of(source, path)
    if not path.endswith(".py"):
        return _Measured(roster_faults=roster)
    held = short_comments.runs_of(source)
    nested = nested_docstrings.long_ones_of(source)
    if was is not None:
        before = set(short_comments.runs_of(was))
        held = tuple(one for one in held if one not in before)
        named = {(function, length) for _line, function, length in nested_docstrings.long_ones_of(was)}
        nested = tuple(one for one in nested if (one[1], one[2]) not in named)
    return _Measured(held, nested, prose_citations.minted_by(source), roster)


@dataclasses.dataclass(frozen=True)
class _Prose:
    """
    A text as a checker reads it.

    `said` is the text with the wrapping taken out. `path` is the file holding it. `is_literal` marks a string literal.
    A literal takes a narrower shape rule than a comment takes.

    `is_stating_why` marks the `why` bullet of a pending proposal. The WHY rules pass that bullet.

    `measured` holds the readings a checker would otherwise take of the file.
    """

    said: str
    path: str
    is_literal: bool = False
    is_stating_why: bool = False
    measured: _Measured = _Measured()


# A key names a hook registered on an edit. `refusals_for_edit` cannot run that hook. The value is the reason.
NO_CALLABLE = {
    "question-rule.sh": "a shell hook. it reads an isinstance chain rather than prose.",
    "step-rules.sh": "a shell hook. it reads a step name rather than prose.",
    "tense-check.sh": "a shell hook. it warns rather than refuses, and the path comes from a tool call.",
    "unread_files.py": "a path rule. it reads the file name rather than prose.",
}

# The prose checkers. A row names the module of the checker. A row holds the call that gives the refusal. The last field
# names the text the checker reads. A module naming a checker reads this list rather than writing its own.
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
    (
        "document_rules.py",
        lambda one: document_rules.refusal_for(one.said, one.path, one.measured.roster_faults),
        _DOCUMENT,
    ),
    ("ascii_only.py", lambda one: ascii_only.refusal_for(one.said, one.path), _ANY),
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
)

# The hook registered on an edit. A checker runs through this hook. `check_hooks` fires a checker's pair at it.
THE_HOOK = "prose_gate.py"

# The modules `refusals_for_edit` runs. `check_hooks` reads these modules beside the roster.
CALLED = tuple(name for name, _call, _reads in _CHECKERS)


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
    at = next((at for at, line in enumerate(lines) if line.lstrip().startswith(opens)), None)
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

    A fragment the edit touches takes a separate pass through the `_PROSE` checkers. So does a literal of that edit. The
    prose of the file goes to the `_DOCUMENT` checkers whole. `is_prose_of_the_project` decides whether a file reaches
    the prose checkers at all. The `_ANY` checkers read the file either way.

    A prose checker reads the text with the wrapping taken out. A `_FILE` checker reads `_measured` and no text.
    """
    found = _refusals(_Prose(edit.now, path), _ANY)
    if not check_documents.is_prose_of_the_project(path):
        return found
    taken = _measured(edit.now, path, edit.was)
    touched = [one.prose.content for one in collect_fragments.fragments_touched(edit)]
    for prose in touched:
        if prose.strip():
            found += _prose_refusals(prose, path)
    literals = [text for _at, text in collect_fragments.literals_touched(edit)]
    for text in literals:
        found += _refusals(_Prose(collect_fragments.flattened(text), path, is_literal=True), _PROSE)
    # A markdown file goes to the document checkers whole. A count in a heading is a count. A source hands them the same
    # prose the shape rules read, the code around it holding no prose.
    read = edit.now if path.endswith(".md") else "\n\n".join([*touched, *literals])
    found += _refusals(_Prose(collect_fragments.flattened(read), path, measured=taken), _DOCUMENT)
    return found + _refusals(_Prose("", path, measured=taken), _FILE)
