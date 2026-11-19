# SPDX-License-Identifier: MIT
"""
Recover the official grammar from libyeast's grammar.

libyeast's grammar adds token annotations and rules of its own making. The official grammar lacks these. Both grammars
hold a production per indicator character, but libyeast's grammar refers to that production where the official grammar
writes the character.

This undoes those additions. An annotation goes, and its child stays. A zero-width marker goes outright. A reference to
an indicator production becomes the character it names. The rules libyeast added stay out.

The result must match the vendored grammar, except at a production `check_vendor_spec.py` declares a deviation.
`check_vendor_spec.py` checks that.

Usage: `python3 generator/ir2spec.py > recovered.yaml`.
"""

import sys

import annotated2ir
import ir
import ir2annotated

import yaml

# A rule listed here is a rule libyeast adds that matches nothing and may emit a marker. `end-block-scalar` emits
# `end-scalar` unless its chomping is `keep`. The official grammar can leave such a rule out and not notice. `x /
# end-block-scalar` is `x / <empty>`. The official grammar writes that as `x?`.
MARKER_ONLY = frozenset({"end-block-scalar"})

# The official grammar has no question for a scope listed here. That grammar writes the wrapped item in the scope's
# place. A `(recover)` says where a failed cut stops unwinding, and a `(commit)` is a scoped cut. An annotation names
# the characters, and a `(wrap)` puts markers around them.
#
# A `(max)` is a scope too, and it falls outside this list. The official grammar writes the bound of a `(max)` as a bare
# `(max)` in front of the region the bound covers.
_WRAPS_WHAT_IT_WRITES = (ir.CommitWrapper, ir.RecoverWrapper, ir.TokenWrapper, ir.Wrapper)

# libyeast adds these rules around the official grammar. The root `l-yeast-stream` is the rule the parser runs. The
# unparsed recovery `l-recover` is the rule that the root and a failed cut hand off to. The official grammar has no
# counterpart to the root or to the unparsed recovery. The recovery of the official grammar leaves out the rules listed
# here.
OWN = frozenset(  # The rules sit in alphabetical order.
    {
        "l-block-map-entries",
        "l-block-seq-entries",
        "l-leading-empties",
        "l-nb-literal-first",
        "l-nb-same-first",
        "l-recover",
        "l-recover-entry",
        "l-unparsed",
        "l-yeast-stream",
        "nb-unparsed",
        "s-indent-floor",
        "s-indent-le-line",
    }
)

# The character an indicator production names. The official grammar writes the character. libyeast writes the
# production.
INDICATORS = {
    "c-sequence-entry": ord("-"),
    "c-mapping-key": ord("?"),
    "c-mapping-value": ord(":"),
    "c-collect-entry": ord(","),
    "c-sequence-start": ord("["),
    "c-sequence-end": ord("]"),
    "c-mapping-start": ord("{"),
    "c-mapping-end": ord("}"),
    "c-comment": ord("#"),
    "c-anchor": ord("&"),
    "c-alias": ord("*"),
    "c-tag": ord("!"),
    "c-literal": ord("|"),
    "c-folded": ord(">"),
    "c-single-quote": ord("'"),
    "c-double-quote": ord('"'),
    "c-directive": ord("%"),
    "c-escape": ord("\\"),
}


def _flatten(items: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
    """
    The items of a sequence, with nested sequences spliced in.

    Wrapping part of a sequence nests a sequence inside a sequence. The markers around `l-directive` wrap such a part,
    and the comments that follow the directive stay outside it.

    Sequencing is associative. Splicing the nested sequences back is a change of form rather than of grammar.
    `normalized` applies it to the official grammar too. The splice favours neither grammar.
    """
    return tuple(spliced for item in items for spliced in ir.ITEMS_IN_A_SEQUENCE(item))


def _normalized_sequence(node: ir.SeqTree) -> ir.Node:
    """`node` with nested sequences spliced in. A sequence of a single item collapses into that item."""
    items = _flatten(node.items)
    return items[0] if len(items) == 1 else ir.SeqTree(items)


def _normalized_alternation(node: ir.AltTree) -> ir.Node:
    """`node` as an optional, where its last branch is `<empty>`."""
    if not (node.items and ir.AS_AN_EMPTY(node.items[-1]) is not None):
        return node
    rest = node.items[:-1]
    return ir.OptTree(rest[0] if len(rest) == 1 else ir.AltTree(rest))


def _normalized_case(node: ir.CaseTree) -> ir.Node:
    """
    `_normalized_case` returns `node` unchanged where the branches and the default of `node` differ. Otherwise
    `_normalized_case` returns the shared item. The soft-commit case of `c-double-quoted` has such branches once
    `_erase` strips the `CommitWrapper`. `generator/check_vendor_spec.py` compares that rule with the output of
    `official`.
    """
    held = [branch.item for branch in node.branches] + ([node.default] if node.default is not None else [])
    return held[0] if held and all(item == held[0] for item in held) else node


# A node in the form the official grammar uses. A sequence, an alternation and a case can change form. A node of another
# kind keeps its form.
_NORMALIZED: ir.Question[ir.Node] = ir.Question(
    "a node in the form the official grammar uses",
    {
        ir.SeqTree: _normalized_sequence,
        ir.AltTree: _normalized_alternation,
        ir.CaseTree: _normalized_case,
        (
            ir.BindTree,
            ir.EmptyTree,
            ir.FailTree,
            ir.OptTree,
            ir.PlusTree,
            ir.RepTree,
            ir.StarTree,
            ir.TrimStarTree,
        ): lambda node: node,
        (*ir.ACTIONS, *ir.CONSUMING, *ir.GUARDS, *ir.WRAPPERS, *ir.STATES, *ir.PARTS, *ir.CALLS, *ir.VALUE_KINDS): (
            lambda node: node
        ),
    },
)


def _normalize(node: ir.Node) -> ir.Node:
    """
    `node` in the form the official grammar uses. The function normalizes the children of `node` first. `_NORMALIZED`
    then reshapes the node rebuilt from them.
    """
    return _NORMALIZED(ir.rebuilt(node, _normalize))


# Whether the official grammar drops an action libyeast writes. The official grammar emits no token and names no error.
# It writes no cut either.
_IS_DROPPED: ir.Question[bool] = ir.Question(
    "whether the official grammar drops an action libyeast writes",
    {
        (ir.CutAction, ir.EmitAction, ir.ErrorAction): True,
        (
            ir.ClearVarAction,
            ir.CloseWindowAction,
            ir.CommitProvisionalAction,
            ir.ConsumeNoCharAction,
            ir.EndMustConsumeAction,
            ir.ExcludeAtAction,
            ir.IncreaseAction,
            ir.InjectBeforeAction,
            ir.MarkProvisionalAction,
            ir.OpenProvisionalAction,
            ir.OpenWindowAction,
            ir.PopBackTrackAction,
            ir.PopCodeAction,
            ir.PopIndentAction,
            ir.PopMessageAction,
            ir.PopRecoveryAction,
            ir.PushBackTrackAction,
            ir.PushCodeAction,
            ir.PushIndentAction,
            ir.PushMessageAction,
            ir.PushRecoveryAction,
            ir.RetypeProvisionalAction,
            ir.SetForbiddenAction,
            ir.SetVarAction,
            ir.StartMustConsumeAction,
        ): False,
        (*ir.CONSUMING, *ir.GUARDS, *ir.TREES, *ir.WRAPPERS, *ir.STATES, *ir.PARTS, *ir.CALLS, *ir.VALUE_KINDS): False,
    },
)


def _erased_children(node: ir.Node, owner: str) -> ir.Node:
    """`node` with the nodes it holds erased."""
    return ir.rebuilt(node, lambda item: _erase(item, owner))


def _erased_bound(node: ir.MaxWrapper, owner: str) -> ir.Node:
    """
    `node` as the official grammar writes it. libyeast wraps a production in `(max)`. The official grammar writes the
    character bound as a bare `(max)` in front of that production.
    """
    if node.item is None:
        return _erased_children(node, owner)
    return ir.SeqTree((ir.MaxWrapper(node.limit),) + ir.ITEMS_IN_A_SEQUENCE(_erase(node.item, owner)))


def _erased_call(node: ir.RefCall, owner: str) -> ir.Node:
    """
    `node` as the official grammar writes it. A marker becomes the empty match. An indicator with no arguments becomes
    its character, unless `owner` is that indicator.
    """
    if node.name in MARKER_ONLY:
        return ir.EmptyTree()
    if not node.args and node.name in INDICATORS and node.name != owner:
        return ir.OneCharSet(INDICATORS[node.name])
    return _erased_children(node, owner)


def _erased_sequence(node: ir.SeqTree, owner: str) -> ir.Node:
    """`node` with its items erased. An action `_IS_DROPPED` names leaves the sequence."""
    return ir.SeqTree(tuple(_erase(item, owner) for item in node.items if not _IS_DROPPED(item)))


# The node the official grammar writes where libyeast writes a node. A wrapper `_WRAPS_WHAT_IT_WRITES` names gives way
# to its item.
_ERASED: ir.Question[ir.Node] = ir.Question(
    "the node the official grammar writes where libyeast writes a node",
    {
        _WRAPS_WHAT_IT_WRITES: lambda node, owner: _erase(node.item, owner),
        ir.MaxWrapper: _erased_bound,
        ir.RefCall: _erased_call,
        ir.SeqTree: _erased_sequence,
        (
            ir.AltTree,
            ir.BindTree,
            ir.CaseTree,
            ir.EmptyTree,
            ir.FailTree,
            ir.OptTree,
            ir.PlusTree,
            ir.RepTree,
            ir.StarTree,
            ir.TrimStarTree,
        ): _erased_children,
        (*ir.ACTIONS, *ir.CONSUMING, *ir.GUARDS, *ir.STATES, *ir.PARTS, *ir.VALUE_KINDS): _erased_children,
    },
)


def _erase(node: ir.Node, owner: str) -> ir.Node:
    """The node the official grammar writes where libyeast writes `node`."""
    return _ERASED(node, owner)


def official(grammar: dict[str, ir.Prod]) -> dict[str, object]:
    """The official grammar's mapping, recovered from the libyeast grammar."""
    recovered = {}
    for name, production in grammar.items():
        if name in MARKER_ONLY or name in OWN:
            continue  # the official grammar has no such rule, marker-only or one of libyeast's own
        body = _normalize(_erase(production.body, name))
        recovered[name] = ir.Prod(production.number, name, production.params, body)
    return ir2annotated.regenerate(recovered)


def normalized(grammar: dict[str, ir.Prod]) -> dict[str, object]:
    """
    A grammar's mapping with its bodies in the form the official grammar uses. `check_vendor_spec` holds `official` to
    this form.
    """
    flattened = {}
    for name, production in grammar.items():
        flattened[name] = ir.Prod(production.number, name, production.params, _normalize(production.body))
    return ir2annotated.regenerate(flattened)


def main() -> None:
    yaml.safe_dump(official(annotated2ir.load()), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
