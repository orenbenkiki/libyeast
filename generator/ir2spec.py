# SPDX-License-Identifier: MIT
"""Recover the official grammar from libyeast's, to prove we still speak its language.

libyeast's grammar adds two things the official one lacks: the token annotations, and a production for each indicator
character so that an annotation has somewhere to attach. Both are undone here — an annotation is dropped and its child
kept, a zero-width marker is dropped outright, and a reference to an indicator production becomes the character it
names. What comes out must be the vendored grammar, which `check_vendor_spec.py` is what checks.

Usage: `python3 generator/ir2spec.py > recovered.yaml`
"""

import sys

import ir2annotated
import ir
import annotated2ir

import yaml

# The rules libyeast adds that match nothing at all. They exist only to emit a marker, so the official grammar can
# leave them out and never notice: `x / end-block-scalar` is `x / <empty>`, which is `x?`, which is what it writes.
MARKER_ONLY = frozenset({"end-block-scalar"})

# The rules libyeast adds around the official grammar: the root the parser runs, and the unparsed recovery it and a
# failed cut hand off to. They consume, so they are not marker-only, and the official grammar has no counterpart to
# compare them against, so recovering it just leaves them out.
OWN = frozenset(  # in alphabetical order
    {
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

# The character each indicator production names. The official grammar writes the character; libyeast writes the
# production, so that the token annotation has somewhere to go.
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


def flatten(items):
    """The items of a sequence, with nested sequences spliced in.

    Wrapping part of a sequence — the markers around a directive, say, but not around the comments that follow it —
    nests a sequence inside a sequence. Erasing the annotation leaves the nesting behind, where the official grammar
    writes the items flat. Sequencing is associative, so splicing them back is a change of spelling, not of grammar,
    and `normalize` applies it to the official grammar too, so neither side is flattered.
    """
    spliced = []
    for item in items:
        spliced.extend(item.items if isinstance(item, ir.Seq) else [item])
    return tuple(spliced)


def normalize(node):
    """`node` with its sequences flattened, and a sequence of one item collapsed into that item."""
    node = ir.rebuilt(node, normalize)
    if isinstance(node, ir.Seq):
        items = flatten(node.items)
        return items[0] if len(items) == 1 else ir.Seq(items)
    if isinstance(node, ir.Alt) and node.items and isinstance(node.items[-1], ir.Empty):
        # An alternation whose last branch matches nothing is an optional, which is how the official grammar writes it.
        rest = node.items[:-1]
        return ir.Opt(rest[0] if len(rest) == 1 else ir.Alt(rest))
    return node


def erase(node, owner):
    """What the official grammar writes where libyeast writes `node`."""
    if isinstance(node, (ir.Token, ir.Wrap, ir.Recover, ir.Commit)):
        # A `(recover)` says where a failed cut stops unwinding, and a `(commit)` is a scoped cut, both questions the
        # official grammar never asks: what they wrap is what that grammar writes.
        return erase(node.item, owner)
    if isinstance(node, ir.Max) and node.item is not None:
        # libyeast wraps a production in `(max)`; the official grammar writes the character bound as a bare `(max)`
        # before that production instead, so the wrapping is undone into the sequence the vendored grammar spells.
        inner = erase(node.item, owner)
        items = inner.items if isinstance(inner, ir.Seq) else (inner,)
        return ir.Seq((ir.Max(node.limit),) + items)
    if isinstance(node, ir.Ref) and node.name in MARKER_ONLY:
        return ir.Empty()
    if isinstance(node, ir.Ref) and not node.args and node.name in INDICATORS and node.name != owner:
        return ir.Char(INDICATORS[node.name])
    if isinstance(node, ir.Seq):
        kept = [erase(item, owner) for item in node.items if not isinstance(item, (ir.Emit, ir.Cut, ir.Error))]
        return ir.Seq(tuple(kept))
    return ir.rebuilt(node, lambda item: erase(item, owner))


def official(grammar):
    """The official grammar's mapping, recovered from libyeast's."""
    recovered = {}
    for name, production in grammar.items():
        if name in MARKER_ONLY or name in OWN:
            continue  # the official grammar has no such rule: a marker-only one, or one of libyeast's own
        body = normalize(erase(production.body, name))
        recovered[name] = ir.Prod(production.number, name, production.params, body)
    return ir2annotated.regenerate(recovered)


def normalized(grammar):
    """A grammar's mapping with its sequences flattened — what `official` is compared against."""
    flattened = {}
    for name, production in grammar.items():
        flattened[name] = ir.Prod(production.number, name, production.params, normalize(production.body))
    return ir2annotated.regenerate(flattened)


def main():
    yaml.safe_dump(official(annotated2ir.load()), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
