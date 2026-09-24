# SPDX-License-Identifier: MIT
"""
Write down what a critic run or a review run answered.

A workflow opens no file, and an agent inside a workflow holds no tool. The answer comes back as JSON, and this module
writes it. The proposals go under a heading naming the run. The rule counts go into `.claude/rule-use.json`.

`apply_prose` writes the rewrites of a critic run into the tree. The next `update_ledger_and_queue` queues the new
prose. The prose digest of a passed fragment goes into the ledger, and leaves the prose queue. `apply_prose` and
`update_ledger_and_queue` hold the run lock.

**Usage:** `python3 generator/record_run.py <run.json> [directory]`. The run file holds a workflow's answer. A critic
run that rewrote prose or passed a key wants the directory of the settling queues named.
"""

import json
import os
import subprocess
import sys

from typing import Any

import apply_prose
import collect_fragments
import gate
import settling_state

_USAGE = "usage: record_run.py <run.json> [directory]"  # a bad call gets it.

# The name a run goes under, keyed by what only that run's answer holds. `critic.js` examines fragments.
# `pre-commit-review.js` reports findings.
_RUNS = (("examined", "critic"), ("findings", "review"))

# The file `apply_prose` reads the rewrites out of. It sits in the directory of the settling queues.
_REWRITTEN = "rewritten.json"

# The file holding how a rule fares. `gate.rule_use` reads it back.
_RULE_USE = os.path.join(gate.TREE, ".claude", "rule-use.json")

# The note that file opens with. `collect_fragments` reads the `_` key of a JSON file as its prose.
_RULE_USE_SAYS = (
    "A settling run cites a rule of `.claude/conventions.md` per change. `cited` counts the changes citing a rule. "
    "`accepted` counts the changes the comparator let through. `overlaps` pairs a rule with a second rule. A later "
    "change applied that second rule to the same run of text. `record_run` writes this file."
)


def _loaded(path: str) -> Any:
    """The run `path` names. A workflow answer nests under `result` where the task file wraps it."""
    with open(path, encoding="utf-8") as handle:
        held = json.load(handle)
    return held["result"] if isinstance(held, dict) and "result" in held else held


def _who(held: dict[str, Any]) -> str:
    """The run that raised the proposals. `_RUNS` says which key a run's answer holds."""
    for key, who in _RUNS:
        if key in held:
            return who
    raise ValueError(f"the answer names no run this writes down. it holds {sorted(held)}")


def _changed(held: dict[str, Any]) -> list[dict[str, Any]]:
    """The changes a critic run's answer holds. The comparator does not see those changes."""
    said = []
    for one in held.get("rewritten") or []:
        for change in one.get("changes") or []:
            said.append({**change, "key": one.get("key", ""), "round": 1, "verdict": "unjudged"})
    return said


def _counted(changes: list[dict[str, Any]], was: dict[str, Any]) -> dict[str, Any]:
    """`was` with `changes` counted into it. A rule holds the changes citing it and the changes the comparator took."""
    held = {name: dict(one) for name, one in was.items()}
    for one in changes:
        rule = str(one.get("rule", ""))
        if not rule:
            continue
        entry = held.setdefault(rule, {"cited": 0, "accepted": 0})
        entry["cited"] += 1
        if one.get("verdict") == "accept":
            entry["accepted"] += 1
    return held


def _overlapped(changes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    The rule pairs the accepted changes contend over.

    A change rewrites a run of text. A later change of the same fragment quotes text inside that run. The pair of rules
    names a place where applying a rule opened work for another.
    """
    taken = [one for one in changes if one.get("verdict") == "accept"]
    found = []
    for early in taken:
        for late in taken:
            if late.get("key") != early.get("key") or late.get("round", 0) <= early.get("round", 0):
                continue
            new, old = str(early.get("new", "")), str(late.get("old", ""))
            if new and old and (old in new or new in old):
                found.append((str(early.get("rule", "")), str(late.get("rule", ""))))
    return found


def rule_use_counted(changes: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Count `changes` into `.claude/rule-use.json`. This answers with the rules counted and the pairs found.

    Public. `converge_prose` counts the changes of a comparator's answer here.
    """
    was: dict[str, Any] = {"rules": {}, "overlaps": []}
    if os.path.exists(_RULE_USE):
        with open(_RULE_USE, encoding="utf-8") as handle:
            was = {**was, **json.load(handle)}
    rules = _counted(changes, was["rules"])
    overlaps = {(one["rules"][0], one["rules"][1]): one["count"] for one in was["overlaps"]}
    for pair in _overlapped(changes):
        overlaps[pair] = overlaps.get(pair, 0) + 1
    held = {
        "_": _RULE_USE_SAYS,
        "rules": {name: rules[name] for name in sorted(rules)},
        "overlaps": [{"rules": list(pair), "count": overlaps[pair]} for pair in sorted(overlaps)],
    }
    with open(_RULE_USE, "w", encoding="utf-8") as handle:
        json.dump(held, handle, indent=2)
        handle.write("\n")
    return len(rules), len(overlaps)


def _said(one: dict[str, Any]) -> str:
    """
    A proposal as the pending file writes it. The rule opens the item in bold, and the reasons indent under it.

    `Before` quotes prose the proposed checker refuses. `After` says that prose again in the form it passes. The pair is
    a fixture. Whoever implements the rule holds the checker to refusing `Before` and passing `After`.

    A label goes in bold. `no-em-dash-and-no-colon` spares the colon of a definition term written that way, and refuses
    the colon of a term written in italics.
    """
    return (
        f"- **{one.get('rule', '')}**\n"
        f"  - {settling_state.WHY} {one.get('why', '')}\n"
        f"  - **Seen:** {collect_fragments.key_named(str(one.get('seen', '')))}\n"
        f"  - **Before:** `{one.get('before', '')}`\n"
        f"  - **After:** `{one.get('after', '')}`"
    )


def proposal_refusals(one: Any, who: str = "converge") -> list[str]:
    """
    The refusals the checkers give for the pending proposals file with `one` written in under `who`. The checkers read
    the item as `proposals_appended` writes it.

    Public. `prose_answer` asks this of an agent's proposal. `converge_prose` asks this of a critic's proposal and of a
    condensed one. `update_ledger_and_queue` asks this of a suggested proposal.
    """
    if not isinstance(one, dict):
        return ["a proposal holds no object"]
    path = os.path.relpath(settling_state.PENDING, gate.TREE)
    return apply_prose.text_refusals(path, _pending_with([one], who))


def _pending_with(held: list[dict[str, Any]], who: str) -> str:
    """
    The text of the pending file with the proposals `held` written in. A heading names the run that raised them. The
    proposals go at the end of that heading.

    A second heading of the same text would key a pair of markdown paragraphs alike. `collect_fragments` calls that a
    collision.
    """
    with open(settling_state.PENDING, encoding="utf-8") as handle:
        was = handle.read().rstrip("\n")
    body = "\n\n".join(_said(one) for one in held)
    heading = f"## From `{who}`"
    lines = was.split("\n")
    at = lines.index(heading) if heading in lines else -1
    if at < 0:
        said = f"{was}\n\n{heading}\n\n{body}"
    else:
        ends = next((under for under in range(at + 1, len(lines)) if lines[under].startswith("## ")), len(lines))
        head, tail = "\n".join(lines[:ends]).rstrip("\n"), "\n".join(lines[ends:]).strip("\n")
        said = f"{head}\n\n{body}" + (f"\n\n{tail}" if tail else "")
    return f"{said}\n"


def proposals_appended(held: list[dict[str, Any]], who: str) -> int:
    """
    Write the proposals `held` into the pending file under `who`, and answer with the count written.

    Public. `converge_prose` writes the proposals a condense gave back here.
    """
    if not held:
        return 0
    said = _pending_with(held, who)
    with open(settling_state.PENDING, "w", encoding="utf-8") as handle:
        handle.write(said)
    return len(held)


def _rewrites(held: dict[str, Any]) -> list[dict[str, Any]]:
    """The rewrites a critic run's answer holds, in the shape `apply_prose` takes. `apply_prose` applies the changes."""
    return [
        {"key": one["key"], "changes": one["changes"]}
        for one in held.get("rewritten") or []
        if isinstance(one, dict) and one.get("changes")
    ]


def _written(held: list[dict[str, Any]], into: str) -> set[str]:
    """
    Write the rewrites `held` names into the tree, and answer with the keys that landed.

    `apply_prose` holds back a rewrite it cannot shape. A key held back keeps the prose the tree had. An `apply_prose`
    that answers nothing crashed, and the run ends there.
    """
    path = os.path.join(into, _REWRITTEN)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(held, handle, indent=2)
    run = subprocess.run(
        ["python3", os.path.join(gate.TREE, "generator", "apply_prose.py"), path],
        cwd=gate.TREE,
        capture_output=True,
        text=True,
        check=False,
    )
    print(run.stderr, end="", file=sys.stderr)
    if not run.stdout.strip().startswith("{"):
        sys.exit(run.returncode or 1)
    said = json.loads(run.stdout)
    for one in said.get("held_back") or []:
        print(f"  held back: {one}", file=sys.stderr)
    return set(said.get("landed") or [])


def _approved(keys: list[str], into: str) -> None:
    """
    Put the prose digests of `keys` into the ledger, and take them out of the prose queue.

    A run whose passed keys the prose queue lacks reached this module once already. `_approved` stops on such a run. A
    second call would count the proposals and the rules twice.
    """
    by_key = {one.key: one.prose.sha for one in collect_fragments.fragments()}
    digests = [by_key[key] for key in keys if key in by_key]
    queue = settling_state.prose_queue(into)
    if not [digest for digest in digests if digest in queue]:
        print(f"the prose queue lacks the {len(keys)} passed key(s). this module wrote the run down before.")
        sys.exit(1)
    settling_state.appended(settling_state.LEDGER, digests)
    for digest in digests:
        settling_state.prose_state_written(into, digest, None)
    print(f"{len(digests)} passed digest(s) reached the ledger")


def main() -> None:
    """
    Bank the passed keys, write the proposals and the rule counts, then write the rewrites.

    A proposal the checkers refuse stops the run before anything lands. `apply_prose` holds back a rewrite the checkers
    refuse.
    """
    if len(sys.argv) not in (2, 3):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    held = _loaded(sys.argv[1])
    who = _who(held)
    refused = [found for one in held.get("proposals") or [] for found in proposal_refusals(one, who)]
    if refused:
        print("the checkers refused a proposal of this run. nothing was written.\n\n" + "\n\n".join(refused))
        sys.exit(1)
    rewrites, keys = _rewrites(held), list(held.get("passed") or [])
    if (rewrites or keys) and len(sys.argv) != 3:
        print(f"{len(rewrites)} rewrite(s) and {len(keys)} passed key(s) want the directory of the settling queues.")
        sys.exit(1)
    if keys:
        with settling_state.run_locked(sys.argv[2]):
            _approved(keys, sys.argv[2])
    count = proposals_appended(held.get("proposals") or [], who)
    print(f"{count} proposal(s) appended to `.claude/proposals-pending.md` under `From {who}`")
    changes = _changed(held)
    if changes:
        rules, overlaps = rule_use_counted(changes)
        print(f"{len(changes)} change(s) counted into `.claude/rule-use.json`")
        print(f"that file holds {rules} rule(s) and {overlaps} pair(s)")
    if rewrites:
        landed = _written(rewrites, sys.argv[2])
        stale = {one["key"] for one in rewrites} - landed
        print(f"{len(landed)} rewrite(s) reached a file, and {len(stale)} stayed out")
        if stale:
            sys.exit(1)


if __name__ == "__main__":
    main()
