# SPDX-License-Identifier: MIT
"""
A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

Slow and obviously correct: it matches a production against an input the way the grammar reads, character by character
with backtracking, and emits the yeast token stream — so that libyeast's grammar is proved to produce the reference's
tokens before any C exists to be wrong, and so that, taught the canonical form later, it becomes the net every
normalization step is checked against. It takes the grammar as an argument, so the same interpreter and the same
fixtures judge the grammar as it is now and the structurally-simplified grammar later.

It matches every node the IR defines, and a node it does not know raises rather than passing quietly — the fixture that
reached one is reported as the crash it is. The character-level nodes (`Char`, `Range`, `Diff`, `Empty`, `Seq`, `Alt`,
`Ref`), the repetitions (`Star`, `Plus`, `Opt`, `Rep`), the parameter machinery (`Case`, `Bind`, `SetVar`, the
arithmetic, and the `Lt`/`Le`/`Max`/`Bound` predicates), and the assertions and lookahead (`StartOfLine`, `EndOfStream`,
`Look`, `NegLook`, `LookBehind`, `ExcludeAt`). It produces tokens from the annotation nodes: `Token` gives its
characters a code and cuts the run at both edges, `Wrap` brackets its match in `begin`/`end` markers, `Emit` is a marker
on its own, and `Error` is an error token naming what was expected. `Recover` says where a failed `(cut)` stops
unwinding.

Matching is success-continuation style: `match` calls a continuation for each way a node matches, in greedy order, and
the continuation reports whether the rest of the parse succeeded — so an alternation is re-entered when a later element
fails, the way the reference backtracks. A `(cut)` is where that stops: past it the parse does not backtrack, and if the
rest then fails it becomes an error token naming what was expected, after which the input from there comes back as
unparsed, each line split into its content and its break.
"""

import os
import sys

import ir
import wire
import yaml

# The continuation-passing matcher recurses once per grammar step and once per repetition, so a match nests far deeper
# than Python's default limit allows even for the small conformance inputs. A caller running the recursive helpers a
# transformed grammar carries (see `check_normalize`) raises this further, from a stack large enough to hold it.
sys.setrecursionlimit(20000)

# A production may nest this deep before the parse is refused — the depth cap the emitted parser will carry too, a guard
# on runaway recursion (a recursive helper that never bottoms out, pathological nesting). It sits well above any
# legitimate depth; nearing it, the production stack is traced to stderr so what recurses is seen, and reaching it
# raises a clear error rather than leaving Python's own limit to fire a bare one.
DEPTH_LIMIT = 6000
DEPTH_TRACE = 40  # productions of the stack's deep end to show, and how near the cap to start showing them

# The text of every message a `(cut)` names, from the grammar's companion table — the one source the interpreter and the
# generated C table both read, so the error text the interpreter emits is the error text the parser will.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "grammar", "messages.yaml")) as _f:
    MESSAGES = yaml.safe_load(_f)

# The production a failed cut hands off to: it brings the input back unparsed and, where the resume policy says so,
# carries on at the next document. Matching it, rather than emitting the tokens by hand, keeps recovery in the grammar,
# where the C parser generates it from — a cut says only where the unwind lands, never what to do about it.
RECOVER = "l-recover"


class CommitFailure(Exception):
    """
    Raised when the parse fails after passing a `(cut)`.

    It carries the cut's message `code`, and unwinds past the backtracking frames — which is what a commit is: none of
    them gets to try another way — to the handler that turns it into an error token and the unparsed recovery.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class DepthExceeded(Exception):
    """Raised when a production nests past `DEPTH_LIMIT` — runaway recursion, refused after its trace is shown."""


BYTE_ORDER_MARK = 0xFEFF  # consumed without ending the start of a line, unlike any other character


def _decode_one(raw, offset):
    """
    The codepoint of the UTF-8 sequence at `offset` and its byte length, or `(None, 1)` where the byte begins none. RFC
    3629, matching the C decoder: an invalid byte is one unit of one byte, so a run of them resyncs at the next valid
    lead rather than swallowing what follows it.
    """
    lead = raw[offset]
    if lead < 0x80:
        return lead, 1
    if 0xC2 <= lead <= 0xDF:
        length = 2
    elif 0xE0 <= lead <= 0xEF:
        length = 3
    elif 0xF0 <= lead <= 0xF4:
        length = 4
    else:
        return None, 1  # a continuation byte alone, an overlong lead, or a byte no sequence starts with
    if offset + length > len(raw):
        return None, 1  # the sequence runs off the end of the input
    try:
        return ord(raw[offset : offset + length].decode("utf-8")), length
    except (UnicodeDecodeError, TypeError):
        return None, 1  # a bad continuation, an overlong encoding, a surrogate, or a codepoint past U+10FFFF


def _decode(raw):
    """
    `raw` as parallel lists: `chars` holds a codepoint per character and `None` per invalid byte, `byte_at` the byte
    offset of each — with a final `byte_at` entry at the end of the input, so unit `i` spans `raw[byte_at[i]:byte_at[i +
    1]]`.
    """
    chars = []
    byte_at = []
    offset = 0
    while offset < len(raw):
        codepoint, length = _decode_one(raw, offset)
        chars.append(codepoint)
        byte_at.append(offset)
        offset += length
    byte_at.append(len(raw))
    return chars, byte_at


class Emitter:
    """
    The token stream a run builds, and the input it reads to build it.

    Characters consumed accumulate into a run carrying the current code; the run becomes a token wherever it is cut — at
    a token annotation's edge or a marker. A checkpoint captures the whole of the state, so an alternative that fails
    can be undone to the point before it, tokens and all.
    """

    def __init__(self, raw):
        self.raw = raw  # the input bytes, as they are: a token's text is a slice of these, never a re-encoding
        self.chars, self.byte_at = _decode(raw)  # one unit per character or invalid byte; position indexes them
        self.position = 0
        self.mark = wire.Mark(0, 0, 1, 0)
        self.tokens = []
        self.run = None  # (code character, start mark, start position) of the open run, or None
        self.provisional = None  # where the open provisional run begins in `tokens`, or None — only one is open
        self.trail = []  # the provisional undo journal — retyped codes, an injected marker, where the run stood at an
        # open or a commit: the only token mutations that are not appends, which a rewind pops to undo what a
        # token-count truncation cannot
        self.code = "unparsed-text"  # the token code the next character carries; a `(token)` sets it, restoring the
        # production's own on the way out — which it reads back from `env["code"]`, the code the production was entered
        # under, the way the C parser reads it off the frame rather than a second stack.
        self.env = {}  # the current production's parameters (n/m/c/t/r) and their values
        self.match_start = 0  # where the enclosing Match() scope began, for Match() and Len(Match())
        self.is_sol = True  # at the start of a line: true at the start of the input, and after every break
        self.forbidden = ()  # patterns that must not match at a start of line — the ongoing `(exclude)` guards in scope
        self.pending = ()  # the `end` markers of the `(wrap)`s the parse is inside, outermost first
        self.ceiling = None  # the position a `(max)` window ends at, past which committed input may not be consumed
        self.ceiling_message = None  # the cut message a consume past the ceiling raises — the window's, held with it
        self.probing = 0  # how many lookaheads are in progress — a probe may read past the ceiling, a commit may not
        self.stack = []  # the productions currently entered, outermost first — the depth guard's trace of what nests
        self.commitments = []  # one `[reached]` record per open committed region, innermost last — not checkpointed:
        # the push and pop actions restore it on their own failure paths, and a region once reached stays reached
        self.deterministic = (
            frozenset()
        )  # the productions entered committed — first holding gate, no second try — which
        # `run` sets from `normalize.deterministic_productions`; empty runs the whole grammar backtracking

    def checkpoint(self):
        return (
            self.position,
            self.mark,
            len(self.tokens),
            len(self.trail),
            self.run,
            self.code,
            dict(self.env),
            self.match_start,
            self.is_sol,
            self.forbidden,
            self.pending,
            self.ceiling,
            self.ceiling_message,
            self.probing,
        )

    def rewind(self, checkpoint):
        (
            self.position,
            self.mark,
            token_count,
            trail_length,
            self.run,
            self.code,
            env,
            self.match_start,
            self.is_sol,
            self.forbidden,
            self.pending,
            self.ceiling,
            self.ceiling_message,
            self.probing,
        ) = checkpoint
        # The parameters are copied out rather than adopted: an alternation rewinds to the same checkpoint once per
        # branch, so handing a branch the checkpoint's own dictionary would let its `(set)` reach back into what the
        # branch after it rewinds to. Everything else here is either a value or a length, and cannot be written through.
        self.env = dict(env)
        # The journal is undone before the token list is cut back: its entries are the only mutations that are not
        # appends, and popping them newest first restores every index they were recorded at.
        while len(self.trail) > trail_length:
            entry = self.trail.pop()
            if entry[0] == "retype":
                self.tokens[entry[1]] = entry[2]
            elif entry[0] == "inject":
                del self.tokens[entry[1]]
                self.provisional = entry[1]
            else:  # "run": where the provisional run stood before an open or a commit moved it
                self.provisional = entry[1]
        del self.tokens[token_count:]

    def consume(self):
        """
        Take the character or invalid byte at the position into the open run, opening one under the current code if none
        is. An invalid byte is no break and no byte-order mark; it advances a byte and a column like any other.
        """
        if self.ceiling is not None and not self.probing and self.position >= self.ceiling:
            # A committed character past a `(max)` window's edge exhausts it, failing the cut the window names. The open
            # run is left as it is so the tokens up to here still emit; a lookahead is exempt and reads on freely.
            raise CommitFailure(self.ceiling_message)
        if self.run is None:
            self.run = (wire.CODE_CHAR[self.code], self.mark, self.position)
        codepoint = self.chars[self.position]
        byte_length = self.byte_at[self.position + 1] - self.byte_at[self.position]
        is_break = codepoint == wire.LINE_FEED or (codepoint == wire.CARRIAGE_RETURN and not self._before_line_feed())
        if is_break:
            self.mark = wire.Mark(self.mark.byte + byte_length, self.mark.char + 1, self.mark.line + 1, 0)
            self.is_sol = True
        else:
            self.mark = wire.Mark(
                self.mark.byte + byte_length, self.mark.char + 1, self.mark.line, self.mark.column + 1
            )
            if codepoint != BYTE_ORDER_MARK:
                self.is_sol = False
        self.position += 1

    def _before_line_feed(self):
        """Whether a CR at the position is immediately followed by an LF, so the two are one break."""
        return self.position + 1 < len(self.chars) and self.chars[self.position + 1] == wire.LINE_FEED

    def cut(self):
        """
        End the open run, emitting it as a token if it took anything. Its text is the raw input bytes it spans, escaped
        for the wire — the bytes as they are, whether characters or an unparsed-invalid run.
        """
        if self.run is not None:
            character, start, start_position = self.run
            raw = self.raw[self.byte_at[start_position] : self.byte_at[self.position]]
            if raw:
                self.tokens.append(wire.Token(character, start, wire.escape(raw, character)))
            self.run = None

    def marker(self, code):
        """
        Emit a zero-width marker of `code`, cutting the open run before it, and track what it leaves open.

        A marker is paired by its code and never by the node that emitted it, which is how `check_markers` reads one
        too. A `(wrap)` is not the only thing that opens one: a block scalar opens with an `(emit)` because the position
        of its `end-scalar` depends on the chomping and is sometimes injected ahead of the breaks it holds, which a
        `(wrap)` cannot say. Pairing by node would leave those invisible to a parse that has to close what it opened.
        """
        self.cut()
        self.tokens.append(wire.Token(wire.CODE_CHAR[code], self.mark, ""))
        if code.startswith("begin-"):
            self.pending += ("end-" + code[len("begin-") :],)
        elif self.pending and self.pending[-1] == code:
            self.pending = self.pending[:-1]

    def error(self, message):
        """Emit an error token: `message` as its text, at the position, spanning no input. Cuts the open run first."""
        self.cut()
        self.tokens.append(wire.Token(wire.ERROR, self.mark, wire.escape(message.encode("utf-8"))))

    def open_provisional(self):
        """
        Open the provisional run: the tokens emitted from here on are undecided until a commit resolves them. Cuts the
        open character run first, so what was consumed before this point stays decided.
        """
        assert self.provisional is None, "a provisional run opened inside one"
        self.cut()
        self.trail.append(("run", self.provisional))
        self.provisional = len(self.tokens)

    def retype_provisional(self, payload, breaks):
        """
        Rewrite the open provisional run's codes by class: `breaks` for a token whose characters were consumed as a line
        break, `payload` for one that consumed anything else, a code of `None` keeping its class as it is. A marker or
        an error, having no consumed characters, keeps its code either way. Cuts the open character run first, so it is
        a token the rewrite sees.
        """
        assert self.provisional is not None, "a retype outside a provisional run"
        self.cut()
        for index in range(self.provisional, len(self.tokens)):
            token = self.tokens[index]
            if not token.text or token.code == wire.ERROR:
                continue
            is_break = wire.units(token.text, token.code)[0][0] in (wire.CARRIAGE_RETURN, wire.LINE_FEED)
            code = breaks if is_break else payload
            if code is None or wire.CODE_CHAR[code] == token.code:
                continue
            self.trail.append(("retype", index, token))
            self.tokens[index] = wire.Token(wire.CODE_CHAR[code], token.start, token.text)

    def inject_before(self, code):
        """
        Put a decided zero-width marker of `code` ahead of the open provisional run, and of everything undecided — at
        the run's own start, which is where the runtime's injection stands. The run begins one token later for it.
        """
        assert self.provisional is not None, "an injection outside a provisional run"
        start = self.tokens[self.provisional].start if self.provisional < len(self.tokens) else self.mark
        self.trail.append(("inject", self.provisional))
        self.tokens.insert(self.provisional, wire.Token(wire.CODE_CHAR[code], start, ""))
        self.provisional += 1

    def commit_provisional(self):
        """Resolve the open provisional run: its tokens are decided, and so is everything emitted after."""
        assert self.provisional is not None, "a commit with no provisional run open"
        self.trail.append(("run", self.provisional))
        self.provisional = None


def _leading_spaces(emitter):
    """Count the spaces at the position, without consuming them — what the in-line indentation auto-detection reads."""
    count = 0
    while emitter.position + count < len(emitter.chars) and emitter.chars[emitter.position + count] == 0x20:
        count += 1
    return count


def _skip_break(chars, position):
    """The position after the break at `position` — CR LF together, or a lone CR or LF — or `position` if none."""
    if position < len(chars) and chars[position] == wire.CARRIAGE_RETURN:
        return position + (2 if position + 1 < len(chars) and chars[position + 1] == wire.LINE_FEED else 1)
    if position < len(chars) and chars[position] == wire.LINE_FEED:
        return position + 1
    return position


def _detect_indent(emitter):
    """
    The indentation of the first content line at or after the position, peeked without consuming.

    A block collection is already at the start of that line; a block scalar is mid-line, at the end of its header, so
    the header line and any empty lines are skipped first. Where no content line follows — a block scalar of empty lines
    alone — the level is the widest of those empty lines instead, the spec's §8.1.1.1 fallback.
    """
    chars = emitter.chars
    position = emitter.position
    breaks = (wire.CARRIAGE_RETURN, wire.LINE_FEED)
    if not emitter.is_sol:
        while position < len(chars) and chars[position] not in breaks:
            position += 1
        position = _skip_break(chars, position)
    widest_empty = 0
    while position < len(chars):
        spaces = 0
        while position + spaces < len(chars) and chars[position + spaces] == 0x20:
            spaces += 1
        after = position + spaces
        if after >= len(chars) or chars[after] in breaks:
            widest_empty = max(widest_empty, spaces)
            position = _skip_break(chars, after)  # an empty line — skip it
            continue
        return spaces
    return widest_empty


def evaluate(expression, emitter, grammar):
    """
    Evaluate a value expression — a parameter, a literal, the matched text, or the arithmetic and dispatch over them.

    `Match()` is the input the enclosing `Bound`/`Bind` scope has consumed so far, which is what `Ord` and `Len` read.
    """
    if isinstance(expression, ir.Lit):
        return expression.value
    if isinstance(expression, ir.Param):
        return emitter.env.get(expression.name)  # an out-parameter (m, t) may be passed on before it is set
    if isinstance(expression, ir.Match):
        # Every unit in a Match scope is a character — Match feeds only indentation and ordinal arithmetic, which no
        # invalid byte reaches — so its codepoints reconstruct the text the scope consumed.
        return "".join(chr(codepoint) for codepoint in emitter.chars[emitter.match_start : emitter.position])
    if isinstance(expression, ir.Ord):
        return ord(evaluate(expression.arg, emitter, grammar)) - ord("0")  # (ord) is a digit 1-9 to its integer value
    if isinstance(expression, ir.Len):
        return len(evaluate(expression.arg, emitter, grammar))
    if isinstance(expression, ir.Add):
        return evaluate(expression.a, emitter, grammar) + evaluate(expression.b, emitter, grammar)
    if isinstance(expression, ir.Sub):
        return evaluate(expression.a, emitter, grammar) - evaluate(expression.b, emitter, grammar)
    if isinstance(expression, ir.Flip):
        value = emitter.env[expression.var]
        for branch in expression.branches:
            if branch.value == value:
                return evaluate(branch.item, emitter, grammar)
        raise KeyError(f"Flip on {expression.var}={value!r} has no branch")
    if isinstance(expression, ir.AutoDetectInLineIndent):
        return max(1, _leading_spaces(emitter))
    if isinstance(expression, ir.AutoDetectIndent):
        return max(1, _detect_indent(emitter) - emitter.env.get("n", 0))
    if isinstance(expression, ir.Ref):
        production = grammar[expression.name]
        arguments = tuple(evaluate(argument, emitter, grammar) for argument in expression.args)
        saved = emitter.env
        emitter.env = {**saved, **dict(zip(production.params, arguments))}
        result = evaluate(production.body, emitter, grammar)
        emitter.env = saved
        return result
    raise NotImplementedError(f"cannot evaluate {type(expression).__name__}")


def _accept():
    """The outermost continuation: the first whole match is the answer, so it is accepted and the run commits."""
    return True


def _probe(pattern, emitter, grammar):
    """
    Whether `pattern` matches at the position, leaving the emitter untouched — a lookahead that keeps no effect.

    A `(cut)` inside a lookahead is speculative, not a commit of the whole parse, so a `CommitFailure` here is caught
    and read as "did not match" rather than allowed to escape.
    """
    checkpoint = emitter.checkpoint()
    emitter.probing += 1  # a lookahead reads past a `Limited` window's edge freely; the rewind restores the count
    try:
        matched = match(pattern, emitter, grammar, _accept)
    except CommitFailure:
        matched = False
    emitter.rewind(checkpoint)
    return matched


def _gate_holds(gate, emitter, grammar):
    """Whether `gate` holds at the position — the peeked character found, and every zero-width guard true."""
    if gate.peek is not None and not _probe(gate.peek, emitter, grammar):
        return False
    return all(_probe(guard, emitter, grammar) for guard in gate.guards)


def _repeat(item, emitter, grammar, k):
    """
    Match `item` greedily zero or more times, then the continuation — backtracking to fewer repetitions if it fails.

    A zero-width match cannot repeat without looping, so it is taken once and no more.
    """
    checkpoint = emitter.checkpoint()
    before = emitter.position

    def more():
        if emitter.position == before:
            return k()  # a zero-width repetition — keep this one and stop, or the repetition would never end
        return _repeat(item, emitter, grammar, k)

    if match(item, emitter, grammar, more):
        return True
    emitter.rewind(checkpoint)
    return k()


def _forbidden_here(emitter, grammar):
    """
    Whether an in-scope `(exclude)` guard matches at a start of line here — where content must not begin.

    This is the reference's `forbidding`: a document forbids `c-forbidden` (a `---` or `...` line) throughout, so a
    plain scalar cannot run past the document boundary. The guard is checked only at a start of line, where such a
    marker can appear.
    """
    if not emitter.is_sol or not emitter.forbidden:
        return False
    patterns = emitter.forbidden
    emitter.forbidden = ()  # a forbidden pattern is matched without the guard, or it would forbid its own characters
    try:
        return any(_probe(pattern, emitter, grammar) for pattern in patterns)
    finally:
        emitter.forbidden = patterns


def _fail(emitter, message):
    """
    Emit the error where the parse stopped — `message`, or bare when empty — and close what it left open.

    The emitter is already at the end of what cleanly matched: at the last cut for a committed failure, at the start for
    an uncommitted one. A raise skips the frames that would have closed the `(wrap)`s the parse was inside, so they are
    closed here instead: a `begin` marker gets its `end` on every path, which is what lets the fold that rebuilds the
    production tree stand on an errored stream at all — and, once the parse resumes, is what keeps the next document a
    sibling of the failed one rather than a child of it.

    The three land in the one order that keeps their positions meaning what they say. All are zero-width and here, so
    only their order distinguishes them: the error is *inside* what failed, so it comes first; the unparsed run the
    recovery brings back is inside *nothing*, so the markers close before it.

    What the parser does about the input from there is not decided here: that is the grammar's `l-recover`, which the
    caller matches. The guard is cleared because an `(exclude)` the abandoned parse had in scope never got to unwind,
    and the recovery is entitled to the guards its own rules declare and no others; the `(max)` window goes for the same
    reason, so the recovery reads on past the edge the abandoned parse had failed against.
    """
    emitter.code = "unparsed-text"  # a raise skips the token frames' cleanup; from here on the input is unparsed
    emitter.forbidden = ()
    emitter.ceiling = None
    emitter.ceiling_message = None
    emitter.error(message)
    while emitter.pending:
        emitter.marker(emitter.pending[-1])


def match(node, emitter, grammar, k):
    """
    Match `node` from the emitter's position and call the continuation `k` for each way it matches, in greedy order.

    Backtracking is success-continuation style: on a match the interpreter calls `k`, and `k` returns whether the rest
    of the parse succeeded from there. If it did, the match commits and this returns True, leaving the emitter in that
    state; if it did not, the match is rewound — tokens and position both — and the next way is tried. This returns
    False once every way is exhausted, having left the emitter as it found it. Nothing is committed until `k` accepts.
    """
    if isinstance(node, ir.Char):
        if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] == node.cp:
            if _forbidden_here(emitter, grammar):
                return False
            checkpoint = emitter.checkpoint()
            emitter.consume()
            if k():
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Range):
        codepoint = emitter.chars[emitter.position] if emitter.position < len(emitter.chars) else None
        if codepoint is not None and node.lo <= codepoint <= node.hi:
            if _forbidden_here(emitter, grammar):
                return False
            checkpoint = emitter.checkpoint()
            emitter.consume()
            if k():
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Invalid):
        # A byte that begins no character. It belongs to no set, so only the recovery rules ask for it, where a run of
        # these becomes one unparsed-invalid token.
        if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] is None:
            if _forbidden_here(emitter, grammar):
                return False
            checkpoint = emitter.checkpoint()
            emitter.consume()
            if k():
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Empty):
        return k()
    if isinstance(node, ir.Ref):
        production = grammar[node.name]
        # A parameter passed as itself (`m`, not `n+1`) is by reference: the callee may set it — that is how the block
        # header hands the detected indent and chomping back — so its final value propagates out to the caller.
        by_reference = [
            parameter
            for parameter, argument in zip(production.params, node.args)
            if isinstance(argument, ir.Param) and argument.name == parameter
        ]
        arguments = tuple(evaluate(argument, emitter, grammar) for argument in node.args)
        saved_env = emitter.env
        saved_forbidden = emitter.forbidden  # inherited by the callee, and any (exclude) it adds is scoped to it
        # A production inherits the ambient parameters and overrides only the ones it declares, so `n` stays in scope
        # through a callee that does not name it — which is how the block header's indent detection still reads `n`. Its
        # run code, `(match)` origin and `(max)` window are the ones in force where it was entered, which a `(token)`, a
        # `(<<<)` and a `(max)` it lowers to restore past a nested one. The scopes come before the arguments, so a
        # production that declares one takes what it is passed instead of what is in force: a helper split out of the
        # middle of a `(token)` is entered under the pushed code but must restore the outer one, which its caller passes
        # it as the `code` it was itself entered under.
        emitter.env = {
            **saved_env,
            "code": emitter.code,
            "match_start": emitter.match_start,
            "ceiling": emitter.ceiling,
            "ceiling_message": emitter.ceiling_message,
            **dict(zip(production.params, arguments)),
        }

        def continue_out():
            callee_env = emitter.env
            callee_forbidden = emitter.forbidden
            caller_env = dict(saved_env)
            for parameter in by_reference:
                value = callee_env.get(parameter)
                if value is not None:
                    caller_env[parameter] = value
            emitter.env = (
                caller_env  # the caller sees its own parameters again, with any by-reference result carried out
            )
            emitter.forbidden = saved_forbidden
            if k():
                return True
            emitter.env = callee_env  # restore the callee's scope so its body can try its next way
            emitter.forbidden = callee_forbidden
            return False

        emitter.stack.append(node.name)
        if len(emitter.stack) >= DEPTH_LIMIT:
            trace = " -> ".join(emitter.stack[-DEPTH_TRACE:])
            emitter.stack.pop()
            raise DepthExceeded(f"production nesting reached {DEPTH_LIMIT}, deepest: ...{trace}")
        if len(emitter.stack) > DEPTH_LIMIT - DEPTH_TRACE:
            print(f"    depth {len(emitter.stack)}: {node.name}", file=sys.stderr)
        try:
            body = production.body
            if node.name in emitter.deterministic and isinstance(body, ir.Choice) and len(body.alternatives) > 1:
                # A deterministic production commits: the first alternative whose gate holds is the parse, and its
                # failure is the production's — no other is tried. The proved-disjoint gates are what make this the same
                # parse backtracking finds; an empty gate is the unconditional fallthrough and always holds, and a guard
                # refusing tries the next alternative, as its zero-width prefix fails it in backtracking.
                committed = False
                for alternative in body.alternatives:
                    if _gate_holds(alternative.gate, emitter, grammar):
                        committed = match(alternative, emitter, grammar, continue_out)
                        break
            else:
                committed = match(body, emitter, grammar, continue_out)
        finally:
            emitter.stack.pop()
        if not committed:
            emitter.env = saved_env
            emitter.forbidden = saved_forbidden
        return committed
    if isinstance(node, ir.Seq):

        def step(index):
            if index == len(node.items):
                return k()
            return match(node.items[index], emitter, grammar, lambda: step(index + 1))

        return step(0)
    if isinstance(node, ir.Alt):
        checkpoint = emitter.checkpoint()
        for item in node.items:
            if match(item, emitter, grammar, k):
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Diff):
        # A difference is a character class: the base, minus each excluded. The exclusions are checked first, as
        # lookaheads that consume nothing, so an excluded character is never trial-consumed to be rejected — which a
        # `Limited` window would otherwise see as a break or an over-run committed and refuse.
        for excluded in node.minus:
            if _probe(excluded, emitter, grammar):
                return False
        start = emitter.checkpoint()
        if not match(node.base, emitter, grammar, _accept):
            emitter.rewind(start)
            return False
        if k():
            return True
        emitter.rewind(start)
        return False
    if isinstance(node, ir.Opt):
        checkpoint = emitter.checkpoint()
        if match(node.item, emitter, grammar, k):  # greedy: prefer to match, fall back to the empty match
            return True
        emitter.rewind(checkpoint)
        return k()
    if isinstance(node, ir.Choice):
        for alternative in node.alternatives:
            if match(alternative, emitter, grammar, k):
                return True
        return False
    if isinstance(node, ir.Alternative):
        # The gate is a test and nothing more, so the peek is matched as a lookahead and the character it found is taken
        # by the `ConsumeChar` among the actions. Backtracking makes trying the parts in order the same as testing the
        # gate first and committing to it; what a gate means to a parser that does not backtrack is determinize's to
        # say.
        parts = () if node.gate.peek is None else (ir.Look(node.gate.peek),)
        parts += tuple(node.gate.guards) + tuple(node.actions)
        # A recovery riding the edge is the `(recover)` scope over the call it protects — the same handler, its resume
        # point the frame's own return, which is exactly the continuation the call already has here.
        first = node.first if node.recover is None else ir.Recover(node.recover, node.first)
        parts += tuple(item for item in (first, node.second) if item is not None)
        return match(ir.Seq(parts), emitter, grammar, k)
    if isinstance(node, ir.ConsumeChar):
        if emitter.position >= len(emitter.chars):
            raise AssertionError("a gated character is not there: the gate let through what it should have refused")
        checkpoint = emitter.checkpoint()
        emitter.consume()
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.ConsumePeeked):
        # The gate's literal, taken on its word — though this slow oracle re-checks the word, so a gate that lies is a
        # crash here rather than a wrong parse; the generated parser advances without a second look.
        for offset, codepoint in enumerate(node.text):
            if emitter.position + offset >= len(emitter.chars) or emitter.chars[emitter.position + offset] != codepoint:
                raise AssertionError("a gated literal is not there: the gate let through what it should have refused")
        checkpoint = emitter.checkpoint()
        for _codepoint in node.text:
            emitter.consume()
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.ConsumeLiteral):
        # The whole sequence or none of it: one comparison of a few characters, which either stands or leaves nothing
        # taken. Each character is matched as itself, so the start-of-line guard applies exactly as it would alone.
        checkpoint = emitter.checkpoint()
        for codepoint in node.text:
            if not match(ir.Char(codepoint), emitter, grammar, _accept):
                emitter.rewind(checkpoint)
                return False
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.ConsumeCountedSpan):
        # Exactly `count` characters of the set, all or nothing: one scan that counts, and a count short of the mark
        # matches nothing at all rather than leaving what it took. A non-positive count matches nothing, as a
        # zero-length indent does.
        count = evaluate(node.count, emitter, grammar)
        checkpoint = emitter.checkpoint()
        for _ in range(max(count, 0)):
            if not match(node.set, emitter, grammar, _accept):
                emitter.rewind(checkpoint)
                return False
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.ConsumeSpan):  # a `Star` over a character class, as the canonical form spells it
        return _repeat(node.set, emitter, grammar, k)
    if isinstance(node, ir.ConsumeTrimmedSpan):  # a `TrimStar`, as the canonical form spells it
        return match(ir.TrimStar(node.full, node.trim), emitter, grammar, k)
    if isinstance(node, ir.Star):
        return _repeat(node.item, emitter, grammar, k)
    if isinstance(node, ir.Plus):
        checkpoint = emitter.checkpoint()
        before = emitter.position

        def after_first():
            if emitter.position == before:
                return k()
            return _repeat(node.item, emitter, grammar, k)

        if match(node.item, emitter, grammar, after_first):
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.TrimStar):
        # A maximal run of `full`, its trailing `trim` characters given back: consume greedily, remembering where the
        # last character that was not `trim` ended, then rewind to it. Possessive, as its char-class guards make it —
        # the run never takes a character a later rule needs — so there is no shorter match to fall back to.
        span = emitter.checkpoint()  # the run kept so far; empty at the start, so an all-`trim` run consumes nothing
        while emitter.position < len(emitter.chars):
            at_trim = _probe(node.trim, emitter, grammar)
            before = emitter.position
            step = emitter.checkpoint()
            if not match(node.full, emitter, grammar, _accept):
                emitter.rewind(step)
                break
            if emitter.position == before:
                emitter.rewind(step)  # a zero-width match cannot repeat without looping
                break
            if not at_trim:
                span = emitter.checkpoint()
        emitter.rewind(span)
        return k()
    if isinstance(node, ir.Rep):
        count = evaluate(node.count, emitter, grammar)

        def step(index):
            if index >= count:  # a non-positive count matches nothing, as a zero-length indent does
                return k()
            return match(node.item, emitter, grammar, lambda: step(index + 1))

        return step(0)
    if isinstance(node, ir.Case):
        value = emitter.env.get(node.var)  # a parameter never set is no branch's value, so it takes the `else`
        for branch in node.branches:
            if branch.value == value:
                return match(branch.item, emitter, grammar, k)
        if node.default is not None:
            return match(node.default, emitter, grammar, k)
        return False
    if isinstance(node, ir.SetVar):
        checkpoint = emitter.checkpoint()
        emitter.env[node.param] = evaluate(node.value, emitter, grammar)
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Increase):
        checkpoint = emitter.checkpoint()
        emitter.env[node.param] = max(emitter.env.get(node.param, 0), emitter.mark.column)
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Bind):
        saved_start = emitter.match_start
        emitter.match_start = emitter.position

        def bound():  # noqa: N807 — a continuation, not a special method
            checkpoint = emitter.checkpoint()
            emitter.env[node.param] = evaluate(node.value, emitter, grammar)
            emitter.match_start = saved_start
            if k():
                return True
            emitter.rewind(checkpoint)  # undo the bound value and restore the Match() scope so the condition can go on
            return False

        matched = match(node.cond, emitter, grammar, bound)
        emitter.match_start = saved_start
        return matched
    if isinstance(node, ir.Lt):
        return k() if evaluate(node.a, emitter, grammar) < evaluate(node.b, emitter, grammar) else False
    if isinstance(node, ir.Le):
        return k() if evaluate(node.a, emitter, grammar) <= evaluate(node.b, emitter, grammar) else False
    if isinstance(node, ir.Max):
        if node.item is None:
            return k()  # the vendored grammar's bare length note, which libyeast never runs — only recovers to
        if emitter.ceiling is not None:
            return match(node.item, emitter, grammar, k)  # nested: only the outermost window applies, being the buffer
        emitter.ceiling = emitter.position + evaluate(node.limit, emitter, grammar)
        emitter.ceiling_message = node.message  # a consume past the edge raises this, keeping the tokens up to it

        def past_window():
            emitter.ceiling = None  # the window bounds `item`, not the parse that carries on once it has matched
            emitter.ceiling_message = None
            if k():
                return True
            return False  # `item` will try its next way; its own rewind restores the ceiling the checkpoint kept

        try:
            # The window is exhausted where the match would consume past the edge: `consume` fails the window's cut, and
            # the overflow unwinds to the recovery, keeping the tokens up to the edge — the error the caller emits cuts
            # the open run into the last of them. Any exit clears the ceiling: a cut unwinding past here leaves it.
            return match(node.item, emitter, grammar, past_window)
        finally:
            emitter.ceiling = None
            emitter.ceiling_message = None
    if isinstance(node, ir.Bound):
        saved_start = emitter.match_start
        emitter.match_start = emitter.position

        def scoped():  # noqa: N807 — a continuation, not a special method
            here = emitter.match_start
            emitter.match_start = saved_start
            if k():
                return True
            emitter.match_start = here  # restore the Match() scope so the wrapped item can try its next way
            return False

        matched = match(node.item, emitter, grammar, scoped)
        emitter.match_start = saved_start
        return matched
    if isinstance(node, ir.StartOfLine):
        return k() if emitter.is_sol else False
    if isinstance(node, ir.EndOfStream):
        return k() if emitter.position == len(emitter.chars) else False
    if isinstance(node, ir.Look):
        return k() if _probe(node.item, emitter, grammar) else False
    if isinstance(node, ir.NegLook):
        return k() if not _probe(node.item, emitter, grammar) else False
    if isinstance(node, ir.LiteralPeek):
        # The literal ahead and its follow test — one bounded zero-width test, spelled here as the lookahead it means:
        # each literal character as itself, a `then` as end-of-input-or-the-class, a `barrier` as a negative look, which
        # passes at the end of the input on its own.
        pattern = tuple(ir.Char(cp=codepoint) for codepoint in node.text)
        if node.then is not None:
            pattern += (ir.Alt(items=(ir.EndOfStream(), ir.Look(node.then))),)
        if node.barrier is not None:
            pattern += (ir.NegLook(node.barrier),)
        return k() if _probe(ir.Seq(items=pattern), emitter, grammar) else False
    if isinstance(node, ir.ExcludeAt):
        saved_forbidden = emitter.forbidden
        emitter.forbidden = saved_forbidden + (node.item,)  # an ongoing guard, in scope until the production returns
        if k():
            return True
        emitter.forbidden = saved_forbidden
        return False
    if isinstance(node, ir.LookBehind):
        target = emitter.position
        for start in range(target - 1, -1, -1):
            checkpoint = emitter.checkpoint()
            emitter.probing += 1  # a look-behind reads speculatively, past any `Limited` edge; the rewind restores it
            emitter.position = start
            reached = match(node.item, emitter, grammar, lambda: emitter.position == target)
            emitter.rewind(checkpoint)
            if reached:
                return k()
        return False
    if isinstance(node, ir.Token):
        entry = emitter.checkpoint()
        emitter.cut()
        surrounding = emitter.code  # the production's own code, restored at the token's trailing edge
        emitter.code = node.code

        def close_token():
            middle = emitter.checkpoint()
            emitter.cut()  # end the token's run at its trailing edge
            emitter.code = surrounding  # the following characters carry the surrounding code again
            if k():
                return True
            emitter.code = node.code
            emitter.rewind(middle)  # reopen the run so the wrapped item can try its next way
            return False

        matched = match(node.item, emitter, grammar, close_token)
        if not matched:
            emitter.rewind(entry)  # undo the leading cut and the code change
        return matched
    if isinstance(node, ir.Wrap):
        entry = emitter.checkpoint()
        emitter.marker(node.begin)

        def close_wrap():
            middle = emitter.checkpoint()
            emitter.marker(node.end)
            if k():
                return True
            emitter.rewind(middle)
            return False

        matched = match(node.item, emitter, grammar, close_wrap)
        if not matched:
            emitter.rewind(entry)
        return matched
    if isinstance(node, ir.Emit):
        checkpoint = emitter.checkpoint()
        emitter.marker(node.code)
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.PushCode):
        checkpoint = emitter.checkpoint()
        emitter.cut()
        emitter.code = node.code
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.PopCode):
        checkpoint = emitter.checkpoint()
        emitter.cut()
        emitter.code = emitter.env["code"]  # the production's own run code, held on its frame
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.OpenMatch):
        checkpoint = emitter.checkpoint()
        emitter.match_start = emitter.position
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.CloseMatch):
        checkpoint = emitter.checkpoint()
        emitter.match_start = emitter.env["match_start"]  # the production's own `(match)` origin, held on its frame
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.OpenWindow):
        checkpoint = emitter.checkpoint()
        if emitter.ceiling is None:  # outermost-only: a nested window keeps the outer edge, being the buffer
            emitter.ceiling = emitter.position + evaluate(node.limit, emitter, grammar)
            emitter.ceiling_message = node.message
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.CloseWindow):
        checkpoint = emitter.checkpoint()
        emitter.ceiling = emitter.env["ceiling"]  # the production's own `(max)` window, held on its frame
        emitter.ceiling_message = emitter.env["ceiling_message"]
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.OpenProvisional):
        checkpoint = emitter.checkpoint()
        emitter.open_provisional()
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.RetypeProvisional):
        checkpoint = emitter.checkpoint()
        emitter.retype_provisional(node.payload, node.breaks)
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.InjectBefore):
        checkpoint = emitter.checkpoint()
        emitter.inject_before(node.code)
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.CommitProvisional):
        checkpoint = emitter.checkpoint()
        emitter.commit_provisional()
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Cut):
        if k():
            return True
        raise CommitFailure(node.message)  # committed: unwind past the backtracking, this is the error
    if isinstance(node, ir.PushMessage):
        # A committed region opens: the record pairs with the `PopMessage` that closes it, which marks it reached. A
        # failure that unwinds back here with the region never closed is the error; through a closed one it backtracks
        # like any other match — the commitment does not reach past its close. A `(commit)` scope's terms exactly, the
        # close standing where the scope's end stood.
        record = [False]
        emitter.commitments.append(record)
        if k():
            return True
        popped = emitter.commitments.pop()
        assert popped is record, "a committed region closed out of order"
        if record[0]:
            return False
        raise CommitFailure(node.message)
    if isinstance(node, ir.PopMessage):
        record = emitter.commitments.pop()
        record[0] = True  # reached: the region's commitment is kept, whatever backtracking does after
        if k():
            return True
        emitter.commitments.append(record)  # backtracked into the region: it is open again, though already kept
        return False
    if isinstance(node, ir.Commit):
        # A `(cut)` scoped to `item`: it is the error only where `item` never reaches its own end. `reached` is set the
        # first time `item` matches through to the continuation, so a continuation that then fails backtracks the whole
        # of `item` like any other match — the commitment does not reach past it. An `item` that cannot close never
        # reaches its end, and that is the error. A `(cut)` inside `item` fires on its own terms, escaping past here.
        reached = [False]

        def at_end():
            reached[0] = True
            return k()

        if match(node.item, emitter, grammar, at_end):
            return True
        if reached[0]:
            return False
        raise CommitFailure(node.message)
    if isinstance(node, ir.Error):
        checkpoint = emitter.checkpoint()
        emitter.error(MESSAGES[node.message])
        if k():
            return True
        emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Recover):
        depth, code, forbidden, env, ceiling, ceiling_message, commitments = (
            len(emitter.pending),
            emitter.code,
            emitter.forbidden,
            dict(emitter.env),
            emitter.ceiling,
            emitter.ceiling_message,
            len(emitter.commitments),
        )
        try:
            return match(node.item, emitter, grammar, k)
        except CommitFailure as failure:
            # The cut asks whether this rule answers for it. Undo what the abandoned parse left of the scopes it was
            # inside — those frames never got to, including a `(max)` window it failed inside of — and put this rule's
            # own back: the recovery is this rule's to name, so it reads this rule's parameters and not those of
            # whatever failed somewhere below it.
            stopped = emitter.checkpoint()
            emitter.code = code
            emitter.forbidden = forbidden
            emitter.env = env
            emitter.ceiling = ceiling
            emitter.ceiling_message = ceiling_message
            del emitter.commitments[commitments:]  # the regions the abandoned parse left open
            emitter.error(MESSAGES[failure.code])
            while len(emitter.pending) > depth:
                emitter.marker(emitter.pending[-1])  # close what `item` opened, down to here and no further
            if match(node.recovery, emitter, grammar, _accept):
                return k()  # recovered: carry on as though `item` had matched, so a repetition takes its next turn
            emitter.rewind(stopped)  # this rule does not answer for it after all: leave no trace and let it go on up
            raise
    raise NotImplementedError(f"interpreter does not support {type(node).__name__}")


def run(grammar, production, data, parameters=None, deterministic=frozenset()):
    """
    Run `production` on the UTF-8 `data`, returning the yeast tokens it emits — a rejection among them if it rejects.

    `deterministic` names the productions entered committed — the first alternative whose gate holds, no second try — so
    a grammar runs hybrid: committed where its decisions are proved, backtracking everywhere else. Empty backtracks all.

    `parameters` binds the production's parameters from the fixture's filename — `n`/`m` are integers, `c`/`t`/`r`
    strings. A production that declares `r` and is run without one resumes the way a zeroed `ys_options` does.

    The production is entered as a reference to it, the way every other rule is entered, rather than by matching its
    body: a rule run at the top is still a rule, and whatever watches references — the coverage gate — must see it.
    """
    # A caller names the production polymorphically — the fixture's, the root's — and a monomorphized grammar holds only
    # its specialized copies; resolve to the copy, its finite parameters moved into the name. The resume policy is read
    # first, before that move takes it from the arguments, since recovery needs it whether it stays a parameter or not.
    parameters = parameters or {}
    resume = parameters.get("r", "n")
    production, parameters = ir.entry(grammar, production, parameters)
    emitter = Emitter(data)
    emitter.deterministic = deterministic
    emitter.env = {name: int(value) if name in ("n", "m") else value for name, value in parameters.items()}
    if "r" in grammar[production].params:
        emitter.env.setdefault("r", resume)  # a production run without a resume policy takes the zeroed one, no-resume
    entry = ir.Ref(production, tuple(ir.Lit(emitter.env.get(name)) for name in grammar[production].params))

    # A cut says where the unwind lands and nothing else; what to do about the input from there is `l-recover`'s, which
    # under a resuming policy parses the rest of the stream — and that may commit and fail again. So recovery is a loop
    # rather than one handoff, and a second error inside a resumed document needs no mechanism of its own.
    node = entry
    failed_at = None
    while True:
        try:
            matched = match(node, emitter, grammar, _accept)
        except CommitFailure as failure:
            _fail(emitter, MESSAGES[failure.code])  # committed: the error names what the cut expected
        else:
            if matched:
                emitter.cut()
                return emitter.tokens
            # A root parse is total — it recovers rather than fails — so its failing without committing is a grammar
            # bug, not an input we accept; an isolated non-root production may fail, and reports what matched and where
            # it stopped.
            if production == ir.ROOT:
                raise AssertionError(f"{ir.ROOT} failed without committing: the root production must be total")
            _fail(emitter, "")  # uncommitted: a bare error where what matched ends, no expectation to name
        # Recovering to where we already recovered from would go round for ever: a failed cut on a document boundary
        # leaves `l-unparsed` nothing to consume, so what resumes there has to be what makes the progress.
        if failed_at == emitter.position:
            raise AssertionError(f"recovery at position {failed_at} consumed nothing: the parse cannot go on")
        failed_at = emitter.position
        # The parse has unwound past every rule that might have answered for it, so it is at the stream's own level and
        # there is no indentation left to bound the recovery by. The resume policy resolves the same way the entry did —
        # into the name where the grammar is monomorphized, so recovery re-enters the right copy rather than the base.
        recover, recover_args = ir.entry(grammar, RECOVER, {"n": -1, "r": resume})
        node = ir.Ref(recover, tuple(ir.Lit(recover_args[parameter]) for parameter in grammar[recover].params))
