# SPDX-License-Identifier: MIT
"""
Check that the interpreter reproduces the conformance fixtures it covers.

Take a fixture whose production rests on the nodes the interpreter supports, and run that production. Compare the token
stream the run emits against the stream the fixture froze. The comparison goes byte by byte.

This is where libyeast's grammar proves it emits the reference's tokens, a production at a time. The malformed inputs
are among them. A failed cut writes an error token and hands the remainder to the unparsed recovery. A fixture can hold
that stream like any other.
"""

import os

import annotated2ir
import gate
import interpreter
import ir
import spec_tests
import wire


def reproduced(grammar: dict[str, ir.Prod], fixtures: list[spec_tests.Fixture] | None = None) -> list[str]:
    """
    The fixtures `grammar` does not reproduce token for token, as error strings. Empty where the grammar reproduces the
    whole set.

    Takes the grammar as an argument the way the interpreter does. A structurally-transformed grammar emits the same
    token streams as the base grammar. The fixtures are the base's frozen output. Reproducing them means the transform
    changed no token.
    """
    if fixtures is None:
        fixtures = spec_tests.load()
    ir.say(f"        {len(fixtures)} fixture(s), spread over the cores")
    held = gate.spread(_run_one, (grammar,), fixtures)
    return [error for error in held if error is not None]


def _run_one(held: tuple[dict[str, ir.Prod]], fixture: spec_tests.Fixture) -> str | None:
    """The way `fixture` differs from the stream it froze. `None` comes back where the pair agree."""
    (grammar,) = held
    try:
        arguments = spec_tests.arguments(fixture, grammar)
        tokens = interpreter.run(grammar, fixture.production, fixture.input, arguments)
        actual = wire.serialize(tokens)
    except Exception as error:  # noqa: BLE001  failure-is-reported: as `actual` below  # pylint: disable=W0718
        actual = f"(crash: {type(error).__name__}: {error})"
    if actual == fixture.expected:
        return None
    reason = actual if actual.startswith("(") else "output differs from the fixture"
    return f"{os.path.basename(fixture.input_path)}: {reason}"


def main() -> None:
    fixtures = spec_tests.load()
    errors = reproduced(annotated2ir.load(), fixtures)
    gate.report(
        errors, "fixture(s) the interpreter does not reproduce", f"interpreter: {len(fixtures)} fixtures reproduced"
    )


if __name__ == "__main__":
    main()
