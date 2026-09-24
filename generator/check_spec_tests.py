# SPDX-License-Identifier: MIT
"""
Check that libyeast's conformance fixtures are intact and well-formed.

An `.input` in `tests/spec/` pairs with an `.output`, and an `.output` with an `.input`. A filename decodes to a
production the grammar still has, with the parameters it declares and well-formed values. An output parses as the wire
format, with marks that chain and markers that balance.

A fixture whose name calls its input invalid holds an invalid input. The production refuses that input, or stops short
of the end. Either way the match fails.

This guards the migrated suite against a fixture orphaned by a grammar change. The same guard covers a hand-edit that
broke a name. It covers an output that is not a token stream. It covers a name that claims what the fixture does not
show. The guard runs before anybody asks the interpreter to reproduce a fixture.

`check_markers` cannot reach the marker rule. That gate settles the grammar's clean paths. That gate says nothing of the
tokens an error leaves behind.

A fixture of the root is a whole parse and balances exactly. A fixture of a rule run outside the root may close what its
caller would have opened. Neither may leave a marker open.
"""

import os

import annotated2ir
import gate
import ir
import spec_tests
import wire


def main() -> None:
    grammar = annotated2ir.load()
    fixtures = spec_tests.load()

    errors = []

    inputs = {os.path.basename(fixture.input_path)[: -len(".input")] for fixture in fixtures}
    outputs = {path.name[: -len(".output")] for path in gate.named_in(spec_tests.TESTS_DIR, ".output")}
    for stem in sorted(inputs - outputs):
        errors.append(f"the input of {stem} has no matching output")
    for stem in sorted(outputs - inputs):
        errors.append(f"the output of {stem} has no matching input")

    for fixture in fixtures:
        name = os.path.basename(fixture.input_path)
        reason = spec_tests.runnable_fault(fixture, grammar) or spec_tests.bad_value(fixture)
        if reason is not None:
            errors.append(f"{name}: {reason}")
            continue
        if not os.path.exists(fixture.output_path):
            continue  # already reported as unpaired above; there is nothing to read a token stream out of
        tokens = wire.parse(fixture.expected)
        fault = wire.chain_fault(tokens) or wire.marker_fault(tokens, fixture.production == ir.ROOT)
        if fault is None and fixture.is_invalid and wire.is_clean(tokens, len(fixture.input)):
            fault = "the name says invalid, and the production matches the whole input cleanly"
        if fault is not None:
            errors.append(f"{name}: {fault}")

    gate.report(errors, "malformed conformance fixture(s)", f"conformance fixtures: {len(fixtures)} intact")


if __name__ == "__main__":
    main()
