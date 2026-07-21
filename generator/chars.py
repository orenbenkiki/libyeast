# SPDX-License-Identifier: MIT
"""
The character model the decoder is built from, derived from the grammar IR.

The grammar names 57 literal characters and tests 19 distinct character sets, so those 76 are every question the parser
can ask about a character. `Model.key` answers all of them at once: a 32-bit word holding the character's named-literal
id, one bit per character set it belongs to, and its UTF-8 length. Unions and subtractions are evaluated here, so a test
in the parser is a single bit test.
"""

import dataclasses

import ir

MAX_CODEPOINT = 0x10FFFF

# The key's layout. The parser tests it with `key & YS_SET_*` and `YS_LIT(key) == YS_LIT_*`, so the fields must not move
# without the generated tables moving with them.
LIT_BITS = 6  # the named-literal id, in bits 0..5
SET_SHIFT = 6  # the set bits, one per tested set, from bit 6 up
LEN_SHIFT = 26  # the UTF-8 length, in bits 26..28

LIT_NONE = 0  # not one of the grammar's named characters; the named characters take the ids above it


def denote(grammar, node, seen=()):
    """
    The set of codepoints `node` denotes, or None if `node` is not a pure character node.

    A token annotation says what the characters are called, not which they are, so it is looked straight through.
    """
    if isinstance(node, (ir.Token, ir.Wrap)):
        return denote(grammar, node.item, seen)
    if isinstance(node, ir.Char):
        return ("literal", node.cp)
    if isinstance(node, ir.Range):
        return ("range", node.lo, node.hi)
    if isinstance(node, ir.Alt):
        parts = [denote(grammar, item, seen) for item in node.items]
        return None if any(part is None for part in parts) else ("union", tuple(parts))
    if isinstance(node, ir.Diff):
        base = denote(grammar, node.base, seen)
        minus = [denote(grammar, item, seen) for item in node.minus]
        if base is None or any(part is None for part in minus):
            return None
        return ("difference", base, tuple(minus))
    if isinstance(node, ir.Ref) and not node.args and node.name in grammar and node.name not in seen:
        return denote(grammar, grammar[node.name].body, seen + (node.name,))
    return None


def contains(denotation, codepoint):
    """Whether `denotation` contains `codepoint`."""
    kind = denotation[0]
    if kind == "literal":
        return codepoint == denotation[1]
    if kind == "range":
        return denotation[1] <= codepoint <= denotation[2]
    if kind == "union":
        return any(contains(part, codepoint) for part in denotation[1])
    if kind == "difference":
        return contains(denotation[1], codepoint) and not any(contains(part, codepoint) for part in denotation[2])
    raise ValueError(f"unknown denotation {denotation!r}")


def children(node):
    """The IR nodes directly nested within `node`."""
    if not dataclasses.is_dataclass(node):
        return
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        for item in value if isinstance(value, tuple) else (value,):
            if dataclasses.is_dataclass(item):
                yield item


def gathered(grammar, kind, of):
    """What `of` takes from every node of `kind` anywhere in `grammar`, without repeats and in order."""
    found = set()
    for production in grammar.values():
        pending = [production.body]
        while pending:
            node = pending.pop()
            if isinstance(node, kind):
                found.add(of(node))
            pending.extend(children(node))
    return sorted(found)


def literals(grammar):
    """Every codepoint the grammar names as a literal character, ordered by codepoint."""
    return gathered(grammar, ir.Char, lambda node: node.cp)


def ranges(grammar):
    """Every codepoint range the grammar names, as an ordered `[(low, high)]`."""
    return gathered(grammar, ir.Range, lambda node: (node.lo, node.hi))


def representatives(grammar):
    """
    One codepoint from each segment the grammar can tell apart, in order.

    Every set is built from the grammar's literals and ranges, so a key is constant across the codepoints between two
    consecutive boundaries. Checking one codepoint per segment is therefore exhaustive, at a few dozen probes rather
    than the 1.1 million a sweep would cost.
    """
    boundaries = {0}
    for codepoint in literals(grammar):
        boundaries |= {codepoint, codepoint + 1}
    for low, high in ranges(grammar):
        boundaries |= {low, high + 1}
    return sorted(boundary for boundary in boundaries if boundary <= MAX_CODEPOINT)


def tested_sets(grammar):
    """
    The character sets the grammar tests, as an ordered `[(name, denotation)]`.

    A tested set is a *maximal* character node — one whose parent is not itself a character node — so the ranges inside
    `c-printable`'s union do not count: nothing asks about them alone, only about `c-printable`. Sets denoting the same
    codepoints share one entry. A set takes the name of the production defining it, or, where the grammar tests it
    inline, the name of its enclosing production plus an index (`ns-plain-first` holds two).
    """
    owners = {}

    def collect(node, owner, is_inside_set):
        denotation = denote(grammar, node)
        if denotation is not None:
            if not is_inside_set and denotation[0] != "literal":
                owners.setdefault(denotation, owner)
            is_inside_set = True
        for child in children(node):
            collect(child, owner, is_inside_set)

    for name, production in grammar.items():
        collect(production.body, name, False)

    defined = {}
    for name, production in sorted(grammar.items()):
        denotation = denote(grammar, production.body)
        if denotation is not None and denotation[0] != "literal":
            defined.setdefault(denotation, name)

    named, inline_counts = [], {}
    for denotation, owner in owners.items():
        name = defined.get(denotation)
        if name is None:
            index = inline_counts.get(owner, 0)
            inline_counts[owner] = index + 1
            name = f"{owner}-inline-{index}"
        named.append((name, denotation))
    return sorted(named)


class Model:
    """The character model: the grammar's named literals, the sets it tests, and the key of any codepoint."""

    def __init__(self, grammar):
        self.literals = literals(grammar)
        self.sets = tested_sets(grammar)
        self.literal_ids = {codepoint: index + 1 for index, codepoint in enumerate(self.literals)}
        # The sentinels take the ids just past the named characters, so they cannot collide with one.
        self.lit_eof = len(self.literals) + 1
        self.lit_invalid = self.lit_eof + 1
        if self.lit_invalid >= (1 << LIT_BITS):
            raise ValueError(f"{len(self.literals)} literals plus the sentinels overflow {LIT_BITS} bits")
        if SET_SHIFT + len(self.sets) > LEN_SHIFT:
            raise ValueError(f"{len(self.sets)} set bits do not fit between bit {SET_SHIFT} and bit {LEN_SHIFT}")

    def set_mask(self, index):
        """The bit mask of the tested set at `index`."""
        return 1 << (SET_SHIFT + index)

    def key(self, codepoint, length):
        """The key of `codepoint`, encoded in `length` bytes."""
        key = self.literal_ids.get(codepoint, LIT_NONE) | (length << LEN_SHIFT)
        for index, (_name, denotation) in enumerate(self.sets):
            if contains(denotation, codepoint):
                key |= self.set_mask(index)
        return key

    def sentinel(self, literal_id, length):
        """The key of a sentinel: a literal id and no set bits, so every membership test fails at it."""
        return literal_id | (length << LEN_SHIFT)
