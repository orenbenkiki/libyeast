#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook runs the prose checkers over an edit. The hook refuses the edit where a checker
refuses it. `hook_health` reads the edit first, and the hook refuses an edit that `hook_health` refuses.

`checkers._CHECKERS` names the checkers. This hook holds no list of its own. A checker added to `_CHECKERS` reaches an
edit through this hook.

`checkers.refusals_for_edit` decides which text goes to which checker.

The hook records the text it read under the call's `tool_use_id` after the edit passes. `edit_landed` compares the file
the tool writes against that text.

This hook imports without a guard. A checker that cannot check must fail loudly. A silent checker and a clean edit read
the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import sys

import checkers
import collect_fragments
import edit_landed
import gate
import hook_health
import refusal


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.written(payload.get("tool_input", {}))
    if edit is None or not gate.is_in_the_tree(edit.path):
        return
    path = os.path.relpath(os.path.abspath(edit.path), gate.TREE)
    broken = hook_health.refusal_for(edit.now, path)
    if broken:
        refusal.refuse(broken)
    found = checkers.refusals_for_edit(edit, path)
    if found:
        refusal.refuse("\n\n".join(found))
    called = payload.get("tool_use_id")
    if called:
        predicted = edit_landed.predicted_at(str(called))
        os.makedirs(os.path.dirname(predicted), exist_ok=True)
        with open(predicted, "w", encoding="utf-8") as handle:
            handle.write(edit.now)


if __name__ == "__main__":
    main()
