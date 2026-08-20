# SPDX-License-Identifier: MIT
"""
The space of states a parse can decide in, and the subsets of it a grammar's guards name.

Every guard asks about one axis of a small space: the character in front of the parse, the character behind it, whether
it stands at a line start, whether it stands under indentation, and two bits of its own bookkeeping. So what a parse
stands in when it decides is one point of that space, and what a gate admits, what a way takes and what a call site can
reach are each a subset of it — a `SubSpace`.

Five of the axes are booleans, so a standing is one of the 32 assignments to them, and a `SubSpace` is the characters
admitted under each. That is exact: the axes are finite, union, intersection and containment are computed per standing,
and nothing is widened or narrowed to make an answer fit. A comparison relating two of the parse's own values — `n`
against a measured length, a column or the auto-detected indent — is no axis here, standing between quantities rather
than fixing one, so a guard asking it constrains nothing and its way is admitted under every standing.

The characters are `chars.py`'s spans, the invalid byte among them as the `(-1, -1)` interval it already is. The end of
the stream is not one of them and is not the absence of them either: it is the axis's own value, carried beside the
spans, because a way entered at the end of the stream is a way entered somewhere.
"""

import dataclasses
import itertools

import chars

ALL_CHARACTERS = ((-1, chars.MAX_CODEPOINT),)  # the invalid byte and every codepoint, which is every character there is


@dataclasses.dataclass(frozen=True, order=True)
class Standing:
    """Where a parse stands, as the booleans every guard but the character ones ask about."""

    is_at_line_start: bool
    is_after_ns_char: bool
    is_indented: bool
    did_match_full_span: bool
    did_consume_since_open: bool


STANDINGS = tuple(Standing(*values) for values in itertools.product((False, True), repeat=5))

AXES = tuple(field.name for field in dataclasses.fields(Standing))


@dataclasses.dataclass(frozen=True, order=True)
class Region:
    """The characters a `SubSpace` admits under one standing, and whether it admits the end of the stream there."""

    standing: Standing
    spans: tuple = ()
    is_at_end: bool = False

    def __bool__(self):
        """Whether the region holds a state at all."""
        return bool(self.spans) or self.is_at_end


@dataclasses.dataclass(frozen=True)
class SubSpace:
    """
    A set of the states a parse can decide in, as one `Region` per standing it admits anything under.

    Canonical: the regions are ordered by standing, one standing appears once, no region is empty and no spans are
    unsorted or adjacent. So two subspaces holding the same states are equal, and `NOWHERE` is the only empty one.
    """

    regions: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "regions", tuple(sorted(region for region in self.regions if region)))

    def __bool__(self):
        """Whether the subspace holds a state at all."""
        return bool(self.regions)

    def __or__(self, other):
        """The states either subspace holds."""
        return SubSpace(_combined(self, other, _unioned))

    def __and__(self, other):
        """The states both subspaces hold."""
        return SubSpace(_combined(self, other, _intersected))

    def holds(self, other):
        """Whether every state `other` holds is one this subspace holds."""
        return self | other == self

    def under(self, standing):
        """The characters this subspace admits under `standing`, as a `Region`, empty where it admits none."""
        for region in self.regions:
            if region.standing == standing:
                return region
        return Region(standing)


NOWHERE = SubSpace()
EVERYWHERE = SubSpace(Region(standing, ALL_CHARACTERS, True) for standing in STANDINGS)
# Every state a character can be taken in, which is every one but the end of the stream: taking is what the end has none
# of, so a way that must take is a way no parse standing there enters.
ANY_CHARACTER = SubSpace(Region(standing, ALL_CHARACTERS) for standing in STANDINGS)


def characters(spans=(), is_at_end=False):
    """The subspace admitting `spans`, and the end of the stream where `is_at_end`, wherever the parse stands."""
    admitted = tuple(tuple(span) for span in chars.merged_spans(spans))
    return SubSpace(Region(standing, admitted, is_at_end) for standing in STANDINGS)


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
    matched = [
        standing for standing in STANDINGS if all(getattr(standing, axis) == wanted for axis, wanted in asked.items())
    ]
    return SubSpace(Region(standing, ALL_CHARACTERS, True) for standing in matched)


def _combined(one, other, combine):
    """The regions of `one` and `other` combined standing by standing."""
    return [combine(one.under(standing), other.under(standing)) for standing in STANDINGS]


def _unioned(one, other):
    """The characters either region admits."""
    merged = chars.merged_spans([*one.spans, *other.spans])
    return Region(one.standing, tuple(tuple(span) for span in merged), one.is_at_end or other.is_at_end)


def _intersected(one, other):
    """The characters both regions admit — what one admits, less everything the other does not."""
    outside = chars.subtracted_spans(list(ALL_CHARACTERS), list(other.spans))
    kept = chars.subtracted_spans(list(one.spans), outside)
    return Region(one.standing, tuple(tuple(span) for span in kept), one.is_at_end and other.is_at_end)
