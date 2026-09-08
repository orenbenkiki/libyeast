#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook checks a claim before the claim goes into prose.

Code gets a feedback loop as the author types. Prose gets none until somebody reads it. This hook is that loop. This
hook cannot tell whether a claim is true. It stops a claim going in unexamined.

This hook names the word it refuses. `prose_rewrite` names nothing. A word fault is a claim that may be false, and a
grep settles it. A shape fault goes away when I say the sentence again.

`a-count-is-answered-for` refuses a vague quantifier. `every-claim-is-checked-before-it-is-written` refuses a universal
and a superlative. `use-the-words-this-project-uses` refuses a word the project turned down.

`prose_rules.words_found` holds the patterns. `check_prose` runs the same checker over the tree.
`collect_fragments.fragments_touched` gives the fragments the edit writes prose into.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import sys

import collect_fragments
import prose_rules
import refusal

# The refusal for a vague quantifier.
_VAGUE = (
    "This edit writes a vague quantifier into {path}: {said}. It is unfalsifiable by construction. It neither "
    'states a count nor says which ones. Say WHICH ONES instead: not "falls to a fraction of what it read" but '
    '"takes every ungated way that begins with a set, leaving the ways that begin with a call". Where no '
    "characterisation can be found, the sentence was reporting a measurement and belongs to whatever measures it. "
    "Rule: a-count-is-answered-for."
)

# The refusal for a universal or a superlative.
_UNIVERSAL = (
    "This edit writes a universal into {path}: {said}. Each is refuted by one counter-example. Go and find it "
    "before this stands: grep the tree, read the callee, expand the glob, check the signature. Then say out loud "
    "what you looked at and what it said, and write the edit again. Keep the word where the tree bears it out, and "
    "say WHICH ONES where it does not. Intent is free and state costs a grep, and what gets written is the state. "
    "Rule: every-claim-is-checked-before-it-is-written."
)

# The refusal for a word the project turned down.
_TURNED_DOWN = (
    "This edit writes a word this project turned down, into {path}: {said}. The ban is a ruling, and it is written "
    "beside the pattern in `prose_rules`. Read the reason there and use the word it names instead. "
    "Rule: use-the-words-this-project-uses."
)


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the word rules give for `prose` at `path`, or None where the words pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.
    """
    found = prose_rules.words_found(prose)
    universal = sorted(set(found["universal"]) | set(found["superlative"]))
    for said, wording in (
        (found["vague quantifier"], _VAGUE),
        (universal, _UNIVERSAL),
        (found["word we do not use"], _TURNED_DOWN),
    ):
        if said:
            return wording.format(path=path, said="; ".join(said))
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.edited(payload.get("tool_input", {}))
    if edit is None:
        return
    said = [one.prose.content for one in collect_fragments.fragments_touched(edit)]
    said += [literal for _at, literal in collect_fragments.literals_touched(edit)]
    found = refusal_for("\n".join(said), edit.path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
