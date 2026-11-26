# SPDX-License-Identifier: MIT
"""
Check the prose already in the tree against the rules the write-time hooks apply to an edit.

`prose_gate` is a `PreToolUse` hook. The hook refuses text as the writer writes it. The hook reads a file only when a
writer edits that file. This module asks `checkers.refusals_for_edit` of a file of the tree.

The rules reaching the tree this way are `a-claim-writes-no-universal`, `a-sentence-stays-within-the-limits` and
`use-the-words-this-project-uses`. `no-em-dash-and-no-colon`, `a-count-is-answered-for` and
`a-comment-writes-no-why-clause` go with them. So do `a-sentence-opens-on-a-subject`, `text-says-what-is` and
`faulty-prose-is-rewritten-not-patched`. `an-invariant-docstring-states-its-claim` reaches an invariant's docstring.
`collect_fragments.prose_literals` reads the invariant's name string as data. `a-named-hedge-is-refused` and
`a-sentence-hangs-a-single-tail` reach the tree here too. `a-reference-parser-has-one-name`,
`a-run-of-pronouns-reaches-back-to-a-noun` and `no-discovery-narration` reach it as well.

`a-plan-step-is-not-named-by-a-numeral`, `a-so-clause-is-a-why` and `a-dotted-name-sits-in-a-code-span` reach the tree
here. `a-whose-clause-is-a-hung-tail`, `a-possessive-owns-a-noun` and `a-sentence-does-not-define-by-itself` reach it
too. `a-fragment-opens-on-its-own-referent` and `a-code-span-hides-no-passive` reach it as well.

`a-free-choice-word-is-a-universal` reaches the tree here. So do `a-purpose-clause-says-no-action`,
`a-pointer-names-its-referent` and `a-name-of-the-tree-sits-in-a-code-span`. `a-marked-up-word-is-read-as-a-word` and
`a-paragraph-opens-on-a-noun` reach it here too. So do `a-question-word-is-no-noun` and `a-sentence-states-no-plan`.

A fault a hook would have refused is a fault here. The gate also faults a `not-prose:` marker beside no prose literal.
The gate faults a fragment `settling_state.unsettled` names too. So does a fragment `settling_state.prose_queue` names.
The gate says whether the tree still holds such a fault.

`converge_prose` drops a fragment from the prose queue when it approves or gives up on that fragment.
`settling_state.given_up` puts a fragment the run gives up on in the unsettled queue.

`a-declared-exception-carries-its-reason` reaches a literal through the `not-prose:` marker. `collect_fragments` reads
the marker off the source.

Usage: `python3 generator/check_prose.py <directory>`. The directory holds the settling queues.
"""

import os
import pathlib
import sys

import gate
import settling_state

sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))

# pylint: disable=wrong-import-position
import checkers  # noqa: E402
import collect_fragments  # noqa: E402

_USAGE = "usage: check_prose.py <directory>"  # a bad call gets it.


def _faults() -> list[str]:
    """
    The faults in the files the tree tracks. A fault is a refusal the write-time hook would give, or a `not-prose:`
    marker that no prose literal uses. A fault says its file in front.

    `checkers.refusals_for_edit` reads a file of the tree as an edit that writes the file from nothing. The write-time
    hook reads an edit through the same function. That function hands a file outside `gate.prose_files` to the checkers
    that read any text.
    """
    prose = set(gate.prose_files())
    held = []
    for name in gate.tracked_files():
        path = pathlib.Path(gate.TREE, name)
        # `_faults` skips a tracked path missing from the working tree.
        if not path.exists():
            continue
        found = checkers.refusals_for_edit(collect_fragments.whole(str(path)), name)
        held += [f"{name} {said.splitlines()[0]}" for said in found]
        if path not in prose or collect_fragments.does_hold_hook_payloads(name):
            continue
        source = path.read_text(encoding="utf-8")
        language = collect_fragments.language_of(name)
        marked = collect_fragments.not_prose_lines(source)
        used: set[int] = set()
        for at, _literal in collect_fragments.prose_literals(source, language or ""):
            used.update(marker for marker in (at, at - 1) if marker in marked)
        held += [
            f"{name}:{unused_marker} states a `not-prose:` marker, and no literal there reads as prose"
            for unused_marker in sorted(set(marked) - used)
        ]
    return held


def _unsettled_faults(into: str) -> list[str]:
    """
    The fragments the settling loop set aside in the unsettled prose queue under `into`.

    A fragment whose prose kept moving past the round cap lands there. `converge_prose._given_up` puts it there. The
    loop leaves it out of the ledger and out of the work queue. A later run passes it by. Rewriting its prose by hand
    changes the digest. The loop then takes the new text up.

    A digest the tree dropped names no fragment. This reports nothing for it. The next run drops a dropped digest from
    the queue.
    """
    by_digest = {one.prose.sha: one for one in collect_fragments.fragments()}
    return [
        f"{by_digest[digest].key} sits unsettled. The settling loop gave up on it. Rewrite its prose. "
        f"{os.path.join(into, settling_state.REVIEW)} may hold a critic's draft a claims reader changed"
        for digest in settling_state.unsettled(into)
        if digest in by_digest
    ]


def _unexamined_faults(into: str) -> list[str]:
    """
    Answer a fault per fragment waiting in the prose queue under `into`. A queued digest that no current fragment holds
    yields no fault. A fault tells the reader to run `make examine-prose`.
    """
    by_digest = {one.prose.sha: one for one in collect_fragments.fragments()}
    return [
        f"{by_digest[digest].key} waits in the prose queue. Run `make examine-prose`"
        for digest in settling_state.prose_queue(into)
        if digest in by_digest
    ]


def main() -> None:
    """
    Report the faults in the prose of the tree. `_faults` gives the prose a hook would refuse, and a `not-prose:` marker
    that marks no prose literal. `_unsettled_faults` gives the fragments the settling loop gave up on.
    `_unexamined_faults` gives the fragments waiting in the prose queue. The command line names the directory holding
    the queues that `unsettled` and `prose_queue` read.
    """
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    gate.report(
        _faults() + _unsettled_faults(sys.argv[1]) + _unexamined_faults(sys.argv[1]),
        "fault(s) in the prose of the tree. Rewrite the sentence, or run `make examine-prose`",
        "prose: the tree says nothing a write-time hook would refuse. the queues are empty.",
    )


if __name__ == "__main__":
    main()
