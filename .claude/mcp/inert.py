#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
An MCP server offering a tool that does nothing.

An agent definition lists the tools its agent may call. An empty list places no restriction. The agent then gets the
tools a subagent can have. A list whose names resolve to nothing stops the agent launching. So an agent meant to call no
tool must still list one. This server offers `nothing` for that. A call returns empty text and touches no file, no shell
and no network.

`.mcp.json` registers this server. `.claude/agents/prose-critic.md` and `.claude/agents/prose-compare.md` list only
`mcp__inert__nothing`.

Speaks JSON-RPC over stdin and stdout, a message to a line.
"""

import json
import sys

# The tool this server offers. An agent definition names it as `mcp__inert__nothing`.
_TOOL = {
    "name": "nothing",
    "description": "Does nothing and returns empty text. Answer from the prompt instead.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

# The protocol version answered where the client names none.
_SPOKEN = "2025-06-18"


def _answer(request: dict[str, object]) -> dict[str, object] | None:
    """The result for `request`. A method this server does not serve gives back None."""
    method = request.get("method")
    if method == "initialize":
        asked = request.get("params")
        version = asked.get("protocolVersion") if isinstance(asked, dict) else None
        return {
            "protocolVersion": version if isinstance(version, str) else _SPOKEN,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "inert", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": [_TOOL]}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": ""}], "isError": False}
    return None


def main() -> None:
    """Answer the requests arriving on standard input until the stream closes."""
    for line in sys.stdin:
        said = line.strip()
        if not said:
            continue
        request = json.loads(said)
        # A notification has no id and wants no reply.
        if "id" not in request:
            continue
        result = _answer(request)
        if result is None:
            reply = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "no such method"}}
        else:
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
