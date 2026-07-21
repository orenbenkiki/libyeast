# SPDX-License-Identifier: MIT
"""
Typed IR for the YAML grammar.

`annotated2ir.py` reads the grammar into these nodes; `grammar2decoder.py` and the gate checks read them back out. This
is a faithful 1:1 mirror of the yaml-grammar operator vocabulary — every operator maps to one node and nothing is
normalized here, so the round-trip stays exact. Flattening and simplification belong to whatever consumes the IR:
`ir2spec.py` does its own, to compare against the official grammar.

Every node is a dataclass, and every grammar node it holds is a field of its own or an item of a tuple of them — which
is what lets a walker recurse over the IR without knowing what any particular node is. `Branch` exists for that reason:
a `(case)` branch is a node rather than a bare pair, so nothing has to special-case one.
"""

from dataclasses import dataclass, fields, is_dataclass, replace

# The production the whole grammar hangs off: a YAML stream, and then the end of the input.
ROOT = "l-yeast-stream"

# The parameters `normalize.monomorphize` specializes away into a production's name: the ones with finitely many values,
# passed lexically so their value is settled where a production is entered. The context `c`, the chomping `t` (once
# `lift-chomping` has made it lexical rather than a match's stashed state), and the resume policy `r` are the three;
# `n`, `m` and `f` are integers and stay. A left-out finite parameter takes its default — an omitted resume policy is
# the no-resume one — which `entry` fills, so the root and a fixture that names none reach the copy that fixes it.
FINITE_PARAMS = ("c", "t", "r")
FINITE_DEFAULTS = {"r": "n"}


def specialized(base, bindings):
    """
    The name of the monomorphic copy of `base` that fixes `bindings` — its finite parameters, in `FINITE_PARAMS` order,
    each written `_<parameter>_<value>`. A parameter left unset (its value `None`, as a context is where none is
    established) or at its default is not in the name, so the copy that fixes only defaults keeps the base name and
    resolves like it — which keeps the root `l-yeast-stream` and lets a fixture that names no resume policy find it.
    """
    return base + "".join(
        f"_{parameter}_{bindings[parameter]}"
        for parameter in FINITE_PARAMS
        if bindings.get(parameter) is not None and bindings.get(parameter) != FINITE_DEFAULTS.get(parameter)
    )


def entry(grammar, name, parameters):
    """
    Resolve a call of `name` with `parameters` to the production `grammar` holds: its monomorphic copy if present — the
    finite parameters it fixes in its name moved out of the arguments — else `name` unchanged, arguments whole. A
    left-out finite default is filled, so a fixture or the root finds its copy. Only the finite parameters the resolved
    production no longer declares are dropped: a monomorphic copy has shed them, but the base name at its default is
    still the polymorphic production, which declares and is passed them.
    """
    given = {parameter: parameters[parameter] for parameter in FINITE_PARAMS if parameter in parameters}
    for bindings in (given, {**FINITE_DEFAULTS, **given}):
        resolved = specialized(name, bindings)
        if resolved in grammar:
            declared = set(grammar[resolved].params)
            return resolved, {n: v for n, v in parameters.items() if n not in FINITE_PARAMS or n in declared}
    return name, dict(parameters)


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
class AutoDetectIndent:
    """
    `<auto-detect-indent>`: the indentation of the next line that holds a character other than a space, less `n`.

    The current line counts only if the parse is at its start — so a block collection measures the line it stands on,
    and a block scalar measures past the rest of its header line, the break, and however many empty lines follow.
    """


@dataclass(frozen=True)
class AutoDetectInLineIndent:
    """
    `<auto-detect-in-line-indent>`: the spaces that follow, here, on this line — not a line's indentation.

    What a compact collection is indented by, measured from just after the `-` or the `?` that introduced it.
    """


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
class Branch:
    """One branch of a `(case)` or a `(flip)`: what to use when the parameter has this value."""

    value: str
    item: object


@dataclass(frozen=True)
class Flip:
    """`(flip)`: a pure value transformer over a parameter (e.g. `in-flow` mapping one context to another)."""

    var: str
    branches: tuple  # (Branch, ...), each holding a result expression


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
class StartOfLine:
    """`<start-of-line>`: a zero-width assertion that the parser is at the start of a line."""


@dataclass(frozen=True)
class EndOfStream:
    """`<end-of-stream>`: a zero-width assertion that the parser is at the end of the input."""


@dataclass(frozen=True)
class Invalid:
    """
    `<invalid>`: one byte that begins no valid UTF-8 sequence. It belongs to no character set, so it matches nowhere the
    grammar names a character — only the recovery rules reach for it, where a run of these is `unparsed-invalid`.
    """


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
class TrimStar:
    """
    A maximal run of `full` whose trailing run of `trim` characters is given back — the normalized form of a `(trim*
    content)*`, where `full` is `trim | content`. A plain scalar's in-line run is one: it keeps its inner spaces and
    leaves the trailing ones, so `nb-ns-plain-in-line` — `(s-white* ns-plain-char)*` — becomes `TrimStar` over `s-white
    | ns-plain-char`, trimming `s-white`; the single- and double-quoted in-line runs likewise. It matches the empty
    string, so a run of nothing but `trim` characters consumes none of them.
    """

    full: object
    trim: object


@dataclass(frozen=True)
class ConsumeSpan:
    """
    A maximal run of `set` characters, consumed in one scan — what a `Star` over a character class becomes in the
    canonical form, mapping to a single repeated-char-set match. Matches the empty string.
    """

    set: object


@dataclass(frozen=True)
class ConsumeTrimmedSpan:
    """
    A maximal run of `full` characters whose trailing run of `trim` is given back, consumed in one scan — what a
    `TrimStar` becomes in the canonical form, the two-set trimming scan a plain or quoted scalar's line compiles to.
    Matches the empty string.
    """

    full: object
    trim: object


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
    """
    `(max)`: a bound of `limit` characters — the implicit-key lookahead limit (§7.4.2).

    The vendored grammar writes it before a production, as a length note on what follows: `(max): N`. libyeast writes it
    around the production instead — `(max): [N, message, rule]` — where it is the bounded window a parser resolves the
    key within: consuming past `limit` characters is the error `message`, and unparsed from there. The single line the
    key is also held to needs no help here — the flow-key context already forbids a break inside a key. `ir2spec` undoes
    the wrapping back to the vendored's preceding `(max): N`.
    """

    limit: object
    message: object = None
    item: object = None


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
    """
    `(case)`: dispatch on a parameter's value, each branch a grammar node, `default` the `else` for a value no branch
    names — `None` where there is none, and then a value with no branch is a path that does not match.
    """

    var: str
    branches: tuple  # (Branch, ...), each holding a grammar node
    default: object = None


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
class Increase:
    """
    `(increase)`: increase indentation parameter `param` to the current column — `param = max(param, column)` — a
    zero-width action. It records the widest indentation seen so far, which is how a block scalar's leading empty lines
    set the floor its first content line may not fall below.
    """

    param: str


# --- token annotations ---
#
# The parser accumulates the characters it consumes into a run, and gives the run a code. A run ends — becoming one
# token — wherever a `Token` scope begins or ends, and wherever an `Emit` marker falls. So an annotation does not make
# *a* token: it says what code the characters consumed within it carry, and where the runs are cut.
#
# A character consumed under no annotation at all carries the code `unparsed`, which is what the parser says about input
# it could not parse. On the success path that is always a mistake, so `validate_grammar.py` holds every
# character-consuming node to lying within some `Token`.


@dataclass(frozen=True)
class Token:
    """
    `(token)`: the characters `item` consumes carry `code`, but for those a nested annotation claims.

    The run is cut at both edges, so the characters before, within and after `item` fall into separate tokens. `item`
    may yield several tokens (where it nests annotations of its own) or none at all (where it consumes nothing).
    """

    code: str
    item: object


@dataclass(frozen=True)
class Wrap:
    """
    `(wrap)`: zero-width `begin` and `end` markers bracketing `item` — sugar for an `Emit` on either side of it.

    A node of its own, rather than the sequence it stands for, so that the two markers are paired by construction and a
    `begin` cannot lose its `end`.
    """

    begin: str
    end: str
    item: object


@dataclass(frozen=True)
class Emit:
    """`(emit)`: a zero-width token at this point, which also cuts the run of characters around it."""

    code: str


@dataclass(frozen=True)
class PushCode:
    """
    A zero-width action that cuts the run and sets the code its following characters carry to `code` — what a `(token)`
    opens with, over the production's own, which `PopCode` restores at its trailing edge.
    """

    code: str


@dataclass(frozen=True)
class PopCode:
    """
    A zero-width action that cuts the run and restores the code its following characters carry to the production's own —
    `env["code"]`, the code it was entered under, held on its frame rather than a stack, since a `(token)` never nests
    within one body. Paired with `PushCode`: `Token(code, item)` lowers to `PushCode(code), item, PopCode`.
    """


@dataclass(frozen=True)
class OpenMatch:
    """
    A zero-width action that sets the origin a `(match)` measures from to the current position — what `(<<<)` marks
    before the run it bounds. `CloseMatch` restores it at the trailing edge.
    """


@dataclass(frozen=True)
class CloseMatch:
    """
    A zero-width action that restores the `(match)` origin to the production's own — `env["match_start"]`, the origin it
    was entered under, held on its frame not a stack, since a `(<<<)` never nests within one body. Paired with
    `OpenMatch`: `Bound(item)` lowers to `OpenMatch, item, CloseMatch`.
    """


@dataclass(frozen=True)
class OpenWindow:
    """
    A zero-width action that opens a `(max)` window `limit` characters wide, past which a committed consume fails the
    cut `message` names. Only the outermost applies — a nested one keeps the outer edge, being the buffer — and
    `CloseWindow` restores it at the trailing edge.
    """

    limit: object
    message: str


@dataclass(frozen=True)
class CloseWindow:
    """
    A zero-width action that restores the `(max)` window to the production's own — `env["ceiling"]`, the window it was
    entered under, held on its frame not a stack, since a `(max)` never nests within one body. Paired with `OpenWindow`:
    `Max(limit, message, item)` lowers to `OpenWindow(limit, message), item, CloseWindow`.
    """


@dataclass(frozen=True)
class Cut:
    """
    `(cut)`: a zero-width commit past which the parse does not backtrack; on a later failure it is the error, and

    `message` names the expectation to report — a key into `grammar/messages.yaml`.
    """

    message: str


@dataclass(frozen=True)
class Commit:
    """
    `(commit)`: match `item`, committing only to `item` being present — a `(cut)` scoped to what follows it.

    Where a `(cut)` commits the whole parse from its point on, `(commit)` commits only that `item` can match at all: if
    `item` cannot (a quoted scalar that never closes, a flow collection that never ends), `message` is the error, as a
    `(cut)` raises it. But if `item` matches and a *later* rule fails, the match backtracks like any other — the
    commitment does not reach past `item`. That is what lets a flow scalar which closed cleanly be reinterpreted when it
    turns out not to be the mapping key it was tried as, while one that never closed is still the error it should be.
    `message` keys `grammar/messages.yaml`, as a `(cut)`'s does. An implicit key that will not parse is simply not this
    key rather than an error, so where a commit is reached in a key context the grammar wraps it in a `(case) c` whose
    key branches are the bare `item` and whose `else` is the commit — the softening a switch on `c`, not the parser's.
    """

    message: str
    item: object


@dataclass(frozen=True)
class Error:
    """
    `(error)`: a zero-width error token at this point, which also cuts the run of characters around it.

    `message` names the expectation to report — a key into `grammar/messages.yaml`, as a `(cut)`'s does. Unlike a
    `(cut)` it is a match rather than a commit: it emits and succeeds, so what the parser does about the input from here
    is the grammar's to say, in the rule that holds it.
    """

    message: str


@dataclass(frozen=True)
class Recover:
    """
    `(recover)`: where a `(cut)` inside `item` stops unwinding, when `recovery` says it stops here.

    A cut unwinds past every frame between it and whatever will answer for it. This is a rule saying "that is me": the
    error is emitted, the markers `item` opened are closed down to this point and no further, and `recovery` matches
    whatever of the input this rule is willing to give up — after which the parse carries on from here as though `item`
    had matched, so a repetition around it takes its next turn.

    `recovery` decides whether that happens at all: a rule reached under a resume policy that does not recover here has
    no branch to take, so it does not match, and the cut goes on unwinding to whoever does answer for it. That is what
    keeps a policy that recovers elsewhere from noticing this rule exists.
    """

    recovery: object
    item: object


@dataclass(frozen=True)
class Prod:
    """A named production: its spec number, name, parameter list, and body node."""

    number: int
    name: str
    params: tuple
    body: object


# The nodes that match without consuming: a lookahead reads the input and gives it back. In alphabetical order.
ZERO_WIDTH = (ExcludeAt, Look, LookBehind, NegLook)


def rebuilt(node, visit):
    """
    `node` with every grammar node it holds replaced by `visit` of that node.

    The generic walker the module's shape exists for: it reaches every node the same way, whatever node it is — a field
    of its own or an item of a tuple of them — so a caller recurses without special-casing any node. A `Lit` and a
    `Param` are values rather than grammar and are left as they are.
    """
    if not is_dataclass(node):
        return node
    changed = {}
    for field in fields(node):
        value = getattr(node, field.name)
        if is_dataclass(value) and not isinstance(value, (Lit, Param)):
            changed[field.name] = visit(value)
        elif isinstance(value, tuple) and value and all(is_dataclass(item) for item in value):
            changed[field.name] = tuple(visit(item) for item in value)
    return replace(node, **changed) if changed else node
