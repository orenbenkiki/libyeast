#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. `.claude/settings.json` runs `prose_gate` on those tools, and `checkers._CHECKERS` runs
this hook. The hook holds a comment inside a body to `a-comment-inside-a-body-is-a-note`.

A long comment restates the code in prose, or defends a decision nobody questioned.

`checkers._measured` hands the hook the runs of the whole file the edit would leave. The hook also refuses a run the
file held before the edit. The hook looks for a run of comment lines that opens inside a function body. The hook skips a
run at module level or class level. A run below a field's trailing comment continues that comment. The hook counts the
lines of the trailing comment and the run below it together.

`check_conventions` is the authority and reads the syntax tree. This hook refuses a long run as the writer writes it.
"""

import check_conventions


def runs_of(source: str) -> tuple[tuple[str, int], ...]:
    """
    The long comment runs of `source`. A pair holds a run's opening line, stripped, and how many lines that run takes.

    `checkers` reads this off the file the edit would leave. A run's length is a reading of the file. Rewrapping the
    file moves that length.
    """
    lines = source.split("\n")
    return tuple((lines[first - 1].strip(), whole) for first, whole in check_conventions.long_comment_runs(source))


def refusal_for(prose: str, path: str, runs: tuple[tuple[str, int], ...] | None = None) -> str | None:
    """
    Return the refusal the length rule gives for the comment runs at `path`. Return None where `path` names no Python
    file, or where the length rule passes those runs.

    `runs` comes from `runs_of`. A caller that gives none has `runs_of` read `prose`.
    """
    if not path.endswith(".py"):
        return None
    found = runs_of(prose) if runs is None else runs
    if not found:
        return None
    said = "; ".join(f"{whole} lines at `{opens}`" for opens, whole in found)
    return (
        f"This edit leaves a comment of more than {check_conventions.MOST_COMMENT_LINES} lines inside a body, "
        f"in {path}: {said}.\n\n"
        "A comment inside a body is a note. A passage there is the code restated in prose, or a defence of a "
        "decision nobody has questioned. The largest one in this tree ran eight lines and came to one.\n\n"
        "CUT IT. Read the code it sits on and keep only what the code does not say. Where a reader would undo the "
        "decision without a word about it, that word is one sentence. Everything else goes.\n\n"
        "Where the block documents the field assigned under it, put it directly above that assignment.\n\n"
        "Rule: a-comment-inside-a-body-is-a-note."
    )
