# SPDX-License-Identifier: MIT
"""
Update the ledger and the settling queues from the tree. A settling run starts with this update.

`settling_state` names the files. `updated` drops a ledger digest no fragment of the tree has. `updated` drops an
unsettled digest and a prose queue entry the tree lacks the same way. A kept prose queue entry takes the key of the
fragment holding its digest. `updated` adds an `UNEXAMINED` entry for a digest missing from the ledger, the unsettled
prose queue and the prose queue. `updated` empties the pending tasks queue. The completed tasks queue keeps its results.

`converge_prose` calls `updated` under the run lock. The script takes that lock itself.

**Usage:** `python3 generator/update_ledger_and_queue.py <directory>`. The directory holds the settling queues.
"""

import dataclasses
import sys

from collections.abc import Iterable

import apply_prose
import collect_fragments
import record_run
import settling_state

_USAGE = "usage: update_ledger_and_queue.py <directory>"  # a bad call gets it.


def _kept(was: Iterable[str], held: set[str]) -> list[str]:
    """Returns the digests of `was` that `held` holds. A repeated digest keeps its first place."""
    return [digest for digest in dict.fromkeys(was) if digest in held]


def _is_sound(fragment: collect_fragments.Fragment, draft: list[str], change: settling_state.Change) -> bool:
    """Whether `change` rewrites `draft`, and the checkers pass the draft it leaves."""
    said = apply_prose.applied(draft, [dataclasses.asdict(change)])
    return said != draft and not apply_prose.draft_refusals(fragment, said)


def _purged(fragment: collect_fragments.Fragment, state: settling_state.ProseState) -> int:
    """
    Give `state` the prose the tree holds as its draft, and drop the changes that are not sound. Answer with the count
    dropped.

    The draft and the tree share a digest. The wrapping of the draft can still differ from the tree. A state left with
    no change waits for a critic.
    """
    state.draft = [site.prose for site in fragment.sites]
    kept = [one for one in state.changes if _is_sound(fragment, state.draft, one)]
    dropped = len(state.changes) - len(kept)
    if dropped:
        state.changes = kept
        state.is_dirty = True
    return dropped


def updated(into: str, fragments: Iterable[collect_fragments.Fragment]) -> str:
    """
    Update the ledger and the queues under `into` from `fragments`, and say what they hold.

    A kept prose queue entry comes ahead of a new one. A run then takes up the started work first. `_purged` cleans a
    kept entry. A suggested proposal the checkers refuse leaves the suggested proposals queue.

    Public. `converge_prose` calls this as a run starts.
    """
    by_fragment: dict[str, collect_fragments.Fragment] = {}
    for one in fragments:
        by_fragment.setdefault(one.prose.sha, one)
    by_digest = {digest: one.key for digest, one in by_fragment.items()}
    tree = set(by_digest)
    was = settling_state.ledger()
    ledger = _kept(was, tree)
    unsettled = _kept(settling_state.unsettled(into), tree - set(ledger))
    settled = set(ledger) | set(unsettled)
    queue: dict[str, settling_state.ProseState | str] = {}
    purged = 0
    for digest, state in settling_state.prose_queue(into).items():
        if digest not in tree or digest in settled:
            continue
        if isinstance(state, settling_state.ProseState):
            state.key = by_digest[digest]
            purged += _purged(by_fragment[digest], state)
        queue[digest] = state
    suggested = settling_state.lines_read(settling_state.SUGGESTED)
    proposals = [one for one in suggested if not record_run.proposal_refusals(one)]
    for digest in by_digest:
        if digest not in settled:
            queue.setdefault(digest, settling_state.UNEXAMINED)
    settling_state.rewritten(settling_state.LEDGER, ledger)
    settling_state.unsettled_rewritten(into, unsettled)
    settling_state.prose_queue_rewritten(into, queue)
    settling_state.pending_tasks_written(into, [])
    if len(proposals) < len(suggested):
        settling_state.rewritten(settling_state.SUGGESTED, proposals)
    started = [digest for digest, state in queue.items() if state != settling_state.UNEXAMINED]
    return (
        f"the tree has {len(tree)} prose digest(s). the ledger holds {len(ledger)} of them, and dropped "
        f"{len(set(was)) - len(ledger)}. the prose queue holds {len(queue)}, and work started on {len(started)}. "
        f"the unsettled prose queue holds {len(unsettled)}. {purged} queued change(s) and "
        f"{len(suggested) - len(proposals)} suggested proposal(s) failed the checkers and left."
    )


def main() -> None:
    """Take the run lock over the directory the command line names. Then update the ledger and the queues there."""
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    into = sys.argv[1]
    with settling_state.run_locked(into):
        print(updated(into, collect_fragments.fragments()))


if __name__ == "__main__":
    main()
