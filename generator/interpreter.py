# SPDX-License-Identifier: MIT
"""A backtracking interpreter of the grammar, run against libyeast's conformance fixtures.

Slow and obviously correct: it matches a production against an input the way the grammar reads, character by character
with backtracking, and emits the yeast token stream — so that libyeast's grammar is proved to produce the reference's
tokens before any C exists to be wrong, and so that, taught the canonical form later, it becomes the net every
normalization step is checked against. It takes the grammar as an argument, so the same interpreter and the same
fixtures judge the grammar as it is now and the structurally-simplified grammar later.

`SUPPORTED` says which nodes it knows; `coverable` says which fixtures rest on only those, and so are the ones it is
asked to reproduce. It matches the character-level nodes (`Char`, `Range`, `Diff`, `Empty`, `Seq`, `Alt`, `Ref`), the
repetitions (`Star`, `Plus`, `Opt`, `Rep`), the parameter machinery (`Case`, `Bind`, `SetVar`, the arithmetic, and the
`Lt`/`Le`/`Max`/`Bound` predicates), and the assertions and lookahead (`StartOfLine`, `EndOfStream`, `Look`, `NegLook`,
`LookBehind`, `ExcludeAt`). It produces tokens from the annotation nodes: `Token` gives its characters a code and cuts
the run at both edges, `Wrap` brackets its match in `begin`/`end` markers, and `Emit` is a marker on its own.

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
# than Python's default limit allows even for the small conformance inputs.
sys.setrecursionlimit(20000)

# The text of every message a `(cut)` names, from the grammar's companion table — the one source the interpreter and the
# generated C table both read, so the error text the interpreter emits is the error text the parser will.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "grammar", "messages.yaml")) as _f:
    MESSAGES = yaml.safe_load(_f)


class CommitFailure(Exception):
    """Raised when the parse fails after passing a `(cut)`.

    It carries the cut's message `code`, and unwinds past the backtracking frames — which is what a commit is: none of
    them gets to try another way — to the handler that turns it into an error token and the unparsed recovery.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


BYTE_ORDER_MARK = 0xFEFF  # consumed without ending the start of a line, unlike any other character

# The grammar nodes the interpreter matches, and the value expressions it evaluates. Both grow a family at a time, and
# with them the fixtures the interpreter can reproduce. `Branch` is structural, shared by `Case` and `Flip`.
SUPPORTED_NODES = (
    ir.Char,
    ir.Range,
    ir.Diff,
    ir.Empty,
    ir.Seq,
    ir.Alt,
    ir.Ref,
    ir.Token,
    ir.Wrap,
    ir.Emit,
    ir.Cut,
    ir.Star,
    ir.Plus,
    ir.Opt,
    ir.Rep,
    ir.Case,
    ir.Bind,
    ir.SetVar,
    ir.Lt,
    ir.Le,
    ir.Max,
    ir.Bound,
    ir.StartOfLine,
    ir.EndOfStream,
    ir.Look,
    ir.NegLook,
    ir.LookBehind,
    ir.ExcludeAt,
)
SUPPORTED_VALUES = (
    ir.Param,
    ir.Lit,
    ir.Match,
    ir.Ord,
    ir.Len,
    ir.Add,
    ir.Sub,
    ir.Flip,
    ir.AutoDetectIndent,
    ir.AutoDetectInLineIndent,
)
SUPPORTED = SUPPORTED_NODES + SUPPORTED_VALUES + (ir.Branch,)


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
        self.env = {}  # the current production's parameters (n/m/c/t) and their values
        self.match_start = 0  # where the enclosing Match() scope began, for Match() and Len(Match())
        self.is_sol = True  # at the start of a line: true at the start of the input, and after every break
        self.forbidden = ()  # patterns that must not match at a start of line — the ongoing `(exclude)` guards in scope

    def checkpoint(self):
        return (
            self.position,
            self.mark,
            len(self.tokens),
            self.run,
            len(self.codes),
            dict(self.env),
            self.match_start,
            self.is_sol,
            self.forbidden,
        )

    def rewind(self, checkpoint):
        (
            self.position,
            self.mark,
            token_count,
            self.run,
            code_count,
            self.env,
            self.match_start,
            self.is_sol,
            self.forbidden,
        ) = checkpoint
        del self.tokens[token_count:]
        del self.codes[code_count:]

    def consume(self):
        """Take the character at the position into the open run, opening one under the current code if none is."""
        if self.run is None:
            self.run = (wire.CODE_CHAR[self.codes[-1]], self.mark, self.position)
        character = self.text[self.position]
        codepoint = ord(character)
        byte_length = len(character.encode("utf-8"))
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

    def error(self, message):
        """Emit an error token: `message` as its text, at the position, spanning no input. Cuts the open run first."""
        self.cut()
        self.tokens.append(wire.Token(wire.ERROR, self.mark, wire.escape(message.encode("utf-8"))))


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
                stack.extend(current.args)  # the argument expressions must be supported too
                continue
            if not isinstance(current, SUPPORTED):
                return False
            for field in current.__dataclass_fields__:
                value = getattr(current, field)
                for child in value if isinstance(value, tuple) else (value,):
                    if hasattr(child, "__dataclass_fields__"):
                        stack.append(child)
    return True


def _leading_spaces(emitter):
    """Count the spaces at the position, without consuming them — what the in-line indentation auto-detection reads."""
    count = 0
    while emitter.position + count < len(emitter.text) and emitter.text[emitter.position + count] == " ":
        count += 1
    return count


def _skip_break(text, position):
    """The position after the break at `position` — CR LF together, or a lone CR or LF — or `position` if none."""
    if position < len(text) and text[position] == "\r":
        return position + (2 if position + 1 < len(text) and text[position + 1] == "\n" else 1)
    if position < len(text) and text[position] == "\n":
        return position + 1
    return position


def _detect_indent(emitter):
    """The indentation of the first content line at or after the position, peeked without consuming.

    A block collection is already at the start of that line; a block scalar is mid-line, at the end of its header, so
    the header line and any empty lines are skipped first.
    """
    text = emitter.text
    position = emitter.position
    if not emitter.is_sol:
        while position < len(text) and text[position] not in "\r\n":
            position += 1
        position = _skip_break(text, position)
    while position < len(text):
        spaces = 0
        while position + spaces < len(text) and text[position + spaces] == " ":
            spaces += 1
        after = position + spaces
        if after >= len(text) or text[after] in "\r\n":
            position = _skip_break(text, after)  # an empty line — skip it
            continue
        return spaces
    return 0


def evaluate(expression, emitter, grammar):
    """Evaluate a value expression — a parameter, a literal, the matched text, or the arithmetic and dispatch over them.

    `Match()` is the input the enclosing `Bound`/`Bind` scope has consumed so far, which is what `Ord` and `Len` read.
    """
    if isinstance(expression, ir.Lit):
        return expression.value
    if isinstance(expression, ir.Param):
        return emitter.env.get(expression.name)  # an out-parameter (m, t) may be passed on before it is set
    if isinstance(expression, ir.Match):
        return emitter.text[emitter.match_start : emitter.position]
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
    """Whether `pattern` matches at the position, leaving the emitter untouched — a lookahead that keeps no effect.

    A `(cut)` inside a lookahead is speculative, not a commit of the whole parse, so a `CommitFailure` here is caught
    and read as "did not match" rather than allowed to escape.
    """
    checkpoint = emitter.checkpoint()
    try:
        matched = match(pattern, emitter, grammar, _accept)
    except CommitFailure:
        matched = False
    emitter.rewind(checkpoint)
    return matched


def _repeat(item, emitter, grammar, k):
    """Match `item` greedily zero or more times, then the continuation — backtracking to fewer repetitions if it fails.

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
    """Whether an in-scope `(exclude)` guard matches at a start of line here — where content must not begin.

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


def _recover_unparsed(emitter):
    """Emit the input from the position to the end as unparsed tokens: each line's content and its break, apart."""
    text = emitter.text
    while emitter.position < len(text):
        emitter.codes.append("unparsed")
        while emitter.position < len(text) and text[emitter.position] not in "\r\n":
            emitter.consume()
        emitter.cut()
        emitter.codes.pop()
        if emitter.position < len(text):
            emitter.codes.append("unparsed-break")
            carriage_return = text[emitter.position] == "\r"
            emitter.consume()
            if carriage_return and emitter.position < len(text) and text[emitter.position] == "\n":
                emitter.consume()  # a CR LF pair is one break, and so one token
            emitter.cut()
            emitter.codes.pop()


def match(node, emitter, grammar, k):
    """Match `node` from the emitter's position and call the continuation `k` for each way it matches, in greedy order.

    Backtracking is success-continuation style: on a match the interpreter calls `k`, and `k` returns whether the rest
    of the parse succeeded from there. If it did, the match commits and this returns True, leaving the emitter in that
    state; if it did not, the match is rewound — tokens and position both — and the next way is tried. This returns
    False once every way is exhausted, having left the emitter as it found it. Nothing is committed until `k` accepts.
    """
    if isinstance(node, ir.Char):
        if emitter.position < len(emitter.text) and ord(emitter.text[emitter.position]) == node.cp:
            if _forbidden_here(emitter, grammar):
                return False
            checkpoint = emitter.checkpoint()
            emitter.consume()
            if k():
                return True
            emitter.rewind(checkpoint)
        return False
    if isinstance(node, ir.Range):
        if emitter.position < len(emitter.text) and node.lo <= ord(emitter.text[emitter.position]) <= node.hi:
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
        # through a callee that does not name it — which is how the block header's indent detection still reads `n`.
        emitter.env = {**saved_env, **dict(zip(production.params, arguments))}

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

        committed = match(production.body, emitter, grammar, continue_out)
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
        start = emitter.checkpoint()
        if not match(node.base, emitter, grammar, _accept):  # the base's first way; a difference is a character class
            emitter.rewind(start)
            return False
        after_base = emitter.checkpoint()
        for excluded in node.minus:
            emitter.rewind(start)
            if _probe(excluded, emitter, grammar):
                emitter.rewind(start)
                return False
        emitter.rewind(after_base)
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
    if isinstance(node, ir.Rep):
        count = evaluate(node.count, emitter, grammar)

        def step(index):
            if index >= count:  # a non-positive count matches nothing, as a zero-length indent does
                return k()
            return match(node.item, emitter, grammar, lambda: step(index + 1))

        return step(0)
    if isinstance(node, ir.Case):
        value = emitter.env[node.var]
        for branch in node.branches:
            if branch.value == value:
                return match(branch.item, emitter, grammar, k)
        return False
    if isinstance(node, ir.SetVar):
        checkpoint = emitter.checkpoint()
        emitter.env[node.param] = evaluate(node.value, emitter, grammar)
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
        return k() if emitter.position - emitter.match_start <= evaluate(node.limit, emitter, grammar) else False
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
        return k() if emitter.position == len(emitter.text) else False
    if isinstance(node, ir.Look):
        return k() if _probe(node.item, emitter, grammar) else False
    if isinstance(node, ir.NegLook):
        return k() if not _probe(node.item, emitter, grammar) else False
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
            emitter.position = start
            reached = match(node.item, emitter, grammar, lambda: emitter.position == target)
            emitter.rewind(checkpoint)
            if reached:
                return k()
        return False
    if isinstance(node, ir.Token):
        entry = emitter.checkpoint()
        emitter.cut()
        emitter.codes.append(node.code)

        def close_token():
            middle = emitter.checkpoint()
            emitter.cut()  # end the token's run at its trailing edge
            code = emitter.codes.pop()  # the following characters carry the surrounding code again
            if k():
                return True
            emitter.codes.append(code)
            emitter.rewind(middle)  # reopen the run so the wrapped item can try its next way
            return False

        matched = match(node.item, emitter, grammar, close_token)
        if not matched:
            emitter.rewind(entry)  # undo the leading cut and the pushed code
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
    if isinstance(node, ir.Cut):
        if k():
            return True
        raise CommitFailure(node.message)  # committed: unwind past the backtracking, this is the error
    raise NotImplementedError(f"interpreter does not support {type(node).__name__}")


def run(grammar, production, data, parameters=None):
    """Run `production` on the UTF-8 `data`, returning the yeast tokens it emits, or None if it does not match.

    `parameters` binds the production's parameters from the fixture's filename — `n`/`m` are integers, `c`/`t` strings.
    """
    emitter = Emitter(data.decode("utf-8"))
    emitter.env = {name: int(value) if name in ("n", "m") else value for name, value in (parameters or {}).items()}
    try:
        matched = match(grammar[production].body, emitter, grammar, _accept)
    except CommitFailure as failure:
        emitter.codes[:] = ["unparsed"]  # a raise skips the token frames' cleanup; the rest of the input is unparsed
        emitter.error(MESSAGES[failure.code])
        _recover_unparsed(emitter)
        return emitter.tokens
    if not matched:
        return None
    emitter.cut()
    return emitter.tokens
