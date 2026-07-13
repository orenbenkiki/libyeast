# SPDX-License-Identifier: MIT
"""Recover the official grammar from libyeast's, to prove we still speak its language.

libyeast's grammar adds two things the official one lacks: the token annotations, and a production for each indicator
character so that an annotation has somewhere to attach. Both are undone here — an annotation is dropped and its child
kept, a zero-width marker is dropped outright, and a reference to an indicator production becomes the character it
names. What comes out must be the vendored grammar, which `check_vendor_spec.py` is what checks.

Usage: `python3 generator/ir2spec.py > recovered.yaml`
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir2annotated  # noqa: E402
import ir  # noqa: E402
import annotated2ir  # noqa: E402

import yaml  # noqa: E402

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
    if isinstance(node, ir.Case):
        return ir.Case(node.var, tuple((value, normalize(branch)) for value, branch in node.branches))
    if not dataclasses.is_dataclass(node):
        return node
    changed = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if dataclasses.is_dataclass(value) and not isinstance(value, (ir.Lit, ir.Param)):
            changed[field.name] = normalize(value)
        elif isinstance(value, tuple) and value and all(dataclasses.is_dataclass(item) for item in value):
            changed[field.name] = tuple(normalize(item) for item in value)
    node = dataclasses.replace(node, **changed) if changed else node
    if isinstance(node, ir.Seq):
        items = flatten(node.items)
        return items[0] if len(items) == 1 else ir.Seq(items)
    return node


def erase(node, owner):
    """What the official grammar writes where libyeast writes `node`."""
    if isinstance(node, (ir.Token, ir.Wrap)):
        return erase(node.item, owner)
    if isinstance(node, ir.Case):
        return ir.Case(node.var, tuple((value, erase(branch, owner)) for value, branch in node.branches))
    if isinstance(node, ir.Ref) and not node.args and node.name in INDICATORS and node.name != owner:
        return ir.Char(INDICATORS[node.name])
    if isinstance(node, ir.Seq):
        kept = [erase(item, owner) for item in node.items if not isinstance(item, ir.Emit)]
        return ir.Seq(tuple(kept))
    if not dataclasses.is_dataclass(node):
        return node
    changed = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if dataclasses.is_dataclass(value) and not isinstance(value, (ir.Lit, ir.Param)):
            changed[field.name] = erase(value, owner)
        elif isinstance(value, tuple) and value and all(dataclasses.is_dataclass(item) for item in value):
            changed[field.name] = tuple(erase(item, owner) for item in value)
    return dataclasses.replace(node, **changed) if changed else node


def official(grammar):
    """The official grammar's mapping, recovered from libyeast's."""
    recovered = {}
    for name, production in grammar.items():
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
