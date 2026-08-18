#!/usr/bin/env bash
# PostToolUse/Edit|Write: a question about the grammar is decided through `ir.Reading`, never an `isinstance` chain.
#
# Reads only the text the edit introduces, so the ~68 dispatches already standing do not fire on every edit. A rewrite
# that reshapes one kind and passes the rest through is not a question about the grammar and is not what this is about —
# the block asks for that to be said out loud rather than assumed.
set -uo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
case "$path" in
*/generator/*.py) ;;
*) exit 0 ;;
esac

# What this edit adds: an Edit's replacement, or a Write's whole content.
added=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // .tool_input.content // ""')
printf '%s' "$added" | grep -Eq 'isinstance\([^)]*ir\.' || exit 0
printf '%s' "$added" | grep -q 'ir\.Reading(' && exit 0

jq -nc '{
    decision: "block",
    reason: "This edit adds an `isinstance` dispatch over `ir.` kinds. A question about the grammar is decided through a total `ir.Reading` — measure which kinds reach it, name those, and let an unnamed kind raise. If this is a rewrite rather than a question (it reshapes one kind and passes the rest through), say so explicitly and carry on. Memory: no-shape-recognizers-no-gate-blindness."
}'
