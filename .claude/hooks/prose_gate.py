#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook runs the prose checkers over an edit. The hook refuses the edit where a checker
refuses it.

`checkers.CHECKERS` names the checkers. This hook holds no list of its own. A checker added to `CHECKERS` reaches an
edit through this hook.

`checkers.refusals_for_edit` decides which text goes to which checker.

This hook imports without a guard. A checker that cannot check must fail loudly. A silent checker and a clean edit read
the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import sys

import checkers
import collect_fragments
import gate
import refusal


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.written(payload.get("tool_input", {}))
    if edit is None or not gate.is_in_the_tree(edit.path):
        return
    found = checkers.refusals_for_edit(edit, os.path.relpath(os.path.abspath(edit.path), gate.TREE))
    if found:
        refusal.refuse("\n\n".join(found))


if __name__ == "__main__":
    main()
