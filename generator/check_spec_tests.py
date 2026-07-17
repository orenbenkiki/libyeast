# SPDX-License-Identifier: MIT
"""Check that libyeast's conformance fixtures are intact and well-formed.

Every `.input` in `tests/spec/` must pair with an `.output` and every `.output` with an `.input`; every filename must
decode to a production the grammar still has, with the parameters it declares and well-formed values; and every output
must parse as the wire format, with marks that chain and markers that balance. This guards the migrated suite against a
fixture orphaned by a grammar change, a hand-edit that broke a name, and an output that is not a token stream — before
the interpreter is ever asked to reproduce one.

The marker rule is what `check_markers` cannot reach: that gate settles the grammar's clean paths, and says nothing
about what an error leaves behind. A fixture of the root is a whole parse and must balance exactly; one of a rule run by
itself may close what its caller would have opened, but neither may leave a marker open.
"""

import os

import annotated2ir
import gate
import ir
import spec_tests
import wire


def main():
    grammar = annotated2ir.load()
    fixtures = spec_tests.load()

    errors = []

    inputs = {os.path.basename(fixture.input_path)[: -len(".input")] for fixture in fixtures}
    outputs = {name[: -len(".output")] for name in os.listdir(spec_tests.TESTS_DIR) if name.endswith(".output")}
    for stem in sorted(inputs - outputs):
        errors.append(f"{stem}.input: has no matching .output")
    for stem in sorted(outputs - inputs):
        errors.append(f"{stem}.output: has no matching .input")

    for fixture in fixtures:
        name = os.path.basename(fixture.input_path)
        reason = spec_tests.is_runnable(fixture, grammar) or spec_tests.bad_value(fixture)
        if reason is not None:
            errors.append(f"{name}: {reason}")
            continue
        if not os.path.exists(fixture.output_path):
            continue  # already reported as unpaired above; there is nothing to read a token stream out of
        tokens = wire.parse(fixture.expected)
        fault = wire.chain_fault(tokens) or wire.marker_fault(tokens, fixture.production == ir.ROOT)
        if fault is not None:
            errors.append(f"{name}: {fault}")

    gate.report(errors, "malformed conformance fixture(s)", f"conformance fixtures: {len(fixtures)} intact")


if __name__ == "__main__":
    main()
