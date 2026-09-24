# SPDX-License-Identifier: MIT
"""
Check that a tracked file holds ASCII.

A character past ASCII looks like the ASCII character it replaces. An em-dash looks like a hyphen. A curly quote looks
like an apostrophe. A non-breaking space looks like a space. A column count disagrees with the editor. A grep for the
ASCII form skips the line.

The project names a character past ASCII with an escape. The escape names the codepoint.

`_MAY_HOLD_ANY_BYTE` declares the paths holding such a character as content. This gate holds a declaration in both
directions. A pattern that matches no such file fails here.

`.claude/hooks/ascii_only.py` refuses such a character as an edit writes it. This gate reads the tree.
`a-tracked-file-holds-ascii` is the rule both apply.

Usage: `python3 generator/check_ascii.py`.
"""

import fnmatch
import pathlib
import subprocess
import unicodedata

import gate

# The paths holding a character past ASCII as content, and why. A reason comes off the day it stops being true.
_MAY_HOLD_ANY_BYTE: dict[str, str] = {
    "tests/spec/*.input": "fixture input to the parser may contain non-ASCII characters",
    "third_party/*": "vendored file generated elsewhere may contain non-ASCII characters",
    "tests/hooks.json": "probe edit the ascii hook must refuse contains a non-ASCII character",
}


def _tracked() -> list[str]:
    """The paths git tracks. Sorting settles the order. `SCAN_FILES` in the Makefile names the same paths."""
    named = ["git", "ls-files", "--", ".", ":(exclude)docs"]
    said = subprocess.run(named, cwd=gate.TREE, capture_output=True, text=True, check=True)
    return sorted(said.stdout.splitlines())


def declaring(path: str) -> set[str]:
    """The patterns of `_MAY_HOLD_ANY_BYTE` that `path` matches."""
    return {pattern for pattern in _MAY_HOLD_ANY_BYTE if fnmatch.fnmatchcase(path, pattern)}


def past_ascii(text: str) -> list[tuple[int, int, str]]:
    """
    `(the line, the column, the character)` per character of `text` past ASCII.

    Public. The `ascii_only` hook reads an edit through here, and this gate reads the tree through here.
    """
    return [
        (at, column, character)
        for at, line in enumerate(text.split("\n"), 1)
        for column, character in enumerate(line, 1)
        if ord(character) > 127
    ]


def named_character(character: str) -> str:
    """The codepoint of `character`, and the name Unicode gives it."""
    return f"U+{ord(character):04X} {unicodedata.name(character, 'unnamed')}"


def _past_ascii(path: str) -> list[str]:
    """`file:line:column` and the character, per character of `path` past ASCII."""
    whole = pathlib.Path(gate.TREE, path)
    # git tracks a path the working tree no longer holds, a deletion staged and not yet committed.
    if not whole.exists():
        return []
    with open(whole, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return [f"{path}:{at}:{column}: {named_character(one)}" for at, column, one in past_ascii(text)]


def main() -> None:
    """Report the characters past ASCII. `_MAY_HOLD_ANY_BYTE` declares the paths this gate passes over."""
    faults = []
    covered: set[str] = set()
    for path in _tracked():
        found = _past_ascii(path)
        if not found:
            continue
        patterns = declaring(path)
        if patterns:
            covered |= patterns
            continue
        faults += found
    faults += [
        f"{pattern}: declared in `_MAY_HOLD_ANY_BYTE`, and no file it matches holds a character past ASCII"
        for pattern in sorted(_MAY_HOLD_ANY_BYTE)
        if pattern not in covered
    ]
    gate.report(
        faults,
        "character(s) past ASCII. Write the ASCII the text means, or the escape that names the codepoint",
        "ascii: a tracked file holds ASCII",
    )


if __name__ == "__main__":
    main()
