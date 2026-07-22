# SPDX-License-Identifier: MIT
"""
Fold libyeast's yeast token stream up to the YAML Test Suite's event level, and check the two are compatible.

The community YAML Test Suite (`third_party/yaml-test-suite/`, vendored data form) states, per case, an `in.yaml` input
and the `test.event` stream a conformant parser produces for it — or an `error` marker where it must reject the input.
libyeast is a token parser, a level below events, so the check is a deterministic **fold**: the yeast stream's
`begin-`/`end-` markers rebuild the event tree and the leaf tokens fill it, and the question is whether that stream is
*compatible* with the expected events — would it produce them, matched as far as the token layer settles them. Node and
pair brackets, indicators, whitespace, indentation and breaks are presentation the events do not carry; they fold away.
A scalar's value is reconstructed as far as the tokens mechanically give it — content joined, a `line-fold` a space and
a `line-feed` a newline, an escape resolved — not by any value-layer decision above the tokens.

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
    r"""The characters a wire token's escaped text stands for: a codepoint per `\xHH`/`\uHHHH`, else literal."""
    return "".join(chr(value) for value, _length, _piece in wire.units(text))


def _uri_unescape(text):
    """A tag URI's `%XX` escapes as the characters they denote: each run of escaped bytes decoded as UTF-8."""
    out, raw, index = [], bytearray(), 0
    while index < len(text):
        if text[index] == "%" and index + 3 <= len(text):
            raw.append(int(text[index + 1 : index + 3], 16))
            index += 3
        else:
            if raw:
                out.append(raw.decode("utf-8", "replace"))
                raw = bytearray()
            out.append(text[index])
            index += 1
    if raw:
        out.append(raw.decode("utf-8", "replace"))
    return "".join(out)


def _expand_tag(handle, suffix, tags):
    """
    A tag's `handle` and `suffix` as the event shows it: its handle resolves through `tags` — the document's `%TAG`
    directives over the default primary `!` and secondary `!!` — so `!!str` is `tag:yaml.org,2002:str`, a local `!foo`
    stays `!foo`, and a verbatim `!<uri>` is the URI it wrote. A URI's `%XX` escapes are decoded. A named handle with no
    `%TAG` to resolve it is undefined, and using it is an error the resolution the fold stands in for reports.
    """
    if handle.startswith("!<") and handle.endswith(">"):
        return _uri_unescape(handle[2:-1])  # a verbatim !<uri>, written with no handle span
    if handle not in tags:  # only a named `!x!` reaches here undefined; `!` and `!!` are always the defaults
        raise Incompatible(f"undefined tag handle {handle!r}")
    return _uri_unescape(tags[handle] + suffix)


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
        if self.kind == "=ALI":
            parts.append(f"*{self.value}")
        elif self.style is not None:
            parts.append(f"{self.style}{self.value}")
        return " ".join(parts)


def parse_events(text):
    """
    Parse a `test.event` file into a list of `Event`s.

    A line is `KIND rest`: a collection marker keeps only its kind (a flow `{}`/`[]` hint is presentation); `=VAL` and
    `=ALI` carry an optional `&anchor`, an optional `<tag>`, then a `<style><value>` where the style is one of `:'"|>`.
    """
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        kind, _, rest = line.partition(" ")
        if kind in ("+STR", "-STR", "+DOC", "-DOC", "-MAP", "-SEQ"):
            events.append(Event(kind))
        elif kind in ("+MAP", "+SEQ"):
            anchor = tag = None
            for token in rest.split():  # an optional `{}`/`[]` flow hint, then `&anchor`, then `<tag>`
                if token.startswith("&"):
                    anchor = token[1:]
                elif token.startswith("<"):
                    tag = token[1:-1]
            events.append(Event(kind, anchor, tag))
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
    r"""A `test.event` value's own escaping — `\n`, `\t`, `\\`, `\r` — back to the characters it stands for."""
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

# The tag handles every document starts with, before its own `%TAG` directives: the primary `!` names a local tag, the
# secondary `!!` resolves to the YAML tag namespace.
DEFAULT_TAGS = {"!": "!", "!!": "tag:yaml.org,2002:"}


def fold(tokens):
    """
    Fold a yeast token stream into the events it would produce, or raise `Incompatible` if it holds an error token.

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
    anchor = None  # [name parts] while inside a begin-anchor..end-anchor
    tag = None  # [handle parts, suffix parts, handle-closed?] while inside a begin-tag..end-tag
    tags = dict(DEFAULT_TAGS)  # the document's tag handles, its `%TAG` directives over the defaults
    directive = None  # [name, handle parts, prefix parts, in-handle?, in-prefix?] while inside a directive
    yaml_directives = 0  # `%YAML` directives seen in the document; more than one is an error

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
                scalar[1].append(_resolve_escape(escape[1:]))  # escape[0] is the opening backslash
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
        if anchor is not None:
            if code == _C["meta"]:
                anchor.append(token.text and _unescape_wire(token.text))
            elif code == _C["end-anchor"]:
                pending_anchor = "".join(anchor)
                anchor = None
            continue
        if tag is not None:
            if code == _C["end-handle"]:
                tag[2] = True
            elif code == _C["end-tag"]:
                pending_tag = _expand_tag("".join(tag[0]), "".join(tag[1]), tags)
                tag = None
            elif code in (_C["indicator"], _C["meta"]):
                tag[1 if tag[2] else 0].append(token.text and _unescape_wire(token.text))
            continue
        if directive is not None:
            if code == _C["begin-handle"]:
                directive[3] = True
            elif code == _C["end-handle"]:
                directive[3] = False
            elif code == _C["begin-tag"]:
                directive[4] = True
            elif code == _C["end-tag"]:
                directive[4] = False
            elif code == _C["meta"]:
                directive[1 if directive[3] else 2 if directive[4] else 0].append(_unescape_wire(token.text))
            elif code == _C["indicator"] and (directive[3] or directive[4]):
                directive[1 if directive[3] else 2].append(_unescape_wire(token.text))
            elif code == _C["end-directive"]:
                name = directive[0][0] if directive[0] else ""  # a `%YAML` directive's version joins its name here too
                if name == "TAG":
                    tags["".join(directive[1])] = "".join(directive[2])
                elif name == "YAML":
                    yaml_directives += 1
                    if yaml_directives > 1:
                        raise Incompatible("repeated %YAML directive")
                directive = None
            continue
        if code == _C["begin-directive"]:
            directive = [[], [], [], False, False]
        elif code in BEGIN:
            if code == _C["begin-document"]:
                tags = dict(DEFAULT_TAGS)
                yaml_directives = 0
            events.append(Event(BEGIN[code], pending_anchor, pending_tag))
            pending_anchor = pending_tag = None
        elif code in END:
            events.append(Event(END[code]))
        elif code == _C["begin-scalar"]:
            scalar = [None, []]
        elif code == _C["begin-alias"]:
            in_alias, alias = True, []
        elif code == _C["begin-anchor"]:
            anchor = []
        elif code == _C["begin-tag"]:
            tag = [[], [], False]
    events.append(Event("-STR"))
    return events


def _resolve_escape(parts):
    r"""A double-quoted `\`-escape (its indicator and digits gathered in `parts`) as the character it denotes."""
    body = "".join(parts)
    if not body:
        return ""
    lead = body[0]
    if lead in "xuU":
        return chr(int(body[1:], 16))
    return ESCAPES.get(lead, lead)


class Incompatible(Exception):
    """The token stream cannot produce the expected events."""


def run_case(grammar, data, deterministic=frozenset()):
    """Fold the events libyeast produces for `data`, or raise `Incompatible` (an error token, or a crash)."""
    tokens = interpreter.run(grammar, "l-yeast-stream", data, {}, deterministic=deterministic)
    return fold(tokens)
