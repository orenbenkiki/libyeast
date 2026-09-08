# SPDX-License-Identifier: MIT
"""
Write the prose of the queue's unexamined fragments into batches.

The linguistic pass reads prose. A batch gives a fragment's key, where the fragment is, and the fragment's prose. A
batch holds no code. A reader holding code starts checking claims. Claim checking belongs to the settling pass.

`_A_BATCH` fragments go in a batch. A run writes `_MOST_BATCHES` batches. A caller may lower both. The queue holds the
remaining fragments for a later run. This deletes the batches a previous run wrote.

`manifest.json` names the batches. This writes a workflow script too. A workflow script opens no file. The script holds
`.claude/conventions.md`, `.claude/rejected.md` and the prose of a batch as string literals.

**Usage:** `python3 generator/batch_pending_fragments.py <fragments.json> <queue.json> <directory> [batches]`.
"""

import json
import os
import re
import sys

from typing import Any

import gate

_USAGE = "usage: batch_pending_fragments.py <fragments.json> <queue.json> <directory> [batches]"  # a bad call gets it.

# The file that names the batches. It sits beside the batches themselves.
_MANIFEST = "manifest.json"

# The number of fragments a reader gets at once.
_A_BATCH = 50
_MOST_BATCHES = 12  # the count of readers a run asks for. The rest of the queue waits for a later run.

# The name a batch file takes. A stale batch file has the same name.
_A_BATCH_FILE = re.compile(r"batch\.[0-9]+\.txt")

# The workflow script this writes beside the batches.
_EXAMINE = "examine.js"

# The documents a reader judges against. `held_to` reads them as a run batches.
_HELD_TO = (".claude/conventions.md", ".claude/rejected.md")

# A reader reads this above the prose of its batch. `.claude/agents/prose-critic.md` says how a reader answers.
# `converge_fragments` asks a reader the same thing inside a settling loop.
ASK = (
    "Judge the writing of this prose. Do not judge whether it is true.\n\n"
    "The conventions you judge against come below. The proposals the author has turned down follow those. Your "
    "batch comes last. Sort the keys of the batch into `passed`, `rewritten` and `out_of_budget`. A key goes in "
    "a single list and no more."
)

# A short batch a reader answers ahead of its own. Answering it writes the fixed prefix into the cache.
_SEED = "### seed\nseed (seed)\nA seed fragment. The batch drops the answer to it.\n"

# The shape a reader answers in.
EXAMINED: dict[str, Any] = {
    "type": "object",
    "required": ["passed", "rewritten", "out_of_budget"],
    "properties": {
        "passed": {
            "type": "array",
            "description": "the key of a fragment whose prose you found sound, written as the batch writes it",
            "items": {"type": "string"},
        },
        "rewritten": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "parts"],
                "properties": {
                    "key": {"type": "string", "description": "the fragment key, written as the batch writes it"},
                    "where": {"type": "string", "description": "the place the batch gives for that fragment"},
                    "parts": {
                        "type": "array",
                        "description": "the whole prose of that fragment said again, a part per part the batch shows. "
                        "this is no patch and no single sentence. a batch showing no part heading wants a single part",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "out_of_budget": {
            "type": "array",
            "description": "the key of a fragment you ran out of room to read, written as the batch writes it",
            "items": {"type": "string"},
        },
        "proposals": {
            "type": "array",
            "description": "a tightening of a write-time checker that would have caught a fault you rewrote",
            "items": {
                "type": "object",
                "required": ["rule", "why", "seen"],
                "properties": {
                    "rule": {
                        "type": "string",
                        "description": "the checker change, stated the way `.claude/conventions.md` states a rule. a "
                        "hyphenated name, then what the checker refuses. the word rules read this wording, and they "
                        "refuse a universal and a count.",
                    },
                    "why": {
                        "type": "string",
                        "description": "the gain for a reader, and what goes wrong without the change",
                    },
                    "seen": {"type": "string", "description": "the fragment key and sentence you rewrote for it"},
                },
            },
        },
    },
}


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def held_to() -> str:
    """The whole text of the documents a reader judges against."""
    said = []
    for name in _HELD_TO:
        with open(os.path.join(gate.TREE, name), encoding="utf-8") as handle:
            said.append(handle.read())
    return "\n\n".join(said)


def _parted(sites: list[dict[str, Any]], prose: str) -> str:
    """
    The prose a reader sees, with a heading over a part where the fragment appears in more than a single place.

    A struct's own comment and the comment on a field are parts apart. A reader that merged them would leave an applier
    guessing which sentence documents which field. A single-site fragment gets no heading.
    """
    if len(sites) < 2:
        return f"{prose}\n"
    said = []
    for at, site in enumerate(sites, start=1):
        said.append(f"--- part {at} of {len(sites)}, at {site['path']}:{site['lines'][0]}\n{site['prose']}")
    return "\n".join(said) + "\n"


def _record(fragment: dict[str, Any]) -> str:
    """A fragment as an examiner reads it. The key, the place it appears, and the prose."""
    return (
        f"### {fragment['key']}\n"
        f"{fragment['path']}:{fragment['first']} ({fragment['kind']})\n"
        f"{_parted(fragment['sites'], fragment['prose']['content'])}"
    )


def _cleared(into: str) -> None:
    """Remove the batches, the manifest and the workflow script a previous run wrote."""
    for name in (_MANIFEST, _EXAMINE):
        stale = os.path.join(into, name)
        if os.path.exists(stale):
            os.remove(stale)
    for path in gate.named_in(into, ".txt"):
        if _A_BATCH_FILE.fullmatch(path.name):
            os.remove(path)


def _written(into: str, at: int, of: int, records: list[str]) -> str:
    """Write a batch and answer with its path."""
    path = os.path.join(into, f"batch.{at}.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"(batch {at} of {of}, {len(records)} fragment(s))\n\n" + "\n".join(records))
    return path


def _script_written(into: str, prose: list[str]) -> str:
    """Write the workflow that reads the batches, and answer with its path."""
    ask = ASK + "\n\n" + held_to()
    body = (
        "export const meta = {\n"
        "  name: 'critic-examine',\n"
        "  description: 'Read a batch of prose fragments and say the faulty ones again',\n"
        "  phases: [{ title: 'Examine' }],\n"
        "}\n\n"
        f"const ASK = {json.dumps(ask)}\n\n"
        f"const SEED = {json.dumps(_SEED)}\n\n"
        f"const EXAMINED = {json.dumps(EXAMINED, indent=2)}\n\n"
        f"const BATCHES = {json.dumps(prose, indent=2)}\n\n"
        "phase('Examine')\n\n"
        "// A reader answers the seed first and writes the fixed prefix into the cache. A later reader reads\n"
        "// that prefix instead of writing a copy apiece. A seed of another shape warms another prefix and buys\n"
        "// nothing.\n"
        "await agent(`${ASK}\\n\\n${SEED}`, {\n"
        "  label: 'seed',\n"
        "  phase: 'Examine',\n"
        "  schema: EXAMINED,\n"
        "  agentType: 'prose-critic',\n"
        "})\n\n"
        "const ANSWERED = await parallel(\n"
        "  BATCHES.map((said, at) => () =>\n"
        "    agent(`${ASK}\\n\\nThis is batch ${at + 1} of ${BATCHES.length}.\\n\\n${said}`, {\n"
        "      label: `examine:${at + 1}`,\n"
        "      phase: 'Examine',\n"
        "      schema: EXAMINED,\n"
        "      agentType: 'prose-critic',\n"
        "    }),\n"
        "  ),\n"
        ")\n\n"
        "return ANSWERED.map((one) => one || { passed: [], rewritten: [], out_of_budget: [], proposals: [] })\n"
    )
    path = os.path.join(into, _EXAMINE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def main() -> None:
    """Write the batches into the directory named on the command line, and print the paths."""
    if len(sys.argv) not in (4, 5):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    fragments_path, queue_path, into = sys.argv[1:4]
    most = min(int(sys.argv[4]), _MOST_BATCHES) if len(sys.argv) == 5 else _MOST_BATCHES
    by_key = {one["key"]: one for one in _loaded(fragments_path)}
    unexamined = [key for key in _loaded(queue_path)["unexamined"] if key in by_key]
    os.makedirs(into, exist_ok=True)
    _cleared(into)
    taking = unexamined[: _A_BATCH * most]
    batches = [taking[at : at + _A_BATCH] for at in range(0, len(taking), _A_BATCH)]
    records = [[_record(by_key[key]) for key in keys] for keys in batches]
    written = [_written(into, at, len(batches), each) for at, each in enumerate(records, start=1)]
    examine = os.path.abspath(_script_written(into, ["\n".join(each) for each in records]))
    manifest = {
        "batches": written,
        "examine": examine,
        "batched": len(taking),
        "unexamined": len(unexamined),
    }
    with open(os.path.join(into, _MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    for path in written:
        print(f"  {path}")
    print(f"  {len(taking)} of {len(unexamined)} unexamined fragment(s) in {len(batches)} batch(es)")


if __name__ == "__main__":
    main()
