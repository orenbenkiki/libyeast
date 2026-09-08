#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
The refusals the write-time hooks give for a piece of prose.

A hook reads a tool call. A caller holding prose and no tool call asks here. `prose_answer` asks this of an agent's
rewrite. A sweep over the tree asks it of a fragment. Both then read prose the way a hook reads an edit.

This list serves both callers. A hook wired into a caller and left out of the other reads as checked from either side.

`NO_CALLABLE` names the hooks `refusals_for` cannot call. The value is the reason. A shell hook reads its payload off
stdin and exposes nothing to import. `check_hooks` holds this module to the roster `.claude/settings.json` registers.

**Usage:** `import checkers`, then `checkers.refusals_for(prose, path)`.
"""

import altitude
import ascii_only
import no_tree_numbers
import prose_rewrite
import prose_words
import short_comments

# A hook registered on an edit that `refusals_for` cannot call. The value is the reason.
NO_CALLABLE = {
    "question-rule.sh": "a shell hook. it reads an isinstance chain rather than prose.",
    "step-rules.sh": "a shell hook. it reads a step name rather than prose.",
    "tense-check.sh": "a shell hook. `prose_rewrite` refuses a tense word in prose already.",
    "unread_files.py": "a path rule. it reads the file name rather than prose.",
}

# The hooks `refusals_for` calls. `check_hooks` reads this beside the roster.
CALLED = (
    "prose_words.py",
    "prose_rewrite.py",
    "no_tree_numbers.py",
    "ascii_only.py",
    "short_comments.py",
    "altitude.py",
)


def refusals_for(prose: str, path: str, lines: str | None = None, is_stating_why: bool = False) -> list[str]:
    """
    The refusals the write-time hooks give for `prose` at `path`.

    `prose` says the text with the comment markers off. `prose_words`, `prose_rewrite` and `no_tree_numbers` read that
    form. A marker splits a sentence where no sentence ends, and the shape rules then report a fault the writer cannot
    see.

    `lines` says the same text as the file writes it. `ascii_only`, `short_comments` and `altitude` read that form.
    `short_comments` counts an indented run of comment lines, and it finds no run in dedented prose. A caller with no
    such text leaves `lines` out, and the prose serves for both.

    `is_stating_why` passes a text whose whole job is a reason. A proposal's `why` field states a reason.
    """
    written = prose if lines is None else lines
    found = [
        prose_words.refusal_for(prose, path),
        prose_rewrite.refusal_for(prose, path, is_stating_why),
        no_tree_numbers.refusal_for(prose, path),
        ascii_only.refusal_for(written, path),
        short_comments.refusal_for(written, path),
        altitude.refusal_for(written, path),
    ]
    return [one for one in found if one]
