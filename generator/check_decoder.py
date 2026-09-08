# SPDX-License-Identifier: MIT
"""
Check the committed decoder tables against the grammar.

A set bit of a key agrees with a direct evaluation of the set it names. A literal id collides with no sentinel, and a
key holds the id of its character. A set a run consumes admits no line break. The committed `src/decoder_tables.h` is
exactly what the grammar produces.

Reports the problems found and exits with a failing status where any turn up.
"""

import io

import annotated2ir
import chars
import gate
import grammar2decoder
import ir

_TABLES = grammar2decoder.TABLES  # the header this holds to what the grammar produces.


def _regenerated(model: chars.Model, grammar: dict[str, ir.Prod]) -> str:
    """The header the grammar calls for. The generator lays it out, and this returns the whole file."""
    source = io.StringIO()
    grammar2decoder.emit(source, model, grammar, grammar2decoder.check_groups(model, grammar))
    return source.getvalue()


def _check_keys(model: chars.Model, grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a set bit of a key agrees with a direct evaluation of the set it names.

    Different code computes the keys and the set definitions. This catches a packing bug that a round-trip through the
    generator would reproduce faithfully in both directions. A codepoint per segment is exhaustive. The literals and
    ranges of the grammar build the sets, and a key cannot vary within a segment.
    """
    errors = []
    for index, (name, denotation) in enumerate(model.sets):
        for codepoint in chars.representatives(grammar):
            is_in_key = (model.key(codepoint, 1) & model.set_mask(index)) != 0
            if is_in_key != chars.does_contain(denotation, codepoint):
                errors.append(
                    f"U+{codepoint:04X}: the key says {'' if is_in_key else 'not '}{name}, the grammar says "
                    f"{'not ' if is_in_key else ''}{name}"
                )
    return errors


def _check_consumed_sets(model: chars.Model, grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no character set a run consumes admits a line break.

    `ys_consume_set` reports how many bytes and how many characters a consume covered, and the parser advances its
    column by the latter. That is exact only while a run cannot cross a line. The sets `ir.repeated` answers for exclude
    the line breaks. Those are the sets under a `(***)`. A `(+++)` and a `({n})` hold such a set too, and so does a
    trimmed star. A set that admitted a break would drift the parser's line and column with no test failing. This gate
    checks the invariant rather than assuming it.

    A consume of a single character goes unasked. Such a consume advances a single character, and a break there is a
    break the emitter counts the same way. `b-line-feed` consuming only `LF` is such a consume, and it is no fault.
    """
    errors = []
    by_denotation = {denotation: name for name, denotation in model.sets}

    def visit(node: ir.Node, owner: str) -> None:
        # What repeats is asked of `ir.repeated` rather than of a list of kinds kept here. Such a list would go stale
        # the moment a run is written a new way, and leave that run unchecked.
        repeated = ir.repeated(node)
        if repeated is not None:
            denotation = chars.denote(grammar, repeated)
            if denotation is not None and denotation[0] != "literal":
                for codepoint in (0x0A, 0x0D):
                    if chars.does_contain(denotation, codepoint):
                        name = by_denotation.get(denotation, "an unnamed set")
                        errors.append(
                            f"{owner}: consumes {name}. that set admits U+{codepoint:04X}, and a consume could then "
                            f"cross a line."
                        )
        for child in chars.children(node):
            visit(child, owner)

    for name, production in grammar.items():
        visit(production.body, name)
    return errors


def _check_literals(model: chars.Model) -> list[str]:
    """
    Check that no literal id collides with a sentinel, and that a key holds the id of its character.

    `chars.Model` numbers the literals while enumerating them. The ids are therefore distinct by construction. The
    collision and the held id are what the numbering cannot settle.
    """
    errors = []
    for codepoint, literal_id in model.literal_ids.items():
        if literal_id in (model.lit_eof, model.lit_invalid):
            errors.append(f"U+{codepoint:04X}: literal id {literal_id} collides with a sentinel")
        if (model.key(codepoint, 1) & ((1 << chars.LIT_BITS) - 1)) != literal_id:
            errors.append(f"U+{codepoint:04X}: literal id {literal_id} is not what its key holds")
    return errors


def main() -> None:
    grammar = annotated2ir.load()
    model = chars.Model(grammar)
    errors = _check_keys(model, grammar) + _check_literals(model) + _check_consumed_sets(model, grammar)
    with open(_TABLES, encoding="utf-8") as handle:
        if handle.read() != _regenerated(model, grammar):
            errors.append(f"{_TABLES} is stale; regenerate it with `make regen`")
    gate.report(
        errors, "decoder table error(s)", f"decoder tables OK: {len(model.literals)} literals, {len(model.sets)} sets"
    )


if __name__ == "__main__":
    main()
