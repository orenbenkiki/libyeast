#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
`prose_gate` runs this hook through `checkers._CHECKERS` on PreToolUse for Edit and Write. This hook checks a claim
before the claim goes into prose.

This hook cannot tell whether a claim is true.

This hook names the word it refuses. `prose_rewrite` names no word. `prose_rewrite` quotes the refused sentence. A
writer clears a shape fault by rewriting the whole prose of the fragment.

`a-count-is-answered-for` refuses a vague quantifier. `a-claim-writes-no-universal` refuses a universal and a
superlative. `use-the-words-this-project-uses` refuses a word the project turned down. `a-named-hedge-is-refused`
refuses the hedges the word list names. `a-reference-parser-has-one-name` refuses a loose name for either upstream
parser. `no-discovery-narration` refuses `turns out` and `turned out`. `a-plan-step-is-not-named-by-a-numeral` refuses a
step named by a numeral. `a-so-clause-is-a-why` refuses a purpose clause riding on `so`.
`a-dotted-name-sits-in-a-code-span` refuses a bare path.

`a-free-choice-word-is-a-universal` refuses `whatever` and `whichever`. `a-purpose-clause-says-no-action` refuses
`exists for` and `exists to`. `a-pointer-names-its-referent` refuses the runs `That last` and `This last`.
`a-name-of-the-tree-sits-in-a-code-span` refuses a name written outside the backticks. `a-question-word-is-no-noun`
refuses a free relative behind a preposition. `a-sentence-states-no-plan` refuses `yet` before a full stop or a comma.
The rule also refuses `not yet`, `will be` and `for now`. `a-marked-up-word-is-read-as-a-word` takes the emphasis off a
word before the rules above read it.

`prose_rules.word_refusal` gives the refusal. `prose_rules.words_found` matches the patterns. `check_prose` runs the
same checker over the tree. `collect_fragments.fragments_touched` gives the fragments whose prose the edit changes.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import prose_rules


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the word rules give for `prose` at `path`, or None where the words pass.

    The `prose_words.py` row of `checkers._CHECKERS` asks here with the prose and the path of an edit. `prose_answer`
    gives the critic's rewrite this same refusal through `apply_prose.draft_refusals`.

    `prose_rules` names the files whose text states a rule. The rules refusing a universal, a vague quantifier and a
    question word for a noun pass those files.
    """
    return prose_rules.word_refusal(prose, path)
