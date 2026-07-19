# SPDX-License-Identifier: MIT
"""Fold libyeast's yeast token stream up to the YAML Test Suite's event level, and check the two are compatible.

The community YAML Test Suite (`third_party/yaml-test-suite/`, vendored data form) states, per case, an `in.yaml` input
and the `test.event` stream a conformant parser produces for it — or an `error` marker where it must reject the input.
libyeast is a token parser, a level below events, so the check is a deterministic **fold**: the yeast stream's
`begin-`/`end-` markers rebuild the event tree and the leaf tokens fill it, and the question is whether that stream is
*compatible* with the expected events — would it produce them, matched as far as the token layer settles them. Node and
pair brackets, indicators, whitespace, indentation and breaks are presentation the events do not carry; they fold
away. A scalar's value is reconstructed as far as the tokens mechanically give it — content joined, a `line-fold` a
space and a `line-feed` a newline, an escape resolved — not by any value-layer decision above the tokens.

This is libyeast's independent net: the suite is derived from the same spec but written by other hands, so it catches a
grammar bug libyeast's own fixtures, migrated from one reference, would share.
"""

import os

import annotated2ir
import interpreter
import wire

SUITE = os.path.join(annotated2ir.TREE, "third_party", "yaml-test-suite")

# The escape sequences a double-quoted scalar's `begin-escape`..`end-escape` span resolves to, keyed by the character
# after the backslash. The numeric escapes (`\xHH`, `\uHHHH`, `\UHHHHHHHH`) are handled apart.
ESCAPES = {
    "0": "\x00",
    "a": "\x07",
    "b": "\x08",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\x0b",
    "f": "\x0c",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": " ",
    "P": " ",
}


def _unescape_wire(text):
    """The characters a wire token's escaped text stands for: a codepoint per `\\xHH`/`\\uHHHH`, else literal."""
    return "".join(chr(value) for value, _length, _piece in wire.units(text))


class Event:
    """One event: its kind (`+MAP`, `=VAL`, …) and the parts written after it, compared as far as they settle."""

    __slots__ = ("kind", "anchor", "tag", "style", "value")

    def __init__(self, kind, anchor=None, tag=None, style=None, value=None):
        self.kind = kind
        self.anchor = anchor
        self.tag = tag
        self.style = style
        self.value = value

    def __repr__(self):
        parts = [self.kind]
        if self.anchor:
            parts.append(f"&{self.anchor}")
        if self.tag:
            parts.append(f"<{self.tag}>")
        if self.style is not None:
            parts.append(f"{self.style}{self.value}")
        return " ".join(parts)


def parse_events(text):
    """Parse a `test.event` file into a list of `Event`s.

    A line is `KIND rest`: a collection marker keeps only its kind (a flow `{}`/`[]` hint is presentation); `=VAL` and
    `=ALI` carry an optional `&anchor`, an optional `<tag>`, then a `<style><value>` where the style is one of `:'"|>`.
    """
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        kind, _, rest = line.partition(" ")
        rest = rest.strip()
        if kind in ("+STR", "-STR", "+DOC", "-DOC", "-MAP", "-SEQ"):
            events.append(Event(kind))
        elif kind in ("+MAP", "+SEQ"):
            events.append(Event(kind))  # a `{}`/`[]` flow hint is not compared
        elif kind == "=ALI":
            events.append(Event(kind, value=rest[1:] if rest.startswith("*") else rest))
        elif kind == "=VAL":
            anchor = tag = None
            while rest and rest[0] in "&<":
                token, _, rest = rest.partition(" ")
                rest = rest.lstrip()
                if token.startswith("&"):
                    anchor = token[1:]
                elif token.startswith("<"):
                    tag = token[1:-1]
            style, value = rest[:1], _unescape_event(rest[1:])
            events.append(Event(kind, anchor, tag, style, value))
        else:
            raise ValueError(f"unknown event {line!r}")
    return events


def _unescape_event(text):
    """A `test.event` value's own escaping — `\\n`, `\\t`, `\\\\`, `\\r` — back to the characters it stands for."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "0": "\x00", "b": "\x08"}.get(nxt, nxt))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


# The wire character each marker the fold cares about carries.
_C = wire.CODE_CHAR
BEGIN = {_C["begin-document"]: "+DOC", _C["begin-mapping"]: "+MAP", _C["begin-sequence"]: "+SEQ"}
END = {_C["end-document"]: "-DOC", _C["end-mapping"]: "-MAP", _C["end-sequence"]: "-SEQ"}


def fold(tokens):
    """Fold a yeast token stream into the events it would produce, or raise `Incompatible` if it holds an error token.

    The stream is bracketed by an implicit `+STR`/`-STR`. A scalar's run of `text`/`meta`/`line-fold`/`line-feed` and
    escapes is gathered between its `begin-scalar` and `end-scalar` and its style read from the indicator that opens it;
    an alias becomes `=ALI`; an anchor or a tag annotates the value or collection it precedes.
    """
    events = [Event("+STR")]
    pending_anchor = pending_tag = None
    scalar = None  # (style, [text parts]) while inside a begin-scalar..end-scalar
    escape = None  # the raw text of a begin-escape..end-escape span
    in_alias = False
    alias = []

    for token in tokens:
        code = token.code
        if code == wire.ERROR:
            raise Incompatible(f"error token: {token.text}")
        if scalar is not None:
            if code == _C["text"]:
                scalar[1].append(token.text and _unescape_wire(token.text))
            elif code == _C["line-fold"]:
                scalar[1].append(" ")
            elif code == _C["line-feed"]:
                scalar[1].append("\n")
            elif code == _C["begin-escape"]:
                escape = []
            elif code == _C["end-escape"]:
                scalar[1].append(_resolve_escape(escape))
                escape = None
            elif escape is not None and code in (_C["indicator"], _C["meta"], _C["text"]):
                escape.append(token.text and _unescape_wire(token.text))
            elif code == _C["indicator"] and scalar[0] is None:
                indicator = _unescape_wire(token.text)
                scalar[0] = {'"': '"', "'": "'", "|": "|", ">": ">"}.get(indicator, scalar[0])
            elif code == _C["end-scalar"]:
                style = scalar[0] or ":"
                events.append(Event("=VAL", pending_anchor, pending_tag, style, "".join(scalar[1])))
                pending_anchor = pending_tag = scalar = None
            continue
        if in_alias:
            if code == _C["meta"]:
                alias.append(token.text and _unescape_wire(token.text))
            elif code == _C["end-alias"]:
                events.append(Event("=ALI", value="".join(alias)))
                in_alias = False
                alias = []
            continue
        if code in BEGIN:
            events.append(Event(BEGIN[code], pending_anchor, pending_tag))
            pending_anchor = pending_tag = None
        elif code in END:
            events.append(Event(END[code]))
        elif code == _C["begin-scalar"]:
            scalar = [None, []]
        elif code == _C["begin-alias"]:
            in_alias, alias = True, []
        elif code == _C["begin-anchor"] or code == _C["begin-tag"]:
            pass  # the name follows as meta/handle; gathered below
        elif code == _C["meta"]:
            pass
    events.append(Event("-STR"))
    return events


def _resolve_escape(parts):
    """A double-quoted `\\`-escape (its indicator and digits gathered in `parts`) as the character it denotes."""
    body = "".join(parts)
    if not body:
        return ""
    lead = body[0]
    if lead in "xuU":
        return chr(int(body[1:], 16))
    return ESCAPES.get(lead, lead)


class Incompatible(Exception):
    """The token stream cannot produce the expected events."""


def run_case(grammar, data):
    """Fold the events libyeast produces for `data`, or raise `Incompatible` (an error token, or a crash)."""
    tokens = interpreter.run(grammar, "l-yeast-stream", data, {})
    return fold(tokens)
