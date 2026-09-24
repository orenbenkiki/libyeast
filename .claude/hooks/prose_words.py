#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook checks a claim before the claim goes into prose.

Code gets a feedback loop as the author types. Prose gets none until somebody reads it. This hook is that loop. This
hook cannot tell whether a claim is true.

This hook names the word it refuses. `prose_rewrite` names nothing. A word fault is a claim that may be false, and a
grep settles it. A writer clears a shape fault by saying the sentence again.

`a-count-is-answered-for` refuses a vague quantifier. `a-claim-writes-no-universal` refuses a universal and a
superlative. `use-the-words-this-project-uses` refuses a word the project turned down. `a-named-hedge-is-refused`
refuses the hedges the word list names. `a-reference-parser-has-one-name` refuses a loose name for either upstream
parser. `no-discovery-narration` refuses `turns out` and `turned out`. `a-plan-step-is-not-named-by-a-numeral` refuses a
step named by a numeral. `a-so-clause-is-a-why` refuses a purpose clause riding on `so`.
`a-dotted-name-sits-in-a-code-span` refuses a bare path.

`a-free-choice-word-is-a-universal` refuses `whatever`. `a-purpose-clause-says-no-action` refuses `exists for` and
`exists to`. `a-pointer-names-its-referent` refuses the runs `That last` and `This last`.
`a-name-of-the-tree-sits-in-a-code-span` refuses a name written outside the backticks. `a-question-word-is-no-noun`
refuses a free relative behind a preposition. `a-sentence-states-no-plan` refuses a sentence ending on `yet`.
`a-marked-up-word-is-read-as-a-word` takes the emphasis off a word before the rules above read it.

`prose_rules.words_found` holds the patterns. `check_prose` runs the same checker over the tree.
`collect_fragments.fragments_touched` gives the fragments the edit writes prose into.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import prose_rules


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the word rules give for `prose` at `path`, or None where the words pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.

    `prose_rules` names the files whose text states a rule. The rule refusing a universal passes those files.
    """
    return prose_rules.word_refusal(prose, path)
