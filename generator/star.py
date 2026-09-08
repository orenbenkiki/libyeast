# SPDX-License-Identifier: MIT
"""
Fold libyeast's yeast token stream up to the YAML Test Suite's event level, and check that both are compatible.

The community YAML Test Suite lives in `third_party/yaml-test-suite/`, as vendored data. Per case the suite states an
`in.yaml` input and the `test.event` stream a conformant parser produces for that input. The suite states an `error`
marker instead where a parser must reject the input.

libyeast is a token parser, and events sit a level above. So the check is a deterministic **fold**. The yeast stream's
`begin-`/`end-` markers rebuild the event tree and the leaf tokens fill it. The question is whether that stream is
*compatible* with the expected events, matched as far as the token layer settles them.

Node and pair brackets are presentation the events leave out. So are indicators and whitespace. So are indentation and
breaks. They fold away. A scalar's value comes back as far as the tokens mechanically give it. The content joins up, a
`line-fold` gives a space and a `line-feed` a newline, and an escape resolves. A value-layer decision above the tokens
comes into none of it.

This is libyeast's independent net. The suite derives from the same spec but comes from other hands. It catches a
grammar bug the fixtures of libyeast, migrated from a single reference, would share.
"""

import dataclasses
import os
from collections.abc import Iterable, Mapping

import gate
import interpreter
import ir
import wire

SUITE = os.path.join(gate.TREE, "third_party", "yaml-test-suite")  # the directory the vendored YAML Test Suite sits in.

# The escape sequences a `begin-escape`..`end-escape` span of a double-quoted scalar resolves to, keyed by the character
# after the backslash. A separate path handles the numeric escapes `\xHH` and `\uHHHH` and `\UHHHHHHHH`.
_ESCAPES = {
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
    "L": "\u2028",
    "P": "\u2029",
}


def _unescape_wire(text: str) -> str:
    r"""
    The characters a wire token's escaped text names. A codepoint per `\xHH` or `\uHHHH`, and a literal otherwise.
    """
    return "".join(chr(value) for value, _length, _piece in wire.units(text))


def _uri_unescape(text: str) -> str:
    """A tag URI's `%XX` escapes as the characters they denote. A run of escaped bytes decodes as UTF-8."""
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


def _expand_tag(handle: str, suffix: str, tags: Mapping[str, str]) -> str:
    """
    A tag's `handle` and `suffix` as the event shows it. The handle resolves through `tags`. That mapping holds the
    document's `%TAG` directives over the default primary `!` and secondary `!!`. So `!!str` is `tag:yaml.org,2002:str`,
    a local `!foo` stays `!foo`, and a verbatim `!<uri>` is the URI it wrote. A URI's `%XX` escapes decode. A named
    handle needs a `%TAG` to resolve it. The resolution this fold models reports an unresolved handle as an error.
    """
    if handle.startswith("!<") and handle.endswith(">"):
        return _uri_unescape(handle[2:-1])  # a verbatim !<uri>, written with no handle span
    if handle not in tags:  # only a named `!x!` reaches here undefined; `!` and `!!` are always the defaults
        raise Incompatible(f"undefined tag handle {handle!r}")
    return _uri_unescape(tags[handle] + suffix)


class _Event:
    """
    An event. The kind is `+MAP` or `=VAL` or another such marker. The parts written after the kind, compared as far as
    they settle.
    """

    __slots__ = ("kind", "anchor", "tag", "style", "value")

    def __init__(
        self,
        kind: str,
        anchor: str | None = None,
        tag: str | None = None,
        style: str | None = None,
        value: str | None = None,
    ) -> None:
        self.kind = kind
        self.anchor = anchor
        self.tag = tag
        self.style = style
        self.value = value

    def __repr__(self) -> str:
        """The event written as the suite writes it. A disagreement comes out in that form."""
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


def parse_events(text: str) -> list[_Event]:
    """
    Parse a `test.event` file into a list of `Event`s.

    A line is `KIND rest`. A collection marker keeps its kind and drops what follows. A flow `{}`/`[]` hint is
    presentation. `=VAL` and `=ALI` take an optional `&anchor`, an optional `<tag>`, then a `<style><value>` where the
    style comes from `:'"|>`.
    """
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        kind, _, rest = line.partition(" ")
        if kind in ("+STR", "-STR", "+DOC", "-DOC", "-MAP", "-SEQ"):
            events.append(_Event(kind))
        elif kind in ("+MAP", "+SEQ"):
            anchor = tag = None
            for token in rest.split():  # an optional `{}`/`[]` flow hint, then `&anchor`, then `<tag>`
                if token.startswith("&"):
                    anchor = token[1:]
                elif token.startswith("<"):
                    tag = token[1:-1]
            events.append(_Event(kind, anchor, tag))
        elif kind == "=ALI":
            events.append(_Event(kind, value=rest[1:] if rest.startswith("*") else rest))
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
            events.append(_Event(kind, anchor, tag, style, value))
        else:
            raise ValueError(f"unknown event {line!r}")
    return events


def _unescape_event(text: str) -> str:
    r"""
    The escaping a `test.event` value uses, back to the characters it names. That is `\n` and `\t` and `\r` and `\\` and
    `\0` and `\b`.
    """
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


# The wire character a marker the fold cares about takes.
_C = wire.CODE_CHAR
_BEGIN = {_C["begin-document"]: "+DOC", _C["begin-mapping"]: "+MAP", _C["begin-sequence"]: "+SEQ"}  # the opening event.
_END = {_C["end-document"]: "-DOC", _C["end-mapping"]: "-MAP", _C["end-sequence"]: "-SEQ"}  # the closing event.

# The tag handles a document starts with, ahead of any `%TAG` directive of its own. The primary `!` names a local tag,
# and the secondary `!!` resolves to the YAML tag namespace.
_DEFAULT_TAGS = {"!": "!", "!!": "tag:yaml.org,2002:"}


@dataclasses.dataclass
class _Scalar:
    """A scalar being gathered. It holds the style and the text of the `begin-scalar`..`end-scalar` span."""

    style: str | None = None
    parts: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _Tag:
    """A tag being gathered. It holds the handle and the suffix, and says whether the handle has closed."""

    handle: list[str] = dataclasses.field(default_factory=list)
    suffix: list[str] = dataclasses.field(default_factory=list)
    is_handle_closed: bool = False

    def gathering(self) -> list[str]:
        """The side the arriving characters belong to."""
        return self.suffix if self.is_handle_closed else self.handle


@dataclasses.dataclass
class _Directive:
    """A directive being gathered. It holds the name, the handle and the prefix, and says which span is open."""

    name: list[str] = dataclasses.field(default_factory=list)
    handle: list[str] = dataclasses.field(default_factory=list)
    prefix: list[str] = dataclasses.field(default_factory=list)
    is_in_handle: bool = False
    is_in_tag: bool = False

    def gathering(self) -> list[str]:
        """The part the arriving characters belong to."""
        if self.is_in_handle:
            return self.handle
        return self.prefix if self.is_in_tag else self.name


def _fold(tokens: Iterable[wire.Token]) -> list[_Event]:
    """
    Fold a yeast token stream into the events it would produce. Raise `Incompatible` where the document is rejected, by
    an error token or by a resolution the tokens cannot show.

    An implicit `+STR`/`-STR` brackets the stream. A scalar gathers a run of `text`/`meta`/`line-fold`/`line-feed` and
    escapes. That run sits between `begin-scalar` and `end-scalar`. The indicator that opens a scalar gives its style.
    An alias becomes `=ALI`. An anchor or a tag annotates the value or collection it precedes.
    """
    events = [_Event("+STR")]
    pending_anchor: str | None = None
    pending_tag: str | None = None
    scalar: _Scalar | None = None  # while inside a begin-scalar..end-scalar
    escape: list[str] | None = None  # the raw text of a begin-escape..end-escape span
    is_in_alias = False
    alias: list[str] = []
    anchor: list[str] | None = None  # the name parts while inside a begin-anchor..end-anchor
    tag: _Tag | None = None  # while inside a begin-tag..end-tag
    tags = dict(_DEFAULT_TAGS)  # the document's tag handles, its `%TAG` directives over the defaults
    directive: _Directive | None = None  # while inside a directive
    yaml_directives = 0  # `%YAML` directives seen in the document; a second is an error

    for token in tokens:
        code = token.code
        if code == wire.ERROR:
            raise Incompatible(f"error token: {token.text}")
        if scalar is not None:
            if code == _C["text"]:
                scalar.parts.append(token.text and _unescape_wire(token.text))
            elif code == _C["line-fold"]:
                scalar.parts.append(" ")
            elif code == _C["line-feed"]:
                scalar.parts.append("\n")
            elif code == _C["begin-escape"]:
                escape = []
            elif code == _C["end-escape"]:
                if escape is None:
                    raise Incompatible("an `end-escape` with no `begin-escape` before it")
                # The raise above says it is a list, and pylint does not narrow it across the loop.
                scalar.parts.append(_resolve_escape(escape[1:]))  # pylint: disable=unsubscriptable-object
                escape = None
            # What an escape is made of. The backslash that opens it and the characters naming it, which the grammar
            # gives as an indicator and meta.
            elif escape is not None and code in (_C["indicator"], _C["meta"]):
                escape.append(token.text and _unescape_wire(token.text))
            elif code == _C["indicator"] and scalar.style is None:
                indicator = _unescape_wire(token.text)
                scalar.style = {'"': '"', "'": "'", "|": "|", ">": ">"}.get(indicator, scalar.style)
            elif code == _C["end-scalar"]:
                style = scalar.style or ":"
                events.append(_Event("=VAL", pending_anchor, pending_tag, style, "".join(scalar.parts)))
                pending_anchor = pending_tag = None
                scalar = None
            continue
        if is_in_alias:
            if code == _C["meta"]:
                alias.append(token.text and _unescape_wire(token.text))
            elif code == _C["end-alias"]:
                events.append(_Event("=ALI", value="".join(alias)))
                is_in_alias = False
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
                tag.is_handle_closed = True
            elif code == _C["end-tag"]:
                pending_tag = _expand_tag("".join(tag.handle), "".join(tag.suffix), tags)
                tag = None
            elif code in (_C["indicator"], _C["meta"]):
                tag.gathering().append(token.text and _unescape_wire(token.text))
            continue
        if directive is not None:
            if code == _C["begin-handle"]:
                directive.is_in_handle = True
            elif code == _C["end-handle"]:
                directive.is_in_handle = False
            elif code == _C["begin-tag"]:
                directive.is_in_tag = True
            elif code == _C["end-tag"]:
                directive.is_in_tag = False
            elif code == _C["meta"]:
                directive.gathering().append(_unescape_wire(token.text))
            elif code == _C["indicator"] and (directive.is_in_handle or directive.is_in_tag):
                (directive.handle if directive.is_in_handle else directive.prefix).append(_unescape_wire(token.text))
            elif code == _C["end-directive"]:
                # A `%YAML` directive's version joins its name here too.
                name = directive.name[0] if directive.name else ""
                if name == "TAG":
                    tags["".join(directive.handle)] = "".join(directive.prefix)
                elif name == "YAML":
                    yaml_directives += 1
                    if yaml_directives > 1:
                        raise Incompatible("repeated %YAML directive")
                directive = None
            continue
        if code == _C["begin-directive"]:
            directive = _Directive()
        elif code in _BEGIN:
            if code == _C["begin-document"]:
                tags = dict(_DEFAULT_TAGS)
                yaml_directives = 0
            events.append(_Event(_BEGIN[code], pending_anchor, pending_tag))
            pending_anchor = pending_tag = None
        elif code in _END:
            events.append(_Event(_END[code]))
        elif code == _C["begin-scalar"]:
            scalar = _Scalar()
        elif code == _C["begin-alias"]:
            is_in_alias, alias = True, []
        elif code == _C["begin-anchor"]:
            anchor = []
        elif code == _C["begin-tag"]:
            tag = _Tag()
    events.append(_Event("-STR"))
    return events


def _resolve_escape(parts: Iterable[str]) -> str:
    r"""
    A double-quoted `\`-escape as the character it denotes. The indicator and the digits arrive gathered in `parts`.
    """
    body = "".join(parts)
    if not body:
        return ""
    lead = body[0]
    if lead in "xuU":
        return chr(int(body[1:], 16))
    return _ESCAPES.get(lead, lead)


class Incompatible(Exception):
    """libyeast rejects the document. An error token, or a resolution error only the fold can see."""


def run_case(grammar: dict[str, ir.Prod], data: bytes) -> list[_Event]:
    """Fold the events libyeast produces for `data`, or raise `Incompatible` where it rejects the document."""
    tokens = interpreter.run(grammar, "l-yeast-stream", data, {})
    return _fold(tokens)
