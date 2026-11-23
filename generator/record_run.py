# SPDX-License-Identifier: MIT
"""
Write down what a review run answered.

The answer of a workflow comes back as JSON, and this module writes down the proposals the answer holds. The proposals
go under a heading naming the run. `rule_use_counted` writes the rule counts into `.claude/rule-use.json`.

**Usage:** `python3 generator/record_run.py <output>`. The task output file holds the answer of `pre-commit-review.js`.
"""

import json
import os
import sys

from typing import Any

import apply_prose
import collect_fragments
import gate
import review_input
import settling_state

_USAGE = "usage: record_run.py <output>"  # a bad call gets it.

# The name the proposals of a review go under.
_REVIEW = "review"

# The file holding how a rule fares. `gate.rule_use` reads it back.
_RULE_USE = os.path.join(gate.TREE, ".claude", "rule-use.json")

# The note that file opens with. `collect_fragments` reads the `_` key of a JSON file as its prose.
_RULE_USE_SAYS = (
    "A settling run cites a rule of `.claude/conventions.md` per change. `cited` counts the changes citing a rule. "
    "`accepted` counts the changes the comparator let through. `overlaps` pairs a rule with a second rule. A later "
    "change applied that second rule to the same run of text. `record_run` writes this file."
)


def _counted(changes: list[dict[str, Any]], was: dict[str, Any]) -> dict[str, Any]:
    """
    `was` with `changes` counted into it. A rule holds a count of the changes citing it and a count of the changes the
    comparator took.
    """
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
    _overlapped returns the rule pairs that accepted changes contend over.

    An early change writes new text into a fragment. A change in a later round quotes old text from that fragment.
    _overlapped pairs the early change with the later change where either text holds the other. The pair of rules names
    a place where applying a rule opened work for another.
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
    Count `changes` into `.claude/rule-use.json`. This answers with the number of rules and the number of rule pairs the
    file holds after the count.

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
    A proposal as the pending file writes it. The rule opens the item in bold, and the other fields indent under it.
    `agent_answers._PROPOSED` describes the `Before` and `After` fields.

    A label goes in bold. The checker in `generator/prose_rules.py` spares the colon of a definition term written that
    way, and refuses the colon of a term written in italics.
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
    The refusals the checkers give for the pending proposals file with `one` written in under `who`. A `one` that is not
    a dict gets the refusal "a proposal holds no object", and the checkers do not run. The checkers read the item as
    `proposals_appended` writes it.

    Public. `prose_answer` asks this of an agent's proposal. `converge_prose` asks this of a critic's proposal and of a
    condensed one. `update_ledger_and_queue` asks this of a suggested proposal. `record_run.main` asks this of a review
    run's proposal.
    """
    if not isinstance(one, dict):
        return ["a proposal holds no object"]
    path = os.path.relpath(settling_state.PENDING, gate.TREE)
    return apply_prose.text_refusals(path, _pending_with([one], who))


def _pending_with(held: list[dict[str, Any]], who: str) -> str:
    """
    The text of the pending file with the proposals `held` written in. A heading names the run that raised them. The
    proposals go at the end of that heading.

    A repeated heading over a repeated paragraph would repeat a markdown key. `collect_fragments` calls that a
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


def main() -> None:
    """
    Write the proposals of a review run. A proposal the checkers refuse stops the run before anything lands.
    """
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    held = review_input.answer_in(sys.argv[1])
    if held is None:
        raise ValueError(f"{sys.argv[1]} holds no review answer")
    refused = [found for one in held.get("proposals") or [] for found in proposal_refusals(one, _REVIEW)]
    if refused:
        print("the checkers refused a proposal of this run. nothing was written.\n\n" + "\n\n".join(refused))
        sys.exit(1)
    count = proposals_appended(held.get("proposals") or [], _REVIEW)
    print(f"{count} proposal(s) appended to `.claude/proposals-pending.md` under `From {_REVIEW}`")


if __name__ == "__main__":
    main()
