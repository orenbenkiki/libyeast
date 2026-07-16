# SPDX-License-Identifier: MIT
"""Check that the interpreter reproduces every conformance fixture it covers.

For each fixture whose production rests on only the nodes the interpreter supports, run the production and compare its
token stream to the fixture's, byte for byte. This is where libyeast's grammar is proved to emit the reference's tokens,
one production at a time — the malformed inputs included, now that a failed cut becomes an error and the unparsed
recovery.
"""

import os

import annotated2ir
import gate
import interpreter
import spec_tests
import wire


def main():
    grammar = annotated2ir.load()
    fixtures = [fixture for fixture in spec_tests.load() if interpreter.coverable(fixture.production, grammar)]

    errors = []
    for fixture in fixtures:
        try:
            arguments = spec_tests.arguments(fixture, grammar)
            tokens = interpreter.run(grammar, fixture.production, fixture.input, arguments)
            actual = wire.serialize(tokens) if tokens is not None else "(no match)"
        except Exception as error:  # noqa: BLE001 — a crash is a divergence to report, not to abort the gate on
            actual = f"(crash: {type(error).__name__}: {error})"
        if actual != fixture.expected:
            reason = actual if actual.startswith("(") else "output differs from the fixture"
            errors.append(f"{os.path.basename(fixture.input_path)}: {reason}")

    gate.report(
        errors, "fixture(s) the interpreter does not reproduce", f"interpreter: {len(fixtures)} fixtures reproduced"
    )


if __name__ == "__main__":
    main()
