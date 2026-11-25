#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker refuses a code line past `gate.WIDEST` columns whose comment holds a mark
`gate.A_MARK` matches. `a-marker-fits-its-line` is the rule.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import collect_fragments
import gate


def refusal_for(text: str, path: str) -> str | None:
    """
    The refusal the marker rule gives for `text` at `path`, or None where the marked code lines fit. The checker skips a
    file whose language has no marker in `collect_fragments.MARKERS`. `collect_fragments.trailing_comments` finds the
    comment that follows code on a line.

    Public. `checkers.refusals_for_edit` runs this over the whole new text of a file.
    """
    language = collect_fragments.language_of(path)
    if not collect_fragments.MARKERS.get(language or "", ""):
        return None
    lines = text.split("\n")
    found = [
        f"  {path}:{at}"
        for at, column in sorted(collect_fragments.trailing_comments(text, language).items())
        if len(lines[at - 1]) > gate.WIDEST and gate.A_MARK.search(lines[at - 1], column)
    ]
    if not found:
        return None
    return (
        f"This edit leaves a marked code line past {gate.WIDEST} columns:\n\n"
        + "\n".join(found)
        + "\n\nShorten the reason after the mark.\n\nRule: a-marker-fits-its-line."
    )
