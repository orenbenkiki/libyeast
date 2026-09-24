# SPDX-License-Identifier: MIT
"""
Write the prose of the queue's unexamined fragments into batches.

The linguistic pass reads prose. A batch gives a fragment's key, where the fragment is, and the fragment's prose. A
batch holds no code.

`_A_BATCH` fragments go in a batch. A run writes `_MOST_BATCHES` batches. A caller may lower both. The queue holds the
remaining fragments for a later run. A run deletes the batches a previous run wrote.

`manifest.json` names the batches. This writes a workflow script too. A workflow script opens no file. The script holds
`.claude/conventions.md`, `.claude/rejected.md` and the prose of a batch as string literals.

**Usage:** `python3 generator/batch_pending_fragments.py <fragments.json> <queues> <directory> [batches]`. `queues`
names the directory of the settling queues.
"""

import json
import os
import re
import sys

from typing import Any

import check_conventions
import gate
import settling_state

_USAGE = "usage: batch_pending_fragments.py <fragments.json> <queues> <directory> [batches]"  # a bad call gets it.

# The file that names the batches. It sits beside the batches themselves.
_MANIFEST = "manifest.json"

# The number of fragments a reader gets at once.
_A_BATCH = 50
_MOST_BATCHES = 12  # the count of readers a run asks for. The rest of the queue waits for a later run.

# The name a batch file takes. A stale batch file has the same name.
_A_BATCH_FILE = re.compile(r"batch\.[0-9]+\.txt")

# The workflow script this module writes beside its batches.
_EXAMINE = "examine.js"

# The rules `rules_by_use` names. A longer list decays the way a long conversation does.
_RULES_SHOWN = 8

# A critic cites a rule this many times before the share turned down says anything. A rule under this bar reports a
# share the next round moves.
_FEWEST_CITED = 100

# The proposals a reader raises from a batch. A reader holds a batch and no view of the tree. The reader cannot sweep a
# pattern and cannot see how far a rule would reach. A cap makes the reader pick rather than list. `sorted` drops a
# proposal past the cap. The schema states no cap. A reader answering past a schema cap fails the call outright.
MOST_PROPOSALS = 1

# The heading `rules_by_use` writes above its list.
_BROKEN_RULES = (
    "## How your changes have fared\n\n"
    "A settling round cited these rules. The comparator read the rewrite against the prose it replaces, and the "
    "counts say how that went. A rule turned down often is a rule this project's writers reach for and misapply."
)

# A reader reads this above the prose of its batch. `.claude/agents/prose-critic.md` says how a reader answers.
# `converge_prose` asks a reader the same thing inside a settling loop.
ASK = (
    "Judge the writing of this prose. Do not judge whether it is true.\n\n"
    "Your definition holds the conventions you judge against. It holds the proposals the author has turned down. "
    "Your batch comes below. Answer with a verdict per key of that batch. A key of another batch belongs to another "
    "reader. An answer leaving a key out comes back to you.\n\n"
    "A rewrite is a list of changes. A change cites by name the rule of `.claude/conventions.md` the old text "
    "breaks. Drop a change you cannot cite a rule for."
)

# A short batch a reader answers ahead of its own. Answering it writes the fixed prefix into the cache.
SEED = "### seed\nseed (seed)\nA seed fragment. The batch drops the verdict it gets back.\n"

# The JS both workflows hold. `EXAMINED` is the schema a reader answers in. `sorted` reads the verdicts back into the
# lists a caller walks.
_HELPERS = """// The shape a reader answers a batch in. The shape holds still between calls. The fixed prefix in front
// of a batch then stays in the cache. A schema naming the keys of a batch would change per call, and a changed
// schema throws the prefix away.
//
// `VERDICTS_SAY` tells a reader to give a verdict per key of its batch. The caller drops a key from outside that batch.
const EXAMINED = {
  type: 'object',
  required: ['verdicts'],
  properties: {
    verdicts: {
      type: 'object',
      description: VERDICTS_SAY,
      additionalProperties: A_VERDICT,
    },
    proposals: PROPOSALS,
  },
}

// The verdicts of `said`, sorted into the lists a caller reads. A `rewritten` verdict citing no change joins what the
// reader ran out of room for.
const sorted = (said) => {
  const raised = ((said && said.proposals) || []).slice(0, MOST_PROPOSALS)
  const held = { passed: [], rewritten: [], out_of_budget: [], proposals: raised }
  for (const [key, one] of Object.entries((said && said.verdicts) || {})) {
    if (one && one.verdict === 'rewritten' && Array.isArray(one.changes) && one.changes.length) {
      held.rewritten.push({ key, changes: one.changes })
    } else if (one && one.verdict === 'passed') {
      held.passed.push(key)
    } else {
      held.out_of_budget.push(key)
    }
  }
  return held
}

// The prose of each site once a rewrite's changes apply. A change names the text it replaces. This rewrites the
// earliest run of that text. The rewrite lands in the site holding the text. A change quoting text no site holds
// rewrites nothing. `apply_prose.applied` answers the same question in Python.
const applied = (proseBySite, changes) => {
  const held = proseBySite.slice()
  for (const one of changes) {
    const at = held.findIndex((said) => one.old && said.includes(one.old))
    if (at >= 0) held[at] = held[at].replace(one.old, one.new)
  }
  return held
}
"""

# The description the verdicts of an answer take. Both workflows write it.
_VERDICTS_SAY = "a verdict per key of the batch. a key outside that batch belongs to another reader"

# The verdict a reader gives a fragment of its batch.
_A_VERDICT: dict[str, Any] = {
    "type": "object",
    "required": ["verdict"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["passed", "rewritten", "out_of_budget"],
            "description": "your finding for this fragment. `passed` says the prose is sound. `rewritten` says you "
            "said areas of the prose again, and `changes` then holds them. `out_of_budget` says you ran out of room "
            "to read it",
        },
        "changes": {
            "type": "array",
            "description": "a change per area of the text you rewrote. cite a rule for an area or make no change "
            "there",
            "items": {
                "type": "object",
                "required": ["old", "new", "rule"],
                "properties": {
                    "old": {
                        "type": "string",
                        "description": "the text you replace, copied out of the fragment character for character",
                    },
                    "new": {"type": "string", "description": "the text that replaces `old`"},
                    "rule": {
                        "type": "string",
                        "description": "the name of the bullet of `.claude/conventions.md` the old text breaks",
                    },
                },
            },
        },
    },
}

# The rest of the shape a reader answers in. `EXAMINED` puts the verdicts of a batch beside it.
_PROPOSED: dict[str, Any] = {
    "properties": {
        "proposals": {
            "type": "array",
            "description": "a tightening of a write-time checker that would have caught a fault you rewrote. a batch "
            "raises the proposal you believe in hardest and leaves a weaker proposal unsaid",
            "items": {
                "type": "object",
                "required": ["rule", "why", "seen", "before", "after"],
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
                    "seen": {
                        "type": "string",
                        "description": "the key of the fragment you rewrote, and no sentence beside it. `before` "
                        "quotes the prose, and a quote outside backticks breaks the shape rules where it lands",
                    },
                    "before": {
                        "type": "string",
                        "description": "a short quote of prose the proposed checker refuses",
                    },
                    "after": {
                        "type": "string",
                        "description": "the same prose said again, in the form the proposed checker passes",
                    },
                },
            },
        },
    },
}


# The answer shape a batch takes. The shape holds still between calls. The fixed prefix in front of a batch then stays
# in the cache. `_VERDICTS_SAY` tells a reader to give a verdict per key of its batch.
EXAMINED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "object",
            "description": _VERDICTS_SAY,
            "additionalProperties": _A_VERDICT,
        },
        **_PROPOSED["properties"],
    },
}


# The sides of a pair a comparator may pick. `converge_prose` reads a verdict against these.
SIDES = ("A", "B")

# The verdict a comparator gives where neither side reads better. `converge_prose` rejects the change then.
A_TIE = "equivalent"

# The shape a comparator answers in. A verdict names the pair it judged, and the batch fixes no id in this shape.
JUDGED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "description": "a verdict per pair in the batch, in the order the pairs arrive",
            "items": {
                "type": "object",
                "required": ["pair", "verdict"],
                "properties": {
                    "pair": {"type": "string", "description": "the pair's id, written as the batch writes it"},
                    "verdict": {
                        "type": "string",
                        "enum": [*SIDES, A_TIE],
                        "description": "the version that reads better, or `equivalent` where neither does",
                    },
                },
            },
        },
    },
}

# The shape a condense answers in. The critic states a proposal in that same item shape.
CONDENSED: dict[str, Any] = {
    "type": "object",
    "required": ["proposals"],
    "properties": {"proposals": EXAMINED["properties"]["proposals"]},
}


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def rules_by_use() -> str:
    """
    A line per rule, saying how often a critic cited that rule and how often the comparator turned the change down.

    Public. A workflow puts this in front of a reader. `.claude/hooks/standing-rules.sh` puts it in front of the session
    at the keyboard. The pair reads this function and cannot drift apart.

    A rule cited often and turned down often is a rule a writer reaches for and misapplies. `.claude/rule-use.json`
    holds both counts. The list runs in the order of the share turned down. A rule `check_conventions.mechanised_rules`
    names stays out. A rule cited fewer times than `_FEWEST_CITED` stays out as well.
    """
    decided = check_conventions.mechanised_rules()
    held = [
        (one.get("cited", 0) - one.get("accepted", 0), one.get("cited", 0), name)
        for name, one in gate.rule_use().items()
        if name not in decided and one.get("cited", 0) >= _FEWEST_CITED
    ]
    if not held:
        return ""
    ranked = sorted(held, key=lambda one: (-one[0] / one[1], -one[1]))[:_RULES_SHOWN]
    lines = "\n".join(
        f"- `{name}`: cited {cited} time(s), and the comparator turned down {rejected} of them "
        f"({rejected / cited:.0%})"
        for rejected, cited, name in ranked
    )
    return f"{_BROKEN_RULES}\n\n{lines}"


def sited(fragment: dict[str, Any]) -> str:
    """
    The prose a reader sees. A fragment with more than a single site gets a heading per site.

    A struct's own comment and the comment on a field are sites apart.

    Public. `converge_prose` writes the prose of a critic's batch this way too.
    """
    sites = fragment["sites"]
    if len(sites) < 2:
        return f"{fragment['prose']['content']}\n"
    said = []
    for at, site in enumerate(sites, start=1):
        line = fragment["starts"][site["path"]] + site["lines"][0]
        said.append(f"--- site {at} of {len(sites)}, at line {line} of {site['path']}.\n{site['prose']}")
    return "\n".join(said) + "\n"


def _record(fragment: dict[str, Any]) -> str:
    """A fragment as an examiner reads it. It holds the key, the place the fragment appears, and the prose."""
    return (
        f"### {fragment['key']}\n"
        f"{fragment['path']}:{fragment['starts'][fragment['path']]} ({fragment['kind']})\n"
        f"{sited(fragment)}"
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
        handle.write(f"(batch {at} of {of} holding {len(records)} fragment(s))\n\n" + "\n".join(records))
    return path


def _script_written(into: str, prose: list[str]) -> str:
    """Write the workflow that reads the batches, and answer with its path."""
    ask = ASK + "\n\n" + rules_by_use()
    body = (
        "export const meta = {\n"
        "  name: 'critic-examine',\n"
        "  description: 'Read a batch of prose fragments and say the faulty ones again',\n"
        "  phases: [{ title: 'Examine' }],\n"
        "}\n\n"
        f"const ASK = {json.dumps(ask)}\n\n"
        f"const SEED = {json.dumps(SEED)}\n\n"
        f"const A_VERDICT = {json.dumps(_A_VERDICT, indent=2)}\n\n"
        f"const PROPOSALS = {json.dumps(_PROPOSED['properties']['proposals'], indent=2)}\n\n"
        f"const MOST_PROPOSALS = {MOST_PROPOSALS}\n\n"
        f"const VERDICTS_SAY = {json.dumps(_VERDICTS_SAY)}\n\n"
        f"{_HELPERS}\n"
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
        "return ANSWERED.map((one) => sorted(one))\n"
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
    fragments_path, queues, into = sys.argv[1:4]
    most = min(int(sys.argv[4]), _MOST_BATCHES) if len(sys.argv) == 5 else _MOST_BATCHES
    by_key = {one["key"]: one for one in _loaded(fragments_path)}
    queue = settling_state.prose_queue(queues)
    unexamined = [key for key, one in by_key.items() if queue.get(one["prose"]["sha"]) == settling_state.UNEXAMINED]
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
