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
INDENT_MODES = annotated2ir.INDENT_MODES

# The nodes that emit no marker: a character, a guard, a commit point, an error token, and the `(flip)` a value
# production is made of. Named rather than assumed, because assuming it is how a `(recover)` once hid every marker
# inside it — a node this does not know is a node whose markers nothing has looked at, and the gate says so rather than
# passing it. In alphabetical order.
SILENT = (
    ir.OneCharSet,
    ir.CharSet,
    ir.ColumnLeGuard,
    ir.ColumnLtGuard,
    ir.CutAction,
    ir.DiffSet,
    ir.EmptyTree,
    ir.EndOfStreamGuard,
    ir.ErrorAction,
    ir.FlipValue,
    ir.IncreaseAction,
    ir.InvalidSet,
    ir.RangeSet,
    ir.SetVarAction,
    ir.StartOfLineGuard,
)
# A scope whose markers are the markers of what it holds: it matches what is inside it, so what is inside it emits.
# Passing over one would let a marker opened there go unclosed, and no other gate looks. A `(max)` is one of these where
# it wraps a match and is not one where it is the vendored grammar's bare length note, so it is answered for on its own;
# a `(recover)` is not one either, its two ways having to agree rather than one of them being the answer.
SCOPES = (ir.CommitWrapper, ir.TokenWrapper)
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
    return _EFFECT(node, values, known)


def _effect_of_run(node, values, known):
    """A sequence's: each item after the one before it, what one leaves open being what the next may close."""
    settled = BALANCED
    for item in node.items:
        settled = compose(settled, effect(item, values, known))
    return settled


def _effect_of_switch(node, values, known):
    """
    A `(case)`'s: the branch the values select, and nothing where it has none.

    A rule reached only in some contexts lists only those: `ns-plain` has no block-in branch, because nothing reaches it
    with block-in. A branch that is not there is a path that cannot be taken, and emits nothing.
    """
    taken = {branch.value: branch.item for branch in node.branches}.get(values[node.var])
    return BALANCED if taken is None else effect(taken, values, known)


def _effect_of_recovery(node, values, known):
    """
    A recovery's: the one way its two paths balance.

    Recovering closes what the item left open, down to here, so that path leaves only what the recovery itself emits —
    and the item's own way has to agree with it.
    """
    return agreed([effect(node.item, values, known), effect(node.recovery, values, known)], "a recovery")


# What each kind leaves open and closes. A kind named nowhere raises: what its markers do is then something nothing has
# looked at, which is how a `(recover)` once hid every marker inside it.
_EFFECT = ir.Reading(
    "the markers a match leaves open and the ones it closes",
    {
        ir.EmitAction: lambda node, values, known: marker(node.code),
        ir.Wrapper: lambda node, values, known: compose(
            compose(marker(node.begin), effect(node.item, values, known)), marker(node.end)
        ),
        SCOPES: lambda node, values, known: effect(node.item, values, known),
        # A wrapping `(max)` is one of those; the vendored grammar's bare `(max)` is a length note and matches nothing.
        ir.MaxWrapper: lambda node, values, known: BALANCED if node.item is None else effect(node.item, values, known),
        ir.ASKED_NOT_TAKEN_NODES: BALANCED,  # what is asked about emits nothing, whatever it matches
        SILENT: BALANCED,
        ir.SeqTree: _effect_of_run,
        ir.AltTree: lambda node, values, known: agreed(
            [effect(item, values, known) for item in node.items], "an alternation"
        ),
        ir.CaseTree: _effect_of_switch,
        ir.OptTree: lambda node, values, known: agreed(
            [effect(node.item, values, known), BALANCED], "an optional rule"
        ),
        # A rule that opens or closes a marker cannot be repeated: twice around leaves twice as many open.
        ir.REPETITIONS: lambda node, values, known: agreed(
            [effect(node.item, values, known), BALANCED], "a repeated rule"
        ),
        ir.BindTree: lambda node, values, known: effect(node.cond, values, known),
        ir.RecoverWrapper: _effect_of_recovery,
        ir.RefCall: lambda node, values, known: known.get(node.name, BALANCED),
    },
)


def settle(grammar, values):
    """How each rule balances its markers, with `c` and `t` fixed — reached by assuming balance and iterating."""
    known = {name: BALANCED for name in grammar}
    errors = {}
    for _pass in range(len(grammar)):
        did_change = False
        for name, production in grammar.items():
            try:
                settled = effect(production.body, values, known)
            except Unbalanced as complaint:
                errors[name] = complaint.reason
                continue
            errors.pop(name, None)
            if settled != known[name]:
                known[name] = settled
                did_change = True
        if not did_change:
            break
    return known, errors


def main():
    grammar = annotated2ir.load()
    complaints = {}
    for context in CONTEXTS:
        for chomping in CHOMPINGS:
            for resume in RESUMES:
                for mode in INDENT_MODES:
                    values = {"c": context, "t": chomping, "r": resume, "i": mode}
                    where = f"c={context}, t={chomping}, r={resume}, i={mode}"
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
        f"markers balance: {len(grammar)} rules, for every context, chomping, resume policy and indentation mode",
    )


if __name__ == "__main__":
    main()
