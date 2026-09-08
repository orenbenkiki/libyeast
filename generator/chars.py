# SPDX-License-Identifier: MIT
"""
The character model the decoder rests on. `chars` derives it from the grammar IR.

The literal characters the grammar names and the distinct character sets the grammar tests are what the parser can ask
about a character. `Model.key` answers those questions at once. The key is a 32-bit word holding the character's
named-literal id, a bit per character set the character belongs to, and the UTF-8 length.

`chars` works out a union and a subtraction here. A test in the parser is then a single bit test.
"""

import dataclasses
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Literal, TypeVar

import ir

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

MAX_CODEPOINT = 0x10FFFF  # the last codepoint Unicode has. A span reaching the top stops here.

# The codepoints a node consumes. The shape names how the grammar builds the set, and `spans` says which characters that
# comes to.
_Denotation = (
    tuple[Literal["literal"], int]
    | tuple[Literal["range"], int, int]
    | tuple[Literal["union"], tuple["_Denotation", ...]]
    | tuple[Literal["difference"], "_Denotation", tuple["_Denotation", ...]]
)

# The value `gathered` takes from a node it finds. The kinds `gathered` walks for settle what it hands `of`.
_Taken = TypeVar("_Taken", bound="SupportsRichComparison")

# The key's layout. The parser tests it with `key & YS_SET_*` and `YS_LIT(key) == YS_LIT_*`. The fields must not move
# without the generated tables moving with them.
LIT_BITS = 6  # the width of the named-literal id. The field starts at the lowest bit.
SET_SHIFT = 6  # the bit the set bits start at. The key holds a bit per tested set from there up.
LEN_SHIFT = 26  # the bit the UTF-8 length starts at.

_LIT_NONE = 0  # not a character the grammar names. The named characters take the ids above this.


def denote(grammar: Mapping[str, ir.Prod], node: ir.Node, seen: tuple[str, ...] = ()) -> _Denotation | None:
    """
    The codepoints `node` consumes, where it consumes a single character. `None` otherwise.

    The question asks the kinds what this takes from the input, and whether that is a single character. The question
    does not ask what admits it. A gate takes no width and says which ways a parse may enter, rather than what a way
    takes.

    `ConsumeCharAction` joins the pair. The action takes the character its gate found. A way built from that pair takes
    the set the gate admits.

    A kind `_DENOTES` leaves out raises rather than denoting nothing. "No set" and "no answer" differ. Reading the
    second as the first would leave a character class unlowered and unseen.
    """
    return _DENOTES(node, grammar, seen)


def _denoted_set(node: ir.CharSet, _grammar: Mapping[str, ir.Prod], _seen: tuple[str, ...]) -> _Denotation | None:
    """A set as the parser already sees it. A single interval spanning a single character is the literal it names."""
    intervals = [span for span in node.spans if span[0] >= 0]  # the invalid byte is no character and belongs to no set
    if len(intervals) == 1 and intervals[0][0] == intervals[0][1]:
        return ("literal", intervals[0][0])
    parts: tuple[_Denotation, ...] = tuple(("range", low, high) for low, high in intervals)
    return None if not parts else parts[0] if len(parts) == 1 else ("union", parts)


def _denoted_union(node: ir.AltTree, grammar: Mapping[str, ir.Prod], seen: tuple[str, ...]) -> _Denotation | None:
    """
    An alternation's codepoints. The union of its ways, where the ways take a single character apiece. A wider way
    leaves the alternation without a denotation.
    """
    parts: list[_Denotation] = []
    for item in node.items:
        part = denote(grammar, item, seen)
        if part is None:
            return None
        parts.append(part)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else ("union", tuple(parts))


def _denoted_difference(node: ir.DiffSet, grammar: Mapping[str, ir.Prod], seen: tuple[str, ...]) -> _Denotation | None:
    """
    A difference's codepoints. The base less the sets it subtracts. A subtracted item that is no set leaves the
    difference without a denotation.
    """
    base = denote(grammar, node.base, seen)
    if base is None:
        return None
    minus: list[_Denotation] = []
    for item in node.minus:
        part = denote(grammar, item, seen)
        if part is None:
            return None
        minus.append(part)
    return ("difference", base, tuple(minus))


def _denoted_call(node: ir.RefCall, grammar: Mapping[str, ir.Prod], seen: tuple[str, ...]) -> _Denotation | None:
    """
    A call's codepoints. The production the call names decides them.

    A call passing arguments takes what its callee takes under those arguments. The callee's body does not say that
    without them. A call the walk reaches again is a recursion. A recursion takes more than a single character, or it
    takes none.
    """
    if node.args or node.name not in grammar or node.name in seen:
        return None
    return denote(grammar, grammar[node.name].body, seen + (node.name,))


def _denoted_choice(node: ir.ChoiceState, grammar: Mapping[str, ir.Prod], seen: tuple[str, ...]) -> _Denotation | None:
    """
    A choice's codepoints. The union of what the ways take, where a way takes a single character apiece. A wider way
    leaves the choice without a denotation.
    """
    parts: list[_Denotation] = []
    for way in node.alternatives:
        part = denote(grammar, way, seen)
        if part is None:
            return None
        parts.append(part)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else ("union", tuple(parts))


# A scope taking exactly what it holds. An annotation names the characters. A commit says what failing means, and a
# `(wrap)` puts markers around the match.
#
# A `(max)` is not here. A `(max)` wrapping a match takes what it holds. A bare length note takes nothing. `_DENOTES`
# gives `(max)` an entry of its own.
_TAKES_WHAT_IT_HOLDS = (ir.CommitWrapper, ir.TokenWrapper, ir.Wrapper)


def _consumed_by_a_way(
    node: ir.AlternativeState, grammar: Mapping[str, ir.Prod], seen: tuple[str, ...]
) -> _Denotation | None:
    """
    A way's codepoints. The action the way performs, where that action takes a single character.

    The way's gate is no part of the answer. A guard takes nothing, and a consume names the set it takes. The guard in
    front settles nothing here.

    A way that hands control on takes what it calls. A span, a literal or a counted run takes a longer stretch and names
    no set. A pair of things taking at once does the same.
    """
    taking = [action for action in node.actions if isinstance(action, ir.CONSUMING)]
    calls = [held for held in (node.first, node.second) if held is not None]
    if len(taking) + len(calls) != 1:
        return None
    if calls:
        return denote(grammar, calls[0], seen)
    # A set standing among the actions names itself; a consume names its `set`; a run names nothing.
    taken = taking[0].set if isinstance(taking[0], ir.ConsumeCharAction) else taking[0]
    return denote(grammar, taken, seen)


# Why a kind names no set of a single character. The groups below state the reasons rather than a list beside the
# answer. The groups hold the kinds that reach this question. A missing kind raises. The raise says a kind nobody has
# asked this of has arrived.
#
# This group takes a run rather than a character. The run turns as often as the input allows, as often as a count fixes,
# or item after item.
_TAKES_MORE_THAN_ONE = (*ir.REPETITIONS, ir.OptTree, ir.SeqTree)
# A kind here takes no character at all. A guard reads and gives back, and an action leaves something behind. An empty
# match does neither. A failing match takes nothing. The parse does not make it. A comparison of counts asks about
# neither the input nor a character.
_TAKES_NONE = (
    ir.IsLessEqualGuard,
    ir.IsLessThanGuard,
    ir.CutAction,
    ir.EmitAction,
    ir.EmptyTree,
    ir.EndOfStreamGuard,
    ir.ErrorAction,
    ir.FailTree,
    ir.ExcludeAtAction,
    ir.IncreaseAction,
    ir.LookGuard,
    ir.LookBehindGuard,
    ir.NegLookGuard,
    ir.SetVarAction,
    ir.StartOfLineGuard,
)
# A kind here is no match at all. A value the parse works out, and the arm of a switch. The arm pairs a parameter's
# value with a match. The arm is no match itself.
_MATCHES_NOTHING = (
    ir.AddValue,
    ir.AtoiValue,
    ir.BranchPart,
    ir.ColumnValue,
    ir.FlipValue,
    ir.LenValue,
    ir.LitValue,
    ir.MatchValue,
    ir.ParamValue,
    ir.SubValue,
)


def _takes_no_single_character(_node: ir.Node, _grammar: Mapping[str, ir.Prod], _seen: tuple[str, ...]) -> None:
    """The answer for a kind that takes nothing, or takes more than a single character."""
    return None


# The groups state the reasons. `_DENOTES` decides a kind against the question rather than by the group it falls in. A
# kind absent from here raises. Nobody has asked this of that kind. Reading the kind as taking nothing would hide that.
_DENOTES: ir.Question[_Denotation | None] = ir.Question(
    "the codepoints that a node consumes as a denotation - `('literal', cp)`, `('range', lo, hi)`, "
    "`('union', parts)` or `('difference', base, minus)` - and `None` for a node consuming other than a single "
    "character",
    {
        # Takes a single character, and names the set itself.
        ir.OneCharSet: lambda node, grammar, seen: ("literal", node.cp),
        ir.RangeSet: lambda node, grammar, seen: ("range", node.lo, node.hi),
        ir.CharSet: _denoted_set,
        ir.AltTree: _denoted_union,
        ir.DiffSet: _denoted_difference,
        ir.RefCall: _denoted_call,
        ir.ChoiceState: _denoted_choice,
        ir.AlternativeState: _consumed_by_a_way,
        ir.BindTree: lambda node, grammar, seen: denote(grammar, node.cond, seen),
        _TAKES_WHAT_IT_HOLDS: lambda node, grammar, seen: denote(grammar, node.item, seen),
        ir.MaxWrapper: lambda node, grammar, seen: (
            None if node.item is None else denote(grammar, node.item, seen)  # a bare length note holds no match.
        ),
        _TAKES_MORE_THAN_ONE: _takes_no_single_character,
        _TAKES_NONE: _takes_no_single_character,
        _MATCHES_NOTHING: _takes_no_single_character,
        # A switch takes what the branch a caller settles takes. A single set cannot answer for it.
        ir.CaseTree: _takes_no_single_character,
        # A recovery takes what its item takes where the parse gets through. A recovery takes what its recovery takes
        # where a cut fired. Those are separate answers.
        ir.RecoverWrapper: _takes_no_single_character,
        # The byte that begins no character. The byte belongs to no character set of its own. The interval `(-1, -1)` is
        # how a set says it holds the byte.
        ir.InvalidSet: _takes_no_single_character,
    },
)


def _form_size(grammar: Mapping[str, ir.Prod], node: ir.Node, seen: tuple[str, ...] = ()) -> int:
    """
    The number of nodes a form of `node` takes, counted through the productions `node` names.

    The size tells a simpler form of a set from a longer form. A pair of productions may denote the same characters. The
    simpler form gives the set its name.

    Counting through the names makes that work. A bare call is a single node, and the production it names may be a whole
    tree. A size stopping at the call would rank an alias ahead of what the alias calls. It would put
    `c-non-specific-tag` ahead of `c-tag`.

    A name the walk reaches again adds nothing. A recursion writes no more the second time round.

    `children` and `references` answer different questions. A question comes once, at the scope where it answers.
    `children` gives the nodes nested a level down. Counting through `children` reaches the nodes of the subtree and no
    name.

    `references` gives the productions the subtree names. A walk of the fields could not find them. A `RefCall`'s name
    and an `(emit)`'s code are both strings. Asking `references` once at the top reaches the names and no node. Asking
    both further down would count a named production once per level above it.
    """
    total = _nested_count(node)
    for name in dict.fromkeys(node.references()):
        if name in grammar and name not in seen:
            total += _form_size(grammar, grammar[name].body, seen + (name,))
    return total


def _nested_count(node: ir.Node) -> int:
    """
    The number of nodes `node` is. The count holds `node` itself and the nodes nested within it, and leaves the names
    `node` mentions out.
    """
    return 1 + sum(_nested_count(child) for child in children(node))


def simplest_name(grammar: Mapping[str, ir.Prod], names: Iterable[str]) -> str:
    """
    The name in `names` to call a set by. The shortest form, then the shortest name, then the first.

    A pair of productions may denote the same characters, such as `c-tag` and the rules that just call it. A table
    should cite the plainest of them. `_form_size` measures plainness rather than settling it alphabetically.
    """
    return min(names, key=lambda name: (_form_size(grammar, grammar[name].body), len(name), name))


def naming_productions(grammar: Mapping[str, ir.Prod]) -> dict[_Denotation, str]:
    """
    `{denotation: the production that names it}`. A single answer per set the grammar denotes, single characters and
    wider sets alike.

    A set and a lone character are the same question asked of different sizes. A single production names the set.

    The decoder's literal table asks that of a character, and the set table asks it of a wider set. Both read the answer
    here, and the tables cannot answer differently.
    """
    candidates: dict[_Denotation, list[str]] = {}
    for name, production in grammar.items():
        denotation = denote(grammar, production.body)
        if denotation is not None:
            candidates.setdefault(denotation, []).append(name)
    return {denotation: simplest_name(grammar, names) for denotation, names in candidates.items()}


def spans(denotation: _Denotation) -> list[tuple[int, int]]:
    """
    A denotation as sorted and disjoint `(low, high)` codepoint intervals. The intervals say which characters the set
    holds.

    A denotation says the shape the grammar built the set from. The intervals answer a question about the set's size or
    its members.

    An unknown tag raises. A denotation nobody has worked out is no empty set.
    """
    if denotation[0] == "literal":
        return [(denotation[1], denotation[1])]
    if denotation[0] == "range":
        return [(denotation[1], denotation[2])]
    if denotation[0] == "union":
        return merged_spans([span for part in denotation[1] for span in spans(part)])
    if denotation[0] == "difference":
        minus = merged_spans([span for part in denotation[2] for span in spans(part)])
        return subtracted_spans(spans(denotation[1]), minus)
    raise ValueError(f"unknown denotation {denotation!r}")


def single_codepoint(denotation: _Denotation | None) -> int | None:
    """The codepoint `denotation` names, where `denotation` names a lone character. `None` in any other case."""
    if denotation is None:
        return None
    intervals = spans(denotation)
    return intervals[0][0] if len(intervals) == 1 and intervals[0][0] == intervals[0][1] else None


def merged_spans(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """`intervals` as sorted, coalesced `(low, high)` pairs."""
    merged: list[tuple[int, int]] = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def intersected_spans(intervals: Sequence[tuple[int, int]], others: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    The intervals `intervals` and `others` both hold, as sorted disjoint pairs. The caller passes both sorted and
    disjoint.
    """
    kept, here, there = [], 0, 0
    while here < len(intervals) and there < len(others):
        low = max(intervals[here][0], others[there][0])
        high = min(intervals[here][1], others[there][1])
        if low <= high:
            kept.append((low, high))
        if intervals[here][1] < others[there][1]:
            here += 1
        else:
            there += 1
    return kept


def subtracted_spans(intervals: Sequence[tuple[int, int]], minus: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """`intervals` with the intervals of `minus` removed."""
    kept = list(intervals)
    for low, high in minus:
        remaining: list[tuple[int, int]] = []
        for start, stop in kept:
            if high < start or low > stop:
                remaining.append((start, stop))
                continue
            if start < low:
                remaining.append((start, low - 1))
            if stop > high:
                remaining.append((high + 1, stop))
        kept = remaining
    return kept


def does_contain(denotation: _Denotation, codepoint: int) -> bool:
    """Whether `denotation` contains `codepoint`."""
    if denotation[0] == "literal":
        return codepoint == denotation[1]
    if denotation[0] == "range":
        return denotation[1] <= codepoint <= denotation[2]
    if denotation[0] == "union":
        return any(does_contain(part, codepoint) for part in denotation[1])
    if denotation[0] == "difference":
        return does_contain(denotation[1], codepoint) and not any(
            does_contain(part, codepoint) for part in denotation[2]
        )
    raise ValueError(f"unknown denotation {denotation!r}")


def children(node: ir.Node) -> Iterator[ir.Node]:
    """The IR nodes directly nested within `node`."""
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        for item in value if isinstance(value, tuple) else (value,):
            if isinstance(item, ir.Node):
                yield item


def gathered(
    grammar: Mapping[str, ir.Prod],
    kind: type[ir.Node] | tuple[type[ir.Node], ...],
    of: Callable[..., _Taken],
) -> list[_Taken]:
    """The values `of` takes from the nodes of `kind` anywhere in `grammar`, without repeats and in order."""
    found: set[_Taken] = set()
    for production in grammar.values():
        pending = [production.body]
        while pending:
            node = pending.pop()
            if isinstance(node, kind):
                found.add(of(node))
            pending.extend(children(node))
    return sorted(found)


def _literals(grammar: Mapping[str, ir.Prod]) -> list[int]:
    """The codepoints the grammar names as a literal character. The list runs in codepoint order."""
    return gathered(grammar, ir.OneCharSet, lambda node: node.cp)


def _ranges(grammar: Mapping[str, ir.Prod]) -> list[tuple[int, int]]:
    """The codepoint ranges the grammar names, as an ordered `[(low, high)]`."""
    return gathered(grammar, ir.RangeSet, lambda node: (node.lo, node.hi))


def representatives(grammar: Mapping[str, ir.Prod]) -> list[int]:
    """
    A codepoint per segment the grammar can tell apart. The list runs in codepoint order.

    The grammar's literals and ranges build a set. A key holds across the codepoints between consecutive boundaries.
    Probing a codepoint per segment is therefore exhaustive. The probes number the segments rather than the codepoints.
    """
    boundaries = {0}
    for codepoint in _literals(grammar):
        boundaries |= {codepoint, codepoint + 1}
    for low, high in _ranges(grammar):
        boundaries |= {low, high + 1}
    return sorted(boundary for boundary in boundaries if boundary <= MAX_CODEPOINT)


def _tested_sets(grammar: Mapping[str, ir.Prod]) -> list[tuple[str, _Denotation]]:
    """
    The character sets the grammar tests, as an ordered `[(name, denotation)]`.

    A tested set is a *maximal* character node, whose parent is no character node itself. So the ranges inside
    `c-printable`'s union do not count. The parser asks about `c-printable` rather than about those ranges.

    Sets denoting the same codepoints share an entry. A set takes the name of the production defining it. A set the
    grammar tests inline takes the name of the enclosing production plus an index. `ns-plain-first` tests more than a
    single set inline.
    """
    owners: dict[_Denotation, str] = {}

    def collect(node: ir.Node, owner: str, is_inside_set: bool) -> None:
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
    named: list[tuple[str, _Denotation]] = []
    inline_counts: dict[str, int] = {}
    for denotation, owner in owners.items():
        set_name = defined.get(denotation)
        if set_name is None:
            index = inline_counts.get(owner, 0)
            inline_counts[owner] = index + 1
            set_name = f"{owner}-inline-{index}"
        named.append((set_name, denotation))
    return sorted(named)


class Model:
    """The character model. The grammar's named literals, the sets it tests, and the key of any codepoint."""

    def __init__(self, grammar: Mapping[str, ir.Prod]) -> None:
        self.literals = _literals(grammar)
        self.sets = _tested_sets(grammar)
        self.literal_ids = {codepoint: index + 1 for index, codepoint in enumerate(self.literals)}
        # The sentinels take the ids just past the named characters. They cannot collide with one.
        self.lit_eof = len(self.literals) + 1
        self.lit_invalid = self.lit_eof + 1
        if self.lit_invalid >= (1 << LIT_BITS):
            raise ValueError(f"{len(self.literals)} literals plus the sentinels overflow {LIT_BITS} bits")
        if SET_SHIFT + len(self.sets) > LEN_SHIFT:
            raise ValueError(f"{len(self.sets)} set bits do not fit between bit {SET_SHIFT} and bit {LEN_SHIFT}")

    def set_mask(self, index: int) -> int:
        """The bit mask of the tested set at `index`."""
        return 1 << (SET_SHIFT + index)

    def key(self, codepoint: int, length: int) -> int:
        """The key of `codepoint`, where `length` bytes encode it."""
        key = self.literal_ids.get(codepoint, _LIT_NONE) | (length << LEN_SHIFT)
        for index, (_name, denotation) in enumerate(self.sets):
            if does_contain(denotation, codepoint):
                key |= self.set_mask(index)
        return key

    def sentinel(self, literal_id: int, length: int) -> int:
        """The key of a sentinel. A literal id and no set bits, and a membership test fails at it."""
        return literal_id | (length << LEN_SHIFT)
