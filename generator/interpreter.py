# SPDX-License-Identifier: MIT
"""
A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

The interpreter is slow and obviously correct. It matches a production against an input the way the grammar reads,
character by character with backtracking, and emits the yeast token stream. The match proves libyeast's grammar produces
the reference's tokens before any C can be wrong. The interpreter is also the net that catches a normalization step
going wrong. It takes the grammar as an argument, and the same interpreter and the same fixtures judge the base grammar
and the stages the pipeline hands on.

The interpreter runs a node the IR defines in the way the node calls for. A match matches, and a value expression
evaluates. A branch of a `(case)` and the gate of an alternative both read through the node holding them. A kind neither
table names raises rather than passing quietly. The run reports the fixture that reached such a kind as a crash.

The interpreter covers the grammar as written. The coverage is the character-level nodes and the repetitions. It is the
parameter machinery and the arithmetic over a parameter. It is the assertions and the lookarounds. The annotations that
give a token a code belong here too. The annotations that give it markers belong here as well.

The interpreter covers the canonical form too. That form holds the consumes a run of characters lowers to, the pairs a
scope comes down to, and the actions of the provisional run. `RecoverWrapper` says where a failed `(cut)` stops
unwinding. A node's meaning lives where the IR defines the node, and the interpreter matches those meanings node for
node.

Matching is success-continuation style. `match` calls a continuation per way a node matches, in greedy order, and the
continuation reports whether the rest of the parse succeeded. `match` therefore re-enters an alternation when a later
element fails, the way the reference backtracks.

Past a `(cut)` the parse does not backtrack. A failure past the cut becomes an error token naming what the rule
expected. The input from there comes back as unparsed, and a line splits into content and break.
"""

import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal, NamedTuple

import chars
import gate
import ir
import spaces
import wire
import yaml

# The continuation-passing matcher recurses once per grammar step and once per repetition. A match therefore nests far
# deeper than Python's default limit allows, even for the small conformance inputs. `check_normalize` runs the recursive
# helpers a transformed grammar holds. That caller raises the limit further and runs on a stack large enough for the
# higher limit.
sys.setrecursionlimit(20000)

# A production may nest this deep before the parse gives up. The limit guards against runaway recursion. A recursive
# helper that does not bottom out is such a case. Pathological nesting is another.
#
# The limit sits well above any legitimate depth. A run nearing the limit traces the production stack to stderr, and a
# reader can see what recurses. A run reaching the limit raises a clear error. The limit Python sets would fire a bare
# error instead.
_DEPTH_LIMIT = 6000
_DEPTH_TRACE = 40  # the productions the trace shows from the stack's deep end, and how near `_DEPTH_LIMIT` it starts.

# The interpreter reads the text of the messages a `(cut)` names from the grammar's companion table. The generated C
# table draws on the same companion table. The error text the interpreter emits is the error text the parser will emit.
with open(os.path.join(gate.TREE, "grammar", "messages.yaml"), encoding="utf-8") as _f:
    MESSAGES = yaml.safe_load(_f)

# The production that a failed cut hands off to. `ir.RECOVER` names it. It brings the input back unparsed. It continues
# at the next document where the resume policy says so.
#
# The interpreter matches that production rather than emitting its tokens by hand. Recovery stays in the grammar, where
# the C parser generates it from. A cut says where the unwind lands. It names no action for that place.
_RECOVER = ir.RECOVER


# A match calls this callback where the match succeeded. It answers whether the rest of the parse succeeded from there.
_Continuation = Callable[[], bool]

# An entry of the unified stack. An entry holds a kind and a value. An entry names the pairs a step opened it for. The
# run seeds an entry with no pair.
_Scope = tuple[str, object, frozenset[int] | None]

# An entry of the provisional undo journal. A retype names the token it replaced. An injection names where it went in.
_TrailEntry = tuple[Literal["retype"], int, wire.Token] | tuple[Literal["inject"], int]

# The result a value expression comes to. The grammar's values are an integer and a string, and the absence of a value.
_Value = int | str | None

# The callback a caller binds to a way and an action. A call hands it the node, where the parse was on either side of
# the node, and the parse itself.
_Checking = Callable[[ir.Node, Iterable[spaces.GuardAnswers], Iterable[spaces.GuardAnswers], "Emitter"], None]


class _Checkpoint(NamedTuple):
    """
    The whole state of the parse at a point. Restoring a checkpoint takes a match's work back.

    This tuple uses names rather than positions. The ways of taking a match back want different parts. An ordinary
    failure wants the state entire. A failure on its way to a recovery wants what the parse was inside of, and
    `give_back` holds that. `token_count` and `trail_length` are lengths rather than the lists. Cutting a list to that
    length takes back what the match added to the list.
    """

    position: int
    mark: wire.Mark
    token_count: int
    trail_length: int
    open_token: "_OpenToken | None"
    provisional: int | None
    provisional_mark: int | None
    code: str
    env: dict[str, _Value]
    shadow: dict[str, tuple[object, ...]]
    stack: tuple[_Scope, ...]
    is_sol: bool
    forbidden: tuple[ir.Node | None, ...]
    pending: tuple[str, ...]
    ceiling: int | None
    ceiling_message: str | None
    probing: int
    did_fill_span: bool | None
    consumed_length: int


class _OpenToken(NamedTuple):
    """
    The token the parse is building. It holds the wire character the code writes. It also holds the place in the input
    where the token began, in a pair of forms.

    Neither form yields the other. A reader of the wire reads `start`, the `wire.Mark` the finished token holds. The cut
    reads `start_position`, an index into `chars`, and measures the token's bytes from there.
    """

    character: str
    start: wire.Mark  # the mark the token holds.
    start_position: int  # the place it began in `chars`. `byte_at` turns that into the byte the text starts at.


class _Recovery(NamedTuple):
    """
    The record a `PushRecoveryAction` leaves behind. That record catches a cut that fails inside the region the action
    opens. The unwind puts the stack back before the recovery and the resume run.

    A `("recovery", entry, pairs)` on the stack of the parse holds such a record. The region then sits where the other
    scopes do, and its `PopRecoveryAction` takes the open on top. `stack` is that stack as it was before the entry went
    on. The region goes back together with what opened inside it.

    `returns` is how deep the return stack was where the region opened. Running the recovery and then the resume
    completes the way that pushed the region. The caller of that way continues past it. `returns` locates that caller,
    and no separate rule does.

    `is_closed` is the element `PopRecoveryAction` writes through, and says whether the region is still open. A cut past
    the close of a region goes to the region in force before that region. A cut inside a region a way reopened comes
    back here.

    The entry is a record rather than a read of the stack. An unwind gives the stack back on its way out. The place of
    the entry says nothing about where the region was before.
    """

    recovery: ir.Node | None
    resume: ir.Node | None
    returns: int
    pending: int
    code: str
    stack: tuple[_Scope, ...]
    forbidden: tuple[ir.Node | None, ...]
    env: dict[str, _Value]
    ceiling: int | None
    ceiling_message: str | None
    is_closed: list[bool]


class Coverage:
    """
    The productions a run reached, and the productions it saw refuse. The names index both.

    The parse itself fills the record, where it enters a production and hands a production back. A wrapper around the
    matcher would miss a recursion that reaches a production by another way.

    A run given such a record writes into it. A run given none records nothing.
    """

    def __init__(self) -> None:
        self.reached: set[str] = set()  # its body offered a solution, or a value expression evaluated it
        self.rejected: set[str] = set()  # it failed to match, or a gate guarding it refused


class _DepthExceeded(Exception):
    """
    The error a production nesting past `_DEPTH_LIMIT` raises. Such nesting is runaway recursion. The trace appears
    before the refusal.
    """


# The kinds of node a consume may name that name their own characters. `chars` reads such a node without consulting the
# grammar, and the answer belongs to the node.
#
# Anything else takes its meaning from a grammar. A reference and a difference are such kinds. The question below
# refuses such a kind.
_HOLDS_ITS_OWN_CHARACTERS = (ir.OneCharSet, ir.CharSet, ir.RangeSet)

# the store `ir.kept_for` keeps. It holds a subject beside the answer. The question below is whether a node names the
# mark and no more.
_ONLY_THE_MARK: dict[tuple[int, object], tuple[object, bool]] = {}


def _is_only_the_mark(node: ir.Node, grammar: Mapping[str, ir.Prod]) -> bool:
    """
    Whether what a consume names holds the byte-order mark and no more.

    At the head of a stream the mark takes no column and leaves a line start in place. Inside a scalar the mark is a
    character of the content like any other. `nb-single-char` and its family hold the mark in the upper range. There the
    mark takes a column as a content character does. The consume's written set tells the pair apart, and the question
    goes to the set rather than to the character.

    The question goes to the node a consume takes. `chars.denote` answers the question for a literal and a set. It
    covers a range and the character a gate found as well.

    The store keeps the answer against the node's identity, and holds the node with it. The id then stays taken, and a
    later object cannot reuse it. The key holds no grammar. A checker that resolves references through a grammar would
    need that grammar. The kinds that reach here name their own characters, and the answer cannot differ between
    grammars. The list above refuses a kind that names no characters of its own. Admitting such a kind would cache a
    wrong answer. This sits on the path a matched character takes, and a key on that path can afford no more than an
    `id`.
    """

    def is_only_the_mark() -> bool:
        if not isinstance(node, _HOLDS_ITS_OWN_CHARACTERS):
            raise ValueError(f"a consume names {type(node).__name__}. that kind names no characters of its own")
        # A node taking no single character denotes nothing. The answer is no, rather than a set to work spans out of.
        # What does not name the mark alone does not name it, however many characters it takes.
        denotation = chars.denote(grammar, node)
        spans = () if denotation is None else tuple(tuple(span) for span in chars.spans(denotation))
        return spans == ((wire.BYTE_ORDER_MARK, wire.BYTE_ORDER_MARK),)

    return ir.kept_for(_ONLY_THE_MARK, node, is_only_the_mark)


# The reads the runs have made of a global that a single value for the parse could not have answered. In such a read,
# the global's stack holds something other than the global's slot.
#
# The stack answers correctly either way. This refuses nothing. A reader judges a grammar by `ASKED` falling, rather
# than by whether the corpus survives it. The slot equals the stack while `ASKED` holds no read.
#
# A grammar whose productions still declare the parameters holds no globals and adds nothing here. This counts the tail
# of the pipeline where the globals appear.
ASKED = {"flattened": 0}


def _decode_one(raw: bytes, offset: int) -> tuple[int | None, int]:
    """
    The codepoint of the UTF-8 sequence at `offset` and its byte length, or `(None, 1)` where the byte begins none.

    The decoder follows RFC 3629 and matches the C decoder. An invalid byte is a unit of a single byte. A run of invalid
    bytes resyncs at the next valid lead and swallows nothing behind that lead.
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
    except (UnicodeDecodeError, TypeError):  # not-a-failure: no character here
        return None, 1  # a bad continuation or an overlong encoding. or a surrogate or a codepoint past U+10FFFF


def _decode(raw: bytes) -> tuple[list[int | None], list[int]]:
    """
    `raw` as parallel lists. `chars` holds a codepoint per character and `None` per invalid byte. `byte_at` holds the
    byte offset per unit, with a final entry at the end of the input. Unit `i` therefore spans `raw[byte_at[i]:byte_at[i
    + 1]]`.
    """
    codepoints: list[int | None] = []
    byte_at: list[int] = []
    offset = 0
    while offset < len(raw):
        codepoint, length = _decode_one(raw, offset)
        codepoints.append(codepoint)
        byte_at.append(offset)
        offset += length
    byte_at.append(len(raw))
    return codepoints, byte_at


class Emitter:
    """
    The token stream a run builds, and the input the run reads to build it.

    Characters consumed accumulate into a token with the current code. The run emits that token wherever a cut falls, at
    a token annotation's edge or at a marker.

    A checkpoint captures the state a rewind restores. That state holds what the match read and the tokens the match
    emitted. That state also holds what the run was inside of. A checkpoint captures nothing about where a failure is
    going. `failing` and `unwinding` are therefore no part of a checkpoint.

    `shadow` is a stack per global. A `(set)` puts a value on and a `(clear)` takes it off. A read takes the top, and
    that is right however the writes nest. The parse acts on the answer the stack gives. `ASKED` tallies the difference
    between the stack and the slot beside it. The tally is a single number over the whole corpus rather than a property
    of any parse, and a step is judged by it.
    """

    def __init__(self, raw: bytes) -> None:
        self.raw = raw  # the input bytes. a token that took input holds a slice of them, not a re-encoding
        self.chars, self.byte_at = _decode(raw)  # a unit per character or invalid byte. position indexes them
        self.position = 0
        self.mark = wire.Mark(0, 0, 1, 0)
        self.tokens: list[wire.Token] = []
        self.open_token: _OpenToken | None = None  # the token being built, or None
        self.provisional: int | None = None  # where the open run begins in `tokens`, or None. a run is open at a time
        self.provisional_mark: int | None = None  # where the run's mark cuts `tokens`, or None. re-taken, the last wins

        # The undo journal of the token mutations that are not appends, a retyped code and an injected marker. A rewind
        # pops it to undo what truncating the token count cannot.
        self.trail: list[_TrailEntry] = []
        self.code = "unparsed-text"  # the token code the next character gets. a `(token)` sets it

        # What the parse gives back on its way out, innermost last, each entry a `(kind, value, the pairs it was opened
        # for)`. A single stack serves the parse, so a pair a factoring split across a call closes off what it opened.
        self.stack: tuple[_Scope, ...] = ()
        self.env: dict[str, _Value] = {}  # the current production's parameters (n/m/c/t/r) and their values
        self.is_sol = True  # at the start of a line. true at the start of the input, and after a break
        self.forbidden: tuple[ir.Node | None, ...] = ()  # what may not match at a start of line. the `(exclude)` guards
        self.pending: tuple[str, ...] = ()  # the `end` markers of the `(wrap)`s the parse is inside, outermost first
        self.ceiling: int | None = None  # where a `(max)` window ends, past which committed input may not be consumed
        self.ceiling_message: str | None = None  # the cut message a consume past the ceiling raises, held with the
        # window. An open displaces the window it finds, and its close puts that window back
        self.probing = 0  # how many lookaheads are in progress. A probe may read past the ceiling, and a commit may not

        # Whether the `ConsumeLimitedSpanAction` just performed took its whole limit. Any other action takes the answer
        # away, and asking with none there is a fault rather than a `False`.
        self.did_fill_span: bool | None = None
        self.entered: list[str] = []  # the productions entered, outermost first. The depth guard's trace of the nesting
        self.returns: list[_Continuation] = []  # where each entered production continues when it matches, outermost
        # first. An unwind continues there rather than where the Python call stack stands. Pushed with `entered`
        self.failing: str | None = None  # the message of the `(cut)` a failure is unwinding to a recovery for. It says
        # the unwind gives back the scopes alone, so the recovery continues over what was read and what was emitted

        # The pairs of the `PopBackTrackAction` a failure is unwinding to, and None where the failure is an ordinary
        # one. While this is set no choice takes another way, and the open gives the region back whole and clears it.
        self.unwinding: frozenset[int] | None = None

        self.coverage: Coverage | None = None  # a `Coverage` to fill where the caller wants one. What each production
        # was seen to do is recorded where it happens
        self.consumed_length = 0  # how many characters the last consume took, which `(len): (match)` reads. A consume
        # writes it, a consume that took none leaves `0`, and no other action touches it
        self.checking: _Checking | None = None  # what the caller holds each way and action to. It is handed the way,
        # where the parse stood either side of it, and the parse itself
        self.holds_indent = False  # whether the grammar says where the indentation changes, which `run` reads off it.
        # A grammar with the pushes can require the parameter and the stack to agree
        self.passing_arguments = False  # while a call's arguments are read. The argument reads `n` under the push its
        # own value made, which is the one place the parameter and the stack disagree
        self.globals: tuple[str, ...] = ()  # the `ir.GLOBAL_PARAMS` no production of this grammar declares, which `run`
        # reads off it. There is a single value for the parse, and a call takes what the callee left back out
        self.shadow: dict[str, tuple[object, ...]] = {}  # a stack per global

    def checkpoint(self) -> _Checkpoint:
        """Capture where the parse is in a `Checkpoint`. `give_back` takes the parse back to that checkpoint."""
        return _Checkpoint(
            self.position,
            self.mark,
            len(self.tokens),
            len(self.trail),
            self.open_token,
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
            self.consumed_length,
        )

    def rewind(self, held: _Checkpoint) -> None:
        """
        Take the parse back to `held` whole. That is the input position, the emission, and what the parse was inside of.

        `give_back` rewinds where nothing is unwinding. A probe rewinds the same way. A declining recovery rewinds once
        it has cleared what was there.
        """
        # Reaching it with a cut still unwinding would hand back the input the recovery continues from. That is the one
        # thing that unwind may not do. It is a wrong parse rather than a failure, and it is refused here.
        assert self.failing is None, "the parse gives the input back under a cut still unwinding"
        self.position = held.position
        self.mark = held.mark
        self.open_token = held.open_token
        self.provisional = held.provisional
        self.provisional_mark = held.provisional_mark
        self.is_sol = held.is_sol
        self.pending = held.pending
        self.probing = held.probing
        self.did_fill_span = held.did_fill_span
        self.consumed_length = held.consumed_length
        self._restore_scopes(held)
        # The journal is undone before the token list is cut back. Popping newest first restores each index the entries
        # were recorded at.
        while len(self.trail) > held.trail_length:
            entry = self.trail.pop()
            if entry[0] == "retype":
                self.tokens[entry[1]] = entry[2]
            else:  # an "inject" is a marker inserted mid-list, deleted to undo what a truncation would miss
                del self.tokens[entry[1]]
        del self.tokens[held.token_count :]

    def _restore_scopes(self, held: _Checkpoint) -> None:
        """
        Put back the scopes the parse was inside of at `held`. It says nothing of the parse's reads and emissions.
        """
        self.code = held.code
        self.stack = held.stack
        self.forbidden = held.forbidden
        self.ceiling = held.ceiling
        self.ceiling_message = held.ceiling_message
        # The parameters are copied out rather than adopted. An alternation rewinds to the same checkpoint once per
        # branch, and a branch's `(set)` must not reach back into what the branch after it rewinds to.
        self.env = dict(held.env)
        self.shadow = dict(held.shadow)  # copied out the same way. a `(set)` must not reach back through it

    def is_unwinding(self) -> bool:
        """Whether a failure is on its way to a settled region's open, or to the handler catching a cut."""
        return self.failing is not None or self.unwinding is not None

    def give_back(self, held: _Checkpoint) -> None:
        """
        Take back to `held` what the failure passing through gives back.

        An ordinary failure gives back the state entire. A settled region the parse hands back gives it back entire too.

        A failure on its way to a recovery gives back the scopes and no more. The input position and the emission both
        stay. The recovery continues from where the input got to, over the tokens already there. The position, the marks
        and the tokens stay as the failure left them.

        A frame taking back its own scopes leaves the recovery's scopes where the unwind stops. A snapshot has nothing
        to say about them.
        """
        if self.failing is None:
            self.rewind(held)
        else:
            self._restore_scopes(held)

    def try_consume(self, is_only_the_mark: bool = False) -> bool:
        """
        Take the character or invalid byte at the position into the open token, and say whether the take happened. A
        token opens under the current code where none is open. An invalid byte is no break and no byte-order mark. It
        advances a byte and a column like any other.

        `is_only_the_mark` says the consume names the byte-order mark and no more. That is the take which neither ends a
        line start nor moves the column. A set holding the rest of the content too names a mark inside a scalar. There
        it is a character like any other.

        The character it declines is a committed character past a `(max)` window's edge. Such a character exhausts the
        window. The cut the window names sits behind that failure, and `failing` says so. A lookahead is exempt and
        reads on freely. The open token stays as it was, and the tokens up to here still emit.
        """
        if self.ceiling is not None and not self.probing and self.position >= self.ceiling:
            self.failing = self.ceiling_message
            return False
        if self.open_token is None:
            self.open_token = _OpenToken(wire.CODE_CHAR[self.code], self.mark, self.position)
        codepoint = self.chars[self.position]
        byte_length = self.byte_at[self.position + 1] - self.byte_at[self.position]
        is_break = codepoint in (wire.LINE_FEED, wire.CARRIAGE_RETURN)
        if is_break:
            # A break puts the parse at a line start and the column at `0`. A carriage return a line feed follows is
            # half of a break, and the feed counts the line.
            counts_a_line = not (codepoint == wire.CARRIAGE_RETURN and self._is_before_line_feed())
            self.mark = wire.Mark(self.mark.byte + byte_length, self.mark.char + 1, self.mark.line + counts_a_line, 0)
            self.is_sol = True
        else:
            # A byte-order mark the grammar asks for is no character of the line. It neither ends the start of the line
            # nor takes a column. What follows it stands where it would have stood without it.
            self.mark = wire.Mark(
                self.mark.byte + byte_length,
                self.mark.char + 1,
                self.mark.line,
                self.mark.column + (not is_only_the_mark),
            )
            if not is_only_the_mark:
                self.is_sol = False
        self.position += 1
        return True

    def _is_before_line_feed(self) -> bool:
        """Whether a CR at the position is immediately followed by an LF. The pair are then a single break."""
        return self.position + 1 < len(self.chars) and self.chars[self.position + 1] == wire.LINE_FEED

    def cut(self) -> None:
        """
        End the open token, and emit that token where it took anything. The text is the raw input bytes the token spans,
        and the wire escapes them. Such a byte may be a character or an unparsed-invalid byte.
        """
        if self.open_token is not None:
            character, start, start_position = self.open_token
            raw = self.raw[self.byte_at[start_position] : self.byte_at[self.position]]
            if raw:
                self.tokens.append(wire.Token(character, start, wire.escape(raw, character)))
            self.open_token = None

    def marker(self, code: str) -> None:
        """
        Emit a zero-width marker of `code`. The call cuts the open token first. The call then records the marker as
        open.

        The emitter pairs a marker by its code rather than by the node that emitted the marker. `check_markers` pairs
        markers the same way.

        A `(wrap)` opens markers. A block scalar opens its own with an `(emit)`. The chomping decides where the block
        scalar's `end-scalar` falls. That marker may fall ahead of the breaks the scalar holds. A `(wrap)` cannot say
        that. Pairing by node would hide such a marker from a parse that closes what it opened.
        """
        self.cut()
        self.tokens.append(wire.Token(wire.CODE_CHAR[code], self.mark, ""))
        if code.startswith("begin-"):
            self.pending += ("end-" + code[len("begin-") :],)
        elif self.pending and self.pending[-1] == code:
            self.pending = self.pending[:-1]

    def error(self, message: str) -> None:
        """
        Emit an error token at the position. The token holds `message` as its text and spans no input. This cuts the
        open token first.
        """
        self.cut()
        self.tokens.append(wire.Token(wire.ERROR, self.mark, wire.escape(message.encode("utf-8"))))

    def open_provisional(self) -> None:
        """
        Open the provisional run. A commit resolves the tokens the run emits from here on. Those tokens stay undecided
        until the commit. This cuts the open character run first. The input the parse consumed before this point stays
        decided.
        """
        assert self.provisional is None, "a provisional run opened inside a provisional run"
        self.cut()
        self.provisional = len(self.tokens)

    def mark_provisional(self) -> None:
        """
        Mark the open run's current position. The mark cuts the held tokens into the region before the mark and the
        region from the mark on. A later retype or injection names the region from the mark on. The call cuts the open
        token first, and the mark falls on a token boundary.

        A call in a marked run moves the mark, and the last call wins. A checkpoint keeps the mark it replaces, and a
        rewind puts that mark back.
        """
        assert self.provisional is not None, "a mark outside a provisional run"
        self.cut()
        self.provisional_mark = len(self.tokens)

    def _region(self, region: str) -> tuple[int, int]:
        """
        Answer the half-open range of token indexes that `region` names within the open run. `region` is `all`, or a
        side of the mark.
        """
        assert self.provisional is not None, f"a {region} region outside a provisional run"
        if region == "all":
            return self.provisional, len(self.tokens)
        assert self.provisional_mark is not None, f"a {region} retype with no mark taken"
        if region == "before_mark":
            return self.provisional, self.provisional_mark
        return self.provisional_mark, len(self.tokens)

    def retype_provisional(self, rest: str | None, breaks: str | None, region: str) -> None:
        """
        Rewrite the held tokens in `region` by kind. A token that took a line break takes `breaks`. A token that took
        anything else takes `rest`. A code of `None` leaves the kind unchanged. A marker and an error take no characters
        and keep their code either way. The call cuts the open token first, and the rewrite then sees that token.
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
            self.tokens[index] = wire.Token(wire.CODE_CHAR[code], token.start, token.text)

    def inject_before(self, codes: Sequence[str], at: str) -> None:
        """
        Put the decided zero-width markers `codes`, in order, into the open run at `at`. `at` names `start`, ahead of
        the whole run. `at` otherwise names `mark`, between the tokens either side of it. The call cuts the open token
        first.

        The run start moves past a start injection. A mark injection sits behind the pre-mark tokens.
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
            self.tokens.insert(index + offset, wire.Token(wire.CODE_CHAR[code], start, ""))
        inserted = len(codes)
        # An index at or past the insertion point moves right by what was inserted. That is the run start where the
        # injection is its own, and the mark where it stands at or past the injection.
        if self.provisional >= index:
            self.provisional += inserted
        if self.provisional_mark is not None and self.provisional_mark >= index:
            self.provisional_mark += inserted

    def commit_provisional(self) -> None:
        """
        Resolve the open provisional run and its mark. The resolution settles the run's tokens. The resolution settles
        the tokens the parse emits after that run.
        """
        assert self.provisional is not None, "a commit with no provisional run open"
        self.provisional = None
        self.provisional_mark = None


def _as_number(expression: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> int:
    """The value `expression` comes to. That value has to be a number."""
    value = _evaluate(expression, emitter, grammar)
    assert isinstance(value, int), f"a read wants a number, and the expression comes to {value!r}"
    return value


def _as_text(expression: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> str:
    """The value `expression` comes to. That value has to be text. `(atoi)` and `(len)` read text."""
    value = _evaluate(expression, emitter, grammar)
    assert isinstance(value, str), f"a read wants text, and the expression comes to {value!r}"
    return value


def _evaluate(expression: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    Evaluate a value expression. A parameter, a literal and the matched text are value expressions. So are the
    arithmetic and the dispatch over those.

    `AtoiValue` reads `Match()`. That is the open token's text. `LenValue` of `Match()` reads the length the last
    consume took. The consume leaves that length in a slot of its own. `LenValue` of anything else measures what the
    expression under it evaluates to. Those meanings of `Match()` differ wherever a cut falls across a consume, and
    wherever a consume takes more than a token.
    """
    return _EVALUATE(expression, emitter, grammar)


def _evaluated_parameter(node: ir.ParamValue, emitter: Emitter, _grammar: Mapping[str, ir.Prod]) -> _Value:
    """A parameter's value, as the environment holds it. This refuses a parameter the environment holds nothing for."""
    value = emitter.env.get(node.name)  # an out-parameter (m, t) may be passed on before it is set
    if node.name == "n" and emitter.holds_indent and not emitter.passing_arguments:
        # The indentation is on its way from a parameter to the stack. Both are kept, and a read compares them wherever
        # the grammar has been through `push-indents`.
        held = _indent_in_force(emitter)
        if held != value:
            raise AssertionError(f"the stack holds an indentation of {held!r} for a parameter of {value!r}")
    if value is None and not emitter.passing_arguments:
        # Nothing holds a value for it. Either no construct has measured a value yet, or a `ClearVarAction` has said the
        # construct that did has ended. Passing it on is not reading it. An out-parameter travels to its setter unset.
        raise AssertionError(f"a read of `{node.name}` finds nothing holding a value")
    return _a_value(value)


def _evaluated_global(node: ir.GlobalValue, emitter: Emitter, _grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    A global's value. That is the top of the global's stack.

    The stack answers, and that answer is right however the writes nest. The slot beside the stack holds what a single
    value for the parse would have held. A read where the stack and the slot differ is a read a global could not have
    answered. `ASKED` counts such a read rather than refusing it. The transformations drive that count to none, and at
    none the slot agrees with the stack.
    """
    held = emitter.shadow.get(node.name, ())
    if not held:
        raise AssertionError(f"a read of `{node.name}` finds nothing holding a value")
    if held[-1] != emitter.env.get(node.name):
        ASKED["flattened"] += 1
    return _a_value(held[-1])


# The intervals `ns-char` names, per grammar, as `ir.kept_for` keeps them.
_NS_CHAR: dict[tuple[int, object], tuple[object, tuple[tuple[int, int], ...] | None]] = {}


def _ns_char_spans(grammar: Mapping[str, ir.Prod]) -> tuple[tuple[int, int], ...] | None:
    """
    The intervals `ns-char` names, or `None` where the grammar does not hold it under that name.

    The cache keys on the grammar's identity and holds the grammar beside the entry. A later dictionary reusing that id
    then gets no answer meant for this grammar. `_ONLY_THE_MARK` keys the same way.
    """

    def worked_out() -> tuple[tuple[int, int], ...] | None:
        held = grammar.get("ns-char")
        denoted = None if held is None else chars.denote(grammar, held.body)
        return None if denoted is None else tuple(chars.spans(denoted))

    return ir.kept_for(_NS_CHAR, grammar, worked_out)


def _answers_now(emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> dict[str, object]:
    """
    The parse's reading of the bookkeeping bits of a `GuardAnswers`, and of the quantities its comparisons are over. An
    answer is `None` where the parse has none to give. The quantities are not axes. `GuardAnswers` compares the
    quantities and holds none of them. A comparison works out from the quantities, and the quantities come back beside
    the bits.

    An answer can be missing. The action in front of a guard says whether the run just performed filled its span, and
    any other action takes that answer away. A turn says whether the parse took a character since the turn opened, and
    outside a turn there is no answer. The indentation is a null where the grammar applies none. The floor holds no
    value until a push puts a value there. The character behind the parse is unanswerable where the grammar names no
    `ns-char`. A consumed length past the column falls outside what `spaces.ALL_GUARD_ANSWERS` enumerates.

    A `GuardAnswers` holds no unanswered axis. A missing answer leaves open the `GuardAnswers` the remaining answers
    allow.
    """
    behind = emitter.chars[emitter.position - 1] if emitter.position else None
    named = _ns_char_spans(grammar)
    is_after_ns_char = (
        None if named is None else behind is not None and any(low <= behind <= high for low, high in named)
    )
    opened_at = next((value for held, value, _pair in reversed(emitter.stack) if held == "consume"), None)
    held = _indent_or_none(emitter)
    floor = emitter.shadow.get("f", ())
    column = emitter.mark.column
    # The standings are enumerated with the consumed length at or below the column. A consume that crossed a line break
    # leaves a length past the column, which no standing holds.
    consumed_length = emitter.consumed_length if emitter.consumed_length <= column else None
    return {
        "is_at_line_start": emitter.is_sol,
        "is_after_ns_char": is_after_ns_char,
        "did_match_full_span": emitter.did_fill_span,
        "did_consume_since_open": None if opened_at is None else emitter.position != opened_at,
        "n": None if held is None else max(held, -1),
        "column": column,
        "consumed_length": consumed_length,
        "floor": floor[-1] if floor else None,
    }


def _guard_answers_now(emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> list[spaces.GuardAnswers]:
    """
    The `GuardAnswers` the parse can be in here. Axes that answer pin the parse to a `GuardAnswers`. An axis with no
    answer leaves more `GuardAnswers` open.

    This holds a space worked out from the grammar to a parse that really happened. A space admitting none of these
    `GuardAnswers` came out too narrow. Reading only the grammar catches no such space.
    """
    answers = _answers_now(emitter, grammar)
    asked = {axis: answers[axis] for axis in spaces.BOOKKEEPING_AXES}
    asked.update(_compared_now(answers))
    # Where every axis answers, a single point holds, and the code builds it rather than looking for it. This is asked
    # at every action and every way of a parse. A walk over the enumeration would cost as much as the whole check.
    if all(answer is not None for answer in asked.values()):
        now = spaces.GuardAnswers(*(bool(asked[axis]) for axis in spaces.AXES))
        return [now] if now in spaces.HELD else []
    return [
        one
        for one in spaces.ALL_GUARD_ANSWERS
        if all(answer is None or getattr(one, axis) == answer for axis, answer in asked.items())
    ]


def _a_value(value: object) -> _Value:
    """`value` as a value the grammar has."""
    assert value is None or isinstance(value, (int, str)), f"a read wants a grammar value, and a parse holds {value!r}"
    return value


def _a_quantity(value: object) -> int:
    """`value` as a quantity the parse has. The parse's quantities are integers."""
    assert isinstance(value, int), f"a read wants a quantity, and the parse answers {value!r}"
    return value


def _compared_now(answers: Mapping[str, object]) -> dict[str, bool | None]:
    """
    The comparisons a `GuardAnswers` makes, said as what the parse's values come to. A comparison is `None` where a
    quantity that axis reads has no value here. The axis then takes either value rather than answering.

    `spaces.comparisons` holds the comparisons themselves, and this does not write them again. A quantity with no value
    gets a placeholder. An axis that does not read that quantity ignores the placeholder. An axis that reads it answers
    `None`.
    """
    quantities = [0 if answers[name] is None else _a_quantity(answers[name]) for name in spaces.QUANTITIES]
    settled = spaces.comparisons(quantities)
    return {
        axis: None if any(answers[name] is None for name in reads) else settled[at]
        for at, (axis, reads) in enumerate(zip(spaces.COMPARISON_AXES, spaces.COMPARISON_READS))
    }


def _evaluated_length(node: ir.LenValue, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    The length of the match. For `(match)` that is the characters the last consume took.

    This reads a slot the consume leaves rather than the token the parser is building. An annotation opening or closing
    cuts a token, and the grammar asks nothing about those cuts.
    """
    if not isinstance(node.arg, ir.MatchValue):
        return len(_as_text(node.arg, emitter, grammar))
    return emitter.consumed_length


def _evaluated_match(_node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    The open token's text.

    The rule reading it wants the digits of a block scalar's indentation indicator, and the token there is exactly
    those. So this reads the token rather than a slot. The consumed length has a slot of its own, and no token holds
    that length.
    """
    start = emitter.position if emitter.open_token is None else emitter.open_token.start_position
    taken = emitter.chars[start : emitter.position]
    return "".join(chr(codepoint) for codepoint in taken if codepoint is not None)


def _evaluated_switch(node: ir.FlipValue, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    A value function answers with the branch the parameter's value names. This refuses a value no branch names.
    """
    value = emitter.env[node.var]
    for branch in node.branches:
        if branch.value == value:
            return _evaluate(branch.item, emitter, grammar)
    raise KeyError(f"Flip on {node.var}={value!r} has no branch")


def _evaluated_call(node: ir.RefCall, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> _Value:
    """
    A call's value is the callee's body under the arguments this call hands it. The call then puts the caller's
    arguments back.
    """
    production = grammar[node.name]
    arguments = tuple(_evaluate(argument, emitter, grammar) for argument in node.args)
    saved = emitter.env
    emitter.env = {**saved, **dict(zip(production.params, arguments))}
    result = _evaluate(production.body, emitter, grammar)
    emitter.env = saved
    if emitter.coverage is not None:
        emitter.coverage.reached.add(node.name)
    return result


# `AutoDetectIndentValue` is absent and raises. The official grammar writes it. libyeast's grammar reads `ColumnValue`
# in its place. A value invented for it here would be a lookahead the parser cannot make.
_EVALUATE: ir.Question[_Value] = ir.Question(
    "the value an expression works out. an indentation or a length is an integer. a finite parameter is its string. "
    "a parameter holding nothing comes to `None`.",
    {
        ir.LitValue: lambda node, emitter, grammar: node.value,
        ir.ParamValue: _evaluated_parameter,
        ir.GlobalValue: _evaluated_global,
        ir.IndentValue: lambda node, emitter, grammar: _indent(emitter),
        ir.MatchValue: _evaluated_match,
        ir.ColumnValue: lambda node, emitter, grammar: emitter.mark.column,
        ir.AtoiValue: lambda node, emitter, grammar: int(_as_text(node.arg, emitter, grammar)),
        ir.LenValue: _evaluated_length,
        ir.AddValue: lambda node, emitter, grammar: _as_number(node.a, emitter, grammar)
        + _as_number(node.b, emitter, grammar),
        ir.SubValue: lambda node, emitter, grammar: _as_number(node.a, emitter, grammar)
        - _as_number(node.b, emitter, grammar),
        ir.FlipValue: _evaluated_switch,
        ir.RefCall: _evaluated_call,
    },
)


def _try_accept() -> bool:
    """The outermost continuation takes the first whole match as the answer."""
    return True


def _popped(
    emitter: Emitter, kind: str, what: str, pair: frozenset[int]
) -> tuple[object, frozenset[int] | None, tuple[_Scope, ...]]:
    """
    `(the value the stack's top entry holds, the pairs it was opened for, the stack without it)`. The call refuses a top
    that is not of `kind`. The call also refuses a top that no pair of `pair` opened.

    A pop takes what its own push put there. A top that is something else means the pop and the push are not the pair
    they read as. An action moved across an action it must not cross leaves such a top. A push whose pop did not run
    leaves such a top too.

    The kind of a top cannot say which pair opened it. A pair of `(token)`s share a kind and belong to different pairs.
    A half therefore names the pairs it belongs to, and a close answers an open where the halves share a pair. They
    share rather than match. A merge leaves a half naming the pairs it replaced.

    A top opened for no pair is the entry the run itself put there. Neither half of the grammar wrote that entry. This
    holds that entry to no pair.
    """
    if not emitter.stack:
        raise AssertionError(f"a pop of {what} finds an empty stack")
    held, value, opened = emitter.stack[-1]
    if held != kind:
        raise AssertionError(f"a pop of {what} finds {held} on the stack")
    if opened is not None and not opened & pair:
        raise AssertionError(f"a pop of {what} finds one of another pair on the stack")
    return value, opened, emitter.stack[:-1]


def _nodes(node: ir.Node) -> Iterable[ir.Node]:
    """`node` and the IR nodes nested within it."""
    yield node
    children: list[ir.Node] = []

    def seen(child: ir.Node) -> ir.Node:
        children.append(child)
        return child

    ir.rebuilt(node, seen)
    for child in children:
        yield from _nodes(child)


# The grammar facts `ir.kept_for` keeps. Whether a grammar pushes indentations, and the grammar's globals.
_WHAT_A_GRAMMAR_HOLDS: dict[tuple[int, object], tuple[object, tuple[bool, tuple[str, ...]]]] = {}


def _what_a_grammar_holds(grammar: Mapping[str, ir.Prod]) -> tuple[bool, tuple[str, ...]]:
    """
    `(whether `grammar` says where the indentation changes, the globals the productions leave undeclared)`.

    Both are properties of the grammar rather than of a run. This works both out once per grammar, rather than once per
    fixture run against the grammar.

    The cache keys on the grammar's identity and holds the grammar beside the entry, as `_ns_char_spans` does. A later
    dictionary reusing that id then gets no answer meant for this grammar.
    """

    def worked_out() -> tuple[bool, tuple[str, ...]]:
        does_push = any(
            isinstance(node, ir.PushIndentAction) for name in grammar for node in _nodes(grammar[name].body)
        )
        held_globals = tuple(
            name for name in ir.GLOBAL_PARAMS if not any(name in grammar[held].params for held in grammar)
        )
        return does_push, held_globals

    return ir.kept_for(_WHAT_A_GRAMMAR_HOLDS, grammar, worked_out)


def _indent_in_force(emitter: Emitter) -> int | None:
    """The indentation the stack holds. This answers `None` where no push has put an indentation there."""
    for kind, value, _pair in reversed(emitter.stack):
        if kind == "indent":
            assert value is None or isinstance(value, int), f"the stack holds {value!r} as an indentation"
            return value
    return None


def _indent_or_none(emitter: Emitter) -> int | None:
    """
    The indentation in force, read from the mechanism the grammar uses. A grammar that pushes uses the stack. A grammar
    that does not push uses the parameter. The answer is `None` where neither holds an indentation.

    In a pushing grammar, `_evaluated_parameter` checks that the stack and the parameter agree at a read of `n`.
    """
    if emitter.holds_indent:
        return _indent_in_force(emitter)
    held = emitter.env.get("n")
    assert held is None or isinstance(held, int), f"the indentation parameter holds {held!r}"
    return held


def _indent(emitter: Emitter) -> int:
    """
    The indentation in force. This refuses a position where no indentation applies.

    The answer is not a null. The grammar pushes a null where no indentation applies. An implicit key reaches a flow
    node with `n` written `null`, and a rule there may not measure against the indentation. The space holding a decision
    has no value for a null either, and its axes compare integers. The checker cannot say such a state.

    The answer does not fall below the `-1` the root enters at. `l+block-sequence` enters at `seq-spaces(n, block-out)`.
    That comes to `n - 1`, and a sequence at the root sits at `-2`. A reader compares the indentation against a column
    or against a match length. A column is not negative and neither is a length. An indentation at or below `-1` is then
    less than either. This holds such an indentation to `-1`.
    """
    held = _indent_or_none(emitter)
    if held is None:
        raise AssertionError("the indentation is read where none applies: the parse is under a null indent")
    return max(held, -1)


def _try_probe(pattern: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> bool:
    """
    Whether `pattern` matches at the position. The probe rewinds the position, the tokens and the scopes to the
    checkpoint it took. A lookahead keeps no effect.

    A `(cut)` inside a lookahead is speculative rather than a commit of the whole parse. A failure unwinding to such a
    cut stops here, and this reads it as "did not match" rather than continuing out.
    """
    checkpoint = emitter.checkpoint()
    emitter.probing += 1  # a lookahead reads past a `(max)` window's edge freely. the rewind restores the count
    did_match = _try_match(pattern, emitter, grammar, _try_accept)
    if emitter.failing is not None:
        emitter.failing = None
        did_match = False
    emitter.rewind(checkpoint)
    return did_match


def _does_gate_hold(asked: ir.GatePart, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> bool:
    """Whether the guards of `asked` hold at the position."""
    return all(_try_probe(guard, emitter, grammar) for guard in asked.guards)


def _try_repeat(item: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Match `item` greedily none or more times, then the continuation. The run is possessive. It takes the maximal run and
    does not fall back to fewer.

    A character-class run has a single outcome, and a shorter run is not what a valid parse needs. `lower-runs` writes
    that possessiveness into the grammar as a settled region around the turns.
    """
    checkpoint = emitter.checkpoint()
    while True:
        before = emitter.position
        step = emitter.checkpoint()
        if not _try_match(item, emitter, grammar, _try_accept):
            if emitter.is_unwinding():
                emitter.give_back(checkpoint)  # a turn that failed under an unwind fails the run rather than ending it
                return False
            emitter.rewind(step)
            break
        if emitter.position == before:
            break  # a zero-width match. it is kept once, repeating it having no end
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_longest_run(
    item: ir.Node, least: int, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    Match the longest run of `item`, then the continuation. It matches where the run took at least `least` turns.

    `least` separates the forms of a run. A run of none or more falls through to the continuation where nothing matched.
    A run that must take a turn refuses there.

    A run of none or more over a character class says what a consume says, and this hands such a run to `_try_repeat`.
    """
    if least == 0 and ir.is_one_char(item, grammar):
        return _try_repeat(item, emitter, grammar, k)
    checkpoint = emitter.checkpoint()
    before = emitter.position

    def try_after_first() -> bool:
        if emitter.position == before:
            return k()  # a zero-width turn. it is kept once, repeating it having no end
        return _try_repeat(item, emitter, grammar, k)

    if _try_match(item, emitter, grammar, try_after_first):
        return True
    if emitter.is_unwinding():
        return False  # taking no turn at all is another way. an unwinding failure does not take it
    emitter.rewind(checkpoint)
    return k() if least == 0 else False


def _is_forbidden_here(emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> bool:
    """
    Whether an in-scope `(exclude)` guard matches at a start of line here, where content must not begin.

    This is the reference's `forbidding`. A document forbids `c-forbidden` throughout. That is a `---` line or a `...`
    line, and a plain scalar cannot run past it. This asks at a start of line, where such a marker appears.
    """
    if not emitter.is_sol or not emitter.forbidden:
        return False
    patterns = emitter.forbidden
    emitter.forbidden = ()  # a forbidden pattern is matched without the guard. it would forbid its own characters
    try:
        return any(_try_probe(pattern, emitter, grammar) for pattern in patterns if pattern is not None)
    finally:
        emitter.forbidden = patterns


def _fail(emitter: Emitter, message: str) -> None:
    """
    Emit the error where the parse stopped, and close what it left open. The error states `message`, and is bare where
    `message` is empty.

    The emitter is already at the end of the run that cleanly matched. A failure travelling out gives back the scopes
    and no more. The `(wrap)`s the parse was inside are still open here, and this closes them. A `begin` marker then
    gets its `end` on any path. The fold that rebuilds the production tree can then work on an errored stream. A resumed
    parse makes the next document a sibling of the failed document rather than a child.

    The error is inside what failed, and it comes before the closing markers. Both are zero-width and fall at the same
    position. The order tells them apart.

    This clears the guards, the scopes and the `(max)` ceiling. The recovery gets the guards its own rules declare, and
    reads on past the edge the abandoned parse failed against. The grammar's `l-recover` decides the course from there.
    """
    emitter.code = "unparsed-text"  # from here on the input is unparsed
    emitter.stack = ()  # a scope left open goes, and with it whatever closing the scope would have restored
    emitter.forbidden = ()
    emitter.ceiling = None
    emitter.ceiling_message = None
    emitter.error(message)
    while emitter.pending:
        emitter.marker(emitter.pending[-1])


def _try_match(node: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Match `node` from the emitter's position. The interpreter takes the ways in greedy order and calls the continuation
    `k` once per way that matches.

    Backtracking is success-continuation style. On a match the interpreter calls `k`, and `k` returns whether the rest
    of the parse succeeded from there. A success commits the match and returns True. The emitter stays in that state. A
    failure rewinds the match and tries the next way. A rewind puts back the tokens and the position both. This returns
    False once the ways run out. The emitter then sits where the match began. A match commits where `k` accepts, and not
    before.

    The machine raises on a kind it has no step for.
    """
    step = _MATCHED.get(type(node))
    if step is None:
        raise NotImplementedError(f"interpreter does not support {type(node).__name__}")
    # The guard asking about a consume of a limited span reads the answer that consume left behind. Taken away here
    # rather than by each action in turn, where an action that forgot looks exactly like an action that remembered.
    if type(node) in _TAKES_THE_ANSWER_AWAY:
        emitter.did_fill_span = None
    # Both wrap the step rather than replacing it, and the measuring goes inside. A check reading the standing before
    # the length was written would read the length before it.
    if type(node) in ir.LEAVES_A_LENGTH:
        step = _with_measuring(step)
    if emitter.checking is not None and type(node) in ir.PERFORMED_STEPS:
        return _checked_against_what_was_predicted(node, emitter, grammar, k, step)
    return step(node, emitter, grammar, k)


def _try_no_char(_node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A run that took no character. The match is empty, and the consumed length becomes none. The node names no character
    set.
    """
    held = emitter.consumed_length
    emitter.consumed_length = 0
    if k():
        return True
    emitter.consumed_length = held
    return False


def _with_measuring(step: Callable[..., bool]) -> Callable[..., bool]:
    """
    Wrap `step` in a step that measures what `step` consumes. Another wrapper may then wrap the result.
    """
    return lambda node, emitter, grammar, k: _try_run_measured(node, emitter, grammar, k, step)


def _try_run_measured(
    node: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation, step: Callable[..., bool]
) -> bool:
    """Perform a consume. The number of characters it took goes into `consumed_length`."""
    began, held = emitter.position, emitter.consumed_length

    def try_matched() -> bool:
        emitter.consumed_length = emitter.position - began
        return k()

    if step(node, emitter, grammar, try_matched):
        return True
    emitter.consumed_length = held
    return False


def _checked_against_what_was_predicted(
    node: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation, step: Callable[..., bool]
) -> bool:
    """
    Perform `node`, and hold where the parse reaches to the caller's prediction.

    The check runs inside the continuation rather than after the step. A later step can fail. The interpreter then
    rewinds the step. After a rewind, the parse can sit somewhere the action did not put it. Inside the continuation,
    the parse sits where the action left it.
    """
    checking = emitter.checking
    assert checking is not None, "a prediction checks a step where the caller wants no check"
    before = _guard_answers_now(emitter, grammar)

    def try_checked() -> bool:
        checking(node, before, _guard_answers_now(emitter, grammar), emitter)
        return k()

    return step(node, emitter, grammar, try_checked)


# `_try_char`, `_try_set` and `_try_range` do the same thing after finding their character. `_try_invalid` does too. The
# matchers differ in how they find the character. `_try_invalid` also takes no byte-order mark. A matcher writes the
# shared part out again. A matcher recurses per grammar step through a continuation. A shared helper would keep a
# further frame across that continuation per character of the input. The helper would save lines. Its extra frame would
# cut the depth a parse can reach.


def _try_char(node: ir.OneCharSet, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A literal character, taken where the character here matches it."""
    if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] == node.cp:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.try_consume(_is_only_the_mark(node, grammar)) and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _try_set(node: ir.CharSet, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A character set, taken where the character here falls in a span of the set.

    The interval `(-1, -1)` is how a set says it holds an invalid byte. The decoder gives an invalid byte no codepoint,
    and `None` names such a byte here.
    """
    codepoint = emitter.chars[emitter.position] if emitter.position < len(emitter.chars) else None
    unit = -1 if codepoint is None and emitter.position < len(emitter.chars) else codepoint
    if unit is not None and any(low <= unit <= high for low, high in node.spans):
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.try_consume(_is_only_the_mark(node, grammar)) and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _try_range(node: ir.RangeSet, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A range, taken where the character here falls between its ends."""
    codepoint = emitter.chars[emitter.position] if emitter.position < len(emitter.chars) else None
    if codepoint is not None and node.lo <= codepoint <= node.hi:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.try_consume(_is_only_the_mark(node, grammar)) and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _try_invalid(_node: ir.Node, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A byte that begins no character.

    The byte belongs to no set. The recovery rules ask for such a byte, and turn a run of such bytes into a single
    unparsed-invalid token.
    """
    if emitter.position < len(emitter.chars) and emitter.chars[emitter.position] is None:
        if _is_forbidden_here(emitter, grammar):
            return False
        checkpoint = emitter.checkpoint()
        if emitter.try_consume() and k():
            return True
        emitter.give_back(checkpoint)
    return False


def _try_call(node: ir.RefCall, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Match the callee's body under the arguments this call hands it. The caller's environment comes back once the match
    is past.
    """
    production = grammar[node.name]
    # A parameter passed as itself (`m`, not `n+1`) is by reference. The callee may set it, which is how the block
    # header hands the detected indent and chomping back. Its final value propagates out to the caller.
    by_reference = [
        parameter
        for parameter, argument in zip(production.params, node.args)
        if isinstance(argument, ir.ParamValue) and argument.name == parameter
    ]
    emitter.passing_arguments = True
    arguments = tuple(_evaluate(argument, emitter, grammar) for argument in node.args)
    emitter.passing_arguments = False
    saved_env = emitter.env
    saved_forbidden = emitter.forbidden  # inherited by the callee, and any (exclude) it adds is scoped to it
    # A production inherits the ambient parameters and overrides the ones it declares. `n` stays in scope through a
    # callee that does not name it.
    emitter.env = {**saved_env, **dict(zip(production.params, arguments))}

    # A global is the parse's rather than any one call's. This takes a cleared value out where the by-reference pass
    # keeps the caller's.
    def try_continue_out() -> bool:
        callee_env = emitter.env
        callee_forbidden = emitter.forbidden
        caller_env = dict(saved_env)
        for parameter in by_reference:
            value = callee_env.get(parameter)
            if value is not None:
                caller_env[parameter] = value
        for name in emitter.globals:
            caller_env[name] = callee_env.get(name)
        emitter.env = caller_env  # the caller sees its own parameters again, with any by-reference result passed out
        emitter.forbidden = saved_forbidden
        if emitter.coverage is not None:
            emitter.coverage.reached.add(node.name)  # its body offered a solution. reaching it means exactly that
        if k():
            return True
        emitter.env = callee_env  # restore the callee's scope so its body can try its next way
        emitter.forbidden = callee_forbidden
        return False

    emitter.entered.append(node.name)
    emitter.returns.append(try_continue_out)  # where this call continues, which an unwind reads as its resume point
    if len(emitter.entered) >= _DEPTH_LIMIT:
        trace = " -> ".join(emitter.entered[-_DEPTH_TRACE:])
        emitter.entered.pop()
        emitter.returns.pop()
        raise _DepthExceeded(f"production nesting reached {_DEPTH_LIMIT}. the deepest is ...{trace}")
    if len(emitter.entered) > _DEPTH_LIMIT - _DEPTH_TRACE:
        print(f"    depth {len(emitter.entered)}: {node.name}", file=sys.stderr)
    try:
        did_match = _try_match(production.body, emitter, grammar, try_continue_out)
    finally:
        emitter.entered.pop()
        emitter.returns.pop()
    if not did_match:
        emitter.env = saved_env
        emitter.forbidden = saved_forbidden
        if emitter.coverage is not None:
            emitter.coverage.rejected.add(node.name)
    return did_match


def _try_sequence(node: ir.SeqTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """Match the items in turn. The items after an item are that item's continuation."""

    def try_step(index: int) -> bool:
        if index == len(node.items):
            return k()
        return _try_match(node.items[index], emitter, grammar, lambda: try_step(index + 1))

    return try_step(0)


def _try_alternation(node: ir.AltTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Try the items in the order held. An item that fails gives the position back before the next item runs. The first
    item that takes the parse on wins.
    """
    checkpoint = emitter.checkpoint()
    for item in node.items:
        if _try_match(item, emitter, grammar, k):
            return True
        if emitter.is_unwinding():
            return False  # a settled region is being given back, and that forbids taking another way here
        emitter.rewind(checkpoint)
    return False


def _try_difference(node: ir.DiffSet, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Match a character of the base that no exclusion matches.

    This probes the exclusions first, as lookaheads that consume nothing. An excluded character therefore reaches no
    trial consume. A `(max)` window would otherwise read such a trial as a consume committed past the window's edge, and
    refuse the parse.
    """
    for excluded in node.minus:
        if _try_probe(excluded, emitter, grammar):
            return False
    start = emitter.checkpoint()
    if not _try_match(node.base, emitter, grammar, _try_accept):
        emitter.give_back(start)
        return False
    if k():
        return True
    emitter.give_back(start)
    return False


def _try_optional(node: ir.OptTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    Match the item greedily where it matches. The parse takes the empty match where the item does not match.
    """
    checkpoint = emitter.checkpoint()
    if _try_match(node.item, emitter, grammar, k):
        return True
    if emitter.is_unwinding():
        return False  # the empty match is another way, which a region being given back does not take
    emitter.rewind(checkpoint)
    return k()


def _try_choice(node: ir.ChoiceState, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    An alternative enters on a gate of its own. The run tries the alternatives in the order held. The first alternative
    to take the parse on wins.
    """
    for alternative in node.alternatives:
        if _try_match(alternative, emitter, grammar, k):
            return True
        if emitter.is_unwinding():
            return False
    return False


def _try_way(node: ir.AlternativeState, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A way is a gate, then what the way performs, then what it calls. Those run as a single sequence.

    The gate is a set of questions and no more. A question asks about the position the parse enters the way at. This
    asks the questions in the order held, and that order does not matter. Backtracking makes trying the parts in order
    the same as asking the gate first and committing to it. The C says what a gate means to a parser that does not
    backtrack.
    """
    # A hoisted gate makes the decision the production it admits would make. A run recording coverage asks the gate on
    # its own to attribute the refusal.
    if emitter.coverage is not None and node.gate.guards and not _does_gate_hold(node.gate, emitter, grammar):
        for called in (node.first, node.second):
            named = getattr(called, "name", None)
            if isinstance(named, str) and named in grammar:
                emitter.coverage.rejected.add(named)
        return False
    parts = tuple(node.gate.guards) + tuple(node.actions)
    # A recovery riding the edge is the `(recover)` scope over the call it protects. A way with no `first` is a tail
    # call, and its `second` is the call rather than what stands past one.
    calls: list[ir.Node | None] = [node.first, node.second]
    at = 0 if node.first is not None else 1
    riding = calls[at]
    if node.recover is not None and riding is not None:
        calls[at] = ir.RecoverWrapper(node.recover, riding)
    parts += tuple(item for item in calls if item is not None)
    if emitter.checking is None:
        return _try_match(ir.SeqTree(parts), emitter, grammar, k)
    checking = emitter.checking
    # Where the parse stands entering the way and where the way leaves it. Taken inside the continuation, past the way
    # the parse may have been rewound to somewhere the way did not leave it.
    entered = _guard_answers_now(emitter, grammar)

    def try_left() -> bool:
        checking(node, entered, _guard_answers_now(emitter, grammar), emitter)
        return k()

    return _try_match(ir.SeqTree(parts), emitter, grammar, try_left)


def _try_gated_char(
    node: ir.ConsumeCharAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    A consume names a set. The run checks the gated character against that set.

    The gate decides the match. A character outside that set then marks a gate that did not do its job, rather than a
    match to refuse. The run then reports such a gate wherever the gate has since moved to.
    """
    if emitter.position >= len(emitter.chars):
        raise AssertionError("a gated character is not there: the gate let a refused character through")
    if not _try_probe(node.set, emitter, grammar):
        raise AssertionError("a gated character is not the consume's own set: the gate admitted what it should not")
    checkpoint = emitter.checkpoint()
    if emitter.try_consume(_is_only_the_mark(node.set, grammar)) and k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_limited_span(
    node: ir.ConsumeLimitedSpanAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    A single consume takes up to `limit` characters of the set. The consume leaves behind whether it reached the limit.

    The consume matches throughout. A consume that finds fewer than the limit takes what is there and says so, where a
    counted consume takes none and gives it back. A way's performance here therefore cannot fail. The guard past the
    consume reads whether the consume reached the limit.
    """
    limit = _as_number(node.limit, emitter, grammar)
    checkpoint = emitter.checkpoint()
    taken = 0
    while taken < limit and _try_match(node.set, emitter, grammar, _try_accept):
        taken += 1
    if taken == 0:
        raise AssertionError("a limited run consumed nothing: the gate let a refused character through")
    emitter.did_fill_span = taken >= limit
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_did_fill_span(_node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """Whether the consume in front of this took its whole limit."""
    if emitter.did_fill_span is None:
        raise AssertionError("a guard asks whether a span filled, and the item in front of it took no limit")
    return k() if emitter.did_fill_span else False


def _try_span(node: ir.ConsumeSpanAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A maximal run of the set. The canonical form writes that as a `StarTree` over a character class.

    This holds the run to consuming a character. The check reads what the run consumed rather than what the set would
    match. The gate in front says the set is there. A run that consumed nothing means the gate let a refused character
    through.
    """
    started = emitter.position

    def try_consumed() -> bool:
        if emitter.position == started:
            raise AssertionError("a run of a class consumed nothing: the gate let a refused character through")
        return k()

    return _try_repeat(node.set, emitter, grammar, try_consumed)


def _try_trimmed_run(node: ir.TrimStarTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A maximal run of `full`. The trailing `trim` characters go back.

    Consume greedily. Remember where the last character outside `trim` ended. Then rewind to it. The char-class guards
    make the run possessive. The run does not take a character a later rule needs, and no shorter match remains to fall
    back to.
    """
    started = emitter.position
    kept = emitter.checkpoint()  # where the run stood past the last character it keeps, empty at the start
    while emitter.position < len(emitter.chars):
        at_trim = _try_probe(node.trim, emitter, grammar)
        before = emitter.position
        step = emitter.checkpoint()
        if not _try_match(node.full, emitter, grammar, _try_accept):
            if emitter.is_unwinding():
                return False  # a turn that failed under an unwind fails the run, it does not end it
            emitter.rewind(step)
            break
        if emitter.position == before:
            emitter.rewind(step)  # a zero-width match cannot repeat without looping
            break
        if not at_trim:
            kept = emitter.checkpoint()
    emitter.rewind(kept)
    if emitter.position == started:
        raise AssertionError("a trimmed run consumed nothing: the gate let a refused character through")
    return k()


def _try_counted_run(node: ir.RepTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The item exactly `count` times. A turn's continuation is the turn after it."""
    count = _as_number(node.count, emitter, grammar)

    def try_step(index: int) -> bool:
        if index >= count:  # a non-positive count matches nothing, as an indent of no length does
            return k()
        return _try_match(node.item, emitter, grammar, lambda: try_step(index + 1))

    return try_step(0)


def _try_open_provisional(_node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    The provisional run opened. A later retype may give a different code to the tokens the run takes from here on.
    """
    checkpoint = emitter.checkpoint()
    emitter.open_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_mark_provisional(_node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A mark in the provisional run. A retype measures from that mark."""
    checkpoint = emitter.checkpoint()
    emitter.mark_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_retype_provisional(
    node: ir.RetypeProvisionalAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """Give the provisional run the codes it holds."""
    checkpoint = emitter.checkpoint()
    emitter.retype_provisional(node.rest, node.breaks, node.region)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_inject(
    node: ir.InjectBeforeAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """Markers put in front of the tokens the provisional run holds. The action names the place."""
    checkpoint = emitter.checkpoint()
    emitter.inject_before(node.codes, node.at)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_commit_provisional(
    _node: ir.Node, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The provisional run closed. The run's tokens then stay as the run left them."""
    checkpoint = emitter.checkpoint()
    emitter.commit_provisional()
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_error(node: ir.ErrorAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The parse emits an error token here."""
    checkpoint = emitter.checkpoint()
    emitter.error(MESSAGES[node.message])
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_marker(node: ir.EmitAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The parse emits a marker here."""
    checkpoint = emitter.checkpoint()
    emitter.marker(node.code)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_push_indent(
    node: ir.PushIndentAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    An indentation put in force. The matching pop takes it off.

    A level is sometimes said against the indentation it displaces, as `n + 1` or `n - 1`. `n + m` says it that way too.
    The matching pop inverts such a level to restore what was there. A level naming the column, the floor or a literal
    has no relation to the indentation it displaces. `s-l+block-indented` pushes a column over a larger indentation, and
    `l+block-mapping` pushes a column over an equal indentation. `c-l+folded` pushes the floor below the indentation in
    force. A pop of such a level says nothing of the indentation it restores.
    """
    checkpoint = emitter.checkpoint()
    # Working out what to push is not a read of the indentation in force. The level replaces that indentation.
    emitter.passing_arguments = True
    level = _evaluate(node.level, emitter, grammar)
    emitter.passing_arguments = False
    emitter.stack += (("indent", level, node.pair),)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_pop_indent(
    node: ir.PopIndentAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The indentation on top comes off. The indentation its push displaced comes back."""
    checkpoint = emitter.checkpoint()
    # The pop says which indentation it takes off, and takes off whatever is on top. The level named is checked once the
    # pop has happened, which is the scope it was written in.
    value, _opened, emitter.stack = _popped(emitter, "indent", "an indentation", node.pair)
    if node.level is not None:  # a pop naming the level it takes off must match it, a pop naming none matches nothing
        said = _evaluate(node.level, emitter, grammar)
        if value != said:
            raise AssertionError(f"the pop takes off an indentation of {value!r} where it says {said!r}")
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_push_code(
    node: ir.PushCodeAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The characters from here on take a token code. The parse cuts the run where that code takes over."""
    checkpoint = emitter.checkpoint()
    emitter.cut()
    emitter.stack += (("code", emitter.code, node.pair),)  # the code this push displaces, for its own pop to take back
    emitter.code = node.code
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_pop_code(node: ir.PopCodeAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The code on top taken off. The run cuts where the displaced code takes over again."""
    checkpoint = emitter.checkpoint()
    emitter.cut()
    displaced_code, _opened, emitter.stack = _popped(emitter, "code", "a `(token)` code", node.pair)
    assert isinstance(displaced_code, str), f"the stack holds {displaced_code!r} as a token code"
    emitter.code = displaced_code
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_open_window(
    node: ir.OpenWindowAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """A budget of characters opened, past which a consume is the overflow the window names."""
    checkpoint = emitter.checkpoint()
    displaced = (emitter.ceiling, emitter.ceiling_message)
    if emitter.ceiling is None:  # the outermost only. an open under it is inside the budget it already bounds
        emitter.ceiling = emitter.position + _as_number(node.limit, emitter, grammar)
        emitter.ceiling_message = node.message
    emitter.stack += (("window", displaced, node.pair),)  # the window this open displaced, for its close to take back
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_close_window(
    node: ir.CloseWindowAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The budget closed. The window its open displaced comes back."""
    checkpoint = emitter.checkpoint()
    displaced, _opened, emitter.stack = _popped(emitter, "window", "a `(max)` window", node.pair)
    assert isinstance(displaced, tuple), f"the stack holds {displaced!r} as a `(max)` window"
    emitter.ceiling, emitter.ceiling_message = displaced
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_cut(node: ir.CutAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A commit. A failure past the cut is this error, and the parse does not retry what sits behind the cut."""
    if k():
        return True
    if emitter.failing is None:
        emitter.failing = node.message
    return False


def _try_open_committed(
    node: ir.PushMessageAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    A committed region opened. The error holds until the region's close runs.

    The record pairs with the `PopMessageAction` that closes the region and marks it reached. A failure unwinding back
    here with the region still open is the error. Through a closed region a failure backtracks like any other match, and
    the commitment does not reach past the close. Those are a `(commit)` scope's terms, and the close falls at the
    scope's end. The stack entry holds the record, and a close writes `reached` into the record. A rewind then puts back
    which regions are open, rather than what a region has already reached.
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


def _try_close_committed(
    node: ir.PopMessageAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The committed region closed, and its commitment holds under any later backtracking."""
    record, opened, emitter.stack = _popped(emitter, "message", "a committed region", node.pair)
    assert isinstance(record, list), f"the stack holds {record!r} as a committed region"
    record[0] = True
    if k():
        return True
    emitter.stack += (("message", record, opened),)  # backtracked into the region. it is open again, though kept
    return False


def _try_committed(node: ir.CommitWrapper, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    A `(cut)` scoped to `item`. The error comes where `item` fails to reach its own end.

    `reached` goes true the first time `item` matches through to the continuation. A continuation that then fails
    backtracks `item` entire, like any other match, and the commitment does not reach past `item`. A `(cut)` inside
    `item` fires by its own standard and escapes past here.
    """
    reached = [False]

    def try_at_end() -> bool:
        reached[0] = True
        return k()

    if _try_match(node.item, emitter, grammar, try_at_end):
        return True
    if reached[0]:
        return False
    if emitter.failing is None:
        emitter.failing = node.message
    return False


def _try_open_recovery(
    node: ir.PushRecoveryAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """A recovery region opened. The region answers a cut raised inside it while the region is open."""
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
            return False  # the region has closed. whatever catches this sits further out
        return _try_recover(entry, emitter, grammar)
    held, _opened, emitter.stack = _popped(emitter, "recovery", "a recovery region", node.pair)
    assert held is entry, "a recovery region closed out of order"
    return False


def _try_close_recovery(
    node: ir.PopRecoveryAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The recovery region closed. From here on, the handler in force before the region opened answers a cut."""
    entry, opened, emitter.stack = _popped(emitter, "recovery", "a recovery region", node.pair)
    assert isinstance(entry, _Recovery), f"the stack holds {entry!r} as a recovery region"
    entry.is_closed[0] = True
    if k():
        return True
    if emitter.failing is not None:
        return False  # a cut past the close, which this region no longer catches
    entry.is_closed[0] = False
    emitter.stack += (("recovery", entry, opened),)  # what follows failed. the region is open again for a way in it
    return False


def _try_recovering(
    node: ir.RecoverWrapper, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    The item. A cut inside the item can ask this rule to answer, and this rule then takes the error and answers instead.
    """
    depth, held = len(emitter.pending), emitter.checkpoint()
    if _try_match(node.item, emitter, grammar, k):
        return True
    if emitter.failing is None:
        return False
    # The cut asks whether this rule catches it. The recovery reads this rule's parameters rather than those of whatever
    # failed below it.
    stopped = emitter.checkpoint()
    emitter.code, emitter.stack, emitter.forbidden = held.code, held.stack, held.forbidden
    emitter.env, emitter.ceiling, emitter.ceiling_message = dict(held.env), held.ceiling, held.ceiling_message
    code, emitter.failing = emitter.failing, None
    emitter.error(MESSAGES[code])
    while len(emitter.pending) > depth:
        emitter.marker(emitter.pending[-1])  # close what `item` opened, down to here and no further
    if _try_match(node.recovery, emitter, grammar, _try_accept):
        return k()  # recovered. continue as though `item` had matched, and a repetition takes its next turn
    emitter.rewind(stopped)  # this rule does not catch it after all. leave no trace and let it go on up
    emitter.failing = code
    return False


def _try_open_turn(
    node: ir.StartMustConsumeAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    A turn that must take a character opens.

    The turn has not matched where its close runs at the open's position. A loop that would repeat the turn forever ends
    there instead.
    """
    checkpoint = emitter.checkpoint()
    emitter.stack += (("consume", emitter.position, node.pair),)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_did_consume_since_open(
    node: ir.DidConsumeSinceOpenGuard, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    Whether the turn that must take a character has done so. The question does not close the turn.

    This asks and no more, the way a gate's questions ask. The open stays on the stack. A way can then ask in front of
    the actions it performs, and a later way can ask again.

    This looks down the stack for its own open rather than demanding an open on top. A pop takes what its own push put
    there, and a pop is right to refuse anything else. A read is not a pop. A code or a committed region opened since
    says nothing about which turn this asks after, and the pair answers that.
    """
    # A `consume` entry holds the pair its open wrote. The pairless entry a run stands on is the indentation it is
    # seeded with.
    for held, opened_at, opened in reversed(emitter.stack):
        if held != "consume" or opened is None or not opened & node.pair:
            continue
        return k() if emitter.position != opened_at else False
    raise AssertionError("a guard asks about a turn that must take a character, and no turn of its own is open")


def _try_close_turn(
    node: ir.EndMustConsumeAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """The turn closes, and its open comes off the stack. The guard in front asks whether the turn matched."""
    opened_at, opened, emitter.stack = _popped(emitter, "consume", "a turn that must take a character", node.pair)
    if k():
        return True
    emitter.stack += (("consume", opened_at, opened),)  # backtracked into the turn. it stands open again
    return False


def _try_open_settled(
    node: ir.PushBackTrackAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    A settled region opens.

    The region's ways sit between here and the close as live choices. The close says that a failure past it unwinds to
    here. A choice on the way then stays where it went. The unwind gives the region back whole. Ordinary backtracking
    continues from in front of the region.
    """
    checkpoint = emitter.checkpoint()
    emitter.stack += (("backtrack", None, node.pair),)
    if k():
        return True
    if emitter.unwinding is not None and emitter.unwinding & node.pair:
        emitter.unwinding = None  # the open the close was unwinding to. the region is given back and that is all
    emitter.give_back(checkpoint)  # a cut unwinding through the region continues past it, giving nothing back
    return False


def _try_close_settled(
    node: ir.PopBackTrackAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    The settled region closes. A failure past the close fails the region entire, and a choice inside the region stays
    where it went. An unwind already under way stays untouched. That unwind heads for an open further out than this
    open.
    """
    _held, _opened, emitter.stack = _popped(emitter, "backtrack", "a settled region", node.pair)
    if k():
        return True
    if emitter.unwinding is None:
        emitter.unwinding = node.pair
    return False


def _try_forbidding(
    node: ir.SetForbiddenAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    The set that may not appear from here on, held as a slot rather than a stack. A write names the set, and no pop puts
    an earlier set back.
    """
    checkpoint = emitter.checkpoint()
    emitter.forbidden = () if node.item is None else (node.item,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_write(node: ir.SetVarAction, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A parameter given the value the expression works out to."""
    checkpoint = emitter.checkpoint()
    value = _evaluate(node.value, emitter, grammar)
    emitter.env[node.param] = value
    if node.param in emitter.globals:  # the stack beside the slot, which a nested write puts its own value on
        emitter.shadow[node.param] = emitter.shadow.get(node.param, ()) + (value,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_clear(node: ir.ClearVarAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """A parameter put back to the state a fresh parse gives it. Reading it there is a fault."""
    checkpoint = emitter.checkpoint()
    if node.param in emitter.globals:
        # A `(clear)` with no `(set)` behind it is not refused.
        emitter.shadow[node.param] = emitter.shadow.get(node.param, ())[:-1]
    emitter.env[node.param] = None
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_raise(node: ir.IncreaseAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """An indentation floor moved up to the column the parse is at, where the column is the higher."""
    checkpoint = emitter.checkpoint()
    raised = max(_a_quantity(emitter.env.get(node.param, 0)), emitter.mark.column)
    emitter.env[node.param] = raised
    if node.param in emitter.globals:
        # Raising a floor is not establishing one. It moves what stands rather than putting something new on. Where
        # nothing stands it is the first. Reading it as `0` already meant that.
        held = emitter.shadow.get(node.param, ())
        emitter.shadow[node.param] = (held[:-1] + (raised,)) if held else (raised,)
    if k():
        return True
    emitter.give_back(checkpoint)
    return False


def _try_binding(node: ir.BindTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The condition, and the parameter given its value once the condition has matched."""

    def try_bound() -> bool:  # noqa: N807. a continuation, not a special method
        checkpoint = emitter.checkpoint()
        value = _evaluate(node.value, emitter, grammar)
        emitter.env[node.param] = value
        if node.param in emitter.globals:  # a write is a write however it is written. the stack beside the slot
            emitter.shadow[node.param] = emitter.shadow.get(node.param, ()) + (value,)
        if k():
            return True
        emitter.give_back(checkpoint)  # undo the bound value so the condition can go on
        return False

    return _try_match(node.cond, emitter, grammar, try_bound)


def _try_window(node: ir.MaxWrapper, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """
    The item runs under a budget of characters. A consume past that budget is the overflow the window names.

    A match that would consume past the edge exhausts the window. `consume` fails the window's cut. The overflow unwinds
    to the recovery and keeps the tokens up to the edge. The error the caller emits cuts the open token into the last of
    those tokens. The path that leaves the window puts the ceiling back on.
    """
    if node.item is None:
        return k()  # the vendored grammar's bare length note, which libyeast's own grammar never places
    if emitter.ceiling is not None:
        # nested. the outermost window applies, an inner window being inside the budget the outer already bounds.
        return _try_match(node.item, emitter, grammar, k)
    emitter.ceiling = emitter.position + _as_number(node.limit, emitter, grammar)
    emitter.ceiling_message = node.message  # a consume past the edge raises this, keeping the tokens up to it

    def try_past_window() -> bool:
        emitter.ceiling = None  # the window bounds `item`, not the parse that continues once it has matched
        emitter.ceiling_message = None
        # Where this fails, `item` tries its next way. Its own rewind restores the ceiling the checkpoint kept.
        return k()

    try:
        return _try_match(node.item, emitter, grammar, try_past_window)
    finally:
        emitter.ceiling = None
        emitter.ceiling_message = None


def _try_exclusion(
    node: ir.ExcludeAtAction, emitter: Emitter, _grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    The set that may not match from here on, added to the forbidden set in force. It holds until the production returns.
    """
    saved_forbidden = emitter.forbidden
    emitter.forbidden = saved_forbidden + (node.item,)
    if k():
        return True
    emitter.forbidden = saved_forbidden
    return False


def _try_look_behind(
    node: ir.LookBehindGuard, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation
) -> bool:
    """
    Whether the item matches the character behind the parse.

    The grammar's look-behind asks whether an `ns-char` is behind. That is a single character. This reads the character
    behind rather than searching backwards for a start the item could reach here from. This refuses an item of more than
    a single character. Searching for where such an item began would re-match input the parse has already read, and cost
    the length of that input.
    """
    if not ir.is_one_char(node.item, grammar):
        raise ValueError(f"a look-behind asks about {node.item}. That is not a single character.")
    target = emitter.position
    if target == 0:
        return False  # nothing stands behind the first character
    checkpoint = emitter.checkpoint()
    emitter.probing += 1  # a look-behind reads speculatively, past a `(max)` edge. the rewind restores it
    emitter.position = target - 1
    did_reach = _try_match(node.item, emitter, grammar, lambda: emitter.position == target)
    if emitter.failing is not None:
        emitter.failing = None  # a cut behind here is speculative too, as one inside a lookahead is
        did_reach = False
    emitter.rewind(checkpoint)
    return k() if did_reach else False


def _try_token(node: ir.TokenWrapper, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The item, the characters it takes under the code, cut from what is on either side."""
    entry = emitter.checkpoint()
    emitter.cut()
    surrounding = emitter.code  # the production's own code, restored at the token's trailing edge
    emitter.code = node.code

    def try_close_token() -> bool:
        middle = emitter.checkpoint()
        emitter.cut()  # end the token's run at its trailing edge
        emitter.code = surrounding  # the following characters take the surrounding code again
        if k():
            return True
        emitter.code = node.code
        emitter.give_back(middle)  # reopen the run so the wrapped item can try its next way
        return False

    did_match = _try_match(node.item, emitter, grammar, try_close_token)
    if not did_match:
        emitter.give_back(entry)  # undo the leading cut and the code change
    return did_match


def _try_wrapped(node: ir.Wrapper, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The item between its opening and closing markers."""
    entry = emitter.checkpoint()
    emitter.marker(node.begin)

    def try_close_wrap() -> bool:
        middle = emitter.checkpoint()
        emitter.marker(node.end)
        if k():
            return True
        emitter.give_back(middle)
        return False

    did_match = _try_match(node.item, emitter, grammar, try_close_wrap)
    if not did_match:
        emitter.give_back(entry)  # a failure on its way to a recovery leaves the `begin` for the recovery to close
    return did_match


def _try_switch(node: ir.CaseTree, emitter: Emitter, grammar: Mapping[str, ir.Prod], k: _Continuation) -> bool:
    """The branch whose value the parameter holds, and the `else` where no branch holds it."""
    value = emitter.env.get(node.var)  # a parameter left unset is no branch's value. it takes the `else`
    for branch in node.branches:
        if branch.value == value:
            return _try_match(branch.item, emitter, grammar, k)
    if node.default is not None:
        return _try_match(node.default, emitter, grammar, k)
    return False


# The kinds a way performs, bar the consume whose answer a guard is waiting to read. A guard is not among them. A
# question asked between that consume and the guard leaves what the consume did in place. A gate can then tell the ways
# apart.
_TAKES_THE_ANSWER_AWAY = frozenset(ir.PERFORMED_NODES) - {ir.ConsumeLimitedSpanAction}

# The step the machine takes per kind. This is the machine itself rather than a question asked about the grammar. It is
# therefore a step to take rather than an `ir.Question` to consult. A call through a question's type costs C stack, and
# a direct call spends none. A C stack holds a bounded nesting, and a parse of a real input reaches far past that bound.
_MATCHED: dict[type, Callable[..., bool]] = {
    ir.OneCharSet: _try_char,
    ir.CharSet: _try_set,
    ir.RangeSet: _try_range,
    ir.InvalidSet: _try_invalid,
    ir.EmptyTree: lambda node, emitter, grammar, k: k(),
    # The twin of the empty match. A fail comes back wherever it appears. A choice holding a fail goes on to its next
    # way. A run holding a fail does not match.
    ir.FailTree: lambda node, emitter, grammar, k: False,
    ir.RefCall: _try_call,
    ir.SeqTree: _try_sequence,
    ir.AltTree: _try_alternation,
    ir.DiffSet: _try_difference,
    ir.OptTree: _try_optional,
    ir.ChoiceState: _try_choice,
    ir.AlternativeState: _try_way,
    ir.ConsumeCharAction: _try_gated_char,
    ir.ConsumeLimitedSpanAction: _try_limited_span,
    ir.DidMatchFullSpanGuard: _try_did_fill_span,
    ir.ConsumeSpanAction: _try_span,
    # A trimmed run takes the name `TrimStarTree`. The canonical form uses that name for the same match.
    ir.ConsumeTrimmedSpanAction: lambda node, emitter, grammar, k: _try_match(
        ir.TrimStarTree(node.full, node.trim), emitter, grammar, k
    ),
    ir.TrimStarTree: _try_trimmed_run,
    ir.StarTree: lambda node, emitter, grammar, k: _try_longest_run(node.item, 0, emitter, grammar, k),
    ir.PlusTree: lambda node, emitter, grammar, k: _try_longest_run(node.item, 1, emitter, grammar, k),
    ir.RepTree: _try_counted_run,
    ir.CaseTree: _try_switch,
    ir.SetForbiddenAction: _try_forbidding,
    ir.SetVarAction: _try_write,
    ir.ClearVarAction: _try_clear,
    ir.IncreaseAction: _try_raise,
    ir.BindTree: _try_binding,
    ir.IsLessThanGuard: lambda node, emitter, grammar, k: (
        k() if _as_number(node.a, emitter, grammar) < _as_number(node.b, emitter, grammar) else False
    ),
    ir.IsLessEqualGuard: lambda node, emitter, grammar, k: (
        k() if _as_number(node.a, emitter, grammar) <= _as_number(node.b, emitter, grammar) else False
    ),
    ir.MaxWrapper: _try_window,
    ir.StartOfLineGuard: lambda node, emitter, grammar, k: k() if emitter.is_sol else False,
    ir.EndOfStreamGuard: lambda node, emitter, grammar, k: k() if emitter.position == len(emitter.chars) else False,
    ir.LookGuard: lambda node, emitter, grammar, k: k() if _try_probe(node.item, emitter, grammar) else False,
    ir.NegLookGuard: lambda node, emitter, grammar, k: k() if not _try_probe(node.item, emitter, grammar) else False,
    ir.ExcludeAtAction: _try_exclusion,
    ir.LookBehindGuard: _try_look_behind,
    ir.TokenWrapper: _try_token,
    ir.Wrapper: _try_wrapped,
    ir.EmitAction: _try_marker,
    ir.PushIndentAction: _try_push_indent,
    ir.PopIndentAction: _try_pop_indent,
    ir.PushCodeAction: _try_push_code,
    ir.PopCodeAction: _try_pop_code,
    ir.OpenWindowAction: _try_open_window,
    ir.CloseWindowAction: _try_close_window,
    ir.OpenProvisionalAction: _try_open_provisional,
    ir.MarkProvisionalAction: _try_mark_provisional,
    ir.RetypeProvisionalAction: _try_retype_provisional,
    ir.InjectBeforeAction: _try_inject,
    ir.CommitProvisionalAction: _try_commit_provisional,
    ir.CutAction: _try_cut,
    ir.PushMessageAction: _try_open_committed,
    ir.PopMessageAction: _try_close_committed,
    ir.CommitWrapper: _try_committed,
    ir.ErrorAction: _try_error,
    ir.PushRecoveryAction: _try_open_recovery,
    ir.PopRecoveryAction: _try_close_recovery,
    ir.RecoverWrapper: _try_recovering,
    ir.ConsumeNoCharAction: _try_no_char,
    ir.StartMustConsumeAction: _try_open_turn,
    ir.DidConsumeSinceOpenGuard: _try_did_consume_since_open,
    ir.EndMustConsumeAction: _try_close_turn,
    ir.PushBackTrackAction: _try_open_settled,
    ir.PopBackTrackAction: _try_close_settled,
}


def _try_recover(entry: _Recovery, emitter: Emitter, grammar: Mapping[str, ir.Prod]) -> bool:
    """
    The region `entry` opened answers a failed cut here. Emit the error, close the markers down to where the region
    began, and match the recovery. Then match the resume, and then the way's ordinary continuation. The parse then goes
    on as though the region had matched.

    The scopes the abandoned parse was inside are already back, and a frame gave its own scopes back on the way out. The
    region's scopes therefore remain here. The scopes the region opened are still open, and this closes them.

    A recovery that does not match is this region declining to answer. The parse then remains as this found it, and the
    cut goes on unwinding.
    """
    stopped = emitter.checkpoint()
    emitter.code, emitter.stack, emitter.forbidden = entry.code, entry.stack, entry.forbidden
    emitter.env, emitter.ceiling, emitter.ceiling_message = dict(entry.env), entry.ceiling, entry.ceiling_message
    code, emitter.failing = emitter.failing, None
    emitter.error(MESSAGES[code])
    while len(emitter.pending) > entry.pending:
        emitter.marker(emitter.pending[-1])  # close what the region covered opened, down to here and no further
    resume_at = emitter.returns[entry.returns - 1]
    assert entry.recovery is not None and entry.resume is not None, "a recovery region answering with nothing"
    recovery, resume = entry.recovery, entry.resume
    if _try_match(recovery, emitter, grammar, lambda: _try_match(resume, emitter, grammar, resume_at)):
        return True
    emitter.rewind(stopped)
    emitter.failing = code
    return False


def run(
    grammar: Mapping[str, ir.Prod],
    production: str,
    data: bytes,
    parameters: Mapping[str, str] | None = None,
    coverage: Coverage | None = None,
    checking: _Checking | None = None,
) -> list[wire.Token]:
    """
    Run `production` on the UTF-8 `data`. This returns the yeast tokens the run emits, and a rejection appears among
    them where the run rejects.

    A production backtracks. `PLAN.md` owes a committed mode, and that mode enters the productions whose decisions a
    proof covers. `PLAN.md` owes the certificate naming those productions as well.

    `parameters` binds the production's parameters from the fixture's filename. The filename holds them as text. The run
    reads `n` and `m` as numbers. The finite `c`, `t` and `r` stay text. `i` stays text. `p` says how much of `data` the
    parse crosses to reach the rule, and names no parameter. A production declaring `r` with no `r` given resumes the
    way a zeroed `ys_options` does.

    `coverage` is a `Coverage` for the run to record what the parse reached and what it saw refuse. It is `None` where
    the caller wants no record.

    `checking` hears what the parse may be inside at a way and at an action. This reads nothing back from `checking`,
    and the caller collects the faults. The caller thereby checks a space computed over the grammar against a parse that
    ran. `checking` hears inside a lookaround as much as outside.

    The run enters the production as a reference rather than by matching the body.
    """
    # A caller names the production polymorphically, and a monomorphized grammar holds its specialized copies alone. The
    # resume policy is read before the move to the copy takes it from the arguments.
    parameters = dict(parameters or {})
    reached_through = int(parameters.pop("p", "0"))
    resume = parameters.get("r", "n")
    production, parameters = ir.entry(grammar, production, parameters)
    emitter = Emitter(data)
    # What the rule is entered after is read rather than stipulated. The characters before `p` are taken, and the token
    # they built is dropped.
    if reached_through > len(emitter.chars):
        raise ValueError(
            f"the parse reaches the rule through {reached_through} characters of an input holding "
            f"{len(emitter.chars)}"
        )
    for _character in range(reached_through):
        # A byte-order mark is refused rather than taken. What it does to the column is the grammar's to say, and there
        # is no consume here to read that off.
        if emitter.chars[emitter.position] == wire.BYTE_ORDER_MARK:
            raise ValueError(
                "the parse reaches the rule through a byte-order mark. the grammar says which column that mark sits at."
            )
        emitter.try_consume()
    emitter.open_token = None
    emitter.coverage = coverage
    emitter.holds_indent, emitter.globals = _what_a_grammar_holds(grammar)
    emitter.checking = checking
    emitter.env = {name: int(value) if name in ("n", "m") else value for name, value in parameters.items()}
    if "r" in grammar[production].params:
        emitter.env.setdefault("r", resume)  # a production run without a resume policy takes the zeroed one, no-resume
    if "n" in emitter.env:  # the indentation the run is entered under, which no alternative pushed and none pops
        emitter.stack = (("indent", emitter.env["n"], None),)
    entered = emitter.stack  # what the run itself put there. a parse that closed what it opened ends at this
    entry = ir.RefCall(
        production, tuple(ir.LitValue(_a_value(emitter.env.get(name))) for name in grammar[production].params)
    )

    # A cut says where the unwind lands and nothing more. A resumed parse may commit and fail again, which makes
    # recovery a loop rather than a single handoff.
    node = entry
    failed_at = None
    while True:
        did_match = _try_match(node, emitter, grammar, _try_accept)
        if emitter.failing is not None:
            code, emitter.failing = emitter.failing, None
            _fail(emitter, MESSAGES[code])  # committed and nothing caught it. the error names what the cut wanted
        else:
            if did_match:
                emitter.cut()
                # A parse that has matched has closed what it opened, leaving the stack at what the run itself put
                # there. A close left unreached leaves its scope standing here.
                if emitter.stack != entered:
                    raise AssertionError(
                        "the parse ends holding "
                        + (", ".join(kind for kind, _value, _pair in emitter.stack) or "nothing")
                        + ". the parse began holding "
                        + (", ".join(kind for kind, _value, _pair in entered) or "nothing")
                    )
                return emitter.tokens
            # A root parse is total and recovers rather than failing. An isolated non-root production may fail, and
            # reports what matched and where it stopped.
            if production == ir.ROOT:
                raise AssertionError(f"{ir.ROOT} failed without committing: the root production must be total")
            _fail(emitter, "")  # uncommitted. a bare error where what matched ends, no expectation to name
        # Recovering to where we already recovered from would go round for ever. A failed cut on a document boundary
        # leaves `l-unparsed` nothing to consume. What resumes there has to be what makes the progress.
        if failed_at == emitter.position:
            raise AssertionError(f"recovery at position {failed_at} consumed nothing: the parse cannot go on")
        failed_at = emitter.position
        # The parse has unwound past each rule that might have caught it, to the stream's own level. The resume policy
        # resolves the way the entry did, into the name where the grammar is monomorphized.
        recover, recover_args = ir.entry(grammar, _RECOVER, {"n": -1, "r": resume})
        emitter.stack = (("indent", recover_args["n"], None),)  # the stream's own level, the recovery entered under it
        entered = emitter.stack
        node = ir.RefCall(
            recover, tuple(ir.LitValue(_a_value(recover_args[parameter])) for parameter in grammar[recover].params)
        )
