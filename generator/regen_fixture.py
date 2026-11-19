# SPDX-License-Identifier: MIT
"""
Freeze a conformance fixture's token stream. Run the interpreter over the fixture's input and write the `.output` beside
that input.

A fixture holds the tokens the base grammar emits.

This freezes a fixture the caller names. This sweeps no set of fixtures. A sweep would re-freeze the fixtures from the
interpreter they judge. This prints how a new stream differs from the stream the fixture already holds. The author reads
a re-freeze before committing it.

Somebody writing a fixture or fixing a fault in a fixture runs this by hand, as `python3 generator/regen_fixture.py
<stem>`. The stem is the filename without its extension.

The gate in the `Makefile` holds the grammar to the fixtures and runs this script at no point.
"""

import os
import sys

import annotated2ir
import gate
import interpreter
import ir
import spec_tests
import wire


def _emitted(fixture: spec_tests.Fixture, grammar: dict[str, ir.Prod]) -> str:
    """
    `fixture`'s input makes a token stream. `_emitted` returns that stream as the wire text a `.output` file holds.
    """
    arguments = spec_tests.arguments(fixture, grammar)
    return wire.serialize(interpreter.run(grammar, fixture.production, fixture.input, arguments))


def _differences(was: str, now: str) -> list[str]:
    """
    The function reports a line where the frozen stream and the emitted stream differ. A report names the line number,
    the frozen text and the emitted text. The list is empty where the frozen stream and the emitted stream agree.
    """
    lines = []
    old, new = was.split("\n"), now.split("\n")
    for index in range(max(len(old), len(new))):
        before = old[index] if index < len(old) else "(nothing)"
        after = new[index] if index < len(new) else "(nothing)"
        if before != after:
            lines.append(f"    line {index + 1}: {before!r} becomes {after!r}")
    return lines


def _frozen(stem: str) -> list[str]:
    """
    Write the interpreter's output to the fixture named `stem`. `stem` is the filename without its extension.

    Reports where what the fixture holds differs from what the interpreter emits. A re-freeze says what it gave up.
    """
    wanted = [fixture for fixture in spec_tests.load() if _stem(fixture) == stem]
    if not wanted:
        return [f"{stem}: no fixture of that name in {spec_tests.TESTS_DIR}"]
    (fixture,) = wanted
    now = _emitted(fixture, annotated2ir.load())
    was = fixture.expected if os.path.exists(fixture.output_path) else None
    with open(fixture.output_path, "w", encoding="utf-8") as handle:
        handle.write(now)
    changed = _differences(was, now) if was is not None else []
    said = f"froze {os.path.basename(fixture.output_path)}: {now.count(chr(10))} line(s)"
    print(f"{said}{'' if was is not None else ', new'}")
    for line in changed:
        print(line)
    return []


def _stem(fixture: spec_tests.Fixture) -> str:
    """Returns a fixture's name without its extension. The command line uses that name to select the fixture."""
    return os.path.basename(fixture.input_path)[: -len(".input")]


def main() -> None:
    if len(sys.argv) != 2:
        gate.report([f"usage: {os.path.basename(sys.argv[0])} <fixture-stem>"], "argument error(s)", "")
    gate.report(_frozen(sys.argv[1]), "fixture error(s)", "fixture frozen")


if __name__ == "__main__":
    main()
