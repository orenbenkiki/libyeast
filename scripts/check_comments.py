#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Enforce C comment style in the given .c/.h files.

Rule: a /* ... */ comment goes inline only, with non-whitespace code following the closing */ on the same line, as in
`foo(/* count */ 5)`. Any other comment must use //.

Violations print as `<path>:<line>: message` for an editor to go to.
"""

import sys


def _block_comments(text: str) -> list[tuple[int, int, int, int]]:
    """
    `(start_line, start_column, end_line, end_column)` per /* */ comment.

    A column counts from `0`, and the end column is at the '*' of the closing '*/'. The scan skips strings, character
    literals and // line comments. A /* inside those opens nothing.
    """
    comments = []
    state = "code"
    start_line = start_column = 0  # where the open block comment began, read only while inside it
    line, column, index, size = 1, 0, 0, len(text)
    while index < size:
        character = text[index]
        following = text[index + 1] if index + 1 < size else ""
        step = 1
        if state == "code":
            if character == '"':
                state = "string"
            elif character == "'":
                state = "character"
            elif character == "/" and following == "/":
                state = "line"
                step = 2
            elif character == "/" and following == "*":
                state = "block"
                start_line, start_column = line, column
                step = 2
        elif state in ("string", "character"):
            if character == "\\":
                step = 2
            elif character == ('"' if state == "string" else "'"):
                state = "code"
        elif state == "line":
            if character == "\n":
                state = "code"
        elif state == "block":
            if character == "*" and following == "/":
                comments.append((start_line, start_column, line, column))
                state = "code"
                step = 2
        for offset in range(step):
            if index + offset < size and text[index + offset] == "\n":
                line += 1
                column = 0
            else:
                column += 1
        index += step
    return comments


def _check(path: str) -> list[tuple[int, str]]:
    """The (line, complaint) per /* */ comment in `path` that is not inline."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    lines = text.split("\n")
    problems = []
    for _start_line, _start_column, end_line, end_column in _block_comments(text):
        after = lines[end_line - 1][end_column + 2 :]
        if after.strip() == "":
            problems.append((end_line, "use // here. a /* */ comment goes inline only, and code follows it."))
    return problems


def main() -> int:
    is_failed = False
    for path in sys.argv[1:]:
        for line, complaint in _check(path):
            print(f"{path}:{line}: {complaint}")
            is_failed = True
    if is_failed:
        print("comment-style check failed", file=sys.stderr)
    return 1 if is_failed else 0


if __name__ == "__main__":
    sys.exit(main())
