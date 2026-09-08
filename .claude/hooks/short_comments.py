#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. `a-comment-inside-a-body-is-a-note` rather than a passage.

A long comment restates the code in prose, or defends a decision nobody questioned.

Reads only the text the edit introduces. It looks for an indented run of comment lines. A comment at column `0` sits at
module level, and this hook skips it. A run below a field's trailing comment continues that comment, and the length
counts them together.

`check_conventions` is the authority and reads the syntax tree. This refuses the shape as the writer writes it.
"""

import json
import re
import sys

import collect_fragments
import refusal

# The length a comment inside a body may run to.
_MOST_LINES = 2

# An indented line that holds a comment and no code.
_A_COMMENT_LINE = re.compile(r"^\s+#")

# The field whose trailing comment a run below it continues.
_A_FIELD = re.compile(r"\s*self\.\w+")


def _long_runs(added: str) -> list[tuple[int, str]]:
    """The indented comment runs past `_MOST_LINES`, as `(how many lines it runs, its first line)`."""
    lines = added.split("\n")
    held = []
    at = 0
    while at < len(lines):
        if not _A_COMMENT_LINE.match(lines[at]):
            at += 1
            continue
        first = at
        while at < len(lines) and _A_COMMENT_LINE.match(lines[at]):
            at += 1
        before = lines[first - 1] if first else ""
        whole = at - first + (1 if "#" in before and _A_FIELD.match(before) else 0)
        if whole > _MOST_LINES:
            held.append((whole, lines[first].strip()))
    return held


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the length rule gives for `prose` at `path`, or None where the runs pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.
    """
    if not path.endswith(".py"):
        return None
    found = _long_runs(prose)
    if not found:
        return None
    said = "; ".join(f"{length} lines at `{opens}`" for length, opens in found)
    return (
        f"This edit writes a comment of more than {_MOST_LINES} lines inside a body, into {path}: {said}.\n\n"
        "A comment inside a body is a note. A passage there is the code restated in prose, or a defence of a "
        "decision nobody has questioned. The largest one in this tree ran eight lines and came to one.\n\n"
        "CUT IT. Read the code it sits on and keep only what the code does not say. Where a reader would undo the "
        "decision without a word about it, that word is one sentence. Everything else goes.\n\n"
        "Where the block documents the field assigned under it, put it directly above that assignment.\n\n"
        "Rule: a-comment-inside-a-body-is-a-note."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.edited(payload.get("tool_input", {}))
    if edit is None:
        return
    found = refusal_for(edit.now, edit.path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
