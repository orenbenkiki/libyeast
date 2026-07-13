# SPDX-License-Identifier: MIT
"""Static validation of the grammar IR.

Loads the vendored grammar (via `spec2grammar`) and checks that:
  * every referenced production exists,
  * every reference passes as many arguments as the target declares, and
  * every production is referenced, so nothing is dead — except the `UNREFERENCED` ones, each of which must exist.
Reports every problem found and exits non-zero if there are any.
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir  # noqa: E402
import spec2grammar  # noqa: E402


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


# The productions nothing references: the stream root, and the named indicator (§5.3) and reserved (§5.4)
# characters, which the spec defines but never uses — every use site spells the character out.
UNREFERENCED = frozenset(
    {
        "l-yaml-stream",
        "c-alias",
        "c-anchor",
        "c-collect-entry",
        "c-comment",
        "c-directive",
        "c-double-quote",
        "c-escape",
        "c-folded",
        "c-literal",
        "c-mapping-end",
        "c-mapping-key",
        "c-mapping-start",
        "c-mapping-value",
        "c-reserved",
        "c-sequence-end",
        "c-sequence-entry",
        "c-sequence-start",
        "c-single-quote",
        "c-tag",
    }
)


def validate(grammar):
    """Return a list of human-readable validation errors (empty if the grammar is clean)."""
    errors = []
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
    grammar = spec2grammar.load(sys.argv[1] if len(sys.argv) > 1 else spec2grammar.DEFAULT_SPEC)
    errors = validate(grammar)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} grammar validation error(s)", file=sys.stderr)
        sys.exit(1)
    print(f"grammar validation OK: {len(grammar)} productions")


if __name__ == "__main__":
    main()
