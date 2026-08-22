# SPDX-License-Identifier: MIT
"""
Check the subspace algebra against the states it stands for.

A `SubSpace` is a description; the states it holds are what it means. So every operation is judged by enumerating those
states over a small alphabet — a codepoint from each side of every span boundary the cases use, and the end of the
stream — and asking whether the operation's result holds exactly the states set arithmetic on the enumerations gives.
The alphabet is small and the standings are all 32, so the enumeration is exhaustive over what the cases can tell apart.

Reports every problem found and exits non-zero if there are any.
"""

import itertools

import chars
import gate
import spaces

# One codepoint from each side of every boundary the cases below name, the invalid byte and the highest codepoint among
# them, so a span that ends where another begins is told from one that does not.
ALPHABET = (-1, 0, 0x20, 0x41, 0x42, 0x5A, 0x7E, 0x7F, 0x80, 0xD7FF, 0xE000, chars.MAX_CODEPOINT)

END_OF_STREAM = "end of stream"  # no codepoint, so it stands for itself in an enumeration


def cases():
    """The subspaces every law below is checked over, as an ordered `[(name, subspace)]`."""
    letters = spaces.characters([(0x41, 0x5A)])
    printable = spaces.characters([(0x20, 0x7E)], is_at_end=True)
    return [
        ("nowhere", spaces.NOWHERE),
        ("everywhere", spaces.EVERYWHERE),
        ("letters", letters),
        ("printable", printable),
        ("the end alone", spaces.characters(is_at_end=True)),
        ("the invalid byte", spaces.characters([(-1, -1)])),
        ("at a line start", spaces.where(is_at_line_start=True)),
        ("not at a line start", spaces.where(is_at_line_start=False)),
        ("indented, after ns-char", spaces.where(is_indented=True, is_after_ns_char=True)),
        ("letters at a line start", letters & spaces.where(is_at_line_start=True)),
        ("printable when indented", printable & spaces.where(is_indented=True)),
        ("two disjoint characters", spaces.characters([(0x41, 0x41), (0x7E, 0x7E)])),
    ]


def states_of(subspace):
    """The states `subspace` holds, as a set of `(standing, codepoint or END_OF_STREAM)` pairs."""
    states = set()
    for standing in spaces.STANDINGS:
        admitted = subspace.under(standing)
        states |= {(standing, code) for code in ALPHABET if any(low <= code <= high for low, high in admitted.spans)}
        if admitted.is_at_end:
            states.add((standing, END_OF_STREAM))
    return states


def check_canonical(named):
    """Check that a subspace says one thing per standing, and that what it says is coalesced."""
    errors = []
    for name, subspace in named:
        if len(subspace.admitted) != len(spaces.STANDINGS):
            errors.append(f"{name}: {len(subspace.admitted)} answers for {len(spaces.STANDINGS)} standings")
            continue
        for standing in spaces.STANDINGS:
            spans = subspace.under(standing).spans
            if list(spans) != [tuple(span) for span in chars.merged_spans(spans)]:
                errors.append(f"{name}: the spans under {standing} are unsorted or adjacent")
    return errors


# One set of states, spelled several ways: out of order, cut in two at a boundary that closes up, and a span repeated.
# The table is asked for each and must hand back the one answer, since the algebra reads two answers as two sets.
SPELLINGS = (
    ("a run given whole", [(0x41, 0x5A)]),
    ("the same run cut in two", [(0x41, 0x4F), (0x50, 0x5A)]),
    ("the same run out of order", [(0x50, 0x5A), (0x41, 0x4F)]),
    ("the same run said twice", [(0x41, 0x5A), (0x45, 0x50), (0x41, 0x5A)]),
)


def check_table():
    """
    Check that the table hands out one `Characters` per set of states, whatever spelling asks for it.

    Equality being identity is what the algebra rests on: two answers holding the same characters must be one object, or
    a subspace built one way compares unequal to a subspace built another and the two are read as different sets. A
    table keyed on what it was handed rather than on what that comes to is how that breaks, so the spellings above are
    asked for and held to being the one answer.
    """
    errors = []
    wanted = spaces.held(SPELLINGS[0][1])
    for said, spans in SPELLINGS[1:]:
        if spaces.held(spans) is not wanted:
            errors.append(f"{said}: the table hands out a second answer for the states {SPELLINGS[0][0]} names")
    if spaces.held([(0x41, 0x5A)], is_at_end=True) is wanted:
        errors.append("the end of the stream: the table hands out one answer whether it is admitted or not")
    return errors


def check_operations(named):
    """Check that union, intersection and containment hold exactly what set arithmetic on the states gives."""
    errors = []
    for (one_name, one), (other_name, other) in itertools.product(named, repeat=2):
        pair = f"{one_name} against {other_name}"
        if states_of(one | other) != states_of(one) | states_of(other):
            errors.append(f"{pair}: the union does not hold both")
        if states_of(one & other) != states_of(one) & states_of(other):
            errors.append(f"{pair}: the intersection does not hold what both hold")
        if one.holds(other) != (states_of(other) <= states_of(one)):
            errors.append(f"{pair}: containment disagrees with the states held")
        if bool(one) != bool(states_of(one)):
            errors.append(f"{one_name}: emptiness disagrees with the states held")
    return errors


def check_laws(named):
    """Check the algebra's own identities, which the states cannot show where the alphabet does not reach."""
    errors = []
    for name, subspace in named:
        if subspace | spaces.NOWHERE != subspace or subspace & spaces.EVERYWHERE != subspace:
            errors.append(f"{name}: is changed by the empty union or the whole intersection")
        if subspace & spaces.NOWHERE != spaces.NOWHERE:
            errors.append(f"{name}: meets the empty subspace somewhere")
        if not spaces.EVERYWHERE.holds(subspace) or not subspace.holds(spaces.NOWHERE):
            errors.append(f"{name}: is not between the empty subspace and the whole one")
        if not subspace.holds(subspace):
            errors.append(f"{name}: does not hold itself")
    for (one_name, one), (other_name, other) in itertools.product(named, repeat=2):
        if one | other != other | one or one & other != other & one:
            errors.append(f"{one_name} against {other_name}: the operation is not symmetric")
        if not (one | other).holds(one) or not one.holds(one & other):
            errors.append(f"{one_name} against {other_name}: the union or the intersection is on the wrong side")
    return errors


def check_axes():
    """Check that every axis of a standing is one `where` narrows on, and that the standings are all of them."""
    errors = []
    if len(spaces.STANDINGS) != len(set(spaces.STANDINGS)) or len(spaces.STANDINGS) != 2 ** len(spaces.AXES):
        errors.append(f"{len(spaces.STANDINGS)} standings is not every assignment to {len(spaces.AXES)} axes")
    for axis in spaces.AXES:
        narrowed = spaces.where(**{axis: True})
        if narrowed | spaces.where(**{axis: False}) != spaces.EVERYWHERE:
            errors.append(f"{axis}: its two sides are not the whole space")
        if narrowed & spaces.where(**{axis: False}) != spaces.NOWHERE:
            errors.append(f"{axis}: its two sides meet")
    return errors


def main():
    named = cases()
    errors = check_canonical(named) + check_table() + check_operations(named) + check_laws(named) + check_axes()
    gate.report(
        errors,
        "subspace error(s)",
        f"subspace algebra OK: {len(named)} subspaces over {len(spaces.STANDINGS)} standings",
    )


if __name__ == "__main__":
    main()
