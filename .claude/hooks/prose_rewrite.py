#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a sentence with a faulty shape. The refusal asks the writer to say the
fragment again.

This hook enforces `faulty-prose-is-re-said-not-patched` and `text-says-what-is`. `a-sentence-hangs-a-single-tail` and
`a-run-of-pronouns-reaches-back-to-a-noun` join them. `a-paragraph-opens-on-a-noun`, `a-sentence-opens-on-a-subject` and
`a-comment-writes-no-why-clause` join them too.

`a-whose-clause-is-a-hung-tail`, `a-possessive-owns-a-noun` and `a-sentence-does-not-define-by-itself` join them as
well. `a-fragment-opens-on-its-own-referent` and `a-code-span-hides-no-passive` join them.

This refusal names no pattern. The refusal quotes the refused sentence. A caller of this hook reads that quote.

The refusal states the writing guidelines instead. A guideline reaches the writer at the keyboard.

This hook takes the shape rules. `prose_words` takes the word rules. This hook enforces
`a-sentence-stays-within-the-limits` and `no-em-dash-and-no-colon`. A word rule refuses a claim, and the fix is a grep
or a deletion. A reader has to know which word the rule refused. A shape rule refuses a sentence, and the fix is saying
it again.

`prose_rules` judges the prose the edit leaves behind. `prose_rules` reads none of the prose the edit replaces. The
author refused a rule comparing new prose against old prose. `.claude/rejected.md` holds that ruling under the word-run
entry.

A reader opens a fragment through a hover or a symbol index. A refusal therefore names a fragment and asks for that
fragment again.  This hook judges a string literal too. The refusal names the place the literal appears.
`prose_rules.literal_shape_faults` judges a literal. `check_prose` judges the literals of the tree through that
function.

`check_prose` prints the faults in full.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import collect_fragments
import prose_rules

# The refusal this hook prints.
_SAID = """The prose this edit leaves in {where} is shaped past what one pass reads. It is refused.

Say the whole prose of that fragment again. Delete it, then write it from what the code does and from the claims the old
text made. Keep the claims, including the ones the code cannot show you. Do not repair the sentences you have.

You are told nothing of the fault itself. Hunting a pattern is what produced the fault.

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

# A writer without a checker reads this line under the refusal. The quoted sentences follow this line.
_QUOTED = """

The sentences below are the refused sentences. Say them again. This refusal names the sentence and nothing besides. A
pattern hunt gets nothing here.
"""


def _is_faulty(prose: str, is_stating_why: bool = False) -> bool:
    """Whether the shape rules refuse `prose`, as a sentence or as a whole fragment."""
    return bool(prose_rules.prose_faults(prose, is_stating_why))


def _refusal(where: list[str], quoted: list[str]) -> str:
    """The refusal naming the places `where` and quoting the sentences `quoted`."""
    said = _SAID.format(where=", ".join(where))
    return said + _QUOTED + "".join(f"\n  {one}" for one in dict.fromkeys(quoted))


def refusal_for(prose: str, where: str, is_stating_why: bool = False, is_literal: bool = False) -> str | None:
    """
    The refusal the shape rules give for `prose` at `where`, or None where the shape passes.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal. A proposal's
    `why` field states a reason, and `is_stating_why` takes the WHY clause off that field.

    `is_literal` marks a string literal. `literal_shape_faults` holds the shape rules a literal takes.
    """
    if not collect_fragments.is_about_the_tree(where):
        return None
    if is_literal:
        held = prose_rules.literal_shape_faults(prose)
        if not held:
            return None
        return _refusal([f"the literal at {where}"], [said.partition("] ")[2] for said in held])
    if not _is_faulty(prose, is_stating_why):
        return None
    return _refusal([where], prose_rules.refused_sentences(prose, is_stating_why))
