# SPDX-License-Identifier: MIT
"""
Check that the Python wire code map matches the C one.

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
        # `ys_code_char` answers '\0' where the wire spells nothing, and a line is NUL-terminated, so a code written as
        # one would read back as an empty line. Every character being printable is what keeps the two apart, and is what
        # a wire being text means in the first place.
        if in_c is not None and not 0x21 <= ord(in_c) <= 0x7E:
            errors.append(f"{code}: is written {in_c!r}, which is not a printable character a wire can carry")

    gate.report(
        errors, "code(s) that differ between wire.c and wire.py", f"wire code map: {len(wire.CODE_CHAR)} codes agree"
    )


if __name__ == "__main__":
    main()
