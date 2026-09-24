# SPDX-License-Identifier: MIT
"""
The files that hold the state of the settling, and the lock a run takes over them.

The ledger holds an approved prose digest per line. The prose queue holds a line per change of a `ProseState`, keyed by
the prose digest. The unsettled prose queue holds a digest per line. The pending tasks queue holds the `Task`s waiting
to run. The completed tasks queue holds a `TaskResult` per finished task.

A kill can tear the last line of a file. A reader drops that line. A rewrite goes in under a second name, and a rename
puts it in place. An append to the completed tasks queue and a take off it both hold a file lock.

`update_ledger_and_queue` and `converge_prose` read and write these files. `record_run` writes them for the critic
workflow.
"""

import contextlib
import dataclasses
import fcntl
import json
import os
import re

from collections.abc import Iterable, Iterator
from typing import Any

import gate

# The approved prose digests. Git tracks this file.
LEDGER = os.path.join(gate.TREE, ".claude", "prose-ledger.jsonl")

# The convention proposals the critics made and no condense has taken. Git tracks this file.
SUGGESTED = os.path.join(gate.TREE, ".claude", "suggested-proposals.jsonl")

# The condensed proposals awaiting a ruling. `check_proposals` refuses while this file holds a proposal.
PENDING = os.path.join(gate.TREE, ".claude", "proposals-pending.md")

# The label of the bullet under a pending proposal that holds its `why` field. `record_run` writes it. `checkers` passes
# a WHY clause in that bullet.
WHY = "**Why:**"

# The files under the settling directory. `make examine-prose` names that directory.
_PROSE_QUEUE = "prose-queue.jsonl"
_UNSETTLED = "unsettled-prose.jsonl"  # the digests of the fragments past the round limit.
_PENDING_TASKS = "pending-tasks.json"  # the tasks waiting for a slot.
_COMPLETED_TASKS = "completed-tasks.jsonl"  # the results of the finished tasks.
_COMPLETED_LOCK = "completed-tasks.lock"  # the file an append and a take lock.
_RUN_LOCK = "run.lock"  # the file a run locks while it runs.

# The state of a fragment no critic has read. The tree holds its draft.
UNEXAMINED = "unexamined"

# A prose digest, as `collect_fragments.prose_digest` writes it.
_A_DIGEST = re.compile(r"[0-9a-f]{16}")


@dataclasses.dataclass
class Change:
    """
    A change a critic suggested to a draft.

    `old` is the text the change replaces, and `new` is the text it puts there. `rule` names the convention the critic
    cited. `is_old_first` says whether the comparator sees `old` under `A`. `verdict` is `None` until a comparator
    judges the change. The comparator then sets `verdict` to `accept` or `reject`. `record_run.rule_use_counted` reads
    those words. `is_asked_alone` says whether a comparator passed over the change. A later comparator task then holds
    that change as its only item.
    """

    id: str
    old: str
    new: str
    rule: str
    is_old_first: bool
    verdict: str | None = None
    is_asked_alone: bool = False


@dataclasses.dataclass
class ProseState:
    """
    The work done on a fragment a critic has read.

    `key` names the fragment. `draft` holds the prose per site, and the tree holds the same prose. `rounds` counts the
    critic calls over the fragment. `is_dirty` says whether the draft waits for a critic. `changes` holds the changes of
    the last critic call.
    """

    key: str
    draft: list[str]
    rounds: int
    is_dirty: bool
    changes: list[Change]


@dataclasses.dataclass
class Task:
    """
    An agent call waiting to run. `kind` is `critic` or `compare`.

    A critic task lists prose digests under `items`. A comparator task lists change ids there.
    """

    id: str
    kind: str
    items: list[str]


@dataclasses.dataclass
class TaskResult:
    """
    The answer of a finished task.

    `answer` holds the object the agent answered, or `None` where the call died. A reply may stop the run, and `stopped`
    then names the cause. `stopped` is empty for a reply that lets the run go on. `said` holds the text of the reply.
    """

    task: Task
    answer: Any
    stopped: str
    said: str


def _prose_state_of(held: Any) -> ProseState | str:
    """The state a line of the prose queue holds. `UNEXAMINED` stays a string."""
    if held == UNEXAMINED:
        return UNEXAMINED
    return ProseState(**{**held, "changes": [Change(**one) for one in held["changes"]]})


def _task_result_of(held: dict[str, Any]) -> TaskResult:
    """The `TaskResult` a line of the completed tasks queue holds."""
    return TaskResult(**{**held, "task": Task(**held["task"])})


def _json_of(held: Any) -> Any:
    """`held` in the form JSON takes. A dataclass turns into a dict."""
    return dataclasses.asdict(held) if dataclasses.is_dataclass(held) and not isinstance(held, type) else held


def lines_read(path: str) -> list[Any]:
    """
    Read `path` as a JSON value per line. The answer is empty where no run has written the file.

    A kill can tear the last line. `lines_read` drops a torn last line. A torn line above the last line raises.
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        lines = [line for line in handle.read().split("\n") if line.strip()]
    if not lines:
        return []
    *above, last = lines
    held = [json.loads(line) for line in above]
    try:
        held.append(json.loads(last))
    except json.JSONDecodeError:  # not-a-failure: a kill tore this line, and the next write replaces it
        pass
    return held


def _replaced(path: str, text: str) -> None:
    """Write `text` to `path`. The text lands under a second name, and a rename moves it to `path`."""
    writing = f"{path}.writing"
    with open(writing, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(writing, path)


def rewritten(path: str, held: Iterable[Any]) -> None:
    """Write `held` to `path`, a JSON value per line."""
    _replaced(path, "".join(json.dumps(_json_of(one)) + "\n" for one in held))


def appended(path: str, held: Iterable[Any]) -> None:
    """Add the values of `held` at the end of `path`. `rewritten` says how a line reads."""
    with open(path, "a", encoding="utf-8") as handle:
        for one in held:
            handle.write(json.dumps(_json_of(one)) + "\n")


def ledger() -> list[str]:
    """The approved digests, in the order of the file. This raises on a line holding anything besides a digest."""
    held = lines_read(LEDGER)
    for one in held:
        if not isinstance(one, str) or not _A_DIGEST.fullmatch(one):
            raise ValueError(f"{LEDGER}: {one!r} is not a prose digest")
    return held


def prose_queue(into: str) -> dict[str, ProseState | str]:
    """
    Read the prose queue under `into`. The answer maps a prose digest to its state.

    A line reads as `{"digest": ..., "state": ...}`. A later line for a digest replaces an earlier one. A `null` state
    takes the digest out.
    """
    held: dict[str, ProseState | str] = {}
    for one in lines_read(os.path.join(into, _PROSE_QUEUE)):
        if one["state"] is None:
            held.pop(one["digest"], None)
        else:
            held[one["digest"]] = _prose_state_of(one["state"])
    return held


def prose_queue_rewritten(into: str, held: dict[str, ProseState | str]) -> None:
    """Write the prose queue under `into` as a line per digest."""
    rewritten(
        os.path.join(into, _PROSE_QUEUE),
        ({"digest": digest, "state": _json_of(state)} for digest, state in held.items()),
    )


def prose_state_written(into: str, digest: str, state: ProseState | str | None) -> None:
    """Append the state of `digest` to the prose queue under `into`. `None` takes the digest out."""
    appended(os.path.join(into, _PROSE_QUEUE), [{"digest": digest, "state": _json_of(state)}])


def unsettled(into: str) -> list[str]:
    """The digests of the unsettled prose queue under `into`, in the order of the file."""
    return list(lines_read(os.path.join(into, _UNSETTLED)))


def unsettled_rewritten(into: str, held: Iterable[str]) -> None:
    """Replace the unsettled prose queue under `into` with `held`."""
    rewritten(os.path.join(into, _UNSETTLED), held)


def given_up(into: str, digest: str) -> None:
    """Append `digest` to the unsettled prose queue under `into`."""
    appended(os.path.join(into, _UNSETTLED), [digest])


def pending_tasks_written(into: str, held: Iterable[Task]) -> None:
    """Write the pending tasks queue under `into`."""
    _replaced(
        os.path.join(into, _PENDING_TASKS), json.dumps([dataclasses.asdict(one) for one in held], indent=2) + "\n"
    )


@contextlib.contextmanager
def _completed_locked(into: str) -> Iterator[None]:
    """Hold the lock of the completed tasks queue under `into`."""
    with open(os.path.join(into, _COMPLETED_LOCK), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def completed(into: str, result: TaskResult) -> None:
    """Append `result` to the completed tasks queue under `into`. The append holds the lock."""
    with _completed_locked(into):
        appended(os.path.join(into, _COMPLETED_TASKS), [result])


def completed_taken(into: str) -> TaskResult | None:
    """
    Take the first result off the completed tasks queue under `into`. The answer is `None` where the queue is empty.

    The take holds the lock. The take writes the rest of the queue under a second name and then renames that file into
    place.
    """
    path = os.path.join(into, _COMPLETED_TASKS)
    with _completed_locked(into):
        held = lines_read(path)
        if not held:
            return None
        first, *rest = held
        rewritten(path, rest)
    return _task_result_of(first)


@contextlib.contextmanager
def run_locked(into: str) -> Iterator[None]:
    """
    Hold the run lock under `into`. A second run raises here rather than waiting.

    The lock is an operating system file lock. The system releases the lock of a killed run.
    """
    os.makedirs(into, exist_ok=True)
    with open(os.path.join(into, _RUN_LOCK), "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as caught:
            raise RuntimeError(f"another run holds {os.path.join(into, _RUN_LOCK)}") from caught
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
