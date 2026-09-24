#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. The hook holds an edited path to `a-file-of-the-tree-has-a-reader`.

A hook asks `collect_fragments` for the fragments an edit leaves. `collect_fragments` yields no fragment for a file in a
language no reader knows. The hook then passes in silence. The prose in that file then lands unread.

`collect_fragments._unnamed_prose` asks whether a tracked file has a reader. This hook asks that question of the edited
path. The edited file need not exist.

The hook skips a file outside the tree and a path `git` ignores.
"""

import json
import os
import subprocess
import sys

import check_documents
import collect_fragments
import gate
import refusal


def _is_ignored(path: str) -> bool:
    """
    Whether `git` ignores `path`.
    """
    answered = subprocess.run(
        ["git", "-C", gate.TREE, "check-ignore", "-q", path], capture_output=True, text=True, check=False
    )
    return answered.returncode == 0


def _refusal_for(path: str) -> str | None:
    """
    The refusal `a-file-of-the-tree-has-a-reader` gives for `path`, or None where a reader knows the language.

    `gate.NOT_OURS` names the data of this tree. `gate.UNREAD` names the file the checkers leave alone.
    """
    if not check_documents.is_prose_of_the_project(path) or collect_fragments.language_of(path) is not None:
        return None
    return (
        f"This edit writes {path}. The readers here know no such language, and the prose in that file would land "
        f"unread.\n\n"
        "Give `collect_fragments._LANGUAGES` an entry for the language, and `collect_fragments._MARKERS` the marker "
        "its comments use. A file holding no prose of ours goes in `gate.UNREAD` instead. The comment above that list "
        "says why.\n\n"
        "Rule: a-file-of-the-tree-has-a-reader."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    named = payload.get("tool_input", {}).get("file_path")
    if not isinstance(named, str) or not gate.is_in_the_tree(named):
        return
    path = os.path.relpath(os.path.abspath(named), gate.TREE)
    if _is_ignored(path):
        return
    found = _refusal_for(path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
