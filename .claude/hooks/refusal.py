# SPDX-License-Identifier: MIT
"""
The way a hook answers a tool call.

A `PreToolUse` hook answers the tool with a JSON payload. The shape of that payload is a contract with the tool. A copy
with a wrong key prints nothing and reads exactly like an edit that passed. This module holds the payload, and a hook
imports it.

`refuse` turns the call back. `amend` lets it through with other arguments. `prose_answer` amends, and the shape it
sends came out of a probe rather than a document.

This is not a hook. `.claude/settings.json` registers the hooks and does not name this module. Python runs a hook by its
path and puts the hook's directory first on the import path. A hook beside this file finds this module there.
"""

import json
import sys

from collections.abc import Mapping
from typing import Any, NoReturn


def refuse(said: str) -> NoReturn:
    """Answer the tool call with a refusal, and stop."""
    print(json.dumps({"decision": "block", "reason": said}))
    sys.exit(0)


def amend(tool_input: Mapping[str, Any], why: str) -> NoReturn:
    """Let the tool call through with `tool_input` in place of the arguments it came with, and stop."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": why,
                    "updatedInput": tool_input,
                }
            }
        )
    )
    sys.exit(0)
