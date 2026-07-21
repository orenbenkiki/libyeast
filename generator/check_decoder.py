# SPDX-License-Identifier: MIT
"""
Check the committed decoder tables against the grammar.

Verifies that every bit of every key agrees with a direct evaluation of the set it stands for, and that the committed
`src/decoder_tables.h` is exactly what the grammar produces. Reports every problem found and exits non-zero if there are
any.
"""

import io

import chars
import grammar2decoder
import ir
import annotated2ir
import gate

TABLES = grammar2decoder.TABLES


def regenerated(model, grammar):
    """The header as the grammar says it should be. The generator lays it out itself, so this is the whole file."""
    source = io.StringIO()
    grammar2decoder.emit(source, model, grammar, grammar2decoder.check_groups(model, grammar))
    return source.getvalue()


def check_keys(model, grammar):
    """
    Every set bit of every key must agree with a direct evaluation of the set it stands for.

    The keys and the set definitions are computed by different code, so this catches a packing bug that a round-trip
    through the generator alone would reproduce faithfully in both directions. One codepoint per segment is exhaustive:
    the sets are built from the grammar's own literals and ranges, so a key cannot vary within a segment.
    """
    errors = []
    for index, (name, denotation) in enumerate(model.sets):
        for codepoint in chars.representatives(grammar):
            is_in_key = (model.key(codepoint, 1) & model.set_mask(index)) != 0
            if is_in_key != chars.contains(denotation, codepoint):
                errors.append(
                    f"U+{codepoint:04X}: the key says {'' if is_in_key else 'not '}{name}, the grammar says "
                    f"{'not ' if is_in_key else ''}{name}"
                )
    return errors


def check_scanned_sets(model, grammar):
    """
    No character set the grammar scans in a run may admit a line break.

    `ys_scan_set` reports how many bytes and how many characters a run covered, and the parser advances its column by
    the latter. That is exact only while a run cannot cross a line — which holds because every set under a `(***)`,
    `(+++)` or `({n})` excludes the line breaks. Were one ever not to, the parser's line and column would drift with no
    test failing, so the invariant is checked rather than assumed.
    """
    errors = []
    by_denotation = {denotation: name for name, denotation in model.sets}

    def visit(node, owner):
        if isinstance(node, (ir.Star, ir.Plus, ir.Rep)):
            denotation = chars.denote(grammar, node.item)
            if denotation is not None and denotation[0] != "literal":
                for codepoint in (0x0A, 0x0D):
                    if chars.contains(denotation, codepoint):
                        name = by_denotation.get(denotation, "an unnamed set")
                        errors.append(
                            f"{owner}: scans {name}, which admits U+{codepoint:04X} — a run could cross a line"
                        )
        for child in chars.children(node):
            visit(child, owner)

    for name, production in grammar.items():
        visit(production.body, name)
    return errors


def check_literals(model):
    """Every named character must have a distinct id, and no id may collide with a sentinel."""
    errors = []
    for codepoint, literal_id in model.literal_ids.items():
        if literal_id in (model.lit_eof, model.lit_invalid):
            errors.append(f"U+{codepoint:04X}: literal id {literal_id} collides with a sentinel")
        if (model.key(codepoint, 1) & ((1 << chars.LIT_BITS) - 1)) != literal_id:
            errors.append(f"U+{codepoint:04X}: literal id {literal_id} is not what its key carries")
    return errors


def main():
    grammar = annotated2ir.load()
    model = chars.Model(grammar)
    errors = check_keys(model, grammar) + check_literals(model) + check_scanned_sets(model, grammar)
    with open(TABLES, encoding="utf-8") as handle:
        if handle.read() != regenerated(model, grammar):
            errors.append(f"{TABLES} is stale; regenerate it with `make regen`")
    gate.report(
        errors, "decoder table error(s)", f"decoder tables OK: {len(model.literals)} literals, {len(model.sets)} sets"
    )


if __name__ == "__main__":
    main()
