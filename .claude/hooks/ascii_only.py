#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a character past ASCII. `a-tracked-file-holds-ascii` is the rule.

An em-dash looks like a hyphen. A curly quote looks like an apostrophe. A non-breaking space looks like a space. A
reader cannot tell them apart. A column count disagrees with the editor. A grep for the ASCII form skips the line.

The hook reads the file the edit would leave. The hook skips a file outside the tree.

`check_ascii` refuses the same characters in the tree.
"""

import check_ascii


def refusal_for(text: str, path: str) -> str | None:
    """
    Return the refusal the ASCII rule gives for `text` at `path`. Return None where the characters of `text` pass.

    A caller with prose and no file asks here. `check_ascii` finds the characters, and the gate over the tree finds them
    the same way. `check_ascii.declaring` names a file holding such a character as content, and `refusal_for` passes
    that file.
    """
    if check_ascii.declaring(path):
        return None
    found = sorted({check_ascii.named_character(one) for _, _, one in check_ascii.past_ascii(text)})
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
