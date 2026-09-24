#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker `checkers` runs. It holds a document to the rules `check_documents` and `check_conventions` decide over the
whole file.

`check_documents.history_errors` reads `DESIGN.md`, `PLAN.md` and `CHANGELOG.md` for a document narrating its own
history. `check_documents.domain_errors` reads the same documents for a tense another document owns. `text-says-what-is`
is the rule of both. `check_conventions.convention_note_faults` reads `.claude/conventions.md`.
`a-mechanised-rule-names-its-convention` is that rule.

`roster_faults_of` reads `README.md` against the `Makefile`. A target of the `Makefile` is code. `checkers` takes that
reading of the file and hands the faults to `refusal_for`.

The gates read the tree through the same functions.
"""

import check_conventions
import check_documents


def roster_faults_of(text: str, path: str) -> tuple[str, ...]:
    """
    The disagreements between the roster `README.md` lists and the targets the `Makefile` holds.

    `checkers` reads this off the file the edit would leave. A target is code, and the prose of the file names none.
    """
    return tuple(check_documents.readme_roster_errors(path, text))


def _said(found: list[str], rule: str) -> str:
    """The refusal that lists the faults `found` and ends on the rule `rule`. An empty rule ends on the list."""
    listed = "This edit leaves a document fault:\n\n" + "\n".join(f"  {one}" for one in found)
    return listed + (f"\n\nRule: {rule}." if rule else "")


def refusal_for(text: str, path: str, roster_faults: tuple[str, ...] | None = None) -> str | None:
    """
    The refusals the whole-document rules give for `text` at `path`, joined, or None.

    `roster_faults` comes from `roster_faults_of`. A caller that gives none has `roster_faults_of` read `text`.
    """
    held = []
    tense = check_documents.history_errors(path, text) + check_documents.domain_errors(path, text)
    if tense:
        held.append(_said(tense, "text-says-what-is"))
    roster = roster_faults_of(text, path) if roster_faults is None else roster_faults
    if roster:
        held.append(_said(list(roster), ""))
    notes = check_conventions.convention_note_faults(path, text)
    if notes:
        held.append(_said(notes, "a-mechanised-rule-names-its-convention"))
    return "\n\n".join(held) or None
