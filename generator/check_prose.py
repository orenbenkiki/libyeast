# SPDX-License-Identifier: MIT
"""
Check the prose already in the tree against the rules the write-time hooks apply to an edit.

`prose_words` and `prose_shape` are `PreToolUse` hooks. A hook refuses text as the writer writes it. A hook reads no
line the tree already holds. This module asks `prose_rules` of the fragments `collect_fragments` enumerates.

The rules reaching the tree this way are `every-claim-is-checked-before-it-is-written`, `short-plain-sentences` and
`use-the-words-this-project-uses`. `no-em-dash-and-no-colon`, `a-count-is-answered-for` and `a-comment-describes-what`
go with them. So do `a-sentence-names-its-actor`, `text-says-what-is` and `faulty-prose-is-re-said-not-patched`.
`an-invariant-docstring-states-its-claim` reaches an invariant's docstring. That checker skips the invariant's name
string.

A fault here is a fault a hook would have refused. The gate says whether the tree still holds any.

A faulty sentence gets said again. A checker that refuses a sentence it should pass gets fixed. This gate offers no
third way out.

`a-declared-exception-carries-its-reason` reaches a literal through the `not-prose:` marker. A record layout or a
fixture line takes that marker. `collect_fragments` reads the marker off the source.

Usage: `python3 generator/check_prose.py`.
"""

import collect_fragments
import gate
import prose_rules


def _faults() -> list[str]:
    """
    The faults a hook would refuse in the prose the tree holds. A fault says its place in front.

    The word rules cover a fragment of the project. The shape rules skip a file outside `is_about_the_tree`, and the
    hooks skip the same file. A string literal that reads as prose gets the word rules and the shape rules both.
    """
    held = []
    for one in collect_fragments.fragments():
        found = prose_rules.word_faults(one.prose.content)
        if collect_fragments.is_about_the_tree(one.path):
            found += prose_rules.shape_faults(one.prose.content) + prose_rules.fragment_faults(one.prose.content)
        held += [f"{one.path}:{one.first} {said}" for said in found]
    for path in gate.prose_files():
        name, source = str(path.relative_to(gate.TREE)), path.read_text(encoding="utf-8")
        if collect_fragments.does_hold_hook_payloads(name):
            continue
        language = collect_fragments.language_of(name)
        marked, used = collect_fragments.not_prose_lines(source), set()
        for at, literal in collect_fragments.prose_literals(source, language or ""):
            markers = [marker for marker in (at, at - 1) if marker in marked]
            if markers:
                used.update(markers)
                continue
            held += [f"{name}:{at} {said}" for said in prose_rules.literal_faults(literal)]
        held += [
            f"{name}:{at} states a `not-prose:` marker, and no literal there reads as prose"
            for at in sorted(set(marked) - used)
        ]
    return held


def main() -> None:
    """Report the fragments holding prose a hook would refuse."""
    gate.report(
        _faults(),
        "fault(s) in the prose of the tree. Say the sentence again",
        "prose: the tree says nothing a write-time hook would refuse",
    )


if __name__ == "__main__":
    main()
