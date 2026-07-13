# SPDX-License-Identifier: MIT
"""Check that the grammar's zero-width markers balance.

Every `begin-` marker must be closed by its own `end-`, on every path, and a rule must balance them the same way
whichever path is taken through it — otherwise the token stream is a tree only sometimes, and the fold that rebuilds the
production tree from it has nothing to stand on.

Nothing else catches this. The rule that every consumed character lies within a token action says nothing about markers,
which consume nothing at all; and a marker that is never emitted looks exactly like a marker that is not needed.

The chomping decides where a block scalar ends — `b-chomped-last` closes it when there is content to close, and
`l-keep-empty` when the content was empty and kept — so the markers balance per value of `t` rather than per branch.
The check therefore specializes: it fixes `c` and `t` to each of their values in turn, and requires balance for each.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import annotated2ir  # noqa: E402
import ir  # noqa: E402

ROOT = "l-yaml-stream"
ZERO_WIDTH = (ir.Look, ir.NegLook, ir.LookBehind, ir.ExcludeAt)
CONTEXTS = ("block-in", "block-out", "block-key", "flow-in", "flow-out", "flow-key")
CHOMPINGS = ("strip", "clip", "keep")
BALANCED = ((), ())  # no marker left open, and none closed that was not opened here


class Unbalanced(Exception):
    """A rule whose markers do not balance, with the rule named once it is known."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def marker(code):
    """What a code leaves behind: a marker it opens, a marker it closes, or nothing — most codes are not markers."""
    if code.startswith("begin-"):
        return ((), (code[len("begin-") :],))
    if code.startswith("end-"):
        return ((code[len("end-") :],), ())
    return BALANCED


def compose(before, after):
    """The markers two nodes leave behind, one after the other: what `before` opened, `after` may close."""
    opened, closing = list(before[1]), list(after[0])
    while opened and closing:
        if opened[-1] != closing[0]:
            raise Unbalanced(f"`end-{closing[0]}` closes `begin-{opened[-1]}`")
        opened.pop()
        closing.pop(0)
    return (tuple(before[0]) + tuple(closing), tuple(opened) + tuple(after[1]))


def agreed(effects, what):
    """The one way `what`'s branches balance their markers, or a complaint that they do not agree on one."""
    distinct = set(effects)
    if len(distinct) > 1:
        ways = " and ".join(sorted(str(effect) for effect in distinct))
        raise Unbalanced(f"the branches of {what} balance their markers differently: {ways}")
    return distinct.pop() if distinct else BALANCED


def effect(node, grammar, values, known):
    """The markers `node` leaves open or closes, with `c` and `t` fixed to `values`."""
    if isinstance(node, ir.Emit):
        return marker(node.code)
    if isinstance(node, ir.Wrap):
        return compose(compose(marker(node.begin), effect(node.item, grammar, values, known)), marker(node.end))
    if isinstance(node, ir.Token):
        return effect(node.item, grammar, values, known)
    if isinstance(node, ZERO_WIDTH):
        return BALANCED  # a lookahead emits nothing, whatever it matches
    if isinstance(node, ir.Seq):
        settled = BALANCED
        for item in node.items:
            settled = compose(settled, effect(item, grammar, values, known))
        return settled
    if isinstance(node, ir.Alt):
        return agreed([effect(item, grammar, values, known) for item in node.items], "an alternation")
    if isinstance(node, ir.Case):
        # A rule reached only in some contexts lists only those: `ns-plain` has no block-in branch, because nothing
        # reaches it with block-in. A branch that is not there is a path that cannot be taken, and emits nothing.
        taken = dict(node.branches).get(values[node.var])
        return BALANCED if taken is None else effect(taken, grammar, values, known)
    if isinstance(node, ir.Opt):
        return agreed([effect(node.item, grammar, values, known), BALANCED], "an optional rule")
    if isinstance(node, (ir.Star, ir.Plus, ir.Rep)):
        # A rule that opens or closes a marker cannot be repeated: twice around leaves twice as many open.
        return agreed([effect(node.item, grammar, values, known), BALANCED], "a repeated rule")
    if isinstance(node, ir.Bind):
        return effect(node.cond, grammar, values, known)
    if isinstance(node, ir.Ref):
        return known.get(node.name, BALANCED)
    return BALANCED  # a character, a range, a difference, a marker of position: none of them emit


def settle(grammar, values):
    """How each rule balances its markers, with `c` and `t` fixed — reached by assuming balance and iterating."""
    known = {name: BALANCED for name in grammar}
    errors = {}
    for _pass in range(len(grammar)):
        changed = False
        for name, production in grammar.items():
            try:
                settled = effect(production.body, grammar, values, known)
            except Unbalanced as complaint:
                errors[name] = complaint.reason
                continue
            errors.pop(name, None)
            if settled != known[name]:
                known[name] = settled
                changed = True
        if not changed:
            break
    return known, errors


def main():
    grammar = annotated2ir.load()
    complaints = {}
    for context in CONTEXTS:
        for chomping in CHOMPINGS:
            values = {"c": context, "t": chomping}
            known, errors = settle(grammar, values)
            for name, reason in errors.items():
                complaints.setdefault((name, reason), []).append(f"c={context}, t={chomping}")
            if known[ROOT] != BALANCED:
                left = ", ".join(known[ROOT][1]) or "none"
                closed = ", ".join(known[ROOT][0]) or "none"
                reason = f"the stream leaves open: {left}; and closes what it never opened: {closed}"
                complaints.setdefault((ROOT, reason), []).append(f"c={context}, t={chomping}")

    if complaints:
        for (name, reason), wheres in sorted(complaints.items()):
            print(f"{name}: {reason}", file=sys.stderr)
            print(
                f"    with {wheres[0]}" + (f", and {len(wheres) - 1} more" if len(wheres) > 1 else ""), file=sys.stderr
            )
        print(f"{len(complaints)} rule(s) whose markers do not balance", file=sys.stderr)
        sys.exit(1)
    print(f"markers balance: {len(grammar)} rules, for every context and chomping")


if __name__ == "__main__":
    main()
