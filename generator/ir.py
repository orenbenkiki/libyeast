# SPDX-License-Identifier: MIT
"""
The typed IR for the YAML grammar.

`annotated2ir.py` reads the grammar into these nodes. `grammar2decoder.py` reads them back out.

Over the operator vocabulary libyeast writes, the IR is a faithful mirror. An operator maps to a single node. A
`GatePart` sorts its guards, and the reader builds no `GatePart`. The round-trip through `ir2annotated.py` therefore
stays exact, and `generator/check_annotated_roundtrip.py` holds it there.

The reader also takes the vendored grammar's forms, and there the mirror is not exact. `(ord)` lands on the same
`AtoiValue` as `(atoi)`. `(<<<)` becomes the node it wraps. Neither form round-trips. `ir2spec.py` flattens and
simplifies the IR it reads.

A node is a dataclass. A grammar node inside a node is a field of its own, or an item of a tuple. A walker can therefore
recurse over the IR without knowing which node it holds. A `(case)` branch is a `BranchPart` node rather than a bare
pair.

A node also answers `references()` with the productions its subtree names directly. The answer does not follow into the
bodies of those productions. A class lists its own fields that can hold a production. It leaves out the fields that are
codes or message keys.

A question about a node is a `Question`. A `Question` raises on a kind its table does not name. This mechanises
`a-kind-dispatch-raises`. `check_normalize` reports a handler that a corpus run left unreached.
"""

import time
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, replace
from typing import ClassVar, Generic, Self, TypeVar, TypeVarTuple, cast

# The production the whole grammar hangs off. `l-yeast-stream` is a YAML stream, and then either the end of the input or
# a `TRAILING_CONTENT` error that `l-recover` follows.
ROOT = "l-yeast-stream"

# A failed cut hands the input to this production. The parser enters the production by name rather than by a call.
# RECOVER is a start state of its own, like a copy of the root. An unwind lands on RECOVER. A resume policy decides
# whether the parse goes on past RECOVER.
RECOVER = "l-recover"

# The parameters `normalize._monomorphize` specializes away into a production's name. Such a parameter has finitely many
# values. A call passes such a parameter lexically. The value settles where a parse enters a production. These
# parameters are the context `c` and the chomping `t`. They are also the block scalar's indentation mode `i`. Once
# `lift-setters` has run, the chomping and the mode are lexical rather than a match's stashed state. The resume policy
# `r` joins them. `n`, `m` and `f` stay out of the set.
#
# A left-out resume policy takes the default `n` that `FINITE_DEFAULTS` gives. Under `n`, `l-unparsed` runs to the end
# of the input. `entry` fills the default in. The root and a fixture naming no resume policy then reach the copy fixing
# `n`.
FINITE_PARAMS = ("c", "t", "r", "i")
FINITE_DEFAULTS = {"r": "n"}  # The defaults `entry` gives a left-out parameter.

# The parameters that are a single value for the parse rather than a value per call. `read-global-f` and `read-global-m`
# take them off the declarations and the calls. `interpreter._try_write` sets the slot and pushes the value onto the
# global's stack. `interpreter._evaluated_global` returns the top of the stack.
#
# A global holds a value that does not nest. `c-indentation-indicator` writes `m`. `l-strip-empty` and `l-keep-empty`
# write `f`. `l-nb-literal-first` and `l-nb-same-first` write `f` as well.
#
# `clear-f` and `clear-m` say where the globals `f` and `m` stop applying. `interpreter._try_clear` pops the global's
# stack and empties the slot. A read past that region with an outer value on the stack takes that value, and
# `check_normalize._check` refuses such a read. The list runs in alphabetical order.
GLOBAL_PARAMS = ("f", "m")


# The arguments a call hands a production. `entry` sorts them out by name. It reads the values of the `FINITE_PARAMS`
# arguments to name the monomorphic copy.
_Passed = TypeVar("_Passed")


def specialized(base: str, bindings: Mapping[str, object]) -> str:
    """
    Answer the name of the monomorphic copy of `base` that fixes `bindings`. `bindings` holds finite parameters. The
    name adds a `_<parameter>_<value>` suffix for a parameter it fixes. The suffixes follow `FINITE_PARAMS` order.

    The name leaves out a parameter set to `None` or to its default. A copy fixing only defaults keeps the base name and
    resolves like the base. The root `ROOT` keeps the name `l-yeast-stream` that way. A fixture naming no resume policy
    then finds the root.
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
    copy is present, and `entry` then moves the finite parameters the name fixes out of the arguments. Otherwise the
    answer is `name` itself, and the arguments stay whole. `entry` also tries the finite defaults filled in.
    `specialized` skips a default. This second try yields the first name again.

    `entry` drops the finite parameters the resolved production stops declaring. A monomorphic copy has shed them. In a
    grammar `_monomorphize` has not run on, the base name is the polymorphic production. That production declares the
    parameters and takes them.
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
    its base does, and the exclusions only narrow the base. An empty `AltTree` matches no character. A non-empty
    `AltTree` matches a single character where its branches do. Its set is then a union of char sets. A lowered optional
    `x | <empty>` answers `False`. A `RefCall` does where the body of its production does. A `RefCall` to a production
    already on the path through `seen` counts as a single character.

    The table names the other kinds it holds as no such node, and a kind the table does not name raises. `_IS_ONE_CHAR`
    is a `Question`. A silent `False` here would turn a consume into a way, and the run would gain an empty fallback
    nobody wrote.
    """
    return _IS_ONE_CHAR(node, grammar, seen)


# The type a question answers with. It is the type of a handler's result. Asking the question gives back the same type.
_Answer = TypeVar("_Answer")


class Question(Generic[_Answer]):
    """
    A question is a total dispatch over node kinds. It says what to do per kind. A default plays no part.

    A caller asks a question about a node through this. The alternative is a chain of `isinstance` tests ending in a
    fallthrough. Such a fallthrough answers permissively for a form its author did not think of. The chain then reports
    its author's oversight as a fact about the grammar. These rules hold a question to its own claim.

    - **It raises on a kind the table does not name.** The message says which question and which kind. There is no
      default and no way to write a default.
    - **`unexercised` reports a handler no input reaches.** A kind whose handler nothing reaches is a guess about the
      grammar. `_check` in `check_normalize.py` calls `unexercised` once the whole corpus has run. An input that reaches
      a kind exercises that kind. A partial run says nothing about a kind no input reached.

    Those rules pin the table to the kinds that occur. A missing kind raises, and the run reports a spare handler. A
    question then says nothing about kinds it cannot see.

    A pair of lists name kinds that have not arrived. The lists differ in whether a family answers such a kind. A
    question names the family of kinds it means, and a real property of the kinds decides that family. The question then
    says which part of that family has not turned up.

    - `untested` names a kind a family covers, and nobody has tested that answer. The family answers a kind that
      arrives. The run records the arrival, reports the kind and fails. Either the family's answer is right for that
      kind and the kind comes off the list, or the answer is wrong. Then the question wants a finer family that sets
      that kind apart from the family's other kinds.
    - `unknown` names a kind nothing here covers, and a family says nothing for it either. A kind that arrives raises
      where it appears, and the question gives nothing back.

    Both lists say what has happened rather than what cannot happen.

    - **The table names no kind twice.** Named groups overlap. `CONSUMES_NOTHING` holds the members of `ACTIONS`. A
      chain of tests resolves that silently by its order. The author's intent goes unsaid. A question raises on such an
      overlap until somebody writes the answer down.

    Handlers take the node and the values the caller threads through, and a caller calls a question the same way.
    `question(node, grammar, ways)` reaches `handler(node, grammar, ways)`. A handler that is not callable is the answer
    itself. A call gets that value back. Keep it immutable.
    """

    asked: ClassVar[list["Question"]] = []  # `asked` holds the questions in the order somebody made them.

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
        # The question refuses an untested kind no family names.
        idle = sorted(kind.__name__ for kind in self._untested if kind not in self._by_kind)
        if idle:
            raise TypeError(
                f"the question of {what} calls {', '.join(idle)} untested. no group of that question names the kind."
            )
        # The question drops an unknown kind from the families.
        for kind in self._unknown:
            self._by_kind.pop(kind, None)
        stray = sorted(kind.__name__ for kind in (*self._by_kind, *self._untested, *self._unknown) if kind not in KINDS)
        if stray:
            raise TypeError(f"the question of {what} names {', '.join(stray)}. those are no kinds of node.")
        self._used: set[type] = set()
        self._arrived: set[type] = set()  # `_arrived` holds the untested kinds the question has seen.
        Question.asked.append(self)

    def __call__(self, node: object, *given: object) -> _Answer:
        """Ask the question of `node`. The question raises where its mapping leaves the node's kind out."""
        kind = type(node)
        if kind in self._unknown:
            raise TypeError(f"the question of {self.what} knows nothing of {kind.__name__}, and it has arrived")
        if kind not in self._by_kind:
            raise TypeError(f"the question of {self.what} names no handler for {kind.__name__}")
        handler = self._by_kind[kind]
        if kind in self._untested:
            self._arrived.add(kind)  # A family answered the kind. Nobody has tested that answer.
        else:
            self._used.add(kind)
        return handler(node, *given) if callable(handler) else handler

    def unused(self) -> list[str]:
        """
        The handled kinds no caller asked for. The list leaves out the untested kinds. A kind here is a guess nothing
        bore out.
        """
        return sorted(kind.__name__ for kind in self._by_kind if kind not in self._used | self._untested)

    def arrived(self) -> list[str]:
        """
        The kinds this `Question` calls untested that have since arrived. A kind here is a decision this `Question`
        owes.
        """
        return sorted(kind.__name__ for kind in self._arrived)

    def used(self) -> list[str]:
        """
        Names of the kinds a call has answered. An untested kind stays out. `Question` records an untested kind among
        the arrived kinds instead.
        """
        return sorted(kind.__name__ for kind in self._used)

    def mark_reached(self, used: Container[str], arrived: Container[str]) -> None:
        """Mark the kinds another process reached. `__name__` gives the names."""
        self._used |= {kind for kind in KINDS if kind.__name__ in used}
        self._arrived |= {kind for kind in KINDS if kind.__name__ in arrived}


# `rounds` raises past this many rounds. `fixed_point` raises when it answers a single key more than this many times. A
# fixpoint ends once nothing changes. A loop still going here has stopped settling.
#
# `deepest_rounds` says on a run how far out of reach this cap sits.
ROUNDS = 100

# The most rounds a fixpoint has taken, and the name says which fixpoint. The cap `ROUNDS` is a backstop far out of
# reach. This binding says how far, and the run reports the depth at the end.
_DEEPEST: dict[str, int] = {}


_STARTED = time.time()  # the moment the run began. A progress line reports the elapsed time against it.


# The work this process is on. A line the process prints names that work. The process that shares the work out leaves it
# empty, and a process that took a share names the item. The workers of a check write to the same stream, and their
# lines interleave.
_WORKING = ""


def working(what: str) -> None:
    """Say what this process is working on. A line `say` prints then names that work."""
    global _WORKING  # noqa: PLW0603  # pylint: disable=global-statement
    _WORKING = what


def say(message: str) -> None:
    """
    Say where a run has got to. The line states the clock and how long the run has gone, and `say` flushes stdout after
    it.

    Python buffers a stdout that is no terminal by the block, as https://docs.python.org/3/library/sys.html#sys.stdout
    says. An unflushed line arrives once the run is over.

    A line from a worker names the work rather than the worker.
    """
    where = f" {_WORKING}" if _WORKING else ""
    print(f"[{time.strftime('%H:%M:%S')} {time.time() - _STARTED:6.1f}s{where}] {message}", flush=True)


def rounds(what: str) -> Iterator[int]:
    """
    Count a round of a fixpoint. Raise where `what` has stopped settling.
    """
    for at in range(ROUNDS):
        _DEEPEST[what] = max(_DEEPEST.get(what, 0), at + 1)
        # A fixpoint says its first round. A stuck fixpoint goes on saying its current round at a steady interval.
        if at % 10 == 0:
            say(f"        {what}: round {at + 1}")
        yield at
    raise AssertionError(f"`{what}` did not settle in {ROUNDS} rounds")


# The type of a key in the map `fixed_point` works out.
_Key = TypeVar("_Key")

# The type of the value `fixed_point` gives a key.
_Value = TypeVar("_Value")


def fixed_point(
    what: str,
    start: Mapping[_Key, _Value],
    answer: Callable[[_Key, Mapping[_Key, _Value]], _Value],
    dependents: Mapping[_Key, Iterable[_Key]],
) -> dict[_Key, _Value]:
    """
    The fixed point that `answer` reaches from `start`. `answer` takes a key and the values known, and returns the key's
    value. `dependents` names the keys to answer again after the value of a key changes. A key `dependents` leaves out
    has no dependents.

    A worklist drives this. A key goes back on the list only where `dependents` lists it under a key whose value has
    changed. This raises on a key answered more than `ROUNDS` times. `deepest_rounds` reports the most answers a key
    took.
    """
    known = dict(start)
    pending = list(known)
    queued = set(pending)
    answered: dict[_Key, int] = {}
    while pending:
        key = pending.pop()
        queued.discard(key)
        answered[key] = answered.get(key, 0) + 1
        if answered[key] > ROUNDS:
            raise AssertionError(f"`{what}` did not settle in {ROUNDS} answers of a key")
        value = answer(key, known)
        if value == known[key]:
            continue
        known[key] = value
        for other in dependents.get(key, ()):
            if other not in queued:
                pending.append(other)
                queued.add(other)
    _DEEPEST[what] = max(_DEEPEST.get(what, 0), *answered.values())
    return known


def deepest_rounds() -> dict[str, int]:
    """
    `{what: the most rounds it took}`. The answer means something once a whole run has finished, as `unexercised` does.
    """
    return dict(sorted(_DEEPEST.items(), key=lambda counted: -counted[1]))


def unexercised() -> dict[str, list[str]]:
    """
    The function answers `{what: [kind]}` for a question holding a handler no input reached. The list leaves out the
    kinds a question calls untested. An input that reaches a kind exercises that kind. A partial run says nothing about
    a kind no input reached.
    """
    return {question.what: question.unused() for question in Question.asked if question.unused()}


def owed() -> dict[str, list[str]]:
    """
    `{what: [kind]}` per question whose untested part has arrived. A kind here is a decision that question owes. A run
    that reaches such a kind says so and fails. The run does not pass on the word of a family.
    """
    return {question.what: question.arrived() for question in Question.asked if question.arrived()}


# The record of the kinds a process reached. `what_was_reached` gives the record and `also_reached` folds it back in.
# The record holds the kinds a question covered, how deep a fixpoint went, and the untested kinds that arrived.
Reached = tuple[dict[str, set[str]], dict[str, int], dict[str, set[str]]]


def what_was_reached() -> Reached:
    """
    Answer the record this process has reached. The record holds the kinds a question covered, how deep a fixpoint went,
    and the untested kinds that arrived.

    A check that shares its work out over the cores runs in forked children. A child marks what it reached in a copy of
    the record. https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods describes that copy. A
    kind the child reached counts as reached. The record comes back with the answers, and `also_reached` folds it in.
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

    The result is a new value. The nodes are frozen.

    A class writes `renamed` beside `references`, and writes both of them itself. Neither derives from a list of fields.
    The fields mean different things. A call is not a continuation, and a protected match is not the handler that
    catches it. `_check_renaming` holds the pair together, and no shared declaration does. A rename must change exactly
    the names `references` reports.
    """
    # A value comes back as what it went in as. A walk over a tuple cannot say that. The `cast` here claims it to the
    # type checker, rather than in the `renamed` methods that unpack the result.
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
    The methods a node of the IR answers, and what a leaf gives back.

    A leaf holds no production name of its own. It inherits both methods and writes neither. A node holding other nodes
    overrides them, and reads the fields through `_refs` and `_renamed`.

    A node says for itself which fields hold a production name. `ParamValue` holds a `name` and answers with nothing,
    and that name is a grammar parameter.
    """

    def references(self) -> list[str]:
        """The production names this node holds, in the order the node holds them. A repeat stays."""
        return []

    # An override such as the `renamed` of `SubValue` reads `names`. A leaf holds no production name, and the
    # `references` of `Node` gives back nothing.
    def renamed(self, names: Mapping[str, str]) -> Self:  # pylint: disable=unused-argument
        """This node itself, unchanged."""
        return self


# --- value / parameter expressions.


@dataclass(frozen=True)
class ParamValue(Node):
    """
    A grammar parameter. `n` and `m` are indentations, `c` is the context and `t` is the chomping, as
    https://yaml.org/spec/1.2.2/#42-production-parameters says. `c-indentation-indicator` writes `m`.
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
    `(match)` is the characters of the token the parse is building. A rule has just matched those characters. A rule
    reads them where the rule must act on that text. The parse remembers where the open token began, and reads the
    characters from that start up to the current position.

    Under `(len)` a reader does not take `(match)` as text at all. The last consume's length is a slot of its own, and
    `LenValue` takes that slot. The meanings differ wherever a token cuts across a consume, or holds more than a single
    consume.
    """


@dataclass(frozen=True)
class GlobalValue(Node):
    """
    The value of the global `name`. The global belongs to the parse rather than to a call.

    A reader takes the value off the top of the global's stack rather than through a parameter. `SetVarAction` and
    `IncreaseAction` write it. `ClearVarAction` says where it stops applying.
    """

    name: str


@dataclass(frozen=True)
class IndentValue(Node):
    """
    The indentation in force. A grammar that pushes holds it on the stack. A grammar that does not push holds it in the
    parameter `n`. `PushIndentAction` puts the value on the stack and `PopIndentAction` takes the value back. The node
    itself holds no field.
    """


@dataclass(frozen=True)
class AutoDetectIndentValue(Node):
    """
    `<auto-detect-indent>` measures the next line holding a character other than a space. The value subtracts `n` from
    the indentation of that line. A reader takes it without consuming anything, and no bound applies.
    https://yaml.org/spec/1.2.2/#8111-block-indentation-indicator defines the marker.

    The node belongs to the official grammar. libyeast's grammar reads `ColumnValue` where the official grammar writes
    this node. `interpreter._EVALUATE` evaluates no such node. The node stays for the vendored grammar
    `check_vendor_spec` compares against, and that grammar writes the node. `annotated2ir.load` reads libyeast's grammar
    and the vendored grammar alike.
    """


@dataclass(frozen=True)
class ColumnValue(Node):
    """
    `<column>` is the column the parse is at. The count starts at `0`, as `Mark` counts it.

    `interpreter._EVALUATE` reads the column off the `mark` of the `Emitter`. The parse reads the column without
    lookahead.
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
    `(len)` is the length of its argument in characters.

    The grammar applies `(len)` to `(match)`. An `Emitter` counts the characters the last consume took. `(len)` of
    `(match)` gives that count. `interpreter._evaluated_length` takes that count off `Emitter.consumed_length` and
    measures no text. On another expression, `(len)` gives the length of its value.
    """

    arg: Node

    def references(self) -> list[str]:
        return _refs(self.arg)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class AtoiValue(Node):
    """`(atoi)` is the integer that the decimal digits of the text `arg` read as."""

    arg: Node

    def references(self) -> list[str]:
        return _refs(self.arg)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (arg,) = _renamed(names, self.arg)
        return replace(self, arg=arg)


@dataclass(frozen=True)
class BranchPart(Node):
    """
    A branch of a `(case)` or a `(flip)`. The interpreter takes the branch where the parameter holds the branch's value,
    in `_try_switch` for a `(case)` and in `_evaluated_switch` for a `(flip)`.
    """

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
    branches: tuple[BranchPart, ...]  # The `branches` pair a value of `var` with a result expression apiece.

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
    `interpreter._MATCHED` answers false for a `FailTree`, and runs the continuation of an `EmptyTree` in place.

    A `(case)` may decline a value of a finite parameter. `l-recover-entry` declines under a policy that does not
    recover here. Such a case states the decline with a `<fail>` branch rather than by leaving the branch out.

    A case names a branch per value its finite parameter takes, or has a default. `validate_grammar._check_total_cases`
    holds the case to that. A walk over such a case then reaches a node saying the decline, rather than an emptiness it
    has to read as refusal.
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
    `<invalid>` is a byte that begins no valid UTF-8 sequence. `ys_next_char` gives this byte the key
    `YS_LIT_KEY_INVALID`. A set holds the byte only through the interval `(-1, -1)`. The recovery rule `l-unparsed`
    reaches for the byte. A run of such bytes takes `unparsed-invalid`.
    """


@dataclass(frozen=True)
class CharSet(Node):
    """
    The node matches a character out of `spans`. The parser sees the set that way, and the parser can ask about a
    character that way.

    `spans` is a tuple of inclusive `(low, high)` codepoint intervals, sorted and disjoint and merged. A pair of nodes
    denoting the same characters are therefore the same node. The invalid byte is the interval `(-1, -1)`, a unit no
    character can hold. A set holding the invalid byte beside real characters holds that interval.

    A pass computes a union or a subtraction as it makes this node, and the node keeps neither operation. `chars.Model`
    gives a character a key holding a bit per set the grammar tests. `src/decoder.c` tests a character against a set
    with a bit mask.
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
    """
    `({N})` matches `item` exactly `count` times, and no times where `count` is not positive. `count` holds `N`. `N` is
    a literal or a parameter.
    """

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
    `TrimStarTree` matches a maximal run of `full` and gives back the trailing run of `trim` characters.

    The run must keep a character. The interpreter raises an `AssertionError` where the run keeps none. A run of only
    `trim` characters keeps none.
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
    The action consumes a maximal run of `set` characters. Normalization turns a `StarTree` or a `PlusTree` over a
    character class into this action. The action maps to a repeated-char-set match.

    The action takes at least a character. A gate sits in front of the action and peeks at `set`. A star adds a way
    beside the action. That way holds a `NegLookGuard` on the same question and a `ConsumeNoCharAction`. The star takes
    none on that way rather than through this action.
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
    The IR writes a run that took no character as this action. The match is empty, and the length reads `0`.

    A run that must take a character is a gate peeking its set and a consume of that set. A run that may take none
    offers that pair as one way. Its other way refuses the set and performs this action.

    A counted run builds this action too. The parse may work the count out. A guard then refuses a positive count, and
    the way behind that guard performs this action. The grammar may write a literal count that is no positive integer.
    The run is then this action and no more.

    The way taking none performs this rather than performing nothing. A length read past a way that performed nothing
    would be the length the last run left. That is a different run's answer.

    The action names no set. A pair of these actions compares equal.
    """


@dataclass(frozen=True)
class ConsumeCharAction(Node):
    """
    A `ConsumeCharAction` consumes the character a gate peeked. An action that finds no character shows a failed gate.
    The interpreter reports that failure rather than matching nothing.

    `set` holds the characters the action consumes. The gate tests the same characters.
    `normalize._split_consumes_into_gates` puts `set` on the action. `normalize._hoist_guards_to_callers` can move the
    gate into a caller. A reader takes a way's consumption from `set` rather than from a guard.
    """

    set: Node

    def references(self) -> list[str]:
        return _refs(self.set)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (renamed_set,) = _renamed(names, self.set)
        return replace(self, set=renamed_set)


def _asked_in_order(guard: object) -> tuple[str, str]:
    """
    Order a gate's guards by the kind, then by the whole question. The order is total, and it holds across runs.
    """
    return type(guard).__name__, repr(guard)


@dataclass(frozen=True)
class GatePart(Node):
    """
    The `guards` are the questions a parse enters an alternative on. A guard takes no character and holds at the parse's
    position. The guards mean a set rather than a sequence. They ask about the same position, and no order between
    guards means anything. A gate holding no guard is the unconditional fallthrough, and it sits on the last
    alternative. `normalize._every_option_is_reachable` checks that a way no input refuses comes last. The question
    about the character in front is a `LookGuard` over its class, and that guard sits among the other guards.

    A gate holds its guards as a tuple in a canonical order rather than as a set. A pair of gates asking the same
    questions are then the same gate. The sweep in `normalize._cleaned` can therefore merge the productions that hold
    them. The pipeline's output stays the same from run to run. A set would order its members by hash. A `ParamValue`
    names its parameter with a string. Python salts a string's hash afresh per process, as
    https://docs.python.org/3/reference/datamodel.html#object.__hash__ says.
    """

    guards: tuple[Node, ...] = ()

    def __post_init__(self) -> None:
        """
        Sort the guards into the order `_asked_in_order` gives. A pair of `GatePart` gates asking the same thing then
        compare equal.
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
    the way hands control to. `first` is the call and `second` is where the path continues past it. `second` with no
    `first` is a tail call. `second` has nothing behind it.

    `recover` rides the member of the pair holding the call it protects. That is `first` where a call sits there, and
    `second` where the way is a tail call.

    A cut unwinding out of that call stops at the recovery production `recover` names. The interpreter emits the error.
    It closes the markers the call opened down to that recovery. That recovery production matches what the failed call
    gives up, and the parse continues past the call as though it had matched. A recovery that does not match sends the
    cut on up.
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
        # The constructor refuses a way with a call and nothing behind it.
        if self.first is not None and self.second is None:
            raise ValueError(
                f"a way calling {self.first} with nothing behind it - a tail call is `second` with no " f"`first`"
            )
        # The constructor refuses a way with a recovery and no call.
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
    A production's body as the state machine reads it. The body holds its `alternatives` in order. The parse takes the
    first alternative that matches. A pass shapes a body into a `ChoiceState` unless the body is a set. A canonical
    choice then reads apart from an unshaped body.
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
    The action consumes up to `limit` characters of `set`. The parse takes the characters as a single consume. The
    action records whether the consume filled the limit. A run that fills the limit and a run that falls short both
    match. `DidMatchFullSpanGuard` tells them apart.

    The consume cannot fail.
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

    The guard asks about the action in front and no more. It runs where that action left the parse. Any other action
    takes the answer away. Asking with none there raises rather than answering.
    """


@dataclass(frozen=True)
class ConsumeTrimmedSpanAction(Node):
    """
    A maximal run of `full` characters that gives back its trailing run of `trim`. The parse takes it as a single
    consume. `check_dead_code._KEPT_THOUGH_DEAD` keeps this node, and no step builds it. It takes at least a character,
    and the interpreter asserts that.

    The pipeline builds none. A `TrimStarTree` sits where this node would sit. This file writes the node, and the
    interpreter matches it. `check_dead_code` declares it kept though dead.
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
    """`(===)` is positive lookahead. It takes nothing."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class NegLookGuard(Node):
    """`(!==)` is negative lookahead. It takes nothing."""

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
    """`(---)` is character-class subtraction. It is `base` less the characters `minus` holds."""

    base: Node
    minus: tuple[Node, ...]

    def references(self) -> list[str]:
        return _refs(self.base, self.minus)

    def renamed(self, names: Mapping[str, str]) -> Self:
        base, minus = _renamed(names, self.base, self.minus)
        return replace(self, base=base, minus=minus)


@dataclass(frozen=True)
class ExcludeAtAction(Node):
    """`(exclude)` consumes nothing. It adds `item` to the set the emitter forbids from matching."""

    item: Node

    def references(self) -> list[str]:
        return _refs(self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (item,) = _renamed(names, self.item)
        return replace(self, item=item)


@dataclass(frozen=True)
class MaxWrapper(Node):
    """
    `(max)` is a bound of `limit` characters. It is the implicit-key lookahead limit
    https://yaml.org/spec/1.2.2/#742-flow-mappings sets.

    The vendored grammar writes `(max)` before a production, as a length note on what follows. That form is `(max): N`.
    libyeast writes the bound around the production instead, as `(max): [N, message, rule]`. There it is the bounded
    window a parser resolves the key within. Consuming past `limit` characters is the error `message`.

    A key also stays within a single line, and `(max)` adds no check for that. `ir2spec` undoes the wrapping back to the
    vendored grammar's preceding `(max): N`.
    """

    limit: Node
    message: str | None = None
    item: Node | None = None

    def references(self) -> list[str]:
        return _refs(self.limit, self.item)

    def renamed(self, names: Mapping[str, str]) -> Self:
        limit, item = _renamed(names, self.limit, self.item)
        return replace(self, limit=limit, item=item)


@dataclass(frozen=True)
class IsLessThanGuard(Node):
    """`(<)` asserts the first value is less than the second, for the pair the parse works out."""

    a: Node
    b: Node

    def references(self) -> list[str]:
        return _refs(self.a, self.b)

    def renamed(self, names: Mapping[str, str]) -> Self:
        a, b = _renamed(names, self.a, self.b)
        return replace(self, a=a, b=b)


@dataclass(frozen=True)
class IsLessEqualGuard(Node):
    """`(<=)` asserts the first value is no greater than the second, for the pair the parse works out."""

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
    """A bare `(set)` binds parameter `param` to `value`. It matches the empty input and takes no character."""

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
    A zero-width action says `param` stops applying here. Past this action the environment holds no value for `param`. A
    read of the parameter there faults, unless the emitter is passing arguments. A global's stack gives up only its top
    value. A value beneath the top stays on the stack.

    After `_clear_reads`, a production that reads `param` ends on this action.
    """

    param: str


@dataclass(frozen=True)
class IncreaseAction(Node):
    """
    `(increase)` raises indentation parameter `param` to the current column. The write is `param = max(param, column)`.
    The action is zero-width. It records the widest indentation the parse has reached. `l-leading-empties`,
    `l-strip-empty` and `l-keep-empty` raise the floor that way.
    """

    param: str


# --- token annotations.
#
# The parser accumulates the characters it consumes into a run, and gives the run a code. A run ends wherever a
# `TokenWrapper` scope begins or ends, and wherever an `EmitAction` marker falls. A run that ends becomes a token. So an
# annotation does not make *a* token. It says what code the characters consumed within take. It also says where the
# parser cuts the runs.
#
# A character consumed under no annotation gets the code `unparsed-text`, the code a new `Emitter` starts with.
# `interpreter._fail` gives that code to the input past a failure. On the success path an `unparsed` character is a
# mistake. `validate_grammar.py` refuses a character-consuming node that lies outside a `TokenWrapper`.


@dataclass(frozen=True)
class TokenWrapper(Node):
    """
    `(token)` says the characters `item` consumes take `code`. A character a nested annotation claims takes the code of
    that annotation instead.

    The parse cuts the run at both edges. The characters before, within and after `item` fall into separate tokens. A
    nested annotation cuts its own token out of the item. `cut` emits no token for an item that consumes nothing.
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

    This is a single node rather than a sequence written out. The node pairs the markers, and a `begin` cannot lose its
    `end`.
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
    """
    `(emit)` emits a zero-width token at its own position in the grammar. The interpreter's `marker` calls `cut` before
    it emits the token. `cut` ends the open run of characters.
    """

    code: str


# A step writes halves. A half names in `pair` the pairs it can belong to. A pair as written gets an entry, and both
# halves of that pair name that entry.
#
# A close answers the open on the stack where both halves name a pair in common. `interpreter._popped` holds a close to
# that. The kind of an open cannot say which pair the open belongs to. A pair of `(token)`s are the same kind and
# different pairs. A close that takes the wrong open of its own kind is invisible to a reader asking which pair is
# there.
#
# The provisional run opens on `OpenProvisionalAction` and closes on `CommitProvisionalAction`. Neither half of that run
# holds a `pair`. `ys_queue_open_run` keeps a single run open at a time, and its close has nothing to tell apart.
#
# `pair` is a set of pairs rather than a lone entry. `_merged` makes halves indistinguishable. The merge joins the
# productions `normalize._grouped` finds alike. Those differ only in the pairs they hold or in the names of helpers that
# behave alike. The half that survives `_merged` holds the pairs of the productions it joined. So a close answers an
# open where the sets intersect. A close then cannot tell apart a pair of opens whose productions the merge joined.


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
    A zero-width action takes the indentation in force off the stack. The level it displaced comes back.

    `level` may name the value its `PushIndentAction` put there. The passes that mint a pair leave the pop's `level` at
    `None`. `pair` holds the mark that the pop shares with its push. The level says nothing the stack does not already
    hold, and the pop restores from the stack instead. A pop takes off the entry on top.

    `interpreter._try_pop_indent` checks a named level where the pop runs. `interpreter._popped` refuses a top opened
    for another pair, and that makes the pairing a refusal rather than a claim. Pops of the same level with different
    `pair` marks compare unequal. `pair`, not the level, keeps such pops from merging.

    The level is `None` where the pop names none. A push names the level it puts there.
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
    A zero-width action sets what may not match at a start of line to `item`. `item` is `None` where the line start
    forbids nothing. `interpreter._is_forbidden_here` asks the set at a start of line.

    An `(exclude)` becomes this action, at either end of the run the exclusion covers. The set is a single value for the
    parse rather than a value per call. This therefore writes a slot, and the write that ends a scope names what applies
    after it. It does not take back what the opening write displaced.

    The write takes nothing back. `l-unparsed` under the policy `i` opens an exclusion inside an exclusion of
    `l-bare-document`. The inner exclusion also names `s-indent-le-line`.
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
    A zero-width action that cuts the run and sets the code its following characters take to `code`. A `(token)` opens
    with this action. The action pushes the displaced code onto the stack. The matching `PopCodeAction` takes that code
    back.
    """

    code: str
    pair: frozenset[int]


@dataclass(frozen=True)
class PopCodeAction(Node):
    """
    A zero-width action cuts the run and takes the code for its following characters off the top of the stack. Its
    `PushCodeAction` displaced that code. A `TokenWrapper` lowers to a `PushCodeAction`, its item, and a
    `PopCodeAction`. A pop with nothing pushed raises. The pair says where a code begins and ends.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushMessageAction(Node):
    """
    A zero-width action that opens a committed region under `message`. A `(commit)` opens with this action. The parse
    must get through the input from here to the `PopMessageAction` that closes the region. A failure that unwinds past
    this point with the region left open reports `message`. A failure that unwinds through a closed region backtracks
    like any other.

    A gate does not rise past such a push. Refusing entry to a region the grammar committed to must stay the error
    `message` names.
    """

    message: str
    pair: frozenset[int]


@dataclass(frozen=True)
class PopMessageAction(Node):
    """
    A zero-width action that closes the committed region the innermost `PushMessageAction` opened. A parse that reaches
    this action marks the region reached. A later failure then backtracks through the region like any other, and reports
    no error.

    `_lowered_commit` wraps the item of a `CommitWrapper` in a `PushMessageAction` and a `PopMessageAction`. The pop
    pairs with its push on the stack of the parse, the way a `(token)`'s code does.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushRecoveryAction(Node):
    """
    A zero-width action that says what catches a failed `(cut)` from here on. `recovery` matches the part of the input
    the parse gives up. `resume` is where the parse continues past the protected call once `recovery` has matched.

    The parse holds the pair together. An unwind reads where to stop and where to go on, and it reads both from a single
    place. The unwind does not derive from what the abandoned parse left behind.

    `l-recover-entry` declines under a resume policy that does not recover here. The cut goes on unwinding to whoever
    does catch it.
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
    unwinds to the recovery region that answered before that push.

    The pop takes the top of the parse's stack. The pop refuses a top that is not a recovery region. The pop also
    refuses a top opened for pairs of which none is in `pair`.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class OpenWindowAction(Node):
    """
    `OpenWindowAction` opens a `(max)` window `limit` characters wide. Past the window's edge, a committed consume in
    `Emitter` fails the cut `message` names.

    Windows do not nest. The outermost window applies. An inner window sits inside the budget the outer already bounds.
    An open under an outer window pushes a stack entry for its close and sets no ceiling.
    """

    limit: Node
    message: str
    pair: frozenset[int]

    def references(self) -> list[str]:
        return _refs(self.limit)

    def renamed(self, names: Mapping[str, str]) -> Self:
        (limit,) = _renamed(names, self.limit)
        return replace(self, limit=limit)


@dataclass(frozen=True)
class CloseWindowAction(Node):
    """
    A zero-width action closes a `(max)` window. It goes with `OpenWindowAction`, and both halves hold the same `pair`.
    `_lowered_window` places the item between the halves of a `MaxWrapper`.

    A close pops the stack entry its own open pushed. The close then restores the ceiling that open displaced. The
    window is gone exactly where the open that set it closes.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class StartMustConsumeAction(Node):
    """
    A zero-width action that opens a region which must take a character. The `DidConsumeSinceOpenGuard` of the region
    may run with the parse still at the position of this action. The guard then finds that the region has not matched.

    The action opens the first turn of a run and the turns the loop repeats. The grammar can see the action there. A
    turn taking no character is a turn that would repeat forever. A run therefore says of its turn that the turn takes a
    character. The guard compares the position at the open with the current position. The action is harmless around a
    turn that takes a character anyway. A run marks a turn whether or not the turn needs the mark.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class DidConsumeSinceOpenGuard(Node):
    """
    Whether the region its own `StartMustConsumeAction` opened has taken a character. It refuses where the parse has not
    moved since that open. `EndMustConsumeAction` ends the region. A gate holding this asks a question and leaves the
    parse where it was, the way the other guards do.

    The guard and `StartMustConsumeAction` go together, on the stack of the parse as the other pairs do. The guard reads
    the innermost open with the same `pair`, rather than the innermost open of any pair. Opens of other pairs may sit
    between the halves, and the parse walks past those opens.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class EndMustConsumeAction(Node):
    """
    A zero-width action that closes the region its own `StartMustConsumeAction` opened. It takes that open off the stack
    of the parse. It takes the open on *top*, and that open must name this `pair`. The action looks no further down.
    Popping the top tells this action apart from `DidConsumeSinceOpenGuard`. That guard reads rather than pops, and
    walks past the opens of other pairs.

    The action says nothing about whether the region matched. `DidConsumeSinceOpenGuard` asks that. A way holding this
    action has entered on the answer already.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PushBackTrackAction(Node):
    """
    A zero-width action that opens a region the parse gives back whole. `PopBackTrackAction` closes the region. The
    parse keeps the ways it takes inside the region. A failure past the close gives the region up. The parse does not
    choose among those ways again.

    This action makes a repetition possessive. A run takes its turns. A continuation that fails fails the run entire.
    The parse holds no shorter run to fall back to. A failure before the close backtracks like any other.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class PopBackTrackAction(Node):
    """
    A zero-width action closes the region the innermost `PushBackTrackAction` opened. It settles the ways taken inside
    that region. `_popped` refuses a stack top opened for pairs of which none is in the pop's `pair`.
    """

    pair: frozenset[int]


@dataclass(frozen=True)
class OpenProvisionalAction(Node):
    """
    A zero-width action that opens the provisional run. The parse has still to decide the tokens it emits from here on.
    `ys_queue_emit` builds those tokens and holds them in the queue. `ys_parser_read` hands back none of those tokens
    until a `CommitProvisionalAction` resolves the run. Before then only `ys_queue_run` reaches them. A single run is
    open at a time.
    """


@dataclass(frozen=True)
class MarkProvisionalAction(Node):
    """
    `MarkProvisionalAction` takes no width and marks the open run's current position. `mark_provisional` cuts the run
    into the region before the mark and the region from the mark on. A later `RetypeProvisionalAction` names either
    region, and an `InjectBeforeAction` at `mark` lands between them.

    A run holds a mark at a time. A second call to `mark_provisional` moves it, and the last call wins. A mark is a
    parse position, rather than a property of any token.
    """


@dataclass(frozen=True)
class RetypeProvisionalAction(Node):
    """
    A zero-width action. `retype_provisional` rewrites the held tokens in `region` by kind. `_region` reads `region` as
    `all`, `before_mark` or `after_mark`. A token whose characters came in as a line break takes `breaks`, and any other
    token with text takes `rest`. A token with no text and an error keep their codes. A kind whose code is `None` keeps
    its own.

    The run stays open. The field is `breaks` rather than the `break` code `wire.CODE_CHAR` names. Python reserves
    `break`, as https://docs.python.org/3/reference/lexical_analysis.html#keywords says. `rest` and `breaks` are token
    codes rather than production names. `retype_provisional` looks them up in `wire.CODE_CHAR`.
    """

    rest: str | None
    breaks: str | None
    region: str


@dataclass(frozen=True)
class InjectBeforeAction(Node):
    """
    `InjectBeforeAction` takes no width. `inject_before` writes the token codes in `codes` into the open run in order.
    The tokens hold empty text. `at` names the place, either `start` or `mark`.

    `start` sits ahead of the whole run. `mark` sits at the mark `MarkProvisionalAction` set.

    `ys_queue_inject` puts a single decided token ahead of an open run. `codes` are token codes rather than production
    names.
    """

    codes: tuple[str, ...]
    at: str


@dataclass(frozen=True)
class CommitProvisionalAction(Node):
    """
    A zero-width action that resolves the open run.

    The action goes with `OpenProvisionalAction` dynamically. The run belongs to the queue that `ys_queue_run` reads
    rather than to any single call.
    """


@dataclass(frozen=True)
class CutAction(Node):
    """
    `(cut)` is a zero-width commit. The parse does not backtrack past a cut. A failure past the cut reports `message`. A
    later cut that failed first keeps its own message.

    `message` is a key into `grammar/messages.yaml`, as `generator/check_messages.py` checks.
    """

    message: str


@dataclass(frozen=True)
class CommitWrapper(Node):
    """
    `(commit)` matches `item`. It commits to `item` being present and no further. A `(cut)` scoped to `item` says the
    same thing.

    `interpreter._try_cut` runs a `(cut)`, and the parse retries nothing behind the cut. `(commit)` commits to `item`
    being able to match at all. `message` is the error where `item` cannot match, as a `(cut)`'s message names its
    error. `c-double-quoted` and `c-single-quoted` commit to a closing quote. `c-flow-sequence` and `c-flow-mapping`
    commit to a closing bracket.

    The match backtracks like any other where `item` matches and a *later* rule fails. The commitment does not reach
    past `item`. `message` keys `grammar/messages.yaml`, as a `(cut)`'s message does. `generator/check_messages.py`
    fails the build where that table lacks the key.

    An implicit key that will not parse raises no error. `c-double-quoted` wraps its commit in a `(case) c`. The key
    branches are the bare `item`, and the `else` is the commit. The grammar softens the error with a switch on `c`, and
    the parser decides nothing.
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
    token. The interpreter's `error` makes that cut.

    `message` names the expectation to report. It is a key into `grammar/messages.yaml`, as a `(cut)`'s message is a key
    there. Unlike a `(cut)`, an `(error)` is a match rather than a commit. It emits and succeeds. The rule holding the
    `(error)` says what the parser does with the input from here.
    """

    message: str


@dataclass(frozen=True)
class RecoverWrapper(Node):
    """
    `(recover)` is where a `(cut)` inside `item` stops unwinding, when `recovery` says it stops here.

    A cut unwinds past the calls between itself and the rule that will catch it. A `(recover)` is such a rule. The
    interpreter emits the error. It closes the markers `item` opened down to this point and no further. `recovery`
    matches the part of the input this rule is willing to give up. The parse then continues from here as though `item`
    had matched, and a repetition around the rule takes its next turn.

    `recovery` decides whether that happens at all. `l-recover-entry` declines under a resume policy that does not
    recover here. The rule does not match, and the cut goes on unwinding to whoever does catch it. A policy recovering
    elsewhere sees no sign of this rule.
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


# The families of kinds. This file holds the families together. A family is a statement about the kinds themselves.
# `ACTIONS` holds the actions. `CONSUMES_NOTHING` holds the kinds that take no character. `HOLDS_A_MATCH` holds the
# kinds that hold a match.
#
# A `Question` takes a family as a key. A family separates an answer from another. The `untested` parameter of a
# `Question` lists the members of a family that have yet to turn up.
#
# A pair of families with the same members are a family under a pair of names. `_refuse_a_family_said_twice` refuses
# such a pair.

# The nodes holding characters a rule asks about rather than takes. A lookaround asks about its own position. The
# question is whether its item matches in front, does not match in front, or matched behind. An exclusion says what may
# not match at a start of line from here on. `interpreter._is_forbidden_here` asks the exclusion at a take that follows.
#
# So a walk of the characters the grammar takes stops at such a node. A rule asks about the characters inside rather
# than taking them. Counting the characters inside such a node would count characters the parse does not consume.
#
# A node does not join this family by taking nothing. A guard and an action outside `CONSUMING` take nothing too.
# `EndOfStreamGuard` asks about the input and falls outside this family. The binding lists the nodes in alphabetical
# order.
ASKED_NOT_TAKEN_NODES = (ExcludeAtAction, LookBehindGuard, LookGuard, NegLookGuard)

# The kinds that take characters themselves rather than through the nodes they hold. Such a kind takes at least a
# character wherever it matches. `interpreter._try_limited_span` raises on a consume that takes none. This set is the
# counterpart of `CONSUMES_NOTHING`.
#
# A kind that reads on a way and not on another is neither this nor `CONSUMES_NOTHING`. A run, a repetition and a choice
# are such kinds, and a reader asks about their parts instead.
#
# The set lists its kinds in alphabetical order.
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

# `ACTIONS` holds the action kinds that `CONSUMING` leaves out. `ACTIONS` sorts its kinds by name.
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

# A guard is a question the parse answers where it is, and the answer takes no character. A guard asks what the input
# holds around the position, and how the count it holds compares. It leaves nothing behind and may decline. A reader
# tells a guard from an action on those grounds. `interpreter._MATCHED` runs a guard as a test. A guard that holds runs
# the continuation in place.
#
# A `(cut)` is no guard. It takes nothing either. It commits the parse rather than asking anything, and belongs in
# `ACTIONS`. `interpreter._try_cut` runs a cut. The names run in alphabetical order, save that `DidMatchFullSpanGuard`
# comes before `DidConsumeSinceOpenGuard`.
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

# The kinds the machine performs in a way as single steps. The set holds the actions. It holds the consumes as well. A
# set sits *inside* a consume. The consume consumes that set rather than performing it as a step.
PERFORMED_STEPS = (
    *ACTIONS,
    ConsumeCharAction,
    ConsumeLimitedSpanAction,
    ConsumeSpanAction,
    ConsumeTrimmedSpanAction,
)

# A kind in this set takes characters and leaves the length behind, in the forms the pipeline gives them. The
# repetitions the grammar writes are such kinds. So are the consumes `normalize._spanned_run` builds. A
# `ConsumeCharAction` is such a kind too.
#
# `ConsumeNoCharAction` falls outside this set. `interpreter._try_no_char` sets its length to `0`. The tuple lists its
# kinds in alphabetical order.
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
# does neither. `<empty>` is neither an action nor a guard. It does nothing, and matches wherever a way arrives. This
# tuple holds `<empty>`.
CONSUMES_NOTHING = (*ACTIONS, *GUARDS, EmptyTree)

# `EmptyTree` is in `CONSUMES_NOTHING` and in `TREES`. The author of a question naming both families leaves `EmptyTree`
# out of `CONSUMES_NOTHING` or out of `TREES`. A `Question` naming a kind twice raises.

# A peek is a guard that holds its question about the input as an `item` and takes nothing. It may ask about the
# character in front or the character behind. `every-peek-is-a-character-set` is the rule over these. A guard that reads
# the input need not be a peek. `<end-of-stream>` asks whether a character is there at all. Its `EndOfStreamGuard` holds
# no question.
PEEKS = (LookGuard, LookBehindGuard, NegLookGuard)

# The guards that read what is in front of the parse. `EndOfStreamGuard` asks whether the input has ended. `LookGuard`
# asks whether the input ahead matches an item. `NegLookGuard` asks whether the input ahead fails to match an item. A
# look behind, the parse's place in the line and the comparison of the indentation fall outside these guards.
_LOOKS_AHEAD = (EndOfStreamGuard, LookGuard, NegLookGuard)

# The kinds that match a single character, under any character of their set. A difference or an alternation can match a
# single character. So can a call. A difference matches a single character where its base does. An alternation with
# items matches a single character where its items do. A call matches a single character where the body of its rule
# does. Whether such a kind matches a single character is a question about the grammar rather than about the kind. The
# tuple lists the kinds in alphabetical order.
_ALWAYS_ONE_CHAR = (CharSet, InvalidSet, OneCharSet, RangeSet)

# A kind in this set repeats a match. `interpreter._try_longest_run` matches the item as many times as the input allows.
# `interpreter._try_counted_run` matches the item as many times as a count fixes. A run that a parser makes as a consume
# stays out of this set. `normalize._spanned_run` turns a run of a single character into such a consume. The source
# lists the kinds in alphabetical order.
REPETITIONS = (PlusTree, RepTree, StarTree)

# A repetition the input ends rather than a count. `interpreter._try_repeat` takes turns until what it repeats declines
# or matches nothing. `REPETITIONS` takes in the counted repetition `RepTree` as well.
RUNS = (PlusTree, StarTree)

# A node of a kind in this set holds its content rather than bracketing the content with a pair. The `lower-scopes`
# phase takes apart a node of this set. A `RecoverWrapper` stays whole. The set lists its kinds in alphabetical order.
WRAPPERS = (CommitWrapper, MaxWrapper, RecoverWrapper, TokenWrapper, Wrapper)

# The shapes of the tree. The shapes are a choice of ways and a run of a way. They are zero or more and one or more.
# They are a counted repetition and a trimmed repetition. They are an optional and a binding. They are a switch, an
# empty match and a failed match. The tuple lists them in alphabetical order of class name.
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

# The kinds the machine has a state for. Those are the choice of ways a production offers, and a way among them. The
# lowerings turn the shapes of `TREES` into these kinds. In alphabetical order.
STATES = (AlternativeState, ChoiceState)

# The kinds a node holds that are neither a match nor a value. `BranchPart` is a branch of a `(case)` or a `(flip)`, and
# it pairs a parameter's value with a result. `GatePart` is the gate a parse enters a way on, and it holds the guards
# the way asks. In alphabetical order.
PARTS = (BranchPart, GatePart)

# The kind that hands control to a production.
CALLS = (RefCall,)

# The kinds whose item a parse enters at the node's position. A run and a repetition take their first turn there. A
# scope and a commit take their content there. The list runs in alphabetical order.
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

# The kinds that hold a match. The invariant `every-sub-item-is-one-step` refuses such a kind where an item sits. The
# step `lower-bind` turns a binding into a run of its match and then an action. The set lists its kinds in alphabetical
# order.
HOLDS_A_MATCH = (AltTree, BindTree, RecoverWrapper, SeqTree)

# `BODY_KINDS` names the shapes of a production's body. An `AltTree` is an alternation. A `ChoiceState` is a shaped
# choice. A `RecoverWrapper` holds a way under a `(recover)` handler. A `SeqTree` is a concatenation. A body of another
# kind is a single way. `BODY_KINDS` leaves a binding out. A body that is a `BindTree` hides a write behind a match, and
# `no-bind-nodes` counts such a body. The names run in alphabetical order.
BODY_KINDS = (AltTree, ChoiceState, RecoverWrapper, SeqTree)

# An item may take one of these shapes. An item is something the machine does where it appears. Such an item is a call,
# a step that takes no character, or a step that takes characters.
#
# A guard's question is the guard's business and no item of the way. A peek holds a set. An exclusion holds a question
# that a bounded run of steps answers. `every-peek-is-a-character-set` covers the peek's set, and
# `every-exclusion-is-bounded` covers the exclusion's question.
#
# The list names the families and the call rather than the forms a phase reaches. The machine would read a taking left
# out of the list as an unknown shape.
LEAF_ITEMS = (*CONSUMES_NOTHING, *CONSUMING, RefCall)

# `interpreter._EVALUATE` works out the value of the kinds it lists. It lists no `AutoDetectIndentValue`. `ColumnValue`
# measures a position. `IndentValue` reads the indentation in force. A grammar that pushes holds the indentation on the
# stack. Another grammar holds it in the parameter `n`. `LenValue` measures the length of its argument. `LenValue` of
# `(match)` gives the length the last consume took. `AutoDetectIndentValue` is the official grammar's detected
# indentation. `interpreter._EVALUATE` is a `Question`, and a `Question` raises on a kind its table leaves out.
# `LitValue`, `ParamValue` and `GlobalValue` name a value. `MatchValue` reads the matched text. `AtoiValue` reads the
# text of its `arg` as an integer. `AddValue` and `SubValue` do arithmetic. `FlipValue` switches over a parameter. The
# tuple lists the kinds in alphabetical order.
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

# The kinds that do not match a single character. A repetition or a sequence takes a run rather than a character. A
# lookaround and a marker take none. A guard takes none. An action other than a consume takes none. A value expression
# is no match at all.
#
# This set and the kinds that can be a character together come to `KINDS`. The set lists its kinds in alphabetical
# order, save that `DidMatchFullSpanGuard` comes before `DidConsumeSinceOpenGuard`.
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

# The kinds of node the IR writes inside a body. `_NOT_ONE_CHAR` names a kind that cannot be a character. The tuple
# below names a kind that can be a character. The pair together cover the kinds.
#
# A kind added to neither is a kind `Question` refuses to hear about. A table naming such a kind names no kind of node.
#
# `KINDS` leaves out `Prod`. A `Prod` holds a spec number and a name. A `Prod` also holds a parameter list and a body. A
# body hangs off a `Prod`.
KINDS = _NOT_ONE_CHAR + (AltTree, CaseTree, OneCharSet, CharSet, DiffSet, InvalidSet, RangeSet, RefCall)


def _refuse_a_kind_outside_the_partition() -> None:
    """
    Refuse a kind that sits in a pair of the families of `_PARTITION`. A kind of `KINDS` may sit in none. A `Question`
    built from the families then raises on that kind, and the question names that kind by hand.
    """
    placed = [one for family in _PARTITION for one in family]
    twice = sorted({one.__name__ for one in placed if placed.count(one) > 1})
    if twice:
        raise TypeError(f"the families of `_PARTITION` place {twice} twice")


def _refuse_a_family_said_twice() -> None:
    """
    Refuse a pair of families of kinds that name the same members. That is a family under a pair of names.

    A family is a statement about the kinds themselves. The members tell a pair apart, rather than the prose above them.

    A pair of comments may ask different questions. The first asks what takes a character wherever it matches. The
    second asks what takes a character itself rather than through the nodes it holds. The pair are a family the moment
    they come to the same members. A reader answering the first through the second is then right by accident.
    """
    said: dict[frozenset[type], str] = {}
    for name, family in globals().items():
        if name.isupper() and isinstance(family, tuple) and family and all(isinstance(one, type) for one in family):
            members = frozenset(family)
            if members in said:
                raise TypeError(f"`{said[members]}` and `{name}` name the same kinds. that is one family twice over.")
            said[members] = name


# `is_one_char` reads its answer from this table. It covers the kinds somebody wrote in and no more. A kind absent from
# here is a kind nobody has asked this of. `is_one_char` raises on such a kind.
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
        # This list names the kinds that reach here and are no single-character class. `ConsumeCharAction` consumes a
        # single character and still answers `False`.
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


# `repeated` reads its answer from this table. A parser may make a run as a single `ConsumeSpanAction`. Such a run
# repeats nothing.
_REPEATED: Question["Node | None"] = Question(
    "the match a node takes again and again",
    {
        TrimStarTree: lambda node: node.full,
        REPETITIONS: lambda node: node.item,
        (AltTree, BindTree, CaseTree, EmptyTree, FailTree, OptTree, SeqTree): None,
        (*ACTIONS, *CONSUMING, *GUARDS, *WRAPPERS, *STATES, *PARTS, *CALLS, *VALUE_KINDS): None,
    },
)

# `rebuilt` reads this table to decide whether it visits a node a field holds. A literal and a parameter hold no
# grammar.
_IS_REBUILT_INTO: Question[bool] = Question(
    "whether a rebuild visits a node a field holds",
    {
        (LitValue, ParamValue): False,
        (
            AddValue,
            AtoiValue,
            AutoDetectIndentValue,
            ColumnValue,
            FlipValue,
            GlobalValue,
            IndentValue,
            LenValue,
            MatchValue,
            SubValue,
        ): True,
        (*ACTIONS, *CONSUMING, *GUARDS, *TREES, *WRAPPERS, *STATES, *PARTS, *CALLS): True,
    },
)


# The families over `KINDS`. A kind sits in a single family here at most. `_refuse_a_kind_outside_the_partition` holds
# the families to that.
_PARTITION = (ACTIONS, CONSUMING, GUARDS, TREES, WRAPPERS, STATES, PARTS, CALLS, VALUE_KINDS)


def kinds_other_than(*kinds: type) -> tuple[type, ...]:
    """
    The kinds of `_PARTITION` that `kinds` leaves out. A question takes this tuple as a key, and gives its kinds a
    shared answer.

    Public. A question outside this module calls `kinds_other_than` to name the kinds it answers alike.
    """
    return tuple(one for family in _PARTITION for one in family if one not in kinds)


def is_one_of(family: tuple[type, ...], what: str) -> Question[bool]:
    """
    The question whether a node is of a kind in `family`. `what` says the question in words.

    `normalize` builds its yes-or-no questions over a family through this function.
    """
    return Question(what, {family: True, kinds_other_than(*family): False})


# The items a node puts in a sequence it sits in. A sequence puts its own items there.
ITEMS_IN_A_SEQUENCE: Question[tuple["Node", ...]] = Question(
    "the items a node puts in a sequence it sits in",
    {
        SeqTree: lambda node: node.items,
        kinds_other_than(SeqTree): lambda node: (node,),
    },
)

# Whether a node reads the text the parse matched.
DOES_READ_THE_MATCH = is_one_of((MatchValue,), "whether a node reads the text the parse matched")

# Whether a node is the length the last consume took. `(len)` of `(match)` is that length.
IS_THE_CONSUMED_LENGTH: Question[bool] = Question(
    "whether a node is the length the last consume took",
    {LenValue: lambda node: DOES_READ_THE_MATCH(node.arg), kinds_other_than(LenValue): False},
)

# The call of a production a node makes, and `None` for a node that hands control to no production.
CALLED: Question[RefCall | None] = Question(
    "the call of a production a node makes", {CALLS: lambda node: node, kinds_other_than(*CALLS): None}
)

# The name of the production a call enters. Any other kind raises.
NAME_CALLED: Question[str] = Question("the name of the production a call enters", {CALLS: lambda node: node.name})

# The kinds holding a `pair`. A bracket opens and closes with nodes of the same pair.
_PAIRED = (
    CloseWindowAction,
    DidConsumeSinceOpenGuard,
    EndMustConsumeAction,
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
    StartMustConsumeAction,
)

# The pair a node holds, and `None` for a node that holds no pair.
PAIR_HELD: Question[frozenset[int] | None] = Question(
    "the pair a node holds", {_PAIRED: lambda node: node.pair, kinds_other_than(*_PAIRED): None}
)

# The parameter a node reads, and `None` for a node that reads none. A `GlobalValue` is no parameter.
PARAMETER_READ: Question[str | None] = Question(
    "the parameter a node reads", {ParamValue: lambda node: node.name, kinds_other_than(ParamValue): None}
)

# The change a node makes to the depth of the indentation stack.
INDENTS_PUSHED: Question[int] = Question(
    "the change a node makes to the depth of the indentation stack",
    {PushIndentAction: 1, PopIndentAction: -1, kinds_other_than(PushIndentAction, PopIndentAction): 0},
)

# Whether a node does nothing.
DOES_NOTHING = is_one_of((EmptyTree,), "whether a node does nothing")

# Whether a node is a character set the parser tests by its spans. A `DiffSet` and a `RangeSet` are other set forms.
IS_A_CHARACTER_SET = is_one_of((CharSet,), "whether a node is a character set the parser tests by its spans")

# Whether a node is `<fail>`, the match no input makes.
DOES_ALWAYS_FAIL = is_one_of((FailTree,), "whether a node is the match no input makes")

# Whether a node cuts its way. `interpreter._try_cut` reports the error a `(cut)` names where a failure comes past that
# cut. `_try_cut` skips that report where a later cut failed first.
DOES_CUT = is_one_of((CutAction,), "whether a node cuts its way")

# The match a commit holds, and `None` for a node that commits nothing. `interpreter._try_committed` sets an error where
# that match fails before its end. The error is the commit's `message`.
COMMITTED: Question["Node | None"] = Question(
    "the match a commit holds", {CommitWrapper: lambda node: node.item, kinds_other_than(CommitWrapper): None}
)

# The spans a character set takes. Any other kind raises.
SPANS_OF_A_SET: Question[tuple[tuple[int, int], ...]] = Question(
    "the spans a character set takes", {CharSet: lambda node: node.spans}
)

# Whether a run can take no turn. `(***)` can. `(+++)` must take a turn.
CAN_TAKE_NO_TURN: Question[bool] = Question("whether a run can take no turn", {StarTree: True, PlusTree: False})

# The change a node makes to the count of open message regions.
REGIONS_OPENED: Question[int] = Question(
    "the change a node makes to the count of open message regions",
    {PushMessageAction: 1, PopMessageAction: -1, kinds_other_than(PushMessageAction, PopMessageAction): 0},
)

# The change a node makes to the depth of open `(max)` windows.
WINDOWS_OPENED: Question[int] = Question(
    "the change a node makes to the depth of open `(max)` windows",
    {
        OpenWindowAction: 1,
        CloseWindowAction: -1,
        (
            *(kind for kind in ACTIONS if kind not in (OpenWindowAction, CloseWindowAction)),
            *CONSUMING,
            *GUARDS,
        ): 0,
    },
)

# Whether a node asks about the input in front of the parse.
IS_LOOKING_AHEAD = is_one_of(_LOOKS_AHEAD, "whether a node asks about the character in front of the parse")

# Whether a node is an item the machine does where it appears.
IS_A_LEAF_ITEM = is_one_of(LEAF_ITEMS, "whether a node is an item the machine does where it appears")

# Whether a node is a guard.
IS_A_GUARD = is_one_of(GUARDS, "whether a node is a guard")

# Whether a node takes characters.
IS_CONSUMING = is_one_of(CONSUMING, "whether a node takes characters")

# The match a lookaround holds, or `None` for a node that peeks at nothing.
PEEKED: Question["Node | None"] = Question(
    "the match a lookaround holds",
    {PEEKS: lambda node: node.item, kinds_other_than(*PEEKS): None},
)

# The ways an alternation offers, and `None` for a node of any other kind. `ITEMS_IN_AN_ALTERNATION` answers `(node,)`
# for such a node.
WAYS_OF_AN_ALTERNATION: Question[tuple["Node", ...] | None] = Question(
    "the ways an alternation offers", {AltTree: lambda node: node.items, kinds_other_than(AltTree): None}
)

# The items a sequence runs in order, and `None` for a node that runs none. `ITEMS_IN_A_SEQUENCE` answers `(node,)` for
# such a node.
ITEMS_IN_ORDER: Question[tuple["Node", ...] | None] = Question(
    "the items a sequence runs in order", {SeqTree: lambda node: node.items, kinds_other_than(SeqTree): None}
)

# The items a node puts in an alternation it sits in. An alternation puts its own items there.
ITEMS_IN_AN_ALTERNATION: Question[tuple["Node", ...]] = Question(
    "the items a node puts in an alternation it sits in",
    {AltTree: lambda node: node.items, kinds_other_than(AltTree): lambda node: (node,)},
)


def repeated(node: "Node") -> "Node | None":
    """The match `node` takes again and again, and `None` where it takes a turn at most."""
    return _REPEATED(node)


# `Kept` is the shape a `kept_for` store takes. The key is the subject's id beside the other values the answer turns on.
# The value is the subject beside its answer.
Kept = dict[tuple[int, object], tuple[object, _Answer]]


def kept_for(store: Kept[_Answer], subject: object, work: Callable[[], _Answer], under: object = ()) -> _Answer:
    """
    `work()` for `subject`, worked out once and kept in `store` against that object's identity and `under`.

    The store keys on identity rather than equality. An asker asks about a grammar or a node. A grammar compares equal
    to objects that are not it. A `Node` compares equal to objects that are not it. A grammar is a dictionary of whole
    production trees.

    The store keeps `subject` beside the answer, and checks it. An `id` is unique among the objects alive at once and no
    further, as https://docs.python.org/3/library/functions.html#id says. A grammar that has been let go frees its id
    for the next grammar. A store holding only the answer would hand the next grammar its predecessor's answer. Holding
    the subject keeps the subject alive, and the id goes on naming the subject.

    `under` holds the other values the answer depends on. Such a value is hashable, and the store compares it by value.
    An asker whose answer turns on an action as well as a grammar names the action there. The asker builds no key of its
    own.
    """
    known = store.get((id(subject), under))
    if known is None or known[0] is not subject:
        store[id(subject), under] = known = (subject, work())
    return known[1]


def rebuilt(node: "Node", visit: Callable[["Node"], "Node"]) -> "Node":
    """
    `node` with the grammar nodes it holds replaced by `visit` of those nodes.

    `rebuilt` visits a node in a field where `_IS_REBUILT_INTO` says yes. A literal or a parameter in a field stays
    unchanged. A caller recurses without special-casing any node. `rebuilt` visits the items of a non-empty tuple of
    nodes. `_IS_REBUILT_INTO` does not filter those items.
    """
    changed: dict[str, object] = {}
    for field in fields(node):
        value = getattr(node, field.name)
        if isinstance(value, Node) and _IS_REBUILT_INTO(value):
            changed[field.name] = visit(value)
        elif isinstance(value, tuple) and value and all(isinstance(item, Node) for item in value):
            changed[field.name] = tuple(visit(item) for item in value)
    return replace(node, **changed) if changed else node


# The families come first, and the pair of checks comes last. An earlier check reads and judges only the families bound
# above it.
_refuse_a_family_said_twice()
_refuse_a_kind_outside_the_partition()
