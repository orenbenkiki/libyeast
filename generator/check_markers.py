# SPDX-License-Identifier: MIT
"""
Check that the grammar's zero-width markers balance.

Every `begin-` marker must be closed by its own `end-`, on every path, and a rule must balance them the same way
whichever path is taken through it — otherwise the token stream is a tree only sometimes, and the fold that rebuilds the
production tree from it has nothing to stand on.

Nothing else catches this. The rule that every consumed character lies within a token action says nothing about markers,
which consume nothing at all; and a marker that is never emitted looks exactly like a marker that is not needed.

The chomping decides where a block scalar ends — `b-chomped-last` closes it when there is content to close, and
`l-keep-empty` when the content was empty and kept — so the markers balance per value of `t` rather than per branch. The
check therefore specializes: it fixes each finite parameter — `c`, `t` and the resume policy `r` — to each of its values
in turn, and requires balance for each.
"""

import annotated2ir
import gate
import ir

CONTEXTS, CHOMPINGS, RESUMES = annotated2ir.CONTEXTS, annotated2ir.CHOMPINGS, annotated2ir.RESUMES

# The nodes that emit no marker: a character, a guard, a commit point, an error token, and the `(flip)` a value
# production is made of. Named rather than assumed, because assuming it is how a `(recover)` once hid every marker
# inside it — a node this does not know is a node whose markers nothing has looked at, and the gate says so rather than
# passing it. In alphabetical order.
SILENT = (
    ir.Char,
    ir.Cut,
    ir.Diff,
    ir.Empty,
    ir.EndOfStream,
    ir.Error,
    ir.Flip,
    ir.Increase,
    ir.Invalid,
    ir.Le,
    ir.Lt,
    ir.Range,
    ir.SetVar,
    ir.StartOfLine,
)
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


def effect(node, values, known):
    """The markers `node` leaves open or closes, with `c` and `t` fixed to `values`."""
    if isinstance(node, ir.Emit):
        return marker(node.code)
    if isinstance(node, ir.Wrap):
        return compose(compose(marker(node.begin), effect(node.item, values, known)), marker(node.end))
    if isinstance(node, (ir.Token, ir.Bound, ir.Commit)) or (isinstance(node, ir.Max) and node.item is not None):
        # A `(<<<)`, a `(commit)` or a wrapping `(max)` matches what is inside it, so what is inside it emits. Passing
        # over it would let a marker opened there go unclosed, and no other gate looks.
        return effect(node.item, values, known)
    if isinstance(node, ir.ZERO_WIDTH):
        return BALANCED  # a lookahead emits nothing, whatever it matches
    if isinstance(node, ir.Seq):
        settled = BALANCED
        for item in node.items:
            settled = compose(settled, effect(item, values, known))
        return settled
    if isinstance(node, ir.Alt):
        return agreed([effect(item, values, known) for item in node.items], "an alternation")
    if isinstance(node, ir.Case):
        # A rule reached only in some contexts lists only those: `ns-plain` has no block-in branch, because nothing
        # reaches it with block-in. A branch that is not there is a path that cannot be taken, and emits nothing.
        taken = {branch.value: branch.item for branch in node.branches}.get(values[node.var])
        return BALANCED if taken is None else effect(taken, values, known)
    if isinstance(node, ir.Opt):
        return agreed([effect(node.item, values, known), BALANCED], "an optional rule")
    if isinstance(node, (ir.Star, ir.Plus, ir.Rep)):
        # A rule that opens or closes a marker cannot be repeated: twice around leaves twice as many open.
        return agreed([effect(node.item, values, known), BALANCED], "a repeated rule")
    if isinstance(node, ir.Bind):
        return effect(node.cond, values, known)
    if isinstance(node, ir.Recover):
        # Both ways through must balance the same way. Recovering closes what the item left open, down to here, so that
        # path leaves only what the recovery itself emits — and the item's own way has to agree with it.
        return agreed([effect(node.item, values, known), effect(node.recovery, values, known)], "a recovery")
    if isinstance(node, ir.Ref):
        return known.get(node.name, BALANCED)
    if isinstance(node, SILENT):
        return BALANCED
    raise TypeError(f"cannot tell what markers {type(node).__name__} leaves: it is not known to emit, or not to")


def settle(grammar, values):
    """How each rule balances its markers, with `c` and `t` fixed — reached by assuming balance and iterating."""
    known = {name: BALANCED for name in grammar}
    errors = {}
    for _pass in range(len(grammar)):
        changed = False
        for name, production in grammar.items():
            try:
                settled = effect(production.body, values, known)
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
            for resume in RESUMES:
                values = {"c": context, "t": chomping, "r": resume}
                where = f"c={context}, t={chomping}, r={resume}"
                known, errors = settle(grammar, values)
                for name, reason in errors.items():
                    complaints.setdefault((name, reason), []).append(where)
                if known[ir.ROOT] != BALANCED:
                    left = ", ".join(known[ir.ROOT][1]) or "none"
                    closed = ", ".join(known[ir.ROOT][0]) or "none"
                    reason = f"the stream leaves open: {left}; and closes what it never opened: {closed}"
                    complaints.setdefault((ir.ROOT, reason), []).append(where)

    errors = []
    for (name, reason), wheres in sorted(complaints.items()):
        more = f", and {len(wheres) - 1} more" if len(wheres) > 1 else ""
        errors.append(f"{name}: {reason}\n    with {wheres[0]}{more}")
    gate.report(
        errors,
        "rule(s) whose markers do not balance",
        f"markers balance: {len(grammar)} rules, for every context, chomping and resume policy",
    )


if __name__ == "__main__":
    main()
