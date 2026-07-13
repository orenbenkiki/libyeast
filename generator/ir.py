# SPDX-License-Identifier: MIT
"""Typed IR for the YAML grammar.

`spec2grammar.py` emits these nodes into `grammar.py`; `grammar2parser.py` consumes them. This is a faithful 1:1
mirror of the yaml-grammar operator vocabulary — every operator maps to one node and nothing is normalized here.
Flattening and simplification belong to later `grammar2parser.py` passes, so the round-trip stays exact.
"""

from dataclasses import dataclass

# --- value / parameter expressions ---


@dataclass(frozen=True)
class Param:
    """A grammar parameter: `n` (indentation), `c` (context), `m` (indent indicator), or `t` (chomping)."""

    name: str


@dataclass(frozen=True)
class Lit:
    """A literal value: an int, a string (e.g. `"block-in"`), or None (the grammar's `null`)."""

    value: object


@dataclass(frozen=True)
class Match:
    """The `(match)` special value: the text matched so far, for length/ordinal computations."""


@dataclass(frozen=True)
class Add:
    """`(+)`: integer addition of two expressions."""

    a: object
    b: object


@dataclass(frozen=True)
class Sub:
    """`(-)`: integer subtraction of two expressions."""

    a: object
    b: object


@dataclass(frozen=True)
class Ord:
    """`(ord)`: the numeric value of a matched digit."""

    arg: object


@dataclass(frozen=True)
class Len:
    """`(len)`: the length of a match."""

    arg: object


@dataclass(frozen=True)
class Flip:
    """`(flip)`: a pure value transformer over a parameter (e.g. `in-flow` mapping one context to another)."""

    var: str
    branches: tuple  # ((value, result-expression), ...)


# --- grammar nodes (matchers) ---


@dataclass(frozen=True)
class Char:
    """A single literal codepoint."""

    cp: int


@dataclass(frozen=True)
class Range:
    """An inclusive codepoint range `[lo, hi]`."""

    lo: int
    hi: int


@dataclass(frozen=True)
class Ref:
    """A reference to another production, passing `args` (expressions)."""

    name: str
    args: tuple = ()


@dataclass(frozen=True)
class Empty:
    """`<empty>`: the epsilon match."""


@dataclass(frozen=True)
class Seq:
    """`(all)`: an ordered concatenation."""

    items: tuple


@dataclass(frozen=True)
class Alt:
    """`(any)`: an ordered alternation."""

    items: tuple


@dataclass(frozen=True)
class Star:
    """`(***)`: zero or more."""

    item: object


@dataclass(frozen=True)
class Plus:
    """`(+++)`: one or more."""

    item: object


@dataclass(frozen=True)
class Opt:
    """`(???)`: optional (zero or one)."""

    item: object


@dataclass(frozen=True)
class Rep:
    """`({N})`: exactly `count` times, where `count` is an expression (a literal or a parameter)."""

    count: object
    item: object


@dataclass(frozen=True)
class Look:
    """`(===)`: positive lookahead (zero-width)."""

    item: object


@dataclass(frozen=True)
class NegLook:
    """`(!==)`: negative lookahead (zero-width)."""

    item: object


@dataclass(frozen=True)
class LookBehind:
    """`(<==)`: positive look-behind (the preceding input matched `item`)."""

    item: object


@dataclass(frozen=True)
class Bound:
    """`(<<<)`: match `item`, subject to a predicate embedded within it (e.g. an indentation-length bound)."""

    item: object


@dataclass(frozen=True)
class Diff:
    """`(---)`: character-class subtraction — `base` but none of `minus`."""

    base: object
    minus: tuple


@dataclass(frozen=True)
class ExcludeAt:
    """`(exclude)`: a zero-width negative guard (the current position is not at `item`)."""

    item: object


@dataclass(frozen=True)
class Max:
    """`(max)`: a length bound (the single-line simple-key limit)."""

    limit: object


@dataclass(frozen=True)
class Lt:
    """`(<)`: assert the first expression is less than the second."""

    a: object
    b: object


@dataclass(frozen=True)
class Le:
    """`(<=)`: assert the first expression is less than or equal to the second."""

    a: object
    b: object


@dataclass(frozen=True)
class Case:
    """`(case)`: dispatch on a parameter's value, each branch a grammar node."""

    var: str
    branches: tuple  # ((value, node), ...)


@dataclass(frozen=True)
class Bind:
    """`(if)` + `(set)`: match `cond`, binding parameter `param` to `value` as a side effect."""

    cond: object
    param: str
    value: object


@dataclass(frozen=True)
class SetVar:
    """`(set)` standalone: bind parameter `param` to `value` with no matching (a zero-width action)."""

    param: str
    value: object


@dataclass(frozen=True)
class Prod:
    """A named production: its spec number, name, parameter list, and body node."""

    number: int
    name: str
    params: tuple
    body: object
