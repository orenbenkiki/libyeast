# SPDX-License-Identifier: MIT
"""A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

Slow and obviously correct: it matches a production against an input the way the grammar reads, character by character
with backtracking, and emits the yeast token stream — so that libyeast's grammar is proved to produce the reference's
tokens before any C exists to be wrong, and so that, taught the canonical form later, it becomes the net every
normalization step is checked against. It takes the grammar as an argument, so the same interpreter and the same
fixtures judge the grammar as it is now and the structurally-simplified grammar later.

`SUPPORTED` says which nodes it knows, and grows a family at a time; `coverable` says which fixtures rest on only those,
and so are the ones it is asked to reproduce. It matches the character-level nodes — `Char`, `Range`, `Diff`, `Empty`,
`Seq`, `Alt`, and `Ref` — and produces tokens from the annotation nodes: `Token` gives its characters a code and cuts
the run at both edges, `Wrap` brackets its match in `begin`/`end` markers, and `Emit` is a marker on its own.
"""

import ir
import wire

# The grammar nodes the interpreter matches. It grows a family at a time, and with it the fixtures it can reproduce.
SUPPORTED = (ir.Char, ir.Range, ir.Diff, ir.Empty, ir.Seq, ir.Alt, ir.Ref, ir.Token, ir.Wrap, ir.Emit)


class Emitter:
    """The token stream a run builds, and the input it reads to build it.

    Characters consumed accumulate into a run carrying the current code; the run becomes a token wherever it is cut —
    at a token annotation's edge or a marker. A checkpoint captures the whole of the state, so an alternative that
    fails can be undone to the point before it, tokens and all.
    """

    def __init__(self, text):
        self.text = text  # the input, as codepoints
        self.position = 0
        self.mark = wire.Mark(0, 0, 1, 0)
        self.tokens = []
        self.run = None  # (code character, start mark, start position) of the open run, or None
        self.codes = ["unparsed"]  # the stack of token codes; the top is the one the next character carries

    def checkpoint(self):
        return (self.position, self.mark, len(self.tokens), self.run, len(self.codes))

    def rewind(self, checkpoint):
        self.position, self.mark, token_count, self.run, code_count = checkpoint
        del self.tokens[token_count:]
        del self.codes[code_count:]

    def consume(self):
        """Take the character at the position into the open run, opening one under the current code if none is."""
        if self.run is None:
            self.run = (wire.CODE_CHAR[self.codes[-1]], self.mark, self.position)
        character = self.text[self.position]
        codepoint = ord(character)
        byte_length = len(character.encode("utf-8"))
        if codepoint == wire.LINE_FEED or (codepoint == wire.CARRIAGE_RETURN and not self._before_line_feed()):
            self.mark = wire.Mark(self.mark.byte + byte_length, self.mark.char + 1, self.mark.line + 1, 0)
        else:
            self.mark = wire.Mark(
                self.mark.byte + byte_length, self.mark.char + 1, self.mark.line, self.mark.column + 1
            )
        self.position += 1

    def _before_line_feed(self):
        """Whether a CR at the position is immediately followed by an LF, so the two are one break."""
        return self.position + 1 < len(self.text) and ord(self.text[self.position + 1]) == wire.LINE_FEED

    def cut(self):
        """End the open run, emitting it as a token if it took any characters."""
        if self.run is not None:
            character, start, start_position = self.run
            text = self.text[start_position : self.position]
            if text:
                self.tokens.append(wire.Token(character, start, wire.escape(text.encode("utf-8"))))
            self.run = None

    def marker(self, code):
        """Emit a zero-width marker of `code`, cutting the open run before it."""
        self.cut()
        self.tokens.append(wire.Token(wire.CODE_CHAR[code], self.mark, ""))


def coverable(production, grammar):
    """Whether every node reachable from `production` is one the interpreter supports."""
    seen = set()
    frontier = [production]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        node = grammar.get(name)
        if node is None:
            return False
        stack = [node.body]
        while stack:
            current = stack.pop()
            if isinstance(current, ir.Ref):
                frontier.append(current.name)
                continue
            if not isinstance(current, SUPPORTED):
                return False
            for field in current.__dataclass_fields__:
                value = getattr(current, field)
                for child in value if isinstance(value, tuple) else (value,):
                    if hasattr(child, "__dataclass_fields__"):
                        stack.append(child)
    return True


def match(node, emitter, grammar):
    """Match `node` against the emitter's input from its position, emitting tokens, returning whether it matched.

    A backtracking matcher: an alternative that fails is rewound, tokens and position both, so nothing it did survives
    for the next to trip over. Nothing is committed until the whole match returns.
    """
    if isinstance(node, ir.Char):
        if emitter.position < len(emitter.text) and ord(emitter.text[emitter.position]) == node.cp:
            emitter.consume()
            return True
        return False
    if isinstance(node, ir.Range):
        if emitter.position < len(emitter.text) and node.lo <= ord(emitter.text[emitter.position]) <= node.hi:
            emitter.consume()
            return True
        return False
    if isinstance(node, ir.Empty):
        return True
    if isinstance(node, ir.Ref):
        return match(grammar[node.name].body, emitter, grammar)
    if isinstance(node, ir.Seq):
        for item in node.items:
            if not match(item, emitter, grammar):
                return False
        return True
    if isinstance(node, ir.Alt):
        checkpoint = emitter.checkpoint()
        for item in node.items:
            if match(item, emitter, grammar):
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Diff):
        start = emitter.checkpoint()
        if not match(node.base, emitter, grammar):
            emitter.rewind(start)
            return False
        after_base = emitter.checkpoint()
        for excluded in node.minus:
            emitter.rewind(start)
            matched = match(excluded, emitter, grammar)
            emitter.rewind(start)
            if matched:
                return False
        emitter.rewind(after_base)
        return True
    if isinstance(node, ir.Token):
        emitter.cut()
        emitter.codes.append(node.code)
        matched = match(node.item, emitter, grammar)
        if matched:
            emitter.cut()
        emitter.codes.pop()
        return matched
    if isinstance(node, ir.Wrap):
        emitter.marker(node.begin)
        matched = match(node.item, emitter, grammar)
        if matched:
            emitter.marker(node.end)
        return matched
    if isinstance(node, ir.Emit):
        emitter.marker(node.code)
        return True
    raise NotImplementedError(f"interpreter does not support {type(node).__name__}")


def run(grammar, production, data):
    """Run `production` on the UTF-8 `data`, returning the yeast tokens it emits, or None if it does not match."""
    emitter = Emitter(data.decode("utf-8"))
    if not match(grammar[production].body, emitter, grammar):
        return None
    emitter.cut()
    return emitter.tokens
