#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on StructuredOutput. The checkers that refuse an edit reach a rewrite the critic hands back.

The critic reads nothing and runs nothing. Its answer arrives as this tool call, and the checkers reach the answer here.

`apply_prose.draft_refusals` checks a rewrite, and `record_run.proposal_refusals` checks a proposal. A refused rewrite
goes back with the refusal a writer of the same edit reads. The critic then rewrites the refused fragment inside its own
turn.

A refusal names the fragments that failed and no more. The critic answers with those fragments. This hook keeps the
fragments that passed, under the agent that sent them. The call this hook lets through holds the kept fragments beside
the new ones.

An agent answers again after a refusal, until this hook passes the answer. The `--max-turns` that `agent_calls._called`
passes bounds the call.

This hook reads a call naming a `verdicts` map or a `proposals` list. `.claude/agents/prose-critic.md` says what the
critic answers with. An empty `verdicts` map gets the refusal a gap in a tiling gets. A kept fragment or a kept proposal
spares the empty map that refusal.

A critic call names its batch through `collect_fragments.CRITIC_BATCH`. This hook refuses a verdict outside that batch.
It also refuses an answer that leaves a key of the batch with no verdict.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import dataclasses
import functools
import json
import os
import re
import sys

from typing import Any

import agent_answers
import apply_prose
import claim_state
import collect_fragments
import gate
import record_run
import refusal

# The directory holding the fragments an agent got right and the journal of the calls this hook decides.
_HELD_IN = os.path.join(gate.TREE, ".git", "prose-answer")

# The journal takes a line per call this hook decides. `refuse` and `amend` end the hook after their line. A person
# reads the journal to see whether this hook refused, amended or passed a call.
_DECISIONS = os.path.join(_HELD_IN, "decisions.jsonl")

# The refusal this hook opens with. The faults the answer drew follow it.
_SAID = (
    "Your answer breaks something this project holds prose to. The lines below name the fragment and the fault. A "
    "fault says what to do about it. Answer with the fragments named below and no more. This hook keeps what this "
    "call got right, and hands it to the workflow."
)

# `amend` gives this note as its reason.
_WHY = "the fragments this agent got right earlier come back in the answer."

# The name the store counts refusals under.
_REFUSALS = "refusals"

# The agent reads this where a change quotes text that no single site of the fragment holds.
_NOT_HELD = (
    "The change to `{key}` says it replaces `{old}`. That fragment holds no such text in a single comment. Copy `old` "
    "out of the batch character for character. A change stops where its comment stops. Then answer with the change "
    "again."
)

# The critic reads this where a change cites a rule the conventions do not hold.
_NOT_A_RULE = (
    "The change to `{key}` cites `{rule}`. `.claude/conventions.md` holds no rule under that name. Cite a rule that "
    "file holds. Drop the change where no rule of that file covers it."
)

# A claims reader reads this where a citation names code the tree lacks.
_NOT_SHOWN = (
    "Your answer about `{key}` cites `{name}`. A fetch reaches no code under that name. Cite the code you read, the "
    "way a fetch writes a name."
)

# A claims reader reads this where a citation names more than a single fragment.
_SHARED = (
    "Your answer about `{key}` cites `{name}`. That name reaches {keys}. The lines of a citation count from the first "
    "line of a single fragment. Cite under the key you read."
)

# A claims reader reads this where an unverifiable run names a fragment the reader did not read.
_NOT_FETCHED = (
    "Your answer about `{key}` calls a run unverifiable, and `looked_for` names `{name}`. You did not read `{held}`. "
    "Fetch `{held}`. Then settle the run against the code of `{held}`."
)

# A claims reader reads this where its answer cites a read that `unpointed_reads` finds the fragment does not point at.
_NOT_POINTED = (
    "Your answer about `{key}` cites the fragments below under `shown_by` or `denied_by`. Neither the prose nor the "
    "code names them. A claim resting on such a fragment names it in the run holding the claim. That run takes the "
    "verdict `changed`. A line below gives a fragment and the name to write. Drop a citation the claim does not rest "
    "on.\n\n{reads}"
)

# A claims reader reads this where a citation names no valid range of lines. A range starting before the first line is
# invalid. So is a range running backwards or past the end of the code.
_NOT_A_RANGE = (
    "Your answer about `{key}` cites `{name}` from line {first} to line {last}. The code of that fragment ends at "
    "line {lines}. Count the lines as the code arrived, from its own first line."
)

# A reader reads this where a part of its answer is no JSON object.
_NOT_AN_OBJECT = (
    "Your answer about `{key}` holds a {what} that is no JSON object. Rewrite that {what} in the shape the schema "
    "gives it."
)

# A reader reads this where a verdict names a key the tree lacks.
_NOT_A_FRAGMENT = (
    "Your answer holds a verdict about `{key}`. The tree holds no fragment under that key. Answer about the keys your "
    "prompt holds."
)

# The lists a call names beside the prose it holds. A later call adds to these.
_BESIDES = ("passed", "out_of_budget")

# The key that names the proposals in a call and in the store. The store keys a proposal by its rule. A later proposal
# under the same rule replaces the entry once it passes `record_run.proposal_refusals`.
_PROPOSALS = "proposals"

# The name of the map the checkers read. A fragment key names an entry.
_VERDICTS = "verdicts"

# The refusal of a verdict whose key sits outside the critic's batch.
_OUTSIDE = (
    "Your answer gives `{key}` a verdict. `{key}` sits outside your batch. Give verdicts to the keys of your batch."
)

# The refusal of a key of the batch with no verdict in this answer or in the kept verdicts.
_UNANSWERED = "Your answer gives `{key}` no verdict. `{key}` belongs to your batch. Give it a verdict."


def _not_held(key: str, one: Any, prose_by_site: list[str]) -> str | None:
    """
    The function refuses a change whose `old` no single site of `prose_by_site` holds. The function refuses a blank
    `old` and an `old` that is no string. The function returns `None` for a sound `old`.
    """
    old = one.get("old")
    if not isinstance(old, str) or not old.strip() or not apply_prose.is_held(prose_by_site, old):
        said = str(old)[:60] if isinstance(old, str) else "nothing shaped like a string"
        return _NOT_HELD.format(key=key, old=said)
    return None


def _uncited(key: str, changes: list[Any], prose_by_site: list[str]) -> list[str]:
    """The refusals for a fragment's changes. A change quotes text a site of the fragment holds and cites a rule."""
    rules = gate.conventions()
    said = []
    for one in changes:
        if not isinstance(one, dict):
            said.append(_NOT_AN_OBJECT.format(key=key, what="change"))
            continue
        unfound = _not_held(key, one, prose_by_site)
        if unfound:
            said.append(unfound)
        rule = str(one.get("rule", ""))
        if rule not in rules:
            said.append(_NOT_A_RULE.format(key=key, rule=rule[:60]))
    return said


@functools.cache
def _key_by_name() -> dict[str, str]:
    """
    `{a name a fetch serves: the key of the fragment under that name}`.

    A key names its fragment. A name reaching a single fragment names that fragment. `_shared_names` holds a name
    reaching more.
    """
    held = collect_fragments.fragments() + collect_fragments.code_only()
    return {name: next(iter(keys)) for name, keys in collect_fragments.reach_by_name(held).items() if len(keys) == 1}


def _code_lines() -> dict[str, int]:
    """`{a name a fetch serves: the count of lines of code the fragment under that name holds}`."""
    held = collect_fragments.fragments() + collect_fragments.code_only()
    lines = {one.key: len(one.code.content.rstrip().split("\n")) for one in held}
    return {name: lines[key] for name, key in _key_by_name().items()}


def _shared_names() -> dict[str, list[str]]:
    """`{a name reaching more than a single fragment: the keys it reaches}`. A key of the tree is no such name."""
    held = collect_fragments.fragments() + collect_fragments.code_only()
    return {name: sorted(keys) for name, keys in collect_fragments.reach_by_name(held).items() if len(keys) > 1}


def _uncited_one(key: str, one: dict[str, Any], lines: dict[str, int], shared: dict[str, list[str]]) -> list[str]:
    """
    The refusals for a citation. `lines` holds the count of code lines a name reaches. `shared` holds the keys a name
    reaches where it reaches more than a single fragment.
    """
    name = str(one.get("name") or "").strip()
    if name in shared:
        return [_SHARED.format(key=key, name=name[:60], keys=", ".join(f"`{reached}`" for reached in shared[name]))]
    if name not in lines:
        return [_NOT_SHOWN.format(key=key, name=name[:60] or "nothing")]
    first, last = one.get("from"), one.get("to")
    if not isinstance(first, int) or not isinstance(last, int) or not 0 < first <= last <= lines[name]:
        return [_NOT_A_RANGE.format(key=key, name=name[:60], first=first, last=last, lines=lines[name])]
    return []


def _uncited_code(key: str, runs: list[dict[str, Any]]) -> list[str]:
    """
    The refusals for a citation reaching no code.

    A claims reader decides a run against code, and a citation names the fragment and the lines it read. A name no fetch
    reaches cites nothing. A line past the end of that fragment's code cites nothing either.
    """
    lines = _code_lines()
    shared = _shared_names()
    said = []
    for run in runs:
        for held in ("shown_by", "denied_by"):
            for one in run.get(held) or []:
                if isinstance(one, dict):
                    said += _uncited_one(key, one, lines, shared)
                else:
                    said.append(_NOT_AN_OBJECT.format(key=key, what="citation"))
    return said


def _reads() -> set[str]:
    """
    The keys the record at `collect_fragments.CLAIM_RECORD` holds. `_reads` raises where
    `collect_fragments.CLAIM_RECORD` names no record in the environment. `claims_message` writes a record for a fragment
    naming a declaration. `_reads` returns an empty set where `collect_fragments.CLAIM_RECORD` names a missing file.
    """
    path = os.environ.get(collect_fragments.CLAIM_RECORD, "")
    if not path:
        raise ValueError(f"a claims answer arrived, and {collect_fragments.CLAIM_RECORD} names no record")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def _drafts() -> dict[str, list[str]]:
    """
    The drafts the file at `collect_fragments.CLAIM_DRAFT` holds, as `{a key: the prose per site}`. The answer is empty
    where the environment names no such file.
    """
    path = os.environ.get(collect_fragments.CLAIM_DRAFT, "")
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        held: dict[str, list[str]] = json.load(handle)
    return held


def _unpointed(fragment: collect_fragments.Fragment, draft: list[str], runs: list[dict[str, Any]]) -> list[str]:
    """
    Build the refusal for the reads the prose of `draft` does not point at. `draft` holds the new prose per site of
    `fragment`. `runs` holds the runs of the answer. `claim_state.relied_on` keeps the reads those runs cite and the
    questions among the reads. `claim_state.unpointed_reads` decides such a read.

    The refusal gives the path of a file fragment where `collect_fragments.cited_keys` turns that path into the key. A
    paragraph of a markdown document takes the path of the document where `cited_keys` turns that path into a key.
    Otherwise the refusal gives the shortest name `cited_keys` turns into the key. `cited_keys` reads a name as the
    prose of `fragment`. The function raises on a key no such name reaches.
    """
    held = collect_fragments.fragments() + collect_fragments.code_only()
    by_key = {one.key: one for one in held}
    resolves = collect_fragments.resolved(held)
    cited = collect_fragments.cited_keys(fragment, "\n".join(draft), resolves, by_key)
    drafted = dataclasses.replace(
        fragment, references={key: collect_fragments.code_digest(by_key[key]) for key in cited}
    )
    relied = claim_state.relied_on(_reads(), runs, held)
    left = claim_state.unpointed_reads(drafted, relied, held, collect_fragments.keys_by_name(held))
    if not left:
        return []
    names: dict[str, str] = {}
    for key in left:
        path = by_key[key].path
        reached = collect_fragments.cited_keys(fragment, f"`{path}`", resolves, by_key)
        is_a_paragraph = collect_fragments.is_markdown(key) and not collect_fragments.is_a_document(key)
        if (collect_fragments.is_a_file(key) and key in reached) or (is_a_paragraph and reached):
            names[key] = path
    for name, keys in sorted(resolves.items(), key=lambda pair: (len(pair[0]), pair[0])):
        for key in keys:
            if key in left and key not in names:
                if key in collect_fragments.cited_keys(fragment, f"`{name}`", resolves, by_key):
                    names[key] = name
    unnamed = [key for key in left if key not in names]
    if unnamed:
        raise ValueError(f"no name the tree resolves reaches {', '.join(unnamed)}, and `{fragment.key}` read it")
    reads = "\n".join(f"- `{key}`: cite it as `{names[key]}`" for key in left)
    return [_NOT_POINTED.format(key=fragment.key, reads=reads)]


def _unread_names(key: str, runs: list[dict[str, Any]]) -> list[str]:
    """
    The refusals for an `UNVERIFIABLE` run whose `looked_for` names a fragment the reader did not read. `_reads` names
    the fragments the reader read.
    """
    read = _reads()
    named = _key_by_name()
    said = []
    for run in runs:
        if run.get("verdict") != agent_answers.UNVERIFIABLE:
            continue
        cited = collect_fragments.cited_by(str(run.get("looked_for") or ""), key.partition(":")[0])
        for name in sorted(cited):
            held = named.get(name)
            if held is not None and held != key and held not in read:
                said.append(_NOT_FETCHED.format(key=key, name=name, held=held))
    return said


def _findings(
    key: str, one: dict[str, Any], shown: str, prose_by_site: list[str], path: str = ""
) -> tuple[list[str], list[dict[str, str]]]:
    """
    The faults of a verdict, and the changes it asks for.

    A claims reader tiles the prose under `chunks`, and `agent_answers.tiling` reads that tiling through
    `agent_answers.tiled_faults`. `agent_answers.claim_changes` turns a run under `changed` into a change. A critic
    writes its changes under `changes` and tiles nothing.

    `shown` holds the prose the reader read. A tiling joins back into that text. `prose_by_site` holds the prose of the
    fragment's sites, and a change quotes text out of a single site. `path` names the fragment's file.
    """
    chunks = one.get("chunks")
    if chunks is None:
        changes = list(one.get("changes") or [])
        return (_uncited(key, changes, prose_by_site) if changes else []), changes
    runs, faults = agent_answers.tiling(shown, chunks, path)
    changes = agent_answers.claim_changes(runs)
    faults += _uncited_code(key, runs) + _unread_names(key, runs)
    return faults + (_uncited(key, changes, prose_by_site) if changes else []), changes


def _judged(held: dict[str, Any], kept: dict[str, Any]) -> list[str]:
    """
    Give back the refusals for the verdicts a `verdicts` answer holds.

    `_findings` gives back the faults of a verdict and the changes that verdict asks for. A change quotes the text it
    replaces and cites a rule. `apply_prose.draft_refusals` checks the prose those changes leave. A verdict drawing no
    refusal goes into `kept`. `main` hands a kept verdict on in the answer it lets through.

    A verdict holding `chunks` goes through the checks of a claims answer. A verdict without `chunks` goes through those
    of a critic answer.

    A draft from `_drafts` takes the place of the prose the tree holds for its fragment.
    """
    by_key = {one.key: one for one in collect_fragments.fragments()}
    for key, draft in _drafts().items():
        by_key[key] = collect_fragments.drafted(by_key[key], draft)
    said = []
    for key, one in held.items():
        fragment = by_key.get(key)
        if not isinstance(one, dict):
            said.append(f"### {key}\n\n" + _NOT_AN_OBJECT.format(key=key, what="verdict"))
            continue
        if fragment is None:
            said.append(f"### {key}\n\n" + _NOT_A_FRAGMENT.format(key=key))
            continue
        prose_by_site = [site.prose for site in fragment.sites]
        shown = fragment.prose.content
        found, changes = _findings(key, one, shown, prose_by_site, fragment.path)
        draft = apply_prose.applied(prose_by_site, changes) if not found and changes else prose_by_site
        if not found and changes:
            found = apply_prose.draft_refusals(fragment, draft)
        if not found and one.get("chunks") is not None:
            found = _unpointed(fragment, draft, agent_answers.runs_of(one["chunks"]))
        if found:
            said.append(f"### {key}\n\n" + "\n\n".join(found))
        else:
            kept[_VERDICTS][key] = one
    return said


def _batch() -> set[str]:
    """
    The keys of the critic batch `collect_fragments.CRITIC_BATCH` names. The answer is empty where the environment names
    no batch.
    """
    return {key for key in os.environ.get(collect_fragments.CRITIC_BATCH, "").split("\n") if key}


def _batch_faults(verdicts: dict[str, Any], kept: dict[str, Any], batch: set[str]) -> list[str]:
    """
    The refusals for a key of `verdicts` outside `batch`, and for a key of `batch` with no verdict. `kept` holds the
    verdicts earlier calls got right.
    """
    outside, unanswered = agent_answers.batch_mismatch(set(verdicts) | set(kept[_VERDICTS]), batch)
    said = [f"### {key}\n\n" + _OUTSIDE.format(key=key) for key in outside]
    return said + [f"### {key}\n\n" + _UNANSWERED.format(key=key) for key in unanswered]


def _kept_at(agent_id: str) -> str:
    """The file holding what an agent got right."""
    named = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id) or "unnamed"
    return os.path.join(_HELD_IN, f"{named}.json")


def _kept(agent_id: str) -> dict[str, Any]:
    """
    The verdicts and proposals an agent got right on earlier calls. Those calls also left the `_BESIDES` lists and a
    count of refusals.
    """
    path = _kept_at(agent_id)
    empty: dict[str, Any] = {_PROPOSALS: {}, _VERDICTS: {}, **{name: [] for name in _BESIDES}}
    if not os.path.exists(path):
        return empty
    with open(path, encoding="utf-8") as handle:
        held: dict[str, Any] = json.load(handle)
    return {**empty, **held}


def _keeps(agent_id: str, held: dict[str, Any]) -> None:
    """
    Write the kept map into a file named after the agent. The kept map holds verdicts, proposals and a refusal count. It
    also holds the `passed` list and the `out_of_budget` list.
    """
    os.makedirs(_HELD_IN, exist_ok=True)
    with open(_kept_at(agent_id), "w", encoding="utf-8") as handle:
        json.dump(held, handle)


def _drops(agent_id: str) -> None:
    """
    Forget an agent once this hook passes the call the agent settles on. A refused call keeps the agent. `refuse` exits
    first.
    """
    path = _kept_at(agent_id)
    if os.path.exists(path):
        os.remove(path)


def _widened(kept: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    """
    Add to `kept` the lists `_BESIDES` picks out of this call. `_widened` skips an entry that `kept` holds already.
    """
    for name in _BESIDES:
        said = tool_input.get(name)
        if isinstance(said, list):
            kept[name] += [one for one in said if one not in kept[name]]
    return kept


def _proposed(tool_input: dict[str, Any], kept: dict[str, Any]) -> None:
    """
    Keep the proposals `record_run.proposal_refusals` passes, and drop a faulty proposal.

    The amended answer holds a single proposal per rule, the last that passed under it.
    """
    for one in tool_input.get(_PROPOSALS) or []:
        if not record_run.proposal_refusals(one):
            kept[_PROPOSALS][str(one.get("rule", ""))] = one


def _logged(agent_id: str, outcome: str, verdicts: Any, refusals: int) -> None:
    """
    Append a line to `_DECISIONS`. The line names the agent and the outcome. It holds the keys of `verdicts`. It counts
    the refusals the agent has taken.
    """
    keys = sorted(verdicts) if isinstance(verdicts, dict) else []
    record = {"agent": agent_id, "outcome": outcome, "keys": keys, "refusals": refusals}
    os.makedirs(_HELD_IN, exist_ok=True)
    with open(_DECISIONS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


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
    batch = _batch()
    said: list[str] = []
    if isinstance(verdicts, dict) and batch:
        said += _batch_faults(verdicts, kept, batch)
        verdicts = {key: one for key, one in verdicts.items() if key in batch}
    if isinstance(verdicts, dict):
        said += _judged(verdicts, kept)
    # An answer naming no fragment reaches no verdict, and the checks above read a verdict. Such an answer leaves the
    # whole prose unread. `tiled_faults` says that in the words a reader with a gap gets.
    if not said and isinstance(verdicts, dict) and not verdicts and not kept[_VERDICTS] and not kept[_PROPOSALS]:
        said += agent_answers.tiled_faults("", [])
    if said:
        kept[_REFUSALS] = int(kept.get(_REFUSALS, 0)) + 1
        _keeps(agent_id, kept)
        _logged(agent_id, "refused", verdicts, kept[_REFUSALS])
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
        _logged(agent_id, "amended", answer.get(_VERDICTS), int(kept.get(_REFUSALS, 0)))
        refusal.amend(answer, _WHY)
    _logged(agent_id, "passed", verdicts, int(kept.get(_REFUSALS, 0)))


if __name__ == "__main__":
    main()
