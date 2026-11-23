#!/usr/bin/env bash
# `.claude/settings.json` runs this hook on PreToolUse for `Workflow`. A workflow this tree holds runs from its file.
#
# This hook refuses a call whose `name` names a workflow the tree holds. A call that gives `scriptPath` and no `name`
# passes.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/refusal.sh"

root=${CLAUDE_PROJECT_DIR:-.}
payload=$(cat)
name=$(printf '%s' "$payload" | jq -r '.tool_input.name // ""')

if [ -n "$name" ] && [ -e "$root/.claude/workflows/$name.js" ]; then
    deny "The workflow \`$name\` lives in this tree. Launch it with \`scriptPath: $root/.claude/workflows/$name.js\`. A registered copy can be older than that file."
fi
exit 0
