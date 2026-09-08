#!/usr/bin/env bash
# PreToolUse on any tool. This decides a background agent's tool call rather than asking the user.
#
# A prompt from an agent arrives while the user is typing. The text the user types into that prompt goes to the agent,
# and the session reads none of it. So `STOP` typed there reaches nobody. A hook decision raises no prompt, and this
# answers for an agent instead.
#
# The case list below names what an agent may call. This refuses a call outside that list. A refusal tells the agent to
# stop, and to name the tool in its answer. A refusal lands in `.git/agent-tool-misses` as well. An agent that dies
# before answering reports nothing, and the file survives that.
#
# The session's own calls go through. A payload naming an agent in `agent_id` is an agent's call.
set -euo pipefail

root=${CLAUDE_PROJECT_DIR:-.}
misses="$root/.git/agent-tool-misses"

payload=$(cat)
agent=$(printf '%s' "$payload" | jq -r '.agent_id // ""')
tool=$(printf '%s' "$payload" | jq -r '.tool_name // ""')
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // .tool_input.path // ""')

if [ -z "$agent" ]; then
    exit 0
fi

# The tool and the path an agent may call. A tool taking no path matches an empty path.
may_use() {
    case "$1:$2" in
    StructuredOutput:*) return 0 ;;
    TodoWrite:*) return 0 ;;
    Read:*/.claude/conventions.md) return 0 ;;
    Read:*/.claude/rejected.md) return 0 ;;
    Read:*/scratchpad/batch.*.md) return 0 ;;
    *) return 1 ;;
    esac
}

# A tool an agent reaches for that this withholds on purpose. Somebody wrote the case down, and a written decision is
# no fault. The refusal is mild, and the misses file takes no line.
#
# A reader goes looking for the source a fragment describes. The batch holds the prose, and the job is the prose. A
# reader that opens the source starts checking claims. Claim checking belongs to a later pass.
may_not_use() {
    case "$1" in
    Read | Grep | Glob) return 0 ;;
    *) return 1 ;;
    esac
}

if may_use "$tool" "$path"; then
    exit 0
fi

if may_not_use "$tool"; then
    jq -nc --arg tool "$tool" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: ($tool + " is no part of this work. Your batch file holds what you need. Go on with the job.")
        }
    }'
    exit 0
fi

printf '%s\t%s\t%s\n' "$agent" "$tool" "$path" >>"$misses"
jq -nc --arg tool "$tool" --arg path "$path" '{
    hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: ("This agent may not call " + $tool + " on " + $path + ". STOP. Call no further tool. Send the answer you have already. Name " + $tool + " in the `refused` field of that answer. Your prompt names a batch file, and that file holds the work.")
    }
}'
exit 0
