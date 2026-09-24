# SPDX-License-Identifier: MIT
"""
The space of states a parse can decide in, and the subsets of it a grammar's guards name.

A guard asks about a single axis of a small space. The character in front of the parse is an axis. The character behind
it is an axis. A further axis is a boolean. A boolean says whether the parse is at a line start. The booleans hold the
bookkeeping bits and the comparisons between the parse's quantities. A deciding parse is at a single point of that
space. A gate admits a subset of that space. A way's take admits a subset, and a call site's reach admits a subset. A
subset is a `SubSpace`.

The axes named by `BOOKKEEPING_AXES` and `COMPARISON_AXES` are booleans. A `GuardAnswers` is an assignment to them. It
answers the questions a guard can ask. A `SubSpace` records the characters admitted under such an assignment. The axes
are finite. This computes union, intersection and containment per assignment. This widens no answer and narrows none to
make it fit.

**A comparison of the parse's quantities is an axis. A quantity is not an axis.** The indentation and the column are
integers of no fixed range. The length of the last consume and the block scalar's floor are integers of no fixed range
as well. A quantity is no coordinate here, and no guard reads a quantity. A guard asks how a pair of quantities compare,
or how the indentation compares against `0`. `COMPARISON_AXES` holds those questions.

Free booleans would admit states no parse can be in. An ordering is transitive. A column is not negative, and a line
start is column `0`. `ALL_GUARD_ANSWERS` holds the assignments those integers make. The module enumerates them once from
the quantities themselves. That enumeration does the arithmetic.

The characters are `chars.py`'s spans. The invalid byte is one of them, as the `(-1, -1)` interval. The end of the
stream is a value of the axis too. A way entered at the end of the stream is a way entered somewhere.

**There is a single `_Characters` per distinct answer, and a subspace holds a reference per assignment.** A grammar
admits the same few sets over and over. A table holds a distinct set once, and a lookup hands out a reference. Equal
sets are then the same object, and `is` decides equality. An operation on a pair of sets looks that pair up. The cost of
the algebra follows the number of distinct sets rather than the number of assignments.
"""

import dataclasses
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

import chars

ALL_CHARACTERS = ((-1, chars.MAX_CODEPOINT),)  # The invalid byte and the codepoints. Together they are the characters.


@dataclasses.dataclass(frozen=True, order=True)
class GuardAnswers:
    """
    The answers a guard gets at a point of the parse, as booleans beside the characters.

    `BOOKKEEPING_AXES` say where the parse is and what it has just done. `COMPARISON_AXES` say how the parse's
    quantities compare.

    The quantities are the indentation `n` and the column. They are also the length of the last consume, and the block
    scalar's leading-empty floor `f`. They are integers of no fixed range, and a quantity is no coordinate here. A guard
    asks how a pair of them compare, or how the indentation compares against `0`.
    """

    is_at_line_start: bool
    is_after_ns_char: bool
    did_match_full_span: bool
    did_consume_since_open: bool
    is_indented: bool  # `0 < n`.
    is_not_too_indented: bool  # `column <= n`.
    is_consumed_length_past_the_indent: bool  # `n < len(match)`.
    is_consumed_length_under_the_indent: bool  # `len(match) < n`.
    is_column_at_least_the_floor: bool  # `f <= column`.
    is_indent_at_least_the_floor: bool  # `f <= n`.


class _Quantities(NamedTuple):
    """
    The parse's quantities. A caller names the quantities by slot rather than by position, in the order `comparisons`
    takes them.

    Named rather than positional. The enumeration here covers the same quantities that the axis comparison above reads,
    and the same quantities that an action write in `normalize` produces. A caller names its slot. The name says which
    quantity is which, rather than the order the writer put them in.
    """

    n: int
    column: int
    consumed_length: int
    floor: int


# The depth to which the enumeration below extends the quantities. The ranks here tell the quantities apart, and the
# count stops growing well inside that depth. `check_spaces` enumerates wider and holds what it finds to the depth here.
_RANKS = 8


def _quantified(is_at_line_start: bool) -> tuple[_Quantities, ...]:
    """
    The parse's quantities take the form of `(n, column, consumed_length, floor)` tuples, whether or not a line start
    applies.

    These tuples state the bounds that `_every_ordering` names, as the tuples themselves rather than as a comparison. A
    caller can then work out a take's effect as an arithmetic on the quantities, and read the result back as
    comparisons.
    """
    columns = (0,) if is_at_line_start else range(1, _RANKS)
    return tuple(
        _Quantities(n, column, consumed_length, floor)
        for n in range(-1, _RANKS)
        for column in columns
        for consumed_length in range(0, column + 1)
        for floor in range(0, _RANKS)
    )


# The quantity names, and the quantities a comparison axis reads. The entries follow `COMPARISON_AXES`.
#
# A reader holding a parse rather than a `GuardAnswers` looks up the quantities an axis reads. A reader lacking a value
# for such a quantity cannot answer the axis. That reader says so rather than guessing.
#
# The quantities sit beside the comparisons. A comparison and its quantities state a single fact.
QUANTITIES = _Quantities._fields
# The quantities a comparison reads, in the order `COMPARISON_AXES` names them.
COMPARISON_READS = (
    ("n",),
    ("column", "n"),
    ("n", "consumed_length"),
    ("consumed_length", "n"),
    ("floor", "column"),
    ("floor", "n"),
)


def comparisons(quantities: Sequence[int]) -> tuple[bool, ...]:
    """
    The comparison axes `(n, column, consumed_length, floor)` settles, in the order `GuardAnswers` holds them.

    A reader of the grammar or of a parse gets them from here. A parse and the enumeration read the same axes. A
    comparison written again beside a caller is a second thing to keep in step.

    `check_spaces._check_guard_answers` does write them again, and says why. That module is an oracle. An oracle
    computing what it checks would check nothing.
    """
    named = _Quantities(*quantities)
    return (
        0 < named.n,
        named.column <= named.n,
        named.n < named.consumed_length,
        named.consumed_length < named.n,
        named.floor <= named.column,
        named.floor <= named.n,
    )


def _every_ordering(is_at_line_start: bool) -> list[tuple[bool, ...]]:
    """
    The ways the parse's quantities can compare against one another, as `(the comparison axes above)` tuples.

    This works the orderings out from the quantities rather than declaring them. The answers are then consistent by
    construction. An ordering is transitive, and no set of integers makes a tuple that is not here.

    The facts below bound the orderings. They hold true of a parse rather than of the grammar's current shape.

    - **A column is where the parse is in its line, and a line start is column `0`.** So the column is `0` at a
      line start and at least `1` anywhere else. A take keeps that. A break goes to column `0` and a line start. Any
      other character takes a column and leaves the line start behind. The byte-order mark moves neither column nor
      line start.
    - **The indentation does not fall below `-1`.** A parse enters the root at `-1` and has pushed nothing there, and
      `0 < n` tells that apart from a pushed `0`.
      The grammar does compute lower. A parse enters a block sequence at `n - 1`. A block sequence at the root
      therefore reaches `-2`. A reader treats a `-1` state the same as a `-2` state. Both read as
      `(<): [n, <column>]` against a column that is not negative. `interpreter._indent` holds the value it hands back
      to `-1`, and no state of a parse falls outside the enumeration here.
    - **What a consume took and the floor are lengths.** Neither is negative.
    - **A consume falls within the line the parse is on.** The consumed length is at most the column. A site reading
      the consumed length measures `s-space*` under a `(token): indent`. A space is not a break. A break would reset
      the column under the consume. A space is not a byte-order mark either. A byte-order mark takes no column of its
      own.
    """
    return sorted({comparisons(quantities) for quantities in _quantified(is_at_line_start)})


def _all_guard_answers() -> tuple[GuardAnswers, ...]:
    """
    The answers a parse can get. An answer pairs an ordering above with a way the bookkeeping axes can go.

    A pair of the bookkeeping axes do not vary freely. Both record a fact about the parse's last step.

    - **Nothing an `ns-char` names comes behind a line start.** A break, a byte-order mark, or the input's start
      comes behind a line start. `ns-char` names none of them.
    - **A run that took its whole limit leaves the parse mid-line.** A limited run the grammar makes takes spaces or
      hex digits. Neither of those holds a break. A consume takes at least a character. A parse that took such a run
      therefore ends past the run, on the line the run started.
    """
    return tuple(
        GuardAnswers(is_at_line_start, is_after_ns_char, did_match_full_span, did_consume_since_open, *compared)
        for is_at_line_start in (False, True)
        for is_after_ns_char in ((False,) if is_at_line_start else (False, True))
        for did_match_full_span in ((False,) if is_at_line_start else (False, True))
        for did_consume_since_open in (False, True)
        for compared in _every_ordering(is_at_line_start)
    )


ALL_GUARD_ANSWERS = _all_guard_answers()

# `AXES` names the axes of `GuardAnswers` in declaration order. A caller building a `GuardAnswers` from a mapping reads
# the axes here.
AXES = tuple(field.name for field in dataclasses.fields(GuardAnswers))

# The kinds of axis. A bookkeeping axis is a bit the parse holds. That bit says where the parse is and what it has just
# done. A comparison axis says how a pair of the parse's quantities compare.
#
# This names both groups rather than taking a group as the axes the other leaves. An axis added to `GuardAnswers` and
# put in neither group draws a refusal here. A group taken as a leftover would swallow that axis silently.
BOOKKEEPING_AXES = ("is_at_line_start", "is_after_ns_char", "did_match_full_span", "did_consume_since_open")
# The axes a comparison decides. `COMPARISON_READS` gives the quantities an axis reads.
COMPARISON_AXES = (
    "is_indented",
    "is_not_too_indented",
    "is_consumed_length_past_the_indent",
    "is_consumed_length_under_the_indent",
    "is_column_at_least_the_floor",
    "is_indent_at_least_the_floor",
)
if set(BOOKKEEPING_AXES) | set(COMPARISON_AXES) != set(AXES):
    raise ValueError("an axis of `GuardAnswers` is in neither group, or a name here is no axis of a `GuardAnswers`")
if len(COMPARISON_READS) != len(COMPARISON_AXES) or any(
    name not in QUANTITIES for reads in COMPARISON_READS for name in reads
):
    raise ValueError("`COMPARISON_READS` leaves a comparison axis without the quantities it reads")

# the index of a `GuardAnswers` in a subspace.
_AT = {guard_answers: at for at, guard_answers in enumerate(ALL_GUARD_ANSWERS)}

HELD = frozenset(ALL_GUARD_ANSWERS)  # the answers a parse ever gets.


@dataclasses.dataclass(frozen=True)
class _Characters:
    """
    The characters a `GuardAnswers` admits, and whether it admits the end of the stream.

    `_KNOWN` holds these and `held` hands them out. A pair of them holding the same characters is a single object.

    `held` builds them. `is` then decides equality without walking either span. `_INTERSECTED` remembers the
    intersection between a pair of them.
    """

    spans: tuple[tuple[int, int], ...] = ()
    is_at_end: bool = False

    def __bool__(self) -> bool:
        """Whether this admits any character or the end of the stream."""
        return bool(self.spans) or self.is_at_end


# A character set as merged and sorted `(low, high)` pairs. `chars` hands a set over in this form.
_Spans = tuple[tuple[int, int], ...]

_KNOWN: dict[tuple[_Spans, bool], _Characters] = {}  # a single `_Characters` per spans-and-end pair.
_INTERSECTED: dict[tuple[_Characters, _Characters], _Characters] = {}  # the characters a pair of them both admit.
_JOINED: dict[tuple[_Characters, _Characters], _Characters] = {}  # the characters either of them admits.
_DROPPED: dict[tuple[_Characters, _Characters], _Characters] = {}  # the characters only the first admits.


def held(spans: Iterable[tuple[int, int]] = (), is_at_end: bool = False) -> _Characters:
    """The `_Characters` admits `spans` and, where `is_at_end`, the end of the stream."""
    admitted = tuple(chars.merged_spans(spans))
    key = (admitted, is_at_end)
    known = _KNOWN.get(key)
    if known is None:
        _KNOWN[key] = known = _Characters(admitted, is_at_end)
    return known


_NONE = held()
_ANYTHING = held(ALL_CHARACTERS, True)  # `_ANYTHING` admits the characters and the end of the stream.


def _intersected(one: _Characters, other: _Characters) -> _Characters:
    """
    The characters both admit. `_INTERSECTED` remembers the answer against the pair.
    """
    if one is other:
        return one
    if one is _NONE or other is _NONE:
        return _NONE
    known = _INTERSECTED.get((one, other))
    if known is None:
        _INTERSECTED[one, other] = known = held(
            chars.intersected_spans(one.spans, other.spans), one.is_at_end and other.is_at_end
        )
    return known


def _dropped(one: _Characters, other: _Characters) -> _Characters:
    """The characters the first admits and the second refuses. `_DROPPED` remembers the answer against the pair."""
    if one is other or one is _NONE:
        return _NONE
    if other is _NONE:
        return one
    known = _DROPPED.get((one, other))
    if known is None:
        _DROPPED[one, other] = known = held(
            chars.subtracted_spans(list(one.spans), list(other.spans)), one.is_at_end and not other.is_at_end
        )
    return known


def _joined(one: _Characters, other: _Characters) -> _Characters:
    """The characters either admits. `_JOINED` remembers the answer against the pair."""
    if one is other:
        return one
    if one is _NONE:
        return other
    if other is _NONE:
        return one
    known = _JOINED.get((one, other))
    if known is None:
        _JOINED[one, other] = known = held([*one.spans, *other.spans], one.is_at_end or other.is_at_end)
    return known


@dataclasses.dataclass(frozen=True)
class SubSpace:
    """
    A set of the states a parse can decide in. The set names what it admits under a `GuardAnswers`.

    The build step keeps the set canonical. A tuple holds a single answer per `GuardAnswers`, in the order specified by
    `ALL_GUARD_ANSWERS`. A `_Characters` holds the spans of an answer.

    A pair of subspaces holding the same states are therefore equal, and equal the cheap way. An answer is the same
    object wherever it says the same thing. `NOWHERE` is the empty subspace.
    """

    admitted: tuple[_Characters, ...] = ()

    def __post_init__(self) -> None:
        """Fill a subspace built with nothing named. The filled subspace admits no character under any answers."""
        if not self.admitted:
            object.__setattr__(self, "admitted", (_NONE,) * len(ALL_GUARD_ANSWERS))

    def __bool__(self) -> bool:
        """Whether the subspace holds a state at all."""
        return any(self.admitted)

    def __or__(self, other: "SubSpace") -> "SubSpace":
        """The states either subspace holds."""
        if self is other:
            return self
        return SubSpace(tuple(map(_joined, self.admitted, other.admitted)))

    def __and__(self, other: "SubSpace") -> "SubSpace":
        """The states both subspaces hold."""
        if self is other:
            return self
        return SubSpace(tuple(map(_intersected, self.admitted, other.admitted)))

    def __sub__(self, other: "SubSpace") -> "SubSpace":
        """The states this subspace holds and `other` refuses."""
        if self is other:
            return NOWHERE
        return SubSpace(tuple(map(_dropped, self.admitted, other.admitted)))

    def does_hold(self, other: "SubSpace") -> bool:
        """Whether the states `other` holds are ones this subspace holds."""
        return self | other == self

    def under(self, guard_answers: GuardAnswers) -> _Characters:
        """The characters this subspace admits under `guard_answers`, as a `_Characters`, empty where it admits none."""
        return self.admitted[_AT[guard_answers]]


NOWHERE = SubSpace()  # the empty subspace.
COMPLETE = SubSpace((_ANYTHING,) * len(ALL_GUARD_ANSWERS))  # `_ANYTHING` under the answers a guard gets.
# The states that admit a character. These are the states but the end of the stream. The end of the stream takes
# nothing. A way that must take is a way no parse enters from there.
ANY_CHARACTER = SubSpace((held(ALL_CHARACTERS),) * len(ALL_GUARD_ANSWERS))


def characters(spans: Iterable[tuple[int, int]] = (), is_at_end: bool = False) -> SubSpace:
    """The subspace admitting `spans` under any guard answer, and the end of the stream where `is_at_end`."""
    return SubSpace((held(spans, is_at_end),) * len(ALL_GUARD_ANSWERS))


def not_characters(spans: Iterable[tuple[int, int]]) -> SubSpace:
    """
    The subspace admitting the characters `spans` refuses, and the end of the stream.

    This says a negative lookahead. The subspace refuses the characters `spans` names, and passes where the input holds
    no character at all. The end of the input matches nothing. The lookahead passes there for free.
    """
    return characters(chars.subtracted_spans(list(ALL_CHARACTERS), list(spans)), is_at_end=True)


# the quantity tuples a `GuardAnswers` holds, worked out once and kept.
_HOLDING: dict[tuple[bool, tuple[bool, ...]], list[_Quantities]] = {}


def _holding(guard_answers: GuardAnswers) -> Sequence[_Quantities]:
    """
    The `(n, column, consumed_length, floor)` tuples `guard_answers` holds. These tuples invert the reading
    `comparisons` gives.
    """
    if not _HOLDING:
        for at_line_start in (False, True):
            for quantities in _quantified(at_line_start):
                _HOLDING.setdefault((at_line_start, comparisons(quantities)), []).append(quantities)
    compared = tuple(getattr(guard_answers, axis) for axis in COMPARISON_AXES)
    return _HOLDING.get((guard_answers.is_at_line_start, compared), ())


TOP = _RANKS - 1  # the value a quantity saturates at. The comparisons stop changing above it, as `check_spaces` holds.


# the answers a `(bits, quantities)` comes to. A move asks for this once per tuple it yields.
_GUARD_ANSWERS_AT: dict[tuple[tuple[bool, ...], tuple[int, ...]], GuardAnswers] = {}


def _guard_answers_at(bits: tuple[bool, ...], quantities: tuple[int, ...]) -> GuardAnswers:
    """
    The answers `bits` and `quantities` come to. An action sets the booleans and moves the quantities.

    A quantity outside the enumeration answers no differently from the edge it passed. The moves therefore saturate
    here, rather than a move clamping for itself.
    """
    guard_answers = _GUARD_ANSWERS_AT.get((bits, quantities))
    if guard_answers is None:
        _GUARD_ANSWERS_AT[bits, quantities] = guard_answers = GuardAnswers(
            *bits, *comparisons(tuple(min(max(value, -1), TOP) for value in quantities))
        )
    return guard_answers


def reached_by(
    move: Callable[[GuardAnswers, _Quantities], Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]],
) -> dict[GuardAnswers, frozenset[GuardAnswers]]:
    """
    `{answers: the answers performing `move` reaches from them}`. This computes the cost of performing an action.

    A caller hands `move` a `GuardAnswers` and the `Quantities` that `GuardAnswers` holds. `move` yields the states
    reached from there, as `(bits, quantities)` pairs. An action that can go more than a single way yields a state per
    way. An action those answers refuse yields no state.

    The arithmetic itself belongs to whoever knows the action. The contents of a `GuardAnswers` and the meaning of the
    results belong here.

    This reads `ALL_GUARD_ANSWERS` rather than the answers some space holds. The answers at a parse decide where an
    action leaves the parse. The result is a property of the action. `reached_by` works it out once, however many spaces
    later perform the action.
    """
    reached: dict[GuardAnswers, frozenset[GuardAnswers]] = {}
    for guard_answers in ALL_GUARD_ANSWERS:
        got: set[GuardAnswers] = set()
        for quantities in _holding(guard_answers):
            for bits, moved in move(guard_answers, quantities):
                landed = _guard_answers_at(bits, moved)
                # Some of the bits are not free of the rest. A move may name answers no parse gets, which dropped would
                # read as a state the action cannot reach.
                if landed not in _AT:
                    raise ValueError(f"a move reaching {landed}. no parse gets those answers.")
                got.add(landed)
        reached[guard_answers] = frozenset(got)
    return reached


def after(space: SubSpace, reached: Mapping[GuardAnswers, Iterable[GuardAnswers]], does_take: bool) -> SubSpace:
    """
    A parse performs something in `space`. `after` returns the states that action reaches. The action's `reached` says
    where it goes.

    `does_take` says whether the action consumed a character. That decides the characters the result admits. Past a take
    the next character is a character the input holds. An action taking nothing leaves the question unchanged.
    """
    held_by: dict[GuardAnswers, _Characters] = {}
    for guard_answers in ALL_GUARD_ANSWERS:
        admitted = space.under(guard_answers)
        if not admitted:
            continue
        handed_on = _ANYTHING if does_take else admitted
        for landed in reached[guard_answers]:
            held_by[landed] = _joined(held_by.get(landed, _NONE), handed_on)
    return SubSpace(tuple(held_by.get(guard_answers, _NONE) for guard_answers in ALL_GUARD_ANSWERS))


def guard_answers_in(wanted_answers: Iterable[GuardAnswers]) -> SubSpace:
    """
    The subspace admitting the characters under `wanted_answers`, and no character anywhere else.

    A caller holding answers rather than axes asks for this. A parse that really reached somewhere becomes a space. A
    checker of the grammar can then ask whether the grammar allows that parse.

    This module builds the subspace rather than the caller that asks for it. `_NONE` and `_ANYTHING` belong here with
    the subspace.
    """
    wanted = set(wanted_answers)
    return SubSpace(tuple(_ANYTHING if guard_answers in wanted else _NONE for guard_answers in ALL_GUARD_ANSWERS))


def where(**asked: bool) -> SubSpace:
    """
    The subspace admitting any character under the answers whose axes are as `asked` names them.

    A keyword is one of `AXES`. The subspace admits both values of an axis `asked` leaves out. A guard says nothing
    about that axis. `guard_answers_in` asks the same question of answers rather than of axes.
    """
    for axis in asked:
        if axis not in AXES:
            raise ValueError(f"no axis named {axis}: the axes are {', '.join(AXES)}")
    return SubSpace(
        tuple(
            _ANYTHING if all(getattr(guard_answers, axis) == wanted for axis, wanted in asked.items()) else _NONE
            for guard_answers in ALL_GUARD_ANSWERS
        )
    )
