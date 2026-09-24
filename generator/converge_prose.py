# SPDX-License-Identifier: MIT
"""
Settle the prose of the tree between the critic and the comparator.

`DESIGN.md` describes the pass under `Settling the prose`. `settling_state` names the files that hold its state. A run
takes the run lock, reads the tree, and calls `update_ledger_and_queue.updated`. The run then goes through passes. A
pass writes a round the comparator decided. A pass with no such round takes a result off the completed tasks queue and
reads it. A pass with no result to read creates a task. The pass then launches a pending task while a slot is free.
After an idle pass, the run waits for a task to finish.

A task runs an agent as a subprocess of the Claude command. The agent reads its batch over standard input and answers
JSON. The task puts that JSON on the completed tasks queue.

**Usage:** `python3 generator/converge_prose.py <directory> [count]`. The directory holds the settling queues. A count
caps the unexamined fragments the run takes. A `--model` names the model the agents run.
"""

import asyncio
import dataclasses
import functools
import json
import os
import random
import signal
import sys
import time

from typing import Any

import apply_prose
import batch_pending_fragments
import collect_fragments
import gate
import record_run
import settling_state
import update_ledger_and_queue

# The message a bad call gets.
_USAGE = "usage: converge_prose.py <directory> [count] [--model <name>]"

# The status of a stop a later run continues from.
_RESUMABLE = 3
_FAILED = 1  # the status of a stop a person investigates.

# The fragments a critic reads at once. A critic reading a longer batch spends fewer output tokens per fragment, and a
# fragment late in the batch gets the thinner reading.
_A_CRITIC_BATCH = 5

# The changes a comparator judges at once. This batch and the critic's batch cost about the same per call.
_A_COMPARE_BATCH = 60

# The turns an agent takes. The StructuredOutput call takes a turn. A retry after a `prose_answer` refusal takes a turn
# too. `prose_answer` stops refusing past its cap, and this count leaves room for those retries.
_MOST_TURNS = 5

# The settings an agent call loads. A plugin the user settings enable stays out of the agent's prompt.
_SETTING_SOURCES = "project,local"

# The environment an agent call adds. The command otherwise puts the user's instruction file and the memory index into
# the first message. That text sits past the cached prefix, and a call writes it to the cache again. The agent
# definition holds the rules an agent needs.
_AGENT_ENVIRONMENT = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
}

# The fields of a call record a run adds up, beside the count of calls and the count of dead calls.
_A_TOTAL = ("items", "messageBytes", "proseBytes", "answerBytes", "seconds", "cost")

# The token counts a run adds up. An entry pairs the name a total takes with the name the command gives that count.
_A_TOKEN = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cacheRead": "cache_read_input_tokens",
    "cacheWrite": "cache_creation_input_tokens",
}

# The tasks a run keeps in flight. The pending tasks queue holds no more than this many tasks either.
_MOST_AGENTS = 8

# The critic rounds a fragment may take. A fragment at this count goes to the unsettled prose queue.
_MOST_ROUNDS = 10

# The idle seconds after which a pass with a free slot sends a fresh seed.
_AN_IDLE_GAP = 300.0

# The seconds a pass may take. A pass does string work, writes a fragment into the tree, and starts a task rather than
# waiting for one. A pass past this budget hangs, and the run stops for a reader to look at the pass.
_A_PASS_BUDGET = 10.0

# The seconds a call may take. The calls on record set this budget.
_A_CALL_BUDGET = 1800.0

# The seconds the run waits for a task before it looks at the state again. A stop request lands within this wait.
_A_WAIT = 5.0

# The name of the journal taking a record per call. A run appends to this journal. `began` tells the runs apart.
_SPENT = "calls.jsonl"

# The agent a kind of call runs as.
_AN_AGENT = {"critic": "prose-critic", "compare": "prose-compare", "condense": "prose-condense"}

# The statuses that say a call reached the plan usage window rather than a fault in the call. A run stops on one of
# these, and a later run continues from the state.
_A_WALL = (429, 529)

# The dead calls in a row a run takes before it stops. `_split` gives the batch of a dead call back to the pending tasks
# queue. A call that answers clears the count.
_MOST_DEAD_CALLS = 10

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

# The coins that decide which side of a pair a rewrite takes.
_COINS = random.Random()

# The ask a condense reads. `.claude/proposals-pending.md` takes what comes back.
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
    "A proposal states a pair of quotes. `before` is prose the proposed checker refuses. `after` says that prose "
    "again in the form the checker passes. A merged proposal keeps the pair of the proposal whose rule you kept."
)

# The answer shape a kind of call asks for. `write_agent_prompts` writes the same shape into the agent's definition.
_A_SCHEMA = {
    "critic": batch_pending_fragments.EXAMINED,
    "compare": batch_pending_fragments.JUDGED,
    "condense": batch_pending_fragments.CONDENSED,
}


class _Overran(Exception):
    """A pass took longer than its budget. The timer raises this, and the run stops."""


@dataclasses.dataclass
class _Settling:
    """
    The state a pass reads and writes. The loop holds this state. A task reads none of it.

    `queue` holds the prose queue as the file holds it. `busy` holds the digests and the change ids a pending or running
    task holds. `fresh` counts the unexamined fragments the run may still give a critic.
    """

    into: str  # the directory holding the settling queues.
    model: str  # the model the agents run. The empty string leaves the default in place.
    began: str  # the clock this run started on. A record of a call names it, and the runs stay apart.
    parent: int  # the process that started the run. The run stops once that process goes away.
    by_digest: dict[str, collect_fragments.Fragment]
    queue: dict[str, settling_state.ProseState | str]
    fresh: int
    pending: list[settling_state.Task] = dataclasses.field(default_factory=list)
    running: dict[str, tuple[settling_state.Task, Any]] = dataclasses.field(default_factory=dict)
    children: set[Any] = dataclasses.field(default_factory=set)
    busy: set[str] = dataclasses.field(default_factory=set)
    tasks_made: int = 0
    calls: int = 0
    seeds: int = 0
    approved: int = 0
    written: int = 0
    given_up: int = 0
    answered_at: float = 0.0
    dead_in_a_row: int = 0  # the dead calls since the last call that answered.
    idle_passes: int = 0  # the passes since the last pass that moved `_progress`.
    stopped: str = ""  # the reason the run stopped. The empty string says the run is still going.
    is_resumable: bool = False  # whether a later run continues from the stop without a person looking.
    is_killing: bool = False  # whether the run killed its agents. A killed call leaves no result.


def _ruled() -> str:
    """The text of the proposals awaiting a ruling. A condense reads that text and states no proposal already there."""
    if not os.path.exists(settling_state.PENDING):
        return ""
    with open(settling_state.PENDING, encoding="utf-8") as handle:
        return handle.read()


def _spent_journal(into: str) -> str:
    """
    The file taking a record per call. A run leaves the records of an earlier run in place, and `began` names a run.
    """
    return os.path.join(into, _SPENT)


def _spent_records(into: str, began: str) -> list[dict[str, Any]]:
    """The records written by the run that `began` names. A record of an earlier run stays out."""
    return [one for one in settling_state.lines_read(_spent_journal(into)) if one.get("run") == began]


def _undecided(entry: settling_state.ProseState) -> list[settling_state.Change]:
    """The changes of `entry` a comparator has yet to judge."""
    return [one for one in entry.changes if one.verdict is None]


def _is_waiting_for_critic(entry: settling_state.ProseState | str) -> bool:
    """Whether a critic reads the fragment next. An unexamined fragment waits for a critic. A dirty draft waits too."""
    if entry == settling_state.UNEXAMINED:
        return True
    return isinstance(entry, settling_state.ProseState) and entry.is_dirty and not _undecided(entry)


def _does_a_pass_take(entry: settling_state.ProseState) -> bool:
    """
    Whether a pass of the run can take `entry` up.

    A critic reads an entry waiting for one. `_try_writing` closes a round of an entry holding changes. An entry that is
    neither sits in the queue and no pass selects it.
    """
    return _is_waiting_for_critic(entry) or bool(entry.changes)


def _state_of(state: _Settling, digest: str) -> settling_state.ProseState:
    """The state of `digest`. An unexamined fragment takes its draft from the tree."""
    entry = state.queue[digest]
    if isinstance(entry, settling_state.ProseState):
        return entry
    fragment = state.by_digest[digest]
    return settling_state.ProseState(fragment.key, [site.prose for site in fragment.sites], 0, True, [])


def _counted(state: _Settling) -> str:
    """The depth of the queues, as a log line ends on it."""
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
        f"critics={kinds.count('critic')} compares={kinds.count('compare')} calls={state.calls} "
        f"approved={state.approved} written={state.written} unsettled={state.given_up}"
    )


def _logged(state: _Settling, what: str) -> None:
    """
    Append a line to the run's log. A reader tails that file while the run moves.

    A line gives the clock, what happened, and the depth of the queues. The line goes to standard error, and standard
    output takes the summary.
    """
    said = f"{time.strftime('%H:%M:%S')} {what} | {_counted(state)}"
    with open(os.path.join(state.into, "log.txt"), "a", encoding="utf-8") as handle:
        handle.write(said + "\n")
    print(said, file=sys.stderr, flush=True)


def _stop(state: _Settling, why: str, is_resumable: bool) -> None:
    """Stop the run. The first reason stays."""
    if state.stopped:
        return
    state.stopped = why
    state.is_resumable = is_resumable
    _logged(state, f"the run stops: {why}")


def _stored(state: _Settling, digest: str, entry: settling_state.ProseState | str) -> None:
    """
    Put `entry` in the prose queue under `digest`.

    A queue entry no pass takes up stays there for ever. The run then ends with work in the queue and nothing to do.
    This raises on such an entry rather than writing it. A fragment with nothing left to settle goes to the ledger
    through `_approved`, or to the unsettled prose queue through `_given_up`.
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
    The changes of `changes` the draft bears out.

    This leaves out a change quoting text the draft lacks. This leaves out a change whose run overlaps a change already
    placed.
    """
    text = "\n".join(draft)
    held: list[dict[str, str]] = []
    runs: list[tuple[int, int]] = []
    for said in changes:
        if not isinstance(said, dict):
            continue
        old = str(said.get("old") or "")
        at = text.find(old) if old else -1
        if at < 0:
            continue
        if any(start < at + len(old) and at < end for start, end in runs):
            continue
        runs.append((at, at + len(old)))
        held.append({"old": old, "new": str(said.get("new") or ""), "rule": str(said.get("rule") or "")})
    return held


def _sorted(said: Any) -> dict[str, Any]:
    """
    The verdicts of `said`, in the lists a caller reads.

    A `rewritten` verdict citing no change joins what the reader ran out of room for. An answer shaped otherwise names
    no fragment.
    """
    if not isinstance(said, dict):
        return {"passed": [], "rewritten": [], "out_of_budget": [], "proposals": []}
    raised = said.get("proposals")
    raised = list(raised)[: batch_pending_fragments.MOST_PROPOSALS] if isinstance(raised, list) else []
    held: dict[str, Any] = {"passed": [], "rewritten": [], "out_of_budget": [], "proposals": raised}
    verdicts = said.get("verdicts")
    for key, one in (verdicts if isinstance(verdicts, dict) else {}).items():
        changes = (one or {}).get("changes")
        if (one or {}).get("verdict") == "rewritten" and isinstance(changes, list) and changes:
            held["rewritten"].append({"key": key, "changes": changes})
        elif (one or {}).get("verdict") == "passed":
            held["passed"].append(key)
        else:
            held["out_of_budget"].append(key)
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
    """Whether a critic answer speaks for a fragment of its batch. A critic speaking for none told the loop nothing."""
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


def _read_critic(state: _Settling, task_keys: dict[str, str], said: Any) -> None:
    """
    Take a critic's answer.

    A passed fragment goes into the ledger. A rewritten fragment counts a round and keeps the changes the draft bears
    out. A discarded change leaves the draft dirty. A fragment the critic says nothing about counts a round and stays
    dirty. An answer the checkers refuse changes nothing.
    """
    answer = _sorted(said)
    if _is_refused(state, task_keys, answer):
        return
    _proposed(state, answer["proposals"])
    passed = set(answer["passed"])
    rewritten = {one["key"]: one["changes"] for one in answer["rewritten"]}
    for key, digest in task_keys.items():
        if key in passed:
            _approved(state, digest)
            continue
        entry = _state_of(state, digest)
        entry.rounds += 1
        suggested = rewritten.get(key, [])
        placed = _placed(entry.draft, suggested)
        if len(placed) < len(suggested):
            _logged(state, f"{key}: the draft bears out {len(placed)} of {len(suggested)} change(s)")
        entry.changes = [
            settling_state.Change(
                f"{digest}:{entry.rounds}:{at}", one["old"], one["new"], one["rule"], _COINS.random() < 0.5
            )
            for at, one in enumerate(placed)
        ]
        entry.is_dirty = not placed or len(placed) < len(suggested)
        _stored(state, digest, entry)


def _shifted(state: _Settling, runs: dict[str, list[tuple[int, int, int]]]) -> None:
    """
    Move the sites and the starts a tree write shifted. `runs` comes from `apply_prose.fragment_written`.

    A run replaced a site of the fragment written. A site or a start below a run moves by the lines the run gained. The
    runs of a file come in the order of the write. A later line comes first.
    """
    for path, file_runs in runs.items():
        for digest, fragment in list(state.by_digest.items()):
            if path not in fragment.starts:
                continue
            start = fragment.starts[path]
            blocks = [
                collect_fragments.site_lines(fragment, site) if site.path == path else () for site in fragment.sites
            ]
            for first, held, holds in file_runs:
                gained = holds - held
                if start > first:
                    start += gained
                for at, block in enumerate(blocks):
                    if not block:
                        continue
                    if block[0] == first:
                        blocks[at] = tuple(range(first, first + holds))
                    elif block[0] > first:
                        blocks[at] = tuple(line + gained for line in block)
            sites = tuple(
                dataclasses.replace(site, lines=tuple(line - start for line in block)) if block else site
                for site, block in zip(fragment.sites, blocks)
            )
            state.by_digest[digest] = dataclasses.replace(
                fragment, starts={**fragment.starts, path: start}, sites=sites
            )


def _written_into_tree(state: _Settling, digest: str, entry: settling_state.ProseState, draft: list[str]) -> str:
    """
    Write `draft` into the tree for the fragment of `digest`, and answer with the new digest. An empty answer says the
    run stopped.

    The write first asks `apply_prose.does_hold` of a site. A site answers no where the tree has moved past the prose
    the fragment records. A site answers no where `apply_prose` has no shaper for its layout. A JSON note is such a
    site, and a writer edits that note by hand. Either answer stops the run. A hook refusal stops the run. A person
    investigates such a stop.
    """
    fragment = state.by_digest.get(digest)
    if fragment is None or len(draft) != len(fragment.sites):
        _stop(state, f"the run holds no fragment of {len(draft)} site(s) for {entry.key}", False)
        return ""
    for site in fragment.sites:
        if not apply_prose.does_hold(fragment, site):
            _stop(state, f"the driver cannot write {entry.key} back into {site.path}", False)
            return ""
    found = apply_prose.draft_refusals(fragment, draft)
    if found:
        _stop(state, f"the checkers refused the rewrite of {entry.key}:\n" + "\n\n".join(found), False)
        return ""
    runs = apply_prose.fragment_written(fragment, draft)
    new_digest = collect_fragments.prose_digest(draft)
    rewritten = dataclasses.replace(
        fragment,
        key=collect_fragments.key_for(fragment.key, new_digest),
        prose=dataclasses.replace(fragment.prose, content="\n".join(draft), sha=new_digest),
        sites=tuple(dataclasses.replace(site, prose=said) for site, said in zip(fragment.sites, draft)),
    )
    del state.by_digest[digest]
    state.by_digest[new_digest] = rewritten
    _shifted(state, runs)
    entry.key = rewritten.key
    state.written += 1
    _logged(state, f"write {rewritten.key}")
    return new_digest


def _resolved(state: _Settling, digest: str, entry: settling_state.ProseState) -> None:
    """
    Close a round of `entry`. The comparator has judged the changes of that round.

    The accepted changes go into the tree in a single write. The fragment then moves to its new digest. A fragment with
    an accepted change is dirty. A clean fragment goes into the ledger. A dirty fragment waits for a critic.
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
    new_digest = _written_into_tree(state, digest, entry, draft)
    if not new_digest:
        return
    entry.draft = draft
    entry.is_dirty = True
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
        new_side = batch_pending_fragments.SIDES[int(change.is_old_first)]
        if side in batch_pending_fragments.SIDES:
            change.verdict = "accept" if side == new_side else "reject"
        elif side == batch_pending_fragments.A_TIE:
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


def _split(state: _Settling, task: settling_state.Task, said: str) -> None:
    """
    Take a dead call. The halves of its batch go back to the pending tasks queue.

    A dead critic call over a single fragment counts a round, and the fragment stays dirty. A dead comparator call over
    a single change rejects that change. The run stops where `_MOST_DEAD_CALLS` calls in a row die.
    """
    state.dead_in_a_row += 1
    _logged(state, f"a {task.kind} call gave the loop nothing: {said[:160]}")
    if state.dead_in_a_row > _MOST_DEAD_CALLS:
        _stop(state, f"the calls keep dying: {said[:160]}", True)
        return
    if len(task.items) > 1:
        half = (len(task.items) + 1) // 2
        for items in (task.items[:half], task.items[half:]):
            _queued(state, task.kind, items)
        return
    if task.kind == "compare":
        _read_compare(state, task, {"verdicts": []})
        return
    _read_critic(state, _keyed_batch(state, task), {"verdicts": {}})


def _read(state: _Settling, result: settling_state.TaskResult) -> None:
    """Take a task result off the completed tasks queue. A stale result changes nothing."""
    task = result.task
    state.busy.difference_update(task.items)
    if result.stopped:
        _stop(state, result.stopped, True)
        return
    if task.kind == "compare":
        if result.answer is None:
            _split(state, task, result.said)
            return
        state.dead_in_a_row = 0
        _read_compare(state, task, result.answer)
        return
    task_keys = _keyed_batch(state, task)
    if not task_keys:
        return
    if result.answer is None or not _is_heard(task_keys, result.answer):
        _split(state, task, result.said or "the critic spoke for no fragment of its batch")
        return
    state.dead_in_a_row = 0
    _read_critic(state, task_keys, result.answer)


def _queued(state: _Settling, kind: str, items: list[str]) -> None:
    """Add a task over `items` to the pending tasks queue."""
    state.tasks_made += 1
    state.pending.append(settling_state.Task(f"{state.began}:{state.tasks_made}", kind, list(items)))
    state.busy.update(items)
    settling_state.pending_tasks_written(state.into, state.pending)
    _logged(state, f"queue a {kind} task over {len(items)} item(s)")


def _is_kind_busy(state: _Settling, kind: str) -> bool:
    """Whether a task of `kind` is pending or in flight."""
    return any(task.kind == kind for task in state.pending) or any(
        task.kind == kind for task, _runner in state.running.values()
    )


def _try_compare_task(state: _Settling) -> bool:
    """
    Create a comparator task where the state allows one.

    A change a comparator passed over gets a task of its own. A full batch makes a task. Near the end of the run, a
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
    Near the end of the run, a shorter batch makes a task. The end is near when no comparator task is pending or in
    flight.

    A critic reads a fragment the driver cannot write back. A critic that passes such a fragment settles it, and no
    write follows. A change the comparator keeps for such a fragment stops the run at the write.
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


def _try_planning(state: _Settling) -> bool:
    """
    Create a task where the state allows one. The driver tries a comparator task first. The driver waits while the
    pending queue is full.
    """
    if len(state.pending) >= _MOST_AGENTS:
        return False
    return _try_compare_task(state) or _try_critic_task(state)


def _asked(state: _Settling, digest: str) -> str:
    """A fragment as a critic's batch writes it."""
    fragment = state.by_digest[digest]
    entry = _state_of(state, digest)
    shown = dataclasses.asdict(fragment)
    shown["prose"]["content"] = "\n".join(entry.draft)
    for site, said in zip(shown["sites"], entry.draft):
        site["prose"] = said
    return (
        f"### {entry.key}\n{fragment.path}:{fragment.first} ({fragment.kind})\n{batch_pending_fragments.sited(shown)}"
    )


def _a_change(one: settling_state.Change) -> str:
    """A change as a comparator's batch writes it. The coin decides which side takes the old text."""
    first, second = (one.old, one.new) if one.is_old_first else (one.new, one.old)
    return f"### {one.id}\n\n#### A\n{first}\n\n#### B\n{second}\n"


# The critic's ask, computed once. A changing ask would throw the cached prefix away.
_THE_ASK: list[str] = []


def _ask() -> str:
    """The text a critic reads above its batch. The rules a writer of this project breaks come with it."""
    if not _THE_ASK:
        _THE_ASK.append(f"{batch_pending_fragments.ASK}\n\n{batch_pending_fragments.rules_by_use()}")
    return _THE_ASK[0]


def _message(state: _Settling, task: settling_state.Task) -> tuple[str, int]:
    """The message a task sends, and the bytes of prose it holds. An empty message says the task went stale."""
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


def _spent(kind: str, message: str, counts: tuple[int, int], reply: dict[str, Any], seconds: float) -> dict[str, Any]:
    """
    The record of a call. `counts` holds the items of the batch and the bytes of prose they hold.

    `usage` holds the token counts under the names the command gave them. `seconds` covers the subprocess. `durationMs`
    holds the time the command reported for itself.
    """
    usage = reply.get("usage")
    return {
        "kind": kind,
        "items": counts[0],
        "messageBytes": len(message.encode()),
        "proseBytes": counts[1],
        "answerBytes": len(json.dumps(reply.get("structured_output") or "").encode()),
        "seconds": seconds,
        "durationMs": reply.get("duration_ms"),
        "apiMs": reply.get("duration_api_ms"),
        "turns": reply.get("num_turns"),
        "cost": reply.get("total_cost_usd"),
        "isError": bool(reply.get("is_error")),
        "status": reply.get("api_error_status"),
        "usage": usage if isinstance(usage, dict) else {},
    }


async def _spoken(
    state: _Settling, kind: str, message: str, counts: tuple[int, int] = (0, 0)
) -> tuple[Any, dict[str, Any]]:
    """
    Answer with what a call said. The record of that call joins the journal of calls.

    A seed call and a condense call hold no batch. The record of such a call counts no item and no prose.
    """
    began = time.monotonic()
    state.calls += 1
    said, reply = await _called(state, kind, message)
    record = {"run": state.began, **_spent(kind, message, counts, reply, time.monotonic() - began)}
    settling_state.appended(_spent_journal(state.into), [record])
    return said, reply


async def _called(state: _Settling, kind: str, message: str) -> tuple[Any, dict[str, Any]]:
    """
    The answer the agent for `kind` gives `message`, and the reply the command wrapped it in.

    The answer is `None` where the call died. A call past its budget dies. This function then kills its subprocess.
    """
    named = [
        "claude",
        "-p",
        "--agent",
        _AN_AGENT[kind],
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(_A_SCHEMA[kind]),
        "--permission-prompts",
        "none",
        "--max-turns",
        str(_MOST_TURNS),
        "--no-session-persistence",
        "--setting-sources",
        _SETTING_SOURCES,
    ]
    if state.model:
        named += ["--model", state.model]
    run = await asyncio.create_subprocess_exec(
        *named,
        cwd=gate.TREE,
        env={**os.environ, **_AGENT_ENVIRONMENT},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    state.children.add(run)
    try:
        out, err = await asyncio.wait_for(run.communicate(message.encode()), _A_CALL_BUDGET)
    except TimeoutError:  # failure-is-reported: the dead reply this returns, and the log line of its task
        run.kill()
        await run.wait()
        return None, {"is_error": True, "result": f"the call ran past {_A_CALL_BUDGET} seconds"}
    finally:
        state.children.discard(run)
    text = out.decode(errors="replace")
    aside = err.decode(errors="replace").strip()
    if not text.strip().startswith("{"):
        said = aside or f"the command wrote no answer and left with {run.returncode}"
        return None, {"is_error": True, "result": said}
    try:
        held = json.loads(text)
    except json.JSONDecodeError:  # not-a-failure: the reply below names the fault
        return None, {"is_error": True, "result": f"the command wrote no JSON: {text[:200]}"}
    if held.get("subtype") == "error_max_turns":
        return None, {**held, "result": f"the agent ran past {_MOST_TURNS} turns"}
    if held.get("is_error"):
        return None, held
    answer = held.get("structured_output")
    if not isinstance(answer, dict):
        return None, {**held, "is_error": True, "result": "the answer made no StructuredOutput call"}
    return answer, held


def _does_the_reply_stop_the_run(reply: dict[str, Any]) -> bool:
    """
    Whether the reply stops the run.

    A call at the plan usage window stops the run. An error past that window kills the call, and `_split` reads the
    error.
    """
    return bool(reply.get("is_error")) and reply.get("api_error_status") in _A_WALL


async def _calling(  # pylint: disable=broad-exception-caught
    state: _Settling, task: settling_state.Task, message: str, prose: int
) -> None:
    """
    Run a task, and append its result to the completed tasks queue. This writes no state.

    A call that raises gives a dead result. A stopped run kills a call in flight. Such a call gives no result, and a
    later run makes the call again.
    """
    try:
        said, reply = await _spoken(state, task.kind, message, (len(task.items), prose))
    except Exception as caught:  # failure-is-reported: the result this appends, and the log
        said = None
        reply = {"is_error": True, "result": f"the call raised {type(caught).__name__}: {caught}"}
    if state.is_killing:
        return
    stopped = str(reply.get("result") or reply.get("api_error_status")) if _does_the_reply_stop_the_run(reply) else ""
    settling_state.completed(
        state.into, settling_state.TaskResult(task, said, stopped, str(reply.get("result") or "")[:2000])
    )


def _finished(state: _Settling, task_id: str, _runner: Any) -> None:
    """Forget a task that finished. The completed tasks queue holds its result."""
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

    A pass that moves none of these achieved nothing. The readings leave out the queue of pending tasks. A pass that
    queues a task and launches none has moved no work along, and the loop counts such a pass as idle.
    """
    return (state.calls, state.approved, state.written, state.given_up, len(state.running), len(state.queue))


def _try_launching(state: _Settling) -> bool:
    """
    Launch pending tasks while a slot is free, and say whether a task launched.

    `_message` gives nothing back for a task the queue has moved past. Such a task leaves without a call, and the log
    takes a line for it. A drop that goes unsaid reads exactly like a task nobody planned.
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
    settling_state.pending_tasks_written(state.into, state.pending)
    return is_launched


def _try_writing(state: _Settling) -> bool:
    """
    Close a round the comparator decided, and say whether the round closed.

    A round with no undecided change left goes into the tree. The write takes a pass of its own.
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
    where a result waits. A pass with no result to read creates a task. The pass then launches the pending tasks.

    The writing goes first. `_is_waiting_for_critic` reads a fragment with a decided round as one waiting for a critic.
    A pass that planned before it wrote would send that fragment to a critic.
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
    """Write the cached prefix of the critic and of the comparator before a batch goes out."""
    state.seeds += 1
    _logged(state, f"seed {state.seeds} writes the cached prefix")
    held = await asyncio.gather(
        _spoken(state, "critic", f"{_ask()}\n\n{batch_pending_fragments.SEED}"),
        _spoken(state, "compare", f"{_JUDGE}\n\n{_SEED_CHANGE}"),
        return_exceptions=True,
    )
    for got in held:
        if isinstance(got, BaseException):
            _stop(state, f"a seed call raised {type(got).__name__}: {got}", False)
            continue
        reply = got[1]
        if _does_the_reply_stop_the_run(reply):
            _stop(state, str(reply.get("result") or reply.get("api_error_status")), True)
        elif reply.get("is_error"):
            _logged(state, f"a seed call gave the loop nothing: {str(reply.get('result'))[:160]}")
    state.answered_at = time.monotonic()


def _killed(state: _Settling) -> None:
    """Kill the agents still running. The run loses their work. A later run makes those calls again."""
    state.is_killing = True
    for run in list(state.children):
        try:
            run.kill()
        except ProcessLookupError:  # not-a-failure: the agent finished on its own
            pass


async def _converge(state: _Settling) -> None:
    """
    Run passes until the queues hold nothing to do, or until the run stops.

    A stall that leaves the slots free gets a fresh seed. The run stops once the process that started it goes away.
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
    _killed(state)


async def _condensed(state: _Settling) -> None:
    """
    Condense the suggested proposals into the pending proposals file, and empty the suggested proposals queue.

    A stopped run skips the condense. A condense that gives nothing back leaves the suggested proposals in place. The
    checkers read the condensed proposals. A refusal stops the run as a fault. The pending proposals file then keeps its
    text.
    """
    if state.stopped:
        return
    proposals = settling_state.lines_read(settling_state.SUGGESTED)
    if not proposals:
        return
    ask = f"{_CONDENSE}\n\n{batch_pending_fragments.rules_by_use()}\n\n{_ruled()}"
    said, reply = await _spoken(state, "condense", f"{ask}\n\n{json.dumps(proposals, indent=2)}", (len(proposals), 0))
    if said is None:
        _logged(state, f"the condense gave the loop nothing: {str(reply.get('result'))[:160]}")
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
    loop = asyncio.get_running_loop()
    for named in ("SIGTERM", "SIGHUP", "SIGINT"):
        loop.add_signal_handler(getattr(signal, named), _stop, state, f"the run took {named}", True)
    await _converge(state)
    await _condensed(state)


def _summed(state: _Settling) -> dict[str, Any]:
    """
    The totals of a run's calls, a record per kind of call.

    The journal of calls holds the calls these totals add up. A dead call joins the count of dead calls, and its own
    totals stay in the sum. The journal keeps the records of an earlier run, and `began` leaves those records out.
    """
    blank = {"calls": 0, "dead": 0, **{name: 0 for name in _A_TOTAL}, **{name: 0 for name in _A_TOKEN}}
    held: dict[str, Any] = {}
    for got in _spent_records(state.into, state.began):
        into = held.setdefault(got["kind"], dict(blank))
        into["calls"] += 1
        into["dead"] += int(got["isError"])
        for name in _A_TOTAL:
            into[name] += got[name] or 0
        for name, said in _A_TOKEN.items():
            into[name] += got["usage"].get(said) or 0
    return held


def main() -> None:  # pylint: disable=broad-exception-caught
    """
    Settle the prose under the run lock, and print what the run did.

    The run leaves `_RESUMABLE` where a later run can continue from the stop. The run leaves `_FAILED` where a person
    has to investigate the stop.
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
            model=model,
            began=time.strftime("%Y-%m-%dT%H:%M:%S"),
            parent=os.getppid(),
            by_digest=by_digest,
            queue=queue,
            fresh=int(argv[1]) if len(argv) == 2 else len(queue),
        )
        try:
            asyncio.run(_run(state))
        except Exception as caught:  # failure-is-reported: the stop reason; pylint: disable=broad-exception-caught
            _killed(state)
            _stop(state, f"the run ended on {type(caught).__name__}: {caught}", False)
    print(
        json.dumps(
            {
                "approved": state.approved,
                "written": state.written,
                "unsettled": state.given_up,
                "calls": state.calls,
                "seeds": state.seeds,
                "spent": _summed(state),
                "stopped": state.stopped,
            },
            indent=2,
        )
    )
    if state.stopped:
        print(f"this run stopped: {state.stopped}", file=sys.stderr)
        sys.exit(_RESUMABLE if state.is_resumable else _FAILED)


if __name__ == "__main__":
    main()
