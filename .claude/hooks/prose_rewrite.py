#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a sentence shaped past what a single checker takes in. Say the fragment
again.

`faulty-prose-is-re-said-not-patched` and `text-says-what-is` are the rules. `prose_shape` was here before this hook.
`prose_shape` named the shape it refused and quoted the sentence. A refusal like that reads as a to-do list. I kept the
word order and swapped words around it until the pattern stopped matching. The word order holds the fault. The fault
survived.

So this refusal names no pattern. A pattern hunt gets nothing here. The refusal quotes the refused sentence, and the
rule behind it stays unnamed. A caller of this hook reads that quote. The write-time path reads it too.

The refusal states the writing guidelines instead. A guideline reaches me as I write. A guideline in a document loses to
the docstring under my cursor.

The shape rules come here and the word rules go to `prose_words`. `short-plain-sentences` and `no-em-dash-and-no-colon`
are what this asks. A word rule refuses a claim, and the fix is a grep or a deletion. A reader has to know which word
did that. A shape rule refuses a sentence, and the fix is saying it again.

`prose_rules` judges the prose the edit leaves behind. The prose the edit replaces goes unread. The author refused a
rule comparing new prose against old prose. `.claude/rejected.md` holds that ruling under the word-run entry.

A reader opens a fragment through a hover or a symbol index. The fragment is therefore the unit named and the unit asked
for. Saying a long fragment again shortens it. A string literal comes here too, under the place it appears at.
`prose_rules.literal_shape_faults` judges a literal, and `check_prose` asks that same question of the tree.

`check_prose` prints the faults in full. Diagnosis belongs in a gate I run on purpose.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import sys

import collect_fragments
import prose_rules
import refusal

# The refusal this hook prints.
_SAID = """The prose this edit leaves in {where} is shaped past what one pass reads. It is refused.

Say the whole prose of that fragment again. Delete it, then write it from what the code does and from the claims the old
text made. Keep the claims, including the ones the code cannot show you. Do not repair the sentences you have.

You are told nothing about what the fault is. Hunting a pattern is what produced the fault.

Write it the way you would say it out loud to somebody at the next desk:

  Name the actor. Put the subject first and the verb right after it.
  One thing per sentence. A sentence with two ideas in it is two sentences.
  Say what the thing IS.
  Where a sentence resists, split it. Do not tighten it.
  It comes out shorter than what it replaced. A rewrite that grows was an edit.

These five are the moves that got this text refused before. Each one reads clean and says nothing.

  Name the real actor or delete the sentence. Never hand the verb to a subject that cannot perform it to
  get past the passive rule. A slice does not read. A rule does not fault. A module does. Where nothing
  in the codebase performs the action, the sentence states a property, so state the property.

  Never write "is what" or "are what". A copula joined to a free relative names nobody and says nothing.
  Not "the fragments are what the edit leaves" but "fragments_touched returns the fragments an edit writes into".

  A noun taking "the" or "those" has appeared already. "the digests", "those extents" and "the half" each
  pointed at something no earlier sentence had said.

  Say a claim once. A second sentence that swaps one noun and appends "too" is the same claim.

  One pronoun to a sentence, and its referent is the subject of that same sentence. Naming the noun is
  shorter than pointing at it.

Then run `make verify-prose` and see what it says.

Rule: faulty-prose-is-re-said-not-patched."""

# A writer holding no checker reads this under the refusal. The sentences come below it.
_QUOTED = """

The sentences below are the refused sentences. Say them again. This refusal names the sentence and nothing besides. A
pattern hunt gets nothing here.
"""


def _is_faulty(prose: str, is_stating_why: bool = False) -> bool:
    """Whether the shape rules refuse `prose`, as a sentence or as a whole fragment."""
    return bool(prose_rules.shape_faults(prose, is_stating_why) + prose_rules.fragment_faults(prose))


def _refusal(where: list[str], quoted: list[str]) -> str:
    """The refusal naming the places `where` and quoting the sentences `quoted`."""
    said = _SAID.format(where=", ".join(where))
    return said + _QUOTED + "".join(f"\n  {one}" for one in dict.fromkeys(quoted))


def refusal_for(prose: str, where: str, is_stating_why: bool = False) -> str | None:
    """
    The refusal the shape rules give for `prose` at `where`, or None where the shape passes.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal. A proposal's
    `why` field states a reason, and `is_stating_why` takes the WHY clause off it.
    """
    if not _is_faulty(prose, is_stating_why):
        return None
    return _refusal([where], prose_rules.refused_sentences(prose, is_stating_why))


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.edited(payload.get("tool_input", {}))
    if edit is None:
        return
    if not collect_fragments.is_about_the_tree(edit.path):
        return
    where, quoted = [], []
    for one in collect_fragments.fragments_touched(edit):
        if _is_faulty(one.prose.content):
            where.append(f"{one.key} at {one.path}:{one.first}")
            quoted += prose_rules.refused_sentences(one.prose.content)
    for at, literal in collect_fragments.literals_touched(edit):
        held = prose_rules.literal_shape_faults(literal)
        if held:
            where.append(f"the literal at {edit.path}:{at}")
            quoted += [said.partition("] ")[2] for said in held]
    if not where:
        return
    refusal.refuse(_refusal(where, quoted))


if __name__ == "__main__":
    main()
