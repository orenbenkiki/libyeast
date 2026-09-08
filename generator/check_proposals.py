# SPDX-License-Identifier: MIT
"""
Check that no proposal waits for a ruling.

A review proposes a convention. The author rules on the proposal. It goes into `.claude/conventions.md` where accepted
and into `.claude/rejected.md` where not. A proposal ruled on in conversation and written down in no file comes back the
next round. It comes back the round after that too.

The workflow appends what it proposed to `.claude/proposals-pending.md`. This refuses while that file holds anything.
Ruling a proposal empties it.

**Usage:** `python3 generator/check_proposals.py`.
"""

import os
import re

import gate

_PENDING = os.path.join(gate.TREE, ".claude", "proposals-pending.md")  # the file a review writes its proposals into.

# A proposal. A list item opening with the rule in bold. The item takes a number or a bullet. The checker is the bold
# rather than any wording around it. A pattern matching the whole line stops matching the day the file is laid out
# differently. A gate with no match left reports green.
_A_PROPOSAL = re.compile(r"^\s*(?:[0-9]+\.|[-*])\s+\*\*(.+?)(?:\*\*|$)", re.MULTILINE)

# Who raised the proposals under it.
_A_HEADING = re.compile(r"^#+\s+From\s+`([^`]+)`", re.MULTILINE)


def _pending() -> list[tuple[str, str]]:
    """The proposals written down and not yet ruled on, as `(the reviewer that raised it, the rule)`."""
    if not os.path.exists(_PENDING):
        return []
    with open(_PENDING, encoding="utf-8") as handle:
        said = handle.read()
    who = "nobody named"
    held = []
    for line in said.splitlines():
        heading = _A_HEADING.match(line)
        if heading:
            who = heading.group(1)
            continue
        proposal = _A_PROPOSAL.match(line)
        if proposal:
            held.append((who, proposal.group(1)))
    return held


def _check() -> None:
    """Report the proposals the pending file still holds. Nobody has ruled on those."""
    gate.report(
        [f"{_PENDING}: `{rule}` comes from `{who}`. no ruling file holds it." for who, rule in _pending()],
        "unruled proposal(s). Move a proposal into `.claude/conventions.md` or `.claude/rejected.md` with its reason, "
        "and take the proposal out of the pending file",
        "proposals: none is left unruled",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
