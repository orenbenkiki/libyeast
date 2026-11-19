#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker holds a written path to `a-file-of-the-tree-has-a-reader`.

A checker asks `collect_fragments.fragments_touched` for the fragments whose prose an edit changes. `collect_fragments`
yields no fragment for a file in a language no reader knows. The prose checkers then pass in silence, and the prose in
that file lands unread. This checker asks whether a reader knows the language of the path. The written file need not
exist.

The checker skips a path `git` ignores.
"""

import os
import subprocess

import check_documents
import collect_fragments
import gate


def _is_ignored(path: str) -> bool:
    """
    Whether `git` ignores `path`. This answers yes for a path under `.git`.
    """
    if os.path.relpath(os.path.join(gate.TREE, path), gate.TREE).split(os.sep)[0] == ".git":
        return True
    answered = subprocess.run(
        ["git", "-C", gate.TREE, "check-ignore", "-q", path], capture_output=True, text=True, check=False
    )
    return answered.returncode == 0


def _refusal_for(path: str) -> str | None:
    """
    The refusal `a-file-of-the-tree-has-a-reader` gives for `path`, or None where `path` holds no prose of the project
    or where a reader knows its language.

    `gate.NOT_OURS` names the paths in this tree holding no prose of the project. `gate.UNREAD` names the base names of
    the files the checkers leave alone.
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


def refusal_for(_text: str, path: str) -> str | None:
    """
    The refusal `a-file-of-the-tree-has-a-reader` gives for `path`, or None where the path holds no prose of the project
    or a reader knows the language. A path `git` ignores takes no refusal.

    Public. `checkers` runs this over a file an edit writes.
    """
    return None if _is_ignored(path) else _refusal_for(path)
