# SPDX-License-Identifier: MIT
"""
Check the prose already in the tree against the rules the write-time hooks apply to an edit.

`prose_gate` is a `PreToolUse` hook. The hook refuses text as the writer writes it. The hook reads no line the tree
already holds. This module asks `checkers.refusals_for_edit` of a file of the tree.

The rules reaching the tree this way are `a-claim-writes-no-universal`, `a-sentence-stays-within-the-limits` and
`use-the-words-this-project-uses`. `no-em-dash-and-no-colon`, `a-count-is-answered-for` and
`a-comment-writes-no-why-clause` go with them. So do `a-sentence-opens-on-a-subject`, `text-says-what-is` and
`faulty-prose-is-re-said-not-patched`. `an-invariant-docstring-states-its-claim` reaches an invariant's docstring. That
checker skips the invariant's name string. `a-named-hedge-is-refused` and `a-sentence-hangs-a-single-tail` reach the
tree here too. `a-reference-parser-has-one-name`, `a-run-of-pronouns-reaches-back-to-a-noun` and
`no-discovery-narration` reach it as well.

`a-plan-step-is-not-named-by-a-numeral`, `a-so-clause-is-a-why` and `a-dotted-name-sits-in-a-code-span` reach the tree
here. `a-whose-clause-is-a-hung-tail`, `a-possessive-owns-a-noun` and `a-sentence-does-not-define-by-itself` reach it
too. `a-fragment-opens-on-its-own-referent` and `a-code-span-hides-no-passive` reach it as well.

`a-free-choice-word-is-a-universal` reaches the tree here. So do `a-purpose-clause-says-no-action`,
`a-pointer-names-its-referent` and `a-name-of-the-tree-sits-in-a-code-span`. `a-marked-up-word-is-read-as-a-word` and
`a-paragraph-opens-on-a-noun` reach it here too. So do `a-question-word-is-no-noun` and `a-sentence-states-no-plan`.

A fault here is a fault a hook would have refused. The gate says whether the tree still holds any.

A faulty sentence gets said again. A checker that refuses a sentence it should pass gets fixed. This gate offers no
third way out.

`a-declared-exception-carries-its-reason` reaches a literal through the `not-prose:` marker. A record layout or a
fixture line takes that marker. `collect_fragments` reads the marker off the source.

Usage: `python3 generator/check_prose.py`.
"""

import os
import sys

import gate
import settling_state

sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))

# pylint: disable=wrong-import-position
import checkers  # noqa: E402
import collect_fragments  # noqa: E402

# The directory the settling loop keeps its queues in. The `PROSE_DIR` variable of the `Makefile` names the same one.
_PROSE_QUEUES = os.path.join(gate.TREE, ".git", "critic")


def _faults() -> list[str]:
    """
    The faults a hook would refuse in the prose the tree holds. A fault says its file in front.

    `checkers.refusals_for_edit` reads a file of the tree as an edit that writes the file from nothing. The write-time
    hook reads an edit through the same function.
    """
    held = []
    for path in gate.prose_files():
        name, source = str(path.relative_to(gate.TREE)), path.read_text(encoding="utf-8")
        found = checkers.refusals_for_edit(collect_fragments.whole(str(path)), name)
        held += [f"{name} {said.splitlines()[0]}" for said in found]
        if collect_fragments.does_hold_hook_payloads(name):
            continue
        language = collect_fragments.language_of(name)
        marked = collect_fragments.not_prose_lines(source)
        used: set[int] = set()
        for at, _literal in collect_fragments.prose_literals(source, language or ""):
            used.update(marker for marker in (at, at - 1) if marker in marked)
        held += [
            f"{name}:{at} states a `not-prose:` marker, and no literal there reads as prose"
            for at in sorted(set(marked) - used)
        ]
    return held


def _unsettled_faults() -> list[str]:
    """
    The fragments the settling loop set aside.

    A fragment whose prose kept moving past the round cap lands there. The loop leaves it out of the ledger and out of
    the work queue. A later run passes it by. Saying its prose again by hand changes the digest. The loop then takes the
    new text up.

    A digest the tree dropped names no fragment. This reports nothing for it. The next run drops a dropped digest from
    the queue.
    """
    by_digest = {one.prose.sha: one for one in collect_fragments.fragments()}
    return [
        f"{by_digest[digest].key} sits unsettled. The settling loop gave up on it. Say its prose again"
        for digest in settling_state.unsettled(_PROSE_QUEUES)
        if digest in by_digest
    ]


def main() -> None:
    """Report the prose a hook would refuse, and the fragments the settling loop gave up on."""
    gate.report(
        _faults() + _unsettled_faults(),
        "fault(s) in the prose of the tree. Say the sentence again",
        "prose: the tree says nothing a write-time hook would refuse. nothing sits unsettled.",
    )


if __name__ == "__main__":
    main()
