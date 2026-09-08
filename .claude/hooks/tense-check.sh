#!/usr/bin/env bash
# PostToolUse on Edit and Write. A comment or a document describes the thing rather than its history.
#
# A warning rather than a block. Some of these words are legitimate in prose about the parse itself
# ("a later failure"). `PLAN.md` and `CHANGELOG.md` are exempt.
set -euo pipefail

# `grep` answers `1` where the pattern matched nothing. `grep` answers `2` where the run failed. The wrapper turns the
# first into an answer and lets the second stop the hook. A bare `|| true` would read the pair alike.
# `failures-are-not-ignored` refuses that.
matched() { grep -Eoi "$1" || [ $? -eq 1 ]; }

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
case "$path" in
*/PLAN.md | */CHANGELOG.md) exit 0 ;;
*.md | *.py | *.c | *.h) ;;
*) exit 0 ;;
esac

added=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // .tool_input.content // ""')
found=$(printf '%s' "$added" |
    matched 'step [0-9]+|phase [0-9]+|not yet|will be|will need|previously|for now|- landed|has landed|to be (cut|done|written) (later|when)' |
    sort -u | paste -sd '; ' -)
if [ -z "$found" ]; then
    exit 0
fi

jq -nc --arg found "$found" --arg path "$path" '{
    systemMessage: ("Tense check - " + $path + " gained: " + $found + ". A comment or doc states the current state. The path that produced it belongs to the changelog, and what is coming belongs to PLAN.md. Memory `describe-current-state-never-steps`.")
}'
