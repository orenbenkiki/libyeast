# SPDX-License-Identifier: MIT
r"""
The yeast wire format. Python writes it here.

A token stream has a position comment and a code line per token. The comment is a hash followed by `B: <byte>` and `C:
<char>` and `L: <line>` and `c: <column>`. Then comes a code character, and then the token's text escaped by codepoint.

This is the same format `src/wire.c` reads and writes, and the format the conformance fixtures are in. The interpreter
serializes its tokens into this format for a diff against the fixtures.

The escaping follows `src/wire.c` exactly. A printable ASCII byte other than a backslash comes out as itself. Anything
else becomes `\xXX` or `\uXXXX` or `\UXXXXXXXX`. The hex comes out lower-case.

Marks advance a byte by its UTF-8 length, a character by a single step, and a line at a break. A break is CR, LF, or CR
LF together. A line resets the column. That follows the line counting the reference does.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ERROR = "!"  # the wire's error code. A malformed document takes it.

# The wire character that writes a token code. The grammar names the code, and `src/wire.c`'s YS_WIRE table gives the
# character. `check_wire.py` gates this copy against that table, and the pair cannot drift. `_CHAR_CODE` reads a wire
# back.
CODE_CHAR = {
    "bom": "U",
    "text": "T",
    "meta": "t",
    "break": "b",
    "line-feed": "L",
    "line-fold": "l",
    "indicator": "I",
    "white": "w",
    "indent": "i",
    "directives-end": "K",
    "document-end": "k",
    "begin-escape": "E",
    "end-escape": "e",
    "begin-comment": "C",
    "end-comment": "c",
    "begin-directive": "D",
    "end-directive": "d",
    "begin-tag": "G",
    "end-tag": "g",
    "begin-handle": "H",
    "end-handle": "h",
    "begin-anchor": "A",
    "end-anchor": "a",
    "begin-properties": "P",
    "end-properties": "p",
    "begin-alias": "R",
    "end-alias": "r",
    "begin-scalar": "S",
    "end-scalar": "s",
    "begin-sequence": "Q",
    "end-sequence": "q",
    "begin-mapping": "M",
    "end-mapping": "m",
    "begin-pair": "X",
    "end-pair": "x",
    "begin-node": "N",
    "end-node": "n",
    "begin-document": "O",
    "end-document": "o",
    "begin-stream": "Y",
    "end-stream": "y",
    "unparsed-text": "-",
    "unparsed-break": ".",
    "unparsed-invalid": "~",
    "detected": "$",
}

# `CODE_CHAR` read the other way. A character on a wire maps to the code it writes.
_CHAR_CODE = {character: code for code, character in CODE_CHAR.items()}

# A codepoint that writes a line break. A carriage return followed by a line feed is a single break.
CARRIAGE_RETURN = 0x0D
LINE_FEED = 0x0A  # the other codepoint. A line feed makes a break, or it closes a carriage return.
# the mark the stream may open with. A consume naming that mark takes no column for it.
BYTE_ORDER_MARK = 0xFEFF

# The first line of a wire token. It writes the offsets of the token's position.
_POSITION = re.compile(r"^# B: (\d+), C: (\d+), L: (\d+), c: (\d+)$")
# an escape. `\xXX`, `\uXXXX` and `\UXXXXXXXX` are the widths.
_ESCAPE = re.compile(r"\\x[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8}")


@dataclass(frozen=True)
class Mark:
    """A position in the input. The byte and codepoint offsets. The line counts from `1` and the column from `0`."""

    byte: int
    char: int
    line: int
    column: int


@dataclass(frozen=True)
class Token:
    """A wire token. The code character, the start mark, and the text escaped as the wire writes it."""

    code: str
    start: Mark
    text: str  # escaped, as on the wire. An error holds its message. A leaf holds its escaped input.


def parse(text: str) -> list[Token]:
    """Parse a wire token stream into a list of tokens."""
    lines = text.split("\n")
    tokens = []
    index = 0
    while index < len(lines):
        position = _POSITION.match(lines[index])
        if position is None:
            index += 1
            continue
        byte, char, line, column = (int(group) for group in position.groups())
        code_line = lines[index + 1] if index + 1 < len(lines) else ""
        tokens.append(Token(code_line[:1], Mark(byte, char, line, column), code_line[1:]))
        index += 2
    return tokens


def serialize(tokens: Iterable[Token]) -> str:
    """
    Serialize tokens back into a wire token stream. A trailing newline follows a token, as the reference writes them.
    """
    out = []
    for token in tokens:
        mark = token.start
        # not-prose: the record header of a wire token stream, as `tests/spec/*.output` holds it
        out.append(f"# B: {mark.byte}, C: {mark.char}, L: {mark.line}, c: {mark.column}\n{token.code}{token.text}\n")
    return "".join(out)


def chain_fault(tokens: Iterable[Token]) -> str | None:
    """
    Return a single-line reason the tokens' marks do not chain, or None.

    Contiguous leaf tokens must chain. A token's start plus its text lands on the next token's start. The byte and
    character offsets chain. The column falls outside that guarantee. A token may legitimately re-open a line, and its
    start column need not follow. An error token spans no input, and this skips it.
    """
    previous = None
    for token in tokens:
        if token.code == ERROR:
            continue
        if previous is not None:
            end = _advance(previous.start, previous.text, previous.code)
            if token.start.byte != end.byte or token.start.char != end.char:
                return f"token at {token.start} does not follow the previous token's end {end}"
        previous = token
    return None


def is_clean(tokens: Sequence[Token], size: int) -> bool:
    """
    Whether `tokens` are a clean match of the whole `size` bytes. That means no error, and no byte left unaccounted for.

    A production that rejects its input says so with an error token. A production that stops early says so by leaving
    the last byte it consumed short of the end. Either way the match over the given input was not clean. That is what a
    fixture's `invalid` claims.
    """
    consumed = [token for token in tokens if token.code != ERROR]
    if len(consumed) != len(tokens):
        return False
    end = _advance(consumed[-1].start, consumed[-1].text, consumed[-1].code) if consumed else Mark(0, 0, 1, 0)
    return end.byte == size


def marker_fault(tokens: Iterable[Token], is_whole: bool) -> str | None:
    """
    Return a single-line reason the tokens' markers do not balance, or None.

    A `begin-` marker must get its `end-`, on the paths where the parse went wrong as much as on the clean ones. The
    fold that rebuilds the production tree cannot pair the markers otherwise. A resumed parse would nest what follows
    inside what failed rather than beside it.

    Markers pair by their code. That is how the marker gate of the grammar reads them, and that is how a block scalar
    pairs at all. A block scalar opens with a marker of its own. The chomping decides where the close falls.

    `is_whole` says these tokens are a whole parse, whose markers must balance exactly. A rule run outside the root is
    not a whole parse. Such a rule may close what its caller opened, and `b-chomped-last` does exactly that. So it may
    close a marker these tokens did not open. Leaving a marker open is a fault either way.
    """
    open_markers = []
    for token in tokens:
        code = _CHAR_CODE.get(token.code)
        if code is None or not code.startswith(("begin-", "end-")):
            continue
        name = code.split("-", 1)[1]
        if code.startswith("begin-"):
            open_markers.append(name)
        elif open_markers and open_markers[-1] == name:
            open_markers.pop()
        elif is_whole:
            return f"closes {name} without opening it"
    if open_markers:
        return f"leaves open: {', '.join(reversed(open_markers))}"
    return None


def escape(raw: bytes, code: str | None = None) -> str:
    r"""
    Escape raw `bytes` into wire text, as `src/wire.c` does. Under unparsed-invalid, `code` arrives as its wire
    character. The bytes begin no character, and a byte comes out as `\xXX`.

    Under any other code the escaping goes by codepoint over UTF-8, and that is the default. Printable ASCII but a
    backslash comes out as itself. Anything else becomes `\xXX` or `\uXXXX` or `\UXXXXXXXX`. The hex comes out
    lower-case.
    """
    if code == CODE_CHAR["unparsed-invalid"]:
        return "".join(f"\\x{byte:02x}" for byte in raw)
    out = []
    for character in raw.decode("utf-8"):
        codepoint = ord(character)
        if 0x20 <= codepoint <= 0x7E and codepoint != 0x5C:
            out.append(character)
        elif codepoint <= 0xFF:
            out.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            out.append(f"\\u{codepoint:04x}")
        else:
            out.append(f"\\U{codepoint:08x}")
    return "".join(out)


def _advance(mark: Mark, text: str, code: str | None = None) -> Mark:
    """
    The mark reached after consuming escaped `text` from `mark`. A byte per UTF-8 length, a line at a break. Under
    unparsed-invalid an escape is a raw byte and a column, and an escape counts as no break.
    """
    index = 0
    pieces = units(text, code)
    while index < len(pieces):
        codepoint, byte_length, _escaped = pieces[index]
        if codepoint == CARRIAGE_RETURN and index + 1 < len(pieces) and pieces[index + 1][0] == LINE_FEED:
            mark = Mark(mark.byte + byte_length + pieces[index + 1][1], mark.char + 2, mark.line + 1, 0)
            index += 2
        elif codepoint in (CARRIAGE_RETURN, LINE_FEED):
            mark = Mark(mark.byte + byte_length, mark.char + 1, mark.line + 1, 0)
            index += 1
        else:
            mark = Mark(mark.byte + byte_length, mark.char + 1, mark.line, mark.column + 1)
            index += 1
    return mark


def units(text: str, code: str | None = None) -> list[tuple[int, int, str]]:
    """
    Split escaped wire text into units. A unit is `(value, byte_length, escaped)` per character. Under unparsed-invalid
    an escape is a raw byte and so takes a byte. Under any other code an escape is a codepoint, and that is the default.
    The byte length of such a unit is its UTF-8 length.
    """
    is_invalid = code == CODE_CHAR["unparsed-invalid"]
    result = []
    index = 0
    while index < len(text):
        found = _ESCAPE.match(text, index)
        if found is not None:
            written = found.group(0)
            value = int(written[2:], 16)
            result.append((value, 1 if is_invalid else _utf8_length(value), written))
            index = found.end()
        else:
            written = text[index]
            result.append((ord(written), 1, written))
            index += 1
    return result


def _utf8_length(codepoint: int) -> int:
    """The number of bytes the codepoint occupies in UTF-8."""
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0x7FF:
        return 2
    if codepoint <= 0xFFFF:
        return 3
    return 4
