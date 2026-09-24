#!/usr/bin/env bash
# UserPromptSubmit.
#
# The model loses a rule it read far back in a session.
#
# This hook puts the rules in the prompt afresh per turn.
#
# Keep this list SHORT. A long list decays the same way the conversation does.
set -euo pipefail

cat <<'RULES'
<rules>
Before acting, check these. They are not advisory.

- MECHANISM BEFORE INSTANCE. A problem raised is a single case or an instance of a class. Judge which from context. For
  a class, build the mechanism that handles the class FIRST, then solve the instance. Fixing the cited instance with the
  class mechanism unbuilt is the error.

- FIX BY DELETING. Make it a gate. Failing that, delete the text. Failing that, correct the text in place. A rewrite
  comes last. A fix leaves behind less prose than it removed. Splitting a long sentence into a pair of short ones is
  growth to nobody.

- RULE A PROPOSAL IN THE SAME TURN. A convention proposed and ruled on by nobody comes back the round after. The turn
  ends with that convention in the accepted list or in the rejected list. The entry states why.

- VERIFY BEFORE WRITING. Check a universal against the tree first. Check a superlative, a count and an enumeration the
  same way. Grep the tree. Read the callee. Expand the glob. Intent is free and state costs a grep.

- ONE IDEA PER SENTENCE. Subject and verb near the front. Skip the em-dash. An "it" points at a single thing, and a
  reader sees which. A clause explaining WHY comes out.

- A DOCUMENT AND A COMMENT STATE NO NUMBER THE TREE DECIDES. Say WHICH ONES rather than how much.

These bind your messages as much as your file edits.
</rules>
RULES

# The settling loop records the rules this project's writers break. The lines below print those rules. `batch_pending_fragments.rules_by_use` writes
# those rules to a list. The critic reads that list too. The hook prints nothing in a tree where the settling loop has run no round.
PYTHONPATH="$CLAUDE_PROJECT_DIR/generator" python3 -c \
    'import batch_pending_fragments; said = batch_pending_fragments.rules_by_use(); print(said) if said else None'
