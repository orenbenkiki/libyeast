#!/usr/bin/env bash
# PreToolUse on any tool, and PermissionDenied. This hook holds a turn to the requests the user has made.
#
# A session is serial. The user answers, and the answer shapes what comes next. Stacking a second question behind the
# first buries the first. Launching work while a question waits spends tokens on an answer the user is about to change.
# A `STOP` in that window lands after the damage.
#
# A refused tool call is the user saying no, and the turn stops there. Approving a prompt is the user saying yes, and
# the turn goes on. This hook marks no approved prompt.
#
# `.git/one-at-a-time` marks a launch, and a launch is a question or a fleet of agents. `.git/floor-is-yours` marks a
# refusal. The user speaking fires `UserPromptSubmit`. `clear-one-at-a-time.sh` deletes both marks on that event.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/refusal.sh"

root=${CLAUDE_PROJECT_DIR:-.}
launched="$root/.git/one-at-a-time"
denied="$root/.git/floor-is-yours"

payload=$(cat)
event=$(printf '%s' "$payload" | jq -r '.hook_event_name // ""')
tool=$(printf '%s' "$payload" | jq -r '.tool_name // "a tool call"')

# The tools that put something in front of the user and wait. A turn runs a single one of these.
_A_LAUNCH='^(AskUserQuestion|Workflow|Agent)$'

if [ "$event" = "PermissionDenied" ]; then
    printf '%s' "$tool" >"$denied"
    exit 0
fi

if [ -e "$denied" ]; then
    said=$(cat "$denied" 2>/dev/null || printf 'a tool call')
    deny "The user refused $said. The floor is theirs. Run nothing further. Say what you have in a sentence, name what you were about to do, and stop. This session drops the text the user types into a refusal. Wait for the message they type next."
fi

if printf '%s' "$tool" | grep -Eq "$_A_LAUNCH"; then
    if [ -e "$launched" ]; then
        held=$(cat "$launched" 2>/dev/null || printf 'something')
        deny "This turn already launched $held. Wait for the user to answer. A session is serial. The user reads what came, answers it, and that answer shapes what comes next. Say what you have and stop."
    fi
    printf '%s' "$tool" >"$launched"
fi
exit 0
