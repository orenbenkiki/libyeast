#!/usr/bin/env bash
# The way a shell hook answers a tool call.
#
# A PreToolUse hook answers the tool with a JSON payload, as https://code.claude.com/docs/en/hooks says. This file holds
# the payloads. `.claude/hooks/no-shell-file-writes.sh`, `.claude/hooks/one-at-a-time.sh` and
# `.claude/hooks/workflow-from-the-tree.sh` source it.
#
# `deny` turns a call back as a permission decision. `refuse` and `deny` differ in the shape the tool wants, and a hook
# picks the shape its event takes. https://code.claude.com/docs/en/hooks gives the shapes.
#
# This is no hook. `.claude/settings.json` registers the hooks and does not name this file. `refusal.py` answers the
# same question for a hook written in Python. `a-hook-refuses-through-one-helper` is the rule.
set -euo pipefail

refuse() {
    jq -nc --arg why "$1" '{ decision: "block", reason: $why }'
    exit 0
}

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
