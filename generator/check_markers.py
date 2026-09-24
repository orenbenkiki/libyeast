# SPDX-License-Identifier: MIT
"""
Check that the grammar's zero-width markers balance.

A `begin-` marker gets its own `end-` on any path through the rule. Otherwise the token stream is a tree on a run here
and a tangle on a run there. The fold that rebuilds the production tree then has nothing to rest on.

The rule that a consumed character lies within a token action says nothing about markers. A marker consumes nothing at
all. A marker that goes unemitted looks exactly like a marker nobody needs.

The chomping decides where a block scalar ends. `b-chomped-last` closes it when there is content to close.
`l-keep-empty` closes it when the content was empty and kept. So the markers balance per value of `t` rather than per
branch.

The check therefore specializes. It fixes a finite parameter to a value in turn, and requires balance under the values
that parameter takes. The context `c` and the chomping `t` are finite parameters. So are the resume policy `r` and the
indentation mode `i`.
"""

from collections.abc import Iterable, Mapping

import annotated2ir
import gate
import ir

CONTEXTS, CHOMPINGS, RESUMES = annotated2ir.CONTEXTS, annotated2ir.CHOMPINGS, annotated2ir.RESUMES
_INDENT_MODES = (
    annotated2ir.INDENT_MODES
)  # The values of the indentation mode `i`, a finite parameter the marker walk enumerates.

# The nodes that emit no marker. A character or a guard. A commit point or an error token. The empty and failing
# matches, the writes, and the `(flip)` that makes up a value production.
#
# The gate names these nodes rather than assuming them. The gate reports a node missing from this list rather than
# passing that node. In alphabetical order.
_SILENT = (
    ir.CharSet,
    ir.CutAction,
    ir.DiffSet,
    ir.EmptyTree,
    ir.EndOfStreamGuard,
    ir.ErrorAction,
    ir.FailTree,
    ir.FlipValue,
    ir.IncreaseAction,
    ir.InvalidSet,
    ir.IsLessEqualGuard,
    ir.IsLessThanGuard,
    ir.OneCharSet,
    ir.RangeSet,
    ir.SetVarAction,
    ir.StartOfLineGuard,
)
# A scope whose markers are the markers of the item it holds. The scope matches that item, and the item emits. A walk
# passing over such a scope would miss a marker opened there and left unclosed.
#
# A `(max)` is such a scope where it wraps a match. A bare `(max)` in the vendored grammar is a length note rather than
# a scope, and the module checks a bare `(max)` separately. A `(recover)` is no such scope either. Its ways have to
# agree. The answer does not come from a single way.
_SCOPES = (ir.CommitWrapper, ir.TokenWrapper)

# The markers a node leaves behind. The markers the node closes without opening, and the markers it leaves open.
_Effect = tuple[tuple[str, ...], tuple[str, ...]]

_BALANCED: _Effect = ((), ())  # The rule leaves no marker open and closes no marker it did not open.


class _Unbalanced(Exception):
    """A rule whose markers do not balance. The walk adds the rule name once it reaches the rule."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _marker(code: str) -> _Effect:
    """
    The marker a code leaves behind. A marker the code opens, a marker it closes, or none.
    """
    if code.startswith("begin-"):
        return ((), (code[len("begin-") :],))
    if code.startswith("end-"):
        return ((code[len("end-") :],), ())
    return _BALANCED


def _compose(before: _Effect, after: _Effect) -> _Effect:
    """
    The markers a pair of nodes leave behind. The nodes run in order, and `after` may close a marker `before` opened.
    """
    opened, closing = list(before[1]), list(after[0])
    while opened and closing:
        if opened[-1] != closing[0]:
            raise _Unbalanced(f"`end-{closing[0]}` closes `begin-{opened[-1]}`")
        opened.pop()
        closing.pop(0)
    return (tuple(before[0]) + tuple(closing), tuple(opened) + tuple(after[1]))


def _agreed(effects: Iterable[_Effect], what: str) -> _Effect:
    """The way `what`'s branches balance their markers, or a complaint that the branches disagree."""
    distinct = set(effects)
    if len(distinct) > 1:
        answers = " and ".join(sorted(str(balance) for balance in distinct))
        raise _Unbalanced(f"the branches of {what} balance their markers differently: {answers}")
    return distinct.pop() if distinct else _BALANCED


def _effect(node: ir.Node, values: Mapping[str, str], known: Mapping[str, _Effect]) -> _Effect:
    """The markers `node` leaves open or closes, with the finite parameters fixed to the settings in `values`."""
    return _EFFECT(node, values, known)


def _effect_of_run(node: ir.SeqTree, values: Mapping[str, str], known: Mapping[str, _Effect]) -> _Effect:
    """A sequence's markers. An item follows the item before it, and may close what that item left open."""
    settled = _BALANCED
    for item in node.items:
        settled = _compose(settled, _effect(item, values, known))
    return settled


def _effect_of_switch(node: ir.CaseTree, values: Mapping[str, str], known: Mapping[str, _Effect]) -> _Effect:
    """
    A `(case)`'s markers. A case takes the markers of the branch the values select. A case with no such branch takes
    none.

    A rule reached in a context lists that context. `ns-plain` has no block-in branch. A parse reaches `ns-plain` under
    other contexts. A branch that is not there is a path nobody can take, and such a path emits no marker.
    """
    taken = {branch.value: branch.item for branch in node.branches}.get(values[node.var])
    return _BALANCED if taken is None else _effect(taken, values, known)


def _effect_of_recovery(node: ir.RecoverWrapper, values: Mapping[str, str], known: Mapping[str, _Effect]) -> _Effect:
    """
    A recovery's markers depend on how its paths balance.

    A recovery closes what its item left open. The recovery closes down to where it began. The recovering path leaves
    only what the recovery emits. The item's own way has to agree with that path.
    """
    return _agreed([_effect(node.item, values, known), _effect(node.recovery, values, known)], "a recovery")


# A lookup of a kind this question does not name raises. The markers of such a kind have gone unread.
_EFFECT: ir.Question[_Effect] = ir.Question(
    "a pair: the marker names a match closes without opening, and the names it leaves open",
    {
        ir.EmitAction: lambda node, values, known: _marker(node.code),
        ir.Wrapper: lambda node, values, known: _compose(
            _compose(_marker(node.begin), _effect(node.item, values, known)), _marker(node.end)
        ),
        _SCOPES: lambda node, values, known: _effect(node.item, values, known),
        # A wrapping `(max)` counts as such a scope. A bare `(max)` is a length note and matches nothing.
        ir.MaxWrapper: lambda node, values, known: (
            _BALANCED if node.item is None else _effect(node.item, values, known)
        ),
        # the item under this emits no marker, and the match it makes changes nothing.
        ir.ASKED_NOT_TAKEN_NODES: _BALANCED,
        _SILENT: _BALANCED,
        ir.SeqTree: _effect_of_run,
        ir.AltTree: lambda node, values, known: _agreed(
            [_effect(item, values, known) for item in node.items], "an alternation"
        ),
        ir.CaseTree: _effect_of_switch,
        ir.OptTree: lambda node, values, known: _agreed(
            [_effect(node.item, values, known), _BALANCED], "an optional rule"
        ),
        # A rule that opens or closes a marker takes no repetition. A second pass around the repetition leaves twice as
        # many markers open.
        ir.REPETITIONS: lambda node, values, known: _agreed(
            [_effect(node.item, values, known), _BALANCED], "a repeated rule"
        ),
        ir.BindTree: lambda node, values, known: _effect(node.cond, values, known),
        ir.RecoverWrapper: _effect_of_recovery,
        ir.RefCall: lambda node, values, known: known.get(node.name, _BALANCED),
    },
)


def _settle(grammar: Mapping[str, ir.Prod], values: Mapping[str, str]) -> tuple[dict[str, _Effect], dict[str, str]]:
    """
    The way a rule balances its markers. `values` fixes the finite parameters. The walk assumes balance and iterates.

    A pass can only take an answer a call further. A grammar of `n` productions settles in at most `n` passes, and a
    grammar that has not settled by then is not settling.

    The walk then raises rather than returning. The answers say what a rule does with its markers. A partial answer
    would report a rule as balanced where the walk stopped rather than where the rule settles.
    """
    known: dict[str, _Effect] = {name: _BALANCED for name in grammar}
    errors: dict[str, str] = {}
    for _pass in range(len(grammar)):
        did_change = False
        for name, production in grammar.items():
            try:
                settled = _effect(production.body, values, known)
            except _Unbalanced as complaint:  # failure-is-reported: as `errors[name]`, which this function returns
                errors[name] = complaint.reason
                continue
            errors.pop(name, None)
            if settled != known[name]:
                known[name] = settled
                did_change = True
        if not did_change:
            return known, errors
    raise AssertionError(f"the way a rule balances its markers did not settle in {len(grammar)} passes")


def main() -> None:
    grammar = annotated2ir.load()
    complaints: dict[tuple[str, str], list[str]] = {}
    for context in CONTEXTS:
        for chomping in CHOMPINGS:
            for resume in RESUMES:
                for mode in _INDENT_MODES:
                    values = {"c": context, "t": chomping, "r": resume, "i": mode}
                    where = f"c={context} t={chomping} r={resume} i={mode}"
                    known, errors = _settle(grammar, values)
                    for name, reason in errors.items():
                        complaints.setdefault((name, reason), []).append(where)
                    if known[ir.ROOT] != _BALANCED:
                        left = ", ".join(known[ir.ROOT][1]) or "none"
                        closed = ", ".join(known[ir.ROOT][0]) or "none"
                        reason = (
                            f"the stream leaves open: {left}. the stream closes what it did not open. that is {closed}"
                        )
                        complaints.setdefault((ir.ROOT, reason), []).append(where)

    faults = []
    for (name, reason), wheres in sorted(complaints.items()):
        more = f", and {len(wheres) - 1} more" if len(wheres) > 1 else ""
        faults.append(f"{name}: {reason}\n    with {wheres[0]}{more}")
    gate.report(
        faults,
        "rule(s) whose markers do not balance",
        f"markers balance: {len(grammar)} rules hold under any context and any chomping. they hold under any resume "
        f"policy and any indentation mode.",
    )


if __name__ == "__main__":
    main()
