#!/usr/bin/env bash
# UserPromptSubmit. The rules go in front of the model.
#
# Somebody wrote these rules down, and the model dropped them again and again inside a session. Adherence decayed with
# conversation length. The rules held still while the model's memory of them decayed.
#
# A rule the model read a hundred turns back is gone. This puts the rules a turn back, and does so afresh per turn.
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
