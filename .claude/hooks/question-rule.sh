#!/usr/bin/env bash
# PreToolUse on Edit and Write. `ir.Question` decides a question about the grammar. An `isinstance` chain does not.
#
# Reads only the text the edit introduces. The dispatches already in the tree do not fire on an edit elsewhere.
#
# A rewrite that reshapes a single kind and passes other kinds through asks nothing about the grammar. Such a rewrite
# says `rewrite rather than a question` in its own prose, and that phrase lets the edit through. Said out loud rather
# than assumed.
set -euo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
case "$path" in
*/generator/*.py) ;;
*) exit 0 ;;
esac

# The text this edit adds. An Edit's replacement, or a Write's whole content.
added=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // .tool_input.content // ""')
# A test against `ir.Node` names no kind. It asks whether a value is a node at all. This refuses a dispatch over the
# kinds themselves. The base comes out before the pattern runs.
if ! printf '%s' "$added" | sed 's/ir\.Node\b//g' | grep -Eq 'isinstance\([^)]*ir\.'; then
    exit 0
fi
if printf '%s' "$added" | grep -q 'ir\.Question('; then
    exit 0
fi
# A rewrite declares itself in its own prose, in the words the block below asks for.
if printf '%s' "$added" | grep -qi 'rewrite rather than a question'; then
    exit 0
fi

jq -nc '{
    decision: "block",
    reason: "This edit adds an `isinstance` dispatch over `ir.` kinds. A total `ir.Question` decides a question about the grammar. Measure which kinds reach it, name those, and let an unnamed kind raise. A rewrite reshapes a single kind and hands back any other kind. Write `rewrite rather than a question` in your own prose. Name the kind that gets reshaped, and this lets the edit through. Memory `no-shape-recognizers-no-gate-blindness`."
}'
