#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker refuses a copy in `DESIGN.md` of the prose beside a cited name.
`a-design-paragraph-copies-no-cited-prose` is the rule.

`.claude/conventions.md` gives `DESIGN.md` context, perspective and architecture. The prose beside a name holds the
detail of that name. A copy of that prose goes quietly false once the code moves.

`check_documents.copied_prose_errors` compares the words. `copies_of` hands that function the prose of the fragments
outside `DESIGN.md`. `_cited_prose` reads that prose off the tree on a call. A long-running caller that writes a file
then reads the new prose of that file.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import os

import check_documents
import collect_fragments


def _cited_prose() -> dict[str, list[tuple[str, str]]]:
    """
    A map from a name of the tree to the key and the prose of the fragments outside `DESIGN.md` that the name reaches.
    """
    held = [one for one in collect_fragments.fragments() if one.path != "DESIGN.md"]
    by_key = {one.key: one for one in held}
    return {
        name: [(key, by_key[key].prose.content) for key in sorted(keys)]
        for name, keys in collect_fragments.keys_by_name(held).items()
    }


def copies_of(source: str, path: str) -> tuple[str, ...]:
    """
    The copies `source` makes of the prose beside a name it cites. A path other than `DESIGN.md` makes none.

    Public. `checkers` fills its readings of a file through here.
    """
    if os.path.basename(path) != "DESIGN.md":
        return ()
    return tuple(check_documents.copied_prose_errors(collect_fragments.flattened(source), _cited_prose()))


def refusal_for(prose: str, path: str, copied: tuple[str, ...] | None = None) -> str | None:
    """
    The refusal the copy rule gives for the copies at `path`, or None where the text copies nothing.

    `copied` comes from `copies_of`. A caller that gives none has `copies_of` read `prose`.
    """
    found = copies_of(prose, path) if copied is None else copied
    if not found:
        return None
    return (
        f"This edit leaves {path} holding a copy of the prose of a name it cites:\n\n"
        + "\n".join(f"  {one}" for one in found)
        + "\n\nThe prose beside the name holds that detail. TAKE THE COPY OUT, and keep the citation.\n\n"
        "Rule: a-design-paragraph-copies-no-cited-prose."
    )
