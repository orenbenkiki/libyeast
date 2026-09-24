#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker `checkers` runs. It holds a nested function's docstring to `a-nested-docstring-is-a-note`.

`collect_fragments` folds the docstring of a nested function into the enclosing fragment. A reader who opens the nested
function sees nothing of that docstring. A docstring worth more than a note belongs to a function at the top level.

`check_conventions` holds the length. This checker and that gate read a source through the same function.
"""

import check_conventions


def long_ones_of(source: str) -> tuple[tuple[int, str, int], ...]:
    """
    The nested functions of `source` whose docstring runs long. A triple holds the line, the function and the length.

    `checkers` reads this off the file the edit would leave. A docstring's length is a reading of the file. Rewrapping
    the file moves that length.
    """
    return tuple(check_conventions.long_nested_docstrings(source))


def refusal_for(source: str, path: str, long_ones: tuple[tuple[int, str, int], ...] | None = None) -> str | None:
    """
    The refusal the length rule gives for the nested docstrings at `path`, or None.

    `long_ones` comes from `long_ones_of`. A caller that gives none has `long_ones_of` read `source`.
    """
    if not path.endswith(".py"):
        return None
    found = long_ones_of(source) if long_ones is None else long_ones
    if not found:
        return None
    said = "; ".join(f"`{function}` at line {line} runs {length} lines" for line, function, length in found)
    return (
        f"This edit writes a docstring of more than {check_conventions.MOST_COMMENT_LINES} lines on a nested function, "
        f"into {path}: {said}.\n\n"
        "A nested function is no fragment of its own. A reader who opens it sees nothing of that docstring. Cut the "
        "docstring to a note, or move the function to the top level of the module.\n\n"
        "Rule: a-nested-docstring-is-a-note."
    )
