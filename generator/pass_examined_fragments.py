# SPDX-License-Identifier: MIT
"""
Move the fragments the linguistic pass passed from the queue's `unexamined` list to its `unverified` list.

The move keeps a key with the prose digest it passed against. `update_ledger_and_queue` drops the key back to
`unexamined` on a later run where that digest stops matching.

This refuses a key the queue does not hold under `unexamined`. Such a key is a pass over prose nobody asked about, or a
pass reported twice.

Usage: `python3 generator/pass_examined_fragments.py <fragments.json> <queue.json> <passed.json>`. The passed file is a
json array of keys. This rewrites the queue in place.
"""

import json
import sys

from typing import Any

_USAGE = "usage: pass_examined_fragments.py <fragments.json> <queue.json> <passed.json>"  # the message a bad call gets.


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    """Move the passed keys across, write the queue, and print what moved."""
    if len(sys.argv) != 4:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    fragments_path, queue_path, passed_path = sys.argv[1:4]
    by_key = {one["key"]: one for one in _loaded(fragments_path)}
    queue = _loaded(queue_path)
    passed = _loaded(passed_path)
    unexamined = list(queue["unexamined"])
    unknown = [key for key in passed if key not in unexamined or key not in by_key]
    if unknown:
        for key in unknown:
            print(f"{key}: nothing unexamined holds this key", file=sys.stderr)
        sys.exit(1)
    queue["unexamined"] = sorted(set(unexamined) - set(passed))
    queue["unverified"] = dict(queue["unverified"], **{key: by_key[key]["prose"]["sha"] for key in passed})
    with open(queue_path, "w", encoding="utf-8") as handle:
        json.dump(queue, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"  {queue_path}: {len(passed)} passed, {len(queue['unexamined'])} unexamined, ", end="")
    print(f"{len(queue['unverified'])} unverified")


if __name__ == "__main__":
    main()
