#!/usr/bin/env bash
# The way a shell hook answers a tool call.
#
# A PreToolUse hook answers the tool with a JSON payload. The shape of that payload is a contract with the tool. A copy
# with a wrong key prints nothing and reads exactly like an edit that passed. This file holds the payloads, and a hook
# sources it.
#
# `refuse` turns the call back with a reason the model reads. `deny` turns it back as a permission decision. The pair
# differ in the shape the tool wants, and a hook picks the shape its event takes.
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
