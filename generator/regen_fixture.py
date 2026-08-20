# SPDX-License-Identifier: MIT
"""
Freeze one conformance fixture's token stream: run the interpreter over its input and write the `.output` beside it.

A fixture holds what the base grammar emits, token for token, and what it holds has to come from the interpreter rather
than from a hand. An indent or a white token carries its characters as its text — trailing spaces among them — and a
stream typed out loses them where nothing shows they were there. So the one way to author a fixture is to run it.

Named one at a time and never swept. Rewriting every fixture would re-freeze whatever the interpreter emits today, which
is the oracle answering to the thing it judges; naming one says the change to it is meant. Where the fixture already
holds a stream this says how the new one differs, so a re-freeze is read before it is committed.

Run by hand — `python3 generator/regen_fixture.py <stem>`, the stem being the filename without its extension — where a
fixture is being written or a fault in one is being fixed. No gate runs it: what the gate does with a fixture is hold
the grammar to it, and a target that rewrites one is the opposite of that standing where the gate can reach it.
"""

import os
import sys

import annotated2ir
import gate
import interpreter
import spec_tests
import wire


def emitted(fixture, grammar):
    """The token stream `fixture`'s input makes, as the wire text a `.output` holds."""
    arguments = spec_tests.arguments(fixture, grammar)
    return wire.serialize(interpreter.run(grammar, fixture.production, fixture.input, arguments))


def differences(was, now):
    """How the frozen stream differs from the emitted one, as lines naming each, or none where they agree."""
    lines = []
    old, new = was.split("\n"), now.split("\n")
    for index in range(max(len(old), len(new))):
        before = old[index] if index < len(old) else "(nothing)"
        after = new[index] if index < len(new) else "(nothing)"
        if before != after:
            lines.append(f"    line {index + 1}: {before!r} becomes {after!r}")
    return lines


def frozen(stem):
    """
    Write the fixture named `stem`'s output from the interpreter. `stem` is the filename without its extension.

    Reports what the fixture holds that the interpreter no longer emits, so a re-freeze says what it gave up.
    """
    wanted = [fixture for fixture in spec_tests.load() if _stem(fixture) == stem]
    if not wanted:
        return [f"{stem}: no fixture of that name in {spec_tests.TESTS_DIR}"]
    (fixture,) = wanted
    now = emitted(fixture, annotated2ir.load())
    was = fixture.expected if os.path.exists(fixture.output_path) else None
    with open(fixture.output_path, "w", encoding="utf-8") as handle:
        handle.write(now)
    changed = differences(was, now) if was is not None else []
    ir_say = f"froze {os.path.basename(fixture.output_path)}: {now.count(chr(10))} line(s)"
    print(f"{ir_say}{'' if was is not None else ', new'}")
    for line in changed:
        print(line)
    return []


def _stem(fixture):
    """A fixture's name without its extension, which is what names it on the command line."""
    return os.path.basename(fixture.input_path)[: -len(".input")]


def main():
    if len(sys.argv) != 2:
        gate.report([f"usage: {os.path.basename(sys.argv[0])} <fixture-stem>"], "argument error(s)", "")
    gate.report(frozen(sys.argv[1]), "fixture error(s)", "fixture frozen")


if __name__ == "__main__":
    main()
