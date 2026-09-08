#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a number that depends on the tree as the writer writes it.

The gate refuses the same number minutes later. Refusing it here keeps the number out of the file. A number kept out of
the file cannot go stale in it.

`check_documents.numbers_in_prose` says which numerals a text holds. A document goes through whole, the way the gate
reads one. A count in a heading counts. Another file hands over the prose beside its code, a fragment at a time, and
`collect_fragments.fragments_touched` gives those fragments. This hook imports both rather than copying them.
`check_documents._NOT_A_NUMBER_OF_THE_TREE` excuses a numeral, and whatever survives is a fault. Something outside this
tree fixes what that list holds. A version, a codepoint and a width stay writable. So does a column.

A copy of the prose checker went stale here. The copy left an ordered list's marker in place. The copy then refused the
`1.` opening a plan item as a count.

There is no import guard. A checker that cannot check must fail loudly. A silent checker and a clean edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import sys

import check_documents
import collect_fragments
import refusal


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the number rule gives for `prose` at `path`, or None where the numbers pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.
    """
    found = set(check_documents.numbers_in_prose(prose))
    if not found:
        return None
    said = "; ".join(sorted(found))
    return (
        f"This edit writes a number into {path}: {said}.\n\n"
        "A number that depends on the tree is refused in a document and in a comment alike. It is right "
        "when written and wrong the next time the tree moves, and nobody re-measures it.\n\n"
        "TAKE THE NUMBER OUT. Do not write the same number as a word. Do not swap it for a vague "
        "quantifier either, since that is the same fault with the evidence removed. Say WHICH ONES, or "
        "say nothing and let the reader run the gate.\n\n"
        "`make pc` prints the live figures. That is where a number belongs.\n\n"
        "If this number is fixed by something outside this tree, a spec version, a codepoint, a width, a "
        "column or an RFC, then add it to `check_documents._NOT_A_NUMBER_OF_THE_TREE` and say why.\n\n"
        "Rule: a-count-is-answered-for."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.edited(payload.get("tool_input", {}))
    if edit is None:
        return
    if edit.path.endswith(".md"):
        said = [edit.now]
    else:
        said = [one.prose.content for one in collect_fragments.fragments_touched(edit)]
        said += [literal for _at, literal in collect_fragments.literals_touched(edit)]
    found = refusal_for("\n".join(said), edit.path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
