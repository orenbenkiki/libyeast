#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
`checkers` runs this checker. The checker refuses a citation of Python or of private C in the prose of a public C
declaration. `public-c-cites-public-c` is the rule.

The prose of a public C declaration sits in a header under `include/`. `collect_fragments.resolved` turns a citation
into keys. A citation counts as public where a public C declaration is among its keys. `ys_token_source` reaches a
public typedef and a private struct, and a citation of `ys_token_source` passes.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import os

import collect_fragments


def refusal_for(text: str, path: str) -> str | None:
    """
    The refusal the rule gives for `text` at `path`, or None where the citations pass. This function reads a `.h` file
    sitting directly in a directory named `include`. Any other file passes. The probe that `tests/hooks.json` gives
    `check_hooks` sits in such a directory under a scratch directory.

    `checkers` runs this public function over the prose of a file. `refusals_for_edit` hands it the touched fragments
    and literals of a source.
    """
    if not (path.endswith(".h") and os.path.basename(os.path.dirname(os.path.normpath(path))) == "include"):
        return None
    held = collect_fragments.fragments() + collect_fragments.code_only()
    by_key = {one.key: one for one in held}
    resolves = collect_fragments.resolved(held)
    found = []
    for name in sorted(collect_fragments.cited_by(text, "C")):
        keys = resolves.get(name, ())
        if any(_is_public_c(by_key[key]) for key in keys):
            continue
        hidden = [key for key in keys if key.partition(":")[0] in ("C", "Python")]
        if hidden:
            found.append(f"  `{name}` reaches {', '.join(f'`{key}`' for key in hidden)}")
    if not found:
        return None
    return (
        "This edit cites code a caller of the library does not hold, in the prose of a public header:\n\n"
        + "\n".join(found)
        + "\n\nCite a public C declaration, or say what the code does without naming it.\n\n"
        "Rule: public-c-cites-public-c."
    )


def _is_public_c(one: collect_fragments.Fragment) -> bool:
    """Whether `one` is a C declaration `collect_fragments.is_public` calls public."""
    return one.key.startswith("C:") and not collect_fragments.is_a_file(one.key) and collect_fragments.is_public(one)
