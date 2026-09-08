# SPDX-License-Identifier: MIT
"""
Report the fragments the write-time hooks would refuse.

`collect_fragments` breaks the tree into fragments. A fragment records a site per comment block, and a site names the
lines it occupies. This reads those lines out of the file and hands them to `checkers.refusals_for`. A hook then reads
the same text here that it reads in an edit.

The text holds the comment markers and the indent. A hook wants that form. `short_comments` counts an indented run of
comment lines, and dedented prose hides the run from it.

A fragment with no refusal goes unreported. `apply_prose` writes a rewrite of a reported fragment back into the tree.

**Usage:** `python3 generator/faulty_fragments.py`. The report goes to standard output as JSON.
"""

import importlib
import json
import os
import sys

from typing import Any

import collect_fragments
import gate


def _lines_of(site: Any) -> str:
    """The text of a site, as the file writes it. The markers and the indent come along."""
    with open(os.path.join(gate.TREE, site.path), encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    return "\n".join(lines[site.lines[0] - 1 : site.lines[-1]])


def _faulty() -> list[dict[str, Any]]:
    """The fragments a hook would refuse, with the refusals and the prose of a site apiece."""
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    checkers = importlib.import_module("checkers")
    held = []
    for one in collect_fragments.fragments():
        faults = []
        for site in one.sites:
            faults += checkers.refusals_for(site.prose, site.path, _lines_of(site))
        if faults:
            held.append(
                {
                    "key": one.key,
                    "path": one.path,
                    "sites": [f"{site.path}:{site.lines[0]}" for site in one.sites],
                    "parts": [site.prose for site in one.sites],
                    "faults": faults,
                }
            )
    return held


def main() -> None:
    """Print the faulty fragments as JSON, and say on standard error how many came back."""
    held = _faulty()
    json.dump(held, sys.stdout, indent=2)
    print(f"{len(held)} fragment(s) a hook would refuse", file=sys.stderr)


if __name__ == "__main__":
    main()
