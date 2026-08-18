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
    The codepoints `node` consumes, where it consumes exactly one character — else None.

    One question, asked of every kind: *what does this take from the input, and is it a single character?* Not what
    admits it — a gate is zero-width and says which ways may be entered, never what any of them takes. The one place the
    two meet is `ConsumeChar`, which takes the character its gate found, so a way built from that pair takes the set the
    gate admits.

    A kind named nowhere raises rather than denoting nothing: "no set" and "no answer" are different, and read as the
    first the second would leave a character class unlowered and unseen.
    """
    return _DENOTES(node, grammar, seen)


def _denoted_set(node, grammar, seen):
    """A set as the parser already sees it. A single interval of one character is the literal it names."""
    spans = [span for span in node.spans if span[0] >= 0]  # the invalid byte is no character and belongs to no set
    if len(spans) == 1 and spans[0][0] == spans[0][1]:
        return ("literal", spans[0][0])
    parts = tuple(("range", low, high) for low, high in spans)
    return None if not parts else parts[0] if len(parts) == 1 else ("union", parts)


def _denoted_union(node, grammar, seen):
    """An alternation's: the union of its ways, and nothing unless every one of them takes a single character."""
    parts = [denote(grammar, item, seen) for item in node.items]
    if not parts or any(part is None for part in parts):
        return None
    return parts[0] if len(parts) == 1 else ("union", tuple(parts))


def _denoted_difference(node, grammar, seen):
    """A difference's: the base less each subtracted set, and nothing unless all of them are sets."""
    base = denote(grammar, node.base, seen)
    minus = [denote(grammar, item, seen) for item in node.minus]
    if base is None or any(part is None for part in minus):
        return None
    return ("difference", base, tuple(minus))


def _denoted_call(node, grammar, seen):
    """
    A call's: what the production it names takes.

    A call passing arguments takes what its callee takes under those arguments, which the callee's body alone does not
    say; and a call reached again is a recursion, which takes more than one character or none.
    """
    if node.args or node.name not in grammar or node.name in seen:
        return None
    return denote(grammar, grammar[node.name].body, seen + (node.name,))


def _denoted_choice(node, grammar, seen):
    """A choice's: the union of what its ways take, and nothing unless every one takes a single character."""
    parts = [denote(grammar, way, seen) for way in node.alternatives]
    if not parts or any(part is None for part in parts):
        return None
    return parts[0] if len(parts) == 1 else ("union", tuple(parts))


# A scope that takes exactly what it holds and nothing of its own: an annotation says what the characters are called, a
# commit says what failing means, a `(wrap)` puts markers around them. A `(max)` is one of these where it wraps a match
# and holds nothing where it is a bare length note, so it is answered for on its own.
_TAKES_WHAT_IT_HOLDS = (ir.Commit, ir.Token, ir.Wrap)

# The guards that say which characters may stand in front: a set they must fall in, a set they must fall outside, a run
# they must spell. `<end-of-stream>` is not one — it asks whether a character is there at all and names none — so a gate
# holding one still says nothing about which character a consume beside it takes.
_SAYS_WHAT_STANDS_AHEAD = (ir.LiteralPeek, ir.Look, ir.NegLook)


def _consumed_by_a_way(node, grammar, seen):
    """
    A way's: what it performs, where that is one character.

    Its gate is not part of the answer — a guard takes nothing. The exception is `ConsumeChar`, which is defined as the
    character the gate found, so the set it takes is the set the gate admits: one `Look` and nothing else reading ahead,
    which is what `every-gate-looks-ahead-at-most-once` leaves. A way that hands control on takes what it calls. A span,
    a literal, a counted run, or two things taking at once take more than one character and name no set.
    """
    taking = [action for action in node.actions if isinstance(action, ir.CONSUMING)]
    calls = [held for held in (node.first, node.second) if held is not None]
    if len(taking) + len(calls) != 1:
        return None
    if calls:
        return denote(grammar, calls[0], seen)
    if not isinstance(taking[0], ir.ConsumeChar):
        return denote(grammar, taking[0], seen)  # a set standing among the actions names itself; a run names nothing
    ahead = [guard for guard in node.gate.guards if isinstance(guard, _SAYS_WHAT_STANDS_AHEAD)]
    if len(ahead) != 1 or not isinstance(ahead[0], ir.Look):
        return None  # nothing says which character the gate found, or it says which ones it refused
    return denote(grammar, ahead[0].item, seen)


# The three reasons a kind names no set of one character, each a different thing about it and each named here rather
# than left as a list beside its answer. Only the kinds that reach this reading are in them: what is missing raises,
# which is what says a kind nothing has asked this of has arrived.
#
# It takes a run rather than a character — as many turns as the input allows, as a count fixes, or one after another.
_TAKES_MORE_THAN_ONE = (*ir.REPETITIONS, ir.Opt, ir.Seq)
# It takes no character at all: a guard reads and gives back, an action leaves something behind, an empty match does
# neither, and a comparison of two counts asks about neither the input nor a character.
_TAKES_NONE = (
    ir.ColumnLe,
    ir.ColumnLt,
    ir.Cut,
    ir.Emit,
    ir.Empty,
    ir.EndOfStream,
    ir.Error,
    ir.ExcludeAt,
    ir.Increase,
    ir.Look,
    ir.LookBehind,
    ir.NegLook,
    ir.SetVar,
    ir.StartOfLine,
)
# It is no match at all: a value the parse works out, and the arm of a switch, which pairs a parameter's value with a
# match rather than being one.
_MATCHES_NOTHING = (
    ir.Add,
    ir.Atoi,
    ir.Branch,
    ir.Column,
    ir.Flip,
    ir.Len,
    ir.Lit,
    ir.Match,
    ir.Param,
    ir.Sub,
)


def _takes_no_single_character(node, grammar, seen):
    """The answer for every kind that takes nothing, or takes more than one character."""
    return None


# What each kind takes from the input. The groups are the reasons, and every kind is decided against the one question
# rather than answered by the group it happens to fall in. A kind absent from here has never been asked this, and it
# raises rather than being read as taking nothing.
_DENOTES = ir.Reading(
    "the codepoints a node consumes, where it consumes exactly one character",
    {
        # Takes one character, and names the set itself.
        ir.Char: lambda node, grammar, seen: ("literal", node.cp),
        ir.Range: lambda node, grammar, seen: ("range", node.lo, node.hi),
        ir.CharSet: _denoted_set,
        ir.Alt: _denoted_union,
        ir.Diff: _denoted_difference,
        ir.Ref: _denoted_call,
        ir.Choice: _denoted_choice,
        ir.Alternative: _consumed_by_a_way,
        ir.Bind: lambda node, grammar, seen: denote(grammar, node.cond, seen),
        _TAKES_WHAT_IT_HOLDS: lambda node, grammar, seen: denote(grammar, node.item, seen),
        ir.Max: lambda node, grammar, seen: (
            None if node.item is None else denote(grammar, node.item, seen)  # a bare length note holds no match
        ),
        _TAKES_MORE_THAN_ONE: _takes_no_single_character,
        _TAKES_NONE: _takes_no_single_character,
        _MATCHES_NOTHING: _takes_no_single_character,
        # A switch takes what the branch a caller settles takes, so no one set answers for it.
        ir.Case: _takes_no_single_character,
        # A recovery takes its item's where the parse carries through and its recovery's where a cut fired, which are
        # two answers rather than one.
        ir.Recover: _takes_no_single_character,
        # The byte that begins no character. It belongs to no character set, which is what lets a set say it holds one.
        ir.Invalid: _takes_no_single_character,
    },
)


def spelling_size(grammar, node, seen=()):
    """
    How many nodes it takes to spell `node`, counted through the productions it names.

    What tells a simpler spelling of a set from a longer one, where several productions denote the same characters and
    one of them is to be its name. Counting through the names is what makes it work: a bare call is a single node and
    the production it names may be a whole tree, so a size that stopped at the call would rank an alias ahead of the
    thing it aliases — `c-non-specific-tag` ahead of the `c-tag` it calls.

    A name reached again adds nothing, a recursion being no more of a spelling the second time round.

    `children` and `references` answer different questions and are each asked once, at the scope they answer for.
    `children` gives the nodes nested one level down, so counting through it reaches every node of the subtree and no
    name. `references` gives the productions the subtree names — which no walk of the fields could find, a `Ref`'s name
    and an `(emit)`'s code both being strings — so asking it once at the top reaches every name and no node. Asking both
    at every level would count a named production once per level standing above it.
    """
    total = _nested_count(node)
    for name in dict.fromkeys(node.references()):
        if name in grammar and name not in seen:
            total += spelling_size(grammar, grammar[name].body, seen + (name,))
    return total


def _nested_count(node):
    """How many nodes `node` is, itself and everything nested within it — the names it mentions left alone."""
    return 1 + sum(_nested_count(child) for child in children(node))


def simplest_name(grammar, names):
    """
    Which of `names` is the one to call a set by: the shortest spelling, then the shortest name, then the first.

    Several productions may denote the same characters — `c-tag`, and the two rules that do nothing but call it. The
    name a table cites should be the plainest of them, and plainness is measured rather than settled alphabetically.
    """
    return min(names, key=lambda name: (spelling_size(grammar, grammar[name].body), len(name), name))


def naming_productions(grammar):
    """
    `{denotation: the production that names it}` — one answer for every set the grammar denotes, single characters and
    wider sets alike.

    A set and a lone character are the same question asked of different sizes: which production is this set's name. The
    decoder's literal table wants it of the characters and its set table wants it of the rest, and both read it here so
    the two cannot answer differently.
    """
    candidates = {}
    for name, production in grammar.items():
        denotation = denote(grammar, production.body)
        if denotation is not None:
            candidates.setdefault(denotation, []).append(name)
    return {denotation: simplest_name(grammar, names) for denotation, names in candidates.items()}


def spans(denotation):
    """
    A denotation as sorted, disjoint `(low, high)` codepoint intervals — the set it names, said one way.

    A denotation says how a set was built; the intervals say which characters are in it, which is what a question about
    the set's size or its members wants. A tag named nowhere raises: a denotation nobody has worked out is not an empty
    set.
    """
    kind = denotation[0]
    if kind == "literal":
        return [(denotation[1], denotation[1])]
    if kind == "range":
        return [(denotation[1], denotation[2])]
    if kind == "union":
        return _merged_spans([span for part in denotation[1] for span in spans(part)])
    if kind == "difference":
        return _subtracted_spans(spans(denotation[1]), _merged_spans([s for p in denotation[2] for s in spans(p)]))
    raise ValueError(f"unknown denotation {denotation!r}")


def single_codepoint(denotation):
    """The one codepoint `denotation` names, or None where it names none, several, or nothing at all."""
    if denotation is None:
        return None
    intervals = spans(denotation)
    return intervals[0][0] if len(intervals) == 1 and intervals[0][0] == intervals[0][1] else None


def _merged_spans(intervals):
    """`intervals` as sorted, coalesced `(low, high)` pairs."""
    merged = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _subtracted_spans(intervals, minus):
    """`intervals` with every interval of `minus` removed."""
    for low, high in minus:
        remaining = []
        for start, stop in intervals:
            if high < start or low > stop:
                remaining.append((start, stop))
                continue
            if start < low:
                remaining.append((start, low - 1))
            if stop > high:
                remaining.append((high + 1, stop))
        intervals = remaining
    return intervals


def does_contain(denotation, codepoint):
    """Whether `denotation` contains `codepoint`."""
    kind = denotation[0]
    if kind == "literal":
        return codepoint == denotation[1]
    if kind == "range":
        return denotation[1] <= codepoint <= denotation[2]
    if kind == "union":
        return any(does_contain(part, codepoint) for part in denotation[1])
    if kind == "difference":
        return does_contain(denotation[1], codepoint) and not any(
            does_contain(part, codepoint) for part in denotation[2]
        )
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

    defined = naming_productions(grammar)
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
            if does_contain(denotation, codepoint):
                key |= self.set_mask(index)
        return key

    def sentinel(self, literal_id, length):
        """The key of a sentinel: a literal id and no set bits, so every membership test fails at it."""
        return literal_id | (length << LEN_SHIFT)
