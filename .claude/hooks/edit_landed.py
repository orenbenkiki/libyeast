#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PostToolUse on Edit and Write. This hook compares the file an edit left with the text `prose_gate` read the edit as.

`prose_gate` and the checkers read an edit as `collect_fragments.written` gives it. That text predicts what the tool
writes. `prose_gate` records the prediction under the call's `tool_use_id` after the edit passes. This hook reads the
file the tool wrote. The hook answers where the file and the prediction differ. The checkers then passed text the tree
does not hold.

An edit of a file in the tree with no prediction is a fault too. `prose_gate` then read no edit under this call.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import sys

import gate
import refusal

# The directory holding a prediction per tool call. Git tracks none of it.
_PREDICTIONS = os.path.join(gate.TREE, ".git", "edit-predictions")

# The line that takes the place of a line past the end of a file.
_THE_END = "<the end of the file>"


def predicted_at(tool_use_id: str) -> str:
    """
    The file holding the prediction for the tool call `tool_use_id`.

    Public. `prose_gate` writes the prediction there. `check_hooks` writes there the `predicted` text that a side of a
    pair holds.
    """
    return os.path.join(_PREDICTIONS, tool_use_id)


def _first_difference(landed: str, predicted: str) -> str:
    """
    The first line on which `landed` and `predicted` differ. The answer quotes that line from both texts. A text that
    runs out first gives `_THE_END` in place of a line.
    """
    pairs = zip([*landed.split("\n"), _THE_END], [*predicted.split("\n"), _THE_END])
    held, said = next((one, other) for one, other in pairs if one != other)
    return f"  landed:    {held!r}\n  predicted: {said!r}"


def main() -> None:
    payload = json.load(sys.stdin)
    path = payload.get("tool_input", {}).get("file_path")
    if not isinstance(path, str) or not gate.is_in_the_tree(path):
        return
    called = str(payload.get("tool_use_id") or "")
    held = predicted_at(called)
    if not called or not os.path.exists(held):
        refusal.refuse(
            f"The edit of {path} landed with no prediction. `prose_gate` read no edit under this call, and the hooks "
            "checked none of the file that landed. Read the file, and check the edit by hand."
        )
    with open(held, encoding="utf-8") as handle:
        predicted = handle.read()
    os.remove(held)
    with open(path, encoding="utf-8") as handle:
        landed = handle.read()
    if landed != predicted:
        refusal.refuse(
            f"The edit of {path} landed as text the hooks did not read. `collect_fragments.written` predicted other "
            f"text. The first difference follows.\n\n{_first_difference(landed, predicted)}\n\nFix the file. Then "
            "make `collect_fragments.written` read such an edit the way the tool writes it."
        )


if __name__ == "__main__":
    main()
