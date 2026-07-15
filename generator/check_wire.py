# SPDX-License-Identifier: MIT
"""Check that the Python wire code map matches the C one.

`wire.CODE_CHAR` is the character each token code is written as; the authority is `src/wire.c`'s YS_WIRE table, which
the C parser and the reference share. This parses that table and asserts the Python copy is exactly it, minus the three
error codes, which collapse to one wire character no grammar annotation emits. So the interpreter cannot come to write a
code the C parser would write differently.
"""

import os
import re

import gate
import wire

WIRE_C = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "wire.c")
_ENTRY = re.compile(r"\[YS_CODE_(\w+)\]\s*=\s*'(.)'")


def main():
    with open(WIRE_C) as handle:
        table = _ENTRY.findall(handle.read())

    from_c = {}
    for name, character in table:
        code = name.lower().replace("_", "-")
        if code.startswith("error"):
            continue  # the three error codes are one wire character, and never an annotation's code
        from_c[code] = character

    errors = []
    for code in sorted(set(from_c) | set(wire.CODE_CHAR)):
        in_c = from_c.get(code)
        in_python = wire.CODE_CHAR.get(code)
        if in_c != in_python:
            errors.append(f"{code}: wire.c says {in_c!r}, wire.py says {in_python!r}")

    gate.report(
        errors, "code(s) that differ between wire.c and wire.py", f"wire code map: {len(wire.CODE_CHAR)} codes agree"
    )


if __name__ == "__main__":
    main()
