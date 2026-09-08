#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on StructuredOutput. The rules that refuse an edit reach a rewrite the critic hands back.

The critic holds no tool. Its answer arrives as this tool call. The workflow reads that answer next, and it lands in a
file after that. The rules reach the answer here instead.

The hooks hold the refusals and this calls them. A refused rewrite goes back with the words the writer of that edit
would have read. The critic then says that fragment again inside its own turn.

A refusal names the fragments that failed and no more. The critic answers with those. This hook keeps the fragments that
passed, under the agent that sent them. The call this hook lets through holds the kept fragments beside the new ones. So
the critic writes a fragment the checkers passed once.

A call naming a `rewritten` list gets read. `.claude/agents/prose-critic.md` says what the critic answers with.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import re
import sys

from typing import Any

import checkers
import collect_fragments
import gate
import refusal

# The directory holding the fragments an agent got right. Git tracks none of it.
_HELD_IN = os.path.join(gate.TREE, ".git", "prose-answer")

# The refusal this hook opens with. The refusals the hooks give follow it.
_SAID = (
    "A rewrite or a proposal below breaks a rule this project holds prose to. Say those again. Answer with the "
    "fragments named below and no more. This hook keeps what this call got right, and hands it to the workflow."
)

# The note the amended call states.
_WHY = "the fragments this agent got right earlier come back in the answer."

# The critic reads this where a change names text the fragment does not hold.
_NOT_HELD = (
    "The change to `{key}` says it replaces `{old}`. That fragment holds no such text. Copy `old` out of the batch "
    "character for character. Then answer with the change again."
)

# The file `agent-tools.sh` appends a withheld tool to. `check_agent_tools` reads it as a gate.
_MISSES = os.path.join(gate.TREE, ".git", "agent-tool-misses")

# The file this appends a fragment an agent found no fix for to. `check_agent_tools` reads it as a gate.
_UNFIXED = os.path.join(gate.TREE, ".git", "agent-unfixed")

# The fragment heading a batch writes above a fragment's prose. This reads the keys a batch gave an agent.
_A_FRAGMENT = re.compile(r"^### fragment `(.+?)` in ", re.MULTILINE)

# The critic reads this where the answer names no batch.
_NO_BATCH = (
    "Your answer names no batch this hook can read. Your prompt gives the path of a batch file. Copy that path into "
    "`batch`."
)

# The critic reads this where the answer passes over a fragment.
_PASSED_OVER = (
    "Your answer says nothing about the fragments below.\n\n{keys}\n\nA hook refused a fragment before the batch took "
    "it. A fragment with no change stays faulty. Answer with a change for it. Name the key in `unfixed` where you "
    "find no fix, and say why there."
)

# The critic reads this where the answer leaves out a tool the hook withheld.
_UNREPORTED = (
    "This agent asked for {tools} and the hook withheld that. Your answer leaves it out. Name it in `refused`. An "
    "answer that hides a withheld tool reads as a whole answer."
)

# The lists a call names beside the prose it holds. A later call adds to these.
_BESIDES = ("passed", "out_of_budget")

# The lists this hook holds to the rules, keyed so a later call replaces an entry rather than repeating it.
_CHECKED = ("changes", "proposals")

# The file a proposal lands in. The word rules read a path, and they read this one for a proposal.
_PROPOSALS = ".claude/proposals-pending.md"


def _refusals(prose: str, path: str, is_stating_why: bool = False) -> list[str]:
    """The refusals the write-time hooks give for `prose` at `path`. `checkers` names the hooks it asks."""
    return checkers.refusals_for(prose, path, is_stating_why=is_stating_why)


def _sited() -> dict[str, tuple[str, str]]:
    """`{the fragment key: (the file it is in, the prose it holds)}`, over the tree."""
    return {one.key: (one.path, one.prose.content) for one in collect_fragments.fragments()}


def _not_held(one: Any, prose: str) -> str | None:
    """The refusal for a change naming text `prose` does not hold, or None where the fragment holds that text."""
    old = one.get("old")
    if not isinstance(old, str) or not old.strip() or old not in prose:
        said = str(old)[:60] if isinstance(old, str) else "nothing shaped like a string"
        return _NOT_HELD.format(key=one.get("key", ""), old=said)
    return None


def _uncovered(tool_input: dict[str, Any], kept: dict[str, Any]) -> list[str]:
    """
    The refusals for an answer that passes over a fragment the batch gave the agent.

    A hook refused a fragment before the batch took it. A fragment with no change is therefore faulty still. `unfixed`
    is the way to answer for a fragment with no fix.

    `kept` holds what the calls before this one got right, and this reads a fragment banked there as answered for. A
    call naming the fragments a refusal listed and nothing besides therefore passes. This once read a call on its own. A
    narrowed call drew a refusal, and the agent then sent the whole batch again on the call after it.
    """
    path = str(tool_input.get("batch", ""))
    if not path or not os.path.exists(path):
        return [_NO_BATCH]
    with open(path, encoding="utf-8") as handle:
        given = _A_FRAGMENT.findall(handle.read())
    answered = {said.split("\t")[0] for said in kept["changes"]}
    answered |= {str(one.get("key", "")) for one in tool_input.get("unfixed") or [] if isinstance(one, dict)}
    missing = [key for key in given if key not in answered]
    if not missing:
        return []
    return [_PASSED_OVER.format(count=len(missing), keys="\n".join(f"  {key}" for key in missing))]


def _unreported(agent_id: str, tool_input: dict[str, Any]) -> list[str]:
    """
    The refusals for an answer that leaves out a tool `agent-tools.sh` withheld from this agent.

    An agent handed a refusal may answer as though nothing had been withheld. Such an answer reads as a whole answer. So
    the answer names the withheld tool in `refused`, and this holds the answer to that.
    """
    if not os.path.exists(_MISSES):
        return []
    with open(_MISSES, encoding="utf-8") as handle:
        withheld = {line.split("\t")[1] for line in handle.read().split("\n") if line.startswith(f"{agent_id}\t")}
    said = tool_input.get("refused")
    named = set(said) if isinstance(said, list) else set()
    missing = sorted(withheld - named)
    if not missing:
        return []
    return [_UNREPORTED.format(tools=", ".join(f"`{one}`" for one in missing))]


def _records(agent_id: str, tool_input: dict[str, Any]) -> None:
    """Write down the fragments the agent found no fix for. `check_agent_tools` reports those beside a withheld tool."""
    unfixed = [one for one in tool_input.get("unfixed") or [] if isinstance(one, dict)]
    if not unfixed:
        return
    with open(_UNFIXED, "a", encoding="utf-8") as handle:
        for one in unfixed:
            handle.write(f"{agent_id}\t{one.get('key', '')}\t{one.get('why', '')}\n")


def _kept_at(agent_id: str) -> str:
    """The file holding what an agent got right."""
    named = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id) or "unnamed"
    return os.path.join(_HELD_IN, f"{named}.json")


def _kept(agent_id: str) -> dict[str, Any]:
    """The rewrites and the proposals the agent got right on the calls before this one."""
    path = _kept_at(agent_id)
    if not os.path.exists(path):
        return {**{name: {} for name in _CHECKED}, **{name: [] for name in _BESIDES}}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _keeps(agent_id: str, held: dict[str, Any]) -> None:
    """Write down the fragments the agent has got right."""
    os.makedirs(_HELD_IN, exist_ok=True)
    with open(_kept_at(agent_id), "w", encoding="utf-8") as handle:
        json.dump(held, handle)


def _drops(agent_id: str) -> None:
    """Forget an agent, once the call it settles on has gone through."""
    path = _kept_at(agent_id)
    if os.path.exists(path):
        os.remove(path)


def _widened(kept: dict[str, Any], tool_input: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """`kept` with the lists `names` picks out of this call added to it. An entry appears there once."""
    for name in names:
        said = tool_input.get(name)
        if isinstance(said, list):
            kept[name] += [one for one in said if one not in kept[name]]
    return kept


def _proposed(tool_input: dict[str, Any], kept: dict[str, Any]) -> list[str]:
    """
    The refusals the write-time hooks give for the proposals this call states.

    `rule` and `why` go to the checkers. `seen` quotes the prose the critic found, and a quote of a fault holds that
    fault. `why` states a reason, and the WHY clause comes off it.

    A proposal the checkers pass goes into `kept` under its rule. The agent leaves that proposal out of the next call.
    """
    said = []
    for one in tool_input.get("proposals") or []:
        if not isinstance(one, dict):
            continue
        rule = str(one.get("rule", ""))
        found = _refusals(rule, _PROPOSALS) + _refusals(str(one.get("why", "")), _PROPOSALS, True)
        if found:
            said.append(f"### the proposal opening `{rule[:60]}`\n\n" + "\n\n".join(found))
        else:
            kept["proposals"][rule] = one
    return said


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    held = tool_input.get("changes")
    proposals = tool_input.get("proposals")
    if not isinstance(held, list) and not isinstance(proposals, list):
        return
    agent_id = str(payload.get("agent_id", ""))
    kept = _widened(_kept(agent_id), tool_input, _BESIDES)
    said = _proposed(tool_input, kept)
    said += _unreported(agent_id, tool_input)
    sited = _sited() if held else {}
    for one in held or []:
        if not isinstance(one, dict):
            continue
        key = str(one.get("key", ""))
        path, prose = sited.get(key, (key, ""))
        unfound = _not_held(one, prose) if key in sited else None
        if unfound:
            said.append(f"### {key}\n\n{unfound}")
            continue
        found = _refusals(str(one.get("new", "")), path)
        if found:
            said.append(f"### {key}\n\n" + "\n\n".join(found))
        else:
            kept["changes"][f"{key}\t{one.get('old', '')}"] = one
    if isinstance(held, list):
        said += _uncovered(tool_input, kept)
    if said:
        _keeps(agent_id, kept)
        refusal.refuse(_SAID + "\n\n" + "\n\n".join(said))
    _records(agent_id, tool_input)
    _drops(agent_id)
    answer = dict(tool_input)
    for name in _CHECKED + _BESIDES:
        if kept[name] or name in tool_input:
            answer[name] = list(kept[name].values()) if name in _CHECKED else kept[name]
    # A call that already says everything gets no amendment. Silence is how a hook passes.
    if answer != tool_input:
        refusal.amend(answer, _WHY)


if __name__ == "__main__":
    main()
