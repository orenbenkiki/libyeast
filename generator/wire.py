# SPDX-License-Identifier: MIT
"""The yeast wire format, in Python.

A token stream is written as, per token, a position comment and a code line — `# B: <byte>, C: <char>, L: <line>,
c: <column>` then a code character and the token's text, escaped by codepoint. This is the same format `src/wire.c`
reads and writes and that the conformance fixtures are in; here it is what the interpreter serializes its tokens into to
be diffed against them.

The text is escaped exactly as `src/wire.c` does: a printable ASCII byte other than a backslash stands for itself, and
everything else becomes `\\xXX`, `\\uXXXX`, or `\\UXXXXXXXX` with lower-case hex. Marks advance a byte by its UTF-8
length, a character by one, and a line at each break — CR, LF, or CR LF together — which resets the column, following
the reference's own line counting.
"""

import re
from dataclasses import dataclass

ERROR = "!"  # the code all three failures share on the wire

# The wire character each token code is written as — the grammar names the code, `src/wire.c`'s YS_WIRE table gives the
# character, and `check_wire.py` gates this copy against it so the two cannot drift.
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
    "unparsed": "-",
    "detected": "$",
}

CARRIAGE_RETURN = 0x0D
LINE_FEED = 0x0A

_POSITION = re.compile(r"^# B: (\d+), C: (\d+), L: (\d+), c: (\d+)$")
_ESCAPE = re.compile(r"\\x[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8}")


@dataclass(frozen=True)
class Mark:
    """A position in the input: byte and codepoint offsets, 1-based line, 0-based column."""

    byte: int
    char: int
    line: int
    column: int


@dataclass(frozen=True)
class Token:
    """A wire token: its code character, its start mark, and its text escaped as it appears on the wire."""

    code: str
    start: Mark
    text: str  # escaped, as on the wire; an error's is its message, a leaf's is its escaped input


def parse(wire):
    """Parse a wire token stream into a list of tokens."""
    lines = wire.split("\n")
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


def serialize(tokens):
    """Serialize tokens back into a wire token stream (a trailing newline after each, as the reference writes them)."""
    out = []
    for token in tokens:
        mark = token.start
        out.append(f"# B: {mark.byte}, C: {mark.char}, L: {mark.line}, c: {mark.column}\n{token.code}{token.text}\n")
    return "".join(out)


def chain_fault(tokens):
    """Return a one-line reason the tokens' marks do not chain, or None.

    Contiguous leaf tokens must chain: a token's start plus its text lands on the next token's start. Only the byte and
    character offsets are guaranteed to chain — a token may legitimately re-open a line, so its start column need not
    follow. An error token spans no input and is skipped.
    """
    previous = None
    for token in tokens:
        if token.code == ERROR:
            continue
        if previous is not None:
            end = advance(previous.start, previous.text)
            if token.start.byte != end.byte or token.start.char != end.char:
                return f"token at {token.start} does not follow the previous token's end {end}"
        previous = token
    return None


def escape(raw):
    """Escape raw UTF-8 `bytes` into wire text, as `src/wire.c` does — printable ASCII but a backslash stands for
    itself, and everything else becomes `\\xXX`, `\\uXXXX`, or `\\UXXXXXXXX` with lower-case hex.
    """
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


def advance(mark, text):
    """The mark reached after consuming escaped `text` from `mark` — a byte per UTF-8 length, a line at each break."""
    index = 0
    pieces = units(text)
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


def units(text):
    """Split escaped wire text into codepoint units — `(codepoint, byte_length, escaped)` for each character in it."""
    result = []
    index = 0
    while index < len(text):
        escape = _ESCAPE.match(text, index)
        if escape is not None:
            piece = escape.group(0)
            codepoint = int(piece[2:], 16)
            result.append((codepoint, _utf8_length(codepoint), piece))
            index = escape.end()
        else:
            character = text[index]
            result.append((ord(character), 1, character))
            index += 1
    return result


def _utf8_length(codepoint):
    """The number of bytes the codepoint occupies in UTF-8."""
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0x7FF:
        return 2
    if codepoint <= 0xFFFF:
        return 3
    return 4
