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

# The reference tokenizes an invalid input to these productions through its `recovery`/`unparsed` machinery (rules 185,
# 194, 208, 210 in Reference.bnf) — emitting the parsed prefix and mopping the rest up as unparsed. The spec-faithful
# base grammar has no such machinery, so their invalid fixtures await the error-handling piece, as the error-token ones
# do; a valid input to these productions matches cleanly and is reproduced.
RECOVERY_PRODUCTIONS = frozenset(
    {"s-l+block-indented", "c-l-block-map-implicit-value", "l-explicit-document", "l-any-document"}
)


def _is_pending(fixture):
    """Whether `fixture` awaits the error-handling piece — an error token, or a recovery production's unparsed."""
    if any(token.code == wire.ERROR for token in wire.parse(fixture.expected)):
        return True
    return fixture.is_invalid and fixture.production in RECOVERY_PRODUCTIONS


def main():
    grammar = annotated2ir.load()
    fixtures = [fixture for fixture in spec_tests.load() if interpreter.coverable(fixture.production, grammar)]

    pending = 0
    errors = []
    for fixture in fixtures:
        if _is_pending(fixture):
            pending += 1
            continue
        try:
            tokens = interpreter.run(grammar, fixture.production, fixture.input, fixture.parameters)
            actual = wire.serialize(tokens) if tokens is not None else "(no match)"
        except Exception as error:  # noqa: BLE001 — a crash is a divergence to report, not to abort the gate on
            actual = f"(crash: {type(error).__name__}: {error})"
        if actual != fixture.expected:
            reason = actual if actual.startswith("(") else "output differs from the fixture"
            errors.append(f"{os.path.basename(fixture.input_path)}: {reason}")

    gate.report(
        errors,
        "fixture(s) the interpreter does not reproduce",
        f"interpreter: {len(fixtures) - pending} fixtures reproduced, {pending} awaiting error handling",
    )


if __name__ == "__main__":
    main()
