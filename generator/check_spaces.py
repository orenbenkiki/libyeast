# SPDX-License-Identifier: MIT
"""
Check the subspace algebra against the states it describes.

A `SubSpace` is a description, and the states inside that description are its meaning. So this module judges an
operation by enumerating those states over a small alphabet. The alphabet holds the codepoints at the span ends the
cases use. The enumeration adds the end of the stream. An operation passes where its result holds exactly the states
that set arithmetic on the enumerations gives.

A subspace says a single thing per place and says it coalesced. The table hands out a single answer per set of states.
Any form asking for that set gets the same object. The identities of the algebra hold where the alphabet cannot reach to
show them. An axis of a place cuts the whole space into a pair of sides that do not overlap.

This judges the places themselves the same way, and in both directions. The module walks the quantities a place orders
over a wider range than `spaces` walks them. The places must hold exactly the states that walk reaches. A place that no
parse reaches is an enumeration that is not what it claims. A state that no place names is a hole a subspace would say
nothing about. The walk rests on facts about the grammar, and this checks the grammar holds them.

Reports the problems found and exits with a failing status where it finds any.
"""

import itertools
from collections.abc import Sequence

import annotated2ir
import chars
import gate
import ir
import spaces
import validate_grammar

# The alphabet holds the span ends the cases below name. It adds a codepoint inside the letters and a codepoint past the
# printable run. It holds the null codepoint and the top codepoint. It holds the codepoints on either side of the
# surrogates.
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
        ("the end alone", spaces.characters(is_at_end=True)),  # not-prose: The string names a case in the table.
        ("the invalid byte", spaces.characters([(-1, -1)])),
        ("at a line start", spaces.where(is_at_line_start=True)),
        ("not at a line start", spaces.where(is_at_line_start=False)),
        ("indented, after ns-char", spaces.where(is_indented=True, is_after_ns_char=True)),
        ("letters at a line start", letters & spaces.where(is_at_line_start=True)),
        ("printable when indented", printable & spaces.where(is_indented=True)),
        ("a pair of disjoint characters", spaces.characters([(0x41, 0x41), (0x7E, 0x7E)])),
    ]


def _states_of(subspace: spaces.SubSpace) -> set[tuple[spaces.GuardAnswers, int | str]]:
    """The states `subspace` holds over `_ALPHABET`, as a set of `(answer, codepoint or _END_OF_STREAM)` pairs."""
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


# `_FORMS` holds forms of a single set of characters that `spaces.held` reads. A form writes the set whole. A form cuts
# the set in half at a boundary that `merged_spans` closes up. A form puts the spans out of order. A form repeats a
# span. `spaces.held` must hand back the same object across these forms.
_FORMS = (
    ("a run given whole", [(0x41, 0x5A)]),
    ("the same run cut in halves", [(0x41, 0x4F), (0x50, 0x5A)]),
    ("the same run out of order", [(0x50, 0x5A), (0x41, 0x4F)]),
    ("the same run said twice", [(0x41, 0x5A), (0x45, 0x50), (0x41, 0x5A)]),
)


def _check_table() -> list[str]:
    """
    Check that the table hands out a single `spaces._Characters` per set of characters. A caller asking for that set in
    any form gets the same object.

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
    """
    Check ordered pairs of the named subspaces against set arithmetic on their states. A union, an intersection and a
    difference must match that arithmetic. Containment and emptiness must agree with the states held.
    """
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
    """
    Check that `where` with an axis true and `where` with that axis false cover the whole space and do not overlap.
    Check that no guard answer appears twice.
    """
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
    reaches and no place names is a hole, and a subspace would silently say nothing about it. A `spaces.SubSpace` holds
    an entry per index of `spaces.ALL_GUARD_ANSWERS`, and a state outside that tuple has no entry.

    The check enumerates the states itself rather than re-deriving them. The check and `spaces` agree about the
    orderings without sharing a computation.

    The check also asserts the facts that bound the quantities. A line start is column `0`. A consumed length falls
    within its line. A parse at a line start is not after an `ns-char`. A full span leaves the parse mid-line. A check
    that dropped those facts would call the places they exclude a hole.
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


# The match a counted run repeats. A node that counts no turns gives `None`.
_COUNTED: ir.Question[ir.Node | None] = ir.Question(
    "the match a counted run repeats", {ir.RepTree: lambda node: node.item, ir.kinds_other_than(ir.RepTree): None}
)

# The match a `(***)` run repeats. A node of any other kind gives `None`.
_TAKEN_NONE_OR_MORE_TIMES: ir.Question[ir.Node | None] = ir.Question(
    "the match a `(***)` run repeats", {ir.StarTree: lambda node: node.item, ir.kinds_other_than(ir.StarTree): None}
)


def _check_grammar() -> list[str]:
    """
    Check that the grammar holds the facts `_check_guard_answers` assumes of it. A counted run of a single character
    takes no `b-char`. A `(len)` of `(match)` comes right after a `(***)` run in a sequence. That run takes no `b-char`
    and no `c-byte-order-mark`.
    """
    grammar = annotated2ir.load()
    breaks = _spans(grammar, ir.RefCall("b-char", ()))
    marks = _spans(grammar, ir.RefCall("c-byte-order-mark", ()))
    if breaks is None or marks is None:
        raise AssertionError("`b-char` or `c-byte-order-mark` denotes no set of characters")
    excluded = chars.merged_spans(breaks + marks)
    errors = []
    placed = 0
    for name, production in grammar.items():
        for node in validate_grammar.walk(production.body):
            counted = _COUNTED(node)
            if counted is not None and ir.is_one_char(counted, grammar):
                taken = _spans(grammar, counted)
                if taken is None:
                    raise AssertionError(f"{name}: a counted run of a single character denotes no set")
                if chars.intersected_spans(taken, breaks):
                    errors.append(f"{name}: a counted run takes a `b-char`")
            items = ir.ITEMS_IN_ORDER(node) or ()
            for at, item in enumerate(items):
                read = _lengths_read(item)
                if not read:
                    continue
                placed += read
                run = _TAKEN_NONE_OR_MORE_TIMES(items[at - 1]) if at else None
                taken = _spans(grammar, run) if run is not None else None
                if taken is None or chars.intersected_spans(taken, excluded):
                    errors.append(f"{name}: a `(len)` of `(match)` follows no `(***)` run free of breaks and marks")
    everywhere = sum(
        ir.IS_THE_CONSUMED_LENGTH(node) for one in grammar.values() for node in validate_grammar.walk(one.body)
    )
    if placed != everywhere:
        errors.append(f"{everywhere - placed} `(len)` of `(match)` read(s) sit in no sequence")
    return errors


def _spans(grammar: dict[str, ir.Prod], node: ir.Node) -> list[tuple[int, int]] | None:
    """The codepoint spans `node` takes as a single character. `None` where `node` takes no single character."""
    denotation = chars.denote(grammar, node)
    return chars.spans(denotation) if denotation is not None else None


def _lengths_read(node: ir.Node) -> int:
    """
    Answer the reads of a `(len)` of `(match)` inside `node`. The walk counts no read inside a sequence, and that holds
    for `node` itself.
    """
    if ir.ITEMS_IN_ORDER(node) is not None:
        return 0
    return ir.IS_THE_CONSUMED_LENGTH(node) + sum(_lengths_read(child) for child in chars.children(node))


def main() -> None:
    named = _cases()
    errors = (
        _check_canonical(named)
        + _check_table()
        + _check_operations(named)
        + _check_laws(named)
        + _check_axes()
        + _check_guard_answers()
        + _check_grammar()
    )
    gate.report(
        errors,
        "subspace error(s)",
        f"subspace algebra OK: {len(named)} subspaces over {len(spaces.ALL_GUARD_ANSWERS)} answers",
    )


if __name__ == "__main__":
    main()
