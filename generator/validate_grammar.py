# SPDX-License-Identifier: MIT
"""Static validation of the grammar IR.

Loads libyeast's grammar (via `annotated2ir`) and checks that:
  * every referenced production exists,
  * every reference passes as many arguments as the target declares,
  * every production is referenced, so nothing is dead — except the `UNREFERENCED` ones, each of which must exist, and
  * every character the parser consumes lies within a token annotation.
Reports every problem found and exits non-zero if there are any.
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chars  # noqa: E402
import ir  # noqa: E402
import annotated2ir  # noqa: E402

ROOT = "l-yaml-stream"
ZERO_WIDTH = (ir.Look, ir.NegLook, ir.LookBehind, ir.ExcludeAt)


def walk(node):
    """Yield `node` and every IR node nested within it."""
    yield node
    if dataclasses.is_dataclass(node):
        for field in dataclasses.fields(node):
            yield from walk_value(getattr(node, field.name))


def walk_value(value):
    if dataclasses.is_dataclass(value):
        yield from walk(value)
    elif isinstance(value, tuple):
        for item in value:
            yield from walk_value(item)


# The productions nothing references: the stream root, and c-reserved — the reserved characters (§5.4), which the
# grammar defines but never uses. The indicator characters (§5.3) each have a production too, and those *are*
# referenced: libyeast reaches an indicator through the production that names it, so a token annotation has somewhere
# to attach.
UNREFERENCED = frozenset({"l-yaml-stream", "c-reserved"})


def consumed(node, is_annotated, references):
    """Yield, for each character `node` consumes, whether a token annotation covers it; collect the references reached.

    A lookahead consumes nothing and emits nothing, so what is inside one is neither counted nor followed. A `(---)`
    matches one character, so it counts as one; the characters it subtracts are operands, not matches.
    """
    if isinstance(node, ir.Token):
        yield from consumed(node.item, True, references)
    elif isinstance(node, ZERO_WIDTH):
        return
    elif isinstance(node, (ir.Char, ir.Range, ir.Diff)):
        yield is_annotated
    elif isinstance(node, ir.Ref):
        references.append((node.name, is_annotated))
    elif isinstance(node, ir.Case):
        for _value, branch in node.branches:
            yield from consumed(branch, is_annotated, references)
    else:
        for child in chars.children(node):
            yield from consumed(child, is_annotated, references)


def check_annotated(grammar):
    """Every character the parser consumes must lie within a token annotation.

    A character consumed outside one is given the code `unparsed` — what the parser says about input it could not
    parse — and would reach the caller as a token saying so. On the success path that is always an annotation someone
    forgot, and nothing else would catch it until the token stream was compared against the reference.

    A production is reached with an annotation around it, or without one, or both; whichever it is propagates from the
    root through every reference, until it settles.
    """
    reached = {name: set() for name in grammar}
    reached[ROOT].add(False)
    is_settled = False
    while not is_settled:
        is_settled = True
        for name, production in grammar.items():
            for is_annotated in list(reached[name]):
                references = []
                list(consumed(production.body, is_annotated, references))
                for target, is_covered in references:
                    if target in reached and is_covered not in reached[target]:
                        reached[target].add(is_covered)
                        is_settled = False

    errors = []
    for name, production in sorted(grammar.items()):
        for is_annotated in reached[name]:
            if not all(consumed(production.body, is_annotated, [])):
                errors.append(f"{name}: consumes a character outside any token annotation, which would emit `unparsed`")
                break
    return errors


def validate(grammar):
    """Return a list of human-readable validation errors (empty if the grammar is clean)."""
    errors = check_annotated(grammar)
    referenced = set()
    for name, prod in grammar.items():
        for ref in (n for n in walk(prod.body) if isinstance(n, ir.Ref)):
            referenced.add(ref.name)
            if ref.name not in grammar:
                errors.append(f"{name}: reference to undefined production {ref.name!r}")
            elif len(ref.args) != len(grammar[ref.name].params):
                expected = len(grammar[ref.name].params)
                errors.append(f"{name}: {ref.name!r} called with {len(ref.args)} argument(s), expects {expected}")
    for name in sorted(set(grammar) - referenced - UNREFERENCED):
        errors.append(f"{name}: production is defined but never referenced")
    for name in sorted(UNREFERENCED - set(grammar)):
        errors.append(f"{name}: listed as never referenced but no such production")
    return errors


def main():
    grammar = annotated2ir.load(sys.argv[1] if len(sys.argv) > 1 else annotated2ir.DEFAULT_GRAMMAR)
    errors = validate(grammar)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} grammar validation error(s)", file=sys.stderr)
        sys.exit(1)
    print(f"grammar validation OK: {len(grammar)} productions")


if __name__ == "__main__":
    main()
