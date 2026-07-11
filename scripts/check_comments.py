#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Enforce C comment style in the given .c/.h files.

Rule: a /* ... */ comment is allowed only inline, i.e. with non-whitespace code following the closing */ on the same
line (e.g. `foo(/* count */ 5)`). Every other comment — standalone, trailing, or multi-line — must use //.

Violations print as <path>:<line>: message, for editor go-to-line.
"""

import sys


def find_block_comments(text):
    """Yield (start_line, start_col, end_line, end_col) for each /* */ comment (columns 0-based, end_col at the '*' of
    the closing '*/'). Strings, char literals, and // line comments are skipped."""
    comments = []
    state = "code"
    start = None
    line, col, i, n = 1, 0, 0, len(text)
    while i < n:
        c = text[i]
        d = text[i + 1] if i + 1 < n else ""
        step = 1
        if state == "code":
            if c == '"':
                state = "string"
            elif c == "'":
                state = "char"
            elif c == "/" and d == "/":
                state = "line"
                step = 2
            elif c == "/" and d == "*":
                state = "block"
                start = (line, col)
                step = 2
        elif state == "string":
            if c == "\\":
                step = 2
            elif c == '"':
                state = "code"
        elif state == "char":
            if c == "\\":
                step = 2
            elif c == "'":
                state = "code"
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state == "block":
            if c == "*" and d == "/":
                comments.append((start[0], start[1], line, col))
                state = "code"
                step = 2
        for k in range(step):
            if i + k < n and text[i + k] == "\n":
                line += 1
                col = 0
            else:
                col += 1
        i += step
    return comments


def check(path):
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    problems = []
    for _start_line, _start_col, end_line, end_col in find_block_comments(text):
        after = lines[end_line - 1][end_col + 2 :]
        if after.strip() == "":
            problems.append((end_line, "use // — a /* */ comment is allowed only inline, with code after it"))
    return problems


def main():
    rc = 0
    for path in sys.argv[1:]:
        for line_no, msg in check(path):
            print("%s:%d: %s" % (path, line_no, msg))
            rc = 1
    if rc:
        print("comment-style check failed", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
