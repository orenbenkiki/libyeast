#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker refuses a pointer at the plan document in a file that may not name it.
`no-prose-points-at-the-plan` is the rule.

The project deletes the plan document once its work lands. A pointer at that document then points at nothing.
`check_documents.plan_pointer_errors` decides which files may name the plan document. This checker reads the whole text
of a file. A backticked name in code reads as a pointer too.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import check_documents


def refusal_for(text: str, path: str) -> str | None:
    """
    The refusal the plan rule gives for `text` at `path`, or None where the text points at no plan.

    Public. `checkers.refusals_for_edit` runs this over the whole new text of a file.
    """
    found = check_documents.plan_pointer_errors(path, list(enumerate(text.split("\n"), start=1)))
    if not found:
        return None
    return (
        "This edit leaves a pointer at the plan document:\n\n"
        + "\n".join(f"  {one}" for one in found)
        + "\n\nThe project deletes that document once its work lands. Say what the tree holds, or what is owed, in "
        "the words of this file.\n\nRule: no-prose-points-at-the-plan."
    )
