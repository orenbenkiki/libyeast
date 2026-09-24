# SPDX-License-Identifier: MIT
"""
Check the subspace algebra against the states it describes.

A `SubSpace` is a description, and the states inside that description are its meaning. So this module judges an
operation by enumerating those states over a small alphabet. The alphabet holds a codepoint per side of a span boundary
the cases use, and the end of the stream. An operation passes where its result holds exactly the states that set
arithmetic on the enumerations gives.

A subspace says a single thing per place and says it coalesced. The table hands out a single answer per set of states.
Any form asking for that set gets the same object. The identities of the algebra hold where the alphabet cannot reach to
show them. An axis of a place cuts the whole space in half.

This judges the places themselves the same way, and in both directions. The module walks the quantities a place orders
over a wider range than `spaces` walks them. The places must hold exactly the states that walk reaches. A place that no
parse reaches is an enumeration that is not what it claims. A state that no place names is a hole a subspace would say
nothing about.

Reports the problems found and exits with a failing status where it finds any.
"""

import itertools
from collections.abc import Sequence

import chars
import gate
import spaces

# The alphabet holds a codepoint per side of a boundary the cases below name. It holds the invalid byte and the top
# codepoint too. The alphabet separates a span that ends where another begins from a span that ends elsewhere.
_ALPHABET = (-1, 0, 0x20, 0x41, 0x42, 0x5A, 0x7E, 0x7F, 0x80, 0xD7FF, 0xE000, chars.MAX_CODEPOINT)

_END_OF_STREAM = "end of stream"  # The end of the stream has no codepoint. An enumeration lists the marker as itself.


def _cases() -> list[tuple[str, spaces.SubSpace]]:
    """The subspaces the laws below run over, as an ordered `[(name, subspace)]`."""
    letters = spaces.characters([(0x41, 0x5A)])
    printable = spaces.characters([(0x20, 0x7E)], is_at_end=True)
    return [
        ("nowhere", spaces.NOWHERE),
        ("complete", spaces.COMPLETE),
        ("letters", letters),
        ("printable", printable),
        ("the end alone", spaces.characters(is_at_end=True)),  # not-prose: the name of a case in the table
        ("the invalid byte", spaces.characters([(-1, -1)])),
        ("at a line start", spaces.where(is_at_line_start=True)),
        ("not at a line start", spaces.where(is_at_line_start=False)),
        ("indented, after ns-char", spaces.where(is_indented=True, is_after_ns_char=True)),
        ("letters at a line start", letters & spaces.where(is_at_line_start=True)),
        ("printable when indented", printable & spaces.where(is_indented=True)),
        ("a pair of disjoint characters", spaces.characters([(0x41, 0x41), (0x7E, 0x7E)])),
    ]


def _states_of(subspace: spaces.SubSpace) -> set[tuple[spaces.GuardAnswers, int | str]]:
    """The states `subspace` holds, as a set of `(answer, codepoint or END_OF_STREAM)` pairs."""
    states: set[tuple[spaces.GuardAnswers, int | str]] = set()
    for answer in spaces.ALL_GUARD_ANSWERS:
        admitted = subspace.under(answer)
        states |= {(answer, code) for code in _ALPHABET if any(low <= code <= high for low, high in admitted.spans)}
        if admitted.is_at_end:
            states.add((answer, _END_OF_STREAM))
    return states


def _check_canonical(named: Sequence[tuple[str, spaces.SubSpace]]) -> list[str]:
    """Check that a subspace says a single thing per place, and that the subspace coalesces what it says."""
    errors = []
    for name, subspace in named:
        if len(subspace.admitted) != len(spaces.ALL_GUARD_ANSWERS):
            errors.append(f"{name}: {len(subspace.admitted)} admitted of {len(spaces.ALL_GUARD_ANSWERS)} guard answers")
            continue
        for answer in spaces.ALL_GUARD_ANSWERS:
            spans = subspace.under(answer).spans
            if list(spans) != [tuple(span) for span in chars.merged_spans(spans)]:
                errors.append(f"{name}: the spans under {answer} run out of order or lie adjacent")
    return errors


# `_FORMS` holds forms of a single set of states. A form writes the set whole. A form cuts the set in half at a boundary
# that closes up. A form puts the spans out of order. A form repeats a span. The table must hand back the same object
# across these forms. The algebra reads a pair of answers as a pair of sets.
_FORMS = (
    ("a run given whole", [(0x41, 0x5A)]),
    ("the same run cut in halves", [(0x41, 0x4F), (0x50, 0x5A)]),
    ("the same run out of order", [(0x50, 0x5A), (0x41, 0x4F)]),
    ("the same run said twice", [(0x41, 0x5A), (0x45, 0x50), (0x41, 0x5A)]),
)


def _check_table() -> list[str]:
    """
    Check that the table hands out a single `spaces._Characters` per set of states. A caller asking for that set in any
    form gets the same object.

    The algebra takes equality to be identity. A pair of answers holding the same characters must be a single object.
    Otherwise a subspace written this way compares unequal to a subspace written that way, and the algebra reads them as
    different sets.

    The check catches a table keyed on the form it got rather than on the set that form writes.
    """
    errors = []
    wanted = spaces.held(_FORMS[0][1])
    for said, spans in _FORMS[1:]:
        if spaces.held(spans) is not wanted:
            errors.append(f"{said}: the table hands out a second answer across the states {_FORMS[0][0]} names")
    if spaces.held([(0x41, 0x5A)], is_at_end=True) is wanted:
        errors.append(
            "the end of the stream: the table hands out a single answer whether a guard answer admits it or not"
        )
    return errors


def _check_operations(named: Sequence[tuple[str, spaces.SubSpace]]) -> list[str]:
    """Check that union, intersection and containment hold exactly what set arithmetic on the states gives."""
    errors = []
    for (one_name, one), (other_name, other) in itertools.product(named, repeat=2):
        pair = f"{one_name} against {other_name}"
        if _states_of(one | other) != _states_of(one) | _states_of(other):
            errors.append(f"{pair}: the union does not hold both")
        if _states_of(one & other) != _states_of(one) & _states_of(other):
            errors.append(f"{pair}: the intersection does not hold what both hold")
        if _states_of(one - other) != _states_of(one) - _states_of(other):
            errors.append(f"{pair}: the difference does not hold what only the first holds")
        if one.does_hold(other) != (_states_of(other) <= _states_of(one)):
            errors.append(f"{pair}: containment disagrees with the states held")
        if bool(one) != bool(_states_of(one)):
            errors.append(f"{one_name}: emptiness disagrees with the states held")
    return errors


def _check_laws(named: Sequence[tuple[str, spaces.SubSpace]]) -> list[str]:
    """Check the identities of the algebra. The states cannot show them where the alphabet does not reach."""
    errors = []
    for name, subspace in named:
        if subspace | spaces.NOWHERE != subspace or subspace & spaces.COMPLETE != subspace:
            errors.append(f"{name}: is changed by the empty union or the whole intersection")
        if subspace & spaces.NOWHERE != spaces.NOWHERE:
            errors.append(f"{name}: reaches the empty subspace somewhere")
        if subspace - subspace != spaces.NOWHERE or subspace - spaces.NOWHERE != subspace:
            errors.append(f"{name}: taking itself away leaves something. taking nothing away changes it.")
        if (subspace & spaces.COMPLETE) | (spaces.COMPLETE - subspace) != spaces.COMPLETE:
            errors.append(f"{name}: a subspace and the rest of the whole space do not come to the whole space")
        if not spaces.COMPLETE.does_hold(subspace) or not subspace.does_hold(spaces.NOWHERE):
            errors.append(f"{name}: is not between the empty subspace and the whole subspace")
        if not subspace.does_hold(subspace):
            errors.append(f"{name}: does not hold itself")
    for (one_name, one), (other_name, other) in itertools.product(named, repeat=2):
        if one | other != other | one or one & other != other & one:
            errors.append(f"{one_name} against {other_name}: the operation is not symmetric")
        if not (one | other).does_hold(one) or not one.does_hold(one & other):
            errors.append(f"{one_name} against {other_name}: the union or the intersection is on the wrong side")
    return errors


def _check_axes() -> list[str]:
    """Check that an axis of a place is an axis `where` narrows on. Check that no place appears twice."""
    errors = []
    if len(spaces.ALL_GUARD_ANSWERS) != len(set(spaces.ALL_GUARD_ANSWERS)):
        errors.append(
            f"{len(spaces.ALL_GUARD_ANSWERS)} guard answers hold {len(set(spaces.ALL_GUARD_ANSWERS))} distinct ones"
        )
    for axis in spaces.AXES:
        narrowed = spaces.where(**{axis: True})
        if narrowed | spaces.where(**{axis: False}) != spaces.COMPLETE:
            errors.append(f"{axis}: its two sides are not the whole space")
        if narrowed & spaces.where(**{axis: False}) != spaces.NOWHERE:
            errors.append(f"{axis}: its two sides overlap")
    return errors


# `_WIDER` holds a negative value and values past the range the space enumerates over. A place says an ordering. A wider
# range keeps the orderings a narrower range finds. A wider range that finds no new ordering shows the narrower range
# saw the whole set.
_WIDER = range(-2, 12)


def _check_guard_answers() -> list[str]:
    """
    Check that the places are the states some parse can be in.

    The check owes both directions, and neither answers the other. A place that nothing realizes is a state the algebra
    admits and no parse reaches. That costs nothing, but says the enumeration is not what it claims. A state some parse
    reaches and no place names is a hole, and a subspace would silently say nothing about it.

    The check enumerates the states itself rather than re-deriving them. The check and `spaces` agree about the
    orderings without sharing a computation.

    The check also asserts the facts that bound the quantities. A line start is column `0`. A consumed length falls
    within its line. A character an `ns-char` names sits at or after a line start. A full span leaves the parse
    mid-line. A check that dropped those facts would call the places they exclude a hole.
    """
    reached = set()
    for is_at_line_start in (False, True):
        for is_after_ns_char in ((False,) if is_at_line_start else (False, True)):
            for did_match_full_span in ((False,) if is_at_line_start else (False, True)):
                for did_consume_since_open in (False, True):
                    for n in _WIDER:
                        for column in (0,) if is_at_line_start else range(1, _WIDER.stop):
                            for consumed_length in range(0, column + 1):
                                for floor in range(0, _WIDER.stop):
                                    reached.add(
                                        spaces.GuardAnswers(
                                            is_at_line_start,
                                            is_after_ns_char,
                                            did_match_full_span,
                                            did_consume_since_open,
                                            0 < n,
                                            column <= n,
                                            n < consumed_length,
                                            consumed_length < n,
                                            floor <= column,
                                            floor <= n,
                                        )
                                    )
    held = set(spaces.ALL_GUARD_ANSWERS)
    return [f"{answer}: an answer no parse reaches" for answer in sorted(held - reached)] + [
        f"{answer}: a state some parse reaches that no answer names" for answer in sorted(reached - held)
    ]


def main() -> None:
    named = _cases()
    errors = (
        _check_canonical(named)
        + _check_table()
        + _check_operations(named)
        + _check_laws(named)
        + _check_axes()
        + _check_guard_answers()
    )
    gate.report(
        errors,
        "subspace error(s)",
        f"subspace algebra OK: {len(named)} subspaces over {len(spaces.ALL_GUARD_ANSWERS)} answers",
    )


if __name__ == "__main__":
    main()
