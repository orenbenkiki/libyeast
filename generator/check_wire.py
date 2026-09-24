# SPDX-License-Identifier: MIT
"""
Check that the Python wire code map matches the C map.

`wire.CODE_CHAR` gives the character that writes a token code. The authority is the `YS_WIRE` table in `src/wire.c`. The
C parser and the reference share that table. This parses the table and asserts the Python copy matches it exactly. The
error code falls outside the comparison. A grammar annotation emits that code at no point.
"""

import os
import re

import gate
import wire

_WIRE_C = os.path.join(gate.TREE, "src", "wire.c")  # the C copy of the code map this holds `wire.py` to.
_ENTRY = re.compile(r"\[YS_CODE_(\w+)\]\s*=\s*'(.)'")  # A row pairs a code with the character that writes it.


def main() -> None:
    with open(_WIRE_C, encoding="utf-8") as handle:
        table = _ENTRY.findall(handle.read())

    from_c = {}
    for name, character in table:
        code = name.lower().replace("_", "-")
        if code.startswith("error"):
            continue  # an error code is the wire's own and never an annotation's; `YS_CODE_ERROR` names it
        from_c[code] = character

    errors = []
    for code in sorted(set(from_c) | set(wire.CODE_CHAR)):
        in_c = from_c.get(code)
        in_python = wire.CODE_CHAR.get(code)
        if in_c != in_python:
            errors.append(f"{code}: `src/wire.c` says {in_c!r}, `wire.py` says {in_python!r}")
        # `ys_code_char` answers '\0' where the wire writes nothing, and a line is NUL-terminated. A printable character
        # is what keeps the two apart.
        if in_c is not None and not 0x21 <= ord(in_c) <= 0x7E:
            errors.append(f"{code}: the wire writes {in_c!r}, and that is no printable character a wire can hold")

    gate.report(
        errors,
        "code(s) that differ between `src/wire.c` and `wire.py`",
        f"wire code map: {len(wire.CODE_CHAR)} codes agree",
    )


if __name__ == "__main__":
    main()
