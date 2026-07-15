# SPDX-License-Identifier: MIT
"""Check that the interpreter reproduces every conformance fixture it covers.

For each fixture whose production rests on only the nodes the interpreter supports, run the production and compare its
token stream to the fixture's, byte for byte. This is where libyeast's grammar is proved to emit the reference's tokens,
one production at a time; a fixture whose output carries an error token awaits the error-handling piece — the
interpreter cannot emit one yet — and is counted, not run.
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

    pending = 0
    errors = []
    for fixture in fixtures:
        if any(token.code == wire.ERROR for token in wire.parse(fixture.expected)):
            pending += 1  # its output carries an error token, and emitting errors awaits the error-handling piece
            continue
        tokens = interpreter.run(grammar, fixture.production, fixture.input)
        actual = wire.serialize(tokens) if tokens is not None else "(no match)"
        if actual != fixture.expected:
            errors.append(f"{os.path.basename(fixture.input_path)}: interpreter output differs from the fixture")

    gate.report(
        errors,
        "fixture(s) the interpreter does not reproduce",
        f"interpreter: {len(fixtures) - pending} fixtures reproduced, {pending} awaiting error handling",
    )


if __name__ == "__main__":
    main()
