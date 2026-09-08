# SPDX-License-Identifier: MIT
"""
The typed IR for the YAML grammar.

`annotated2ir.py` reads the grammar into these nodes. `grammar2decoder.py` and the gate checks read them back out.

Over the operator vocabulary libyeast writes, this is a faithful mirror. The match is node for node. An operator maps to
a single node, and this module normalizes nothing. The round-trip through `ir2annotated.py` therefore stays exact, and a
gate holds it there.

The reader also takes the vendored grammar's forms, and there the mirror is not exact. `(ord)` lands on the same
`AtoiValue` as `(atoi)`. The pair agree at the digit `(ord)` applies to. `(<<<)` becomes whatever it wraps. That is a
repetition already possessive here. libyeast's grammar writes neither, and neither round-trips. Flattening and
simplification belong to whatever consumes the IR. `ir2spec.py` does its own, to compare against the official grammar.

A node is a dataclass. A grammar node inside a node is a field of its own, or an item of a tuple. A walker can therefore
recurse over the IR without knowing which node it holds. `BranchPart` exists for that reason. A `(case)` branch is a
node rather than a bare pair, and no reader has to special-case a branch.

A node also answers `references()`. Those are the productions the subtree names directly, and the answer does not follow
into their bodies. A class writes its own fields out. A field that can hold a production sits with the node. So do the
fields that are codes, and the fields that are messages or counts. The nodes themselves say what is reachable.
"""

import time
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, replace
from typing import ClassVar, Generic, Self, TypeVar, TypeVarTuple, cast

# The production the whole grammar hangs off. It is a YAML stream, and then the end of the input.
ROOT = "l-yeast-stream"

# The production that a failed cut hands the input to. It is entered by name rather than called. That makes the
# production a start state of its own beside the root's copies. The unwind lands on it. The parse continues past it
# where the resume policy says so.
RECOVER = "l-recover"

# The parameters `normalize.monomorphize` specializes away into a production's name. Such a parameter has finitely many
# values. A call passes it lexically. The value settles where a parse enters a production. They are the context `c` and
# the chomping `t`. They are also the block scalar's indentation mode `i`. The chomping and the mode are lexical once
# `lift-setters` has made them so, rather than a match's stashed state. The resume policy `r` joins them. `n`, `m` and
# `f` are integers and stay.
#
# A left-out finite parameter takes its default. An omitted resume policy is the policy that does not resume. `entry`
# fills that in, and the root and a fixture that names none reach the copy that fixes it.
FINITE_PARAMS = ("c", "t", "r", "i")
FINITE_DEFAULTS = {"r": "n"}  # the value a left-out parameter takes. A call gives `c`, `t` and `i`.

# The parameters that are a single value for the parse rather than a value per call. `read-global-f` and `read-global-m`
# take them off the declarations and the calls. The reads and the writes then reach the slot itself.
#
# A global is what does not nest. The construct that opens the auto-detected indent measures that indent, and a reader
# takes it while the construct runs. The block scalar's floor comes from the leading empty lines, and the first content
# line reads it. Either way it is a construct at a time.
#
# `clear-f` and `clear-m` say where the pair stop applying. The parse refuses a read past that region rather than
# answering from what the last write left. In alphabetical order.
GLOBAL_PARAMS = ("f", "m")


# The arguments a call hands a production. `entry` sorts them out and reads none.
_Passed = TypeVar("_Passed")


def specialized(base: str, bindings: Mapping[str, object]) -> str:
    """
    The name of the monomorphic copy of `base` that fixes `bindings`. That is its finite parameters, in `FINITE_PARAMS`
    order, and the name writes `_<parameter>_<value>` apiece.

    A parameter left unset is not in the name. Its value is `None`, as a context is where a parse establishes none. Nor
    is a parameter at its default in the name. So the copy that fixes only defaults keeps the base name and resolves
    like it. That keeps the root `l-yeast-stream`, and lets a fixture naming no resume policy find it.
    """
    return base + "".join(
        f"_{parameter}_{bindings[parameter]}"
        for parameter in FINITE_PARAMS
        if bindings.get(parameter) is not None and bindings.get(parameter) != FINITE_DEFAULTS.get(parameter)
    )


def entry(
    grammar: Mapping[str, "Prod"], name: str, parameters: Mapping[str, _Passed]
) -> tuple[str, dict[str, _Passed]]:
    """
    Resolve a call of `name` with `parameters` to the production `grammar` holds. That is the monomorphic copy where a
    copy is present, with the finite parameters the name fixes moved out of the arguments. Otherwise the answer is
    `name` itself, and the arguments stay whole. `entry` fills in a left-out finite default, and a fixture or the root
    then finds its copy.

    This drops the finite parameters the resolved production stops declaring. A monomorphic copy has shed them. The base
    name at its default is still the polymorphic production. That production declares the parameters and takes them.
    """
    given = {parameter: parameters[parameter] for parameter in FINITE_PARAMS if parameter in parameters}
    for bindings in (given, {**FINITE_DEFAULTS, **given}):
        resolved = specialized(name, bindings)
        if resolved in grammar:
            declared = set(grammar[resolved].params)
            return resolved, {n: v for n, v in parameters.items() if n not in FINITE_PARAMS or n in declared}
    return name, dict(parameters)


def is_one_char(node: "Node", grammar: Mapping[str, "Prod"], seen: frozenset[str] = frozenset()) -> bool:
    """
    Whether `node` matches a single character. That is a terminal char class.

    The answer tells a consume from a way, and the interpreter and the normalizer must agree on it. A run over a
    character class is a value the input decides, taken whole and judged whole. A run over anything else is a way the
    parse chooses.

    `_ALWAYS_ONE_CHAR` names what matches a single character outright. That is a `OneCharSet` or a `RangeSet`. The list
    also holds an `InvalidSet`, and the `CharSet` the canonical form writes its sets as.

    A `DiffSet`, an `AltTree` and a `RefCall` match a single character where what they hold does. A `DiffSet` does where
    its base does, and the exclusions only narrow the base. An `AltTree` does where its branches do, and the answer is a
    union of char sets. A lowered optional `x | <empty>` therefore matches more. A `RefCall` does where its production
    does.

    The table names any other kind as no such node, and a kind named in neither list raises. `_IS_ONE_CHAR` is a
    `Question`. It answers for what somebody wrote into the table, and the corpus reaches the answers. A silent `False`
    here would turn a consume into a way, and the run would gain an empty fallback nobody wrote.
    """
    return _IS_ONE_CHAR(node, grammar, seen)


# The type a question answers with. It is the type of a handler's result. Asking the question gives back the same type.
_Answer = TypeVar("_Answer")


class Question(Generic[_Answer]):
    """
    A total dispatch over node kinds. It says what to do per kind. A default plays no part.

    A caller asks a question about a node through this. The alternative is a chain of `isinstance` tests ending in a
    fallthrough. That answers permissively for whatever form its author did not think of, and reports that blindness as
    a fact about the grammar. These rules hold a question to what it claims.

    - **It raises on a kind the table does not name.** The message says which question and which kind. There is no
      default and no way to write a default. Answering for a kind nobody named is the failure this replaces.
    - **Something reaches a handler.** A kind whose handler nothing reaches is a guess about the grammar.
      `unexercised` reports it once the whole corpus has run, and not before. An input that reaches a kind exercises
      that kind. A partial run says nothing about a kind no input reached.

    Those rules pin the table to the kinds that occur. A missing kind raises, and the run reports a spare handler. A
    question then says nothing about kinds it cannot see. A kind added to the IR touches the questions that reach that
    kind, loudly, on the day they do.

    A pair of lists say what has not arrived, and the pair differ in whether an answer exists for it. Intents come
    first. A real property of the kinds separates an answer from another, and a question makes use of it. So a question
    names the family it means. That is a statement about the kinds themselves. It then says which part of that family
    has not turned up.

    - `untested` names a kind a family answers for, and nobody has tested that answer.
      The family answers a kind that arrives, and the family's word is a reasonable word to take. The run records the
      arrival, reports the kind and fails. Somebody then makes the decision rather than passing over it. Either the
      family's answer is right for that kind and the kind comes off the list, or the answer is wrong. Then the family
      was the wrong thing to say here, and a finer family that tells the pair apart is what this wants.
    - `unknown` names a kind nothing here answers for, whatever a family would have said.
      A kind that arrives raises where it appears, and no answer exists to take.

    Both are measurements rather than claims. They say what has happened rather than what cannot.

    - **The table names no kind twice.** Named groups overlap, and `CutAction` is a guard and a commit both. A chain
      of tests resolves that silently by its order. The author's intent goes unsaid. Here it is an error until
      somebody writes the answer down.

    Handlers take the node and whatever the caller threads through, and a caller calls a question the same way.
    `question(node, grammar, ways)` reaches `handler(node, grammar, ways)`. A handler that is not callable is the answer
    itself, `Empty: True` rather than a lambda ignoring what a call hands it. A call gets that value back. Keep it
    immutable.
    """

    asked: ClassVar[list["Question"]] = []  # the questions made, and the order is the order somebody made them in.

    def __init__(
        self,
        what: str,
        over: Mapping[type | tuple[type, ...], "_Answer | Callable[..., _Answer]"],
        untested: Iterable[type] = (),
        unknown: Iterable[type] = (),
    ) -> None:
        self.what = what
        self._by_kind: dict[type, "_Answer | Callable[..., _Answer]"] = {}
        for kinds, handler in over.items():
            for kind in kinds if isinstance(kinds, tuple) else (kinds,):
                if kind in self._by_kind:
                    raise TypeError(f"the question of {what} names {kind.__name__} twice")
                self._by_kind[kind] = handler
        self._untested = frozenset(untested)
        self._unknown = frozenset(unknown)
        both = sorted(kind.__name__ for kind in self._untested & self._unknown)
        if both:
            raise TypeError(f"the question of {what} calls {', '.join(both)} both untested and unknown")
        # An untested kind is a kind a family answers for. There has to be a family that names it. Without a family no
        # answer to call untested, and what is meant is that nothing is known of it.
        idle = sorted(kind.__name__ for kind in self._untested if kind not in self._by_kind)
        if idle:
            raise TypeError(
                f"the question of {what} calls {', '.join(idle)} untested. no group of that question names the kind."
            )
        for kind in self._unknown:
            self._by_kind.pop(kind, None)  # nothing is known of it, whatever a family would have said
        stray = sorted(kind.__name__ for kind in (*self._by_kind, *self._untested, *self._unknown) if kind not in KINDS)
        if stray:
            raise TypeError(f"the question of {what} names {', '.join(stray)}. those are no kinds of node.")
        self._used: set[type] = set()
        self._arrived: set[type] = set()  # the untested kinds that have turned up, each a decision now owed
        Question.asked.append(self)

    def __call__(self, node: object, *given: object) -> _Answer:
        """Ask the question of `node`. The question raises where its mapping leaves the node's kind out."""
        kind = type(node)
        if kind in self._unknown:
            raise TypeError(f"the question of {self.what} knows nothing of {kind.__name__}, and it has arrived")
        handler = self._by_kind.get(kind)
        if handler is None:
            raise TypeError(f"the question of {self.what} has no answer for {kind.__name__}")
        if kind in self._untested:
            self._arrived.add(kind)  # answered by its family, and that the family is right for it is what is owed
        else:
            self._used.add(kind)
        return handler(node, *given) if callable(handler) else handler

    def unused(self) -> list[str]:
        """The kinds this question claims to handle and no caller asked for. A kind here is a guess nothing bore out."""
        return sorted(kind.__name__ for kind in self._by_kind if kind not in self._used | self._untested)

    def arrived(self) -> list[str]:
        """
        The kinds this question calls untested that have since arrived. A kind here is a decision this question owes.
        """
        return sorted(kind.__name__ for kind in self._arrived)

    def used(self) -> list[str]:
        """The kinds this question has answered for."""
        return sorted(kind.__name__ for kind in self._used)

    def mark_reached(self, used: Container[str], arrived: Container[str]) -> None:
        """Mark the kinds another process reached. `__name__` gives the names."""
        self._used |= {kind for kind in KINDS if kind.__name__ in used}
        self._arrived |= {kind for kind in KINDS if kind.__name__ in arrived}


# A round count no fixpoint over a grammar should need. A round drops a call or moves a question, and there are finitely
# many of both. A loop still going here has stopped settling, and a run that says so beats a run that does not return.
#
# `deepest_rounds` says on a run how far out of reach this cap sits. The sweep's refinement of duplicate bodies comes
# nearest. The run reports that rather than this file writing it down. A copy of a measurement goes quietly stale.
ROUNDS = 100

# The most rounds a fixpoint has taken, and the name says which fixpoint. The cap above is a backstop picked to be far
# out of reach. This says how far, and the run reports it at the end. A number nobody measured can then give way to a
# number somebody did.
_DEEPEST: dict[str, int] = {}


_STARTED = time.time()  # the moment the run began. A progress line reports the elapsed time against it.


# The work this process is on, said in a line it prints. The process that shares the work out leaves it empty, and a
# process that took a share names the item. A check with many workers writes to the same stream, and the lines
# interleave.
_WORKING = ""


def working(what: str) -> None:
    """Say what this process is working on. A line the process prints then says which work it belongs to."""
    # There is one of these per process, which is the point of it.
    global _WORKING  # noqa: PLW0603  # pylint: disable=global-statement
    _WORKING = what


def say(message: str) -> None:
    """
    Say where a run has got to, stamped with the clock and with how long it has been going, and flushed.

    It is flushed. Wherever anyone is watching, stdout is a pipe under `tee` or under a log. Python buffers a pipe by
    the block. An unflushed line arrives once the run is over. There is nothing left to report by then.

    A line from a worker names the work rather than the worker. The process that wrote it answers nothing. The subject
    it was answering about is what makes the interleaved lines readable.
    """
    where = f" {_WORKING}" if _WORKING else ""
    print(f"[{time.strftime('%H:%M:%S')} {time.time() - _STARTED:6.1f}s{where}] {message}", flush=True)


def rounds(what: str) -> Iterator[int]:
    """
    The rounds of a fixpoint. This counts a round and says it. It raises where `what` has plainly stopped settling.
    """
    for at in range(ROUNDS):
        _DEEPEST[what] = max(_DEEPEST.get(what, 0), at + 1)
        # The first, and each tenth after it. A settling fixpoint says so once and a stuck fixpoint goes on saying it.
        if at % 10 == 0:
            say(f"        {what}: round {at + 1}")
        yield at
    raise AssertionError(f"`{what}` did not settle in {ROUNDS} rounds")


def deepest_rounds() -> dict[str, int]:
    """
    `{what: the most rounds it took}`. The answer means something once a whole run has finished, as `unexercised` does.
    """
    return dict(sorted(_DEEPEST.items(), key=lambda counted: -counted[1]))


def unexercised() -> dict[str, list[str]]:
    """
    `{what: [kind]}` per question holding a handler nothing reached. It is meaningful after the whole corpus has run,
    and not before. An input that reaches a kind exercises that kind. A partial run says nothing about a kind no input
    reached.
    """
    return {question.what: question.unused() for question in Question.asked if question.unused()}


def owed() -> dict[str, list[str]]:
    """
    `{what: [kind]}` per question whose untested part has arrived. A kind here is a decision that question owes. A run
    that reaches such a kind says so and fails. It does not pass on the family's word.
    """
    return {question.what: question.arrived() for question in Question.asked if question.arrived()}


# The record of what a process reached. `what_was_reached` gives the record and `also_reached` folds it back in. It is
# the kinds a question answered for, how deep a fixpoint went, and the untested kinds that arrived.
Reached = tuple[dict[str, set[str]], dict[str, int], dict[str, set[str]]]


def what_was_reached() -> Reached:
    """
    The record this process has reached. That is the kinds a question answered for, and how deep a fixpoint went.

    A check that shares its work out over the cores runs in forked children. A child marks what it reached in a copy of
    these. A kind the child reached counts as reached. The record comes back with the answers, and this folds it in.
    Without that, `unexercised` would report a handler only a worker reached.
    """
    return (
        {question.what: set(question.used()) for question in Question.asked},
        dict(_DEEPEST),
        {question.what: set(question.arrived()) for question in Question.asked},
    )


def also_reached(held: Reached) -> None:
    """Fold what another process reached into this process's record, as `what_was_reached` gave it."""
    used, deepest, arrived = held
    for question in Question.asked:
        question.mark_reached(used.get(question.what, ()), arrived.get(question.what, ()))
    for what, took in deepest.items():
        _DEEPEST[what] = max(_DEEPEST.get(what, 0), took)


def _refs(*values: object) -> list[str]:
    """
    The production names held anywhere in `values`. The `references` methods read this. A node contributes its own
    `references()`. A tuple contributes the names its items hold. Anything else contributes nothing. That covers a code
    and a message, and a codepoint and `None`.
    """
    names = []
    for value in values:
        if isinstance(value, tuple):
            names.extend(_refs(*value))
        elif isinstance(value, Node):
            names.extend(value.references())
    return names


# The values `_renamed` takes and hands back. A value comes back as what it went in as. A node renames to a node, and a
# tuple of nodes to a tuple of nodes.
_Values = TypeVarTuple("_Values")


def _renamed(names: Mapping[str, str], *values: *_Values) -> tuple[*_Values]:
    """
    `values` with the production names replaced by what `names` maps them to. This is the mirror of `_refs`, and the
    `renamed` methods read it. A node renames itself, and a tuple renames its items. Anything else comes back unchanged.

    The result is a new value, as the results here are new. The nodes are frozen, and an earlier stage of the pipeline
    still holds the old value.

    A class writes `renamed` beside `references`, and writes both of them itself. Neither derives from a list of fields.
    The fields mean different things. A call is not a continuation, and a protected match is not the handler that
    answers for it. A check holds the pair together rather than a shared declaration. A rename must change exactly the
    names `references` reports.
    """
    # Each value comes back as what it went in as. A walk over a tuple cannot say that. It is asserted here, rather than
    # at each `renamed` that unpacks the result.
    return cast(
        tuple[*_Values],
        tuple(
            (
                _renamed(names, *value)
                if isinstance(value, tuple)
                else value.renamed(names) if isinstance(value, Node) else value
            )
            for value in values
        ),
    )


@dataclass(frozen=True)
class Node:
    """
    The methods a node of the IR answers, and the leaf's answer to them.

    A leaf holds no production name of its own. It inherits both methods and writes neither. A node holding other nodes
    overrides them, and reads the fields through `_refs` and `_renamed`.

    A node says for itself which fields hold a production name. `ParamValue` holds a `name` and answers with nothing,
    and that name is a grammar parameter.
    """

    def references(self) -> list[str]:
        """The production names this node holds, in the order the node holds them. A repeat stays."""
        return []

    # The overrides read `names`. A leaf holds no production name.
    def renamed(self, names: Mapping[str, str]) -> Self:  # pylint: disable=unused-argument
        """A copy of this node holding the production names `names` gives."""
        return self


# --- value / parameter expressions.


@dataclass(frozen=True)
class ParamValue(Node):
    """
    A grammar parameter. `n` is the indentation and `c` the context. `m` is the indent indicator and `t` the chomping.
    """

    name: str


@dataclass(frozen=True)
class LitValue(Node):
    """
    A literal value. It is an int. It is also a string such as `"block-in"`. It is None where the grammar says `null`.
    """

    value: int | str | None


@dataclass(frozen=True)
class MatchValue(Node):
    """
    `(match)` is the characters of the token the parse is building. It is what a rule has just matched, read where the
    rule must act on that text. The characters are in hand already, and the parse remembers nothing about where they
    began.

    Under `(len)` a reader does not take it as text at all. The last consume's length is a slot of its own, and that is
    what `LenValue` takes. The meanings differ wherever a token cuts across a consume, or holds more than a single
    consume.
    """


@dataclass(frozen=True)
class GlobalValue(Node):
    """
    The value of the global `name`. A single value holds for the parse, rather than a value per call.

    A reader takes it off the slot rather than through a parameter. `SetVarAction` and `IncreaseAction` write it.
    `ClearVarAction` says where it stops applying. A production declares nothing here, and a call passes nothing down.
    """

    name: str


@dataclass(frozen=True)
class IndentValue(Node):
    """
    The indentation in force. The top of the stack holds it, and the characters here measure against that value.

    A reader takes it off the stack rather than through a parameter. `PushIndentAction` puts the value there and
    `PopIndentAction` takes the value back. A production declares nothing here, and a call passes nothing.
    """


@dataclass(frozen=True)
class AutoDetectIndentValue(Node):
    """
    `<auto-detect-indent>` is the indentation of the next line holding a character other than a space. That figure is
    less `n`. A reader takes it without consuming anything, and no bound applies.

    The node belongs to the official grammar, and a reader takes it from there. libyeast's grammar takes such an
    indentation where it appears and reads `ColumnValue`. This module evaluates no such node. The node stays for the
    vendored grammar `check_vendor_spec` compares against, and that grammar writes it. A single reader loads both.
    """


@dataclass(frozen=True)
class ColumnValue(Node):
    """
    `<column>` is the column the parse is at. The count starts at `0`.

    It is what a construct is indented by, read where the indentation has just been consumed. The parse takes it where
    the parse is, and looks ahead for nothing. The run of spaces a line begins with leaves the parse at the column that
    run measures.
    """


@dataclass(frozen=True)
class AddValue(Node):
    """`(+)` is integer addition of a pair of expressions."""

    a: Node
    b: Node

    def references(self) -> list[str]:
        return _refs(self.a, self.b)

    def renamed(self, names: Mapping[str, str]) -> Self:
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class SubValue(Node):
    """`(-)` is integer subtraction of a pair of expressions."""

    a: Node
    b: Node

    def references(self) -> list[str]:
        return _refs(self.a, self.b)

    def renamed(self, names: Mapping[str, str]) -> Self:
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class LenValue(Node):
    """
    `(len)` is the length of a match in characters.

    Of `(match)`, the argument the grammar gives it, that is how many characters the last consume took. A reader takes
    it off the slot the consume leaves. Measuring the text plays no part. A token is cut wherever an annotation opens or
    closes, and what a rule just matched is neither cut nor joined by that. Of anything else it is the length of
    whatever that evaluates to.
    """

    arg: Node

    def references(self) -> list[str]:
        return _refs(self.arg)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class AtoiValue(Node):
    """`(atoi)` is the integer the decimal digits of a matched string give."""

    arg: Node

    def references(self) -> list[str]:
        return _refs(self.arg)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class BranchPart(Node):
    """A branch of a `(case)` or a `(flip)`. It is what to use when the parameter has this value."""

    value: str
    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class FlipValue(Node):
    """`(flip)` is a pure value transformer over a parameter. `in-flow` maps a context to another context."""

    var: str
    branches: tuple[BranchPart, ...]  # a result expression apiece.

    def references(self) -> list[str]:
        return _refs(self.branches)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (branches,) = _renamed(names, self.branches)
        return replace(self, branches=branches)


# --- grammar nodes (matchers).


@dataclass(frozen=True)
class OneCharSet(Node):
    """A single literal codepoint."""

    cp: int


@dataclass(frozen=True)
class RangeSet(Node):
    """An inclusive codepoint range `[lo, hi]`."""

    lo: int
    hi: int


@dataclass(frozen=True)
class RefCall(Node):
    """A reference to another production. `args` holds the expressions it passes."""

    name: str
    args: tuple[Node, ...] = ()

    def references(self) -> list[str]:
        return [self.name, *_refs(self.args)]

    def renamed(self, names: Mapping[str, str]) -> Self:
        return replace(self, name=names.get(self.name, self.name), args=_renamed(names, *self.args))


@dataclass(frozen=True)
class EmptyTree(Node):
    """`<empty>`: the epsilon match."""


@dataclass(frozen=True)
class FailTree(Node):
    """
    `<fail>` is the match no input makes. It is the twin of `<empty>`. Any input makes `<empty>` and takes nothing.

    A `(case)` may decline a value of a finite parameter. `l-recover-entry` does under a policy that does not recover
    here, and `s-line-prefix` in a context no rule invokes it under. The branch says so with this rather than by being
    absent.

    A case names the values of its parameter, and `validate_grammar._check_total_cases` holds the case to that. A walk
    over such a case then reaches a node saying the decline, rather than an emptiness it has to read as refusal. The
    specialization has no value left to have no branch for.
    """


@dataclass(frozen=True)
class StartOfLineGuard(Node):
    """`<start-of-line>` is a zero-width assertion that the parser is at the start of a line."""


@dataclass(frozen=True)
class EndOfStreamGuard(Node):
    """`<end-of-stream>` is a zero-width assertion that the parser is at the end of the input."""


@dataclass(frozen=True)
class InvalidSet(Node):
    """
    `<invalid>` is a byte that begins no valid UTF-8 sequence. It belongs to no character set. It matches in no place
    the grammar names a character. The recovery rules reach for it, and a run of these takes `unparsed-invalid`.
    """


@dataclass(frozen=True)
class CharSet(Node):
    """
    A character out of `spans`. That is the set as the parser sees it, and the parser can ask about a character that
    way.

    `spans` is a tuple of inclusive `(low, high)` codepoint intervals, sorted and disjoint and merged. A pair of nodes
    denoting the same characters are therefore the same node, and the sweep writes them once. The invalid byte is the
    interval `(-1, -1)`, a unit no character can hold. That is how a set holding it beside real characters says so.

    A union and a subtraction come out where a pass makes this node. Neither rides along. `chars.Model` gives a
    character a key holding a bit per set the grammar tests. The parser runs a bit test and no more. Whatever
    combination of characters and ranges the grammar wrote, the question at run time is the same shape.
    """

    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SeqTree(Node):
    """`(all)`: an ordered concatenation."""

    items: tuple[Node, ...]

    def references(self) -> list[str]:
        return _refs(self.items)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (items,) = _renamed(names, self.items)
        return replace(self, items=items)


@dataclass(frozen=True)
class AltTree(Node):
    """`(any)`: an ordered alternation."""

    items: tuple[Node, ...]

    def references(self) -> list[str]:
        return _refs(self.items)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (items,) = _renamed(names, self.items)
        return replace(self, items=items)


@dataclass(frozen=True)
class StarTree(Node):
    """`(***)`: zero or more."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class PlusTree(Node):
    """`(+++)`: one or more."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class OptTree(Node):
    """`(???)`: optional. The item matches, or it stays away."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class RepTree(Node):
    """`({N})` is exactly `count` times. `count` is an expression, a literal or a parameter."""

    count: Node
    item: Node

    def references(self) -> list[str]:
        return _refs(self.count, self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        count, item = _renamed(names, self.count, self.item)
        return replace(self, count=count, item=item)


@dataclass(frozen=True)
class TrimStarTree(Node):
    """
    A maximal run of `full` that gives back its trailing run of `trim` characters. It is the normalized form of a
    `(trim* content)*`, where `full` is `trim | content` itself.

    The in-line run of a plain scalar is such a node. It keeps the inner spaces and leaves the trailing ones.
    `nb-ns-plain-in-line` is written `(s-white* ns-plain-char)*`. It becomes `TrimStarTree` over `s-white |
    ns-plain-char`, and the trim is `s-white` there. The quoted in-line runs go the same way, in single quotes and in
    double.

    It matches the empty string. A run of only `trim` characters consumes none of them.
    """

    full: Node
    trim: Node

    def references(self) -> list[str]:
        return _refs(self.full, self.trim)

    def renamed(self, names: Mapping[str, str]) -> Self:
        full, trim = _renamed(names, self.full, self.trim)
        return replace(self, full=full, trim=trim)


@dataclass(frozen=True)
class ConsumeSpanAction(Node):
    """
    A maximal run of `set` characters, and the parse takes it as a single consume. A `StarTree` or a `PlusTree` over a
    character class becomes this in the canonical form, and it maps to a single repeated-char-set match.

    It takes at least a character. A gate peeking what `set` denotes sits in front of it for both. The star adds a way
    beside it, a `NegLookGuard` on the same question and a `ConsumeNoCharAction`. Taking none is then a way of its own,
    rather than this action matching nothing.
    """

    set: Node

    def references(self) -> list[str]:
        return _refs(self.set)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (renamed_set,) = _renamed(names, self.set)
        return replace(self, set=renamed_set)


@dataclass(frozen=True)
class ConsumeNoCharAction(Node):
    """
    A run that took no character, and the IR says it as an action. The match is empty, and the length reads `0`.

    A run that must take a character is a gate peeking its set and a consume of that set. A run that may take none
    offers that pair as one way. Its other way refuses the set and performs this action.

    A counted run builds this action too. The parse may work the count out. A guard then asks whether the count is none
    or below, and the way behind that guard performs this action. The grammar may write the count as none. The run is
    then this action and no more. The grammar writes no such count.

    The way taking none performs this rather than performing nothing. A length read past a way that performed nothing
    would be whatever the last run left. That is a different run's answer.

    It names no set. The parse settles the set that took nothing before a reader gets here, and that set makes no
    difference to what this leaves behind. A pair of these are the same action wherever the pair coincide.
    """


@dataclass(frozen=True)
class ConsumeCharAction(Node):
    """
    The character the gate peeked. The run takes it. It consumes a single character. The gate has already found it
    there. A `ConsumeCharAction` that finds nothing is a gate that did not do its job, and the interpreter says so
    rather than matching nothing.

    `set` is the characters it consumes, kept although the gate asks the same question. A gate moves. A pass hoists a
    gate to a caller, splices a gate into another way, or splits a gate from what it protected. The set the consume took
    at authoring time does not move. That set rides here, and a reader takes it off the consume wherever the consume has
    got to. A way's consumption then does not come from whichever guard happens to sit in front of it.
    """

    set: Node

    def references(self) -> list[str]:
        return _refs(self.set)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (renamed_set,) = _renamed(names, self.set)
        return replace(self, set=renamed_set)


def _asked_in_order(guard: object) -> tuple[str, str]:
    """
    The order a gate's guards come in. It is the kind, then the question entire. Total, and stable across runs.
    """
    return type(guard).__name__, repr(guard)


@dataclass(frozen=True)
class GatePart(Node):
    """
    The questions a parse enters an alternative on. A test takes no character. They are the `guards`. A guard holds at
    the parse's position. They are a set rather than a sequence. A guard asks about the same position, and no order
    between them means anything. A gate holding none is the unconditional fallthrough. The last alternative is where
    that appears. The question about the character in front is a `LookGuard` over its class, and that guard sits among
    the other guards.

    A gate holds them as a tuple in a canonical order rather than as a set. A pair of gates asking the same questions
    are then the same gate. That is what lets the sweep merge the productions that hold them. The pipeline's output
    stays the same from run to run. A set would order its members by hash, and a guard reading a named parameter hashes
    through a string, whose hash a process picks afresh.
    """

    guards: tuple[Node, ...] = ()

    def __post_init__(self) -> None:
        """
        Sort the guards into the order `_asked_in_order` gives. A pair of gates asking the same thing then compare
        equal.
        """
        object.__setattr__(self, "guards", tuple(sorted(self.guards, key=_asked_in_order)))

    def references(self) -> list[str]:
        return _refs(self.guards)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (guards,) = _renamed(names, self.guards)
        return replace(self, guards=guards)


@dataclass(frozen=True)
class AlternativeState(Node):
    """
    A way a production may go. It is a `GatePart` to enter on, the `actions` performed, and up to a pair of productions
    the way hands control to. `first` is the call and `second` is where the path continues past it. That makes an edge a
    single push. `second` with no `first` is a tail call. `second` has nothing behind it, and a sequence's trailing
    actions therefore become a continuation of the sequence.

    `recover` rides whichever of the pair holds the call it protects. That is `first` where a call sits there, and
    `second` where the way is a tail call.

    A cut unwinding out of that call stops at the recovery production this names. The interpreter emits the error. It
    closes the markers the call opened down to that recovery. `recover` matches what this rule gives up, and the parse
    continues past the call as though it had matched. A recovery that does not match sends the cut on up.
    """

    gate: GatePart
    actions: tuple[Node, ...] = ()
    first: RefCall | None = None
    second: RefCall | None = None
    recover: Node | None = None

    def __post_init__(self) -> None:
        """
        Refuse a way the machine has no edge for. That is a call with nothing behind it. It is also a recovery riding no
        call.
        """
        # A call with nothing behind it is a push whose return goes nowhere, which no edge of the machine means. A tail
        # call sets `second` and no `first`.
        if self.first is not None and self.second is None:
            raise ValueError(
                f"a way calling {self.first} with nothing behind it - a tail call is `second` with no " f"`first`"
            )
        # A recovery answers for a cut unwinding out of the call it rides. A way with a recovery and making no call
        # rides nothing. It is refused where it is built, the question having no call to ask it of.
        if self.recover is not None and self.first is None and self.second is None:
            raise ValueError(f"a way with the recovery {self.recover} and making no call for it to ride")

    def references(self) -> list[str]:
        return _refs(self.gate, self.actions, self.first, self.second, self.recover)

    def renamed(self, names: Mapping[str, str]) -> Self:
        gate, actions, first, second, recover = _renamed(
            names, self.gate, self.actions, self.first, self.second, self.recover
        )
        return replace(self, gate=gate, actions=actions, first=first, second=second, recover=recover)


@dataclass(frozen=True)
class ChoiceState(Node):
    """
    A production's body as the state machine reads it. It is the `alternatives` in order, and the parse takes the first
    whose gate holds. It replaces `AltTree` where a pass has shaped a body. A canonical choice then reads apart from an
    unshaped body.
    """

    alternatives: tuple[AlternativeState, ...]

    def references(self) -> list[str]:
        return _refs(self.alternatives)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (alternatives,) = _renamed(names, self.alternatives)
        return replace(self, alternatives=alternatives)


@dataclass(frozen=True)
class ConsumeLimitedSpanAction(Node):
    """
    Up to `limit` characters of `set`. The parse takes them as a single consume, and the action reports the count. A run
    that fills the limit and a run that falls short both match. `DidMatchFullSpanGuard` tells them apart.

    It is an action that cannot fail. A gate cannot protect a counted consume. A gate tests the character in front, and
    not `limit` characters. An action asked for a count beyond reach is a way failing on what it performs. Here the
    taking matches. The question about it is a guard like any other, asked past the action in question.
    """

    set: Node
    limit: Node

    def references(self) -> list[str]:
        return _refs(self.set, self.limit)

    def renamed(self, names: Mapping[str, str]) -> Self:
        renamed_set, limit = _renamed(names, self.set, self.limit)
        return replace(self, set=renamed_set, limit=limit)


@dataclass(frozen=True)
class DidMatchFullSpanGuard(Node):
    """
    Whether the `ConsumeLimitedSpanAction` just performed took its whole limit.

    It asks about the action in front and no more. It runs where that action left the parse. Any other action takes the
    answer away. Asking with none there raises rather than answering.

    A guard that reads what an action did is how a taking that cannot fail still says no. The refusal moved out of the
    action and into a question about it.
    """


@dataclass(frozen=True)
class ConsumeTrimmedSpanAction(Node):
    """
    A maximal run of `full` characters that gives back its trailing run of `trim`. The parse takes it as a single
    consume. It is the trimming consume over a pair of sets that a plain or quoted scalar's line is to compile to. It
    takes at least a character, and the interpreter asserts that.

    The pipeline builds none. A `TrimStarTree` sits where this will, and the trim-reuse pass `PLAN.md` owes will make
    the exchange. This file writes the node, and the interpreter matches it. The pass then has a shape to land on rather
    than a vocabulary to invent alongside it. `check_dead_code` declares it kept though dead.
    """

    full: Node
    trim: Node

    def references(self) -> list[str]:
        return _refs(self.full, self.trim)

    def renamed(self, names: Mapping[str, str]) -> Self:
        full, trim = _renamed(names, self.full, self.trim)
        return replace(self, full=full, trim=trim)


@dataclass(frozen=True)
class LookGuard(Node):
    """`(===)` is positive lookahead, taking nothing."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class NegLookGuard(Node):
    """`(!==)` is negative lookahead, taking nothing."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class LookBehindGuard(Node):
    """`(<==)` is positive look-behind. The preceding input matched `item`."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class DiffSet(Node):
    """`(---)` is character-class subtraction. It is `base` less whatever `minus` holds."""

    base: Node
    minus: tuple[Node, ...]

    def references(self) -> list[str]:
        return _refs(self.base, self.minus)

    def renamed(self, names: Mapping[str, str]) -> Self:
        base, minus = _renamed(names, self.base, self.minus)
        return replace(self, base=base, minus=minus)


@dataclass(frozen=True)
class ExcludeAtAction(Node):
    """`(exclude)` is a zero-width negative guard. The current position is not at `item`."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class MaxWrapper(Node):
    """
    `(max)` is a bound of `limit` characters. It is the implicit-key lookahead limit the spec sets in section 7.4.2.

    The vendored grammar writes it before a production, as a length note on what follows. That form is `(max): N`.
    libyeast writes the bound around the production instead, as `(max): [N, message, rule]`. There it is the bounded
    window a parser resolves the key within. Consuming past `limit` characters is the error `message`, and unparsed from
    there.

    A key also sits on a line of its own, and that needs no help here. The flow-key context already forbids a break
    inside a key. `ir2spec` undoes the wrapping back to the vendored grammar's preceding `(max): N`.
    """

    limit: Node
    message: str | None = None
    item: Node | None = None

    def references(self) -> list[str]:
        return _refs(self.limit, self.item)  # `message` is a message key, not a production

    def renamed(self, names: Mapping[str, str]) -> Self:
        limit, item = _renamed(names, self.limit, self.item)
        return replace(self, limit=limit, item=item)


@dataclass(frozen=True)
class IsLessThanGuard(Node):
    """`(<)` asserts the first value is less than the second, whichever pair the parse works out."""

    a: Node
    b: Node

    def references(self) -> list[str]:
        return _refs(self.a, self.b)

    def renamed(self, names: Mapping[str, str]) -> Self:
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class IsLessEqualGuard(Node):
    """`(<=)` asserts the first value is no greater than the second, whichever pair the parse works out."""

    a: Node
    b: Node

    def references(self) -> list[str]:
        return _refs(self.a, self.b)

    def renamed(self, names: Mapping[str, str]) -> Self:
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class CaseTree(Node):
    """
    `(case)` dispatches on a parameter's value, with a grammar node per branch. `default` is the `else` for a value no
    branch names. It is `None` where no default exists, and then a value with no branch is a path that does not match.
    """

    var: str
    branches: tuple[BranchPart, ...]  # a grammar node apiece.
    default: Node | None = None

    def references(self) -> list[str]:
        return _refs(self.branches, self.default)

    def renamed(self, names: Mapping[str, str]) -> Self:
        branches, default = _renamed(names, self.branches, self.default)
        return replace(self, branches=branches, default=default)


@dataclass(frozen=True)
class BindTree(Node):
    """`(if)` with `(set)`. It matches `cond`, and binds parameter `param` to `value` as a side effect."""

    cond: Node
    param: str
    value: Node

    def references(self) -> list[str]:
        return _refs(self.cond, self.value)

    def renamed(self, names: Mapping[str, str]) -> Self:
        cond, value = _renamed(names, self.cond, self.value)
        return replace(self, cond=cond, value=value)


@dataclass(frozen=True)
class SetVarAction(Node):
    """A bare `(set)`. It binds parameter `param` to `value`. It matches nothing and takes nothing."""

    param: str
    value: Node

    def references(self) -> list[str]:
        return _refs(self.value)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (value,) = _renamed(names, self.value)
        return replace(self, value=value)


@dataclass(frozen=True)
class ClearVarAction(Node):
    """
    A zero-width action that says `param` stops applying here. Past this action no slot holds a value for `param`.
    Reading it is a fault rather than whatever the last write left behind.

    A construct that measures `param` finishes, and the path continues with no value of its own in force. The value has
    no further meaning there. A refusal is what says so. Leaving the value in place would let a later read take a
    measurement of something that has ended, and no gate would see that happen.
    """

    param: str


@dataclass(frozen=True)
class IncreaseAction(Node):
    """
    `(increase)` raises indentation parameter `param` to the current column. The write is `param = max(param, column)`.
    It is a zero-width action. It records the widest indentation the parse has reached. That is how a block scalar's
    leading empty lines set the floor its first content line may not fall below.
    """

    param: str


# --- token annotations.
#
# The parser accumulates the characters it consumes into a run, and gives the run a code. A run ends wherever a
# `TokenWrapper` scope begins or ends, and wherever an `EmitAction` marker falls. A run that ends becomes a token. So an
# annotation does not make *a* token. It says what code the characters consumed within take. It also says where the
# parser cuts the runs.
#
# A character consumed under no annotation gets the code `unparsed`. That is what the parser says about input it could
# not parse. On the success path it is a mistake. `validate_grammar.py` holds a character-consuming node to lying within
# a `TokenWrapper`.


@dataclass(frozen=True)
class TokenWrapper(Node):
    """
    `(token)` says the characters `item` consumes take `code`, but for those a nested annotation claims.

    The parse cuts the run at both edges. The characters before, within and after `item` fall into separate tokens.
    `item` may yield more than a single token where the item nests annotations of its own. It may yield none where the
    item consumes nothing.
    """

    code: str
    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class Wrapper(Node):
    """
    `(wrap)` is zero-width `begin` and `end` markers bracketing `item`. It is sugar for an `EmitAction` on either side
    of the item.

    This is a single node rather than a sequence written out. The markers are then paired by construction, and a `begin`
    cannot lose its `end`.
    """

    begin: str
    end: str
    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class EmitAction(Node):
    """`(emit)` is a zero-width token at this point. It also cuts the run of characters around that token."""

    code: str


# The pairs, and what says which close answers which open. A half a step writes names `pair`, the pairs it can belong
# to. A pair as written gets an entry, and both halves of that pair name the same.
#
# A close answers the open on the stack where the pair they share is there. The kind cannot say it. A pair of `(token)`s
# are the same kind and different pairs. A close that takes the wrong open of its own kind is invisible to anything
# asking what kind is there.
#
# The provisional run is the scope with no `pair` on either half. A single run is open at a time, and its close has
# nothing to tell apart.
#
# `pair` is a set of them rather than a lone entry. A merge makes halves indistinguishable. A pair of productions alike
# but for which pair they hold are the same production, and the half that survives holds both. So a close answers an
# open where the sets intersect. That is where a pair they both hold exists. The merge's cost lands exactly there, and
# precision falls where the grammar itself stopped telling the pair apart.


@dataclass(frozen=True)
class PushIndentAction(Node):
    """
    A zero-width action that pushes `level` onto the stack. The characters after it measure against that indentation.
    The push comes off where the call it belongs to is done. That is the production the call continues at.
    """

    level: Node
    pair: frozenset[int]

    def references(self) -> list[str]:
        return _refs(self.level)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (level,) = _renamed(names, self.level)
        return replace(self, level=level)


@dataclass(frozen=True)
class PopIndentAction(Node):
    """
    A zero-width action that takes the indentation in force off the stack. The level it displaced comes back. The pop
    leads a production of its own. It does not sit beside the call in question. An alternative runs nothing past the
    call it makes. The way continues at that production, and the pop is the first thing done there.

    `level` is what its `PushIndentAction` put there. Both halves hold that value where a pass mints the pair together,
    and it rides with the pop wherever the pop moves. It says nothing the stack does not already hold, and the pop
    restores from the stack instead. A pop takes off whatever is on top.

    It is there for a step moving the pop, or for a step moving something past the pop. Such a step can say which
    indentation the actions around it measure against, and needs no table pairing the ends. A check runs where the pop
    runs, and that makes the pairing a refusal rather than a claim. A pair of pops of the same level are the same action
    still, and what merges before this has a level merges after it.

    It is `None` where the pop names none. A push names the level it puts there.
    """

    level: Node | None
    pair: frozenset[int]

    def references(self) -> list[str]:
        return _refs(self.level)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (level,) = _renamed(names, self.level)
        return replace(self, level=level)


@dataclass(frozen=True)
class SetForbiddenAction(Node):
    """
    A zero-width action that sets what may not match at a start of line to `item`. It is `None` where the line start
    forbids nothing.

    It is what an `(exclude)` becomes, at either end of what the exclusion covers. The set is a single value for the
    parse rather than a value per call. So this writes a slot, and the write that ends a scope names what applies after
    it. It does not take back what the opening write displaced.

    The write takes nothing back. The exclusion opening inside another is the entry recovery's exclusion, and it sits
    inside a document's exclusion. It forbids what the outer exclusion does and a thing more, and the inner value
    already says the outer.
    """

    item: Node | None = None

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class PushCodeAction(Node):
    """
    A zero-width action that cuts the run and sets the code its following characters take to `code`. It is what a
    `(token)` opens with. It pushes the displaced code onto the stack. The matching `PopCodeAction` takes that code
    back.
    """

    code: str
    pair: frozenset[int]


@dataclass(frozen=True)
class PopCodeAction(Node):
    """
    A zero-width action that cuts the run and takes the code for its following characters off the top of the stack. That
    is what its `PushCodeAction` displaced. The pop and that push go together, and `Token(code, item)` lowers to
    `PushCode(code), item, PopCode`. A pop with nothing pushed raises. The pair says where a code begins and ends.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushMessageAction(Node):
    """
    A zero-width action that opens a committed region under `message`. It is what a `(commit)` opens with. From here to
    the `PopMessageAction` that closes it, the input must take the parse through. A failure that unwinds past this point
    with the region left open reports `message`. A failure that unwinds through a closed region backtracks like any
    other.

    A gate does not rise past such a push. Refusing entry to a region the grammar committed to must stay the error it
    names.
    """

    message: str
    pair: frozenset[int]


@dataclass(frozen=True)
class PopMessageAction(Node):
    """
    A zero-width action that closes the committed region the innermost `PushMessageAction` opened. Reaching it keeps the
    region's commitment, and a later failure then backtracks through the region softly.

    The pop and `PushMessageAction` go together, and `Commit(message, item)` lowers to `PushMessage(message), item,
    PopMessage`. A split may cut the pair across a minted helper. The pop pairs with its push on the stack of the parse,
    the way a `(token)`'s code does. The place the halves sit is nothing the split has to know.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushRecoveryAction(Node):
    """
    A zero-width action that says what answers for a failed `(cut)` from here on. `recovery` matches whatever of the
    input the parse gives up. `resume` is where it continues once that has matched.

    The parse holds the pair together. An unwind reads where to stop and where to go on from a single place. It works
    neither out from what the abandoned parse left behind. `resume` names the continuation of the way, where the path
    continues past the protected call. That is what makes continuing here the same as the call having matched.

    A recovery that says nothing recovers nothing. A rule reached under a resume policy that does not recover here has
    no branch to take. The cut goes on unwinding to whoever does answer for it.
    """

    recovery: Node
    resume: Node
    pair: frozenset[int]

    def references(self) -> list[str]:
        return _refs(self.recovery, self.resume)

    def renamed(self, names: Mapping[str, str]) -> Self:
        recovery, resume = _renamed(names, self.recovery, self.resume)
        return replace(self, recovery=recovery, resume=resume)


@dataclass(frozen=True)
class PopRecoveryAction(Node):
    """
    A zero-width action that takes back what the innermost `PushRecoveryAction` established. A cut past this point then
    unwinds to whatever answered before it.

    The pop and `PushRecoveryAction` go together. Like the other pairs the halves hold on the stack of the parse rather
    than where a step wrote them. A split that cuts the halves apart is nothing either half has to know.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class OpenWindowAction(Node):
    """
    A zero-width action that opens a `(max)` window `limit` characters wide. Past it, a committed consume fails the cut
    `message` names.

    Windows do not nest. The outermost is what applies. An inner window sits inside the budget the outer already bounds.
    An open under an outer window raises the count and does no more.
    """

    limit: Node
    message: str
    pair: frozenset[int]

    def references(self) -> list[str]:
        return _refs(self.limit)  # `message` is a message key, not a production

    def renamed(self, names: Mapping[str, str]) -> Self:
        (limit,) = _renamed(names, self.limit)
        return replace(self, limit=limit)


@dataclass(frozen=True)
class CloseWindowAction(Node):
    """
    A zero-width action that closes a `(max)` window. It goes with `OpenWindowAction`, and `Max(limit, message, item)`
    lowers to `OpenWindow(limit, message), item, CloseWindow`.

    The window it closes is the outermost open window. An inner window raises the count and does no more. The window is
    gone exactly where the open that set it closes.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class StartMustConsumeAction(Node):
    """
    A zero-width action that opens a region which must take a character. The `DidConsumeSinceOpenGuard` that asks about
    it may run with the parse at the position where this ran. The region has then not matched.

    It is what makes a loop end, and the grammar can see the action there. A turn taking no character is a turn that
    would repeat forever. So a run says of its turn that the turn takes a character. The machine reads a loop that must
    make progress. It compares no positions to find out. The action is harmless around a turn that takes a character
    anyway. A run says it of the turns alike rather than of the turns that need saying.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class DidConsumeSinceOpenGuard(Node):
    """
    Whether the region its own `StartMustConsumeAction` opened has taken a character. It refuses where the parse has not
    moved since that open. It reads and does no more. `EndMustConsumeAction` is what ends the region. A gate holding
    this asks a question and leaves the parse where it was, the way the other guards do.

    The guard and `StartMustConsumeAction` go together, on the stack of the parse as the other pairs do. The open it
    reads is the innermost open with the same `pair`, rather than the innermost open of any pair. Opens of other pairs
    may sit between the halves, and the parse walks past them.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class EndMustConsumeAction(Node):
    """
    A zero-width action that closes the region its own `StartMustConsumeAction` opened. It takes that open off the stack
    of the parse. It takes the open on *top*, and holds that open to naming this `pair`. A pop takes what its own push
    put there. A top naming another pair is a half moved across a pair it must not cross, rather than a reason to look
    further down. That is the discipline a pop here keeps. It tells this apart from `DidConsumeSinceOpenGuard`. That
    guard reads rather than pops, and walks past the opens of other pairs.

    It says nothing about whether the region matched. `DidConsumeSinceOpenGuard` asks that. A way with this already
    entered on the answer. Splitting the pair is what keeps a gate read-only. A guard that closed the region could sit
    in front of nothing that touches the stack. A second ask would find the region gone.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushBackTrackAction(Node):
    """
    A zero-width action that opens a region the parse gives back whole. From here to the `PopBackTrackAction` that
    closes it, the ways taken inside are the ways taken. A failure past the close gives the region up. It does not
    choose among those ways again.

    It is what makes a repetition possessive. The grammar can see the fact rather than leaving it to whoever runs the
    parse. A run takes its turns. A continuation that fails fails the run entire, and no shorter run remains to fall
    back to. A failure before the close backtracks like any other. The region settles what has closed.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PopBackTrackAction(Node):
    """
    A zero-width action that closes the region the innermost `PushBackTrackAction` opened. It settles the ways taken
    inside that region. The pop and `PushBackTrackAction` go together. Like the other pairs the halves hold on the stack
    of the parse rather than where a step wrote them. A split that cuts the halves apart is nothing either half has to
    know.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class OpenProvisionalAction(Node):
    """
    A zero-width action that opens the provisional run. The parse has still to decide the tokens it emits from here on.
    The queue builds those tokens and holds them. Nobody sees a token until a `CommitProvisionalAction` resolves the
    run. It matches `ys_queue_open_run` step for step. A single run is open at a time.
    """


@dataclass(frozen=True)
class MarkProvisionalAction(Node):
    """
    A zero-width action that marks the open run's current position. It cuts the run into the region before the mark and
    the region from the mark on. That is the side a later `RetypeProvisionalAction` or `InjectBeforeAction` names.

    A run holds a mark at a time. Taking the mark again moves it, and the last taken wins. That is how a rule reading
    line by line marks a fresh line. A mark is a parse position, rather than a property of any token.
    """


@dataclass(frozen=True)
class RetypeProvisionalAction(Node):
    """
    A zero-width action that rewrites the held tokens in `region` by kind. `region` is `all`, `before_mark` or
    `after_mark`. A token whose characters came in as a line break takes `breaks`, and any other takes `rest`. A kind
    whose code is `None` keeps its own.

    The run stays open. It matches the rewrite over `ys_queue_run` step for step. There is no discard. A failed
    hypothesis retypes rather than dropping tokens. The field is `breaks` rather than the `break` the runtime writes,
    and Python reserves that word. `rest` and `breaks` are token codes rather than production names.
    """

    rest: str | None
    breaks: str | None
    region: str


@dataclass(frozen=True)
class InjectBeforeAction(Node):
    """
    A zero-width action that puts the decided markers `codes`, in order, into the open run at `at`. That is the run's
    `start` or its `mark`.

    `start` sits ahead of the whole run. `mark` sits between the sides.

    `begin-document` and the node markers go ahead of a document's whites. `begin-pair` goes at the mark where a line
    turned out to be a key. `end-scalar` goes ahead of a block scalar's chomped empty lines. It matches
    `ys_queue_inject` step for step. `codes` are token codes rather than production names.
    """

    codes: tuple[str, ...]
    at: str


@dataclass(frozen=True)
class CommitProvisionalAction(Node):
    """
    A zero-width action that resolves the open run. The queue decides the tokens and hands them back on demand. It
    matches `ys_queue_resolve_run` step for step.

    It goes with `OpenProvisionalAction` dynamically, the way a committed region's push and pop go together. The run
    belongs to the queue rather than to any single call, and a split may cut the pair across productions.
    """


@dataclass(frozen=True)
class CutAction(Node):
    """
    `(cut)` is a zero-width commit past which the parse does not backtrack. On a later failure it is the error.

    `message` names the expectation to report. It is a key into `grammar/messages.yaml`.
    """

    message: str


@dataclass(frozen=True)
class CommitWrapper(Node):
    """
    `(commit)` matches `item`. It commits to `item` being present and no further. It is a `(cut)` scoped to what
    follows.

    A `(cut)` commits the whole parse from its point on. `(commit)` commits to `item` being able to match at all.
    `message` is the error where `item` cannot match, as a `(cut)`'s message names its error. A quoted scalar that stays
    open is such a case, and so is a flow collection that stays open.

    The match backtracks like any other where `item` matches and a *later* rule fails. The commitment does not reach
    past `item`. So a flow scalar that closed cleanly can take another meaning. A rule may have tried it as a mapping
    key and been wrong. A scalar that stayed open is still the error it should name. `message` keys
    `grammar/messages.yaml`, as a `(cut)`'s message does.

    An implicit key that will not parse is not this key. It is no error. The grammar wraps a commit reached in a key
    context in a `(case) c`. The key branches are the bare `item`, and the `else` is the commit. The softening is a
    switch on `c` rather than a decision the parser makes.
    """

    message: str
    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class ErrorAction(Node):
    """
    `(error)` is a zero-width error token at this point. Like an `EmitAction` it cuts the run of characters around that
    token.

    `message` names the expectation to report. It is a key into `grammar/messages.yaml`, as a `(cut)`'s message is a key
    there. Unlike a `(cut)` it is a match rather than a commit. It emits and succeeds. The grammar says what the parser
    does about the input from here, in the rule that holds it.
    """

    message: str


@dataclass(frozen=True)
class RecoverWrapper(Node):
    """
    `(recover)` is where a `(cut)` inside `item` stops unwinding, when `recovery` says it stops here.

    A cut unwinds past the calls between itself and whatever will answer for it. This is a rule saying "that is me". The
    interpreter emits the error. It closes the markers `item` opened down to this point and no further. `recovery`
    matches whatever of the input this rule is willing to give up. The parse then continues from here as though `item`
    had matched, and a repetition around the rule takes its next turn.

    `recovery` decides whether that happens at all. A rule reached under a resume policy that does not recover here has
    no branch to take. The rule does not match, and the cut goes on unwinding to whoever does answer for it. That is
    what keeps a policy recovering elsewhere from noticing this rule exists.
    """

    recovery: Node
    item: Node

    def references(self) -> list[str]:
        return _refs(self.recovery, self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        recovery, item = _renamed(names, self.recovery, self.item)
        return replace(self, recovery=recovery, item=item)


@dataclass(frozen=True)
class Prod(Node):
    """A named production. It holds a spec number and name, a parameter list, and a body node."""

    number: int
    name: str
    params: tuple[str, ...]
    body: Node

    def references(self) -> list[str]:
        return _refs(self.body)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (body,) = _renamed(names, self.body)
        return replace(self, body=body)


# The families of kinds. This file holds them together. A family's word then sits beside a neighbour's word. A family is
# a statement about the kinds themselves. It says what an action is, what takes no character, or what holds a match.
#
# Families separate an answer from another wherever a pass reads the grammar. A question names the family it means, and
# `untested` says which part of that family has yet to turn up.
#
# A pair of families with the same members are a family under a pair of names. A pair that differ by a kind are a
# question about that kind. Both sit here where a reader can see them. A group saying no more than "the kinds this
# question reaches" is no family, and stays where a caller reads it.

# The nodes holding characters a rule asks about rather than takes. A lookaround asks about its own position. The
# question is whether the set is in front, whether the set stays away, or whether it is behind. An exclusion asks at a
# take that follows, and says what may not match from here on.
#
# So a walk of what the grammar takes stops at such a node. A rule asks about the characters inside rather than taking
# them. Counting them would count characters the parse does not consume.
#
# Taking nothing is not what makes such a node. An action and a guard take nothing too. `<end-of-stream>` asks about the
# input and falls outside this family. It holds no characters to stop at. In alphabetical order.
ASKED_NOT_TAKEN_NODES = (ExcludeAtAction, LookBehindGuard, LookGuard, NegLookGuard)

# The kinds that take characters themselves rather than through whatever they hold. Such a kind takes at least a
# character wherever it matches. It is the counterpart of `CONSUMES_NOTHING`.
#
# A walk of a node's children must not descend into such a kind. Doing so counts the same characters twice, or counts a
# peek's set as a match. Said forwards, these are what move the parse on. A question asked in front of such a kind stops
# speaking for where the parse then arrives.
#
# A kind that reads on a way and not on another is neither this nor `CONSUMES_NOTHING`. A run, a repetition and a choice
# are such kinds, and a reader asks about their parts instead.
#
# The forms of taking are here, whichever phase writes them. A family narrowed to the forms a phase happens to use
# answers a question about the other phases by silently not counting them. In alphabetical order.
CONSUMING = (
    CharSet,
    ConsumeCharAction,
    ConsumeLimitedSpanAction,
    ConsumeSpanAction,
    ConsumeTrimmedSpanAction,
    DiffSet,
    InvalidSet,
    OneCharSet,
    RangeSet,
)

# The kinds that leave something behind and match wherever a way reaches them. Such a kind moves the state the parse
# holds rather than the position. In alphabetical order.
ACTIONS = (
    ClearVarAction,
    CloseWindowAction,
    CommitProvisionalAction,
    ConsumeNoCharAction,
    CutAction,
    EmitAction,
    EndMustConsumeAction,
    ErrorAction,
    ExcludeAtAction,
    IncreaseAction,
    InjectBeforeAction,
    MarkProvisionalAction,
    OpenProvisionalAction,
    OpenWindowAction,
    PopBackTrackAction,
    PopCodeAction,
    PopIndentAction,
    PopMessageAction,
    PopRecoveryAction,
    PushBackTrackAction,
    PushCodeAction,
    PushIndentAction,
    PushMessageAction,
    PushRecoveryAction,
    RetypeProvisionalAction,
    SetForbiddenAction,
    SetVarAction,
    StartMustConsumeAction,
)

# A question the parse answers where it is, and the answer takes no character. A guard asks what the input holds around
# the position, and how the count it holds compares. It leaves nothing behind and may decline. That is what tells it
# from an action.
#
# A `(cut)` is no guard. It takes nothing either. It commits the parse rather than asking anything, and belongs with the
# actions. In alphabetical order.
GUARDS = (
    DidMatchFullSpanGuard,
    DidConsumeSinceOpenGuard,
    EndOfStreamGuard,
    IsLessEqualGuard,
    IsLessThanGuard,
    LookBehindGuard,
    LookGuard,
    NegLookGuard,
    StartOfLineGuard,
)

# The kinds a way performs. They are what the machine does where a way runs, to the input or to the state a way holds.
# They may appear in a way's actions, as against the gate of that way. A gate asks and no more.
PERFORMED_NODES = (*ACTIONS, *CONSUMING)

# The steps the machine takes a step at a time in a way. That is the family above without the sets. A set sits *inside*
# a consume. The set is what the consume consumes rather than something performed beside it. A walk holding a way to
# what it does then counts the consume once, rather than once per character.
PERFORMED_STEPS = (
    *ACTIONS,
    ConsumeCharAction,
    ConsumeLimitedSpanAction,
    ConsumeSpanAction,
    ConsumeTrimmedSpanAction,
)

# The kinds that take characters and leave the length behind, in the forms the pipeline gives them. Those are the
# repetitions the grammar writes, and the consumes the lowering turns those into. They are also the consume of a single
# character that a set among a way's actions lowers to.
#
# `(match)` is what such a kind took. A rule asking gets the same answer at a stage.
#
# `ConsumeNoCharAction` falls outside them and writes the length itself. It takes nothing, and there is no length to
# measure. The `0` is the answer rather than what measuring came to. In alphabetical order.
LEAVES_A_LENGTH = (
    ConsumeCharAction,
    ConsumeLimitedSpanAction,
    ConsumeSpanAction,
    ConsumeTrimmedSpanAction,
    PlusTree,
    RepTree,
    StarTree,
    TrimStarTree,
)

# The kinds that take no character at all. An action leaves something behind. A guard asks a question. An empty match
# does neither. `<empty>` is both an action and a guard. It does nothing, and matches wherever a way arrives. A family
# that needs the kind names it. A match begins on whatever is behind such a node.
CONSUMES_NOTHING = (*ACTIONS, *GUARDS, EmptyTree)

# `EmptyTree` is here and in `TREES`. A question naming both families takes it out of the other, and a kind named twice
# is an error.

# A peek is a guard that holds its question about the input as an `item` and takes nothing. It may ask about what is in
# front or what is behind. `every-peek-is-a-character-set` is the rule over these. A guard that reads the input need not
# be a peek. `<end-of-stream>` asks whether a character is there at all, and holds no question.
PEEKS = (LookGuard, LookBehindGuard, NegLookGuard)

# The guards that read what is in front of the parse. The questions are whether a character is there at all, whether the
# character falls in a set, and whether it falls outside that set. A look behind falls outside them. So does where the
# parse is in the line, and so does how the indentation compares.
LOOKS_AHEAD = (EndOfStreamGuard, LookGuard, NegLookGuard)

# The kinds that match a single character, whatever character of their set turns up. A difference or an alternation can
# match a single character. So can a call or a switch. Such a kind does where what it holds does. That is a question
# about the grammar rather than about the kind. In alphabetical order.
_ALWAYS_ONE_CHAR = (CharSet, InvalidSet, OneCharSet, RangeSet)

# A match repeated. It is the same state entered again, as many times as the input allows or as a count fixes. A run
# said as the consume a parser makes falls outside them. The lowering turns such a repetition *into* the consume. In
# alphabetical order.
REPETITIONS = (PlusTree, RepTree, StarTree)

# A repetition the input ends rather than a count. A run takes turns until what it repeats declines. `REPETITIONS` takes
# in the counted repetition as well. The phases lower both, and a count is a run of a length already fixed.
RUNS = (PlusTree, StarTree)

# A node that holds its content rather than bracketing the content with a pair. It is what the wrappers phase takes
# apart. In alphabetical order.
WRAPPERS = (CommitWrapper, MaxWrapper, RecoverWrapper, TokenWrapper, Wrapper)

# The shapes of the tree the lowerings take apart. Those are a choice of ways and a run of a way. They are a repetition,
# an optional and a binding. They are a switch and an empty match.
#
# These are things the grammar writes, and not things the machine has a state for. The phases before the canonical form
# are what remove them. In alphabetical order.
TREES = (
    AltTree,
    BindTree,
    CaseTree,
    EmptyTree,
    FailTree,
    OptTree,
    PlusTree,
    RepTree,
    SeqTree,
    StarTree,
    TrimStarTree,
)

# The kinds the machine has a state for. Those are the choice of ways a production offers, and a way among them. They
# are what the trees above become. In alphabetical order.
STATES = (AlternativeState, ChoiceState)

# The kinds a node holds that are neither a match nor a value. The first is the arm of a switch, and it pairs a
# parameter's value with a match. The second is the gate a parse enters a way on, and it holds the guards the way asks.
# In alphabetical order.
PARTS = (BranchPart, GatePart)

# The kind that hands control to a production.
CALLS = (RefCall,)

# The kinds whose item a parse enters at the node's position. A run and a repetition take their first turn there. A
# scope and a commit take their content there. A lookaround tests at that position. In alphabetical order.
WALKED_UNCONSUMED = (
    CommitWrapper,
    ExcludeAtAction,
    LookBehindGuard,
    LookGuard,
    MaxWrapper,
    NegLookGuard,
    PlusTree,
    RecoverWrapper,
    RepTree,
    StarTree,
    TokenWrapper,
    Wrapper,
)

# The kinds that hold a match, and so cannot appear where an item does. A choice and a run become productions of their
# own. A recovery moves to the edge an alternative rides. A binding becomes an action. In alphabetical order.
HOLDS_A_MATCH = (AltTree, BindTree, RecoverWrapper, SeqTree)

# The shapes a production's body may be, with a state the machine has apiece. It is a choice of ways, or a run of a way.
# It is a way under a handler, or a way. A binding is not among them. A body that is a binding hides a write behind a
# match, and `no-bind-nodes` counts that wherever it appears. In alphabetical order.
BODY_KINDS = (AltTree, ChoiceState, RecoverWrapper, SeqTree)

# The shapes an item may take. An item is something the machine does where it appears. That is a call. It is also
# anything that takes no character. It is anything that takes characters.
#
# A guard's question is the guard's business and no item of the way. That is what lets a peek hold a set, and an
# exclusion a question a bounded run of steps answers. `every-peek-is-a-character-set` and `every-exclusion-is-bounded`
# answer for those.
#
# This says the families and the call, rather than a list of the forms a phase reaches. A taking left out of the list is
# a step the machine has, read as a shape it has no state for.
LEAF_ITEMS = (*CONSUMES_NOTHING, *CONSUMING, RefCall)

# The kinds a production may hold instead of a matcher. Such a kind is a value the parse works out. That is an
# indentation or a measured length, a parameter or a switch over a parameter. It matches nothing. It takes no character
# and reads no input. In alphabetical order.
VALUE_KINDS = (
    AddValue,
    AtoiValue,
    AutoDetectIndentValue,
    ColumnValue,
    FlipValue,
    GlobalValue,
    IndentValue,
    LenValue,
    LitValue,
    MatchValue,
    ParamValue,
    SubValue,
)

# The values with nothing inside them. A walk of the grammar can then tell a value it must leave alone from a value
# holding more of the value language. In alphabetical order.
_VALUE_LEAVES = (LitValue, ParamValue)

# The kinds that do not match a single character. A repetition or a sequence takes a run rather than a character. A
# lookaround and a marker take none, and so do an action and a guard. A value expression is no match at all. The
# consumes the canonical form writes state the count in the name.
#
# With the kinds that can be a character it comes to `KINDS`. That is what it is here for. In alphabetical order.
_NOT_ONE_CHAR = (
    AddValue,
    AlternativeState,
    AtoiValue,
    AutoDetectIndentValue,
    BindTree,
    BranchPart,
    ChoiceState,
    ClearVarAction,
    CloseWindowAction,
    ColumnValue,
    CommitProvisionalAction,
    CommitWrapper,
    ConsumeCharAction,
    ConsumeLimitedSpanAction,
    ConsumeNoCharAction,
    ConsumeSpanAction,
    ConsumeTrimmedSpanAction,
    CutAction,
    DidMatchFullSpanGuard,
    DidConsumeSinceOpenGuard,
    EmitAction,
    EmptyTree,
    EndMustConsumeAction,
    EndOfStreamGuard,
    ErrorAction,
    ExcludeAtAction,
    FailTree,
    FlipValue,
    GatePart,
    GlobalValue,
    IncreaseAction,
    IndentValue,
    InjectBeforeAction,
    IsLessEqualGuard,
    IsLessThanGuard,
    LenValue,
    LitValue,
    LookBehindGuard,
    LookGuard,
    MarkProvisionalAction,
    MatchValue,
    MaxWrapper,
    NegLookGuard,
    OpenProvisionalAction,
    OpenWindowAction,
    OptTree,
    ParamValue,
    PlusTree,
    PopBackTrackAction,
    PopCodeAction,
    PopIndentAction,
    PopMessageAction,
    PopRecoveryAction,
    PushBackTrackAction,
    PushCodeAction,
    PushIndentAction,
    PushMessageAction,
    PushRecoveryAction,
    RecoverWrapper,
    RepTree,
    RetypeProvisionalAction,
    SeqTree,
    SetForbiddenAction,
    SetVarAction,
    StarTree,
    StartMustConsumeAction,
    StartOfLineGuard,
    SubValue,
    TokenWrapper,
    TrimStarTree,
    Wrapper,
)

# The kinds of node the IR writes. That is the kinds of thing that appear inside a body. `_NOT_ONE_CHAR` names a kind
# that cannot be a character. The tuple below names a kind that can be a character. The pair together cover the kinds.
#
# A kind added to neither is a kind `Question` refuses to hear about. A table naming it would be naming what is no kind
# of node. A net reading this has to tell "a kind I know, and this is not it" from "a kind nobody has named".
#
# A `Prod` is no kind of node. It is a name, a parameter list and a body. It is the thing a body hangs off rather than
# anything inside a body. A walk of a body reaches no `Prod`, and no question asks what it means.
KINDS = _NOT_ONE_CHAR + (AltTree, CaseTree, OneCharSet, CharSet, DiffSet, InvalidSet, RangeSet, RefCall)


def _refuse_a_family_said_twice() -> None:
    """
    Refuse a pair of families of kinds that name the same members. That is a family under a pair of names.

    A family is a statement about the kinds themselves. The members tell a pair apart, rather than the prose above them.

    A pair of comments may ask different questions. The first asks what takes a character wherever it matches. The
    second asks what takes a character itself rather than through what it holds. The pair are a family the moment they
    come to the same members. A reader answering the first through the second is then right by accident. This is what
    sees that.
    """
    said: dict[frozenset[type], str] = {}
    for name, family in globals().items():
        if name.isupper() and isinstance(family, tuple) and family and all(isinstance(one, type) for one in family):
            members = frozenset(family)
            if members in said:
                raise TypeError(f"`{said[members]}` and `{name}` name the same kinds. that is one family twice over.")
            said[members] = name


# The table `is_one_char` answers from, as a question. It answers for the kinds somebody wrote in and no more. A kind
# absent from here is a kind nobody has asked this of. Such a kind arriving raises rather than taking an answer. That is
# what a question buys, and why the table is smaller than `KINDS`.
_IS_ONE_CHAR: Question[bool] = Question(
    "whether a node matches a single character",
    {
        _ALWAYS_ONE_CHAR: True,
        DiffSet: lambda node, grammar, seen: is_one_char(node.base, grammar, seen),
        # An empty alternation matches nothing at all. It is no character either.
        AltTree: lambda node, grammar, seen: bool(node.items)
        and all(is_one_char(item, grammar, seen) for item in node.items),
        RefCall: lambda node, grammar, seen: node.name in seen
        or is_one_char(grammar[node.name].body, grammar, seen | {node.name}),
        # The kinds that reach here and are not a single character. This list names them rather than borrowing a family,
        # and no wider group fits.
        (
            AddValue,
            AlternativeState,
            AtoiValue,
            BindTree,
            ChoiceState,
            ClearVarAction,
            CloseWindowAction,
            ColumnValue,
            IsLessEqualGuard,
            IsLessThanGuard,
            CommitWrapper,
            ConsumeCharAction,
            ConsumeNoCharAction,
            ConsumeLimitedSpanAction,
            ConsumeSpanAction,
            CutAction,
            DidMatchFullSpanGuard,
            DidConsumeSinceOpenGuard,
            EmitAction,
            EmptyTree,
            EndMustConsumeAction,
            EndOfStreamGuard,
            ErrorAction,
            ExcludeAtAction,
            FailTree,
            GatePart,
            GlobalValue,
            IncreaseAction,
            IndentValue,
            LenValue,
            MatchValue,
            MaxWrapper,
            OpenWindowAction,
            OptTree,
            PlusTree,
            PopBackTrackAction,
            PopCodeAction,
            PopIndentAction,
            PopMessageAction,
            PopRecoveryAction,
            PushBackTrackAction,
            PushCodeAction,
            PushIndentAction,
            PushMessageAction,
            PushRecoveryAction,
            RecoverWrapper,
            RepTree,
            SeqTree,
            SetForbiddenAction,
            SetVarAction,
            StarTree,
            StartMustConsumeAction,
            StartOfLineGuard,
            SubValue,
            TokenWrapper,
            Wrapper,
        ): False,
    },
)


def repeated(node: "Node") -> "Node | None":
    """
    The match `node` takes again and again, and `None` where it takes a turn at most.

    A run said as the consume a parser makes repeats nothing here. The consume is what a repetition of a class is *for*.
    Counting the consume as a repetition would make the lowered shape the fault the lowering fixed.

    The table names the remaining kinds, and a kind named in neither list raises. A default meaning of "this does not
    repeat" plays no part. A repetition this had not heard of would go unseen. That is what `is_one_char` answering
    `False` by default did.
    """
    if isinstance(node, TrimStarTree):
        return node.full
    if isinstance(node, REPETITIONS):
        return node.item
    if isinstance(node, KINDS):
        return None  # a consume is among these. a run already said as the consume it is repeats nothing further
    raise TypeError(f"cannot tell whether {type(node).__name__} repeats anything")


# The shape a `kept_for` store takes. The key is the subject's id and whatever else the answer turns on. The value is
# the subject beside its answer. A store whose answer has a short shape writes that shape instead of taking this.
Kept = dict[tuple[int, object], tuple[object, _Answer]]


def kept_for(store: Kept[_Answer], subject: object, work: Callable[[], _Answer], under: object = ()) -> _Answer:
    """
    `work()` for `subject`, worked out once and kept in `store` against that object's identity and `under`.

    It keys on identity rather than equality. These answer about a grammar or a node, and both compare equal to others
    that are not them. A pair of stages hold the same frozen action under references denoting different sets. Answering
    the second from the first would be answering about the wrong grammar. A value hash costs more here than it saves. A
    grammar is a dictionary of whole production trees.

    The store keeps `subject` beside the answer, and checks it. An `id` is unique among the objects alive at once and no
    further. A grammar that has been let go frees its id for the next grammar. A store holding only the answer would
    hand the next grammar its predecessor's answer. Holding the subject keeps that grammar alive, and the id goes on
    meaning it.

    `under` is whatever else the answer depends on, hashable and compared by value. An asker whose answer turns on an
    action as well as a grammar names the action there. The asker builds no key of its own.

    This file writes it once. The askers had it written out across a pair of modules. That was a dance of a pair of
    lines apiece, and a comment said it kept the same discipline as the last.
    """
    known = store.get((id(subject), under))
    if known is None or known[0] is not subject:
        store[id(subject), under] = known = (subject, work())
    return known[1]


def rebuilt(node: "Node", visit: Callable[["Node"], "Node"]) -> "Node":
    """
    `node` with the grammar nodes it holds replaced by `visit` of those nodes.

    It is the generic walker the module's shape exists for. It reaches any node the same way. A node is a field of its
    own, or an item of a tuple. A caller recurses without special-casing any node. A value a node holds rather than
    grammar comes back unchanged.
    """
    changed: dict[str, object] = {}
    for field in fields(node):
        value = getattr(node, field.name)
        if isinstance(value, Node) and not isinstance(value, _VALUE_LEAVES):
            changed[field.name] = visit(value)
        elif isinstance(value, tuple) and value and all(isinstance(item, Node) for item in value):
            changed[field.name] = tuple(visit(item) for item in value)
    return replace(node, **changed) if changed else node


# Last, with the families behind it. Run partway down, this would answer for the families above and read exactly the
# same as a run that answered for the whole module.
_refuse_a_family_said_twice()
