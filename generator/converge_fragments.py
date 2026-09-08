# SPDX-License-Identifier: MIT
"""
Write the workflow that settles the prose of the queue's next fragments.

A fragment's prose opens as the draft. A round asks the critic to say the drafts again, a batch of fragments to a call.
The comparator then reads a draft beside its rewrite and names the better side, a batch of pairs to a call. A coin
decides which side of the pair the rewrite goes on. A rewrite that wins becomes the draft. The loop drops a rewrite that
loses.

A fragment leaves the working set where the critic passes the draft, and where a rewrite fails to win. The round after
packs the fragments still moving into full batches again.

A fragment's loop ends on any of these. The critic passes the draft. A rewrite fails to win. The rounds run out.

`_A_POOL` fragments go in a run. `batch_pending_fragments` holds the prompt and the answer shape the critic reads. This
module holds the comparator's prompt and answer shape.

A workflow script opens no file. The script holds `.claude/conventions.md`, `.claude/rejected.md` and the fragments'
prose as string literals.

**Usage:** `python3 generator/converge_fragments.py <queue.json> <directory> [count]`.
"""

import json
import os
import random
import sys

from typing import Any

import batch_pending_fragments
import collect_fragments
import gate

# A bad call gets this.
_USAGE = (
    "usage: converge_fragments.py <queue.json> <directory> [count]\n"
    "       converge_fragments.py --proposals <directory> <run.json>..."
)

# The flag asking for the condense with no settling loop in front of it.
_ALONE = "--proposals"

# The workflow this writes. A caller hands the path to the Workflow tool.
_CONVERGE = "converge.js"

# The fragments a critic call reads.
_A_CRITIC_BATCH = 10

# The pairs a comparator call reads.
_A_COMPARE_BATCH = 20

# The fragments a run takes off the queue. A caller may ask for more.
_A_POOL = 20

# The rounds a run allows. A fragment still moving at the last round keeps its draft.
_MOST_ROUNDS = 7

# The coins that decide which side of a pair the rewrite goes on. This module throws them while writing the script. A
# workflow script throws no coin. A resumed run reads the prompt of the run before it.
_COINS = random.Random()

# A condense reads these beside the conventions. A proposal already ruled comes back no more.
_RULED = ".claude/proposals-pending.md"

# The ask a condense reads. `.claude/proposals-pending.md` takes what comes back.
_CONDENSE = (
    "Condense the proposals below into a shorter list. Do not judge the prose they came from.\n\n"
    "The conventions come below. The proposals the author turned down follow those. The proposals awaiting a "
    "ruling follow those. The proposals of this run come last. A pair of proposals stating the same rule "
    "becomes a single proposal. A proposal an accepted rule already covers comes out. State a rule so a checker can "
    "decide it.\n\n"
    "State a rule the way `.claude/conventions.md` states one. An accepted proposal lands in that file, and the "
    "checkers read the wording there. Write a hyphenated name, then what the checker refuses. The word rules refuse "
    "a universal and a count, and they read your wording too. `refuses a sentence ending on a verb` passes where "
    "`refuses every sentence that ends on a verb` gets turned back."
)

# The shape a condense answers in. The critic states a proposal in that same item shape.
_CONDENSED: dict[str, Any] = {
    "type": "object",
    "required": ["proposals"],
    "properties": {"proposals": batch_pending_fragments.EXAMINED["properties"]["proposals"]},
}

# A comparator reads this above its pair. `.claude/agents/prose-compare.md` says how a comparator answers.
_JUDGE = (
    "Judge which version of this prose reads better. Do not judge whether it is true.\n\n"
    "The conventions you judge against come below. The proposals the author has turned down follow those. Your "
    "batch of pairs comes last. Answer with a verdict of `A`, `B` or `equivalent` for a pair."
)

# The shape a comparator answers in.
_JUDGED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "description": "a verdict per pair in the batch, in the order the pairs arrive",
            "items": {
                "type": "object",
                "required": ["key", "verdict", "why"],
                "properties": {
                    "key": {"type": "string", "description": "the pair's key, written as the batch writes it"},
                    "verdict": {
                        "type": "string",
                        "enum": ["A", "B", "equivalent"],
                        "description": "the version that reads better, or `equivalent` where neither does",
                    },
                    "why": {
                        "type": "string",
                        "description": "a sentence or two naming the concrete difference that decided it",
                    },
                },
            },
        },
    },
}

# The body of the workflow. The constants written above it name the fragments, the asks and the answer shapes.
_BODY = """phase('Converge')

const chunked = (held, size) => {
  const out = []
  for (let at = 0; at < held.length; at += size) out.push(held.slice(at, at + size))
  return out
}

// A fragment stands in more than a single place where a struct's comment and a field's comment belong to it. A part
// heading over each keeps them apart, and the answer comes back a part per part.
const parted = (one, parts) =>
  one.sites.length < 2
    ? `${parts[0]}\\n`
    : parts
        .map((said, at) => `--- part ${at + 1} of ${parts.length}, at ${one.sites[at].path}:${one.sites[at].lines[0]}`
          + `\\n${said}`)
        .join('\\n') + '\\n'

const asked = (one) => `### ${one.key}\\n${one.where}\\n${parted(one, one.parts)}`

// A coin decides which side of the pair the rewrite stands on. A fragment holds a throw for a round.
const isDraftFirst = (one, round) => one.coin[(round - 1) % one.coin.length]

const paired = (one, say, round) => {
  const draft = parted(one, one.parts)
  const other = parted(one, say)
  return isDraftFirst(one, round)
    ? `### ${one.key}\\n\\n#### A\\n${draft}\\n#### B\\n${other}`
    : `### ${one.key}\\n\\n#### A\\n${other}\\n#### B\\n${draft}`
}

const done = []
const proposals = []
let held = FRAGMENTS.slice()
let round = 0

const settle = (one, why) => {
  done.push({
    key: one.key,
    where: one.where,
    sites: one.sites,
    was: one.was,
    parts: one.parts,
    settled: why,
    rounds: one.rounds,
  })
  log(`${one.key}: ${one.rounds} round(s), ${why}`)
}

// A round asks the critic about the working set, a batch of fragments to a call. Further calls ask the comparator
// about the pairs that came back, a batch of pairs to a call. A fragment that settles leaves the set, and the round
// after it packs the rest into full batches again.
while (held.length && round < MOST_ROUNDS) {
  round += 1
  const answers = await parallel(
    chunked(held, A_CRITIC_BATCH).map((batch, at) => () =>
      agent(`${ASK}\\n\\n${batch.map(asked).join('\\n')}`, {
        label: `critic:${round}.${at + 1}`,
        phase: 'Converge',
        schema: EXAMINED,
        agentType: 'prose-critic',
      }),
    ),
  )
  const rewritten = new Map()
  const passed = new Set()
  for (const said of answers) {
    if (!said) continue
    for (const key of said.passed || []) passed.add(key)
    for (const one of said.rewritten || []) {
      if (one && one.key && Array.isArray(one.parts) && one.parts.length) rewritten.set(one.key, one.parts)
    }
    proposals.push(...(said.proposals || []))
  }

  const pairs = []
  const standing = []
  for (const one of held) {
    const say = rewritten.get(one.key)
    if (say && say.join('\\n') !== one.parts.join('\\n')) {
      pairs.push({ one, say })
    } else if (passed.has(one.key)) {
      one.rounds = round
      settle(one, 'the critic passed the draft')
    } else {
      standing.push(one)
    }
  }

  const verdicts = new Map()
  if (pairs.length) {
    const judged = await parallel(
      chunked(pairs, A_COMPARE_BATCH).map((batch, at) => () =>
        agent(`${JUDGE}\\n\\n${batch.map(({ one, say }) => paired(one, say, round)).join('\\n')}`, {
          label: `compare:${round}.${at + 1}`,
          phase: 'Converge',
          schema: JUDGED,
          agentType: 'prose-compare',
        }),
      ),
    )
    for (const got of judged) {
      for (const said of (got && got.verdicts) || []) if (said && said.key) verdicts.set(said.key, said)
    }
  }

  for (const { one, say } of pairs) {
    const got = verdicts.get(one.key)
    const verdict = (got && got.verdict) || 'equivalent'
    one.rounds = round
    if (isDraftFirst(one, round) ? verdict !== 'B' : verdict !== 'A') {
      settle(one, `the comparator left the draft standing (${verdict})`)
      continue
    }
    one.parts = say
    standing.push(one)
  }
  log(`round ${round}: ${pairs.length} pair(s) judged, ${standing.length} fragment(s) still moving`)
  held = standing
}

// A fragment the critic left unnamed reaches here too. A silent critic does not read as a pass.
for (const one of held) settle(one, 'the rounds ran out and the prose was still moving')

const moved = done.filter((one) => one.parts.join('\\n') !== one.was.join('\\n'))
log(`${moved.length} of ${done.length} fragment(s) moved`)

"""

# The tail of the settling workflow. The condense sits between this and the body above it.
_SETTLED = """
return { settled: done, moved: moved.map((one) => one.key), proposals: condensed, raw: proposals.length }
"""

# The tail of the condense workflow.
_ALONE_TAIL = """
return { proposals: condensed, raw: proposals.length }
"""

# The condense, as both workflows write it. A caller declares `proposals` above this block and writes a return under it.
_CONDENSING = """phase('Condense')

// A run raises a proposal per fragment it rewrote, and a pair of them often states the same rule. One reader reads
// them beside the rulings that stand, and answers with the list the author rules on.
let condensed = []
if (proposals.length) {
  const said = await agent(`${CONDENSE}\\n\\n${JSON.stringify(proposals, null, 2)}`, {
    label: 'condense',
    phase: 'Condense',
    schema: CONDENSED,
    agentType: 'prose-critic',
  })
  condensed = (said && said.proposals) || []
  log(`${proposals.length} proposal(s) came back, ${condensed.length} after condensing`)
  const recorded = await agent(
    `Append the proposals below to \\`${RULED}\\`. Group them under a \\`## From \\\\\\`converge\\\\\\`\\` heading. ` +
      `Write a proposal as a numbered list item opening with the rule in bold, then an indented \\`*Why:*\\` ` +
      `paragraph and an indented \\`*Seen:*\\` paragraph. \\`check_proposals\\` counts the bold list items and skips ` +
      `the rest. Write no more than that. Do not rule on them, and do not edit \\`.claude/conventions.md\\` or ` +
      `\\`.claude/rejected.md\\`.\\n\\n` +
      condensed.map((one) => `- **${one.rule}**\\n  - why ${one.why}\\n  - seen ${one.seen}`).join('\\n'),
    { label: 'record-proposals', phase: 'Condense' },
  )
  if (!recorded) {
    throw new Error(`${condensed.length} proposal(s) came back and reached no place in ${RULED}.`)
  }
}
"""


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _ruled() -> str:
    """The text of the proposals awaiting a ruling. A condense reads that text and states no proposal already there."""
    path = os.path.join(gate.TREE, _RULED)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _given(fragment: collect_fragments.Fragment) -> dict[str, Any]:
    """
    A fragment as the workflow reads it. This gives the key, its place and the prose. A coin per round goes with them.
    """
    return {
        "key": fragment.key,
        "where": f"{fragment.path}:{fragment.first} ({fragment.kind})",
        "sites": [{"path": one.path, "lines": list(one.lines)} for one in fragment.sites],
        "parts": [one.prose for one in fragment.sites],
        "was": [one.prose for one in fragment.sites],
        "rounds": 0,
        "coin": [_COINS.random() < 0.5 for _ in range(_MOST_ROUNDS)],
    }


def _written(into: str, given: list[dict[str, Any]]) -> str:
    """Write the workflow that settles `given` and answer with its path."""
    held = batch_pending_fragments.held_to()
    body = (
        "export const meta = {\n"
        "  name: 'critic-converge',\n"
        "  description: 'Settle a batch of prose fragments between the critic and the comparator',\n"
        "  phases: [{ title: 'Converge' }, { title: 'Condense' }],\n"
        "}\n\n"
        f"const ASK = {json.dumps(batch_pending_fragments.ASK + chr(10) * 2 + held)}\n\n"
        f"const JUDGE = {json.dumps(_JUDGE + chr(10) * 2 + held)}\n\n"
        f"const CONDENSE = {json.dumps(_CONDENSE + chr(10) * 2 + held + chr(10) * 2 + _ruled())}\n\n"
        f"const CONDENSED = {json.dumps(_CONDENSED, indent=2)}\n\n"
        f"const RULED = {json.dumps(_RULED)}\n\n"
        f"const EXAMINED = {json.dumps(batch_pending_fragments.EXAMINED, indent=2)}\n\n"
        f"const JUDGED = {json.dumps(_JUDGED, indent=2)}\n\n"
        f"const MOST_ROUNDS = {_MOST_ROUNDS}\n\n"
        f"const A_CRITIC_BATCH = {_A_CRITIC_BATCH}\n\n"
        f"const A_COMPARE_BATCH = {_A_COMPARE_BATCH}\n\n"
        f"const FRAGMENTS = {json.dumps(given, indent=2)}\n\n" + _BODY + "\n" + _CONDENSING + _SETTLED
    )
    path = os.path.join(into, _CONVERGE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def _raised(paths: list[str]) -> list[dict[str, Any]]:
    """
    The proposals the runs at `paths` raised.

    A run file holds the result the workflow answered with, or a bare list of proposals.
    """
    held = []
    for path in paths:
        said = _loaded(path)
        if isinstance(said, dict):
            said = (said.get("result") or said).get("proposals") or []
        held += [one for one in said if isinstance(one, dict)]
    return held


def _condensing(into: str, proposals: list[dict[str, Any]]) -> str:
    """Write the workflow that condenses `proposals` and answer with its path."""
    ask = _CONDENSE + chr(10) * 2 + batch_pending_fragments.held_to() + chr(10) * 2 + _ruled()
    body = (
        "export const meta = {\n"
        "  name: 'critic-condense',\n"
        "  description: 'Condense the proposals a run raised into the list the author rules on',\n"
        "  phases: [{ title: 'Condense' }],\n"
        "}\n\n"
        f"const CONDENSE = {json.dumps(ask)}\n\n"
        f"const CONDENSED = {json.dumps(_CONDENSED, indent=2)}\n\n"
        f"const RULED = {json.dumps(_RULED)}\n\n"
        f"const proposals = {json.dumps(proposals, indent=2)}\n\n" + _CONDENSING + _ALONE_TAIL
    )
    path = os.path.join(into, _CONVERGE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def main() -> None:
    """Write a workflow and print its path. The command line picks the settling loop or the condense."""
    if len(sys.argv) > 3 and sys.argv[1] == _ALONE:
        into = sys.argv[2]
        os.makedirs(into, exist_ok=True)
        proposals = _raised(sys.argv[3:])
        path = os.path.abspath(_condensing(into, proposals))
        print(json.dumps({"converge": path, "proposals": len(proposals)}, indent=2))
        return
    if len(sys.argv) not in (3, 4):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    queue_path, into = sys.argv[1:3]
    most = int(sys.argv[3]) if len(sys.argv) == 4 else _A_POOL
    held = {one.key: one for one in collect_fragments.fragments()}
    taking = [key for key in _loaded(queue_path)["unexamined"] if key in held][:most]
    if not taking:
        print("the queue holds no fragment this tree still bears out", file=sys.stderr)
        sys.exit(1)
    os.makedirs(into, exist_ok=True)
    path = os.path.abspath(_written(into, [_given(held[key]) for key in taking]))
    print(json.dumps({"converge": path, "keys": taking}, indent=2))


if __name__ == "__main__":
    main()
