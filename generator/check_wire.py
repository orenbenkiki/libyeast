# SPDX-License-Identifier: MIT
"""
Check that the Python wire code map matches the C map.

`wire.CODE_CHAR` gives the character that writes a token code. `wire.ERROR` gives the character of the error code. The
authority is the `YS_WIRE` table in `src/wire.c`. This parses the table and asserts the Python copy matches it exactly.
"""

import os
import re

import gate
import wire

_WIRE_C = os.path.join(
    gate.TREE, "src", "wire.c"
)  # the path of `src/wire.c`. That file holds the C code map, and `wire.py` must match it.
_ENTRY = re.compile(r"\[YS_CODE_(\w+)\]\s*=\s*'(.)'")  # A row pairs a code with the character that writes it.

# The Python copy of the code map, with the error code beside the token codes.
_PYTHON = {**wire.CODE_CHAR, "error": wire.ERROR}


def main() -> None:
    with open(_WIRE_C, encoding="utf-8") as handle:
        table = _ENTRY.findall(handle.read())

    from_c = {name.lower().replace("_", "-"): character for name, character in table}

    errors = []
    for code in sorted(set(from_c) | set(_PYTHON)):
        in_c = from_c.get(code)
        in_python = _PYTHON.get(code)
        if in_c != in_python:
            errors.append(f"{code}: `src/wire.c` says {in_c!r}, `wire.py` says {in_python!r}")
        # `ys_code_char` answers '\0' where the wire writes nothing, and a line is NUL-terminated. A printable character
        # tells a written code from the NUL that ends the line.
        if in_c is not None and not 0x21 <= ord(in_c) <= 0x7E:
            errors.append(f"{code}: the wire writes {in_c!r}, and that is no printable character a wire can hold")

    gate.report(
        errors,
        "code(s) that differ between `src/wire.c` and `wire.py`",
        f"wire code map: {len(_PYTHON)} codes agree",
    )


if __name__ == "__main__":
    main()
