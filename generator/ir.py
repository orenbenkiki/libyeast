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

import time
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

# The parameters that are one value for the parse rather than one per call: what `read-global-f` and `read-global-m`
# take off every declaration and every call, leaving the reads and the writes to reach the single slot. A global is what
# does not nest — the auto-detected indent is measured by the construct that opens and read while it stands, the block
# scalar's floor by its leading empty lines and read by its first content line, one construct at a time — and `clear-f`
# and `clear-m` are what say where each stops applying, so a read past its region is refused rather than answered from
# what the last one left. In alphabetical order.
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


def is_one_char(node, grammar, seen=frozenset()):
    """
    Whether `node` matches exactly one character — a terminal char class.

    It is what tells a scan from a way, which both the interpreter and the normalizer must agree on: a run over a
    character class is a value the input decides, taken whole and judged whole, and a run over anything else is a way
    the parse chooses. A `Char`, `Range` or `Invalid` is one; a `Diff` is one when its base is (the exclusions only
    narrow it); an `Alt` is one when every branch is (a union of char sets), so a lowered optional `x | <empty>` is not
    one; a `Ref` is one when its production is.

    Every other kind that reaches here is named as not one, and a kind named nowhere raises: `_IS_ONE_CHAR` is a
    `Reading`, so it answers only for what it has been told about and the corpus proves every answer is reached. A
    silent `False` here would turn a scan into a way, and the run would gain an empty fallback nobody wrote.
    """
    return _IS_ONE_CHAR(node, grammar, seen)


class Reading:
    """
    A total dispatch over node kinds: what to do for each, with nothing left to a default.

    The mechanism every question about a node is asked through, because the alternative — a chain of `isinstance` tests
    ending in a fallthrough — answers permissively for whatever spelling its author did not think of, and reports its
    own blindness as a fact about the grammar. Three rules hold a reading to what it claims:

    - **A kind it was not told about raises**, naming the reading and the kind. There is no default and no way to write
      one: a kind absent from the table is one the reading has never been asked to answer for, and answering anyway is
      the whole of the failure this replaces.
    - **Every handler is used.** A kind whose handler nothing ever reaches is a guess about the grammar, and
      `unexercised` reports it once the whole corpus has run — only then, since a kind is exercised by the inputs that
      reach it and a partial run says nothing about the rest.

    Those two between them pin the table to exactly the kinds that occur: what is missing raises, what is spare is
    reported. So a reading says nothing about kinds it cannot see, and a kind added to the IR touches only the readings
    that actually meet it — on the day they do, loudly, rather than never.

    Two lists say what has never arrived, and they differ in whether there is an answer for it. Intents come first: what
    separates one answer from another is a real property of the kinds, and a reading only makes use of it — so a reading
    names the family it means, which is a statement about the kinds themselves, and then says which part of that family
    it has not met.

    - `untested` — a family names the kind and so answers for it, and nothing has ever put that answer to the test. One
      that arrives is answered, the family's word being a reasonable one to take, and is recorded as arrived: the run
      reports it and fails, so the decision is made rather than passed over. Either the family's answer is right for it
      and the kind comes off the list, or it is wrong — and then the family was the wrong thing to say here, and what is
      needed is a finer one that tells the two apart.
    - `unknown` — nothing here answers for the kind at all, whatever a family would have said. One that arrives raises
      where it stands, there being no answer to take.

    Both are measurements rather than claims: they say what has happened, not what cannot.

    - **No kind is named twice.** Named groups overlap — `Cut` is a guard and a commit both — and a chain of tests
      resolves that silently by its order, with nothing saying which resolution was meant. Here it is an error until
      someone writes the answer down.

    Handlers take the node and whatever the caller threads through, and a reading is called the same way: `reading(node,
    grammar, ways)` reaches `handler(node, grammar, ways)`. A handler that is not callable is the answer itself —
    `Empty: True` rather than a lambda ignoring what it is given — and, being the one value every call gets back, it
    should be one nothing mutates.
    """

    _all = []

    def __init__(self, what, over, untested=(), unknown=()):
        self.what = what
        self._by_kind = {}
        for kinds, handler in over.items():
            for kind in kinds if isinstance(kinds, tuple) else (kinds,):
                if kind in self._by_kind:
                    raise TypeError(f"the reading of {what} names {kind.__name__} twice")
                self._by_kind[kind] = handler
        self._untested = frozenset(untested)
        self._unknown = frozenset(unknown)
        both = sorted(kind.__name__ for kind in self._untested & self._unknown)
        if both:
            raise TypeError(f"the reading of {what} calls {', '.join(both)} both untested and unknown")
        # An untested kind is one a family answers for, so there has to be a family that names it: without one there is
        # no answer to call untested, and what is meant is that nothing is known of it.
        idle = sorted(kind.__name__ for kind in self._untested if kind not in self._by_kind)
        if idle:
            raise TypeError(f"the reading of {what} calls {', '.join(idle)} untested, which no group of it names")
        for kind in self._unknown:
            self._by_kind.pop(kind, None)  # nothing is known of it, whatever a family would have said
        stray = sorted(kind.__name__ for kind in (*self._by_kind, *self._untested, *self._unknown) if kind not in KINDS)
        if stray:
            raise TypeError(f"the reading of {what} names {', '.join(stray)}, which are no kinds of node")
        self._used = set()
        self._arrived = set()  # the untested kinds that have turned up, each one a decision now owed
        Reading._all.append(self)

    def __call__(self, node, *carried):
        kind = type(node)
        if kind in self._unknown:
            raise TypeError(f"the reading of {self.what} knows nothing of {kind.__name__}, and it has arrived")
        handler = self._by_kind.get(kind)
        if handler is None:
            raise TypeError(f"the reading of {self.what} has not been told what {kind.__name__} means")
        if kind in self._untested:
            self._arrived.add(kind)  # answered by its family, and that the family is right for it is what is owed
        else:
            self._used.add(kind)
        return handler(node, *carried) if callable(handler) else handler

    def unused(self):
        """The kinds this reading claims to handle and was never asked about — each one a guess nothing bore out."""
        return sorted(kind.__name__ for kind in self._by_kind if kind not in self._used | self._untested)

    def arrived(self):
        """The kinds this reading calls untested that have since arrived — each one a decision it now owes."""
        return sorted(kind.__name__ for kind in self._arrived)


# What no fixpoint over a grammar should need. Every one of them settles in a handful of rounds — each drops a call or
# moves a question, and there are finitely many of both — so a loop still going here is one that has stopped settling,
# and a run that says so beats one that never returns. The deepest measured is the sweep's refinement of duplicate
# bodies at 24, which `deepest_rounds` reports so this stays a number somebody measured rather than one somebody
# guessed.
ROUNDS = 100

# The most rounds each fixpoint has ever taken, by name. The cap above is a backstop picked to be far out of reach; what
# says how far is this, reported once a whole run has been made, so a number nobody measured can be replaced by one
# somebody did.
_DEEPEST = {}


_STARTED = time.time()


# What this process is working on, said in a line it prints. Empty in the one that shares the work out, and the item in
# each that took a share of it — a check with a dozen workers writes them all to the one stream, interleaved.
_WORKING = ""


def working(what):
    """Say what this process is working on, so the lines it prints say which work they belong to."""
    global _WORKING  # noqa: PLW0603 — there is one of these per process, which is the point of it
    _WORKING = what


def say(message):
    """
    Say where a run has got to, stamped with the clock and with how long it has been going, and flushed.

    Flushed because stdout is a pipe wherever anyone is watching — under `tee`, under a log — and Python buffers a pipe
    by the block, so an unflushed line arrives once the run is over and has nothing left to report.

    A line from a worker names the work rather than the worker: which of a dozen processes wrote it answers nothing, and
    what it was answering about is what makes the interleaved lines readable.
    """
    where = f" {_WORKING}" if _WORKING else ""
    print(f"[{time.strftime('%H:%M:%S')} {time.time() - _STARTED:6.1f}s{where}] {message}", flush=True)


def rounds(what):
    """The rounds of a fixpoint, counted and said, raising where `what` has plainly stopped settling."""
    for round in range(ROUNDS):
        _DEEPEST[what] = max(_DEEPEST.get(what, 0), round + 1)
        if round % 10 == 0:  # the first, and every tenth after it: the deepest fixpoint measured takes 24 rounds, so a
            say(
                f"        {what}: round {round + 1}"
            )  # settling one says so two or three times and a stuck one keeps on
        yield round
    raise AssertionError(f"`{what}` did not settle in {ROUNDS} rounds")


def deepest_rounds():
    """`{what: the most rounds it took}` — meaningful once a whole run has been made, as `unexercised` is."""
    return dict(sorted(_DEEPEST.items(), key=lambda held: -held[1]))


def unexercised():
    """
    `{what: [kind]}` for every reading holding a handler nothing reached — meaningful only after the whole corpus has
    run, since a kind is exercised by the inputs that reach it and a partial run says nothing about the rest.
    """
    return {reading.what: reading.unused() for reading in Reading._all if reading.unused()}


def owed():
    """
    `{what: [kind]}` for every reading whose untested part has arrived — a decision each one owes, and a run that met
    one says so and fails rather than passing on the family's word.
    """
    return {reading.what: reading.arrived() for reading in Reading._all if reading.arrived()}


def what_was_reached():
    """
    What this process has reached: the kinds each reading answered for, and how deep each fixpoint went.

    A check that shares its work out over the cores does it in forked children, and a child marks what it reached in its
    own copy of these. What it reached is still reached, so it comes back with the answers and is folded in here —
    otherwise `unexercised` would report every handler only a worker ever met.
    """
    return (
        {reading.what: {kind.__name__ for kind in reading._used} for reading in Reading._all},
        dict(_DEEPEST),
        {reading.what: {kind.__name__ for kind in reading._arrived} for reading in Reading._all},
    )


def also_reached(held):
    """Fold what another process reached into this one's, as `what_was_reached` gave it."""
    used, deepest, arrived = held
    for reading in Reading._all:
        for kind in KINDS:
            if kind.__name__ in used.get(reading.what, ()):
                reading._used.add(kind)
            if kind.__name__ in arrived.get(reading.what, ()):
                reading._arrived.add(kind)
    for what, rounds in deepest.items():
        _DEEPEST[what] = max(_DEEPEST.get(what, 0), rounds)


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


def _renamed(names, *values):
    """
    `values` with every production name they hold replaced by what `names` maps it to — the mirror of `_refs`, for the
    `renamed` methods: a node renames itself, a tuple its items, and anything else is what it was. A new value, as
    everything here is: the nodes are frozen, and an earlier stage of the pipeline still holds the old one.

    Each class writes its own `renamed` beside its own `references` rather than both deriving from a list of fields,
    because its fields mean different things — a call is not a continuation, a protected match is not the handler that
    answers for it. What holds the two together is a check rather than a shared declaration: renaming everything must
    change exactly the names `references` reports.
    """
    return tuple(
        _renamed(names, *value) if isinstance(value, tuple) else value.renamed(names) if is_dataclass(value) else value
        for value in values
    )


# --- value / parameter expressions ---


@dataclass(frozen=True)
class Param:
    """A grammar parameter: `n` (indentation), `c` (context), `m` (indent indicator), or `t` (chomping)."""

    name: str

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Lit:
    """A literal value: an int, a string (e.g. `"block-in"`), or None (the grammar's `null`)."""

    value: object

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Match:
    """
    `(match)`: the text of the open run — the token about to be emitted, which is what a rule has just matched. What a
    rule reads when it must act on that text, the characters being in hand already, with nothing remembered about where
    they began.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Indent:
    """
    The indentation in force: what the top of the stack holds, and what the characters here are measured against.

    A value read off the stack rather than a parameter passed to get here — `PushIndent` and `PopIndent` are what put it
    there and take it back, so nothing declares it and no call carries it.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class AutoDetectIndent:
    """
    `<auto-detect-indent>`: the indentation of the next line holding a character other than a space, less `n`, read
    without consuming anything and bounded by nothing.

    The official grammar's, and only ever read from it — libyeast's own grammar takes each such indentation where it
    stands and reads `Column`, so nothing here evaluates one. It stays because the vendored grammar `check_vendor_spec`
    compares against spells it, and one reader loads them both.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Column:
    """
    `<column>`: the column the parse stands at, counted from zero.

    What a construct is indented by, read where its indentation has just been consumed rather than worked out by looking
    ahead for it: the run of spaces a line begins with leaves the parse at the column that run measures.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Add:
    """`(+)`: integer addition of two expressions."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)

    def renamed(self, names):
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class Sub:
    """`(-)`: integer subtraction of two expressions."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)

    def renamed(self, names):
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class Len:
    """`(len)`: how many characters a matched string holds."""

    arg: object

    def references(self):
        return _refs(self.arg)

    def renamed(self, names):
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class Atoi:
    """`(atoi)`: the integer the decimal digits of a matched string spell."""

    arg: object

    def references(self):
        return _refs(self.arg)

    def renamed(self, names):
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class Branch:
    """One branch of a `(case)` or a `(flip)`: what to use when the parameter has this value."""

    value: str
    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Flip:
    """`(flip)`: a pure value transformer over a parameter (e.g. `in-flow` mapping one context to another)."""

    var: str
    branches: tuple  # (Branch, ...), each holding a result expression

    def references(self):
        return _refs(self.branches)

    def renamed(self, names):
        (branches,) = _renamed(names, self.branches)
        return replace(self, branches=branches)


# --- grammar nodes (matchers) ---


@dataclass(frozen=True)
class Char:
    """A single literal codepoint."""

    cp: int

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Range:
    """An inclusive codepoint range `[lo, hi]`."""

    lo: int
    hi: int

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Ref:
    """A reference to another production, passing `args` (expressions)."""

    name: str
    args: tuple = ()

    def references(self):
        return [self.name, *_refs(self.args)]

    def renamed(self, names):
        return replace(self, name=names.get(self.name, self.name), args=_renamed(names, *self.args))


@dataclass(frozen=True)
class Empty:
    """`<empty>`: the epsilon match."""

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class StartOfLine:
    """`<start-of-line>`: a zero-width assertion that the parser is at the start of a line."""

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class EndOfStream:
    """`<end-of-stream>`: a zero-width assertion that the parser is at the end of the input."""

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Invalid:
    """
    `<invalid>`: one byte that begins no valid UTF-8 sequence. It belongs to no character set, so it matches nowhere the
    grammar names a character — only the recovery rules reach for it, where a run of these is `unparsed-invalid`.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Seq:
    """`(all)`: an ordered concatenation."""

    items: tuple

    def references(self):
        return _refs(self.items)

    def renamed(self, names):
        (items,) = _renamed(names, self.items)
        return replace(self, items=items)


@dataclass(frozen=True)
class Alt:
    """`(any)`: an ordered alternation."""

    items: tuple

    def references(self):
        return _refs(self.items)

    def renamed(self, names):
        (items,) = _renamed(names, self.items)
        return replace(self, items=items)


@dataclass(frozen=True)
class Star:
    """`(***)`: zero or more."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Plus:
    """`(+++)`: one or more."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Opt:
    """`(???)`: optional (zero or one)."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Rep:
    """`({N})`: exactly `count` times, where `count` is an expression (a literal or a parameter)."""

    count: object
    item: object

    def references(self):
        return _refs(self.count, self.item)

    def renamed(self, names):
        count, item = _renamed(names, self.count, self.item)
        return replace(self, count=count, item=item)


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

    def renamed(self, names):
        full, trim = _renamed(names, self.full, self.trim)
        return replace(self, full=full, trim=trim)


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

    def renamed(self, names):
        (set,) = _renamed(names, self.set)
        return replace(self, set=set)


@dataclass(frozen=True)
class ConsumeChar:
    """
    The one character the gate peeked, taken into the run. It consumes exactly one, always: the gate has already found
    it there, so a `ConsumeChar` that finds nothing is a gate that did not do its job, and the interpreter says so
    rather than matching nothing. The generated parser carries the same assertion.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        then, barrier = _renamed(names, self.then, self.barrier)
        return replace(self, then=then, barrier=barrier)


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

    def renamed(self, names):
        return self


def _asked_in_order(guard):
    """What orders a gate's guards: the kind, then the whole of the question. Total, and the same from run to run."""
    return type(guard).__name__, repr(guard)


@dataclass(frozen=True)
class Gate:
    """
    What an alternative is entered on, tested without consuming: the `guards` that must all hold where the parse stands.
    They are a set and not a sequence — each is a question about the same position, so no order between them means
    anything, and a gate holding none is the unconditional fallthrough, which only the last alternative may carry. The
    question about the character in front is a `Look` over its class, one guard among the rest.

    Held as a tuple in one canonical order rather than as a set, so that two gates asking the same questions are the
    same gate — which is what lets the sweep merge the productions that carry them — while what the pipeline emits stays
    the same from one run to the next. A set would order its members by hash, and a guard reading a named parameter
    hashes through a string, whose hash a process picks afresh.
    """

    guards: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "guards", tuple(sorted(self.guards, key=_asked_in_order)))

    def references(self):
        return _refs(self.guards)

    def renamed(self, names):
        (guards,) = _renamed(names, self.guards)
        return replace(self, guards=guards)


@dataclass(frozen=True)
class Alternative:
    """
    One way a production may go: a `Gate` to enter on, the `actions` it performs, and up to two productions it hands
    control to. `first` is the call and `second` where the path carries on past it — so an edge is one push. `second`
    alone is a tail call. Nothing follows `second`, which is why a sequence's trailing actions become a continuation of
    their own. `recover` rides the push: where it names a recovery production, a cut unwinding out of `first` stops at
    it — the error is emitted, the markers `first` opened are closed down to here, `recover` matches what this rule
    gives up, and the parse carries on at `second` as though `first` had matched. A recovery that does not match sends
    the cut on up.
    """

    gate: object
    actions: tuple = ()
    first: object = None
    second: object = None
    recover: object = None

    def references(self):
        return _refs(self.gate, self.actions, self.first, self.second, self.recover)

    def renamed(self, names):
        gate, actions, first, second, recover = _renamed(
            names, self.gate, self.actions, self.first, self.second, self.recover
        )
        return replace(self, gate=gate, actions=actions, first=first, second=second, recover=recover)


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

    def renamed(self, names):
        (alternatives,) = _renamed(names, self.alternatives)
        return replace(self, alternatives=alternatives)


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

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class ConsumeLimitedSpan:
    """
    Up to `limit` characters of `set`, consumed in one scan, and how many there were is what it says: a scan that fills
    the limit and one that falls short both match, and `DidConsumeFullLimitedSpan` is what tells them apart.

    An action that cannot fail, which is what a counted scan cannot be: a gate speaks for the character in front of it
    and not for `limit` of them, so a scan asked for a count it cannot reach is a way failing on what it performs. Here
    the taking always matches and the question about it is a guard like any other, asked past the action it is about.
    """

    set: object
    limit: object

    def references(self):
        return _refs(self.set, self.limit)

    def renamed(self, names):
        set, limit = _renamed(names, self.set, self.limit)
        return replace(self, set=set, limit=limit)


@dataclass(frozen=True)
class DidConsumeFullLimitedSpan:
    """
    Whether the `ConsumeLimitedSpan` just performed took its whole limit.

    It asks about the action in front of it and nothing else, so it stands where that action left the parse: every other
    action takes the answer away, and asking with none there raises rather than being answered. A guard that reads what
    an action did is how a taking that cannot fail still says no — the refusal moved out of the action and into a
    question about it.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        full, trim = _renamed(names, self.full, self.trim)
        return replace(self, full=full, trim=trim)


@dataclass(frozen=True)
class Look:
    """`(===)`: positive lookahead (zero-width)."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class NegLook:
    """`(!==)`: negative lookahead (zero-width)."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class LookBehind:
    """`(<==)`: positive look-behind (the preceding input matched `item`)."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Diff:
    """`(---)`: character-class subtraction — `base` but none of `minus`."""

    base: object
    minus: tuple

    def references(self):
        return _refs(self.base, self.minus)

    def renamed(self, names):
        base, minus = _renamed(names, self.base, self.minus)
        return replace(self, base=base, minus=minus)


@dataclass(frozen=True)
class ExcludeAt:
    """`(exclude)`: a zero-width negative guard (the current position is not at `item`)."""

    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


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

    def renamed(self, names):
        limit, item = _renamed(names, self.limit, self.item)
        return replace(self, limit=limit, item=item)


@dataclass(frozen=True)
class ColumnLt:
    """`(<)`: assert the first indentation is less than the second."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)

    def renamed(self, names):
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class ColumnLe:
    """`(<=)`: assert the first indentation is less than or equal to the second."""

    a: object
    b: object

    def references(self):
        return _refs(self.a, self.b)

    def renamed(self, names):
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


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

    def renamed(self, names):
        branches, default = _renamed(names, self.branches, self.default)
        return replace(self, branches=branches, default=default)


@dataclass(frozen=True)
class Bind:
    """`(if)` + `(set)`: match `cond`, binding parameter `param` to `value` as a side effect."""

    cond: object
    param: str
    value: object

    def references(self):
        return _refs(self.cond, self.value)

    def renamed(self, names):
        cond, value = _renamed(names, self.cond, self.value)
        return replace(self, cond=cond, value=value)


@dataclass(frozen=True)
class SetVar:
    """`(set)` standalone: bind parameter `param` to `value` with no matching (a zero-width action)."""

    param: str
    value: object

    def references(self):
        return _refs(self.value)

    def renamed(self, names):
        (value,) = _renamed(names, self.value)
        return replace(self, value=value)


@dataclass(frozen=True)
class ClearVar:
    """
    A zero-width action that says `param` stops applying here: past it, nothing holds a value for it, and reading one is
    a fault rather than whatever was left behind.

    Where a construct that measures `param` is done and the path carries on where none of its own is in force, the value
    has no further meaning — and a refusal is what says so, where leaving it standing would let a later read take a
    measurement of something that has ended and no gate would see it happen.
    """

    param: str

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


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

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Emit:
    """`(emit)`: a zero-width token at this point, which also cuts the run of characters around it."""

    code: str

    def references(self):
        return []

    def renamed(self, names):
        return self


# The pairs, and what says which close answers which open. Each half a step writes carries `pair`: the pairs it can
# stand for, one per pair as written and both halves of that pair carrying the same. A close is held to the open
# standing on the stack by the two sharing one — the kind cannot say it, two `(token)`s being the same kind and
# different pairs, and a close that takes the wrong open of its own kind is invisible to anything that only asks what
# kind stands there. The provisional run is the one scope with no `pair` on either half: there is only ever one open, so
# its close has nothing to be told apart from.
#
# A set of them rather than one, because a merge makes halves indistinguishable: two productions alike but for which
# pair they hold are one production, and the half that survives stands for both. So a close answers an open where the
# two intersect, that being where some one pair they both stand for exists — and what a merge costs is told exactly
# there, precision falling where the grammar itself stopped telling the two apart.


@dataclass(frozen=True)
class PushIndent:
    """
    A zero-width action that pushes `level` onto the stack as the indentation the characters after it are measured
    against. It comes off where the call it was pushed for is done with it, which is the production that call carries on
    at.
    """

    level: object
    pair: frozenset

    def references(self):
        return _refs(self.level)

    def renamed(self, names):
        (level,) = _renamed(names, self.level)
        return replace(self, level=level)


@dataclass(frozen=True)
class PopIndent:
    """
    A zero-width action that takes the indentation in force off the stack, putting back the one it displaced. It leads a
    production of its own rather than standing beside the call it answers for: nothing of an alternative runs past the
    call it makes, so the way carries on at that production and the pop is the first thing done there.

    `level` is what its `PushIndent` put there, written on both halves where the two are minted together and carried
    with the pop wherever it moves. It says nothing the stack does not already hold, and is not what the pop restores
    from — a pop takes off whatever is on top. It is there so that a step moving the pop, or moving something past it,
    can say which indentation the actions around it are measuring against, without a table pairing the two ends; and it
    is checked where the pop runs, so the pairing is a refusal rather than a claim. Two pops of the same level are the
    same action still, so what merges before this carries a level merges after it.
    """

    level: object
    pair: frozenset

    def references(self):
        return _refs(self.level)

    def renamed(self, names):
        (level,) = _renamed(names, self.level)
        return replace(self, level=level)


@dataclass(frozen=True)
class SetForbidden:
    """
    A zero-width action that sets what may not match at a start of line to `item`, `None` where nothing may not.

    What an `(exclude)` becomes, at each end of what it covers: the set is one value for the parse rather than one per
    call, so this writes a slot and the write that ends a scope names what stands after it rather than taking back what
    the opening write displaced. Nothing has to be taken back — the only exclusion that opens inside another is the
    entry recovery's inside a document's, and it forbids everything the document's does and one thing more, so the inner
    value already says the outer one.
    """

    item: object = None

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class PushCode:
    """
    A zero-width action that cuts the run and sets the code its following characters carry to `code` — what a `(token)`
    opens with — pushing the code it displaces onto the stack for its own `PopCode` to take back.
    """

    code: str
    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PopCode:
    """
    A zero-width action that cuts the run and takes back the code its following characters carry from the top of the
    stack — what its `PushCode` displaced. Paired with it: `Token(code, item)` lowers to `PushCode(code), item,
    PopCode`. A pop with nothing pushed is refused: the pair is what says where a code begins and ends.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PushMessage:
    """
    A zero-width action that opens a committed region under `message` — what a `(commit)` opens with. From here to the
    `PopMessage` that closes it, the input must carry the parse through: a failure that unwinds past this point with the
    region never closed is `message`, where one that unwinds through a closed region backtracks like any other. A gate
    is never hoisted past one — refusing entry to a region the grammar committed to must stay the error it names.
    """

    message: str
    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PopMessage:
    """
    A zero-width action that closes the committed region the innermost `PushMessage` opened — reaching it is what makes
    the region's commitment kept, so a later failure backtracks through it softly. Paired with `PushMessage`:
    `Commit(message, item)` lowers to `PushMessage(message), item, PopMessage`. The pair may be cut across a minted
    helper: like a `(token)`'s code it pairs with its push on the parse's own stack, so where the halves stand is
    nothing the split has to know.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PushRecovery:
    """
    A zero-width action that says what answers for a failed `(cut)` from here on: `recovery` matches whatever of the
    input the parse gives up, and `resume` is where it carries on once that has matched.

    What the parse holds is the two together, so an unwind reads where to stop and where to go on from one place rather
    than working either out from what the abandoned parse left behind. `resume` names the way's own continuation — where
    the path carries on past the protected call — which is what makes carrying on here the same as the call having
    matched.

    A recovery that says nothing recovers nothing: a rule reached under a resume policy that does not recover here has
    no branch to take, so the cut goes on unwinding to whoever does answer for it.
    """

    recovery: object
    resume: object
    pair: frozenset

    def references(self):
        return _refs(self.recovery, self.resume)

    def renamed(self, names):
        recovery, resume = _renamed(names, self.recovery, self.resume)
        return replace(self, recovery=recovery, resume=resume)


@dataclass(frozen=True)
class PopRecovery:
    """
    A zero-width action that takes back what the innermost `PushRecovery` established, so a cut past this point unwinds
    to whatever answered before it. Paired with `PushRecovery`, and like the other pairs it holds on the parse's own
    stack rather than where it was written, so a split that cuts the two apart is nothing either half has to know.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class OpenWindow:
    """
    A zero-width action that opens a `(max)` window `limit` characters wide, past which a committed consume fails the
    cut `message` names. Windows do not nest: only the outermost applies, an inner one being inside the budget the outer
    already bounds, so an open under one is counted and otherwise does nothing.
    """

    limit: object
    message: str
    pair: frozenset

    def references(self):
        return _refs(self.limit)  # `message` is a message key, not a production

    def renamed(self, names):
        (limit,) = _renamed(names, self.limit)
        return replace(self, limit=limit)


@dataclass(frozen=True)
class CloseWindow:
    """
    A zero-width action that closes a `(max)` window. Paired with `OpenWindow`: `Max(limit, message, item)` lowers to
    `OpenWindow(limit, message), item, CloseWindow`. The one it closes is the outermost open — the inner ones only count
    — so the window is gone exactly when the open that set it is closed.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class StartMustConsume:
    """
    A zero-width action that opens a region which must take a character: where the `EndMustConsume` that closes it is
    reached with the position where this stood, the region has not matched.

    What makes a loop end, said where the grammar can see it. A turn taking no character is a turn that would repeat
    forever, so a run says of its turn that it takes one — the machine reading a loop that must make progress rather
    than comparing positions to find out. Harmless around a turn that always takes one, which is why a run says it of
    every turn rather than of the turns that need it.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class EndMustConsume:
    """
    A zero-width action that closes the region the innermost `StartMustConsume` opened, refusing it where the parse
    stands where the open did. Paired with `StartMustConsume`, on the parse's own stack as the other pairs are.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PushBackTrack:
    """
    A zero-width action that opens a region the parse gives back whole: from here to the `PopBackTrack` that closes it,
    the ways taken inside are the ways taken, and a failure past the close gives the region up rather than choosing
    among them again.

    What makes a repetition possessive, said where the grammar can see it rather than left to whoever runs it: a run
    takes its turns, and a continuation that fails fails the run entire, there being no shorter run to fall back to. A
    failure before the close backtracks like any other — what the region settles is what has closed.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class PopBackTrack:
    """
    A zero-width action that closes the region the innermost `PushBackTrack` opened, settling the ways taken inside it.
    Paired with `PushBackTrack`, and like the other pairs it holds on the parse's own stack rather than where it was
    written, so a split that cuts the two apart is nothing either half has to know.
    """

    pair: frozenset

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class OpenProvisional:
    """
    A zero-width action that opens the provisional run: the tokens emitted from here on are undecided — built and held,
    none handed back — until a `CommitProvisional` resolves them. One-for-one with `ys_queue_open_run`; only one run is
    open at a time.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class MarkProvisional:
    """
    A zero-width action that marks the open run's current position, cutting it into the region before the mark and the
    region from the mark on — the side a later `RetypeProvisional` or `InjectBefore` names. A run carries one mark at a
    time, and taking it again moves it, the last taken winning — how a line scan marks each fresh line. A mark is a
    parse position, not a property of any token.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class CommitProvisional:
    """
    A zero-width action that resolves the open run: its tokens are decided and may be handed back. One-for-one with
    `ys_queue_resolve_run`. Paired with `OpenProvisional` dynamically, as a committed region's push and pop are — the
    run is the queue's, not any one call's, so the pair may be cut across productions.
    """

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Cut:
    """
    `(cut)`: a zero-width commit past which the parse does not backtrack — on a later failure it is the error.

    `message` names the expectation to report — a key into `grammar/messages.yaml`.
    """

    message: str

    def references(self):
        return []

    def renamed(self, names):
        return self


@dataclass(frozen=True)
class Commit:
    """
    `(commit)`: match `item`, committing only to `item` being present — a `(cut)` scoped to what follows it.

    Where a `(cut)` commits the whole parse from its point on, `(commit)` commits only that `item` can match at all: if
    `item` cannot (a quoted scalar that never closes, a flow collection that never ends), `message` is the error, as a
    `(cut)`'s is. But if `item` matches and a *later* rule fails, the match backtracks like any other — the commitment
    does not reach past `item`. That is what lets a flow scalar which closed cleanly be reinterpreted when it turns out
    not to be the mapping key it was tried as, while one that never closed is still the error it should be. `message`
    keys `grammar/messages.yaml`, as a `(cut)`'s does. An implicit key that will not parse is simply not this key rather
    than an error, so where a commit is reached in a key context the grammar wraps it in a `(case) c` whose key branches
    are the bare `item` and whose `else` is the commit — the softening a switch on `c`, not the parser's.
    """

    message: str
    item: object

    def references(self):
        return _refs(self.item)

    def renamed(self, names):
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


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

    def renamed(self, names):
        return self


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

    def renamed(self, names):
        recovery, item = _renamed(names, self.recovery, self.item)
        return replace(self, recovery=recovery, item=item)


@dataclass(frozen=True)
class Prod:
    """A named production: its spec number, name, parameter list, and body node."""

    number: int
    name: str
    params: tuple
    body: object

    def references(self):
        return _refs(self.body)

    def renamed(self, names):
        (body,) = _renamed(names, self.body)
        return replace(self, body=body)


# The families of kinds, and nothing else, so that what one says can be read against what its neighbours say. A family
# is a statement about the kinds themselves — what an action is, what takes no character, what holds a match — and it is
# families that separate one answer from another wherever the grammar is read: a reading names the family it means, and
# `untested` says which part of that family it has not met. Two families with the same members are one family under two
# names, and two that differ by a kind are a question about that kind, so they are kept together where both can be seen.
# A group that says only "the kinds this one reading meets" is no family and stays where it is used.

# The nodes holding characters that are asked about and never taken. A lookaround asks where it stands — whether the set
# is in front, whether it is not, whether it stood behind — and an exclusion asks at every take that follows, saying
# what may not stand from here on. So a walk of what the grammar takes stops at one of these: what is inside is asked,
# not taken, and counting it would count characters the parse never consumes.
#
# Taking nothing is not what makes one of these — every action and every guard takes nothing too. `<end-of-stream>` asks
# about the input and is not one: it holds no characters to stop at. In alphabetical order.
ASKED_NOT_TAKEN_NODES = (ExcludeAt, LiteralPeek, Look, LookBehind, NegLook)

# What always takes at least one character where it matches, the counterpart of `TAKES_NOTHING`. A kind that reads on
# one way and not on another — a run, a repetition, a choice — is neither, and is asked about its parts instead. In
# alphabetical order.
ALWAYS_READS = (Char, CharSet, ConsumeChar, ConsumeLiteral, ConsumePeeked, Diff, Invalid, Range)

# The kinds that take characters themselves, rather than through whatever they hold. What a walk of a node's children
# must not descend into, on pain of counting the same characters twice or of counting a peek's set as a match — and, the
# same thing said forwards, what moves the parse on, so a question asked in front of one no longer speaks for where the
# parse now stands. Every spelling of taking is here, whichever phase writes it: a family narrowed to the spellings one
# phase happens to use answers a question about the other phases' by silently not counting them. In alphabetical order.
CONSUMING = (
    Char,
    CharSet,
    ConsumeChar,
    ConsumeLimitedSpan,
    ConsumeLiteral,
    ConsumePeeked,
    ConsumeSpan,
    ConsumeTrimmedSpan,
    Diff,
    Invalid,
    Range,
)

# What leaves something behind and matches wherever it is reached: it moves the parse's own state and never the
# position. In alphabetical order.
ACTIONS = (
    ClearVar,
    CloseWindow,
    CommitProvisional,
    Cut,
    Emit,
    Error,
    ExcludeAt,
    Increase,
    InjectBefore,
    MarkProvisional,
    OpenProvisional,
    OpenWindow,
    PopBackTrack,
    PopCode,
    PopIndent,
    PopMessage,
    PopRecovery,
    PushBackTrack,
    PushCode,
    PushIndent,
    PushMessage,
    PushRecovery,
    RetypeProvisional,
    SetForbidden,
    SetVar,
    StartMustConsume,
)

# A question the parse answers where it stands, taking nothing: what the input holds around it, and how the count it
# carries compares. It leaves nothing behind and may decline, which is what tells it from an action. A `(cut)` is not
# one of these — it takes nothing either, but it commits the parse rather than asking it anything, and it stands with
# the actions. In alphabetical order.
GUARDS = (
    ColumnLe,
    ColumnLt,
    DidConsumeFullLimitedSpan,
    EndMustConsume,
    EndOfStream,
    LiteralPeek,
    Look,
    LookBehind,
    NegLook,
    StartOfLine,
)

# What a way performs: everything the machine does where it stands, to the input or to the state it carries. What may
# stand in a way's actions, as against its gate, which only asks.
PERFORMED_NODES = (*ACTIONS, *CONSUMING)

# What takes no character at all: an action leaves something behind, a guard asks a question, an empty match does
# neither. `<empty>` is both an action and a guard, doing nothing and always matching, so it is named where each of them
# needs it. What stands behind one of these is what a match begins on.
TAKES_NOTHING = (*ACTIONS, *GUARDS, Empty)

# A peek: a guard that holds its question about the input as an `item` and takes nothing, whether it asks about what
# stands in front or what stands behind. `every-peek-is-a-character-set` is what these are held to. Not every guard that
# reads the input is one — `<end-of-stream>` asks whether a character is there at all and holds no question, and the
# literal form holds a run of characters rather than a set.
PEEKS = (Look, LookBehind, NegLook)

# The guards that read what stands in front of the parse: whether a character is there at all, whether it begins a
# literal, whether it falls in a set, whether it falls outside one. What stands behind is not one of these, and neither
# is where the parse is in the line or how the indentation compares.
LOOKS_AHEAD = (EndOfStream, LiteralPeek, Look, NegLook)

# What a peek holds that shapes the output rather than the question: the run's code and the markers. A lookaround is
# probed and given back, so none of it reaches the stream and none of it is part of what the peek asks. In alphabetical
# order.
PEEK_OUTPUT = (Emit, PopCode, PushCode, Token, Wrap)

# The kinds that always match exactly one character, whichever of their set that character is. The other four that can
# be one — a difference, an alternation, a call, a switch — are one only where what they hold is, which is a question
# about the grammar rather than about the kind. In alphabetical order.
ALWAYS_ONE_CHAR = (Char, CharSet, Invalid, Range)

# A match repeated: the same state entered again, as many times as the input allows or as a count fixes. A run said as
# the scan a parser makes of it is not one of these — the scan is what such a repetition is lowered *to*. In
# alphabetical order.
REPETITIONS = (Plus, Rep, Star)

# A repetition the input ends rather than a count: it takes turns until what it repeats declines, where `REPETITIONS`
# takes in the counted one as well. What the phases lower is these two, a count being a run of a length already fixed.
RUNS = (Plus, Star)

# A run of a character class said as the scan a parser makes of it, which may be asked for none at all and so forces no
# character to be there. In alphabetical order.
SCANS = (ConsumeLimitedSpan, ConsumeSpan, ConsumeTrimmedSpan)

# A node that holds what it covers rather than bracketing it with a pair — what the wrappers phase takes apart. In
# alphabetical order.
HOLDERS = (Commit, Max, Recover, Token, Wrap)

# The kinds whose item is entered where they are: a run and a repetition take their first turn there, a scope and a
# commit their content, and a lookaround tests at the position it stands at. In alphabetical order.
WALKED_UNCONSUMED = (Commit, ExcludeAt, Look, LookBehind, Max, NegLook, Plus, Recover, Rep, Star, Token, Wrap)

# What holds a match, and so cannot stand where an item does. A choice and a run become productions of their own, a
# recovery moves to the edge an alternative rides, and a binding becomes an action. In alphabetical order.
HOLDS_A_MATCH = (Alt, Bind, Recover, Seq)

# What a production's body may be, each a state the machine has: a choice of ways, a run of one, a way under a handler,
# or a way. A binding is not among them — a body that is one hides a write behind a match, which `no-bind-nodes` counts
# wherever it stands. In alphabetical order.
BODY_KINDS = (Alt, Choice, Recover, Seq)

# What an item may be: something the machine does where it stands — a call, or anything that takes no character, or
# anything that takes characters. A guard's question is the guard's own business and no item of the way, which is what
# lets a peek hold a set and an exclusion a question a bounded run of steps answers; `every-peek-is-a-character-set` and
# `every-exclusion-is-bounded` are what answer for those. Said as the two families and the call rather than as a list of
# the spellings one phase reaches: a taking left out of the list is a step the machine has, read as a shape it has no
# state for.
LEAF_ITEMS = (*TAKES_NOTHING, *CONSUMING, Ref)

# What a production may hold instead of a matcher: a value the parse works out — an indentation, a measured length, a
# parameter, a switch over one. It matches nothing, so it takes no character and reads nowhere. In alphabetical order.
VALUE_KINDS = (Add, Atoi, AutoDetectIndent, Column, Flip, Global, Indent, Len, Lit, Match, Param, Sub)

# The values with nothing inside them, which is what lets a walk of the grammar tell a value it must leave alone from a
# value holding more of the value language. In alphabetical order.
VALUE_LEAVES = (Lit, Param)

# The kinds that never match exactly one character. A repetition or a sequence takes a run rather than a character; a
# lookaround, a marker, an action and a guard take none; a value expression is no match at all; and the canonical form's
# own consumes say in their names how much they take. With the eight that can be one character it is `KINDS`, which is
# what it is here for. In alphabetical order.
NOT_ONE_CHAR = (
    Add,
    Alternative,
    Atoi,
    AutoDetectIndent,
    Bind,
    Branch,
    Choice,
    ClearVar,
    CloseWindow,
    Column,
    ColumnLe,
    ColumnLt,
    Commit,
    CommitProvisional,
    ConsumeChar,
    ConsumeLimitedSpan,
    ConsumeLiteral,
    ConsumePeeked,
    ConsumeSpan,
    ConsumeTrimmedSpan,
    Cut,
    DidConsumeFullLimitedSpan,
    Emit,
    Empty,
    EndMustConsume,
    EndOfStream,
    Error,
    ExcludeAt,
    Flip,
    Gate,
    Global,
    Increase,
    Indent,
    InjectBefore,
    Len,
    Lit,
    LiteralPeek,
    Look,
    LookBehind,
    MarkProvisional,
    Match,
    Max,
    NegLook,
    OpenProvisional,
    OpenWindow,
    Opt,
    Param,
    Plus,
    PopBackTrack,
    PopCode,
    PopIndent,
    PopMessage,
    PopRecovery,
    Prod,
    PushBackTrack,
    PushCode,
    PushIndent,
    PushMessage,
    PushRecovery,
    Recover,
    Rep,
    RetypeProvisional,
    Seq,
    SetForbidden,
    SetVar,
    Star,
    StartMustConsume,
    StartOfLine,
    Sub,
    Token,
    TrimStar,
    Wrap,
)

# Every kind there is. `NOT_ONE_CHAR` names all but the eight that can be one character, so the two between them are the
# whole of it — and a kind added to neither is one `Reading` refuses to be told about, since a table naming it would be
# naming what is no kind of node. What reads this is every net that has to tell "a kind I know, which is not this" from
# "a kind nobody has named".
KINDS = NOT_ONE_CHAR + (Alt, Case, Char, CharSet, Diff, Invalid, Range, Ref)

# What `is_one_char` answers, as the reading it is: the kinds it has been asked about and no others. A kind absent from
# here is one nothing has ever asked this of, and arriving it raises rather than being answered for — which is the whole
# of what a reading buys, and why the table is smaller than `KINDS`.
_IS_ONE_CHAR = Reading(
    "whether a node matches exactly one character",
    {
        ALWAYS_ONE_CHAR: True,
        Diff: lambda node, grammar, seen: is_one_char(node.base, grammar, seen),
        # An empty alternation matches nothing at all, so it is no character either.
        Alt: lambda node, grammar, seen: bool(node.items)
        and all(is_one_char(item, grammar, seen) for item in node.items),
        Ref: lambda node, grammar, seen: node.name in seen
        or is_one_char(grammar[node.name].body, grammar, seen | {node.name}),
        # Every kind that reaches here and is not one character: a repetition or a sequence takes a run rather than a
        # character, an action and a guard take none, a value expression is no match at all, and the canonical form's
        # own consumes are answered where they are made. Spelled out rather than taken from a wider group, since no
        # other reading shares one — what is missing raises, and what is spare `unexercised` reports.
        (
            Add,
            Alternative,
            Atoi,
            Bind,
            Choice,
            ClearVar,
            CloseWindow,
            Column,
            ColumnLe,
            ColumnLt,
            Commit,
            ConsumeChar,
            ConsumeLimitedSpan,
            ConsumePeeked,
            ConsumeSpan,
            Cut,
            DidConsumeFullLimitedSpan,
            Emit,
            Empty,
            EndMustConsume,
            EndOfStream,
            Error,
            ExcludeAt,
            Gate,
            Global,
            Increase,
            Indent,
            Len,
            LiteralPeek,
            Match,
            Max,
            OpenWindow,
            Opt,
            Plus,
            PopBackTrack,
            PopCode,
            PopIndent,
            PopMessage,
            PopRecovery,
            PushBackTrack,
            PushCode,
            PushIndent,
            PushMessage,
            PushRecovery,
            Recover,
            Rep,
            Seq,
            SetForbidden,
            SetVar,
            Star,
            StartMustConsume,
            StartOfLine,
            Sub,
            Token,
            Wrap,
        ): False,
    },
)


def repeated(node):
    """
    What `node` takes again and again, and `None` where it takes nothing more than once.

    A run said as the scan a parser makes of it repeats nothing here: the scan is what a repetition of a character class
    is *for*, so counting it as one would make the shape it is lowered to the fault it was lowered to fix. Every other
    kind is named and one named nowhere raises, rather than being read as something that does not repeat — a repetition
    this had not heard of would go unseen, which is what `is_one_char` answering `False` by default did.
    """
    if isinstance(node, TrimStar):
        return node.full
    if isinstance(node, REPETITIONS):
        return node.item
    if isinstance(node, SCANS):
        return None  # a run already said as its scan
    if isinstance(node, KINDS):
        return None
    raise TypeError(f"cannot tell whether {type(node).__name__} repeats anything")


def rebuilt(node, visit):
    """
    `node` with every grammar node it holds replaced by `visit` of that node.

    The generic walker the module's shape exists for: it reaches every node the same way, whatever node it is — a field
    of its own or an item of a tuple of them — so a caller recurses without special-casing any node. What a node holds
    that is a value rather than grammar is left as it is.
    """
    if not is_dataclass(node):
        return node
    changed = {}
    for field in fields(node):
        value = getattr(node, field.name)
        if is_dataclass(value) and not isinstance(value, VALUE_LEAVES):
            changed[field.name] = visit(value)
        elif isinstance(value, tuple) and value and all(is_dataclass(item) for item in value):
            changed[field.name] = tuple(visit(item) for item in value)
    return replace(node, **changed) if changed else node
