# SPDX-License-Identifier: MIT
"""
The space of states a parse can decide in, and the subsets of it a grammar's guards name.

Every guard asks about one axis of a small space: the character in front of the parse, the character behind it, whether
it stands at a line start, two bits of its own bookkeeping, and how the parse's own quantities stand against each other.
So what a parse stands in when it decides is one point of that space, and what a gate admits, what a way takes and what
a call site can reach are each a subset of it — a `SubSpace`.

All but the character axis are booleans, so a *standing* is an assignment to them, and a `SubSpace` is what it admits
under each. That is exact: the axes are finite, union, intersection and containment are computed per standing, and
nothing is widened or narrowed to make an answer fit.

**A comparison of two of the parse's own quantities is an axis, and the quantities are not.** The indentation, the
column, the length of the run just measured and the block scalar's floor are integers of no fixed range, so none of them
is a coordinate — but no guard reads one. Every one asks how two of them stand, and there are six such questions in the
grammar, so those six are the axes and the magnitudes never appear.

Six free booleans would carry states no parse can be in: an ordering is transitive, and a column is never negative while
a line start is column zero. So the standings are not every assignment — they are the assignments some integers make,
enumerated once from the quantities themselves. That is where the arithmetic is done and the only place it is.

The characters are `chars.py`'s spans, the invalid byte among them as the `(-1, -1)` interval it already is. The end of
the stream is not one of them and is not the absence of them either: it is the axis's own value, carried beside the
spans, because a way entered at the end of the stream is a way entered somewhere.

**One `Characters` per distinct answer, and a subspace is a reference to one per standing.** The same few sets are
admitted over and over — a lookahead's set stands under every standing, and the standings a guard narrows to all admit
whatever the other side of the meet admitted — so the answers are held in a table and handed out rather than built.
Equal answers are then the same object, `is` decides equality, and what two of them come to is a lookup on the pair.
That is what keeps the cost of the algebra in the number of *distinct sets* the grammar holds rather than in the number
of standings, which is free to grow with the questions the guards ask.
"""

import dataclasses

import chars

ALL_CHARACTERS = ((-1, chars.MAX_CODEPOINT),)  # the invalid byte and every codepoint, which is every character there is


@dataclasses.dataclass(frozen=True, order=True)
class Standing:
    """
    Where a parse stands, as the booleans every guard but the character ones ask about.

    Four of them say where it is and what it has just done; six say how its own quantities stand — the indentation `n`,
    the column, the length of the run just measured, and the block scalar's leading-empty floor `f`. Those four
    quantities are integers of no fixed range and none of them is a coordinate here; what the guards ask is only ever
    how two of them compare, and these six are every such question the grammar has.
    """

    is_at_line_start: bool
    is_after_ns_char: bool
    did_match_full_span: bool
    did_consume_since_open: bool
    is_indented: bool  # 0 < n
    is_not_too_indented: bool  # column <= n
    is_measured_past_the_indent: bool  # n < len(match)
    is_measured_under_the_indent: bool  # len(match) < n
    is_column_at_least_the_floor: bool  # f <= column
    is_indent_at_least_the_floor: bool  # f <= n


# How far the quantities are enumerated below. Five ranks tell any four of them apart, and the count stops growing well
# inside this: `check_spaces` enumerates wider and holds what it finds to what stands here.
_RANKS = 8


def _compared(is_at_line_start):
    """
    Every way the parse's quantities can stand against each other, as `(the six axes above)` tuples.

    Worked out from the quantities rather than declared, so the answers are consistent by construction: an ordering is
    transitive and no set of integers makes a tuple that is not here. Four facts bound them, each true of every parse
    rather than of the grammar's current shape:

    - **A column is where the parse stands in its line, and a line start is column zero.** So the column is zero at a
      line start and at least one anywhere else — the interpreter says exactly this, setting `is_sol` to `column == 0`.
    - **The indentation reaches one below zero.** The root is entered at `-1`, where nothing has been pushed, and
      `0 < n` tells that apart from a pushed zero.
    - **The measured run and the floor are lengths**, so neither is negative.
    - **The measured run stands within the line the parse is on**, so it is no longer than the column. Every site
      reading it measures `s-space*` under a `(token): indent`, and a space is neither a break — which would reset the
      column under it — nor a byte-order mark, which would take no column of its own.
    """
    columns = (0,) if is_at_line_start else range(1, _RANKS)
    return sorted(
        {
            (0 < n, column <= n, n < measured, measured < n, floor <= column, floor <= n)
            for n in range(-1, _RANKS)
            for column in columns
            for measured in range(0, column + 1)
            for floor in range(0, _RANKS)
        }
    )


def _standings():
    """
    Every standing a parse can be in: the orderings above under each way the four other axes can stand.

    Two of those four are not free either, and both are facts about what the parse has just done:

    - **Nothing an `ns-char` names stands behind a line start.** What is behind one is a break, a byte-order mark, or
      nothing at all, and `ns-char` holds none of the three.
    - **A run that took its whole limit leaves the parse mid-line.** Every limited run the grammar makes takes spaces
      or hex digits, neither holding a break, and no consume ever consumes nothing — so having taken one is standing
      past it, on the line it was taken on.
    """
    return tuple(
        Standing(is_at_line_start, is_after_ns_char, did_match_full_span, did_consume_since_open, *compared)
        for is_at_line_start in (False, True)
        for is_after_ns_char in ((False,) if is_at_line_start else (False, True))
        for did_match_full_span in ((False,) if is_at_line_start else (False, True))
        for did_consume_since_open in (False, True)
        for compared in _compared(is_at_line_start)
    )


STANDINGS = _standings()

AXES = tuple(field.name for field in dataclasses.fields(Standing))

_AT = {standing: at for at, standing in enumerate(STANDINGS)}  # where each standing's answer stands in a subspace


@dataclasses.dataclass(frozen=True)
class Characters:
    """
    The characters admitted under one standing, and whether the end of the stream is admitted there.

    Held in `_KNOWN` and handed out by `held`, so two of these holding the same characters are one object. Nothing
    builds one directly: what makes them worth interning is that `is` then answers what an equality would have to walk
    the spans for, and that what two of them come to can be remembered against the pair.
    """

    spans: tuple = ()
    is_at_end: bool = False

    def __bool__(self):
        """Whether anything is admitted at all."""
        return bool(self.spans) or self.is_at_end


_KNOWN = {}
_MET = {}  # what two of them hold in common, per pair
_JOINED = {}  # what either of them holds, per pair
_DROPPED = {}  # what the first holds and the second does not, per pair


def held(spans=(), is_at_end=False):
    """The one `Characters` admitting `spans` and, where `is_at_end`, the end of the stream."""
    admitted = tuple(tuple(span) for span in chars.merged_spans(spans))
    key = (admitted, is_at_end)
    known = _KNOWN.get(key)
    if known is None:
        _KNOWN[key] = known = Characters(admitted, is_at_end)
    return known


NONE = held()


def _met(one, other):
    """What both admit, remembered against the pair — which is the same pair over and over."""
    if one is other:
        return one
    if one is NONE or other is NONE:
        return NONE
    known = _MET.get((one, other))
    if known is None:
        _MET[one, other] = known = held(
            chars.intersected_spans(one.spans, other.spans), one.is_at_end and other.is_at_end
        )
    return known


def _dropped(one, other):
    """What the first admits and the second does not, remembered against the pair."""
    if one is other or one is NONE:
        return NONE
    if other is NONE:
        return one
    known = _DROPPED.get((one, other))
    if known is None:
        _DROPPED[one, other] = known = held(
            chars.subtracted_spans(list(one.spans), list(other.spans)), one.is_at_end and not other.is_at_end
        )
    return known


def _joined(one, other):
    """What either admits, remembered against the pair."""
    if one is other:
        return one
    if one is NONE:
        return other
    if other is NONE:
        return one
    known = _JOINED.get((one, other))
    if known is None:
        _JOINED[one, other] = known = held([*one.spans, *other.spans], one.is_at_end or other.is_at_end)
    return known


@dataclasses.dataclass(frozen=True)
class SubSpace:
    """
    A set of the states a parse can decide in, as what it admits under each standing.

    Canonical by construction: one answer per standing, standing by standing in `STANDINGS` order, each of them the one
    `Characters` its spans name. So two subspaces holding the same states are equal — and equal the cheap way, every
    answer being the same object where it says the same thing — and `NOWHERE` is the only empty one.
    """

    admitted: tuple = ()

    def __post_init__(self):
        if not self.admitted:
            object.__setattr__(self, "admitted", (NONE,) * len(STANDINGS))

    def __bool__(self):
        """Whether the subspace holds a state at all."""
        return any(self.admitted)

    def __or__(self, other):
        """The states either subspace holds."""
        if self is other:
            return self
        return SubSpace(tuple(map(_joined, self.admitted, other.admitted)))

    def __and__(self, other):
        """The states both subspaces hold."""
        if self is other:
            return self
        return SubSpace(tuple(map(_met, self.admitted, other.admitted)))

    def __sub__(self, other):
        """The states this subspace holds and `other` does not."""
        if self is other:
            return NOWHERE
        return SubSpace(tuple(map(_dropped, self.admitted, other.admitted)))

    def holds(self, other):
        """Whether every state `other` holds is one this subspace holds."""
        return self | other == self

    def under(self, standing):
        """The characters this subspace admits under `standing`, as a `Characters`, empty where it admits none."""
        return self.admitted[_AT[standing]]


NOWHERE = SubSpace()
EVERYWHERE = SubSpace((held(ALL_CHARACTERS, True),) * len(STANDINGS))
# Every state a character can be taken in, which is every one but the end of the stream: taking is what the end has none
# of, so a way that must take is a way no parse standing there enters.
ANY_CHARACTER = SubSpace((held(ALL_CHARACTERS),) * len(STANDINGS))


def characters(spans=(), is_at_end=False):
    """The subspace admitting `spans`, and the end of the stream where `is_at_end`, wherever the parse stands."""
    return SubSpace((held(spans, is_at_end),) * len(STANDINGS))


def not_characters(spans):
    """
    The subspace admitting every character `spans` does not, and the end of the stream.

    A negative lookahead is what this says: it refuses the characters it names and passes where there is no character at
    all, the end of the input matching nothing being how it passes for free.
    """
    return characters(chars.subtracted_spans(list(ALL_CHARACTERS), list(spans)), is_at_end=True)


def where(**asked):
    """
    The subspace admitting every character under the standings whose axes are as `asked` names them.

    Each keyword is one of `AXES`; an axis not named is admitted either way, a guard saying nothing about it.
    """
    for axis in asked:
        if axis not in AXES:
            raise ValueError(f"no axis named {axis}: the axes are {', '.join(AXES)}")
    everything = held(ALL_CHARACTERS, True)
    return SubSpace(
        tuple(
            everything if all(getattr(standing, axis) == wanted for axis, wanted in asked.items()) else NONE
            for standing in STANDINGS
        )
    )
