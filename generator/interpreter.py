# SPDX-License-Identifier: MIT
"""A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

Slow and obviously correct: it matches a production against an input the way the grammar reads, character by character
with backtracking, and emits the yeast token stream — so that libyeast's grammar is proved to produce the reference's
tokens before any C exists to be wrong, and so that, taught the canonical form later, it becomes the net every
normalization step is checked against.

This is the engine core. It matches the character-level nodes — `Char`, `Range`, `Diff`, `Empty`, `Seq`, `Alt`, and
`Ref` to another production — and emits every character it consumes as unparsed, the code a character carries under no
token annotation. `SUPPORTED` says which nodes it knows; `coverable` says which fixtures rest on only those, and so are
the ones it is asked to reproduce.
"""

import ir
import wire

# The grammar nodes the interpreter matches. It grows a family at a time, and with it the fixtures it can reproduce.
SUPPORTED = (ir.Char, ir.Range, ir.Diff, ir.Empty, ir.Seq, ir.Alt, ir.Ref)


def coverable(production, grammar):
    """Whether every node reachable from `production` is one the interpreter supports."""
    seen = set()
    frontier = [production]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        node = grammar.get(name)
        if node is None:
            return False
        stack = [node.body]
        while stack:
            current = stack.pop()
            if isinstance(current, ir.Ref):
                frontier.append(current.name)
                continue
            if not isinstance(current, SUPPORTED):
                return False
            for field in current.__dataclass_fields__:
                value = getattr(current, field)
                for child in value if isinstance(value, tuple) else (value,):
                    if hasattr(child, "__dataclass_fields__"):
                        stack.append(child)
    return True


def match(node, text, position, grammar):
    """Match `node` against `text` from `position`, returning the position after what it consumed, or None to fail.

    A backtracking matcher: an alternative that fails leaves the position untouched for the next to try, since nothing
    is committed until it is returned.
    """
    if isinstance(node, ir.Char):
        return position + 1 if position < len(text) and ord(text[position]) == node.cp else None
    if isinstance(node, ir.Range):
        return position + 1 if position < len(text) and node.lo <= ord(text[position]) <= node.hi else None
    if isinstance(node, ir.Empty):
        return position
    if isinstance(node, ir.Ref):
        return match(grammar[node.name].body, text, position, grammar)
    if isinstance(node, ir.Seq):
        for item in node.items:
            position = match(item, text, position, grammar)
            if position is None:
                return None
        return position
    if isinstance(node, ir.Alt):
        for item in node.items:
            reached = match(item, text, position, grammar)
            if reached is not None:
                return reached
        return None
    if isinstance(node, ir.Diff):
        reached = match(node.base, text, position, grammar)
        if reached is None:
            return None
        for excluded in node.minus:
            if match(excluded, text, position, grammar) is not None:
                return None
        return reached
    raise NotImplementedError(f"interpreter does not support {type(node).__name__}")


def run(grammar, production, data):
    """Run `production` on the UTF-8 `data`, returning the yeast tokens it emits, or None if the production does not
    match. Every character consumed is emitted as unparsed, cut into a token per line's content and each break.
    """
    text = data.decode("utf-8")
    position = match(grammar[production].body, text, 0, grammar)
    if position is None:
        return None
    consumed = text[:position].encode("utf-8")
    return wire.split_unparsed(wire.units(wire.escape(consumed)), wire.Mark(0, 0, 1, 0))
