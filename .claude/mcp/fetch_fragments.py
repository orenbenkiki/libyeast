#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
An MCP server offering the tools that read the fragments of the tree.

The claim pass hands an agent a single fragment. The agent reads that fragment's prose against its code. A sentence
claiming something about another thing needs the fragment holding that thing, and `fetch` answers with it.

The server appends the key it served to the file `collect_fragments.CLAIM_RECORD` names. The driver reads that file once
the call ends. The keys there are the fragments the agent read. A `callers` question adds a line naming the question.
The server also records the keys of the callers it served. A `mentions` question adds a line where the server ran the
search. A list taken off the agent's answer would miss a read the agent left unsaid. Such a read looks the same as a
read that did not happen.

`.mcp.json` registers this server. `.claude/agents/prose-claims.md` lists the tools it offers.

A call is a run of `claude -p`, and `agent_calls._called` starts that run. The run starts this server as a subprocess
over stdio, as https://modelcontextprotocol.io/docs/concepts/transports says. The run ends the server as the call ends,
as https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle#shutdown says. `_converge` in
`generator/converge_claims.py` keeps calls in flight, and writes an answer into the tree as its call returns. The tree
can move under a call still in flight. The server reads the fragments once and keeps them.

Speaks JSON-RPC over stdin and stdout. A line holds a single message.
"""

import json
import os
import sys

from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "generator"))

# pylint: disable=wrong-import-position
import collect_fragments  # noqa: E402

# The `fetch` tool this server offers. The agent definition `.claude/agents/prose-claims.md` names it as
# `mcp__fetch_fragments__fetch`.
_TOOL: dict[str, Any] = {
    "name": "fetch",
    "description": (
        "Fetch a fragment of this repository by name. The answer holds that fragment's prose and the code it "
        "documents. A citation writes such a name, as in `check_documents` or `apply_prose.does_hold`, with no "
        "digest on it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "the name of the fragment to read."}},
        "required": ["name"],
        "additionalProperties": False,
    },
}

# The second tool this server offers. An agent definition names it as `mcp__fetch_fragments__callers`.
_CALLERS_TOOL: dict[str, Any] = {
    "name": "callers",
    "description": (
        "The fragments that call a name. The answer holds the prose and the code of the callers. Ask this where a "
        "claim says what a caller does before or after the call."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "the name whose callers to list."}},
        "required": ["name"],
        "additionalProperties": False,
    },
}

# The third tool this server offers. An agent definition names it as `mcp__fetch_fragments__mentions`.
_MENTIONS_TOOL: dict[str, Any] = {
    "name": "mentions",
    "description": (
        "The fragments that mention a name. The answer holds a key per fragment, and a fetch of such a key reads "
        "that fragment. This tool takes a name the tree declares. The member of a struct is not such a name. This "
        "tool refuses a member."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "the name whose mentions to list."}},
        "required": ["name"],
        "additionalProperties": False,
    },
}

# The most characters of code a fetch hands over for a file. A longer file comes back as `collect_fragments.outline_of`
# writes it.
_MOST_CODE = 32_000

# The protocol version answered where the client names none.
_SPOKEN = "2025-06-18"

# The fragments of the tree. The first fetch, callers or mentions question reads them. A later question reuses these
# fragments.
_HELD: dict[str, collect_fragments.Fragment] = {}

# The names a fetch may ask for, and the keys they name. `collect_fragments.keys_by_name` builds this map.
_KEYS: dict[str, set[str]] = {}

# The names `_mentioning` takes. The tree declares a fragment under such a name, or an enum body declares the name, as
# `c_constants` finds.
_DECLARED: set[str] = set()

# The field names the structs and unions of the tree declare. `collect_fragments.c_members` builds this set, and
# `_mentioning` refuses a name it holds.
_MEMBERS: set[str] = set()


def _read() -> None:
    """
    Read the fragments of the tree, and the names a fetch may ask for. The server reads once, on its first call, and a
    later call finds the roster held.

    `collect_fragments.code_only` joins the roster. Prose of ours makes claims about code that holds no prose of ours. A
    reader reads that code to settle such a claim.
    """
    if _HELD:
        return
    held = collect_fragments.fragments() + collect_fragments.code_only()
    _HELD.update({one.key: one for one in held})
    _KEYS.update(collect_fragments.keys_by_name(held))
    _DECLARED.update(name for name in (one.key.rpartition(":")[2] for one in held) if name)
    _DECLARED.update(collect_fragments.c_constants(held))
    _MEMBERS.update(collect_fragments.c_members(held))


def _recorded(keys: list[str]) -> None:
    """
    Append `keys` to the file `collect_fragments.CLAIM_RECORD` names.

    The claim pass reaches this through the fetch, callers and mentions tools, and `converge_claims` names the file per
    call. `.claude/agents/prose-claims.md` is the agent definition that lists the tools of this server. A run with no
    such name raises. Writing nothing there would leave the driver an empty read-set, and an empty read-set looks the
    same as a fragment that read nothing.
    """
    path = os.environ.get(collect_fragments.CLAIM_RECORD, "")
    if not path:
        raise ValueError(
            f"a fetch served a fragment and {collect_fragments.CLAIM_RECORD} names no file to record it in"
        )
    with open(path, "a", encoding="utf-8") as handle:
        for key in keys:
            handle.write(key + "\n")


def _served(key: str) -> str:
    """
    The text serving the fragment `key`. A file fragment holding more code than `_MOST_CODE` comes back as an outline.
    So does the file fragment of a markdown document. The outline lists the keys of the document's paragraphs under its
    headings.
    """
    one = _HELD[key]
    is_large = collect_fragments.is_a_file(key) and len(one.code.content) > _MOST_CODE
    if collect_fragments.is_a_document(key) or is_large:
        return collect_fragments.outline_of(list(_HELD.values()), one)
    return collect_fragments.said_by(one)


def _fetched(name: str) -> str:
    """
    The text answering a fetch of `name`.

    A name reaching a single fragment answers with what `_served` gives for that fragment, and the record takes its key.
    `_served` gives an outline for a markdown document and for a file fragment holding more code than `_MOST_CODE`. It
    gives what `collect_fragments.said_by` writes for any other fragment, the prose and then the code. A name reaching a
    pair of fragments or more answers with the keys, and the record takes nothing. The caller asks again under the key
    it meant. A name the tree lacks answers with a line saying so.

    A key answers here as a name does. An ambiguous name comes back with the keys, and a caller that reads those keys
    asks under one of them. A directory answers with the keys of the file fragments under it.
    """
    _read()
    asked = name.strip().strip("`")
    if asked in _HELD:
        _recorded([asked])
        return _served(asked)
    keys = sorted(_KEYS.get(asked, ()))
    if not keys:
        under = asked.rstrip("/") + "/"
        files = sorted(
            key for key, one in _HELD.items() if collect_fragments.is_a_file(key) and one.path.startswith(under)
        )
        if files:
            return f"`{name}` is a directory. Ask under the key of a file in it.\n\n" + "\n".join(
                f"- `{key}`" for key in files
            )
        return f"The tree holds no fragment named `{name}`."
    if len(keys) > 1:
        return (
            f"The name `{name}` names more than a single fragment. Ask again under the key you meant.\n\n"
            + "\n".join(f"- `{key}`" for key in keys)
        )
    _recorded(keys)
    return _served(keys[0])


def _calling(name: str) -> str:
    """
    The text answering a question about the callers of `name`. The answer holds the prose and the code of the callers.

    The record takes the question and the keys of the callers. `claim_state` asks the question again, and a caller list
    that moved restales the fragment that read it. A caller whose code moved restales that fragment too.
    """
    _read()
    asked = name.strip().strip("`")
    keys = collect_fragments.callers_of(list(_HELD.values()), asked)
    _recorded([collect_fragments.CALLERS + asked, *keys])
    if not keys:
        return f"The tree holds nothing that calls `{asked}`."
    said = f"These fragments call `{asked}`.\n\n"
    return said + "\n".join(_served(key) for key in keys)


def _mentioning(name: str) -> str:
    """
    The text answering a question about the mentions of `name`.

    The record takes the question rather than the keys. `claim_state` asks the question again, and a list that moved
    restales the fragment that read it.

    The field of a struct comes back refused, and the record takes nothing. The refusal points at the fetch that reads
    the struct holding that field. A name the tree declares no fragment under comes back refused as well.
    """
    _read()
    asked = name.strip().strip("`")
    if asked in _MEMBERS:
        return (
            f"`{asked}` is a field of a C struct, and structs share a field name. A search for that name crosses the "
            "structs declaring it. Fetch the struct you mean and read the field in its code."
        )
    if asked not in _DECLARED:
        return f"The tree declares no fragment named `{asked}`, and this tool reads a name the tree declares."
    keys = collect_fragments.mentions_of(list(_HELD.values()), asked)
    _recorded([collect_fragments.MENTIONS + asked])
    if not keys:
        return f"The tree holds nothing that mentions `{asked}`."
    said = f"These fragments mention `{asked}`. Fetch a key below to read one.\n\n"
    return said + "\n".join(f"- `{key}`" for key in keys)


# The call serving a tool, by the name of that tool.
_SERVED = {_TOOL["name"]: _fetched, _CALLERS_TOOL["name"]: _calling, _MENTIONS_TOOL["name"]: _mentioning}


def _answer(request: dict[str, object]) -> dict[str, object] | None:
    """The result for `request`. A method this server does not serve gives back None."""
    method = request.get("method")
    if method == "initialize":
        asked = request.get("params")
        version = asked.get("protocolVersion") if isinstance(asked, dict) else None
        return {
            "protocolVersion": version if isinstance(version, str) else _SPOKEN,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fetch_fragments", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": [_TOOL, _CALLERS_TOOL, _MENTIONS_TOOL]}
    if method == "tools/call":
        asked = request.get("params")
        given = asked.get("arguments") if isinstance(asked, dict) else None
        name = given.get("name") if isinstance(given, dict) else None
        called = asked.get("name") if isinstance(asked, dict) else None
        serves = _SERVED.get(called)
        if not isinstance(name, str):
            said = "A call names the fragment to read under `name`."
        elif serves is None:
            said = f"This server offers no tool named `{called}`."
        else:
            said = serves(name)
        return {"content": [{"type": "text", "text": said}], "isError": False}
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
