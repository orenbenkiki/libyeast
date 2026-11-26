# SPDX-License-Identifier: MIT
"""
Check that libyeast's conformance fixtures are intact and well-formed.

An `.input` in `tests/spec/` pairs with an `.output`, and an `.output` with an `.input`. A filename decodes to a
production the grammar still has. The filename supplies the parameters that production declares, bar the exemptions of
`spec_tests.runnable_fault`. The filename gives well-formed values. The check reads an output as the wire format. The
marks of an output chain. The markers of an output balance.

A fixture whose name says invalid holds an invalid input. The production refuses that input or stops short of the end.
Either way the match fails.

The check guards the fixtures against a grammar change that orphans a fixture. The check catches a hand-edit that broke
a name. The check catches an output whose marks do not chain or whose markers do not balance. The check catches a name
that claims what the fixture does not show. The `verify` goal lists `verify-fixtures` before `verify-grammar`. The
`verify-grammar` gate runs `verify-grammar-base`. `verify-grammar-base` runs the interpreter over the fixtures.

`check_markers` reads the grammar. `check_markers` cannot reach the markers of a fixture. `check_markers` checks a
recovery too. That gate says nothing of the tokens an error leaves behind.

A fixture of the root is a whole parse. Its markers balance exactly. A fixture of a rule run outside the root may close
a marker the caller opened. Neither may leave a marker open.
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

    inputs = {os.path.basename(loaded_fixture.input_path)[: -len(".input")] for loaded_fixture in fixtures}
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
            continue  # Skip a fixture the loop above reported as unpaired.
        tokens = wire.parse(fixture.expected)
        fault = wire.chain_fault(tokens) or wire.marker_fault(tokens, fixture.production == ir.ROOT)
        if fault is None and fixture.is_invalid and wire.is_clean(tokens, len(fixture.input)):
            fault = "the name says invalid, and the production matches the whole input cleanly"
        if fault is not None:
            errors.append(f"{name}: {fault}")

    gate.report(errors, "malformed conformance fixture(s)", f"conformance fixtures: {len(fixtures)} intact")


if __name__ == "__main__":
    main()
