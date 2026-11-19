# SPDX-License-Identifier: MIT
"""
Settle the prose of the tree between the critic and the comparator.

`DESIGN.md` describes the pass under `Settling the prose`. `settling_state` names the files that hold its state. A run
takes the run lock, reads the tree, and calls `update_ledger_and_queue.updated`. The run then goes through passes. A
pass resolves a round the comparator decided. A pass with no such round takes a result off the completed tasks queue and
reads it. A pass with no result to read tries to create a task. The pass then launches a pending task while a slot is
free. An idle pass with no running task ends the run. After any other idle pass, the run waits for a task to finish, for
at most `_A_WAIT` seconds.

A task runs an agent as a subprocess of the Claude command. The agent reads its batch over standard input and answers
JSON. The task puts that JSON on the completed tasks queue.

**Usage:** `python3 generator/converge_prose.py <directory> [count]`. The directory holds the settling queues. A count
caps the unexamined fragments the run takes. A `--model` names the model the agents run.
"""

import asyncio
import dataclasses
import faulthandler
import functools
import json
import os
import random
import signal
import sys
import time

from typing import Any

import agent_answers
import agent_calls
import apply_prose
import collect_fragments
import converge_claims
import record_run
import settling_state
import update_ledger_and_queue

# The message a bad call gets.
_USAGE = "usage: converge_prose.py <directory> [count] [--model <name>]"

# The most fragments a critic reads at once. A critic reading a longer batch spends fewer output tokens per fragment,
# and a fragment late in the batch gets the thinner reading.
_A_CRITIC_BATCH = 5

# The changes a comparator judges at once. This size and `_A_CRITIC_BATCH` aim at about the same cost per call.
_A_COMPARE_BATCH = 60

# The most turns an agent of a kind may take. A turn is a tool-use round trip, as
# https://code.claude.com/docs/en/agent-sdk/agent-loop#turns-and-messages says.
_TURNS = {"critic": 5, "compare": 5, "condense": 5, **converge_claims.TURNS}

# The tasks a run keeps in flight. The pending tasks queue holds no more than this many tasks either.
_MOST_AGENTS = 8

# The critic rounds a fragment may take. A fragment at this count goes to the unsettled prose queue.
_MOST_ROUNDS = 10

# The seconds without an answer before the run sends a fresh seed. The run seeds only while work is pending and the
# running agents leave a pair of slots free.
_AN_IDLE_GAP = 300.0

# The seconds a pass may take. A pass writes a decided round into the tree, reads a completed result, or plans and
# launches tasks. A pass starts a task rather than waiting for one. A pass past this budget counts as hung. A timer
# interrupts such a pass with `_Overran`, and the run stops with `agent_calls.FAILED` for a person to investigate.
_A_PASS_BUDGET = 10.0

# The seconds the run waits for a task before it looks at the state again. The run notices a stop request when this wait
# ends.
_A_WAIT = 5.0

# The directory under the settling directory taking the record file and the draft file of a claims task.
_CHECKED = "checked"

# The agent a kind of call runs as.
_AN_AGENT = {
    "critic": "prose-critic",
    "compare": "prose-compare",
    "condense": "prose-condense",
    **converge_claims.AN_AGENT,
}

# The passes in a row that leave `_progress` where it was before the run stops. A pass queueing a task while the slots
# are full moves nothing. The pending tasks queue holds no more tasks than the slots hold. This budget sits well above
# that depth. A run past it is going round without doing anything.
_MOST_IDLE_PASSES = 100

# The ask a comparator reads above its batch. `.claude/agents/prose-compare.md` says how a comparator answers.
_JUDGE = (
    "Judge which version of a piece of prose reads better. Do not judge whether it is true.\n\n"
    "A pair opens with an id. Below the id come `A` and a text, then `B` and a text. A coin decided which side "
    "holds the earlier draft. The verdict on a pair tells you nothing about the next pair.\n\n"
    "Answer with a verdict per pair of the batch. The schema names the verdicts."
)

# A seed pair a comparator judges ahead of a batch. The loop drops the answer.
_SEED_CHANGE = "### seed\n\n#### A\nA seed.\n\n#### B\nA seed.\n"

# The coins pick `is_old_first` for a `Change` that `_read_critic` builds.
_COINS = random.Random()

# The ask a condense reads. `record_run.proposals_appended` writes the condensed proposals into `PENDING`.
_CONDENSE = (
    "Condense the proposals below into a shorter list. Do not judge the prose they came from.\n\n"
    "The conventions come below. The proposals the author turned down follow those. The proposals awaiting a "
    "ruling follow those. The proposals of this run come last. A pair of proposals stating the same rule "
    "becomes a single proposal. A proposal an accepted rule already covers comes out. A proposal the author has "
    "turned down comes out. A proposal the pending list already holds comes out too. State a rule a checker can "
    "decide.\n\n"
    "State a rule the way `.claude/conventions.md` states one. An accepted proposal lands in that file, and the "
    "checkers read the wording there. Write a hyphenated name, then what the checker refuses. The word rules refuse "
    "a universal and a count, and they read your wording too. `refuses a sentence ending on a verb` passes where "
    "`refuses every sentence that ends on a verb` gets turned back.\n\n"
    "A proposal states a pair of quotes. `before` is prose the proposed checker refuses. `after` rewrites that prose "
    "into the form the checker passes. A merged proposal keeps the pair of the proposal whose rule you kept."
)

# The answer shape a kind of call asks for. `write_agent_prompts` writes the same shape into the agent's definition.
_A_SCHEMA = {
    "critic": agent_answers.EXAMINED,
    "compare": agent_answers.JUDGED,
    "condense": agent_answers.CONDENSED,
    **converge_claims.A_SCHEMA,
}


class _Overran(Exception):
    """A pass took longer than its budget. The timer raises this, and the run stops."""


@dataclasses.dataclass
class _Settling:
    """
    The state a pass reads and writes. The loop holds this state. A running task reads `caller` and `into`. A running
    critic task also reads `queue` and `by_digest`. The task counts its call in `caller.calls` through
    `agent_calls.spoken`. The task assigns no field of `_Settling`.

    `queue` holds the prose queue as the file holds it. `busy` holds the digests and the change ids a pending or running
    task holds. `fresh` counts the unexamined fragments the run may still give a critic. `caller` runs the agents.
    `by_key` holds the fragments of the tree. `by_key` also holds the code with no prose. A claims message names a
    declaration from there.
    """

    into: str  # the directory holding the settling queues.
    caller: agent_calls.Caller
    parent: int  # the process that started the run. The run stops once that process goes away.
    by_digest: dict[str, collect_fragments.Fragment]
    queue: dict[str, settling_state.ProseState | str]
    fresh: int
    by_key: dict[str, collect_fragments.Fragment]
    pending: list[settling_state.Task] = dataclasses.field(default_factory=list)
    running: dict[str, tuple[settling_state.Task, Any]] = dataclasses.field(default_factory=dict)
    busy: set[str] = dataclasses.field(default_factory=set)
    tasks_made: int = 0
    seeds: int = 0
    approved: int = 0
    written: int = 0
    given_up: int = 0
    answered_at: float = 0.0
    idle_passes: int = (
        # the passes since the last pass that moved `_progress`. A pass that says it did nothing does not change the
        # count.
        0
    )

    @property
    def stopped(self) -> str:
        """The reason the run stopped. The value comes from `Caller`. The empty string says the run is still going."""
        return self.caller.stopped

    @property
    def is_resumable(self) -> bool:
        """
        Whether a later run continues from where this run stopped without a person looking. The `Caller` holds the
        answer.
        """
        return self.caller.is_resumable


def _ruled() -> str:
    """The text of the proposals awaiting a ruling. A condense reads that text and states no proposal already there."""
    if not os.path.exists(settling_state.PENDING):
        return ""
    with open(settling_state.PENDING, encoding="utf-8") as handle:
        return handle.read()


def _undecided(entry: settling_state.ProseState) -> list[settling_state.Change]:
    """The changes of `entry` a comparator has yet to judge."""
    return [one for one in entry.changes if one.verdict is None]


def _is_waiting_for_critic(entry: settling_state.ProseState | str) -> bool:
    """
    Whether a critic reads the fragment next. An unexamined fragment waits for a critic. A dirty draft waits too, unless
    a change of that draft still lacks a verdict or a claims reader still checks its `proposed` prose.
    """
    if entry == settling_state.UNEXAMINED:
        return True
    return (
        isinstance(entry, settling_state.ProseState) and entry.is_dirty and not _undecided(entry) and not entry.proposed
    )


def _does_a_pass_take(entry: settling_state.ProseState) -> bool:
    """
    Whether a pass of the run can take `entry` up.

    A critic reads an entry waiting for one. An entry may hold changes. `_try_writing` closes the round of such an entry
    once no change lacks a verdict. A claims reader checks an entry holding `proposed` prose. `_stored` refuses to queue
    any other entry.
    """
    return _is_waiting_for_critic(entry) or bool(entry.changes) or bool(entry.proposed)


def _state_of(state: _Settling, digest: str) -> settling_state.ProseState:
    """The state of `digest`. An unexamined fragment takes its draft from the tree."""
    entry = state.queue[digest]
    if isinstance(entry, settling_state.ProseState):
        return entry
    fragment = state.by_digest[digest]
    return settling_state.ProseState(fragment.key, [site.prose for site in fragment.sites], 0, True, [])


def _counted(state: _Settling) -> str:
    """The counts of the run, in the form that ends a log line."""
    waiting = [digest for digest, entry in state.queue.items() if _is_waiting_for_critic(entry)]
    undecided = [
        one
        for entry in state.queue.values()
        if isinstance(entry, settling_state.ProseState)
        for one in _undecided(entry)
    ]
    kinds = [task.kind for task, _runner in state.running.values()]
    return (
        f"queue={len(state.queue)} waiting={len(waiting)} undecided={len(undecided)} pending={len(state.pending)} "
        f"critics={kinds.count('critic')} compares={kinds.count('compare')} calls={state.caller.calls} "
        f"approved={state.approved} written={state.written} unsettled={state.given_up}"
    )


def _logged(state: _Settling, what: str) -> None:
    """
    Append a line to the run's log.

    A line gives the clock, what happened, and the counts of the run. The line goes to standard error, and standard
    output takes the summary `converge_prose.main` prints.
    """
    said = f"{time.strftime('%H:%M:%S')} {what} | {_counted(state)}"
    with open(os.path.join(state.into, "log.txt"), "a", encoding="utf-8") as handle:
        handle.write(said + "\n")
    print(said, file=sys.stderr, flush=True)


def _stop(state: _Settling, why: str, is_resumable: bool) -> None:
    """
    Log a reason and stop the run. A second call to `_stop` records no reason of its own. A process alive
    `_MOST_STOPPING` seconds after the first call dumps its thread tracebacks to standard error. The process then exits
    with status 1, as https://docs.python.org/3/library/faulthandler.html#faulthandler.dump_traceback_later says.
    """
    if agent_calls.try_stopping(state.caller, why, is_resumable):
        _logged(state, f"the run stops: {why}")
        faulthandler.dump_traceback_later(_MOST_STOPPING, exit=True)


# The seconds a stopped run may take to exit.
_MOST_STOPPING = 60.0


def _stored(state: _Settling, digest: str, entry: settling_state.ProseState | str) -> None:
    """
    Put `entry` in the prose queue under `digest`.

    This raises on an entry `_does_a_pass_take` answers false for. A fragment with nothing left to settle goes to the
    ledger through `_approved`, or to the unsettled prose queue through `_given_up`.
    """
    if isinstance(entry, settling_state.ProseState) and not _does_a_pass_take(entry):
        draft = "dirty" if entry.is_dirty else "clean"
        raise AssertionError(
            f"the prose queue takes {entry.key} at round {entry.rounds} and no pass advances it. "
            f"the draft is {draft} and holds {len(entry.changes)} change(s)."
        )
    state.queue[digest] = entry
    settling_state.prose_state_written(state.into, digest, entry)


def _dropped(state: _Settling, digest: str) -> None:
    """Take `digest` out of the prose queue."""
    state.queue.pop(digest, None)
    settling_state.prose_state_written(state.into, digest, None)


def _approved(state: _Settling, digest: str) -> None:
    """Put `digest` into the ledger, and take it out of the prose queue."""
    settling_state.appended(settling_state.LEDGER, [digest])
    key = _state_of(state, digest).key
    _dropped(state, digest)
    state.approved += 1
    _logged(state, f"approve {key}")


def _given_up(state: _Settling, digest: str, why: str) -> None:
    """Put `digest` into the unsettled prose queue, and take it out of the prose queue."""
    key = _state_of(state, digest).key
    settling_state.given_up(state.into, digest)
    _dropped(state, digest)
    state.given_up += 1
    _logged(state, f"give up on {key}: {why}")


def _proposed(state: _Settling, raised: list[Any]) -> None:
    """Append the proposals a critic raised to the suggested proposals queue."""
    if raised:
        settling_state.appended(settling_state.SUGGESTED, raised)
        _logged(state, f"{len(raised)} proposal(s) suggested")


def _placed(draft: list[str], changes: list[Any]) -> list[dict[str, str]]:
    """
    The changes of `changes` that fit together. This leaves out a change whose run overlaps a change already placed.

    A change quoting text no site of the draft holds raises. `prose_answer` refuses such a change before it reaches
    here.
    """
    text = "\n".join(draft)
    held: list[dict[str, str]] = []
    runs: list[tuple[int, int]] = []
    for said in changes:
        if not isinstance(said, dict):
            raise ValueError(f"a critic's change is a {type(said).__name__}, and the schema asks for an object")
        old = str(said.get("old") or "")
        if not apply_prose.is_held(draft, old):
            raise ValueError(f"a critic's change quotes text no site holds: {old[:80]!r}")
        at = text.find(old)
        if any(start < at + len(old) and at < end for start, end in runs):
            continue
        runs.append((at, at + len(old)))
        held.append({"old": old, "new": str(said.get("new") or ""), "rule": str(said.get("rule") or "")})
    return held


def _sorted(said: Any) -> dict[str, Any]:
    """
    The verdicts of `said`, in the lists a caller reads.

    An answer outside the schema raises. A `rewritten` verdict citing no change raises. A verdict the schema does not
    name raises. Proposals past `agent_answers.MOST_PROPOSALS` raise.
    """
    if not isinstance(said, dict):
        raise ValueError(f"a critic answered a {type(said).__name__}, and the schema asks for an object")
    raised = said.get("proposals", [])
    verdicts = said["verdicts"]
    if not isinstance(raised, list) or not isinstance(verdicts, dict):
        raise ValueError(f"a critic answered outside the schema: {json.dumps(said)[:200]}")
    if len(raised) > agent_answers.MOST_PROPOSALS:
        raise ValueError(f"a critic raised {len(raised)} proposals, past the cap of {agent_answers.MOST_PROPOSALS}")
    held: dict[str, Any] = {"passed": [], "rewritten": [], "out_of_budget": [], "proposals": raised}
    for key, one in verdicts.items():
        if not isinstance(one, dict):
            raise ValueError(
                f"a critic's verdict over {key} is a {type(one).__name__}, and the schema asks for an object"
            )
        verdict, changes = one.get("verdict"), one.get("changes")
        if verdict == "rewritten":
            if not (isinstance(changes, list) and changes):
                raise ValueError(f"a critic rewrote {key} and cited no change")
            held["rewritten"].append({"key": key, "changes": changes})
        elif verdict == "passed":
            held["passed"].append(key)
        elif verdict == "out_of_budget":
            held["out_of_budget"].append(key)
        else:
            raise ValueError(f"a critic's verdict over {key} is {verdict!r}, and the schema names no such verdict")
    return held


def _keyed_batch(state: _Settling, task: settling_state.Task) -> dict[str, str]:
    """Map the fragment keys of a critic task to their digests. A digest that waits for no critic stays out."""
    held = {}
    for digest in task.items:
        entry = state.queue.get(digest)
        if entry is not None and _is_waiting_for_critic(entry):
            held[_state_of(state, digest).key] = digest
    return held


def _is_heard(task_keys: dict[str, str], said: Any) -> bool:
    """
    Whether a critic answer passes or rewrites a fragment of its batch. A critic speaking for none told the loop
    nothing.
    """
    answer = _sorted(said)
    keys = list(answer["passed"]) + [one["key"] for one in answer["rewritten"]]
    return any(key in task_keys for key in keys)


def _is_refused(state: _Settling, task_keys: dict[str, str], answer: dict[str, Any]) -> bool:
    """
    Whether the checkers refuse a critic's answer. A refusal stops the run as a fault.

    The checkers read the proposals. The checkers also read a rewritten draft with the placed changes applied.
    """
    found = [refused for one in answer["proposals"] for refused in record_run.proposal_refusals(one)]
    rewritten = {one["key"]: one["changes"] for one in answer["rewritten"]}
    for key, digest in task_keys.items():
        if key in rewritten:
            draft = _state_of(state, digest).draft
            found += apply_prose.draft_refusals(
                state.by_digest[digest], apply_prose.applied(draft, _placed(draft, rewritten[key]))
            )
    if found:
        _stop(state, "the checkers refused an answer of the critic:\n" + "\n\n".join(found), False)
    return bool(found)


def _read_critic(state: _Settling, task_keys: dict[str, str], said: Any, told: str = "") -> None:
    """
    Take a critic's answer.

    A passed fragment goes into the ledger. A rewritten fragment counts a round and keeps its changes. A faulty answer
    stops the run and changes no fragment. The checkers may refuse the answer. The answer may speak for a key outside
    its batch. It may give a fragment of the batch no verdict. The draft may not bear out a change the answer makes. The
    stop of an answer that does not match its batch quotes `told`. `_read` passes the `said` of a `TaskResult` as
    `told`. `said` holds the start of the reply and names the transcript of the call where the reply has one.
    """
    answer = _sorted(said)
    if _is_refused(state, task_keys, answer):
        return
    passed = set(answer["passed"])
    rewritten = {one["key"]: one["changes"] for one in answer["rewritten"]}
    spoken = passed | set(rewritten) | set(answer["out_of_budget"])
    outside, unanswered = agent_answers.batch_mismatch(spoken, set(task_keys))
    faults = [f"{key} sits outside the batch" for key in outside] + [f"{key} got no verdict" for key in unanswered]
    for key, changes in sorted(rewritten.items()):
        if key in task_keys:
            placed = _placed(_state_of(state, task_keys[key]).draft, changes)
            if len(placed) < len(changes):
                faults.append(f"{key}: the draft bears out {len(placed)} of {len(changes)} change(s)")
    if faults:
        _stop(state, "a critic answer does not match its batch: " + "; ".join(faults) + f". {told}", False)
        return
    _proposed(state, answer["proposals"])
    for key, digest in task_keys.items():
        if key in passed:
            _approved(state, digest)
            continue
        entry = _state_of(state, digest)
        entry.rounds += 1
        suggested = rewritten.get(key, [])
        placed = _placed(entry.draft, suggested)
        entry.changes = [
            settling_state.Change(
                f"{digest}:{entry.rounds}:{at}", one["old"], one["new"], one["rule"], _COINS.random() < 0.5
            )
            for at, one in enumerate(placed)
        ]
        entry.is_dirty = not placed
        _stored(state, digest, entry)


def _written_into_tree(state: _Settling, digest: str, entry: settling_state.ProseState, draft: list[str]) -> str:
    """
    Write `draft` into the tree for the fragment of `digest`, and answer with the new digest. An empty answer says the
    run stopped.

    The fragment's digest in the tree must still be `digest`. `collect_fragments.flattened` takes the wrapping out
    before the digest. A rewrap keeps that digest, and an edit to the words moves it. A moved digest stops the run.

    The write asks `apply_prose.does_hold` of the fragment's sites. A comment sharing a line with code in a language of
    `_REFLOWED` answers yes. For another site, `does_hold` shapes the site's prose and compares the result with the
    lines in the file. A mismatch answers no. `_json_note` shapes a JSON note. A no from any site stops the run. A
    refusal from `apply_prose.draft_refusals` stops the run.
    """
    fragment = state.by_digest.get(digest)
    if fragment is None or len(draft) != len(fragment.sites):
        _stop(state, f"the run holds no fragment of {len(draft)} site(s) for {entry.key}", False)
        return ""
    if collect_fragments.located(fragment).prose.sha != digest:
        _stop(state, f"the prose of {entry.key} moved in the tree while the run held it", False)
        return ""
    for site in fragment.sites:
        if not apply_prose.does_hold(fragment, site):
            _stop(state, f"the driver cannot write {entry.key} back into {site.path}", False)
            return ""
    found = apply_prose.draft_refusals(fragment, draft)
    if found:
        _stop(state, f"the checkers refused the rewrite of {entry.key}:\n" + "\n\n".join(found), False)
        return ""
    apply_prose.fragment_written(fragment, draft)
    new_digest = collect_fragments.prose_digest(draft)
    before = [site.prose for site in fragment.sites]
    settling_state.write_logged(state.into, "prose", fragment.key, before, draft, new_digest)
    rewritten = collect_fragments.drafted(fragment, draft)
    rewritten.key = collect_fragments.key_for(fragment.key, new_digest)
    del state.by_digest[digest]
    state.by_digest[new_digest] = rewritten
    entry.key = rewritten.key
    state.written += 1
    _logged(state, f"write {rewritten.key}")
    return new_digest


def _resolved(state: _Settling, digest: str, entry: settling_state.ProseState) -> None:
    """
    Close a round of `entry`. The comparator has judged the changes of that round.

    A draft the accepted changes alter waits in `proposed` for a claims reader. `_read_claims` writes that draft into
    the tree unless the reader changes the rewritten text. A clean fragment with no accepted change goes into the
    ledger. A draft the accepted changes leave unaltered waits for a critic where the fragment is dirty or holds an
    accepted change. `_resolved` marks that fragment dirty.
    """
    accepted = [dataclasses.asdict(one) for one in entry.changes if one.verdict == "accept"]
    draft = apply_prose.applied(entry.draft, accepted)
    entry.changes = []
    if draft == entry.draft:
        if entry.is_dirty or accepted:
            entry.is_dirty = True
            _stored(state, digest, entry)
        else:
            _approved(state, digest)
        return
    entry.proposed = draft
    _stored(state, digest, entry)


def _read_claims(state: _Settling, task: settling_state.Task, said: Any) -> None:
    """
    Take a claims reader's answer over the `proposed` prose of a fragment.

    A reader may mark a `changed` run over rewritten text. Such a run says the draft contradicts the code. The fragment
    then keeps its old prose and joins the unsettled prose queue. `settling_state.review_logged` saves the old prose,
    the draft and the reader's corrected draft for a person to read.

    A reader marking no such run sends the draft into the tree. The function drops a `changed` run over kept text.
    `claim_state.stale_claims` names the written fragment to `converge_claims`. The fragment moves to its new digest and
    waits for a critic.

    A fault in the tiling stops the settling run. A write that stops the settling run leaves the fragment in place. The
    function logs and drops a result over a fragment the prose queue holds no draft for.
    """
    digest = task.items[0]
    entry = state.queue.get(digest)
    if not isinstance(entry, settling_state.ProseState) or not entry.proposed:
        _logged(state, f"drop a claims result over {digest}: the prose queue holds no draft there")
        return
    drafted = collect_fragments.drafted(state.by_digest[digest], entry.proposed)
    chunks = converge_claims.verdict_of(said, entry.key).get("chunks")
    runs, faults = agent_answers.tiling(drafted.prose.content, chunks, drafted.path)
    if faults:
        _stop(state, f"the runs of {entry.key} leave the draft uncovered:\n" + "\n".join(faults), False)
        return
    changes = agent_answers.claim_changes(runs)
    spans = agent_answers.rewritten_spans(state.by_digest[digest].prose.content, drafted.prose.content)
    if agent_answers.does_correct_a_rewrite(runs, spans):
        claimed = apply_prose.applied(entry.proposed, changes)
        settling_state.review_logged(state.into, entry.key, entry.draft, entry.proposed, claimed)
        _given_up(state, digest, "a claims reader changed the critic's draft")
        return
    new_digest = _written_into_tree(state, digest, entry, entry.proposed)
    if not new_digest:
        return
    entry.draft, entry.proposed, entry.is_dirty = entry.proposed, [], True
    _dropped(state, digest)
    _stored(state, new_digest, entry)


def _read_compare(state: _Settling, task: settling_state.Task, said: Any) -> None:
    """
    Take a comparator's answer.

    A tie rejects the change. A change with no verdict in a batch of more than a single change goes to a task of its
    own. The driver rejects a change with no verdict in a single-change batch. A change that already has a verdict is
    stale. A verdict the schema does not name raises.
    """
    verdicts = {}
    answered = said.get("verdicts") if isinstance(said, dict) else None
    for got in answered if isinstance(answered, list) else []:
        if isinstance(got, dict) and got.get("pair"):
            verdicts[got["pair"]] = got.get("verdict")
    touched: dict[str, settling_state.ProseState] = {}
    records = []
    by_id = _changes_by_id(state)
    for change_id in task.items:
        if change_id not in by_id:
            continue
        digest, entry, change = by_id[change_id]
        side = verdicts.get(change_id)
        new_side = agent_answers.SIDES[int(change.is_old_first)]
        if side in agent_answers.SIDES:
            change.verdict = "accept" if side == new_side else "reject"
        elif side == agent_answers.A_TIE:
            change.verdict = "reject"
        elif side is not None:
            raise ValueError(f"the schema names no verdict {side!r}. the comparator gave it to {change_id}")
        elif len(task.items) > 1:
            change.is_asked_alone = True
        else:
            change.verdict = "reject"
        touched[digest] = entry
        if change.verdict:
            records.append(
                {
                    "key": entry.key,
                    "round": entry.rounds,
                    "rule": change.rule,
                    "old": change.old,
                    "new": change.new,
                    "verdict": change.verdict,
                }
            )
    if records:
        record_run.rule_use_counted(records)
    # The verdicts go into the queue before any write. A write that stops the run then loses no verdict. `_try_writing`
    # takes the rounds these verdicts close.
    for digest, entry in touched.items():
        _stored(state, digest, entry)


def _died(state: _Settling, task: settling_state.Task, said: str) -> None:
    """
    Stop the run on a dead call, as a fault.
    """
    _stop(state, f"a {task.kind} call over {len(task.items)} item(s) died: {said}", False)


def _read(state: _Settling, result: settling_state.TaskResult) -> None:
    """
    Read a task result `_try_pass` took off the completed tasks queue. A stale critic result frees its items from
    `busy`, and `_read` logs the drop and returns.
    """
    task = result.task
    state.busy.difference_update(task.items)
    if result.stopped:
        _stop(state, result.stopped, True)
        return
    if task.kind in ("compare", "claims"):
        if result.answer is None:
            _died(state, task, result.said)
        elif task.kind == "compare":
            _read_compare(state, task, result.answer)
        else:
            _read_claims(state, task, result.answer)
        return
    if task.kind != "critic":
        raise ValueError(f"a task of the kind {task.kind!r} has no reader")
    task_keys = _keyed_batch(state, task)
    if not task_keys:
        _logged(state, f"drop a critic result over {len(task.items)} item(s): no item waits for a critic")
        return
    if result.answer is None or not _is_heard(task_keys, result.answer):
        _died(state, task, result.said or "the critic spoke for no fragment of its batch")
        return
    _read_critic(state, task_keys, result.answer, result.said)


def _queued(state: _Settling, kind: str, items: list[str]) -> None:
    """Add a task over `items` to the pending tasks queue."""
    state.tasks_made += 1
    state.pending.append(settling_state.Task(f"{state.caller.began}:{state.tasks_made}", kind, list(items)))
    state.busy.update(items)
    _logged(state, f"queue a {kind} task over {len(items)} item(s)")


def _is_kind_busy(state: _Settling, kind: str) -> bool:
    """Whether a task of `kind` is pending or in flight."""
    return any(task.kind == kind for task in state.pending) or any(
        task.kind == kind for task, _runner in state.running.values()
    )


def _try_compare_task(state: _Settling) -> bool:
    """
    Create a comparator task where the state allows one.

    A `Change` a comparator passed over gets a task of its own. A full batch makes a task. Near the end of the run, a
    shorter batch makes a task too. The end is near when the queue holds no unexamined fragment the run may take and no
    critic task is pending or in flight.
    """
    undecided = [
        one
        for entry in state.queue.values()
        if isinstance(entry, settling_state.ProseState)
        for one in _undecided(entry)
        if one.id not in state.busy
    ]
    if not undecided:
        return False
    asked_alone = [one.id for one in undecided if one.is_asked_alone]
    if asked_alone:
        _queued(state, "compare", asked_alone[:1])
        return True
    if len(undecided) < _A_COMPARE_BATCH:
        is_fresh_left = state.fresh > 0 and settling_state.UNEXAMINED in state.queue.values()
        if is_fresh_left or _is_kind_busy(state, "critic"):
            return False
    _queued(state, "compare", [one.id for one in undecided[:_A_COMPARE_BATCH]])
    return True


def _try_critic_task(state: _Settling) -> bool:
    """
    Create a critic task where the state allows one.

    A dirty draft comes ahead of an unexamined fragment. A fragment at the round cap goes to the unsettled prose queue.
    Near the end of the run, the function makes a task of a shorter batch. The end is near when no comparator task is
    pending or in flight.

    A critic reads a fragment the driver cannot write back. A critic that passes such a fragment settles it, and no
    write follows. `_approved` takes a passed fragment into the ledger. A change the comparator keeps for such a
    fragment stops the run at the write. `_written_into_tree` asks `apply_prose.does_hold` before that write, and a no
    stops the run.
    """
    dirty = []
    fresh = []
    for digest, entry in state.queue.items():
        if digest in state.busy or not _is_waiting_for_critic(entry):
            continue
        if entry == settling_state.UNEXAMINED:
            fresh.append(digest)
        else:
            dirty.append(digest)
    batch: list[str] = []
    taken = 0
    for digest in dirty + fresh:
        if len(batch) == _A_CRITIC_BATCH:
            break
        is_fresh = state.queue[digest] == settling_state.UNEXAMINED
        if is_fresh and taken >= state.fresh:
            continue
        if _state_of(state, digest).rounds >= _MOST_ROUNDS:
            _given_up(state, digest, "the rounds ran out and the prose was still moving")
            continue
        batch.append(digest)
        taken += int(is_fresh)
    if not batch:
        return False
    if len(batch) < _A_CRITIC_BATCH and _is_kind_busy(state, "compare"):
        return False
    state.fresh -= taken
    _queued(state, "critic", batch)
    return True


def _try_claims_task(state: _Settling) -> bool:
    """
    Create a claims task where the state allows one. The task takes a fragment holding `proposed` prose and no task.
    """
    for digest, entry in state.queue.items():
        if digest not in state.busy and isinstance(entry, settling_state.ProseState) and entry.proposed:
            _queued(state, "claims", [digest])
            return True
    return False


def _try_planning(state: _Settling) -> bool:
    """
    Create a task where the state allows one. The driver tries a claims task first, a comparator task next, and a critic
    task last. The driver creates no task while the pending queue is full.
    """
    if len(state.pending) >= _MOST_AGENTS:
        return False
    return _try_claims_task(state) or _try_compare_task(state) or _try_critic_task(state)


def _asked(state: _Settling, digest: str) -> str:
    """A fragment as a critic's batch writes it."""
    fragment = state.by_digest[digest]
    entry = _state_of(state, digest)
    shown = dataclasses.asdict(fragment)
    shown["prose"]["content"] = "\n".join(entry.draft)
    for site, said in zip(shown["sites"], entry.draft):
        site["prose"] = said
    return f"### {entry.key}\n{fragment.path} ({fragment.kind})\n{agent_answers.sited(shown)}"


def _a_change(one: settling_state.Change) -> str:
    """A change as a comparator's batch writes it. The coin decides which side takes the old text."""
    first, second = (one.old, one.new) if one.is_old_first else (one.new, one.old)
    return f"### {one.id}\n\n#### A\n{first}\n\n#### B\n{second}\n"


# The critic's ask, computed once. A changing ask would throw the cached prefix away.
# https://platform.claude.com/docs/en/build-with-claude/prompt-caching says a change drops the cache after it.
_THE_ASK: list[str] = []


def _ask() -> str:
    """
    The text a critic reads above its batch. The list `agent_answers.rules_by_use` writes comes with it. That list names
    rules critics cited, beside how often the comparator turned a citation down.
    """
    if not _THE_ASK:
        _THE_ASK.append(f"{agent_answers.ASK}\n\n{agent_answers.rules_by_use()}")
    return _THE_ASK[0]


def _claims_files(state: _Settling, digest: str) -> tuple[str, str]:
    """
    The record file and the draft file of a claims task over `digest`. `collect_fragments.CLAIM_RECORD` names the record
    file to the call, and `collect_fragments.CLAIM_DRAFT` names the draft file.
    """
    where = os.path.join(state.into, _CHECKED)
    os.makedirs(where, exist_ok=True)
    return os.path.join(where, f"{digest}.record"), os.path.join(where, f"{digest}.json")


def _claims_asked(state: _Settling, digest: str) -> tuple[str, int]:
    """
    The message a claims task over `digest` sends, and the bytes of prose it holds. The message holds the `proposed`
    prose of the fragment. The draft file takes that prose for `prose_answer`. An empty message says the task went
    stale.
    """
    entry = state.queue.get(digest)
    if not isinstance(entry, settling_state.ProseState) or not entry.proposed:
        return "", 0
    drafted = collect_fragments.drafted(state.by_digest[digest], entry.proposed)
    record, draft = _claims_files(state, digest)
    with open(draft, "w", encoding="utf-8") as handle:
        json.dump({entry.key: entry.proposed}, handle)
    return converge_claims.claims_message(drafted, state.by_key, record), len(drafted.prose.content.encode())


def _message(state: _Settling, task: settling_state.Task) -> tuple[str, int]:
    """The message a task sends, and the bytes of prose it holds. An empty message says the task went stale."""
    if task.kind == "claims":
        return _claims_asked(state, task.items[0])
    if task.kind == "critic":
        digests = list(_keyed_batch(state, task).values())
        if not digests:
            return "", 0
        prose = sum(len("\n".join(_state_of(state, digest).draft).encode()) for digest in digests)
        return f"{_ask()}\n\n" + "\n".join(_asked(state, digest) for digest in digests), prose
    by_id = _changes_by_id(state)
    changes = [by_id[change_id][2] for change_id in task.items if change_id in by_id]
    if not changes:
        return "", 0
    prose = sum(len((one.old + one.new).encode()) for one in changes)
    return f"{_JUDGE}\n\n" + "\n".join(_a_change(one) for one in changes), prose


async def _calling(  # pylint: disable=broad-exception-caught
    state: _Settling, task: settling_state.Task, message: str, prose: int
) -> None:
    """
    Run a task, and append its result to the completed tasks queue.

    A call that raises gives a dead result. A stopped run kills a call in flight. Such a call gives no result, and a
    later run makes the call again. The queues still hold the work of that call. A later run starts with an empty
    `_Settling.busy`, and `_try_planning` makes the task again.
    """
    environment = None
    if task.kind == "claims":
        record, draft = _claims_files(state, task.items[0])
        environment = {collect_fragments.CLAIM_RECORD: record, collect_fragments.CLAIM_DRAFT: draft}
    elif task.kind == "critic":
        environment = {collect_fragments.CRITIC_BATCH: "\n".join(_keyed_batch(state, task))}
    try:
        counts = (len(task.items), prose)
        said, reply = await agent_calls.spoken(state.caller, task.kind, message, counts, environment, is_kept=True)
    except Exception as caught:  # failure-is-reported: the result this appends, and the log
        said = None
        reply = {"is_error": True, "result": f"the call raised {type(caught).__name__}: {caught}"}
    if state.caller.is_killing:
        return
    is_stopping = agent_calls.does_the_reply_stop_the_run(reply)
    stopped = str(reply.get("result") or reply.get("api_error_status")) if is_stopping else ""
    told = str(reply.get("result") or "")[:2000]
    if reply.get("transcript"):
        told += f". {reply['transcript']} holds what the call streamed"
    settling_state.completed(state.into, settling_state.TaskResult(task, said, stopped, told))


def _finished(state: _Settling, task_id: str, _runner: Any) -> None:
    """
    Forget a task that finished. The completed tasks queue holds its result. A call that a stopped run killed leaves no
    result there.
    """
    state.running.pop(task_id, None)


def _changes_by_id(state: _Settling) -> dict[str, tuple[str, settling_state.ProseState, settling_state.Change]]:
    """
    `{a change id: (its digest, its entry, the change)}` over a change the comparator has yet to judge.

    The id opens on the digest that minted it.
    """
    held = {}
    for digest, entry in state.queue.items():
        if isinstance(entry, settling_state.ProseState):
            for one in _undecided(entry):
                held[one.id] = (digest, entry, one)
    return held


def _progress(state: _Settling) -> tuple[int, ...]:
    """
    The readings a pass moves when it does something.

    A pass that moves none of these achieved nothing. The readings include the length of the queue. A pass that queues a
    task and launches none still changes the readings, and the loop does not count such a pass as idle.
    """
    return (state.caller.calls, state.approved, state.written, state.given_up, len(state.running), len(state.queue))


def _try_launching(state: _Settling) -> bool:
    """
    Launch pending tasks while a slot is free, and say whether a task launched.

    `_message` gives an empty message for a task the queue has moved past. Such a task leaves without a call, and the
    log takes a line for it.
    """
    is_launched = False
    while state.pending and len(state.running) < _MOST_AGENTS:
        task = state.pending.pop(0)
        message, prose = _message(state, task)
        if not message:
            state.busy.difference_update(task.items)
            _logged(state, f"drop a stale {task.kind} task over {len(task.items)} item(s): {', '.join(task.items)}")
            continue
        runner = asyncio.get_running_loop().create_task(_calling(state, task, message, prose))
        state.running[task.id] = (task, runner)
        runner.add_done_callback(functools.partial(_finished, state, task.id))
        is_launched = True
        _logged(state, f"launch a {task.kind} task over {len(task.items)} item(s)")
    return is_launched


def _try_writing(state: _Settling) -> bool:
    """
    Close a round the comparator decided, and say whether the round closed.

    The first round with no undecided change left closes. `_resolved` applies the accepted changes of the round to the
    draft. A draft those changes alter goes to a claims reader through `proposed`. The write takes a pass of its own.
    """
    for digest, entry in list(state.queue.items()):
        if isinstance(entry, settling_state.ProseState) and entry.changes and not _undecided(entry):
            _resolved(state, digest, entry)
            return True
    return False


def _overran(number: int, frame: Any) -> None:
    """Raise where a pass passed its budget. The timer sends the signal that reaches here."""
    raise _Overran(f"a pass passed its budget of {_A_PASS_BUDGET} seconds")


def _try_pass(state: _Settling) -> bool:
    """
    Run a pass under the timer, and say whether it did something.

    A pass writes a round the comparator decided. A pass with no such round takes a result off the completed tasks queue
    where a result waits. A pass with no result to read tries to create a task, unless the run has stopped. The pass
    then launches the pending tasks.

    The writing goes first. A dirty fragment whose round the comparator has decided waits for its write.
    `_is_waiting_for_critic` reads such a fragment as waiting for a critic. A pass that planned first would hand it to a
    critic before the write.
    """
    signal.setitimer(signal.ITIMER_REAL, _A_PASS_BUDGET)
    try:
        if _try_writing(state):
            return True
        result = settling_state.completed_taken(state.into)
        if result is not None:
            state.answered_at = time.monotonic()
            _read(state, result)
            return True
        if state.stopped:
            return False
        is_planned = _try_planning(state)
        return _try_launching(state) or is_planned
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


async def _seed(state: _Settling) -> None:
    """
    Write the cached prefix of the critic and of the comparator before a batch goes out.

    The critic's seed holds the fragment of the tree with the least key. A seed call stops at its first assistant
    message, and gives no verdict.
    """
    state.seeds += 1
    _logged(state, f"seed {state.seeds} writes the cached prefix")
    seed = min(state.by_digest.values(), key=lambda one: one.key)
    record = agent_answers.record_of(dataclasses.asdict(seed))
    held = await asyncio.gather(
        agent_calls.spoken(state.caller, "critic", f"{_ask()}\n\n{record}", is_a_seed=True),
        agent_calls.spoken(state.caller, "compare", f"{_JUDGE}\n\n{_SEED_CHANGE}", is_a_seed=True),
        return_exceptions=True,
    )
    for got in held:
        if isinstance(got, BaseException):
            _stop(state, f"a seed call raised {type(got).__name__}: {got}", False)
            continue
        reply = got[1]
        if agent_calls.does_the_reply_stop_the_run(reply):
            _stop(state, str(reply.get("result") or reply.get("api_error_status")), True)
        elif reply.get("is_error"):
            _stop(state, f"a seed call died: {str(reply.get('result'))[:160]}", False)
    state.answered_at = time.monotonic()


async def _converge(state: _Settling) -> None:
    """
    Run passes until the queues hold nothing to do, or until the run stops.

    A stall with tasks pending and slots to spare gets a fresh seed. The run stops once the process that started it goes
    away.
    """
    await _seed(state)
    while not state.stopped:
        if os.getppid() != state.parent:
            _stop(state, "the process that started the run went away", True)
            break
        is_stalled = time.monotonic() - state.answered_at > _AN_IDLE_GAP
        if is_stalled and state.pending and _MOST_AGENTS - len(state.running) > 1:
            await _seed(state)
            continue
        was = _progress(state)
        if _try_pass(state):
            state.idle_passes = 0 if _progress(state) != was else state.idle_passes + 1
            if state.idle_passes > _MOST_IDLE_PASSES:
                _stop(state, f"the run went round {state.idle_passes} pass(es) without doing anything", False)
                break
            continue
        if not state.running:
            break
        runners = {runner for _task, runner in state.running.values()}
        await asyncio.wait(runners, timeout=_A_WAIT, return_when=asyncio.FIRST_COMPLETED)
    agent_calls.killed(state.caller)


async def _condensed(state: _Settling) -> None:
    """
    Condense the suggested proposals into the pending proposals file, and empty the suggested proposals queue.

    A stopped run skips the condense. An empty suggested proposals queue skips the condense. A condense call that dies
    stops the run, and the suggested proposals stay in place. The checkers read the condensed proposals. A refusal stops
    the run as a fault. The pending proposals file then keeps its text.
    """
    if state.stopped:
        return
    proposals = settling_state.lines_read(settling_state.SUGGESTED)
    if not proposals:
        return
    ask = f"{_CONDENSE}\n\n{agent_answers.rules_by_use()}\n\n{_ruled()}"
    message = f"{ask}\n\n{json.dumps(proposals, indent=2)}"
    said, reply = await agent_calls.spoken(state.caller, "condense", message, (len(proposals), 0))
    if said is None:
        why = str(reply.get("result"))[:160]
        _stop(state, f"the condense call died: {why}. {reply.get('transcript')} holds what it streamed", False)
        return
    condensed = list(said.get("proposals") or [])
    found = [refused for one in condensed for refused in record_run.proposal_refusals(one)]
    if found:
        _stop(state, "the checkers refused a condensed proposal:\n" + "\n\n".join(found), False)
        return
    count = record_run.proposals_appended(condensed, "converge")
    settling_state.rewritten(settling_state.SUGGESTED, [])
    _logged(state, f"{count} proposal(s) condensed from {len(proposals)}")


async def _run(state: _Settling) -> None:
    """Settle the queue, then condense the proposals. A stop signal stops the run at the next pass."""
    agent_calls.stops_on_signals(lambda why: _stop(state, why, True))
    await _converge(state)
    await _condensed(state)
    await _ended(state)


async def _ended(state: _Settling) -> None:
    """
    Cancel the tasks still running, and wait for them a while. The log names a task that outlives its cancel, with the
    stack the task holds.
    """
    left = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in left:
        task.cancel()
    if not left:
        return
    _done, stuck = await asyncio.wait(left, timeout=_MOST_ENDING)
    for task in stuck:
        _logged(state, f"a task outlived its cancel at {_awaiting(task.get_coro())}")


def _awaiting(coroutine: Any) -> str:
    """
    _awaiting follows a coroutine down its await chain. A frame reads as `name:line`. The outermost frame comes first.
    An awaited object with no frame comes last, as its repr.
    """
    frames = []
    while coroutine is not None and getattr(coroutine, "cr_frame", None) is not None:
        frames.append(f"{coroutine.cr_code.co_name}:{coroutine.cr_frame.f_lineno}")
        coroutine = getattr(coroutine, "cr_await", None)
    return " > ".join(frames) + (f" > {coroutine!r}" if coroutine is not None else "")


# The seconds `_ended` waits for the cancelled tasks.
_MOST_ENDING = 10.0


def main() -> None:  # pylint: disable=broad-exception-caught
    """
    Settle the prose under the run lock, and print what the run did.

    The run leaves `agent_calls.RESUMABLE` where a later run can continue from the stop. The run leaves
    `agent_calls.FAILED` where a person has to investigate the stop.
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
    if len(argv) not in (1, 2):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    into = argv[0]
    signal.signal(signal.SIGALRM, _overran)
    with settling_state.run_locked(into):
        fragments = collect_fragments.fragments()
        print(update_ledger_and_queue.updated(into, fragments), file=sys.stderr)
        by_digest: dict[str, collect_fragments.Fragment] = {}
        for one in fragments:
            by_digest.setdefault(one.prose.sha, one)
        queue = settling_state.prose_queue(into)
        state = _Settling(
            into=into,
            caller=agent_calls.Caller(
                into=into,
                model=model,
                began=time.strftime("%Y-%m-%dT%H:%M:%S"),
                agents=_AN_AGENT,
                schemas=_A_SCHEMA,
                turns=_TURNS,
            ),
            parent=os.getppid(),
            by_digest=by_digest,
            queue=queue,
            fresh=int(argv[1]) if len(argv) == 2 else len(queue),
            by_key={one.key: one for one in fragments + collect_fragments.code_only()},
        )
        try:
            asyncio.run(_run(state))
        except Exception as caught:  # failure-is-reported: the stop reason; pylint: disable=broad-exception-caught
            agent_calls.killed(state.caller)
            _stop(state, f"the run ended on {type(caught).__name__}: {caught}", False)
        finally:
            agent_calls.killed(state.caller)
    print(
        json.dumps(
            {
                "approved": state.approved,
                "written": state.written,
                "unsettled": state.given_up,
                "calls": state.caller.calls,
                "seeds": state.seeds,
                "spent": agent_calls.summed(state.caller),
                "stopped": state.stopped,
            },
            indent=2,
        )
    )
    if state.stopped:
        print(f"this run stopped: {state.stopped}", file=sys.stderr)
        sys.exit(agent_calls.RESUMABLE if state.is_resumable else agent_calls.FAILED)


if __name__ == "__main__":
    main()
