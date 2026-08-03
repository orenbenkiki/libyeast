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

Every node also answers `references()`: the productions its subtree names directly, without following into their bodies.
Each class spells its own fields out, so which fields can hold a production — and which are codes, messages, or counts —
is written where the node is defined, and reachability is read off the nodes themselves.
"""

from dataclasses import dataclass, fields, is_dataclass, replace

# The production the whole grammar hangs off: a YAML stream, and then the end of the input.
ROOT = "l-yeast-stream"

# The production a failed cut hands the input to — entered by name rather than called, so it is a start state of its own
# beside the root's copies: the unwind lands on it, and where the resume policy says so the parse carries on past it.
RECOVER = "l-recover"

# The parameters `normalize.monomorphize` specializes away into a production's name: the ones with finitely many values,
# passed lexically so their value is settled where a production is entered. The context `c`, the chomping `t` and the
# block scalar's indentation mode `i` (both once `lift-setters` has made them lexical rather than a match's stashed
# state), and the resume policy `r`; `n`, `m` and `f` are integers and stay. A left-out finite parameter takes its
# default — an omitted resume policy is the no-resume one — which `entry` fills, so the root and a fixture that names
# none reach the copy that fixes it.
FINITE_PARAMS = ("c", "t", "r", "i")
FINITE_DEFAULTS = {"r": "n"}

# The parameters that are one value for the parse rather than one per call: what `normalize.read_globals` takes off
# every declaration and every call, leaving the reads and the writes to reach the single slot. A global is what does not
# nest — the auto-detected indent is measured by the construct that opens and read while it stands, the block scalar's
# floor by its leading empty lines and read by its first content line, one construct at a time — and `clear-params` is
# what says where each stops applying, so a read past its region is refused rather than answered from what the last one
# left. In alphabetical order.
GLOBAL_PARAMS = ("f", "m")


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


def _refs(*values):
    """
    The production names held anywhere in `values`, for the `references` methods: a node contributes its own
    `references()`, a tuple its items', and anything else — a code, a message, a codepoint, `None` — nothing.
    """
    names = []
    for value in values:
        if isinstance(value, tuple):
            names.extend(_refs(*value))
        elif is_dataclass(value):
            names.extend(value.references())
    return names


# --- value / parameter expressions ---


@dataclass(frozen=True)
class Param:
    """A grammar parameter: `n` (indentation), `c` (context), `m` (indent indicator), or `t` (chomping)."""

    name: str

    def references(self):
        return []


@dataclass(frozen=True)
class Lit:
    """A literal value: an int, a string (e.g. `"block-in"`), or None (the grammar's `null`)."""

    value: object

    def references(self):
        return []


@dataclass(frozen=True)
class Match:
    """
    `(match)`: the text of the open run — the token about to be emitted, which is what a rule has just matched. What a
    rule reads when it must act on that text, the characters being in hand already, with nothing remembered about where
    they began.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class Global:
    """
    The value of the global `name` — one for the parse, not one per call.

    A value read off the single slot rather than a parameter passed to get here: `SetVar` and `Increase` write it,
    `ClearVar` says where it stops applying, and nothing declares it or carries it down a call.
    """

    name: str

    def references(self):
        return []


@dataclass(frozen=True)
class Indent:
    """
    The indentation in force: what the top of the stack holds, and what the characters here are measured against.

    A value read off the stack rather than a parameter passed to get here — `PushIndent` and `PopIndent` are what put it
    there and take it back, so nothing declares it and no call carries it.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class AutoDetectIndent:
    """
    `<auto-detect-indent>`: the indentation of the next line holding a character other than a space, less `n`, read
    without consuming anything and bounded by nothing.

    The official grammar's, and only ever read from it — libyeast's own grammar takes each such indentation where it
    stands and reads `Column`, so nothing here evaluates one. It stays because the vendored grammar the erasure gate
    compares against spells it, and one reader loads them both.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class Column:
    """
    `<column>`: the column the parse stands at, counted from zero.

    What a construct is indented by, read where its indentation has just been consumed rather than worked out by looking
    ahead for it: the run of spaces a line begins with leaves the parse at the column that run measures.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class Add:
    """`(+)`: integer addition of two expressions."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)


@dataclass(frozen=True)
class Sub:
    """`(-)`: integer subtraction of two expressions."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)


@dataclass(frozen=True)
class Len:
    """`(len)`: how many characters a matched string holds."""

    arg: object

    def references(self):
        return _refs(self.arg)


@dataclass(frozen=True)
class Atoi:
    """`(atoi)`: the integer the decimal digits of a matched string spell."""

    arg: object

    def references(self):
        return _refs(self.arg)


@dataclass(frozen=True)
class Branch:
    """One branch of a `(case)` or a `(flip)`: what to use when the parameter has this value."""

    value: str
    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Flip:
    """`(flip)`: a pure value transformer over a parameter (e.g. `in-flow` mapping one context to another)."""

    var: str
    branches: tuple  # (Branch, ...), each holding a result expression

    def references(self):
        return _refs(self.branches)


# --- grammar nodes (matchers) ---


@dataclass(frozen=True)
class Char:
    """A single literal codepoint."""

    cp: int

    def references(self):
        return []


@dataclass(frozen=True)
class Range:
    """An inclusive codepoint range `[lo, hi]`."""

    lo: int
    hi: int

    def references(self):
        return []


@dataclass(frozen=True)
class Ref:
    """A reference to another production, passing `args` (expressions)."""

    name: str
    args: tuple = ()

    def references(self):
        return [self.name, *_refs(self.args)]


@dataclass(frozen=True)
class Empty:
    """`<empty>`: the epsilon match."""

    def references(self):
        return []


@dataclass(frozen=True)
class StartOfLine:
    """`<start-of-line>`: a zero-width assertion that the parser is at the start of a line."""

    def references(self):
        return []


@dataclass(frozen=True)
class EndOfStream:
    """`<end-of-stream>`: a zero-width assertion that the parser is at the end of the input."""

    def references(self):
        return []


@dataclass(frozen=True)
class Invalid:
    """
    `<invalid>`: one byte that begins no valid UTF-8 sequence. It belongs to no character set, so it matches nowhere the
    grammar names a character — only the recovery rules reach for it, where a run of these is `unparsed-invalid`.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class CharSet:
    """
    One character out of `spans` — the set as the parser sees it, and the only question it can ask about a character.

    `spans` is a tuple of inclusive `(low, high)` codepoint intervals, sorted and disjoint and merged, so two nodes
    denoting the same characters are the same node and the sweep spells them once. The invalid byte is the interval
    `(-1, -1)`, a unit no character can hold, which is how a set holding it beside real characters says so.

    A union and a subtraction are worked out where this is made, never carried: `chars.Model` gives every character a
    key holding one bit per set the grammar tests, so what the parser runs is a bit test and nothing else. Whatever
    combination of characters and ranges the grammar spelled, the question at run time is the same shape.
    """

    spans: tuple

    def references(self):
        return []


@dataclass(frozen=True)
class Seq:
    """`(all)`: an ordered concatenation."""

    items: tuple

    def references(self):
        return _refs(self.items)


@dataclass(frozen=True)
class Alt:
    """`(any)`: an ordered alternation."""

    items: tuple

    def references(self):
        return _refs(self.items)


@dataclass(frozen=True)
class Star:
    """`(***)`: zero or more."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Plus:
    """`(+++)`: one or more."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Opt:
    """`(???)`: optional (zero or one)."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Rep:
    """`({N})`: exactly `count` times, where `count` is an expression (a literal or a parameter)."""

    count: object
    item: object

    def references(self):
        return _refs(self.count, self.item)


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

    def references(self):
        return _refs(self.full, self.trim)


@dataclass(frozen=True)
class ConsumeSpan:
    """
    A maximal run of `set` characters, consumed in one scan — what a `Star` over a character class becomes in the
    canonical form, mapping to a single repeated-char-set match. Matches the empty string; one that stands for a `Plus`
    sits behind a gate peeking `set`, which is what proves it takes at least one.
    """

    set: object

    def references(self):
        return _refs(self.set)


@dataclass(frozen=True)
class ConsumeChar:
    """
    The one character the gate peeked, taken into the run. It consumes exactly one, always: the gate has already found
    it there, so a `ConsumeChar` that finds nothing is a gate that did not do its job, and the interpreter says so
    rather than matching nothing. The generated parser carries the same assertion.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class LiteralPeek:
    """
    A gate's literal form: the alternative is entered where the input begins with `text` and the first character after
    it passes the follow test — `then`, a class it must belong to, or `barrier`, a class it must not; at most one is
    given, and the end of the input passes either for free, being no character at all. Both `None` where the literal
    alone decides. The polarity is each literal's own: the document markers are followed by white or a break —
    `c-forbidden`'s trailing class, spelled positively — where a directive keyword must not go on as a name. Tested
    without consuming, as every gate part is, and bounded by the longest literal the grammar holds, it lowers to the
    generated parser's single comparison — where a per-character split would spend a state on each.
    """

    text: tuple
    then: object
    barrier: object

    def references(self):
        return _refs(self.then, self.barrier)


@dataclass(frozen=True)
class ConsumePeeked:
    """
    The literal the gate's `LiteralPeek` found, taken into the run. It consumes the literal's characters, always: the
    gate has already found them there, so finding otherwise is a gate that did not do its job — the interpreter says so,
    and the generated parser advances without scanning the bytes a second time. `ConsumeLiteral` stays the
    test-and-consume for a literal no gate vouches for.
    """

    text: tuple

    def references(self):
        return []


@dataclass(frozen=True)
class Gate:
    """
    What an alternative is entered on, tested without consuming: `peek`, the character class the next character must
    belong to — or a `LiteralPeek`, the bounded run the input must begin — or `None` where the alternative is not
    decided by one; and `guards`, the zero-width conditions that must hold with it. A gate with neither is the
    unconditional fallthrough, which only the last alternative may carry.
    """

    peek: object = None
    guards: tuple = ()

    def references(self):
        return _refs(self.peek, self.guards)


@dataclass(frozen=True)
class Alternative:
    """
    One way a production may go: a `Gate` to enter on, the `actions` it performs, and up to two productions it hands
    control to. `first` is the call and `second` the continuation — run `first`, and when it returns resume at `second`
    — so an edge is one push. `second` alone is a tail call; neither is a return. Nothing follows `second`, which is why
    a sequence's trailing actions become a continuation of their own. `recover` rides the push: where it names a
    recovery production, a cut unwinding out of `first` stops at it — the error is emitted, the markers `first` opened
    are closed down to here, `recover` matches what this rule gives up, and the parse resumes where `first` would have
    returned as though it had matched. A recovery that does not match sends the cut on up.
    """

    gate: object
    actions: tuple = ()
    first: object = None
    second: object = None
    recover: object = None

    def references(self):
        return _refs(self.gate, self.actions, self.first, self.second, self.recover)


@dataclass(frozen=True)
class Choice:
    """
    A production's body as the state machine reads it: its `alternatives` in order, the first whose gate holds being the
    one taken. It replaces `Alt` where a body has been shaped, so a choice that is canonical is never mistaken for one
    that is not.
    """

    alternatives: tuple

    def references(self):
        return _refs(self.alternatives)


@dataclass(frozen=True)
class ConsumeLiteral:
    """
    A fixed sequence of characters, matched in one go and all or nothing — `---`, `...`, a directive's `YAML` or `TAG`.
    What a run of literal characters in a sequence becomes in the canonical form: one comparison of a few bytes rather
    than a state per character.
    """

    text: tuple  # the codepoints, in order

    def references(self):
        return []


@dataclass(frozen=True)
class ConsumeCountedSpan:
    """
    Exactly `count` characters of `set`, consumed in one scan, matching nothing if fewer are there — what a `({N})`
    repetition of a character class becomes in the canonical form. `count` is a literal where the grammar fixes it (an
    escape's two, four or eight hex digits) or a parameter where the input does (`s-indent`'s `n` spaces), so one scan
    that counts serves both.
    """

    set: object
    count: object

    def references(self):
        return _refs(self.set, self.count)


@dataclass(frozen=True)
class ConsumeTrimmedSpan:
    """
    A maximal run of `full` characters whose trailing run of `trim` is given back, consumed in one scan — what a
    `TrimStar` becomes in the canonical form, the two-set trimming scan a plain or quoted scalar's line compiles to.
    Matches the empty string.
    """

    full: object
    trim: object

    def references(self):
        return _refs(self.full, self.trim)


@dataclass(frozen=True)
class Look:
    """`(===)`: positive lookahead (zero-width)."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class NegLook:
    """`(!==)`: negative lookahead (zero-width)."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class LookBehind:
    """`(<==)`: positive look-behind (the preceding input matched `item`)."""

    item: object

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Diff:
    """`(---)`: character-class subtraction — `base` but none of `minus`."""

    base: object
    minus: tuple

    def references(self):
        return _refs(self.base, self.minus)


@dataclass(frozen=True)
class ExcludeAt:
    """`(exclude)`: a zero-width negative guard (the current position is not at `item`)."""

    item: object

    def references(self):
        return _refs(self.item)


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

    def references(self):
        return _refs(self.limit, self.item)  # `message` is a message key, not a production


@dataclass(frozen=True)
class Lt:
    """`(<)`: assert the first expression is less than the second."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)


@dataclass(frozen=True)
class Le:
    """`(<=)`: assert the first expression is less than or equal to the second."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)


@dataclass(frozen=True)
class Case:
    """
    `(case)`: dispatch on a parameter's value, each branch a grammar node, `default` the `else` for a value no branch
    names — `None` where there is none, and then a value with no branch is a path that does not match.
    """

    var: str
    branches: tuple  # (Branch, ...), each holding a grammar node
    default: object = None

    def references(self):
        return _refs(self.branches, self.default)


@dataclass(frozen=True)
class Bind:
    """`(if)` + `(set)`: match `cond`, binding parameter `param` to `value` as a side effect."""

    cond: object
    param: str
    value: object

    def references(self):
        return _refs(self.cond, self.value)


@dataclass(frozen=True)
class SetVar:
    """`(set)` standalone: bind parameter `param` to `value` with no matching (a zero-width action)."""

    param: str
    value: object

    def references(self):
        return _refs(self.value)


@dataclass(frozen=True)
class ClearVar:
    """
    A zero-width action that says `param` stops applying here: past it, nothing holds a value for it, and reading one is
    a fault rather than whatever was left behind.

    Where a construct that measures `param` returns to a caller with none of its own, the value has no further meaning —
    and a refusal is what says so, where leaving it standing would let a later read take a measurement of something that
    has ended and no gate would see it happen.
    """

    param: str

    def references(self):
        return []


@dataclass(frozen=True)
class Increase:
    """
    `(increase)`: increase indentation parameter `param` to the current column — `param = max(param, column)` — a
    zero-width action. It records the widest indentation seen so far, which is how a block scalar's leading empty lines
    set the floor its first content line may not fall below.
    """

    param: str

    def references(self):
        return []


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

    def references(self):
        return _refs(self.item)


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

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Emit:
    """`(emit)`: a zero-width token at this point, which also cuts the run of characters around it."""

    code: str

    def references(self):
        return []


@dataclass(frozen=True)
class PushIndent:
    """
    A zero-width action that pushes `level` onto the stack as the indentation the characters after it are measured
    against. It comes off where the call it was pushed for is done with it, which is the production that call carries on
    at.
    """

    level: object

    def references(self):
        return _refs(self.level)


@dataclass(frozen=True)
class PopIndent:
    """
    A zero-width action that takes the indentation in force off the stack, putting back the one it displaced. It leads a
    production of its own rather than standing beside the call it answers for: nothing of an alternative runs after its
    call returns, so the push carries on at that production and the pop is what coming back means.

    `level` is what its `PushIndent` put there, written on both halves where the two are minted together and carried
    with the pop wherever it moves. It says nothing the stack does not already hold, and is not what the pop restores
    from — a pop takes off whatever is on top. It is there so that a step moving the pop, or moving something past it,
    can say which indentation the actions around it are measuring against, without a table pairing the two ends; and it
    is checked where the pop runs, so the pairing is a refusal rather than a claim. Two pops of the same level are the
    same action still, so what merges before this carries a level merges after it.
    """

    level: object

    def references(self):
        return _refs(self.level)


@dataclass(frozen=True)
class PushCode:
    """
    A zero-width action that cuts the run and sets the code its following characters carry to `code` — what a `(token)`
    opens with — pushing the code it displaces onto the stack for its own `PopCode` to take back.
    """

    code: str

    def references(self):
        return []


@dataclass(frozen=True)
class PopCode:
    """
    A zero-width action that cuts the run and takes back the code its following characters carry from the top of the
    stack — what its `PushCode` displaced. Paired with it: `Token(code, item)` lowers to `PushCode(code), item,
    PopCode`. A pop with nothing pushed is refused: the pair is what says where a code begins and ends.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class PushMessage:
    """
    A zero-width action that opens a committed region under `message` — what a `(commit)` opens with. From here to the
    `PopMessage` that closes it, the input must carry the parse through: a failure that unwinds past this point with the
    region never closed raises `message`, where one that unwinds through a closed region backtracks like any other. A
    gate is never hoisted past one — refusing entry to a region the grammar committed to must stay the error it names.
    """

    message: str

    def references(self):
        return []


@dataclass(frozen=True)
class PopMessage:
    """
    A zero-width action that closes the committed region the innermost `PushMessage` opened — reaching it is what makes
    the region's commitment kept, so a later failure backtracks through it softly. Paired with `PushMessage`:
    `Commit(message, item)` lowers to `PushMessage(message), item, PopMessage`. The pair may be cut across a minted
    helper: like a `(token)`'s code it pairs with its push on the parse's own stack, so where the halves stand is
    nothing the split has to know.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class OpenWindow:
    """
    A zero-width action that opens a `(max)` window `limit` characters wide, past which a committed consume fails the
    cut `message` names. Windows do not nest: only the outermost applies, an inner one being inside the budget the outer
    already bounds, so an open under one is counted and otherwise does nothing.
    """

    limit: object
    message: str

    def references(self):
        return _refs(self.limit)  # `message` is a message key, not a production


@dataclass(frozen=True)
class CloseWindow:
    """
    A zero-width action that closes a `(max)` window. Paired with `OpenWindow`: `Max(limit, message, item)` lowers to
    `OpenWindow(limit, message), item, CloseWindow`. The one it closes is the outermost open — the inner ones only count
    — so the window is gone exactly when the open that set it is closed.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class OpenProvisional:
    """
    A zero-width action that opens the provisional run: the tokens emitted from here on are undecided — built and held,
    none handed back — until a `CommitProvisional` resolves them. One-for-one with `ys_queue_open_run`; only one run is
    open at a time.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class MarkProvisional:
    """
    A zero-width action that marks the open run's current position, cutting it into the region before the mark and the
    region from the mark on — the side a later `RetypeProvisional` or `InjectBefore` names. One-for-one with
    `ys_queue_mark_run`; a run carries one mark at a time, and taking it again moves it, the last taken winning — how a
    line scan marks each fresh line. A mark is a parse position, not a property of any token.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class RetypeProvisional:
    """
    A zero-width action that rewrites the held tokens in `region` — `all`, `before_mark` or `after_mark` — by kind: a
    token whose characters were consumed as a line break takes `breaks`, any other takes `rest`, and a kind whose code
    is `None` keeps its own. The run stays open. One-for-one with the rewrite over `ys_queue_run`; there is no discard —
    a failed hypothesis retypes, it never drops tokens. (`breaks` rather than the runtime's `break`, which Python
    reserves.)
    """

    rest: object
    breaks: object
    region: str

    def references(self):
        return []  # `rest` and `breaks` are token codes, not productions


@dataclass(frozen=True)
class InjectBefore:
    """
    A zero-width action that puts the decided markers `codes`, in order, into the open run at `at` — its `start`, ahead
    of the whole run, or its `mark`, between the two sides. `begin-document` and the node markers ahead of a document's
    whites, `begin-pair` at the mark where a line turned out to be a key, `end-scalar` ahead of a block scalar's chomped
    empty lines. One-for-one with `ys_queue_inject`.
    """

    codes: tuple
    at: str

    def references(self):
        return []  # `codes` are token codes, not productions


@dataclass(frozen=True)
class CommitProvisional:
    """
    A zero-width action that resolves the open run: its tokens are decided and may be handed back. One-for-one with
    `ys_queue_resolve_run`. Paired with `OpenProvisional` dynamically, as a committed region's push and pop are — the
    run is the queue's, not any one call's, so the pair may be cut across productions.
    """

    def references(self):
        return []


@dataclass(frozen=True)
class Cut:
    """
    `(cut)`: a zero-width commit past which the parse does not backtrack; on a later failure it is the error, and

    `message` names the expectation to report — a key into `grammar/messages.yaml`.
    """

    message: str

    def references(self):
        return []


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

    def references(self):
        return _refs(self.item)


@dataclass(frozen=True)
class Error:
    """
    `(error)`: a zero-width error token at this point, which also cuts the run of characters around it.

    `message` names the expectation to report — a key into `grammar/messages.yaml`, as a `(cut)`'s does. Unlike a
    `(cut)` it is a match rather than a commit: it emits and succeeds, so what the parser does about the input from here
    is the grammar's to say, in the rule that holds it.
    """

    message: str

    def references(self):
        return []


@dataclass(frozen=True)
class Recover:
    """
    `(recover)`: where a `(cut)` inside `item` stops unwinding, when `recovery` says it stops here.

    A cut unwinds past every call between it and whatever will answer for it. This is a rule saying "that is me": the
    error is emitted, the markers `item` opened are closed down to this point and no further, and `recovery` matches
    whatever of the input this rule is willing to give up — after which the parse carries on from here as though `item`
    had matched, so a repetition around it takes its next turn.

    `recovery` decides whether that happens at all: a rule reached under a resume policy that does not recover here has
    no branch to take, so it does not match, and the cut goes on unwinding to whoever does answer for it. That is what
    keeps a policy that recovers elsewhere from noticing this rule exists.
    """

    recovery: object
    item: object

    def references(self):
        return _refs(self.recovery, self.item)


@dataclass(frozen=True)
class Prod:
    """A named production: its spec number, name, parameter list, and body node."""

    number: int
    name: str
    params: tuple
    body: object

    def references(self):
        return _refs(self.body)


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
