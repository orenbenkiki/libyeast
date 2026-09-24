#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a number that depends on the tree as the writer writes it.
`a-count-is-answered-for` is the rule.

The gate over the tree refuses the same number.

`check_documents.numbers_in_prose` says which numerals a text holds. A document goes through whole, the way the gate
reads one. A count in a heading counts. A file of another kind hands over the prose beside its code. That prose comes a
fragment at a time, and `collect_fragments.fragments_touched` gives those fragments. This hook imports both rather than
copying them. `check_documents._NOT_A_NUMBER_OF_THE_TREE` excuses a numeral. A numeral that list leaves out is a fault.
That list holds numerals that a source outside this tree fixes. A version, a codepoint and a width stay writable. So
does a column.

This hook has no import guard. A silent checker and a clean edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import check_documents


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
