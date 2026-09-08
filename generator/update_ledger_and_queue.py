# SPDX-License-Identifier: MIT
"""
Reduce the ledger to what the tree still bears out, and queue the remainder for review.

The ledger records a fragment reviewed clean against the tree of that day. It holds a prose digest and a code digest per
key. `collect_fragments` writes what the tree says. This compares the pair.

An entry survives while the prose digest and the code digest both still match. An entry survives while the code of what
the prose cites has held still. This writes an entry that goes out of the ledger before any review starts. The review
can then stop part way and the ledger stays true.

The queue holds what the reduced ledger leaves out. A key sits in `unverified` where the linguistic pass has passed it,
kept with the prose digest that pass used. A key whose prose was rewritten since goes back to `unexamined`.

Usage: `python3 generator/update_ledger_and_queue.py <fragments.json> <ledger.json> <queue.json>`. This reads the ledger
and the queue as they are, and writes both in place.
"""

import json
import os
import sys

from typing import Any

_USAGE = "usage: update_ledger_and_queue.py <fragments.json> <ledger.json> <queue.json>"  # the message a bad call gets.

# The names the ledger records against a key, and the names the queue calls its arrays.
_PROSE = "prose"
_CODE = "code"  # the other half. The same digest covers it.
_UNEXAMINED = "unexamined"  # the queue's array of keys the linguistic pass has yet to read.
_UNVERIFIED = "unverified"  # the queue's mapping from a key that pass passed to the prose digest it passed against.


def _loaded(path: str) -> dict[str, Any]:
    """The JSON `path` holds. Empty where no run has written it yet."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        held: dict[str, Any] = json.load(handle)
    return held


def _written(path: str, held: object) -> None:
    """Write `held` to `path` as json."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(held, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _by_key(path: str) -> dict[str, Any]:
    """The fragments `collect_fragments` wrote, keyed by the key they hold."""
    with open(path, encoding="utf-8") as handle:
        held: list[dict[str, Any]] = json.load(handle)
    return {one["key"]: one for one in held}


def _does_the_tree_bear_out(key: str, was: dict[str, Any], now: dict[str, Any]) -> bool:
    """
    Whether the ledger's entry for `key` still holds against the tree.

    The entry holds while both digests match. The entry holds while the code of what the prose cites matches what the
    ledger recorded for that fragment. A citation the ledger does not hold settles nothing, and the entry goes.
    """
    if key not in now:
        return False
    if was[key][_PROSE] != now[key][_PROSE]["sha"] or was[key][_CODE] != now[key][_CODE]["sha"]:
        return False
    for cited in now[key]["references"]:
        if cited not in was or cited not in now or was[cited][_CODE] != now[cited][_CODE]["sha"]:
            return False
    return True


def _reduced(was: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    """
    The ledger, less what the tree has stopped bearing out.

    This decides a key against the ledger as it loaded. Deciding against a ledger already reduced would let an entry
    outlive the citation that invalidated it.
    """
    return {key: was[key] for key in sorted(was) if _does_the_tree_bear_out(key, was, now)}


def _queued(now: dict[str, Any], ledger: dict[str, Any], was: dict[str, str]) -> dict[str, Any]:
    """
    The keys the reduced ledger does not hold, split by what the linguistic pass has already said.

    A key that pass passed stays in `unverified` while the prose digest still matches the digest that pass used. Prose
    rewritten since has not had that pass, and the key goes to `unexamined`.
    """
    unverified = {
        key: sha
        for key, sha in sorted(was.items())
        if key in now and key not in ledger and now[key][_PROSE]["sha"] == sha
    }
    unexamined = sorted(key for key in now if key not in ledger and key not in unverified)
    return {_UNEXAMINED: unexamined, _UNVERIFIED: unverified}


def main() -> None:
    """Reduce the ledger against the fragments, write it, and write the queue of the remainder."""
    if len(sys.argv) != 4:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    fragments_path, ledger_path, queue_path = sys.argv[1:4]
    tree = _by_key(fragments_path)
    was = _loaded(ledger_path)
    queue = _loaded(queue_path)
    ledger = _reduced(was, tree)
    _written(ledger_path, ledger)
    held = _queued(tree, ledger, queue.get(_UNVERIFIED, {}))
    _written(queue_path, held)
    print(f"  {ledger_path}: {len(ledger)} of {len(was)} entries hold over {len(tree)} fragment(s)")
    print(f"  {queue_path}: {len(held[_UNEXAMINED])} unexamined, {len(held[_UNVERIFIED])} unverified")


if __name__ == "__main__":
    main()
