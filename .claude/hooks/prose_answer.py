#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on StructuredOutput. The checkers that refuse an edit reach a rewrite the critic hands back.

The critic holds no tool. Its answer arrives as this tool call. The workflow reads that answer next. The answer then
lands in a file. The checkers reach the answer here instead.

`apply_prose.draft_refusals` checks a rewrite, and `record_run.proposal_refusals` checks a proposal. A refused rewrite
goes back with the refusal a writer of the same edit reads. The critic then says the refused fragment again inside its
own turn.

A refusal names the fragments that failed and no more. The critic answers with those fragments. This hook keeps the
fragments that passed, under the agent that sent them. The call this hook lets through holds the kept fragments beside
the new ones. So the critic does not write a passed fragment again.

A rewrite no retry gets right would run the call out of retries. `_MOST_REFUSALS` caps how often this hook refuses a
single agent. Past the cap, this hook drops the faulty rewrite from the answer. A fragment that passed still goes
through.

This hook reads a call naming a `verdicts` map or a `proposals` list. `.claude/agents/prose-critic.md` says what the
critic answers with.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import re
import sys

from typing import Any

import apply_prose
import collect_fragments
import gate
import record_run
import refusal

# The directory holding the fragments an agent got right. Git tracks none of it.
_HELD_IN = os.path.join(gate.TREE, ".git", "prose-answer")

# The refusal this hook opens with. The refusals the checkers give follow it.
_SAID = (
    "A rewrite below breaks a rule this project holds prose to. Say those again. Answer with the "
    "fragments named below and no more. This hook keeps what this call got right, and hands it to the workflow."
)

# The note the amended call states.
_WHY = "the fragments this agent got right earlier come back in the answer."

# The refusals an agent gets before this hook stops refusing. A rewrite no retry gets right runs the call out of
# retries, and the workflow ends there. The hook drops that rewrite past the cap and lets the rest of the answer
# through.
_MOST_REFUSALS = 3

# The name the store counts refusals under.
_REFUSALS = "refusals"

# The critic reads this where a change names text the fragment does not hold.
_NOT_HELD = (
    "The change to `{key}` says it replaces `{old}`. That fragment holds no such text. Copy `old` out of the batch "
    "character for character. Then answer with the change again."
)

# The critic reads this where a change cites a rule the conventions do not hold.
_NOT_A_RULE = (
    "The change to `{key}` cites `{rule}`. `.claude/conventions.md` holds no rule under that name. Cite a rule that "
    "file holds. Drop the change where no rule of that file covers it."
)

# The lists a call names beside the prose it holds. A later call adds to these.
_BESIDES = ("passed", "out_of_budget")

# The list of proposals the checkers read. The store keys a proposal by its rule. A later call replaces the entry.
_PROPOSALS = "proposals"

# The map the checkers read. A fragment key names an entry, and the schema of a batch wants a verdict per key.
_VERDICTS = "verdicts"


def _not_held(key: str, one: Any, prose: str) -> str | None:
    """The refusal for a change quoting text that `prose` lacks, or None where `prose` holds the quoted text."""
    old = one.get("old")
    if not isinstance(old, str) or not old.strip() or old not in prose:
        said = str(old)[:60] if isinstance(old, str) else "nothing shaped like a string"
        return _NOT_HELD.format(key=key, old=said)
    return None


def _uncited(key: str, changes: list[Any], prose: str) -> list[str]:
    """The refusals for a fragment's changes. A change quotes text the fragment holds and cites a rule."""
    rules = gate.conventions()
    said = []
    for one in changes:
        if not isinstance(one, dict):
            continue
        unfound = _not_held(key, one, prose)
        if unfound:
            said.append(unfound)
        rule = str(one.get("rule", ""))
        if rule not in rules:
            said.append(_NOT_A_RULE.format(key=key, rule=rule[:60]))
    return said


def _judged(held: dict[str, Any], kept: dict[str, Any]) -> list[str]:
    """
    The refusals for the rewrites a `verdicts` answer holds.

    A verdict of `rewritten` holds its changes under `changes`. A change quotes the text it replaces and cites a rule.
    `apply_prose.draft_refusals` checks the prose those changes leave. A verdict that passes goes into `kept`. The call
    after this one brings that verdict back.
    """
    by_key = {one.key: one for one in collect_fragments.fragments()}
    said = []
    for key, one in held.items():
        if not isinstance(one, dict):
            continue
        if one.get("verdict") != "rewritten":
            kept[_VERDICTS][key] = one
            continue
        fragment = by_key.get(key)
        prose_by_site = [site.prose for site in fragment.sites] if fragment else []
        changes = one.get("changes") or []
        found = _uncited(key, changes, "\n".join(prose_by_site))
        if not found and fragment:
            found = apply_prose.draft_refusals(fragment, apply_prose.applied(prose_by_site, changes))
        if found:
            said.append(f"### {key}\n\n" + "\n\n".join(found))
        else:
            kept[_VERDICTS][key] = one
    return said


def _kept_at(agent_id: str) -> str:
    """The file holding what an agent got right."""
    named = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id) or "unnamed"
    return os.path.join(_HELD_IN, f"{named}.json")


def _kept(agent_id: str) -> dict[str, Any]:
    """The verdicts and the proposals the agent got right on the calls before this one."""
    path = _kept_at(agent_id)
    empty: dict[str, Any] = {_PROPOSALS: {}, _VERDICTS: {}, **{name: [] for name in _BESIDES}}
    if not os.path.exists(path):
        return empty
    with open(path, encoding="utf-8") as handle:
        held: dict[str, Any] = json.load(handle)
    return {**empty, **held}


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


def _widened(kept: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    """Add to `kept` the lists `_BESIDES` picks out of this call. The result holds an entry once."""
    for name in _BESIDES:
        said = tool_input.get(name)
        if isinstance(said, list):
            kept[name] += [one for one in said if one not in kept[name]]
    return kept


def _proposed(tool_input: dict[str, Any], kept: dict[str, Any]) -> None:
    """
    Keep the proposals `record_run.proposal_refusals` passes, and drop a faulty proposal.

    A proposal about a word quotes that word. The checkers refuse that quote on a retry too. The amended answer states
    the proposals that passed.
    """
    for one in tool_input.get(_PROPOSALS) or []:
        if not record_run.proposal_refusals(one):
            kept[_PROPOSALS][str(one.get("rule", ""))] = one


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    proposals = tool_input.get(_PROPOSALS)
    verdicts = tool_input.get(_VERDICTS)
    if not isinstance(proposals, list) and not isinstance(verdicts, dict):
        return
    # A `claude -p` call names no agent. Its session tells parallel calls apart.
    agent_id = str(payload.get("agent_id") or payload.get("session_id") or "")
    kept = _widened(_kept(agent_id), tool_input)
    _proposed(tool_input, kept)
    said = _judged(verdicts, kept) if isinstance(verdicts, dict) else []
    if said:
        kept[_REFUSALS] = int(kept.get(_REFUSALS, 0)) + 1
        if kept[_REFUSALS] < _MOST_REFUSALS:
            _keeps(agent_id, kept)
            refusal.refuse(_SAID + "\n\n" + "\n\n".join(said))
    _drops(agent_id)
    answer = dict(tool_input)
    if kept[_PROPOSALS] or _PROPOSALS in tool_input:
        answer[_PROPOSALS] = list(kept[_PROPOSALS].values())
    for name in _BESIDES:
        if kept[name] or name in tool_input:
            answer[name] = kept[name]
    if kept[_VERDICTS] or _VERDICTS in tool_input:
        answer[_VERDICTS] = kept[_VERDICTS]
    # A call that already says everything gets no amendment. Silence is how a hook passes.
    if answer != tool_input:
        refusal.amend(answer, _WHY)


if __name__ == "__main__":
    main()
