#!/usr/bin/env bash
# PreToolUse/Bash: refuse a shell command that writes a repo file.
#
# Edits must go through the Edit tool so they show as a reviewable diff. Reading with grep/awk/sed is fine; writing is
# not. Redirects to /tmp, /dev/null and the job scratch directory are left alone, as is anything named *junk*.
set -uo pipefail

command=$(jq -r '.tool_input.command // ""')

deny() {
    jq -nc --arg why "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $why
        }
    }'
    exit 0
}

# In-place rewriters, wherever they point.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(sed|perl|ruby)[[:space:]]+-[[:alnum:]]*i'; then
    deny "sed/perl -i rewrites a file in place. Use the Edit tool — the user reviews changes as diffs (memory: edit-files-with-edit-tool-never-scripts)."
fi
if printf '%s' "$command" | grep -Eq '\.write_text\(|open\([^)]*,[[:space:]]*.w.\)|>[[:space:]]*\$\(|\btee\b'; then
    deny "This writes a file from the shell. Use the Edit tool (memory: edit-files-with-edit-tool-never-scripts)."
fi

# Redirects whose target is inside the repo: a source directory, or a top-level document.
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party)/'; then
    deny "This redirects into a source directory. Use the Edit tool (memory: edit-files-with-edit-tool-never-scripts)."
fi
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt)([[:space:]]|$)'; then
    deny "This redirects into a repo file. Use the Edit tool (memory: edit-files-with-edit-tool-never-scripts)."
fi

exit 0
