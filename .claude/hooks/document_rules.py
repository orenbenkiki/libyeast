#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker `checkers` runs. It holds a document to the rules `check_documents` and `check_conventions` decide over the
whole file.

`check_documents.history_errors` reads `DESIGN.md`, `PLAN.md` and `CHANGELOG.md` for a document narrating its own
history. `check_documents.domain_errors` reads `DESIGN.md` and `CHANGELOG.md` for a tense another document owns.
`text-says-what-is` is the rule of both. `check_conventions.convention_note_faults` reads `.claude/conventions.md`.
`a-mechanised-rule-names-its-convention` is that rule.

An edit changes a single file. The `verify-documents` gate reads `README.md` against the `Makefile`, and an edit can
then add a target to both files in turn.

The `check_prose` gate reads the tree through the same functions by way of `checkers.refusals_for_edit`.
"""

import check_conventions
import check_documents


def _said(found: list[str], rule: str) -> str:
    """The refusal that lists the faults `found` and ends on the rule `rule`. An empty rule ends on the list."""
    listed = "This edit leaves a document fault:\n\n" + "\n".join(f"  {one}" for one in found)
    return listed + (f"\n\nRule: {rule}." if rule else "")


def refusal_for(text: str, path: str) -> str | None:
    """The refusals the whole-document rules give for `text` at `path`, joined, or None."""
    held = []
    tense = check_documents.history_errors(path, text) + check_documents.domain_errors(path, text)
    if tense:
        held.append(_said(tense, "text-says-what-is"))
    notes = check_conventions.convention_note_faults(path, text)
    if notes:
        held.append(_said(notes, "a-mechanised-rule-names-its-convention"))
    return "\n\n".join(held) or None
