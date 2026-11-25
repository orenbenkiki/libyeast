# SPDX-License-Identifier: MIT
"""
Check that a pattern of `_MAY_HOLD_ANY_BYTE` matches a tracked file holding a character past ASCII.

The project names a character past ASCII with an escape. The escape names the codepoint.

`_MAY_HOLD_ANY_BYTE` declares the paths holding such a character as content. This gate checks that declaration for a
stale pattern. A pattern that matches no such file fails here.

`.claude/hooks/ascii_only.py` refuses such a character. `check_prose` runs that checker over the tree through
`refusals_for_edit`. `a-tracked-file-holds-ascii` is the rule.

Usage: `python3 generator/check_ascii.py`.
"""

import fnmatch
import pathlib
import unicodedata

import gate

# The paths holding a character past ASCII as content, and why. A reason comes off the day it stops being true.
_MAY_HOLD_ANY_BYTE: dict[str, str] = {
    "tests/spec/*.input": "fixture input to the parser may contain non-ASCII characters",
    "third_party/*": "vendored file generated elsewhere may contain non-ASCII characters",
    "tests/hooks.json": "probe edit the ascii hook must refuse contains a non-ASCII character",
}


def declaring(path: str) -> set[str]:
    """The patterns of `_MAY_HOLD_ANY_BYTE` that `path` matches."""
    return {pattern for pattern in _MAY_HOLD_ANY_BYTE if fnmatch.fnmatchcase(path, pattern)}


def past_ascii(text: str) -> list[tuple[int, int, str]]:
    """
    `(the line, the column, the character)` per character of `text` past ASCII.

    Public. The `ascii_only` checker reads a text through here.
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


def _does_hold_past_ascii(path: str) -> bool:
    """Whether the file `path` names holds a character past ASCII."""
    whole = pathlib.Path(gate.TREE, path)
    # `git ls-files` lists a file with an unstaged deletion, as https://git-scm.com/docs/git-ls-files says.
    if not whole.exists():
        return False
    with open(whole, encoding="utf-8", errors="replace") as handle:
        return bool(past_ascii(handle.read()))


def main() -> None:
    """Report a pattern of `_MAY_HOLD_ANY_BYTE` that no file holding a character past ASCII needs."""
    covered: set[str] = set()
    for path in gate.tracked_files():
        if _does_hold_past_ascii(path):
            covered |= declaring(path)
    gate.report(
        [
            f"{pattern}: declared in `_MAY_HOLD_ANY_BYTE`, and no file it matches holds a character past ASCII"
            for pattern in sorted(_MAY_HOLD_ANY_BYTE)
            if pattern not in covered
        ],
        "stale declaration(s). Take out a pattern no file needs",
        "ascii: a declared pattern covers a file that holds a character past ASCII",
    )


if __name__ == "__main__":
    main()
