# SPDX-License-Identifier: MIT
"""
A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

Slow and obviously correct: it matches a production against an input the way the grammar reads, character by character
with backtracking, and emits the yeast token stream — so that libyeast's grammar is proved to produce the reference's
tokens before any C exists to be wrong, and so that it is the net every normalization step is checked against. It takes
the grammar as an argument, so the same interpreter and the same fixtures judge the base grammar and every stage the
pipeline hands on.

It matches every node the IR defines, and a node it does not know raises rather than passing quietly — the fixture that
reached one is reported as the crash it is. The grammar as written: the character-level nodes, the repetitions, the
parameter machinery and its arithmetic, the assertions and the lookarounds, and the annotations that give the tokens
their codes and their markers. The canonical form beside it: the consumes a run of characters is said as, the pairs each
scope is written down to, and the provisional run's own actions. `RecoverWrapper` says where a failed `(cut)` stops
unwinding; what each node means is written where it is defined, and this matches them one for one.

Matching is success-continuation style: `match` calls a continuation for each way a node matches, in greedy order, and
the continuation reports whether the rest of the parse succeeded — so an alternation is re-entered when a later element
fails, the way the reference backtracks. A `(cut)` is where that stops: past it the parse does not backtrack, and if the
rest then fails it becomes an error token naming what was expected, after which the input from there comes back as
unparsed, each line split into its content and its break.
"""

import os
import sys
from typing import NamedTuple

import ir
import wire
import yaml

# The continuation-passing matcher recurses once per grammar step and once per repetition, so a match nests far deeper
# than Python's default limit allows even for the small conformance inputs. A caller running the recursive helpers a
# transformed grammar carries (see `check_normalize`) raises this further, from a stack large enough to hold it.
sys.setrecursionlimit(20000)

# A production may nest this deep before the parse is refused — a guard on runaway recursion (a recursive helper that
# never bottoms out, pathological nesting). It sits well above any legitimate depth; nearing it, the production stack is
# traced to stderr so what recurses is seen, and reaching it raises a clear error rather than leaving Python's own limit
# to fire a bare one.
DEPTH_LIMIT = 6000
DEPTH_TRACE = 40  # productions of the stack's deep end to show, and how near the cap to start showing them

# The text of every message a `(cut)` names, from the grammar's companion table — the one source the interpreter and the
# generated C table both read, so the error text the interpreter emits is the error text the parser will.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "grammar", "messages.yaml")) as _f:
    MESSAGES = yaml.safe_load(_f)

# The production a failed cut hands off to, `ir.RECOVER`: it brings the input back unparsed and, where the resume policy
# says so, carries on at the next document. Matching it, rather than emitting the tokens by hand, keeps recovery in the
# grammar, where the C parser generates it from — a cut says only where the unwind lands, never what to do about it.
RECOVER = ir.RECOVER


class Checkpoint(NamedTuple):
    """
    The whole of the parse's state at a point, so what a match did can be taken back.

    Named rather than positional because the two ways of taking it back want different parts of it: an ordinary failure
    wants all of it, where a failure on its way to a recovery wants only what the parse was inside of — see `give_back`.
    `token_count` and `trail_length` are lengths rather than the lists, which are taken back by cutting them to those.
    """

    position: int
    mark: object
    token_count: int
    trail_length: int
    run: object
    provisional: object
    provisional_mark: object
    code: str
    env: dict
    shadow: dict
    stack: tuple
    is_sol: bool
    forbidden: tuple
    pending: tuple
    ceiling: object
    ceiling_message: object
    probing: int
    did_fill_span: object


class _Recovery(NamedTuple):
    """
    What a `PushRecoveryAction` leaves standing: what answers for a failed cut inside the region, where the parse
    carries on once it has, and everything the unwind has to put back before either runs.

    One of these is what a `("recovery", entry, pairs)` on the parse's own stack holds, so the region stands where every
    other scope does and its `PopRecoveryAction` is held to taking the one open. `stack` is that stack as it was before
    the entry went on it, which is what the unwind puts back — the region and everything opened inside it going
    together.

    `returns` is how deep the return stack stood where the region opened. Running the recovery and then the resume
    completes the way the region was pushed in, so what follows is where that way's own caller carries on — this is
    where to find it, not a rule of its own.

    `is_closed` is the one element `PopRecoveryAction` writes through, and what says whether the region still stands: a
    cut past a region's close is answered by whatever stood before it, and one inside a region reopened by a way of its
    own is answered here again. It is a record and not a read of the stack, because an unwind gives the stack back on
    its way out — where the entry sits says nothing about what the region has already been.
    """

    recovery: object
    resume: object
    returns: int
    pending: int
    code: str
    stack: tuple
    forbidden: tuple
    env: dict
    ceiling: object
    ceiling_message: object
    is_closed: list


class Coverage:
    """
    What a run reached and what it saw refuse, by production name.

    Filled by the parse itself, where the productions are entered and handed back, rather than by wrapping the matcher
    from outside: a wrapper records only the calls that go through the name it replaced, so a recursion reaching one
    another way is missed, and nothing says so. A run given one of these records into it; a run given none records
    nothing and pays no attention.
    """

    def __init__(self):
        self.reached = set()  # its body offered a solution, or a value expression evaluated it
        self.rejected = set()  # it failed to match, or a gate that stands for it refused


class DepthExceeded(Exception):
    """Raised when a production nests past `DEPTH_LIMIT` — runaway recursion, refused after its trace is shown."""


BYTE_ORDER_MARK = 0xFEFF  # consumed without ending the start of a line, unlike any other character

# What the runs have asked of a global that one value for the parse could not have answered: the reads where the stack
# beside it holds something other than the slot does. It does not refuse — the stack answers correctly either way — so a
# grammar is judged by this falling rather than by whether the corpus survives it, and at none the slot is the stack. A
# grammar whose productions still declare the parameters holds no globals at all and adds nothing here, so what this
# counts is the tail of the pipeline where they are.
ASKED = {"flattened": 0}


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
    a token annotation's edge or a marker. A checkpoint captures everything a match can be undone to — what it read,
    what it emitted and what it was inside of, tokens and all — and nothing about where a failure is going, which is why
    `failing` and `unwinding` are no part of one.
    """

    def __init__(self, raw):
        self.raw = raw  # the input bytes, as they are: a token's text is a slice of these, never a re-encoding
        self.chars, self.byte_at = _decode(raw)  # one unit per character or invalid byte; position indexes them
        self.position = 0
        self.mark = wire.Mark(0, 0, 1, 0)
        self.tokens = []
        self.run = None  # (code character, start mark, start position) of the open run, or None
        self.provisional = None  # where the open provisional run begins in `tokens`, or None — only one is open
        self.provisional_mark = None  # where the run's mark cuts `tokens` in two, or None — re-taken, the last wins
        self.trail = []  # the provisional undo journal — a retyped code, an injected marker: the only token mutations
        # that are not appends, which a rewind pops to undo what a token-count truncation cannot
        self.code = "unparsed-text"  # the token code the next character carries; a `(token)` sets it
        self.stack = ()  # the unified stack: what the parse must give back on its way out, innermost last, each entry a
        # `(kind, value, the pairs it was opened for)`. It holds the codes the open `(token)`s displaced, the
        # indentation in force, the `(max)` window an open displaced, a record per open committed region and per open
        # recovery region, the position each turn that must take a character opened at, and an entry per settled region
        # still open — every scope the grammar writes as a pair, on the one stack, so a close is held to taking the
        # scope standing open: of its kind, and one of the pairs the open stands for. One stack for the parse and not
        # one per production, so a pair a factoring split across a call closes off the same stack it opened. The entry
        # the run itself seeds is opened for no pair, no half of the grammar having written it
        self.env = {}  # the current production's parameters (n/m/c/t/r) and their values
        self.is_sol = True  # at the start of a line: true at the start of the input, and after every break
        self.offset = 0  # what the first line's marks are short by where a run begins mid-line, which only a rule run
        # on its own does: the input is a slice of a line and its marks count from its own start, where the grammar
        # measures against the column that slice would stand at. Later lines begin where they say they do
        self.forbidden = ()  # patterns that must not match at a start of line — the ongoing `(exclude)` guards in scope
        self.pending = ()  # the `end` markers of the `(wrap)`s the parse is inside, outermost first
        self.ceiling = None  # the position a `(max)` window ends at, past which committed input may not be consumed
        self.ceiling_message = None  # the cut message a consume past the ceiling raises — the window's, held with it.
        # Windows do not nest, so the outermost is the one in force: an open displaces the window it finds and sets one
        # of its own only where it displaced none, and its close puts back what it displaced
        self.probing = 0  # how many lookaheads are in progress — a probe may read past the ceiling, a commit may not
        self.did_fill_span = (
            None  # whether the `ConsumeLimitedSpanAction` just performed took its whole limit, and nothing
        )
        # where the last action performed was any other: the answer belongs to the action in front of the guard that
        # reads it, so every other action takes it away and asking with none there is a fault rather than a `False`
        self.entered = []  # the productions currently entered, outermost first — the depth guard's trace of what nests
        self.returns = []  # where each entered production carries on when it matches, outermost first — the return
        # stack the generated parser keeps, held here so an unwind can carry on at a call's return point rather than
        # only where the Python call stack happens to be. Pushed and taken back with `entered`, one for one
        self.failing = None  # the message of the `(cut)` a failure is unwinding to a recovery for, or None where no cut
        # stands behind it. It says the unwind gives back only the scopes: what was read stays read and what was emitted
        # stands, which is what the recovery carries on over and where its error is stamped. No part of a checkpoint,
        # for the same reason `unwinding` is not
        self.unwinding = (
            None  # the pairs of the `PopBackTrackAction` a failure is unwinding to, or None where the failure is
        )
        # an ordinary one. A settled region's ways stand between its close and its open as live choices, so while this
        # is set no choice takes another way: the failure travels to the open, which gives the region back whole and
        # clears it. No part of a checkpoint — it says where a failure is going, not where the parse has been
        self.coverage = None  # a `Coverage` to fill where the caller wants one, and nothing where it does not: what
        # each production was seen to do is recorded where it happens, so no caller can reach a production by a route
        # the recording does not cover
        self.deterministic = (
            frozenset()
        )  # the productions entered committed — first holding gate, no second try — which
        # `run` sets from `normalize.deterministic_productions`; empty runs the whole grammar backtracking
        self.holding = frozenset()  # the invariants the caller says this grammar establishes, by name. A run asserts
        # what they promise rather than reading the promise off the grammar, which would only say the shape again — an
        # independent check is one the pipeline tells and the parse tries
        self.holds_indent = False  # whether the grammar says where the indentation changes, which `run` reads off it.
        # While it is both a parameter and the stack's, only a grammar carrying the pushes can be held to the two
        # agreeing; this goes when the parameter does
        self.passing_arguments = False  # while a call's arguments are read. The new indentation is pushed before the
        # call and passed to it as well, so the argument reads `n` under the push its own value made — the two disagree
        # there and nowhere else, and only until the argument goes
        self.globals = ()  # the `ir.GLOBAL_PARAMS` no production of this grammar declares, which `run` reads off it.
        # One value for the parse rather than one per call, so a call carries what the callee left in one back out;
        # until the read-global steps take the declarations away they are parameters, scoped like any other
        self.shadow = {}  # a stack per global, what a `(set)` puts on and a `(clear)` takes off. A read takes the top,
        # which is right however the writes nest, so the parse stands on the stack's answer. What the stack and the slot
        # beside it differ by is tallied module-wide rather than per run, in `ASKED`: it is one number over the whole
        # corpus rather than a property of any one parse, and it is what a step is judged by.

    def checkpoint(self):
        return Checkpoint(
            self.position,
            self.mark,
            len(self.tokens),
            len(self.trail),
            self.run,
            self.provisional,
            self.provisional_mark,
            self.code,
            dict(self.env),
            dict(self.shadow),
            self.stack,
            self.is_sol,
            self.forbidden,
            self.pending,
            self.ceiling,
            self.ceiling_message,
            self.probing,
            self.did_fill_span,
        )

    def rewind(self, held):
        """
        Take the parse back to `held` whole — what it read, what it emitted and what it was inside of.

        What `give_back` does where nothing is unwinding, and what a probe or a declining recovery does having cleared
        what was.
        """
        # Reaching it with a cut still unwinding would hand back the input the recovery carries on from, which is the
        # one thing that unwind may not do — a wrong parse rather than a failure, so it is refused here.
        assert self.failing is None, "the input is given back under a cut still unwinding"
        self.position = held.position
        self.mark = held.mark
        self.run = held.run
        self.provisional = held.provisional
        self.provisional_mark = held.provisional_mark
        self.is_sol = held.is_sol
        self.pending = held.pending
        self.probing = held.probing
        self.did_fill_span = held.did_fill_span
        self._restore_scopes(held)
        # The journal is undone before the token list is cut back: its entries are the only mutations that are not
        # appends, and popping them newest first restores every index they were recorded at. The run start and its mark
        # are values the checkpoint restored above, so a rewound trail leaves only the token surgery to reverse.
        while len(self.trail) > held.trail_length:
            entry = self.trail.pop()
            if entry[0] == "retype":
                self.tokens[entry[1]] = entry[2]
            else:  # "inject": a marker inserted mid-list, deleted to undo what a truncation would miss
                del self.tokens[entry[1]]
        del self.tokens[held.token_count :]

    def _restore_scopes(self, held):
        """Put back what the parse was inside of at `held` — the scopes, and nothing about what it read or emitted."""
        self.code = held.code
        self.stack = held.stack
        self.forbidden = held.forbidden
        self.ceiling = held.ceiling
        self.ceiling_message = held.ceiling_message
        # The parameters are copied out rather than adopted: an alternation rewinds to the same checkpoint once per
        # branch, so handing a branch the checkpoint's own dictionary would let its `(set)` reach back into what the
        # branch after it rewinds to. Everything else here is either a value or a length, and cannot be written through.
        self.env = dict(held.env)
        self.shadow = dict(held.shadow)  # copied out for the same reason: a `(set)` must not reach back through it

    def is_unwinding(self):
        """Whether a failure is on its way somewhere — to a settled region's open, or to whatever answers for a cut."""
        return self.failing is not None or self.unwinding is not None

    def give_back(self, held):
        """
        Take back to `held` what the failure passing through gives back.

        An ordinary failure, and a settled region being given back, give back the whole of it. A failure on its way to a
        recovery gives back only the scopes: what it read is read and what it emitted stands — the recovery carries on
        from where the input got to, over the tokens already there — so the position, the marks and the tokens are left
        as the failure left them. Each frame taking back its own scopes is what leaves the recovery's own standing where
        the unwind stops, so no snapshot has to say what they were.
        """
        if self.failing is None:
            self.rewind(held)
        else:
            self._restore_scopes(held)

    def consume(self):
        """
        Take the character or invalid byte at the position into the open run, opening one under the current code if none
        is, and say whether it was taken. An invalid byte is no break and no byte-order mark; it advances a byte and a
        column like any other.

        The one character it declines is a committed one past a `(max)` window's edge, which exhausts the window: the
        cut the window names stands behind that failure, which is what `failing` says. A lookahead is exempt and reads
        on freely, and the open run is left as it is so the tokens up to here still emit.
        """
        if self.ceiling is not None and not self.probing and self.position >= self.ceiling:
            self.failing = self.ceiling_message
            return False
        if self.run is None:
            self.run = (wire.CODE_CHAR[self.code], self.mark, self.position)
        codepoint = self.chars[self.position]
        byte_length = self.byte_at[self.position + 1] - self.byte_at[self.position]
        is_break = codepoint == wire.LINE_FEED or (
            codepoint == wire.CARRIAGE_RETURN and not self._is_before_line_feed()
        )
        if is_break:
            self.mark = wire.Mark(self.mark.byte + byte_length, self.mark.char + 1, self.mark.line + 1, 0)
            self.is_sol = True
        else:
            # A byte-order mark is no character of the line: it neither ends the start of the line nor takes a column,
            # so what follows it stands where it would have stood without it.
            is_byte_order_mark = codepoint == BYTE_ORDER_MARK
            self.mark = wire.Mark(
                self.mark.byte + byte_length,
                self.mark.char + 1,
                self.mark.line,
                self.mark.column + (not is_byte_order_mark),
            )
            if not is_byte_order_mark:
                self.is_sol = False
        self.position += 1
        return True

    def _is_before_line_feed(self):
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
                self.tokens.append(wire.TokenWrapper(character, start, wire.escape(raw, character)))
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
        self.tokens.append(wire.TokenWrapper(wire.CODE_CHAR[code], self.mark, ""))
        if code.startswith("begin-"):
            self.pending += ("end-" + code[len("begin-") :],)
        elif self.pending and self.pending[-1] == code:
            self.pending = self.pending[:-1]

    def error(self, message):
        """Emit an error token: `message` as its text, at the position, spanning no input. Cuts the open run first."""
        self.cut()
        self.tokens.append(wire.TokenWrapper(wire.ERROR, self.mark, wire.escape(message.encode("utf-8"))))

    def open_provisional(self):
        """
        Open the provisional run: the tokens emitted from here on are undecided until a commit resolves them. Cuts the
        open character run first, so what was consumed before this point stays decided.
        """
        assert self.provisional is None, "a provisional run opened inside one"
        self.cut()
        self.provisional = len(self.tokens)

    def mark_provisional(self):
        """
        Mark the open run's current position, cutting the held tokens into the region before the mark and the region
        from the mark on — the side a later retype or injection names. Cuts the open character run first, so the mark
        falls on a token boundary. Re-taken in a marked run, the mark moves — the last taken wins — and the checkpoint
        carries the one it replaces, so a rewind restores it.
        """
        assert self.provisional is not None, "a mark outside a provisional run"
        self.cut()
        self.provisional_mark = len(self.tokens)

    def _region(self, region):
        """The half-open token index range `region` names within the open run — `all`, or a side of the mark."""
        if region == "all":
            return self.provisional, len(self.tokens)
        assert self.provisional_mark is not None, f"a {region} retype with no mark taken"
        if region == "before_mark":
            return self.provisional, self.provisional_mark
        return self.provisional_mark, len(self.tokens)

    def retype_provisional(self, rest, breaks, region):
        """
        Rewrite the held tokens in `region` by kind: `breaks` for a token whose characters were consumed as a line
        break, `rest` for one that consumed anything else, a code of `None` keeping its kind as it is. A marker or an
        error, having no consumed characters, keeps its code either way. Cuts the open character run first, so it is a
        token the rewrite sees.
        """
        assert self.provisional is not None, "a retype outside a provisional run"
        self.cut()
        start, stop = self._region(region)
        for index in range(start, stop):
            token = self.tokens[index]
            if not token.text or token.code == wire.ERROR:
                continue
            is_break = wire.units(token.text, token.code)[0][0] in (wire.CARRIAGE_RETURN, wire.LINE_FEED)
            code = breaks if is_break else rest
            if code is None or wire.CODE_CHAR[code] == token.code:
                continue
            self.trail.append(("retype", index, token))
            self.tokens[index] = wire.TokenWrapper(wire.CODE_CHAR[code], token.start, token.text)

    def inject_before(self, codes, at):
        """
        Put the decided zero-width markers `codes`, in order, into the open run at `at` — its `start`, ahead of the
        whole run, or its `mark`, between the two sides. Cuts the open character run first. The run start moves past a
        start injection, so the markers it puts there are decided; a mark injection stands behind the pre-mark tokens.
        """
        assert self.provisional is not None, "an injection outside a provisional run"
        self.cut()
        if at == "mark":
            assert self.provisional_mark is not None, "a mark injection with no mark taken"
            index = self.provisional_mark
        else:
            index = self.provisional
        start = self.tokens[index].start if index < len(self.tokens) else self.mark
        for offset, code in enumerate(codes):
            self.trail.append(("inject", index + offset))
            self.tokens.insert(index + offset, wire.TokenWrapper(wire.CODE_CHAR[code], start, ""))
        inserted = len(codes)
        # An index at or past the insertion point moves right by what was inserted — the run start where the injection
        # is its own, the mark where it stands at or past the injection.
        if self.provisional >= index:
            self.provisional += inserted
        if self.provisional_mark is not None and self.provisional_mark >= index:
            self.provisional_mark += inserted

    def commit_provisional(self):
        """Resolve the open provisional run and its mark: its tokens are decided, and so is everything emitted after."""
        assert self.provisional is not None, "a commit with no provisional run open"
        self.provisional = None
        self.provisional_mark = None


def evaluate(expression, emitter, grammar):
    """
    Evaluate a value expression — a parameter, a literal, the matched text, or the arithmetic and dispatch over them.

    `Match()` is the text of the open run — what the rule has just matched — which is what `AtoiValue` and `LenValue`
    read.
    """
    return _EVALUATE(expression, emitter, grammar)


def _evaluated_parameter(node, emitter, grammar):
    """A parameter's value: what the environment holds for it, refused where nothing holds one."""
    value = emitter.env.get(node.name)  # an out-parameter (m, t) may be passed on before it is set
    if node.name == "n" and emitter.holds_indent and not emitter.passing_arguments:
        # The indentation is on its way from a parameter to the stack, and both are kept while the corpus decides
        # whether the pushes stand where they should. Every read of it compares the two, wherever the grammar has been
        # through `push-indents` — before that step there are no pushes and nothing to compare against.
        held = _indent_in_force(emitter)
        if held != value:
            raise AssertionError(f"the stack holds an indentation of {held!r} where the parameter is {value!r}")
    if value is None and not emitter.passing_arguments:
        # Nothing holds a value for it: either no construct has measured one yet or a `ClearVarAction` has said the one
        # that did has ended. Passing it on is not reading it — an out-parameter travels to its setter unset.
        raise AssertionError(f"`{node.name}` is read where nothing holds a value for it")
    return value


def _evaluated_global(node, emitter, grammar):
    """
    A global's value: the top of its own stack.

    The stack answers, which is right however the writes nest; the slot beside it is what one value for the parse would
    have held, and the two differing is a read a global could not have answered. Counted rather than refused: it is the
    number the transformations drive to none, and at none the slot is the stack.
    """
    held = emitter.shadow.get(node.name, ())
    if not held:
        raise AssertionError(f"`{node.name}` is read where nothing holds a value for it")
    if held[-1] != emitter.env.get(node.name):
        ASKED["flattened"] += 1
    return held[-1]


def _evaluated_match(node, emitter, grammar):
    """
    The open run's text: what the rule has just matched, still in hand.

    Every unit in it is a character — the one rule that reads it matches a digit — so its codepoints reconstruct the
    text.
    """
    start = emitter.position if emitter.run is None else emitter.run[2]
    return "".join(chr(codepoint) for codepoint in emitter.chars[start : emitter.position])


def _evaluated_switch(node, emitter, grammar):
    """A value function's: the branch its parameter's value names, refused where no branch names it."""
    value = emitter.env[node.var]
    for branch in node.branches:
        if branch.value == value:
            return evaluate(branch.item, emitter, grammar)
    raise KeyError(f"Flip on {node.var}={value!r} has no branch")


def _evaluated_call(node, emitter, grammar):
    """A call's: the callee's body under the arguments this call hands it, the caller's own put back after."""
    production = grammar[node.name]
    arguments = tuple(evaluate(argument, emitter, grammar) for argument in node.args)
    saved = emitter.env
    emitter.env = {**saved, **dict(zip(production.params, arguments))}
    result = evaluate(production.body, emitter, grammar)
    emitter.env = saved
    if emitter.coverage is not None:
        emitter.coverage.reached.add(node.name)
    return result


# `AutoDetectIndentValue` is absent and raises: the official grammar spells it, libyeast's own reads `ColumnValue` where
# it stands, and a value invented for it here would be a lookahead the parser cannot make.
_EVALUATE = ir.Reading(
    "the value an expression works out: an indentation or length as an integer, a finite parameter as its string, or "
    "`None` where the parameter it reads holds nothing",
    {
        ir.LitValue: lambda node, emitter, grammar: node.value,
        ir.ParamValue: _evaluated_parameter,
        ir.GlobalValue: _evaluated_global,
        ir.IndentValue: lambda node, emitter, grammar: _indent(emitter),
        ir.MatchValue: _evaluated_match,
        # The column the parse stands at, and what the first line's marks are short by where a run began mid-line.
        ir.ColumnValue: lambda node, emitter, grammar: emitter.mark.column
        + (emitter.offset if emitter.mark.line == 1 else 0),
        ir.AtoiValue: lambda node, emitter, grammar: int(evaluate(node.arg, emitter, grammar)),
        ir.LenValue: lambda node, emitter, grammar: len(evaluate(node.arg, emitter, grammar)),
        ir.AddValue: lambda node, emitter, grammar: evaluate(node.a, emitter, grammar)
        + evaluate(node.b, emitter, grammar),
        ir.SubValue: lambda node, emitter, grammar: evaluate(node.a, emitter, grammar)
        - evaluate(node.b, emitter, grammar),
        ir.FlipValue: _evaluated_switch,
        ir.RefCall: _evaluated_call,
    },
)


def _accept():
    """The outermost continuation: the first whole match is the answer, so it is accepted and the run commits."""
    return True


def _popped(emitter, kind, what, pair):
    """
    `(the value the stack's top entry holds, the pairs it was opened for, the stack without it)`, refusing a top that is
    not of `kind` or that no pair of `pair` opened.

    The whole of the discipline: a pop takes what its own push put there, so the top being something else means the two
    are not the pair they read as — an action moved across one it must not cross, or a push whose pop never ran. The
    kind alone cannot say it, two `(token)`s being the same kind and different pairs, so the halves carry which pairs
    they stand for and a close answers an open where the two share one. Sharing rather than matching, because a merge
    leaves a half standing for every pair it replaced.

    A top opened for no pair at all is the one the run itself put there, which no half of the grammar wrote and so
    nothing is held to.
    """
    if not emitter.stack:
        raise AssertionError(f"{what} is taken off an empty stack")
    held, value, opened = emitter.stack[-1]
    if held != kind:
        raise AssertionError(f"{what} is taken off the stack, which holds {held} there")
    if opened is not None and not opened & pair:
        raise AssertionError(f"{what} is taken off the stack, which holds one of another pair there")
    return value, opened, emitter.stack[:-1]


def _nodes(node):
    """`node` and every IR node nested within it."""
    yield node
    children = []
    ir.rebuilt(node, lambda child: (children.append(child), child)[1])
    for child in children:
        yield from _nodes(child)


def _indent_in_force(emitter):
    """The indentation the stack holds, or `None` where nothing has pushed one."""
    for kind, value, _pair in reversed(emitter.stack):
        if kind == "indent":
            return value
    return None


def _indent(emitter):
    """
    The indentation in force, read from whichever mechanism the grammar carries: the stack where it pushes, and the
    parameter where it does not. A grammar that pushes is held to the two agreeing at every read of `n`, so this is one
    value read two ways rather than a choice between two answers.
    """
    return _indent_in_force(emitter) if emitter.holds_indent else emitter.env.get("n")


def _probe(pattern, emitter, grammar):
    """
    Whether `pattern` matches at the position, leaving the position, the tokens and the scopes as it found them — a
    lookahead that keeps no effect.

    A `(cut)` inside a lookahead is speculative, not a commit of the whole parse, so a failure unwinding to one stops
    here and is read as "did not match" rather than carrying on out.
    """
    checkpoint = emitter.checkpoint()
    emitter.probing += 1  # a lookahead reads past a `(max)` window's edge freely; the rewind restores the count
    did_match = match(pattern, emitter, grammar, _accept)
    if emitter.failing is not None:
        emitter.failing = None
        did_match = False
    emitter.rewind(checkpoint)
    return did_match


def _does_gate_hold(gate, emitter, grammar):
    """Whether `gate` holds at the position — every guard it asks true, in any order, none of them taking anything."""
    return all(_probe(guard, emitter, grammar) for guard in gate.guards)


def _repeat(item, emitter, grammar, k):
    """
    Match `item` greedily zero or more times, then the continuation — possessively, taking the maximal run and never
    falling back to fewer. A character-class run is single-outcome by construction: it is only ever followed by
    something off its own set, so a shorter match is never the one a valid parse needs and the maximal run is the
    answer. A run over anything else is taken the same way, which is the possessiveness `lower-runs` writes into the
    grammar as a settled region around the turns. On the continuation's failure the whole run is given back, the way any
    failed match leaves the position untouched. A zero-width match cannot repeat without looping, so it is taken once
    and no more.
    """
    checkpoint = emitter.checkpoint()
    while True:
        before = emitter.position
        step = emitter.checkpoint()
        if not match(item, emitter, grammar, _accept):
            if emitter.is_unwinding():
                emitter.give_back(checkpoint)  # a turn that failed under an unwind fails the run, it does not end it
                return False
            emitter.rewind(step)
            break
        if emitter.position == before:
            break  # a zero-width match: kept once, but repeating it would never end
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _longest_run(item, least, emitter, grammar, k):
    """
    Match the longest run of `item`, then the continuation — matching where the run took at least `least` turns.

    `least` is the whole of the difference between the two spellings of a run: one of none or more falls through to the
    continuation where nothing matched, and one that must take a turn refuses there. What it took is the longest run and
    there is no shorter one to fall back to — a continuation that fails fails the run, the way a failed match anywhere
    leaves the position untouched.

    A run of none or more over a character class is the same thing said as a scan — single-outcome by construction, so
    it is taken whole and judged whole with no turn of its own to give back. A zero-width turn cannot repeat without
    looping, so it is taken once and no more.
    """
    if least == 0 and ir.is_one_char(item, grammar):
        return _repeat(item, emitter, grammar, k)
    checkpoint = emitter.checkpoint()
    before = emitter.position

    def after_first():
        if emitter.position == before:
            return k()  # a zero-width turn: kept once, since repeating it would never end
        return _repeat(item, emitter, grammar, k)

    if match(item, emitter, grammar, after_first):
        return True
    if emitter.is_unwinding():
        return False  # taking no turn at all is another way, which an unwinding failure does not take
    emitter.rewind(checkpoint)
    return k() if least == 0 else False


def _is_forbidden_here(emitter, grammar):
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
    an uncommitted one. A failure travelling out gives back the scopes and nothing else, so the `(wrap)`s the parse was
    inside stand open when this is reached and are closed here: a `begin` marker gets its `end` on every path, which is
    what lets the fold that rebuilds the production tree stand on an errored stream at all — and, once the parse
    resumes, is what keeps the next document a sibling of the failed one rather than a child of it.

    The three land in the one order that keeps their positions meaning what they say. All are zero-width and here, so
    only their order distinguishes them: the error is *inside* what failed, so it comes first; the unparsed run the
    recovery brings back is inside *nothing*, so the markers close before it.

    What the parser does about the input from there is not decided here: that is the grammar's `l-recover`, which the
    caller matches. The guard is cleared because an `(exclude)` the abandoned parse had in scope never got to unwind,
    and the recovery is entitled to the guards its own rules declare and no others; every scope still standing on the
    stack goes for the same reason, so the recovery reads on past the `(max)` edge the abandoned parse had failed
    against, answers for none of what it had committed to, and is answered for by none of the regions it had opened.
    """
    emitter.code = "unparsed-text"  # from here on the input is unparsed
    emitter.stack = ()  # every scope left open goes, and with it whatever closing one would have restored
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

    A kind the machine has no step for raises rather than being passed over: the parse an oracle answers for by accident
    is a reading of the grammar rather than the grammar.
    """
    step = _MATCHED.get(type(node))
    if step is None:
        raise NotImplementedError(f"interpreter does not support {type(node).__name__}")
    # What a scan of a limited span left behind is the answer for the guard that asks about it, so every other action
    # takes it away: past one of those the guard would be reading what some earlier scan did. A call carries it — the
    # guard reading it heads a state of its own, which the way that made the scan hands control to — and so does every
    # shape the machine is made of, none of them being something the machine does to the input. Taken away here rather
    # than by each action in turn, since one that forgot would look exactly like one that remembered.
    if type(node) in _TAKES_THE_ANSWER_AWAY:
        emitter.did_fill_span = None
    return step(node, emitter, grammar, k)


# What every match of a single character does once it has found its character is the same, and what it found it by is
# the only thing that differs between them. It is written out at each of them all the same: this matcher recurses once
# per grammar step and a frame that stands across the continuation is paid for at every character of the input, so what
# a shared helper would save in lines it costs in the depth a parse can reach.


def _matched_char(node, emitter, grammar, k):
    """A literal character: taken where the one standing here is it."""
    if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] == node.cp:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.consume() and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _matched_set(node, emitter, grammar, k):
    """
    A character set: taken where the one standing here falls in one of its spans.

    The invalid byte is the interval `(-1, -1)`, which is how a set says it holds one: the decoder gives an invalid byte
    no codepoint, so `None` is the character it stands for here.
    """
    codepoint = emitter.chars[emitter.position] if emitter.position < len(emitter.chars) else None
    unit = -1 if codepoint is None and emitter.position < len(emitter.chars) else codepoint
    if unit is not None and any(low <= unit <= high for low, high in node.spans):
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.consume() and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _matched_range(node, emitter, grammar, k):
    """A range: taken where the character standing here falls between its ends."""
    codepoint = emitter.chars[emitter.position] if emitter.position < len(emitter.chars) else None
    if codepoint is not None and node.lo <= codepoint <= node.hi:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.consume() and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _matched_invalid(node, emitter, grammar, k):
    """
    A byte that begins no character.

    It belongs to no set, so only the recovery rules ask for it, where a run of these becomes one unparsed-invalid
    token.
    """
    if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] is None:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.consume() and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _matched_call(node, emitter, grammar, k):
    """The callee's body under the arguments this call hands it, the caller's own parameters put back after."""
    production = grammar[node.name]
    # A parameter passed as itself (`m`, not `n+1`) is by reference: the callee may set it — that is how the block
    # header hands the detected indent and chomping back — so its final value propagates out to the caller.
    by_reference = [
        parameter
        for parameter, argument in zip(production.params, node.args)
        if isinstance(argument, ir.ParamValue) and argument.name == parameter
    ]
    emitter.passing_arguments = True
    arguments = tuple(evaluate(argument, emitter, grammar) for argument in node.args)
    emitter.passing_arguments = False
    saved_env = emitter.env
    saved_forbidden = emitter.forbidden  # inherited by the callee, and any (exclude) it adds is scoped to it
    # A production inherits the ambient parameters and overrides only the ones it declares, so `n` stays in scope
    # through a callee that does not name it — which is how the block header's indent detection still reads `n`. The run
    # code and the `(max)` window are not among them: the code is the parse's own stack and the window a global, so a
    # callee reads what is in force rather than a copy taken at the call.
    emitter.env = {**saved_env, **dict(zip(production.params, arguments))}

    # A global is the parse's rather than any one call's, so what the callee left in one reaches the caller whatever the
    # call passed — and a `ClearVarAction` reaches it too, which is why this carries a cleared value out where the
    # by-reference pass keeps the caller's. Only where nothing declares them: until the read-global steps take the
    # declarations away they are parameters, and a call's own binding is the by-reference pass's to carry.
    def continue_out():
        callee_env = emitter.env
        callee_forbidden = emitter.forbidden
        caller_env = dict(saved_env)
        for parameter in by_reference:
            value = callee_env.get(parameter)
            if value is not None:
                caller_env[parameter] = value
        for name in emitter.globals:
            caller_env[name] = callee_env.get(name)
        emitter.env = caller_env  # the caller sees its own parameters again, with any by-reference result carried out
        emitter.forbidden = saved_forbidden
        if emitter.coverage is not None:
            emitter.coverage.reached.add(node.name)  # its body offered a solution, which is what reaching one is
        if k():
            return True
        emitter.env = callee_env  # restore the callee's scope so its body can try its next way
        emitter.forbidden = callee_forbidden
        return False

    emitter.entered.append(node.name)
    emitter.returns.append(continue_out)  # where this call carries on, which an unwind reads as its resume point
    if len(emitter.entered) >= DEPTH_LIMIT:
        trace = " -> ".join(emitter.entered[-DEPTH_TRACE:])
        emitter.entered.pop()
        emitter.returns.pop()
        raise DepthExceeded(f"production nesting reached {DEPTH_LIMIT}, deepest: ...{trace}")
    if len(emitter.entered) > DEPTH_LIMIT - DEPTH_TRACE:
        print(f"    depth {len(emitter.entered)}: {node.name}", file=sys.stderr)
    try:
        body = production.body
        if node.name in emitter.deterministic and isinstance(body, ir.ChoiceState) and len(body.alternatives) > 1:
            # A deterministic production commits: the first alternative whose gate holds is the parse, and its failure
            # is the production's — no other is tried. The proved-disjoint gates are what make this the same parse
            # backtracking finds; an empty gate is the unconditional fallthrough and always holds, and a guard refusing
            # tries the next alternative, as its zero-width prefix fails it in backtracking.
            did_match = False
            for alternative in body.alternatives:
                if _does_gate_hold(alternative.gate, emitter, grammar):
                    did_match = match(alternative, emitter, grammar, continue_out)
                    break
        else:
            did_match = match(body, emitter, grammar, continue_out)
    finally:
        emitter.entered.pop()
        emitter.returns.pop()
    if not did_match:
        emitter.env = saved_env
        emitter.forbidden = saved_forbidden
        if emitter.coverage is not None:
            emitter.coverage.rejected.add(node.name)
    return did_match


def _matched_sequence(node, emitter, grammar, k):
    """Each item in turn, what follows one being the continuation the one before it is matched under."""

    def step(index):
        if index == len(node.items):
            return k()
        return match(node.items[index], emitter, grammar, lambda: step(index + 1))

    return step(0)


def _matched_alternation(node, emitter, grammar, k):
    """Each item in the order held, the position given back between them, and the first that carries the parse wins."""
    checkpoint = emitter.checkpoint()
    for item in node.items:
        if match(item, emitter, grammar, k):
            return True
        if emitter.is_unwinding():
            return False  # a settled region is being given back, and taking another way here is what that forbids
        emitter.rewind(checkpoint)
    return False


def _matched_difference(node, emitter, grammar, k):
    """
    A character class: the base, minus each excluded.

    The exclusions are checked first, as lookaheads that consume nothing, so an excluded character is never
    trial-consumed to be rejected — which a `(max)` window would otherwise see as a consume committed past its edge and
    refuse.
    """
    for excluded in node.minus:
        if _probe(excluded, emitter, grammar):
            return False
    start = emitter.checkpoint()
    if not match(node.base, emitter, grammar, _accept):
        emitter.give_back(start)
        return False
    if k():
        return True
    emitter.give_back(start)
    return False


def _matched_optional(node, emitter, grammar, k):
    """The item where it matches, and the empty match where it does not — greedy, the match preferred."""
    checkpoint = emitter.checkpoint()
    if match(node.item, emitter, grammar, k):
        return True
    if emitter.is_unwinding():
        return False  # the empty match is another way, which a region being given back does not take
    emitter.rewind(checkpoint)
    return k()


def _matched_choice(node, emitter, grammar, k):
    """Each alternative in the order held, each entered on its own gate, and the first that carries the parse wins."""
    for alternative in node.alternatives:
        if match(alternative, emitter, grammar, k):
            return True
        if emitter.is_unwinding():
            return False
    return False


def _matched_way(node, emitter, grammar, k):
    """
    A way: its gate, then what it performs, then what it calls, all as one run.

    The gate is a set of questions and nothing more, each about the position the way is entered at, so they are asked in
    the order held and the order does not matter. Backtracking makes trying the parts in order the same as asking the
    gate first and committing to it; what a gate means to a parser that does not backtrack is determinize's to say.
    """
    # A hoisted gate makes the decision the production it admits used to make: where the gate is not one that call can
    # begin with it refuses, and the call never happens. So the gate saying no is that production saying no, and a run
    # recording coverage asks the gate on its own to attribute the refusal — otherwise gating a rule correctly would
    # make it look untested. A run recording nothing never asks, and pays nothing for the question.
    if emitter.coverage is not None and node.gate.guards and not _does_gate_hold(node.gate, emitter, grammar):
        for held in (node.first, node.second):
            called = getattr(held, "name", None)
            if called in grammar:
                emitter.coverage.rejected.add(called)
        return False
    parts = tuple(node.gate.guards) + tuple(node.actions)
    # A recovery riding the edge is the `(recover)` scope over the call it protects — the same handler, resuming where
    # the way carries on past that call, which is exactly the continuation the call already has here.
    first = node.first if node.recover is None else ir.RecoverWrapper(node.recover, node.first)
    parts += tuple(item for item in (first, node.second) if item is not None)
    return match(ir.SeqTree(parts), emitter, grammar, k)


def _matched_gated_char(node, emitter, grammar, k):
    """The character the gate found, taken on the gate's word."""
    if emitter.position >= len(emitter.chars):
        raise AssertionError("a gated character is not there: the gate let through what it should have refused")
    checkpoint = emitter.checkpoint()
    if emitter.consume() and k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_gated_literal(node, emitter, grammar, k):
    """
    The literal the gate found, taken on the gate's word.

    This slow oracle re-checks the word, so a gate that lies is a crash here rather than a wrong parse; the generated
    parser advances without a second look.
    """
    for offset, codepoint in enumerate(node.text):
        if emitter.position + offset >= len(emitter.chars) or emitter.chars[emitter.position + offset] != codepoint:
            raise AssertionError("a gated literal is not there: the gate let through what it should have refused")
    checkpoint = emitter.checkpoint()
    for _codepoint in node.text:
        if not emitter.consume():
            emitter.give_back(checkpoint)
            return False
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_literal(node, emitter, grammar, k):
    """
    A fixed sequence: the whole of it or none of it.

    One comparison of a few characters, which either stands or leaves nothing taken. Each character is matched as
    itself, so the start-of-line guard applies exactly as it would alone.
    """
    checkpoint = emitter.checkpoint()
    for codepoint in node.text:
        if not match(ir.OneCharSet(codepoint), emitter, grammar, _accept):
            emitter.give_back(checkpoint)
            return False
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_limited_span(node, emitter, grammar, k):
    """
    Up to `limit` characters of the set, taken in one scan, and whether the limit was reached left behind it.

    The taking always matches — a scan that finds fewer than the limit takes what is there and says so, where a counted
    scan takes none and is handed back — so what a way performs here cannot fail, and the question is asked past it by
    the guard that reads the answer.
    """
    limit = evaluate(node.limit, emitter, grammar)
    checkpoint = emitter.checkpoint()
    taken = 0
    while taken < limit and match(node.set, emitter, grammar, _accept):
        taken += 1
    emitter.did_fill_span = taken >= limit
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_did_fill_span(node, emitter, grammar, k):
    """Whether the scan in front of this took its whole limit, which is the only thing this asks."""
    if emitter.did_fill_span is None:
        raise AssertionError("the span a guard asks about is not the action in front of it, which performed no scan")
    return k() if emitter.did_fill_span else False


def _matched_span(node, emitter, grammar, k):
    """A maximal run of the set, which is a `StarTree` over a character class as the canonical form spells it."""
    if "every-consume-is-protected-by-a-gate" in emitter.holding and not _probe(node.set, emitter, grammar):
        raise AssertionError("a gated run is not there: the gate let through what it should have refused")
    return _repeat(node.set, emitter, grammar, k)


def _matched_trimmed_run(node, emitter, grammar, k):
    """
    A maximal run of `full`, its trailing `trim` characters given back.

    Consume greedily, remembering where the last character that was not `trim` ended, then rewind to it. Possessive, as
    its char-class guards make it — the run never takes a character a later rule needs — so there is no shorter match to
    fall back to.
    """
    span = emitter.checkpoint()  # the run kept so far; empty at the start, so an all-`trim` run consumes nothing
    while emitter.position < len(emitter.chars):
        at_trim = _probe(node.trim, emitter, grammar)
        before = emitter.position
        step = emitter.checkpoint()
        if not match(node.full, emitter, grammar, _accept):
            if emitter.is_unwinding():
                return False  # a turn that failed under an unwind fails the run, it does not end it
            emitter.rewind(step)
            break
        if emitter.position == before:
            emitter.rewind(step)  # a zero-width match cannot repeat without looping
            break
        if not at_trim:
            span = emitter.checkpoint()
    emitter.rewind(span)
    return k()


def _matched_counted_run(node, emitter, grammar, k):
    """The item exactly `count` times, one turn's continuation being the next turn."""
    count = evaluate(node.count, emitter, grammar)

    def step(index):
        if index >= count:  # a non-positive count matches nothing, as a zero-length indent does
            return k()
        return match(node.item, emitter, grammar, lambda: step(index + 1))

    return step(0)


def _matched_open_provisional(node, emitter, grammar, k):
    """The provisional run opened, where what is taken from here on may be given a different code later."""
    checkpoint = emitter.checkpoint()
    emitter.open_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_mark_provisional(node, emitter, grammar, k):
    """A mark in the provisional run, which a retype measures from."""
    checkpoint = emitter.checkpoint()
    emitter.mark_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_retype_provisional(node, emitter, grammar, k):
    """The provisional run given the codes it turned out to carry."""
    checkpoint = emitter.checkpoint()
    emitter.retype_provisional(node.rest, node.breaks, node.region)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_inject(node, emitter, grammar, k):
    """Markers put in front of what the provisional run holds, at the place named."""
    checkpoint = emitter.checkpoint()
    emitter.inject_before(node.codes, node.at)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_commit_provisional(node, emitter, grammar, k):
    """The provisional run closed, its tokens standing as they are."""
    checkpoint = emitter.checkpoint()
    emitter.commit_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_error(node, emitter, grammar, k):
    """An error token, left where the parse stands."""
    checkpoint = emitter.checkpoint()
    emitter.error(MESSAGES[node.message])
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_marker(node, emitter, grammar, k):
    """One marker, left where the parse stands."""
    checkpoint = emitter.checkpoint()
    emitter.marker(node.code)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_push_indent(node, emitter, grammar, k):
    """An indentation put in force until its pop takes it off."""
    checkpoint = emitter.checkpoint()
    # Working out what to push is not a read of the indentation in force: the level is what replaces it, and where it is
    # the parameter itself — an indentation a call established and handed back — the two differ here and nowhere else,
    # until the parameter goes.
    emitter.passing_arguments = True
    level = evaluate(node.level, emitter, grammar)
    emitter.passing_arguments = False
    emitter.stack += (("indent", level, node.pair),)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_pop_indent(node, emitter, grammar, k):
    """The indentation on top taken off, putting back the one its push displaced."""
    checkpoint = emitter.checkpoint()
    # The pop says which indentation it takes off, so that a step moving it, or moving something past it, can name what
    # the actions around it measure against. Nothing reads it here — a pop takes off whatever is on top — so it is
    # checked instead, and the pairing it stands for is refused where it does not hold. It is read once the pop has
    # happened, which is the scope it was written in: the level its push computed from the indentation this restores,
    # not from the one it put there.
    value, _opened, emitter.stack = _popped(emitter, "indent", "an indentation", node.pair)
    if node.level is not None:  # `strip-pop-levels` takes it off once no step reads it
        said = evaluate(node.level, emitter, grammar)
        if value != said:
            raise AssertionError(f"the pop takes off an indentation of {value!r} where it says {said!r}")
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_push_code(node, emitter, grammar, k):
    """A token code the characters from here on carry, the run cut where it takes over."""
    checkpoint = emitter.checkpoint()
    emitter.cut()
    emitter.stack += (("code", emitter.code, node.pair),)  # the code this push displaces, for its own pop to take back
    emitter.code = node.code
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_pop_code(node, emitter, grammar, k):
    """The code on top taken off, the run cut where the displaced one takes over again."""
    checkpoint = emitter.checkpoint()
    emitter.cut()
    emitter.code, _opened, emitter.stack = _popped(emitter, "code", "a `(token)` code", node.pair)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_open_window(node, emitter, grammar, k):
    """A budget of characters opened, past which a consume is the overflow the window names."""
    checkpoint = emitter.checkpoint()
    displaced = (emitter.ceiling, emitter.ceiling_message)
    if emitter.ceiling is None:  # outermost-only: an open under one is inside the budget that one already bounds
        emitter.ceiling = emitter.position + evaluate(node.limit, emitter, grammar)
        emitter.ceiling_message = node.message
    emitter.stack += (("window", displaced, node.pair),)  # the window this open displaced, for its close to take back
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_close_window(node, emitter, grammar, k):
    """The budget closed, putting back the window its open displaced."""
    checkpoint = emitter.checkpoint()
    displaced, _opened, emitter.stack = _popped(emitter, "window", "a `(max)` window", node.pair)
    emitter.ceiling, emitter.ceiling_message = displaced
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_cut(node, emitter, grammar, k):
    """A commit: what follows failing is this error, and nothing behind it is retried."""
    if k():
        return True
    if emitter.failing is None:
        emitter.failing = node.message
    return False


def _matched_open_committed(node, emitter, grammar, k):
    """
    A committed region opened, the error standing until its close is reached.

    The record pairs with the `PopMessageAction` that closes it, which marks it reached. A failure that unwinds back
    here with the region never closed is the error; through a closed one it backtracks like any other match — the
    commitment does not reach past its close. A `(commit)` scope's terms exactly, the close standing where the scope's
    end stood. The record is what the stack entry holds and `reached` is written through it, so a rewind puts back which
    regions stand and never what one of them has already been.
    """
    record = [False]
    emitter.stack += (("message", record, node.pair),)
    if k():
        return True
    popped, _opened, emitter.stack = _popped(emitter, "message", "a committed region", node.pair)
    assert popped is record, "a committed region closed out of order"
    if record[0]:
        return False
    if emitter.failing is None:
        emitter.failing = node.message
    return False


def _matched_close_committed(node, emitter, grammar, k):
    """The committed region closed, its commitment kept whatever backtracking does after."""
    record, opened, emitter.stack = _popped(emitter, "message", "a committed region", node.pair)
    record[0] = True
    if k():
        return True
    emitter.stack += (("message", record, opened),)  # backtracked into the region: open again, though kept
    return False


def _matched_committed(node, emitter, grammar, k):
    """
    A `(cut)` scoped to `item`: it is the error only where `item` never reaches its own end.

    `reached` is set the first time `item` matches through to the continuation, so a continuation that then fails
    backtracks the whole of `item` like any other match — the commitment does not reach past it. An `item` that cannot
    close never reaches its end, and that is the error. A `(cut)` inside `item` fires on its own terms, escaping past
    here.
    """
    reached = [False]

    def at_end():
        reached[0] = True
        return k()

    if match(node.item, emitter, grammar, at_end):
        return True
    if reached[0]:
        return False
    if emitter.failing is None:
        emitter.failing = node.message
    return False


def _matched_open_recovery(node, emitter, grammar, k):
    """A recovery region opened, which answers a cut from inside it that its close has not passed."""
    entry = _Recovery(
        recovery=node.recovery,
        resume=node.resume,
        returns=len(emitter.returns),
        pending=len(emitter.pending),
        code=emitter.code,
        stack=emitter.stack,
        forbidden=emitter.forbidden,
        env=dict(emitter.env),
        ceiling=emitter.ceiling,
        ceiling_message=emitter.ceiling_message,
        is_closed=[False],
    )
    emitter.stack += (("recovery", entry, node.pair),)
    if k():
        return True
    if emitter.failing is not None:
        if entry.is_closed[0]:
            return False  # the region has closed, so whatever answers for this stands further out
        return _recover(entry, emitter, grammar)
    held, _opened, emitter.stack = _popped(emitter, "recovery", "a recovery region", node.pair)
    assert held is entry, "a recovery region closed out of order"
    return False


def _matched_close_recovery(node, emitter, grammar, k):
    """The recovery region closed: a cut from here on is answered by whatever stood before it."""
    entry, opened, emitter.stack = _popped(emitter, "recovery", "a recovery region", node.pair)
    entry.is_closed[0] = True
    if k():
        return True
    if emitter.failing is not None:
        return False  # a cut past the close, which this region is no longer the one to answer for
    entry.is_closed[0] = False
    emitter.stack += (("recovery", entry, opened),)  # what follows failed: the region is open again for a way in it
    return False


def _matched_recovering(node, emitter, grammar, k):
    """The item, and where a cut inside it asks this rule to answer, the error and what answers for it instead."""
    depth, held = len(emitter.pending), emitter.checkpoint()
    if match(node.item, emitter, grammar, k):
        return True
    if emitter.failing is None:
        return False
    # The cut asks whether this rule answers for it. The scopes the abandoned parse was inside are already back — each
    # frame gave its own back on its way out — so what stands is this rule's own, and the recovery reads this rule's
    # parameters rather than those of whatever failed somewhere below it. What `item` opened is still open, to be closed
    # down to here.
    stopped = emitter.checkpoint()
    emitter.code, emitter.stack, emitter.forbidden = held.code, held.stack, held.forbidden
    emitter.env, emitter.ceiling, emitter.ceiling_message = dict(held.env), held.ceiling, held.ceiling_message
    code, emitter.failing = emitter.failing, None
    emitter.error(MESSAGES[code])
    while len(emitter.pending) > depth:
        emitter.marker(emitter.pending[-1])  # close what `item` opened, down to here and no further
    if match(node.recovery, emitter, grammar, _accept):
        return k()  # recovered: carry on as though `item` had matched, so a repetition takes its next turn
    emitter.rewind(stopped)  # this rule does not answer for it after all: leave no trace and let it go on up
    emitter.failing = code
    return False


def _matched_open_turn(node, emitter, grammar, k):
    """
    A turn that must take a character opens.

    Where its close is reached at the position this stood at, the turn has not matched, and a loop that would repeat it
    forever ends there instead.
    """
    checkpoint = emitter.checkpoint()
    emitter.stack += (("consume", emitter.position, node.pair),)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_close_turn(node, emitter, grammar, k):
    """The turn closes, and having taken no character is what says it did not match."""
    opened_at, opened, emitter.stack = _popped(emitter, "consume", "a turn that must take a character", node.pair)
    if emitter.position == opened_at:
        return False  # nothing taken: the turn did not match, whatever it performed while standing still
    if k():
        return True
    emitter.stack += (("consume", opened_at, opened),)  # backtracked into the turn: it stands open again
    return False


def _matched_open_settled(node, emitter, grammar, k):
    """
    A settled region opens.

    Its ways stand between here and its close as live choices, and its close is what says a failure past it unwinds to
    here: no choice on the way takes another way, and the region is given back whole here, ordinary backtracking
    carrying on from in front of it.
    """
    checkpoint = emitter.checkpoint()
    emitter.stack += (("backtrack", None, node.pair),)
    if k():
        return True
    if emitter.unwinding is not None and emitter.unwinding & node.pair:
        emitter.unwinding = None  # the open the close was unwinding to, so the region is given back and that is all
    emitter.give_back(checkpoint)  # a cut unwinding through the region carries on past it, giving nothing back
    return False


def _matched_close_settled(node, emitter, grammar, k):
    """
    The settled region closes: what follows it failing is the whole region failing, so nothing inside is taken another
    way. An unwind already under way is left as it stands — it is headed for an open further out than this one's.
    """
    _held, _opened, emitter.stack = _popped(emitter, "backtrack", "a settled region", node.pair)
    if k():
        return True
    if emitter.unwinding is None:
        emitter.unwinding = node.pair
    return False


def _matched_forbidding(node, emitter, grammar, k):
    """What may not stand from here on, as a slot rather than a stack: a write names it, so nothing is taken back."""
    checkpoint = emitter.checkpoint()
    emitter.forbidden = () if node.item is None else (node.item,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_write(node, emitter, grammar, k):
    """A parameter given the value the expression works out to."""
    checkpoint = emitter.checkpoint()
    value = evaluate(node.value, emitter, grammar)
    emitter.env[node.param] = value
    if node.param in emitter.globals:  # the stack beside the slot, which a nested write puts its own value on
        emitter.shadow[node.param] = emitter.shadow.get(node.param, ()) + (value,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_clear(node, emitter, grammar, k):
    """A parameter put back to the state a fresh parse gives it, which reading is a fault."""
    checkpoint = emitter.checkpoint()
    if node.param in emitter.globals:
        # A clear says "from here nothing holds a value", and that is idempotent: saying it of a value already clear is
        # not the error that popping an empty stack would be, and a value routinely has more than one reader to say it.
        # So this takes one off where there is one and does nothing where there is not.
        #
        # What that gives up is a structural refusal: an unbalanced clear would have been caught here and is not. What
        # stands in its place is the corpus — the fixtures reproduced token for token and the suite folded to its
        # events, at every stage. A clear misplaced far enough to matter takes a value from a read that wanted it, and
        # that is a divergence those nets do catch; this one refusal is what is being traded for the idempotence,
        # knowingly.
        emitter.shadow[node.param] = emitter.shadow.get(node.param, ())[:-1]
    emitter.env[node.param] = None
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_raise(node, emitter, grammar, k):
    """An indentation floor moved up to the column the parse stands at, where that is the higher of the two."""
    checkpoint = emitter.checkpoint()
    raised = max(emitter.env.get(node.param, 0), emitter.mark.column)
    emitter.env[node.param] = raised
    if node.param in emitter.globals:
        # Raising a floor is not establishing one: it moves what stands rather than putting something new on. Where
        # nothing stands it is the first, which is what reading it as zero already meant.
        held = emitter.shadow.get(node.param, ())
        emitter.shadow[node.param] = (held[:-1] + (raised,)) if held else (raised,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _matched_binding(node, emitter, grammar, k):
    """The condition, and the parameter given its value once the condition has matched."""

    def bound():  # noqa: N807 — a continuation, not a special method
        checkpoint = emitter.checkpoint()
        value = evaluate(node.value, emitter, grammar)
        emitter.env[node.param] = value
        if node.param in emitter.globals:  # a write is a write however it is spelled: the stack beside the slot
            emitter.shadow[node.param] = emitter.shadow.get(node.param, ()) + (value,)
        if k():
            return True
        emitter.give_back(checkpoint)  # undo the bound value so the condition can go on
        return False

    return match(node.cond, emitter, grammar, bound)


def _matched_window(node, emitter, grammar, k):
    """
    The item under a budget of characters, past which a consume is the overflow the window names.

    The window is exhausted where the match would consume past the edge: `consume` fails the window's cut, and the
    overflow unwinds to the recovery, keeping the tokens up to the edge — the error the caller emits cuts the open run
    into the last of them. Any exit clears the ceiling: a cut unwinding past here leaves it.
    """
    if node.item is None:
        return k()  # the vendored grammar's bare length note, which libyeast's own grammar never places
    if emitter.ceiling is not None:
        # nested: only the outermost applies, an inner one being inside the budget the outer already bounds
        return match(node.item, emitter, grammar, k)
    emitter.ceiling = emitter.position + evaluate(node.limit, emitter, grammar)
    emitter.ceiling_message = node.message  # a consume past the edge raises this, keeping the tokens up to it

    def past_window():
        emitter.ceiling = None  # the window bounds `item`, not the parse that carries on once it has matched
        emitter.ceiling_message = None
        if k():
            return True
        return False  # `item` will try its next way; its own rewind restores the ceiling the checkpoint kept

    try:
        return match(node.item, emitter, grammar, past_window)
    finally:
        emitter.ceiling = None
        emitter.ceiling_message = None


def _matched_peeked_literal(node, emitter, grammar, k):
    """
    The literal ahead and its follow test, as one bounded zero-width question.

    Spelled here as the lookahead it means: each literal character as itself, a `then` as end-of-input-or-the-class, a
    `barrier` as a negative look, which passes at the end of the input on its own.
    """
    pattern = tuple(ir.OneCharSet(cp=codepoint) for codepoint in node.text)
    if node.then is not None:
        pattern += (ir.AltTree(items=(ir.EndOfStreamGuard(), ir.LookGuard(node.then))),)
    if node.barrier is not None:
        pattern += (ir.NegLookGuard(node.barrier),)
    return k() if _probe(ir.SeqTree(items=pattern), emitter, grammar) else False


def _matched_exclusion(node, emitter, grammar, k):
    """What may not stand from here on, added to what already may not: in scope until the production returns."""
    saved_forbidden = emitter.forbidden
    emitter.forbidden = saved_forbidden + (node.item,)
    if k():
        return True
    emitter.forbidden = saved_forbidden
    return False


def _matched_look_behind(node, emitter, grammar, k):
    """Whether the item matches ending where the parse stands, tried from every position in front of it."""
    target = emitter.position
    for start in range(target - 1, -1, -1):
        checkpoint = emitter.checkpoint()
        emitter.probing += 1  # a look-behind reads speculatively, past any `(max)` edge; the rewind restores it
        emitter.position = start
        did_reach = match(node.item, emitter, grammar, lambda: emitter.position == target)
        if emitter.failing is not None:
            emitter.failing = None  # a cut behind here is speculative too, as one inside a lookahead is
            did_reach = False
        emitter.rewind(checkpoint)
        if did_reach:
            return k()
    return False


def _matched_token(node, emitter, grammar, k):
    """The item, the characters it takes carrying its code, cut from what stands on either side of it."""
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
        emitter.give_back(middle)  # reopen the run so the wrapped item can try its next way
        return False

    did_match = match(node.item, emitter, grammar, close_token)
    if not did_match:
        emitter.give_back(entry)  # undo the leading cut and the code change
    return did_match


def _matched_wrapped(node, emitter, grammar, k):
    """The item between its two markers."""
    entry = emitter.checkpoint()
    emitter.marker(node.begin)

    def close_wrap():
        middle = emitter.checkpoint()
        emitter.marker(node.end)
        if k():
            return True
        emitter.give_back(middle)
        return False

    did_match = match(node.item, emitter, grammar, close_wrap)
    if not did_match:
        emitter.give_back(entry)  # a failure on its way to a recovery leaves the `begin` for the recovery to close
    return did_match


def _matched_switch(node, emitter, grammar, k):
    """The branch whose value the parameter holds, and the `else` where no branch holds it."""
    value = emitter.env.get(node.var)  # a parameter never set is no branch's value, so it takes the `else`
    for branch in node.branches:
        if branch.value == value:
            return match(branch.item, emitter, grammar, k)
    if node.default is not None:
        return match(node.default, emitter, grammar, k)
    return False


# The step the machine takes for each kind. This is the machine itself rather than a question asked about the grammar,
# so it is a step to take rather than an `ir.Reading` to consult: a reading is called through its type where a function
# is called directly, which costs C stack, and a few thousand of those nested is all any stack holds — far short of what
# a parse of a real input reaches. Everything a way performs, bar the one scan whose answer a guard is waiting to read.
# A guard is not among them: a question asked between the scan and the guard about it leaves what the scan did standing,
# which is what lets a gate tell the two ways apart.
_TAKES_THE_ANSWER_AWAY = frozenset(ir.PERFORMED_NODES) - {ir.ConsumeLimitedSpanAction}

_MATCHED = {
    ir.OneCharSet: _matched_char,
    ir.CharSet: _matched_set,
    ir.RangeSet: _matched_range,
    ir.InvalidSet: _matched_invalid,
    ir.EmptyTree: lambda node, emitter, grammar, k: k(),
    ir.RefCall: _matched_call,
    ir.SeqTree: _matched_sequence,
    ir.AltTree: _matched_alternation,
    ir.DiffSet: _matched_difference,
    ir.OptTree: _matched_optional,
    ir.ChoiceState: _matched_choice,
    ir.AlternativeState: _matched_way,
    ir.ConsumeCharAction: _matched_gated_char,
    ir.ConsumePeekedAction: _matched_gated_literal,
    ir.ConsumeLiteralAction: _matched_literal,
    ir.ConsumeLimitedSpanAction: _matched_limited_span,
    ir.DidMatchFullSpanGuard: _matched_did_fill_span,
    ir.ConsumeSpanAction: _matched_span,
    # A trimmed run is spelled as the `TrimStarTree` it is, the canonical form's own name for the same match.
    ir.ConsumeTrimmedSpanAction: lambda node, emitter, grammar, k: match(
        ir.TrimStarTree(node.full, node.trim), emitter, grammar, k
    ),
    ir.TrimStarTree: _matched_trimmed_run,
    ir.StarTree: lambda node, emitter, grammar, k: _longest_run(node.item, 0, emitter, grammar, k),
    ir.PlusTree: lambda node, emitter, grammar, k: _longest_run(node.item, 1, emitter, grammar, k),
    ir.RepTree: _matched_counted_run,
    ir.CaseTree: _matched_switch,
    ir.SetForbiddenAction: _matched_forbidding,
    ir.SetVarAction: _matched_write,
    ir.ClearVarAction: _matched_clear,
    ir.IncreaseAction: _matched_raise,
    ir.BindTree: _matched_binding,
    ir.ColumnLtGuard: lambda node, emitter, grammar, k: (
        k() if evaluate(node.a, emitter, grammar) < evaluate(node.b, emitter, grammar) else False
    ),
    ir.ColumnLeGuard: lambda node, emitter, grammar, k: (
        k() if evaluate(node.a, emitter, grammar) <= evaluate(node.b, emitter, grammar) else False
    ),
    ir.MaxWrapper: _matched_window,
    ir.StartOfLineGuard: lambda node, emitter, grammar, k: k() if emitter.is_sol else False,
    ir.EndOfStreamGuard: lambda node, emitter, grammar, k: k() if emitter.position == len(emitter.chars) else False,
    ir.LookGuard: lambda node, emitter, grammar, k: k() if _probe(node.item, emitter, grammar) else False,
    ir.NegLookGuard: lambda node, emitter, grammar, k: k() if not _probe(node.item, emitter, grammar) else False,
    ir.LiteralPeekGuard: _matched_peeked_literal,
    ir.ExcludeAtAction: _matched_exclusion,
    ir.LookBehindGuard: _matched_look_behind,
    ir.TokenWrapper: _matched_token,
    ir.Wrapper: _matched_wrapped,
    ir.EmitAction: _matched_marker,
    ir.PushIndentAction: _matched_push_indent,
    ir.PopIndentAction: _matched_pop_indent,
    ir.PushCodeAction: _matched_push_code,
    ir.PopCodeAction: _matched_pop_code,
    ir.OpenWindowAction: _matched_open_window,
    ir.CloseWindowAction: _matched_close_window,
    ir.OpenProvisionalAction: _matched_open_provisional,
    ir.MarkProvisionalAction: _matched_mark_provisional,
    ir.RetypeProvisionalAction: _matched_retype_provisional,
    ir.InjectBeforeAction: _matched_inject,
    ir.CommitProvisionalAction: _matched_commit_provisional,
    ir.CutAction: _matched_cut,
    ir.PushMessageAction: _matched_open_committed,
    ir.PopMessageAction: _matched_close_committed,
    ir.CommitWrapper: _matched_committed,
    ir.ErrorAction: _matched_error,
    ir.PushRecoveryAction: _matched_open_recovery,
    ir.PopRecoveryAction: _matched_close_recovery,
    ir.RecoverWrapper: _matched_recovering,
    ir.StartMustConsumeAction: _matched_open_turn,
    ir.EndMustConsumeGuard: _matched_close_turn,
    ir.PushBackTrackAction: _matched_open_settled,
    ir.PopBackTrackAction: _matched_close_settled,
}


def _recover(entry, emitter, grammar):
    """
    A failed cut answered by the region `entry` opened: emit the error, close the markers down to where the region
    began, and match what answers for it. Then the resume, and then the way's ordinary continuation, the two together
    being what makes carrying on the same as what the region covered having matched.

    The scopes the abandoned parse was inside are already back, each frame having given its own back on its way out, so
    the region's own stand here. What it opened is still open, which is what there is to close.

    A recovery that does not match is this region declining to answer, so the parse is left as it was found and the cut
    goes on unwinding.
    """
    stopped = emitter.checkpoint()
    emitter.code, emitter.stack, emitter.forbidden = entry.code, entry.stack, entry.forbidden
    emitter.env, emitter.ceiling, emitter.ceiling_message = dict(entry.env), entry.ceiling, entry.ceiling_message
    code, emitter.failing = emitter.failing, None
    emitter.error(MESSAGES[code])
    while len(emitter.pending) > entry.pending:
        emitter.marker(emitter.pending[-1])  # close what the region covered opened, down to here and no further
    carried = emitter.returns[entry.returns - 1]
    if match(entry.recovery, emitter, grammar, lambda: match(entry.resume, emitter, grammar, carried)):
        return True
    emitter.rewind(stopped)
    emitter.failing = code
    return False


def run(grammar, production, data, parameters=None, deterministic=frozenset(), holding=frozenset(), coverage=None):
    """
    Run `production` on the UTF-8 `data`, returning the yeast tokens it emits — a rejection among them if it rejects.

    `deterministic` names the productions entered committed — the first alternative whose gate holds, no second try — so
    a grammar runs hybrid: committed where its decisions are proved, backtracking everywhere else. Empty backtracks all.

    `holding` names the invariants the caller says this grammar establishes. A run then asserts what they promise
    instead of taking the promise back off the grammar — where `every-consume-is-protected-by-a-gate` is named, a run of
    a class that finds none is a gate that lied rather than a match that declined, and it says so at once. Read off the
    shape it would only repeat the static count; told, it is the parse checking what the count claims.

    `parameters` binds the production's parameters from the fixture's filename — `n`/`m` are integers and the finite
    `c`/`t`/`r`/`i` strings, and `o` says which column the run begins at rather than naming a parameter of anything. A
    production that declares `r` and is run without one resumes the way a zeroed `ys_options` does.

    `coverage` is a `Coverage` for the run to record what it reached and what it saw refuse into, and nothing where the
    caller wants none.

    The production is entered as a reference to it, the way every other rule is entered, rather than by matching its
    body: a rule run at the top is still a rule, and what a run records about references must see it.
    """
    # A caller names the production polymorphically — the fixture's, the root's — and a monomorphized grammar holds only
    # its specialized copies; resolve to the copy, its finite parameters moved into the name. The resume policy is read
    # first, before that move takes it from the arguments, since recovery needs it whether it stays a parameter or not.
    parameters = dict(parameters or {})
    # Where the first character stands. A rule entered in the middle of a line — a compact collection just past its `-`
    # — is measured against the column it is at, which a run starting at zero cannot say. It is no parameter of any
    # production: it says where the run begins, the way the input itself does.
    column = int(parameters.pop("o", 0))
    resume = parameters.get("r", "n")
    production, parameters = ir.entry(grammar, production, parameters)
    emitter = Emitter(data)
    emitter.offset = column
    emitter.is_sol = column == 0  # column zero is the start of a line, and any other column is not
    emitter.deterministic = deterministic
    emitter.coverage = coverage
    emitter.holds_indent = any(
        isinstance(node, ir.PushIndentAction) for name in grammar for node in _nodes(grammar[name].body)
    )
    # Whether every run of a class is entered under a gate that found that class — which a grammar says by having no way
    # holding a `ConsumeSpanAction` without a `LookGuard` in its gate. Once a run of none or more has been said as the
    # two ways it is, what is left takes at least one, and a run that finds none is a gate that lied rather than a match
    # that declined. Before that step a span is a run of none or more and taking none is what it is for.
    emitter.holding = holding
    emitter.globals = tuple(
        name for name in ir.GLOBAL_PARAMS if not any(name in grammar[held].params for held in grammar)
    )
    emitter.env = {name: int(value) if name in ("n", "m") else value for name, value in parameters.items()}
    if "r" in grammar[production].params:
        emitter.env.setdefault("r", resume)  # a production run without a resume policy takes the zeroed one, no-resume
    if "n" in emitter.env:  # the indentation the run is entered under, which no alternative pushed and none pops
        emitter.stack = (("indent", emitter.env["n"], None),)
    entered = emitter.stack  # what the run itself put there, which is what a parse that closed what it opened ends at
    entry = ir.RefCall(production, tuple(ir.LitValue(emitter.env.get(name)) for name in grammar[production].params))

    # A cut says where the unwind lands and nothing else; what to do about the input from there is `l-recover`'s, which
    # under a resuming policy parses the rest of the stream — and that may commit and fail again. So recovery is a loop
    # rather than one handoff, and a second error inside a resumed document needs no mechanism of its own.
    node = entry
    failed_at = None
    while True:
        did_match = match(node, emitter, grammar, _accept)
        if emitter.failing is not None:
            code, emitter.failing = emitter.failing, None
            _fail(emitter, MESSAGES[code])  # committed and nothing answered for it: the error names what the cut wanted
        else:
            if did_match:
                emitter.cut()
                # A parse that has matched has closed what it opened: every pair the grammar writes balances, so the
                # stack is back to what the run itself put there. What a wrapper held in a Python frame went with the
                # frame, where these live on the emitter until something takes them off, so a close never reached leaves
                # its scope standing here whichever kind it is — and an abandoned parse's are cleared where it was
                # abandoned rather than reaching this at all.
                if emitter.stack != entered:
                    raise AssertionError(
                        "the parse ends holding "
                        + (", ".join(kind for kind, _value, _pair in emitter.stack) or "nothing")
                        + " where it was entered holding "
                        + (", ".join(kind for kind, _value, _pair in entered) or "nothing")
                    )
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
        emitter.stack = (("indent", recover_args["n"], None),)  # the stream's own level, the recovery entered under it
        entered = emitter.stack
        node = ir.RefCall(recover, tuple(ir.LitValue(recover_args[parameter]) for parameter in grammar[recover].params))
