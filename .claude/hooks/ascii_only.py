#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
`prose_gate` runs this checker on Edit and Write. This checker refuses a character past ASCII.
`a-tracked-file-holds-ascii` is the rule.

An em-dash looks like a hyphen. A curly quote looks like an apostrophe. A non-breaking space looks like a space. A
reader cannot tell them apart. A column count disagrees with the editor. A grep for the ASCII form skips the line.

`refusals_for_edit` hands this checker the file the edit would leave. `prose_gate` skips a file outside the tree. This
checker skips a path that `_MAY_HOLD_ANY_BYTE` declares.

`check_ascii` refuses a pattern of `_MAY_HOLD_ANY_BYTE` that matches no file holding such a character.
"""

import check_ascii


def refusal_for(text: str, path: str) -> str | None:
    """
    Return the refusal the ASCII rule gives for `text` at `path`. Return None where the characters of `text` pass.

    A caller holding the text of an edit asks here, before that text lands in its file. `check_ascii` finds a character
    past ASCII. `check_ascii.declaring` gives the patterns of `_MAY_HOLD_ANY_BYTE` that `path` matches, and
    `refusal_for` passes a path matching any of them.
    """
    if check_ascii.declaring(path):
        return None
    found = sorted({check_ascii.named_character(one) for _, _, one in check_ascii.past_ascii(text)})
    if not found:
        return None
    return (
        f"This edit leaves a character past ASCII in {path}: {', '.join(found)}.\n\n"
        "The character looks like the ASCII it stands in for. A reader cannot tell them apart. A column count "
        "disagrees with the editor, and a grep for the ASCII form skips the line.\n\n"
        "WRITE ASCII. An em-dash comes out as a full stop and a second sentence. A curly quote comes out straight. "
        "An arrow comes out as a word. Where the text names the character itself, write the escape that names the "
        "codepoint.\n\n"
        "`check_ascii._MAY_HOLD_ANY_BYTE` declares a path that holds such a character as content.\n\n"
        "Rule: a-tracked-file-holds-ascii."
    )
