# SPDX-License-Identifier: MIT
"""
Read the prose of the tree against the code it documents, and fix what the code contradicts.

`claim_state` holds what a pass settled and decides what falls stale. A run reads the tree, asks `claim_state` which
fragments fell stale, and gives a stale fragment to the claims reader. The reader answers with runs that tile the prose,
and a run states its own verdict.

A run under `changed` goes into the tree, and the fragment falls stale again under its new digest. A run under
`UNVERIFIABLE` sends the fragment into the unsettled map. `claim_state._NOTE` says the author edits such a fragment, and
the pass then reads it again. A fragment at the call cap goes into that map as well. A fragment with no `changed` run
and no `UNVERIFIABLE` run settles. An answer may cite a fetched fragment that its own fragment does not point at. The
fragment of such an answer goes into the unsettled map instead.

A gap between the runs of an answer stops the pass.

The reader fetches another fragment through `.claude/mcp/fetch_fragments.py`. That server appends the key it served to
the file `collect_fragments.CLAIM_RECORD` names. A run names a file per call. The run reads that file once the call
ends, and the keys there become the reads of the call.

`_converge` keeps up to `_AT_ONCE` calls in flight, and launches a call as a slot frees. A write shifts the lines below
it. `_converge` reads the tree again after a write, and the sites take their new lines.

**Usage:** `python3 generator/converge_claims.py <directory> [count]`. The directory takes the journal of calls and the
keys a call fetched. A count caps the fragments the run reads. A `--model` names the model the reader runs. A `--only`
names a file holding a key per line, and the run reads those keys. A key `claim_state` calls unsettled comes back into
the run through such a file. An `--again` names a file of keys in the same form. The run reads those keys again, a
settled key among them. The command stops as a fault where the run leaves unread a key either file names.
"""

import asyncio
import dataclasses
import json
import os
import shutil
import sys
import time
import traceback

from typing import Any

import agent_answers
import agent_calls
import apply_prose
import claim_state
import collect_fragments
import settling_state
import update_ledger_and_queue

# The message a bad call gets.
_USAGE = (
    "usage: converge_claims.py <directory> [count] [--model <name>] [--only <file of keys>] [--again <file of keys>]"
)

# The agent a kind of call runs as. `converge_prose` runs a claims call too.
AN_AGENT = {"claims": "prose-claims"}

# The answer shape a kind of call asks for. `write_agent_prompts` writes the same shape into the agent's definition.
A_SCHEMA = {"claims": agent_answers.CLAIMED}

# The most turns an agent of a kind may take. `generator/agent_calls.py` passes it as `--max-turns`.
# https://code.claude.com/docs/en/agent-sdk/agent-loop counts a turn per round of tool calls.
TURNS = {"claims": 40}

# `_converge` keeps no more calls than this in flight once the first call has ended.
_AT_ONCE = 8

# The calls a fragment may take. A fragment still stale at this count goes to the unsettled map.
_MOST_ROUNDS = 3

# `_settled` hands this reason to `_given_up` where `claim_state.unpointed_reads` reports a read.
_UNCITED = "the prose rests on code it does not cite"

# `_one_call` hands this reason to `_given_up` where `did_the_answers_run_out` finds the agent spent its retries on
# answers a hook refused.
_REFUSED = "the checkers refused the rewrites until the retries ran out"

# The directory under the run's directory taking the keys a call fetched.
_FETCHED = "fetched"

# The journal under the run's directory taking a call's answer. `_taken_over` reads the journal at the head of a run.
_ANSWERS = "claims-answers.jsonl"

# The journal under the run's directory keeping the answers `_answered` takes. A person reads the citations there.
# `_re_read` empties `_ANSWERS` and leaves this journal in place.
_SAID = "claims-said.jsonl"

# The file taking an answer whose runs leave the prose uncovered. A person reads that file, and the next untiled answer
# writes over it through `rewritten`.
_UNTILED = "untiled-answer.json"

# The heading over the declarations the prose names. `.claude/agents/prose-claims.md` explains that section to the
# reader.
_NAMED = "## The declarations the prose names"

# The ask a claims reader gets above its fragment. `.claude/agents/prose-claims.md` says how the reader answers.
_ASK = (
    "Read the prose below against the code below it. Fix a sentence the code contradicts.\n\n"
    "Call `mcp__fetch_fragments__fetch` for a name you cannot settle from the code in front of you. Answer with a "
    "verdict for the key of the fragment."
)


@dataclasses.dataclass
class _Claiming:
    """
    The state the loop of `_converge` holds.

    `by_key` holds the fragments of the tree. `settled` holds the settlements of the store. `_re_read` writes `settled`
    back to the file. `rounds` counts the calls this run has launched over a fragment. An answer the journal hands over
    counts as well. `left` counts the fragments the run may still read. A second read of a fragment takes nothing from
    `left`. `is_budgeted` says whether a count set `left`. `only` names the keys the run reads, and an empty `only`
    opens the run to the whole stale set. `owed` counts the stale fragments and the calls in flight.

    `trail` holds the rewrites a run made to a fragment. A give-up at the call cap hands that trail to the unsettled
    map. A reader that rewrites a fragment and rewrites it back leaves the trail that says so.
    """

    into: str  # `into` names the directory taking the journal of answers and the keys a call fetched.
    caller: agent_calls.Caller
    by_key: dict[str, collect_fragments.Fragment]
    settled: dict[str, claim_state.Verified]
    unsettled: dict[str, Any]
    left: int
    is_budgeted: bool
    only: set[str]
    owed: int = 0
    rounds: dict[str, int] = dataclasses.field(default_factory=dict)
    trail: dict[str, list[Any]] = dataclasses.field(default_factory=dict)
    passed: int = 0
    written: int = 0
    given_up: int = 0

    @property
    def stopped(self) -> str:
        """The reason the run stopped. The empty string `Caller` starts with says the run is still going."""
        return self.caller.stopped

    @property
    def is_resumable(self) -> bool:
        """Whether a later run continues from the stop without a person looking."""
        return self.caller.is_resumable


def _logged(state: _Claiming, what: str) -> None:
    """
    Append a line to the run's log. A reader tails that file while the run moves.

    A line gives the clock, what happened, and the counts. The line goes to standard error, and standard output takes
    the summary.
    """
    budget = f" left={state.left}" if state.is_budgeted else ""
    counted = (
        f"owed={state.owed}{budget} calls={state.caller.calls} passed={state.passed} written={state.written} "
        f"aside={state.given_up} unsettled={len(state.unsettled)}"
    )
    said = f"{time.strftime('%H:%M:%S')} {what} | {counted}"
    with open(os.path.join(state.into, "claims-log.txt"), "a", encoding="utf-8") as handle:
        handle.write(said + "\n")
    print(said, file=sys.stderr, flush=True)


def _stop(state: _Claiming, why: str, is_resumable: bool) -> None:
    """Stop the run, and log the stop. The first reason stays."""
    if agent_calls.try_stopping(state.caller, why, is_resumable):
        _logged(state, f"the run stops: {why}")


def _halted(state: _Claiming, why: str) -> None:
    """
    Stop the run for a signal, and kill the agents in flight. `claim_state.stale_claims` names their fragments again.
    """
    _stop(state, why, True)
    agent_calls.killed(state.caller)


def _given_up(state: _Claiming, key: str, claims: list[Any], why: str) -> None:
    """
    Put `key` into the unsettled map, under `why` and what the run has to show for it. `_applied` hands over the claims
    no fetch settled. `_next_keys` hands over the rewrites. `_one_call` hands over a refusal. `_settled` hands over the
    reads no prose cites. `_written_into_tree` hands over nothing for a fragment the driver cannot write back.
    `_written_into_tree` hands over a critic's rewrite and a claim rewrite where those overlap. The settlement of `key`
    comes out of the store.

    The entry takes the digests `key` held. A claim from `_applied` names the text its reader read. An edit to the
    fragment leaves the entry behind. `claim_state.stale_claims` reports such a fragment, and
    `claim_state.claims_purged` sends it back to the pass.
    """
    state.settled.pop(key, None)
    state.unsettled[key] = {"why": why, "claims": claims, "own": claim_state.digests_of(state.by_key[key])}
    state.given_up += 1
    _logged(state, f"give up on {key}: {why}")


def _record_path(state: _Claiming, key: str) -> str:
    """
    The file taking the keys a call over `key` fetched. A digest of `key` keeps the name short and tells the files
    apart.
    """
    return os.path.join(state.into, _FETCHED, collect_fragments.prose_digest([key]))


def _fetched_by(path: str) -> dict[str, str]:
    """
    `{a key the call fetched: the empty string}`, read off the record at `path`.

    A `path` holding no file answers empty. `_settled` fills in the digests.
    """
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return {line.strip(): "" for line in handle if line.strip()}


def verdict_of(said: Any, key: str) -> dict[str, Any]:
    """
    The findings an answer gives `key`. An answer naming no findings there gives an empty dict.

    Public. `converge_prose` reads a claims answer over a critic's draft this way.
    """
    verdicts = said.get("verdicts") if isinstance(said, dict) else None
    one = (verdicts or {}).get(key) if isinstance(verdicts, dict) else None
    return one if isinstance(one, dict) else {}


def _settled(state: _Claiming, key: str, read: dict[str, str], runs: list[dict[str, Any]]) -> None:
    """
    Put the settlement of the fragment under the parameter `key` into the store. `read` names the fragments the call
    fetched. `claim_state.unpointed_reads` checks those reads. `claim_state.refined` narrows a read of a file to the
    declarations `runs` cite, and the store takes the narrowed reads.

    A read naming a question rather than a fragment takes the digest of that question's answer. `claim_state.answer_sha`
    writes that digest. `claim_state.stale_claims` restales the fragment once that answer moves.
    """
    one = state.by_key[key]
    around = list(state.by_key.values())
    for name in sorted(read):
        if not collect_fragments.is_a_question(name) and name not in state.by_key:
            _stop(state, f"a call over {key} fetched {name}, and the run holds no such fragment", False)
            return
    left = claim_state.unpointed_reads(one, read, around, collect_fragments.keys_by_name(around))
    if left:
        _given_up(state, key, _uncited(left), _UNCITED)
        return
    held = {
        name: (
            claim_state.answer_sha(around, name)
            if collect_fragments.is_a_question(name)
            else collect_fragments.code_digest(state.by_key[name])
        )
        for name in sorted(claim_state.refined(read, runs, around))
    }
    state.settled[key] = claim_state.Verified(claim_state.digests_of(one), state.rounds.get(key, 1), held)
    state.passed += 1
    _logged(state, f"settle {key} over {len(held)} fetch(es)")


def _uncited(left: list[str]) -> list[dict[str, str]]:
    """The claims an unsettled entry holds for the reads `left`. A claim names a read the prose does not cite."""
    return [
        {
            "claim": f"`{name}`",
            "looked_for": f"A reading fetched `{name}`. The prose cites no fragment reaching it, and the code names "
            "none. Cite the fragment a claim of the prose rests on.",
        }
        for name in left
    ]


def _written_into_tree(state: _Claiming, key: str, runs: list[dict[str, Any]]) -> collect_fragments.Fragment | None:
    """
    Write into the tree the changes a reader's runs over `key` ask for. This returns the rewritten fragment, and `None`
    where the write did not happen. The rewritten fragment joins `by_key` under its new key.

    `apply_prose.does_hold` answers no where shaping a site's prose does not give back the lines it came from. Such a
    fragment goes to the unsettled map. `settling_state.last_write` may name the prose pass as the last writer of the
    fragment. A change over text the prose pass rewrote sends the fragment to the unsettled map with both rewrites, and
    a person reviews it. `agent_answers.does_correct_a_rewrite` decides the overlap. A checker that refuses the
    rewritten fragment stops the run. `_stop` hands `agent_calls.try_stopping` an `is_resumable` of false, and the
    driver exits with `FAILED` for a person to investigate. `apply_prose.applied` raises on a change no site holds.
    """
    one = state.by_key[key]
    draft = [site.prose for site in one.sites]
    for site in one.sites:
        if not apply_prose.does_hold(one, site):
            _given_up(state, key, [], f"the driver cannot write it back into {site.path}")
            return None
    placed = agent_answers.claim_changes(runs)
    written = apply_prose.applied(draft, placed)
    found = apply_prose.draft_refusals(one, written)
    if found:
        _stop(state, f"the checkers refused the rewrite of {key}:\n" + "\n\n".join(found), False)
        return None
    critic = settling_state.last_write(state.into, one.prose.sha)
    if critic is not None and critic["run"] == "prose":
        was = collect_fragments.drafted(one, critic["before"]).prose.content
        if agent_answers.does_correct_a_rewrite(runs, agent_answers.rewritten_spans(was, one.prose.content)):
            rewrites = [{"critic_before": critic["before"], "critic_after": draft, "claims_after": written}]
            _given_up(state, key, rewrites, "the claim rewrite changes the critic's rewrite, and a person reviews both")
            return None
    apply_prose.fragment_written(one, written)
    state.trail.setdefault(key, []).extend({"was": said["old"], "wrote": said["new"]} for said in placed)
    state.written += 1
    _logged(state, f"write {key} over {len(placed)} change(s)")
    new_digest = collect_fragments.prose_digest(written)
    settling_state.write_logged(state.into, "claims", one.key, draft, written, new_digest)
    rewritten = collect_fragments.drafted(one, written)
    rewritten.key = collect_fragments.key_for(one.key, new_digest)
    state.by_key[rewritten.key] = rewritten
    return rewritten


def _applied(state: _Claiming, key: str, said: Any, read: dict[str, str]) -> None:
    """
    Take the findings a reader gave `key`, and decide what the fragment takes next.

    The runs tile the prose. A run under `changed` goes into the tree, and a later call reads the fragment again under
    its new digest. `_re_read` hands `_converge` the fragment. A run under `UNVERIFIABLE` goes into the unsettled map. A
    fragment can hold both kinds of run. The rewritten fragment then goes into the unsettled map under its new key. A
    fragment whose runs are sound or `external` goes to `_settled`.

    A tiling with a gap stops the run. `prose_answer` refuses such an answer. Nobody read the text under the gap. A
    reader that cannot tile a fragment breaks the shape this pass rests on, and a person reads such a stop. The run
    writes the answer into the file `_UNTILED` names, and the stop names that file. A later run replays the journal of
    answers rather than that file. `_taken_over` does the replay.
    """
    one = state.by_key[key]
    runs, faults = agent_answers.tiling(one.prose.content, verdict_of(said, key).get("chunks"), one.path)
    if faults:
        where = os.path.join(state.into, _UNTILED)
        settling_state.rewritten(where, [{"key": key, "faults": faults, "said": said}])
        gaps = "\n".join(faults)
        _stop(state, f"the runs of {key} leave the prose uncovered:\n{gaps}\nthe answer sits in {where}", False)
        return
    changes = agent_answers.claim_changes(runs)
    open_claims = [
        {"claim": str(got.get("text") or ""), "looked_for": str(got.get("looked_for") or "")}
        for got in runs
        if got.get("verdict") == agent_answers.UNVERIFIABLE
    ]
    rewritten = _written_into_tree(state, key, runs) if changes else None
    if state.stopped:
        return
    if open_claims:
        held = key if rewritten is None else rewritten.key
        _given_up(state, held, open_claims, f"{len(open_claims)} claim(s) no fetch settled")
    elif not changes:
        relied = claim_state.relied_on(read, runs, list(state.by_key.values()))
        _settled(state, key, {name: read[name] for name in relied}, runs)


def _answered(state: _Claiming, key: str, said: Any, read: dict[str, str]) -> None:
    """
    Append the answer of a call over `key` to the journals `_ANSWERS` and `_SAID`.

    The journal `_ANSWERS` keeps the answers the store has not taken, and a later run applies them through `_taken_over`
    rather than paying for the calls again. `own` names the digests the fragment held for the call.
    """
    one = state.by_key[key]
    record = {"key": key, "own": claim_state.digests_of(one), "said": said, "read": sorted(read)}
    settling_state.appended(os.path.join(state.into, _ANSWERS), [record])
    settling_state.appended(os.path.join(state.into, _SAID), [record])


def _taken_over(state: _Claiming) -> None:
    """
    Apply the answers an earlier run left in the journal.

    An answer names the digests its fragment held. A fragment the tree has moved past drops its answer, and the run
    reads that fragment again. A rewrite that reached the tree moves the digest of a fragment's prose, and the answer of
    that fragment drops here. A fragment `claim_state.stale_claims` does not name drops its answer too.
    """
    held = settling_state.lines_read(os.path.join(state.into, _ANSWERS))
    owed = claim_state.stale_claims(collect_fragments.fragments())
    for one in held:
        key = str(one.get("key") or "")
        got = state.by_key.get(key)
        if key not in owed:
            _logged(state, f"the journal holds an answer over {key}, and the pass owes {key} no reading")
            continue
        if got is None or claim_state.digests_of(got) != one.get("own"):
            _logged(state, f"the journal holds an answer over {key} that the tree has moved past")
            continue
        state.rounds[key] = state.rounds.get(key, 0) + 1
        _applied(state, key, one.get("said"), {name: "" for name in one.get("read") or []})
    if held:
        _logged(state, f"the journal handed the run {len(held)} answer(s)")


def claims_message(one: collect_fragments.Fragment, by_key: dict[str, collect_fragments.Fragment], path: str) -> str:
    """
    The message a claims call over `one` sends. `by_key` holds the fragments of the tree and those
    `collect_fragments.code_only` gives. `path` names the file taking the keys the call fetched.

    The fetch server appends to `path`. A file an earlier call left there would name a fetch this call did not make.
    This deletes that file first.

    The message follows the fragment with the declarations `Fragment.references` names, and `path` takes the keys of
    those declarations. A reader cites such a declaration like a fetched one. A file the prose names stays out of the
    message. The reader fetches such a file, as `.claude/agents/prose-claims.md` directs.

    Public. `converge_prose` reads a critic's draft through this message.
    """
    if os.path.exists(path):
        os.remove(path)
    named = [held for held in one.references if held in by_key and not collect_fragments.is_a_file(held)]
    message = f"{_ASK}\n\n{collect_fragments.said_by(one)}"
    if named:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(f"{held}\n" for held in named)
        message += f"\n{_NAMED}\n\n" + "\n".join(collect_fragments.said_by(by_key[held]) for held in named)
    return message


async def _one_call(state: _Claiming, key: str) -> tuple[str, Any, dict[str, str]]:
    """
    Run a call over the fragment the parameter key names. The answer holds the key, what the call said, and the keys the
    call fetched. `claims_message` writes the message.
    """
    path = _record_path(state, key)
    one = state.by_key[key]
    message = claims_message(one, state.by_key, path)
    counts = (1, len(one.prose.content.encode()))
    said, reply = await agent_calls.spoken(
        state.caller, "claims", message, counts, {collect_fragments.CLAIM_RECORD: path}
    )
    if agent_calls.does_the_reply_stop_the_run(reply):
        _stop(state, str(reply.get("result") or reply.get("api_error_status")), True)
        return key, None, {}
    if said is None and agent_calls.did_the_answers_run_out(reply):
        refused = {"claim": "the rewrite", "looked_for": agent_calls.last_refusal(reply)}
        _given_up(state, key, [refused], _REFUSED)
        return key, None, {}
    if said is None:
        why = str(reply.get("result"))[:160]
        _stop(state, f"the call over {key} died: {why}. {reply.get('transcript')} holds what it streamed", False)
        return key, None, {}
    read = _fetched_by(path)
    _answered(state, key, said, read)
    return key, said, read


def _took(state: _Claiming, got: tuple[str, Any, dict[str, str]] | BaseException) -> None:
    """
    Take the result of a call.

    A call that raises stops the run, and that run cannot resume unless an earlier stop left it resumable.
    `try_stopping` keeps that earlier stop. A call that dies stops the run the same way, and `_one_call` names the
    transcript the call left. A call whose agent spent its retries on refused answers does not stop the run. `_one_call`
    files that fragment as unsettled, and the run goes on.

    A run that stopped on a status of `agent_calls._A_WALL` reads the answers that come back after the refused call. The
    run has already paid for those calls. `agent_calls._spent` records the cost of a call. A run that stopped on a fault
    reads no further answer. A rewrite from an answer read ahead of the fault has already reached the tree.
    """
    if isinstance(got, BaseException):
        _stop(state, "a call raised:\n" + "".join(traceback.format_exception(got)), False)
        return
    key, said, read = got
    if said is None or state.stopped and not state.is_resumable:
        return
    _applied(state, key, said, read)


def _next_keys(state: _Claiming, stale: dict[str, str], flying: set[str], room: int) -> list[str]:
    """
    The keys the next calls read. This takes them out of `stale` in key order, and stops at `room` keys. A key in
    `flying` stays in `stale`.

    A fragment new to the run needs room in the budget of the run. A fragment that has had `_MOST_ROUNDS` calls goes to
    the unsettled map with its trail.
    """
    taken: list[str] = []
    for key in sorted(stale):
        if len(taken) == room:
            break
        if key in flying:
            continue
        is_fresh = key not in state.rounds
        if is_fresh and state.left <= 0:
            continue
        del stale[key]
        if state.rounds.get(key, 0) >= _MOST_ROUNDS:
            _given_up(state, key, state.trail.get(key, []), "the calls ran out and the prose was still moving")
            continue
        state.left -= is_fresh
        state.rounds[key] = state.rounds.get(key, 0) + 1
        taken.append(key)
    return taken


def _re_read(state: _Claiming) -> dict[str, str]:
    """
    Read the tree again, and answer with the fragments that fell stale.

    A write moved the lines below it. Reading the tree again gives the sites their new lines.
    `claim_state.claims_purged` decides the answer, and the store drops the entries in it.

    The store takes what the run has settled. The journal of answers then empties.

    `by_key` takes the fragments of `collect_fragments.code_only` as well. A reader fetches such a fragment, and
    `_settled` looks the fetched key up here. Such a fragment holds no prose, and the stale set leaves it out.

    A run naming keys drops the rest of the stale set.
    """
    held = collect_fragments.fragments()
    state.by_key = {one.key: one for one in held + collect_fragments.code_only()}
    claim_state.claims_written(state.settled, state.unsettled, state.by_key)
    settling_state.rewritten(os.path.join(state.into, _ANSWERS), [])
    found = claim_state.claims_purged(held)
    for key in found:
        state.settled.pop(key, None)
        state.unsettled.pop(key, None)
    if not state.only:
        return found
    return {key: why for key, why in found.items() if key in state.only}


async def _converge(state: _Claiming) -> None:
    """
    Read the stale fragments with up to `_AT_ONCE` calls in flight. A call that ends frees its slot, and `_next_keys`
    fills the slot. The run ends where no call is in flight and `_next_keys` takes no key. A stop launches no further
    call, and the run ends once the calls in flight end.

    A write moves the lines below it. The run reads the tree again after a write, ahead of the next answer. The next
    write then lands on the lines the file holds.

    The run waits for the first call before it launches a second. The first call writes the cached prefix the later
    calls read. https://platform.claude.com/docs/en/build-with-claude/prompt-caching describes that prefix.
    """
    agent_calls.stops_on_signals(lambda why: _halted(state, why))
    _taken_over(state)
    stale = _re_read(state)
    flying: dict[asyncio.Task[tuple[str, Any, dict[str, str]]], str] = {}
    is_warm = False
    while True:
        state.owed = len(stale) + len(flying)
        if not state.stopped:
            room = (_AT_ONCE if is_warm else 1) - len(flying)
            for key in _next_keys(state, stale, set(flying.values()), room):
                flying[asyncio.create_task(_one_call(state, key))] = key
                _logged(state, f"read {key}")
        if not flying:
            break
        done, _ = await asyncio.wait(flying, return_when=asyncio.FIRST_COMPLETED)
        is_warm = True
        for task in done:
            key = flying.pop(task)
            written = state.written
            _took(state, task.exception() or task.result())
            stale.pop(key, None)
            if state.written > written:
                stale = _re_read(state)
    agent_calls.killed(state.caller)
    _re_read(state)


def _keys_named(argv: list[str], flag: str) -> tuple[set[str], list[str]]:
    """
    Read keys from the file that follows `flag` in `argv`. That file holds a key per line. Hand back the keys and `argv`
    with `flag` and the file removed. An `argv` without `flag` yields no key. A `flag` with nothing after it stops the
    command with the usage line.
    """
    if flag not in argv:
        return set(), argv
    at = argv.index(flag)
    if at + 1 >= len(argv):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    with open(argv[at + 1], encoding="utf-8") as handle:
        keys = {key.strip() for key in handle if key.strip()}
    return keys, argv[:at] + argv[at + 2 :]


def main() -> None:
    """
    Read the claims of the tree under the run lock, and print what the run did.

    The run leaves `agent_calls.RESUMABLE` where a later run can continue from the stop. The run leaves
    `agent_calls.FAILED` where a person has to investigate the stop. A second run raises at the lock rather than writing
    the store beside the first.
    """
    argv = sys.argv[1:]
    model = ""
    if "--model" in argv:
        at = argv.index("--model")
        if at + 1 >= len(argv):
            print(_USAGE, file=sys.stderr)
            sys.exit(2)
        model = argv[at + 1]
        argv = argv[:at] + argv[at + 2 :]
    only, argv = _keys_named(argv, "--only")
    again, argv = _keys_named(argv, "--again")
    only |= again
    if len(argv) not in (1, 2):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    into = argv[0]
    with settling_state.run_locked(into):
        os.makedirs(os.path.join(into, _FETCHED), exist_ok=True)
        held = collect_fragments.fragments()
        print(update_ledger_and_queue.updated(into, held), file=sys.stderr)
        settled, unsettled = claim_state.read_claims()
        lost = sorted(key for key in only if key not in {one.key for one in held})
        if lost:
            print(f"the tree holds no fragment under {', '.join(lost)}", file=sys.stderr)
            sys.exit(2)
        by_key = {one.key: one for one in held}
        for key, left in claim_state.unpointed_settlements(held).items():
            settled.pop(key, None)
            unsettled[key] = {"why": _UNCITED, "claims": _uncited(left), "own": claim_state.digests_of(by_key[key])}
        for key in only:
            unsettled.pop(key, None)
        for key in again:
            settled.pop(key, None)
        state = _Claiming(
            into=into,
            caller=agent_calls.Caller(
                into=into,
                model=model,
                began=time.strftime("%Y-%m-%dT%H:%M:%S"),
                agents=AN_AGENT,
                schemas=A_SCHEMA,
                turns=TURNS,
            ),
            by_key={one.key: one for one in held + collect_fragments.code_only()},
            settled=settled,
            unsettled=unsettled,
            left=int(argv[1]) if len(argv) == 2 else len(only or held),
            is_budgeted=len(argv) == 2,
            only=only,
        )
        try:
            asyncio.run(_converge(state))
        except Exception as caught:  # failure-is-reported: the stop reason; pylint: disable=broad-exception-caught
            agent_calls.killed(state.caller)
            _stop(state, f"the run ended on {type(caught).__name__}: {caught}", False)
            claim_state.claims_written(state.settled, state.unsettled, state.by_key)
        finally:
            agent_calls.killed(state.caller)
        shutil.rmtree(os.path.join(into, _FETCHED), ignore_errors=True)
    print(
        json.dumps(
            {
                "settled": state.passed,
                "written": state.written,
                "unsettled": state.given_up,
                "calls": state.caller.calls,
                "spent": agent_calls.summed(state.caller),
                "stopped": state.stopped,
            },
            indent=2,
        )
    )
    if state.stopped:
        print(f"this run stopped: {state.stopped}", file=sys.stderr)
        sys.exit(agent_calls.RESUMABLE if state.is_resumable else agent_calls.FAILED)
    unread = sorted(key for key in only if key not in state.rounds)
    if unread:
        print(f"this run read none of {', '.join(unread)}, and `--only` or `--again` names them", file=sys.stderr)
        sys.exit(agent_calls.FAILED)


if __name__ == "__main__":
    main()
