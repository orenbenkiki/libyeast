# SPDX-License-Identifier: MIT
"""
Recover the official grammar from libyeast's grammar, to prove we still speak its language.

libyeast's grammar adds what the official grammar lacks. The token annotations. A production per indicator character.
That production gives an annotation somewhere to attach. Rules of libyeast's own making.

This undoes those additions. An annotation goes, and its child stays. A zero-width marker goes outright. A reference to
an indicator production becomes the character it names. The rules libyeast added stay out.

The result must be the vendored grammar. That is what `check_vendor_spec.py` checks.

Usage: `python3 generator/ir2spec.py > recovered.yaml`.
"""

import sys

import annotated2ir
import ir
import ir2annotated

import yaml

# The rules libyeast adds that match nothing at all. They exist only to emit a marker. The official grammar can leave
# them out and not notice. `x / end-block-scalar` is `x / <empty>`. That is `x?`, and `x?` is what it writes.
MARKER_ONLY = frozenset({"end-block-scalar"})

# A scope the official grammar has no question for. That grammar writes the wrapped item in the scope's place. A
# `(recover)` says where a failed cut stops unwinding, and a `(commit)` is a scoped cut. An annotation names the
# characters, and a `(wrap)` puts markers around them.
#
# A `(max)` is a scope too, and it falls outside this list. The official grammar writes the bound of a `(max)` as a bare
# `(max)` before what the bound covers.
_WRAPS_WHAT_IT_WRITES = (ir.CommitWrapper, ir.RecoverWrapper, ir.TokenWrapper, ir.Wrapper)

# The rules libyeast adds around the official grammar. The root the parser runs, and the unparsed recovery that root and
# a failed cut hand off to. They consume, and are not marker-only. The official grammar has no counterpart to compare
# them against. The recovery of the official grammar leaves them out.
OWN = frozenset(  # in alphabetical order.
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
# production. That production gives the token annotation somewhere to go.
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

    Wrapping part of a sequence nests a sequence inside a sequence. Say the markers around a directive, but not around
    the comments that follow it. Erasing the annotation leaves the nesting behind, where the official grammar writes the
    items flat.

    Sequencing is associative. Splicing them back is a change of form rather than of grammar. `normalize` applies it to
    the official grammar too. Neither side gains from that.
    """
    spliced: list[ir.Node] = []
    for item in items:
        spliced.extend(item.items if isinstance(item, ir.SeqTree) else [item])
    return tuple(spliced)


def _normalize(node: ir.Node) -> ir.Node:
    """
    `node` in the form the official grammar uses.

    This flattens sequences, and a sequence of a single item collapses into that item. An alternation whose last way
    matches nothing becomes the optional that grammar writes. A case whose branches hold the same thing becomes that
    thing.
    """
    node = ir.rebuilt(node, _normalize)
    if isinstance(node, ir.SeqTree):
        items = _flatten(node.items)
        return items[0] if len(items) == 1 else ir.SeqTree(items)
    if isinstance(node, ir.AltTree) and node.items and isinstance(node.items[-1], ir.EmptyTree):
        # An alternation whose last branch matches nothing is an optional, which is how the official grammar writes it.
        rest = node.items[:-1]
        return ir.OptTree(rest[0] if len(rest) == 1 else ir.AltTree(rest))
    if isinstance(node, ir.CaseTree):
        # A case whose branches are alike is that branch, with no dispatch. libyeast's soft-commit case reads that way
        # once the commit it wraps is erased to its item, matching the official grammar's bare rule.
        held = [branch.item for branch in node.branches] + ([node.default] if node.default is not None else [])
        if held and all(item == held[0] for item in held):
            return held[0]
    return node


def _erase(node: ir.Node, owner: str) -> ir.Node:
    """The node the official grammar writes where libyeast writes `node`."""
    if isinstance(node, _WRAPS_WHAT_IT_WRITES):
        return _erase(node.item, owner)
    if isinstance(node, ir.MaxWrapper) and node.item is not None:
        # libyeast wraps a production in `(max)`. The official grammar writes the character bound as a bare `(max)`
        # before that production instead. The wrapping is undone into the sequence the vendored grammar writes.
        inner = _erase(node.item, owner)
        items = inner.items if isinstance(inner, ir.SeqTree) else (inner,)
        return ir.SeqTree((ir.MaxWrapper(node.limit),) + items)
    if isinstance(node, ir.RefCall) and node.name in MARKER_ONLY:
        return ir.EmptyTree()
    if isinstance(node, ir.RefCall) and not node.args and node.name in INDICATORS and node.name != owner:
        return ir.OneCharSet(INDICATORS[node.name])
    if isinstance(node, ir.SeqTree):
        kept = [
            _erase(item, owner)
            for item in node.items
            if not isinstance(item, (ir.EmitAction, ir.CutAction, ir.ErrorAction))
        ]
        return ir.SeqTree(tuple(kept))
    return ir.rebuilt(node, lambda item: _erase(item, owner))


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
    """A grammar's mapping with its sequences flattened. This is what `check_vendor_spec` holds `official` to."""
    flattened = {}
    for name, production in grammar.items():
        flattened[name] = ir.Prod(production.number, name, production.params, _normalize(production.body))
    return ir2annotated.regenerate(flattened)


def main() -> None:
    yaml.safe_dump(official(annotated2ir.load()), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
