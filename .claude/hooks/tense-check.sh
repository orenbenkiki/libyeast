#!/usr/bin/env bash
# PostToolUse/Edit|Write: comments and docs describe what IS — not the path that produced it, nor what is coming.
#
# A warning rather than a block: some of these words are legitimate in prose about the parse itself ("a later failure"),
# and `PLAN.md` is the one document whose whole job is the future, so it is exempt.
set -uo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
case "$path" in
*/PLAN.md | */CHANGELOG.md) exit 0 ;;
*.md | *.py | *.c | *.h) ;;
*) exit 0 ;;
esac

added=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // .tool_input.content // ""')
found=$(printf '%s' "$added" |
    grep -Eoi 'step [0-9]+|phase [0-9]+|not yet|will be|will need|previously|for now|— landed|has landed|to be (cut|done|written) (later|when)' |
    sort -u | paste -sd '; ' -)
[ -z "$found" ] && exit 0

jq -nc --arg found "$found" --arg path "$path" '{
    systemMessage: ("Tense check — " + $path + " gained: " + $found + ". A comment or doc states what IS. The path that produced it belongs to the changelog, and what is coming belongs to PLAN.md. Memory: describe-current-state-never-steps.")
}'
