#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. `a-tracked-file-holds-ascii` rather than a character that looks like ASCII.

An em-dash looks like a hyphen. A curly quote looks like an apostrophe. A non-breaking space looks like a space. A
reader cannot tell them apart. A column count disagrees with the editor. A grep for the ASCII form skips the line.

The hook reads the file the edit would leave. A file outside the tree belongs to somebody else and goes unread.

`check_ascii` is the authority and reads the tree. This refuses the character as an edit writes it.
"""

import json
import os
import sys
import unicodedata

import check_ascii
import collect_fragments
import gate
import refusal


def _past_ascii(text: str) -> list[str]:
    """The characters of `text` past ASCII. A codepoint names the character."""
    found = {character for character in text if ord(character) > 127}
    return [f"U+{ord(character):04X} {unicodedata.name(character, 'unnamed')}" for character in sorted(found)]


def refusal_for(text: str, path: str) -> str | None:
    """
    The refusal the ASCII rule gives for `text` at `path`, or None where the characters pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.
    """
    found = _past_ascii(text)
    if not found:
        return None
    return (
        f"This edit writes a character past ASCII into {path}: {', '.join(found)}.\n\n"
        "The character looks like the ASCII it stands in for. A reader cannot tell them apart. A column count "
        "disagrees with the editor, and a grep for the ASCII form skips the line.\n\n"
        "WRITE ASCII. An em-dash comes out as a full stop and a second sentence. A curly quote comes out straight. "
        "An arrow comes out as a word. Where the text names the character itself, write the escape that names the "
        "codepoint.\n\n"
        "`check_ascii._MAY_HOLD_ANY_BYTE` declares a path that holds such a character as content.\n\n"
        "Rule: a-tracked-file-holds-ascii."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.written(payload.get("tool_input", {}))
    if edit is None or not gate.is_in_the_tree(edit.path):
        return
    path = os.path.relpath(os.path.abspath(edit.path), gate.TREE)
    if check_ascii.declaring(path):
        return
    found = refusal_for(edit.now, path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
