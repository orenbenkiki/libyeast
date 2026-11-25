# SPDX-License-Identifier: MIT
"""
Normalize the grammar, a goal at a time.

The pipeline runs a sequence of phases. A phase names the invariants it owns. The steps of a phase leave those
invariants at none. `invariant_faults` enforces an invariant from the first step that names it. The law "none stays
none" then makes a later step keep it.

`_PHASES` says what a phase is for, beside the steps that serve it. `invariant_faults` holds `STEPS` to the law.
`unsettled_invariants` says what the last stage still breaks. `untested_steps` says which steps promise something
nothing checks. The pipeline's claims are then answerable rather than described.

**A question that dispatches on node kinds names the kinds it accepts and raises on any other kind.** It has no trailing
default. A `return None` catches a kind nobody thought about. So do `return True`, `continue` and `break`. A default
answer covering a form the question does not recognise is not caution. It reports the blindness of the question as a
fact about the grammar. A count taken from it then reads plausible and wrong. `_is_nullable` and `_split` ask an
`ir.Question` for that reason. So do `_shortest_match` and `_entered_unconsumed`. `_CHOICE_WAYS` and `_ALTERNATIVES`
answer the kinds they leave out alike through `ir.kinds_other_than`.

**And a caller asks a question in a single place.** `_gated_by` states where a parse may enter a way, and
`_accepted_way` says what a way consumes. `_is_nullable` says whether a match can consume nothing. `_split` names a
match's consuming half beside its empty half. `_items_of_way` says what a way performs, and `_parts_of_way` says what a
question about a way walks. `after_action` says where performing an action leaves a parse. `_entry_and_exit_spaces` says
where a parse enters a production and where it leaves. A step or a check that needs one of these calls it rather than
walking the grammar again. Separate walks answering the same question drift. The drift shows up as a count that moves
where nothing about the grammar did.

**The askers of a way's entry ask different questions.** `spaces_of_ways` intersects a production's entry with the gate
the way holds. The call graph grows that entry. `_spaces_paths_enter_ways_in` reads `_leaf_tables` and works on the ways
of a choice short of the last. It unions what the paths into the production ask, taken together with the way's gate.
`spaces_of_ways` names the states a parse entering the way may be in.
"""

import collections
import dataclasses
import os
from collections.abc import Callable, Container, Iterable, Mapping, Sequence

import annotated2ir
import chars
import gate
import ir
import spaces
import wire

# The paths into a production, as `_asked_where_entered` works them out. A path is the set of guards a parse asks on
# entering the production.
_Paths = frozenset[frozenset[ir.Node]]

# A production name mapped to the paths into that production.
_Entered = dict[str, _Paths]

# Whether a match has a way that takes a character, beside whether it has a way that takes none.
_Split = tuple[bool, bool]

# A production name mapped to the `_Split` of that production.
_Ways = Mapping[str, _Split]

# The guard answers before an action, mapped to the guard answers after the action. The flag says whether the action
# took a character.
_Moved = tuple[dict[spaces.GuardAnswers, frozenset[spaces.GuardAnswers]], bool]


@dataclasses.dataclass(frozen=True)
class _Invariant:
    """
    An invariant names a property the grammar must satisfy and counts the places where the grammar breaks that property.

    A step naming an invariant that another step names works the count of that invariant rather than a count of its own.
    `test` takes a grammar and gives back an error string per place the grammar breaks the invariant. The list's length
    is the count, and the strings say where.

    A test asks about a grammar rather than changing it. The counting pass can therefore hand the same grammars to the
    invariants at once, in the order the cores take them. Calling an invariant reads the grammar before and after its
    test runs, and holds the test to asking rather than changing. A production is frozen. A grammar can differ only
    where a test added, removed or rebound a production in it. The call raises on such a test rather than handing back
    its answer.
    """

    name: str
    test: Callable[[dict[str, ir.Prod]], list[str]]

    def __call__(self, grammar: dict[str, ir.Prod]) -> list[str]:
        """
        Run the test over `grammar` and answer with what it faults. The call raises where the test added, removed or
        rebound a production.
        """
        held = {name: id(production) for name, production in grammar.items()}
        try:
            return self.test(grammar)
        finally:
            # The check runs even where the test raises. A change on the way out would otherwise go unseen.
            if {name: id(production) for name, production in grammar.items()} != held:
                raise AssertionError(f"`{self.name}` changed the grammar rather than reading it")


# A selection giving a sequence or an alternation as a group of items. It gives `None` for a node of another kind.
_AsAGroup = Callable[[ir.Node], ir.SeqTree | ir.AltTree | None]


class _Namer:
    """
    The namer mints fresh helper-production names of the form `<base>_<N>`. The `<N>` is the next count unused for that
    base.

    The steps thread a single namer through, and a base's count runs across them. A helper minted for `foo` takes the
    name `foo_1`, and `foo_2` follows. A helper minted while a later step processes `foo_3` comes out `foo_4` rather
    than `foo_3_1`. The base is `foo` with an `_<N>` suffix stripped. A pair of steps minting for the same base do not
    collide. The namer mints the pairs the scope actions name as well, on a single count for the whole pipeline rather
    than per base.

    The namer hands a name out once. The namer also hands out no name that a grammar it has seen already holds. A
    grammar the namer sees goes into `_taken`, and the count skips what is there.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._taken: set[str] = set()
        self._pairs = 0

    def sees(self, grammar: Mapping[str, ir.Prod]) -> None:
        """
        Take `grammar`'s names as ones not to hand out.
        """
        self._taken.update(grammar)

    def pair(self) -> frozenset[int]:
        """
        A fresh pair identifier, wrapped in the frozen set both its halves name.

        A single count serves the pairs. The count does not run per kind, per step, or per base. The names count per
        base. A name has to differ from the names of its own base. A pair has to differ from the other pairs. A
        `PushCodeAction` pair cannot collide with a `PushIndentAction` pair even by accident.
        """
        self._pairs += 1
        return frozenset({self._pairs})

    def fresh(self, owner: str) -> str:
        """A fresh `<base>_<N>` name for a helper of `owner`. The base is `owner` without its `_<N>` suffix."""
        head, _underscore, tail = owner.rpartition("_")
        base = head if tail.isdigit() and head else owner
        while True:
            self._counts[base] = self._counts.get(base, 0) + 1
            minted = f"{base}_{self._counts[base]}"
            if minted not in self._taken:
                self._taken.add(minted)
                return minted


def _branch(node: ir.CaseTree | ir.FlipValue, value: object) -> ir.Node:
    """
    The item of the `(case)`/`(flip)` `node`'s branch for `value`. The `else` default answers where no branch names the
    value.
    """
    for branch in node.branches:
        if branch.value == value:
            return branch.item
    default = getattr(node, "default", None)
    if default is not None:
        return default
    raise ValueError(f"{node.var} has no branch for {value!r}")


def _is_using(node: object, param: str) -> bool:
    """
    Whether a `ParamValue` named `param` appears anywhere in `node`. This walks the fields itself.
    """
    if not isinstance(node, ir.Node):
        return False
    named = ir.AS_A_PARAMETER(node)
    if named is not None:
        return named.name == param
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, ir.Node):
            if _is_using(value, param):
                return True
        elif isinstance(value, tuple):
            if any(isinstance(item, ir.Node) and _is_using(item, param) for item in value):
                return True
    return False


def _substitute(node: ir.Node, param: str, value: str) -> ir.Node:
    """`node` with `ParamValue(param)` replaced by `LitValue(value)`."""
    named = ir.AS_A_PARAMETER(node)
    if named is not None and named.name == param:
        return ir.LitValue(value)
    return ir.rebuilt(node, lambda child: _substitute(child, param, value))


def _finite_setter(node: ir.Node) -> tuple[str, list[str], dict[object, ir.Node]] | None:
    """
    `(param, [value, ...], {value: condition})` for a `node` that alternates `BindTree`s. The `BindTree`s match a
    condition and set a single finite parameter to a literal. That shape is a data-dependent setter of a finite
    parameter. Anything else is `None`. The values run in the alternation's order. The choice that lifts the setter
    tries them in that order.
    """
    alternation = ir.AS_AN_ALTERNATION(node)
    if alternation is None or not alternation.items:
        return None
    binds = [bind for item in alternation.items if (bind := ir.AS_A_BINDING(item)) is not None]
    if len(binds) != len(alternation.items):
        return None
    params = {bind.param for bind in binds}
    written = [bind.value for bind in binds]
    if len(params) != 1 or not all(_IS_A_LITERAL(one) for one in written):
        return None
    (param,) = params
    if param not in ir.FINITE_PARAMS:
        return None
    values = [one for one in (getattr(said, "value") for said in written) if isinstance(one, str)]
    if len(values) != len(written):
        return None
    return param, values, {said: bind.cond for said, bind in zip(values, binds)}


def _dispatch(body: ir.Node, param: str, values: Sequence[str]) -> ir.Node:
    """
    `body` with the tail that uses `param` replaced by an ordered choice over `values`. A branch substitutes
    `LitValue(value)` for `ParamValue(param)`. The tail runs from the first use of `param` to the end of the top-level
    sequence.
    """
    items = ir.ITEMS_IN_A_SEQUENCE(body)
    first = next(index for index, item in enumerate(items) if _is_using(item, param))
    prefix, tail = items[:first], items[first:]
    choice = ir.AltTree(tuple(ir.SeqTree(tuple(_substitute(item, param, value) for item in tail)) for value in values))
    return ir.SeqTree(prefix + (choice,)) if prefix else choice


def _lift_setters(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Make a data-dependent finite parameter lexical. The parameter then monomorphizes like the context.

    The chomping `t` and the indentation mode `i` are the data-dependent ones. `c-chomping-indicator` sets `t` by
    matching an indicator. `c-indentation-indicator` sets `i` by whether a digit is there. `monomorphize` specializes a
    `(case)`. A setter writes the parameter instead of switching on it. A reader takes the value out of the env.
    `monomorphize` specializes neither. The function inverts a setter into a `(case)` on the setter's parameter. A
    branch of that `(case)` matches the setter's condition for a single value. A production holds the parameter as a
    local out-parameter. The inversion turns that production into an ordered choice over the parameter's values. The
    choice starts at the production's first use of the parameter. A branch fixes the parameter to a literal it hands the
    setter and the reader alike. The parse tries the values in the setter's order. The value whose condition holds
    matches.
    """
    setters = {name: setter for name in grammar if (setter := _finite_setter(grammar[name].body))}
    values = {param: ordered for param, ordered, _conditions in setters.values()}
    result = {}
    for name, production in grammar.items():
        body: ir.Node
        if name in setters:
            param, ordered, conditions = setters[name]
            body = ir.CaseTree(param, tuple(ir.BranchPart(value, conditions[value]) for value in ordered))
        else:
            body = production.body
            for param in ir.FINITE_PARAMS:
                if param in values and param not in production.params and _is_using(body, param):
                    body = _dispatch(body, param, values[param])
        result[name] = dataclasses.replace(production, body=body)
    return result


def _switches_on_a_finite(
    node: ir.CaseTree | ir.FlipValue,
    _grammar: dict[str, ir.Prod],
    direct: set[str],
    _references: list[tuple[str, set[str]]],
) -> None:
    """Add the finite parameter `node` switches on to `direct`."""
    if node.var in ir.FINITE_PARAMS:
        direct.add(node.var)


def _reads_a_finite(
    node: ir.ParamValue, _grammar: dict[str, ir.Prod], direct: set[str], _references: list[tuple[str, set[str]]]
) -> None:
    """Add the finite parameter `node` names to `direct`."""
    if node.name in ir.FINITE_PARAMS:
        direct.add(node.name)


def _passes_a_finite(
    node: ir.RefCall, grammar: dict[str, ir.Prod], _direct: set[str], references: list[tuple[str, set[str]]]
) -> None:
    """Append to `references` the callee `node` names, with the finite parameters `node` hands an argument for."""
    passed = {p for p, _argument in zip(grammar[node.name].params, node.args) if p in ir.FINITE_PARAMS}
    references.append((node.name, passed))


_READS_A_FINITE: ir.Question[None] = ir.Question(
    "nothing, adding to `direct` the finite parameters a node reads and to `references` the calls it makes",
    {
        (ir.CaseTree, ir.FlipValue): _switches_on_a_finite,
        ir.ParamValue: _reads_a_finite,
        ir.RefCall: _passes_a_finite,
        (
            *(kind for kind in ir.CONSUMES_NOTHING if kind is not ir.EmptyTree),
            *ir.CONSUMING,
            *(kind for kind in ir.VALUE_KINDS if kind not in (ir.FlipValue, ir.ParamValue)),
            *ir.WRAPPERS,
            *(kind for kind in ir.TREES if kind is not ir.CaseTree),
            *ir.STATES,
            *ir.PARTS,
        ): lambda node, grammar, direct, references: None,
    },
)


def _gather_finite(
    node: ir.Node, grammar: dict[str, ir.Prod], direct: set[str], references: list[tuple[str, set[str]]]
) -> None:
    """
    Walk `node` and the nodes `ir.rebuilt` visits under it. Add the finite parameters those nodes read to `direct`. Add
    the calls those nodes make to `references`.
    """
    _READS_A_FINITE(node, grammar, direct, references)

    def asked(child: ir.Node) -> ir.Node:
        _gather_finite(child, grammar, direct, references)
        return child

    ir.rebuilt(node, asked)


def _relevant_finite(grammar: dict[str, ir.Prod]) -> dict[str, set[str]]:
    """
    The finite parameters a production's specialized subtree depends on. A monomorphic copy must fix those in its name.
    A production reads some in its body directly, through a `CaseTree` or `FlipValue` on a parameter, or through a
    parameter passed as itself. A production also inherits parameters and hands them down. A callee's relevant
    parameters are therefore relevant to the caller too, save the parameters the caller passes an argument for. This is
    a least fixed point. A reference can reach back to its own production.
    """
    reads: dict[str, set[str]] = {}
    calls: dict[str, list[tuple[str, set[str]]]] = {}
    for name, production in grammar.items():
        direct: set[str] = set()
        references: list[tuple[str, set[str]]] = []
        _gather_finite(production.body, grammar, direct, references)  # noqa: E501
        reads[name], calls[name] = direct, references

    def answer(name: str, known: Mapping[str, frozenset[str]]) -> frozenset[str]:
        return frozenset(reads[name]).union(*(known[callee] - passed for callee, passed in calls[name]))

    relevant = ir.fixed_point(
        "the finite parameters a production's specialized subtree depends on",
        {name: frozenset(direct) for name, direct in reads.items()},
        answer,
        _inverted({name: [callee for callee, _passed in held] for name, held in calls.items()}),
    )
    return {name: set(held) for name, held in relevant.items()}


def _finite_value(expression: ir.Node, grammar: dict[str, ir.Prod], env: Mapping[str, object]) -> str | None:
    """
    A finite value expression over a parameter `ir.FINITE_PARAMS` names, as its concrete value under `env`. This inlines
    a value function the way a call to it would. A value function such as `in-flow` maps a context to another.
    """
    return _FINITE_VALUE(expression, grammar, env)


def _finite_value_of_call(node: ir.RefCall, grammar: dict[str, ir.Prod], env: Mapping[str, object]) -> str | None:
    """A call's value is the callee's body under the values this call hands it."""
    callee = grammar[node.name]
    inner = {parameter: _finite_value(argument, grammar, env) for parameter, argument in zip(callee.params, node.args)}
    return _finite_value(callee.body, grammar, inner)


# A node that settles a finite parameter. That is the parameter itself or a literal. A switch over a parameter settles a
# parameter too. A call to a production that is a value function settles a parameter as well. A kind absent from here
# settles no finite value. The walk raises on such a kind rather than guessing a value.
_FINITE_VALUE: ir.Question[str | None] = ir.Question(
    "the string a finite parameter is settled to by an expression, and `None` where the expression settles none",
    {
        ir.ParamValue: lambda node, grammar, env: env[node.name],
        ir.LitValue: lambda node, grammar, env: node.value,
        ir.FlipValue: lambda node, grammar, env: _finite_value(_branch(node, env[node.var]), grammar, env),
        ir.RefCall: _finite_value_of_call,
    },
)


def _runtime_value_of_call(expression: ir.RefCall, grammar: dict[str, ir.Prod], env: Mapping[str, object]) -> ir.Node:
    """The value of a call. That is the callee's body under the arguments the call hands it."""
    callee = grammar[expression.name]
    inner = {
        parameter: (
            _finite_value(argument, grammar, env)
            if parameter in ir.FINITE_PARAMS
            else _runtime_value(argument, grammar, env)
        )
        for parameter, argument in zip(callee.params, expression.args)
    }
    return _runtime_value(callee.body, grammar, inner)


def _runtime_value_of_parameter(
    expression: ir.ParamValue, _grammar: dict[str, ir.Prod], env: Mapping[str, object]
) -> ir.Node:
    """The node `env` binds a parameter to. A parameter `env` leaves unbound stays."""
    held = env.get(expression.name, expression)
    assert isinstance(held, ir.Node), f"`{expression.name}` holds {held!r} rather than a node"
    return held


# A runtime argument reduced under the values a copy fixes. A call, a switch and a parameter reduce. A node of another
# kind keeps its form. The reduction reaches the held nodes that `ir.rebuilt` visits.
_RUNTIME_VALUE: ir.Question[ir.Node] = ir.Question(
    "a runtime argument reduced under the values a copy fixes",
    {
        ir.RefCall: _runtime_value_of_call,
        ir.FlipValue: lambda expression, grammar, env: _runtime_value(
            _branch(expression, env[expression.var]), grammar, env
        ),
        ir.ParamValue: _runtime_value_of_parameter,
        ir.kinds_other_than(ir.RefCall, ir.FlipValue, ir.ParamValue): lambda expression, grammar, env: ir.rebuilt(
            expression, lambda inner: _runtime_value(inner, grammar, env)
        ),
    },
)


def _runtime_value(expression: ir.Node, grammar: dict[str, ir.Prod], env: Mapping[str, object]) -> ir.Node:
    """Reduce a runtime argument under `env`. The arithmetic over `n`, `m` and `f` stays."""
    return _RUNTIME_VALUE(expression, grammar, env)


@dataclasses.dataclass
class _Specializing:
    """
    The state a walk of `_monomorphize` shares. `relevant` names the finite parameters a production's subtree reads.
    `pending` holds a copy still to make. A call asks for one, and `_monomorphize` seeds a copy of the root per resume
    policy. A copy there is a name and the values the copy fixes.
    """

    grammar: dict[str, ir.Prod]
    relevant: dict[str, set[str]]
    pending: list[tuple[str, dict[str, object]]]


def _specialized_case(node: ir.CaseTree, env: Mapping[str, object], walk: _Specializing) -> ir.Node:
    """
    `_specialized_case` picks the branch matching the value `env` gives the parameter. A case on a parameter outside
    `ir.FINITE_PARAMS` keeps its form.

    An unmatched value takes the case's default. An unmatched case with no default raises `ValueError`. That error marks
    a grammar saying nothing. An `ir.FailTree` matches nothing instead, as `_MATCHED` answers false for it.
    """
    if node.var not in ir.FINITE_PARAMS:
        return ir.rebuilt(node, lambda child: _specialized(child, env, walk))
    value = env.get(node.var)
    for branch in node.branches:
        if branch.value == value:
            return _specialized(branch.item, env, walk)
    if node.default is not None:
        return _specialized(node.default, env, walk)
    raise ValueError(f"the case on {node.var} has no branch for {value!r} and no default")


def _specialized_call(node: ir.RefCall, env: Mapping[str, object], walk: _Specializing) -> ir.Node:
    """
    A call of the callee's copy. The copy fixes the finite values the call passes, and inherits the ambient ones.

    The copy binds the finite parameters its subtree reads. An unset parameter binds `None`. The copy's name leaves out
    a parameter bound to `None` or to its default.
    """
    passed, args = {}, []
    for parameter, argument in zip(walk.grammar[node.name].params, node.args):
        if parameter in ir.FINITE_PARAMS:
            passed[parameter] = _finite_value(argument, walk.grammar, env)
        else:
            args.append(_runtime_value(argument, walk.grammar, env))
    ambient = {x: passed[x] if x in passed else env.get(x) for x in walk.relevant[node.name]}
    walk.pending.append((node.name, ambient))
    return ir.RefCall(ir.specialized(node.name, ambient), tuple(args))


# A node of a copy that fixes some finite values. A case and a call specialize. A node of another kind keeps its form.
# The walk reaches the nodes such a node holds.
_SPECIALIZED: ir.Question[ir.Node] = ir.Question(
    "a node of a copy that fixes some finite values",
    {
        ir.CaseTree: _specialized_case,
        ir.RefCall: _specialized_call,
        ir.kinds_other_than(ir.CaseTree, ir.RefCall): lambda node, env, walk: ir.rebuilt(
            node, lambda child: _specialized(child, env, walk)
        ),
    },
)


def _specialized(node: ir.Node, env: Mapping[str, object], walk: _Specializing) -> ir.Node:
    """`node` in the copy that fixes the values of `env`."""
    return _SPECIALIZED(node, env, walk)


def _monomorphize(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Specialize the lexical finite parameters away. `ir.FINITE_PARAMS` lists them. A walk from the root follows
    references and finds the combinations of finite parameter values a production occurs under. `_monomorphize` copies
    the production once per combination. A copy evaluates its `CaseTree` down to the values that copy fixes. The name
    becomes `ir.specialized`, and the signature drops those parameters. `n`, `m` and `f` stay parameters.
    """
    result: dict[str, ir.Prod] = {}
    done: set[str] = set()
    pending: list[tuple[str, dict[str, object]]] = []
    relevant = _relevant_finite(grammar)
    walk = _Specializing(grammar, relevant, pending)
    # `_monomorphize` seeds a copy of the root per resume policy `annotated2ir.RESUMES` lists.
    for resume in annotated2ir.RESUMES:
        pending.append((ir.ROOT, {x: resume if x == "r" else ir.FINITE_DEFAULTS.get(x) for x in relevant[ir.ROOT]}))
    while pending:
        name, ambient = pending.pop()
        new_name = ir.specialized(name, ambient)
        if new_name in done:
            continue
        done.add(new_name)
        production = grammar[name]
        body = _specialized(production.body, ambient, walk)
        params = tuple(parameter for parameter in production.params if parameter not in ir.FINITE_PARAMS)
        result[new_name] = ir.Prod(production.number, new_name, params, body)
    return result


def _does_fail_outright(items: Iterable[ir.Node]) -> bool:
    """
    Whether a run of items can take no input at all. That is a `<fail>` among them that nothing has committed in front
    of.

    Past a cut, `interpreter._try_cut` turns a refusal into the error the cut names. A run whose `<fail>` sits behind a
    cut therefore fails differently from a run no input takes. Taking the first for the second would turn a committed
    error into a refusal the parse backtracks past.
    """
    for item in items:
        if ir.AS_A_CUT(item) is not None:
            return False
        if ir.AS_A_FAIL(item) is not None:
            return True
    return False


def _pruned(node: ir.Node, failing: set[str]) -> ir.Node:
    """
    `_pruned` rewrites the parts of `node` first. `_PRUNED` then rewrites `node` itself.

    `_PRUNED` rewrites a shape holding a `<fail>` and a call of a failing production. Any other shape comes back
    unchanged. A choice drops its `<fail>` ways. A choice left with no way becomes a `<fail>`. A run becomes a `<fail>`
    where a `<fail>` comes before any cut. An option or a star around a `<fail>` becomes the empty match. A plus or a
    counted repetition around a `<fail>` becomes a `<fail>`. A binding around a `<fail>` becomes a `<fail>`. A wrapper
    around a `<fail>` also becomes a `<fail>`. A call of a failing production becomes a `<fail>`.

    A recovery with a `<fail>` handler becomes its protected match. A cut below such a recovery unwinds to an outer
    handler, as `interpreter._try_open_recovery` shows. `_PRUNED` keeps a commit unchanged. `interpreter._try_committed`
    turns a refusal of the committed match into the error that commit names, where no cut below has named its own error.
    `interpreter._try_open_recovery` gives a recovery's handler a turn only where a cut below it has failed.
    """
    return _PRUNED(ir.rebuilt(node, lambda child: _pruned(child, failing)), failing)


def _does_hold_a_fail(node: ir.Node) -> bool:
    """Whether the match `node` holds inside it is `<fail>`."""
    held = _repeated(node)
    return held is not None and ir.AS_A_FAIL(held) is not None


def _pruned_alternation(node: ir.AltTree, _failing: set[str]) -> ir.Node:
    """`node` without the ways that match nothing. A choice left with no way matches nothing."""
    items = tuple(item for item in node.items if ir.AS_A_FAIL(item) is None)
    return dataclasses.replace(node, items=items) if items else ir.FailTree()


# A part of a body without its ways that match nothing. `_pruned` says what a kind becomes.
_PRUNED: ir.Question[ir.Node] = ir.Question(
    "a part of a body with its ways that match nothing taken out",
    {
        ir.RecoverWrapper: lambda node, _failing: node.item if ir.AS_A_FAIL(node.recovery) is not None else node,
        ir.CommitWrapper: lambda node, _failing: node,
        ir.RefCall: lambda node, failing: ir.FailTree() if node.name in failing else node,
        ir.AltTree: _pruned_alternation,
        ir.SeqTree: lambda node, _failing: ir.FailTree() if _does_fail_outright(node.items) else node,
        (ir.OptTree, ir.StarTree, ir.TrimStarTree): lambda node, _failing: (
            ir.EmptyTree() if _does_hold_a_fail(node) else node
        ),
        (ir.PlusTree, ir.RepTree, ir.BindTree, ir.MaxWrapper, ir.TokenWrapper, ir.Wrapper): lambda node, _failing: (
            ir.FailTree() if _does_hold_a_fail(node) else node
        ),
        ir.kinds_other_than(
            ir.RecoverWrapper,
            ir.CommitWrapper,
            ir.RefCall,
            ir.AltTree,
            ir.SeqTree,
            ir.OptTree,
            ir.StarTree,
            ir.TrimStarTree,
            ir.PlusTree,
            ir.RepTree,
            ir.BindTree,
            ir.MaxWrapper,
            ir.TokenWrapper,
            ir.Wrapper,
        ): lambda node, _failing: node,
    },
)


def _repeated(node: ir.Node) -> ir.Node | None:
    """`_repeated` returns the match a node holds inside it. `_REPEATED` lists the node kinds holding a match."""
    return _REPEATED(node)


# The kinds where a `<fail>` can be the entire match they hold. A commit and a recovery handle a refusal themselves and
# do not reach here. A kind missing from the table raises rather than guessing at an `item` the kind may not have. A
# `TrimStarTree` holds its match in `full`. A `BindTree` holds its match in `cond`.
_REPEATED: ir.Question[ir.Node | None] = ir.Question(
    "the match a node holds inside it",
    {
        ir.BindTree: lambda node: node.cond,
        ir.TrimStarTree: lambda node: node.full,
        (
            ir.MaxWrapper,
            ir.OptTree,
            ir.PlusTree,
            ir.RepTree,
            ir.StarTree,
            ir.TokenWrapper,
            ir.Wrapper,
        ): lambda node: node.item,
    },
)


def _prune_failures(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Take out the matches nothing makes. A way the parse offers is then a way some input takes.

    `_monomorphize` puts such matches into the grammar. A `(case)` naming `<fail>` for a value leaves that value's copy
    unable to match. A caller offering that copy as a way offers a way nothing enters.

    This is a fixed point over the productions. A body that becomes `<fail>` makes a call of it fail too.
    """

    callers = _callers(grammar)
    callees = _inverted(callers)

    def answer(name: str, known: Mapping[str, ir.Node]) -> ir.Node:
        failing = {callee for callee in callees.get(name, ()) if ir.AS_A_FAIL(known[callee]) is not None}
        return _pruned(grammar[name].body, failing)

    bodies = ir.fixed_point(
        "the ways nothing takes",
        {name: production.body for name, production in grammar.items()},
        answer,
        callers,
    )
    return {name: dataclasses.replace(production, body=bodies[name]) for name, production in grammar.items()}


def _bound(node: ir.Node, mapping: Mapping[str, ir.Node]) -> ir.Node:
    """
    `node` with the `ParamValue`s the `mapping` names replaced by their expressions. A call then substitutes lexically.
    This walks the fields itself.
    """
    if not isinstance(node, ir.Node):
        return node
    named = ir.AS_A_PARAMETER(node)
    if named is not None:
        return mapping.get(named.name, named)
    changed: dict[str, object] = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, ir.Node):
            changed[field.name] = _bound(value, mapping)
        elif isinstance(value, tuple) and value and all(isinstance(item, ir.Node) for item in value):
            changed[field.name] = tuple(_bound(item, mapping) for item in value)
    return dataclasses.replace(node, **changed) if changed else node


def _named(held: "_Invariant | tuple[_Invariant, ...]") -> tuple["_Invariant", ...]:
    """`held` as a tuple of invariants. A single invariant becomes the tuple holding it."""
    return (held,) if isinstance(held, _Invariant) else tuple(held)


# The rules a pipeline step must satisfy. `_Step` says what a declaration means. The rules here say how to arrive at a
# step. A step has an argument in a comment directly above it. The argument answers these rules. `step_arguments`
# refuses a step with no such comment.
#
# **A single thing per step.** A writer splits a step doing more than that. A split changes no grammar. A split buys a
# name on the corpus diff and a smaller rule to prove by eye. The pipeline, `STEPS`, is a list. A split costs nothing.
#
# **Make a pair of things alike first. Then compare with `==`.** A factoring may miss a shared prefix between a pair of
# ways. The step hoisting the shared part into a prefix is then missing. An equivalence rule that knows what a pair of
# different-looking chains mean is the wrong shape. Nobody can prove such a rule by eye. Such a rule hides a canonical
# form nobody had written down. A simplification that looks impossible is a missing normalizing step first, and an
# inherent conflict second.
#
# **A step singles out no production by name.** A step names a universal rule and applies it wherever that rule holds. A
# step does not name the productions it applies to. Ordering tempts a writer to name a site. Under
# backtracking-with-commits the order of the alternatives is semantics. A step that cannot reach a form where the
# orderings compare equal asks what universal rule is missing, rather than which site to name.
#
# **A transformation is local and mechanical.** A step reads its own production's nodes, plus the grammar-wide tables
# that are themselves defined production by production. The correctness argument rests on that and no more. A step may
# not lean on a global property of the parse, even a property true by construction. The right rule states the same fact
# locally, in a device the grammar already owns.
#
# **What a transform produces is a global or a stack entry.** The parse holds a global singleton, possibly empty, or an
# entry in the unified stack. A value fitting neither is a value the C parser cannot hold. A step producing such a value
# has produced something that cannot run, and the corpus has no say in it.
#
# **Read the transform by hand against the semantics of the nodes it moves.** The gates are a net rather than a
# substitute. A transformation that moves an action into another production can change nothing the corpus sees. That
# happens wherever the sites it hits are identities. The transformation stays wrong at the next site.
#
# **The order of design is the invariant, then the measurement, then the way to achieve it.** The test comes before the
# transform, and somebody watches the test fail. A test that nobody saw fail proves nothing.
@dataclasses.dataclass(frozen=True)
class _Step:
    """
    A single step of the pipeline.

    A step transforms the grammar and declares what it does to an invariant's count. `settles` means the step drives
    that count to none. The grammar the step gets breaks the invariant. `establishes` means the count reads none
    afterwards, and the invariant made no sense before the step ran. `reduces` means the step lowers a count that later
    steps lower further.

    A step declares `settles`, `establishes` and `reduces` directly, and derives none from another. Steps may share an
    `_Invariant` and a `transform` where they do the same work on different grounds.

    `lapses` maps an invariant name to a reason. It licenses this step to break what an earlier step settled.
    `invariant_faults` holds a step to its declarations. A settling step ends at none. A count does not rise, and a
    count at none stays there. `invariant_faults` also compares those declarations against what the counts did.

    Steps match invariants by name. Steps naming a single invariant work a single count. A misspelt name starts a count
    of its own.

    `untestable` says why a step checks nothing. A step names an invariant or names a reason. Naming both is a fault.
    Naming neither is temporary, and `untested_steps` counts those.

    A `transform` of `None` makes the step a claim. It changes no grammar. It marks where an invariant already reads
    none.
    """

    name: str
    transform: Callable[[dict[str, ir.Prod], "_Namer"], dict[str, ir.Prod]] | None = None
    settles: "_Invariant | tuple[_Invariant, ...]" = ()
    reduces: "_Invariant | tuple[_Invariant, ...]" = ()
    establishes: "_Invariant | tuple[_Invariant, ...]" = ()
    lapses: dict[str, str] = dataclasses.field(default_factory=dict)
    untestable: str = ""

    def __post_init__(self) -> None:
        """Take the invariants this step names as a tuple. `_named` reads a lone invariant and a tuple alike."""
        for field in ("settles", "reduces", "establishes"):
            object.__setattr__(self, field, _named(getattr(self, field)))

    def does_settle(self, invariant: _Invariant) -> bool:
        """Whether this step says it takes `invariant`'s count to none."""
        return invariant.name in {held.name for held in _named(self.settles)}

    def does_establish(self, invariant: _Invariant) -> bool:
        """
        Whether this step says `invariant` holds of the grammar the step hands on. The invariant was no question before
        the step ran.
        """
        return invariant.name in {held.name for held in _named(self.establishes)}

    def does_finish(self, invariant: _Invariant) -> bool:
        """Whether this step says `invariant` reads none in what it hands on."""
        return self.does_settle(invariant) or self.does_establish(invariant)

    def does_reduce(self, invariant: _Invariant) -> bool:
        """Whether this step says it lowers `invariant`'s count without driving that count to none."""
        return invariant.name in {held.name for held in _named(self.reduces)}

    @property
    def is_a_claim(self) -> bool:
        """Whether the step changes no grammar."""
        return self.transform is None

    @property
    def invariants(self) -> tuple[_Invariant, ...]:
        """The invariants this step names."""
        return (*_named(self.settles), *_named(self.reduces), *_named(self.establishes))


@dataclasses.dataclass(frozen=True)
class _Phase:
    """
    A phase of the pipeline, and the steps that serve it.

    `settles` names the invariants the phase settles. `phase_faults` reports an invariant under `settles` that no step
    of the phase settles or establishes. `establishes` names what the grammar already holds where the phase opens.
    `flattened` puts that claim in front of the steps. A phase that names nothing under either field raises.

    `steps` runs in the order written. `STEPS` is the phases flattened.
    """

    name: str
    settles: "_Invariant | tuple[_Invariant, ...]" = ()
    establishes: "_Invariant | tuple[_Invariant, ...]" = ()
    steps: tuple[_Step, ...] = ()

    def __post_init__(self) -> None:
        """
        Take the invariants this phase names as a tuple. `_named` reads a lone invariant and a tuple alike. A phase
        naming nothing under `settles` or `establishes` raises.
        """
        object.__setattr__(self, "establishes", _named(self.establishes))
        object.__setattr__(self, "settles", _named(self.settles))
        if not self.settles and not self.establishes:
            raise AssertionError(f"the `{self.name}` phase names no invariant to settle and none to establish")

    def flattened(self) -> tuple[_Step, ...]:
        """The steps this phase contributes. A phase establishing something claims that before its steps run."""
        if not self.establishes:
            return self.steps
        # The claim establishes what the phase names. It has no transform.
        return (_Step(f"holds-{self.name}", establishes=self.establishes), *self.steps)


def _absent(name: str, *kinds: type) -> _Invariant:
    """
    An `_Invariant` that counts nodes of `kinds` left in the grammar. A lowering step rewrites that shape away. A later
    step may write it back only where that step's `lapses` names the invariant, and `invariant_faults` faults any other.
    A fault names the production and the node class.
    """

    is_lowered: ir.Question[bool] = ir.Question(
        f"whether a node is a shape `{name}` counts", {kinds: True, ir.kinds_other_than(*kinds): False}
    )

    def test(grammar: dict[str, ir.Prod]) -> list[str]:
        faults = []

        def walk(owner: str, node: ir.Node) -> None:
            if is_lowered(node):
                faults.append(f"{owner}: a {type(node).__name__} survives the step that lowers it")

            def asked(child: ir.Node, holder: str = owner) -> ir.Node:
                walk(holder, child)
                return child

            ir.rebuilt(node, asked)

        for owner, production in grammar.items():
            walk(owner, production.body)
        return faults

    return _Invariant(name, test)


def _hands_a_finite_argument(node: ir.RefCall, grammar: dict[str, ir.Prod], owner: str, faults: list[str]) -> None:
    """Append to `faults` a line per finite parameter `node` hands something other than a literal."""
    callee = grammar.get(node.name)
    for param in ir.FINITE_PARAMS:
        if callee is None or param not in callee.params:
            continue
        position = callee.params.index(param)
        if position < len(node.args) and not _IS_A_LITERAL(node.args[position]):
            faults.append(f"{owner}: hands `{node.name}` a `{param}` that is not a literal")


# Whether a value is a literal. A caller asks this of a call's argument, where a finite parameter must be a literal.
# This question covers a `RefCall` too. The spec writes `in-flow(c)` as a production, and `c-flow-sequence` hands a call
# of it as an argument. That call is no literal. Specialization evaluates it to a value and puts that value in the name
# of the callee's copy.
_IS_A_LITERAL: ir.Question[bool] = ir.Question(
    "whether a value is a literal",
    {
        ir.LitValue: True,
        (*(kind for kind in ir.VALUE_KINDS if kind is not ir.LitValue), ir.RefCall): False,
    },
)

# The question `_finite_argument_faults` asks of a node and of the nodes `ir.rebuilt` hands it.
# `_hands_a_finite_argument` decides the call. The other kinds the table names get nothing back. The table leaves out
# `EmptyTree`, and the question raises on an `EmptyTree`.
_HANDS_A_FINITE_ARGUMENT: ir.Question[None] = ir.Question(
    "nothing, appending to `faults` a line per call handing a finite parameter something other than a literal",
    {
        ir.RefCall: _hands_a_finite_argument,
        (
            *(kind for kind in ir.CONSUMES_NOTHING if kind is not ir.EmptyTree),
            *ir.CONSUMING,
            *ir.VALUE_KINDS,
            *ir.WRAPPERS,
            *ir.TREES,
            *ir.STATES,
            *ir.PARTS,
        ): lambda node, grammar, owner, faults: None,
    },
)


def _finite_argument_faults(node: ir.Node, grammar: dict[str, ir.Prod], owner: str, faults: list[str]) -> None:
    """Ask `node` and what it holds whether a call hands a finite parameter something other than a literal."""
    _HANDS_A_FINITE_ARGUMENT(node, grammar, owner, faults)

    def asked(child: ir.Node) -> ir.Node:
        _finite_argument_faults(child, grammar, owner, faults)
        return child

    ir.rebuilt(node, asked)


def _finite_parameters_are_always_literal(grammar: dict[str, ir.Prod]) -> list[str]:
    """Check that a call hands a finite parameter a literal value."""
    faults: list[str] = []
    for name, production in grammar.items():
        _finite_argument_faults(production.body, grammar, name, faults)
    return faults


_FINITE_PARAMETERS_ARE_ALWAYS_LITERAL = _Invariant(
    "finite-parameters-are-always-literal", _finite_parameters_are_always_literal
)


def untested_steps() -> list[str]:
    """
    The steps naming neither an invariant nor a reason for having none. Such a step promises what nothing checks.

    `_check` reports the steps this returns as faults. A step with a written `untestable` falls outside this count.
    """
    return [step.name for step in STEPS if not step.invariants and not step.untestable]


def phase_faults() -> list[str]:
    """The invariants a phase settles that no step of it settles or establishes, as error strings."""
    faults = []
    for phase in _PHASES:
        for held in _named(phase.settles):
            if not any(step.does_finish(held) for step in phase.steps):
                faults.append(
                    f"[{phase.name}] settles `{held.name}`. The steps of that phase neither settle nor establish it."
                )
    return faults


def _one_count(
    held: tuple[Sequence[tuple[str, dict[str, ir.Prod]]], Mapping[str, _Invariant]], asked: tuple[int, str]
) -> int:
    """The count for a single `(stage, invariant)` pair. A worker that `spread` forks runs the count."""
    built, by_name = held
    at, named = asked
    return _counted(by_name[named], built[at][1])


def _counted_over(
    built: Sequence[tuple[str, dict[str, ir.Prod]]],
    by_name: Mapping[str, _Invariant],
    asked: Sequence[tuple[int, str]] | None = None,
) -> dict[tuple[int, str], int]:
    """
    `{(stage, invariant): count}`. `_counted_over` asks the pairs `asked` names, and shares the asks out over the cores.

    An ask is a pure question about a grammar already built, and the asks run independently. `asked` names the `(stage,
    invariant)` pairs to put. The default is the whole cross-product, and a caller that knows an invariant answers of a
    stage names its own pairs.
    """
    if asked is None:
        asked = [(at, named) for at in range(len(built)) for named in by_name]
    ir.say(f"    counting {len(by_name)} invariant(s) over {len(built)} stage(s) with {os.cpu_count()} workers")
    counts = gate.spread(_one_count, (built, by_name), asked, named=lambda pair: f"[{built[pair[0]][0]}] {pair[1]}")
    return dict(zip(asked, counts))


def _counted(invariant: _Invariant, grammar: dict[str, ir.Prod]) -> int:
    """
    The count of places `grammar` breaks `invariant`.

    `_counted` asks the invariant's question of `grammar`. `_one_count` picks the stage. `ir.Question` raises on a shape
    its mapping leaves out.
    """
    return len(invariant(grammar))


def _does_step_name(step: _Step, named: str) -> bool:
    """Whether `step` names the invariant `named`."""
    return any(one.name == named for one in step.invariants)


def _first_step_naming(named: str) -> int:
    """The index of the first step naming the invariant `named`."""
    return min(at for at, step in enumerate(STEPS) if _does_step_name(step, named))


def invariant_faults(built: Sequence[tuple[str, dict[str, ir.Prod]]]) -> list[str]:
    """
    Return an error string per place the pipeline breaks its own law. The list is empty where the law holds.

    An invariant's `test` counts the places the grammar breaks that invariant. The law over the stages holds this way.
    The count does not rise. The step that settles the invariant takes the count to none. The count stays none past that
    step. A step whose `lapses` names the invariant may break it, and says why. The list names a step that breaks an
    invariant without a lapse as a fault.

    Steps match an invariant by its `name`. Steps naming a single invariant therefore work a single count. A count runs
    from the first stage whose step names it. A step establishing the invariant starts the count a stage later.

    A step names an invariant under `settles`, `reduces` or `establishes`. Take a step settling an invariant whose count
    already read none in the grammar the step got. That step settles nothing. The step lowers no count and establishes
    no invariant. An invariant that already reads none goes in as a claim. The claim says where the invariant holds, and
    pins the invariant on no step.

    `counts` takes a count once. The laws below compare the counts `counts` holds.
    """
    faults, taken = [], set()
    by_name = {held.name: held for step in STEPS for held in step.invariants}
    # The questions about an invariant start at the first step naming it. They start a stage later where that step
    # establishes the invariant.
    asking = {}
    for named in by_name:
        first = _first_step_naming(named)
        asking[named] = first + 1 if STEPS[first].does_establish(by_name[named]) else first
    counts = _counted_over(
        built, by_name, [(at, named) for named, first in asking.items() for at in range(first, len(built))]
    )
    for index, step in enumerate(STEPS):
        if step.invariants and step.untestable:
            faults.append(f"[{step.name}] names an invariant and says it has none. It is one or the other")
        if step.is_a_claim and not step.invariants:
            faults.append(f"[{step.name}] transforms nothing and names nothing")
        if step.is_a_claim and step.lapses:
            faults.append(f"[{step.name}] transforms nothing and declares a lapse. A lapse is a licence to break.")
        for named in step.lapses:
            if named not in by_name:
                faults.append(f"[{step.name}] declares a lapse of `{named}`. The steps do not name that invariant.")
        for held in step.invariants:
            # A step that establishes an invariant must leave the count at none.
            if step.is_a_claim or held in _named(step.reduces) or step.does_establish(held):
                continue
            if counts.get((index, held.name)) == 0:
                faults.append(
                    f"[{step.name}] settles `{held.name}`. The count was already none in the grammar the step read. "
                    f"The step claims rather than changes, and a break here comes from a step behind it."
                )
    for named in sorted(by_name):
        test = by_name[named]
        first = _first_step_naming(named)
        is_settled = False
        count_before: int | None = None
        for index in range(first, len(STEPS)):
            step, label = STEPS[index], built[index + 1][0]
            count = counts[index + 1, named]
            is_licensed = named in step.lapses
            is_broken = (
                (count_before is not None and count > count_before)
                or (is_settled and count)
                or (step.does_finish(test) and count)
            )
            if is_broken and is_licensed:
                taken.add((step.name, named))
            if count_before is not None and count > count_before and not is_licensed:
                faults.append(
                    f"[{label}] `{named}` rises from {count_before} to {count}, and the step declares no lapse"
                )
            if is_settled and count and not is_licensed:
                faults.append(f"[{label}] the step settles `{named}`, leaves it at {count}, and declares no lapse")
            if step.does_finish(test) and count and not is_licensed:
                faults.append(f"[{label}] says it leaves `{named}` at none and leaves {count} behind")
            # `reduces` says a step lowers a count without finishing it.
            if step.does_reduce(test) and not count:
                faults.append(f"[{label}] says it only lowers `{named}` and leaves none behind")
            is_settled = (is_settled or step.does_finish(test)) and not count
            count_before = count
    # Report a step that takes a count to none and does not say so.
    for index, step in enumerate(STEPS):
        for named, test in by_name.items():
            before, after = counts.get((index, named)), counts.get((index + 1, named))
            if before and after == 0 and not step.does_finish(test):
                faults.append(f"[{step.name}] takes `{named}` to none and does not say so")
    # Read from the first step naming the invariant.
    for named, test in by_name.items():
        first = _first_step_naming(named)
        for index in range(first, len(STEPS)):
            step = STEPS[index]
            before, after = counts.get((index, named)), counts.get((index + 1, named))
            if before is not None and after is not None and after < before and not _does_step_name(step, named):
                faults.append(f"[{step.name}] lowers `{named}` from {before} to {after} and does not name it")
    # A lapse licenses a break. A lapse with no break under it is a fault.
    for step in STEPS:
        for named in step.lapses:
            if named in by_name and (step.name, named) not in taken:
                faults.append(f"[{step.name}] declares a lapse of `{named}` and does not break it")
    for index, step in enumerate(STEPS):
        for held in step.invariants:
            before, after = counts.get((index, held.name)), counts.get((index + 1, held.name))
            if before is not None and after and after >= before:
                faults.append(
                    f"[{step.name}] names `{held.name}` and neither lowers it ({before} to {after}) nor leaves none"
                )
    return faults


def unsettled_invariants(grammar: dict[str, ir.Prod]) -> list[tuple[str, int]]:
    """
    The invariants the pipeline names or owes that `grammar` still breaks, as `[(name, count)]` worst first.

    A step may settle an invariant that fails at the end. An invariant settled early and broken later under a declared
    lapse still counts as unsettled. A lapse that no later step repairs is work still owed.

    `OWED` holds the invariants no phase has taken on. This counts them beside the invariants the steps name.
    """
    named = {held.name: held for step in STEPS for held in step.invariants}
    named.update({held.name: held for held in OWED})
    counted = [(name, len(test(grammar))) for name, test in sorted(named.items())]
    return sorted(((name, count) for name, count in counted if count), key=lambda held: -held[1])


def _entered_by_name(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions that a parse enters without a call, as start states of their own. The root has a copy per resume
    policy. The recovery a failed cut lands on has a copy per resume policy. A caller of the library chooses the resume
    policy in `ys_options.resume`. A redirect cannot reach a production no caller names. These therefore keep their own
    names through a cleanup.
    """
    return {
        ir.entry(grammar, name, {"n": -1, "r": resume})[0]
        for name in (ir.ROOT, ir.RECOVER)
        for resume in annotated2ir.RESUMES
    }


def _reachable(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions the parse can enter, and the productions those reach. The parse enters some by name, and a node's
    `references` gives the productions it reaches.
    """
    seen = set()
    worklist = list(_entered_by_name(grammar))
    while worklist:
        name = worklist.pop()
        if name in seen or name not in grammar:
            continue
        seen.add(name)
        worklist.extend(grammar[name].references())
    return seen


def _purged(grammar: dict[str, ir.Prod]) -> dict[str, ir.Prod]:
    """
    `grammar` without the productions no parse can enter. That is dead-production elimination. The walk runs down from
    the copies of the root and of the recovery that `_entered_by_name` gives.
    """
    keep = _reachable(grammar)
    return {name: production for name, production in grammar.items() if name in keep}


def _spliced(grammar: dict[str, ir.Prod], keep: Container[str]) -> tuple[dict[str, ir.Prod], dict[str, str]]:
    """
    `grammar` with its calls to do-nothing productions spliced. A map rides along. It names the callee such a production
    resolves to. Such a production sits outside `keep`. Its body is a lone ungated alternative holding a lone step. The
    alternative has no action, no continuation and no recovery of its own. The production amounts to its callee. A
    reference to the production therefore becomes a reference to that callee. The spliced production's own parameters
    bind to the call's arguments in its body. A chain of such productions collapses in a single pass. A production that
    reaches only itself stays.
    """
    passthroughs = {}
    for name, production in grammar.items():
        body = ir.AS_A_CHOICE(production.body)
        if name in keep or body is None or len(body.alternatives) != 1:
            continue
        [way] = body.alternatives
        if way.gate.guards or way.actions:
            continue
        if way.first is None or way.second is not None or way.recover is not None:
            continue
        passthroughs[name] = way.first
    if not passthroughs:
        return grammar, {}

    def resolved(reference: ir.Node) -> ir.Node:
        seen: set[str] = set()
        while (call := ir.AS_A_CALL(reference)) is not None and call.name in passthroughs and call.name not in seen:
            seen.add(call.name)
            reference = _bound(passthroughs[call.name], dict(zip(grammar[call.name].params, call.args)))
        return reference

    def rewrite(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, rewrite)
        return resolved(node) if ir.AS_A_CALL(node) is not None else node

    swept = {name: dataclasses.replace(p, body=rewrite(p.body)) for name, p in grammar.items()}
    return swept, {name: getattr(resolved(reference), "name") for name, reference in passthroughs.items()}


def _pairs_blanked(body: ir.Node) -> tuple[ir.Node, tuple[frozenset[int], ...]]:
    """
    `(body with every pair blanked, the pairs it held)`, in the order the walk finds them.

    The pair a half belongs to is not what a production does. A pair of productions differing only there behave alike
    and merge. This reads whether the node has a `pair` at all, rather than a list of the kinds that do.
    """
    held: list[frozenset[int]] = []

    def rewrite(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, rewrite)
        if isinstance(node, ir.Node) and any(field.name == "pair" for field in dataclasses.fields(node)):
            held.append(getattr(node, "pair"))
            return _with_pair(node, frozenset())
        return node

    return rewrite(body), tuple(held)


def _with_call(way: ir.AlternativeState, slot: str, called: ir.RefCall | None) -> ir.AlternativeState:
    """`way` with the call in `slot` replaced. The slot is the way's `first` or its `second`."""
    if slot == "first":
        return dataclasses.replace(way, first=called)
    return dataclasses.replace(way, second=called)


def _with_pair(node: ir.Node, pair: frozenset[int]) -> ir.Node:
    """`node` with the pair it holds replaced. The caller has found the field on it."""
    return dataclasses.replace(node, **{"pair": pair})


def _pairs_written(body: ir.Node, pairs: Iterable[frozenset[int]]) -> ir.Node:
    """`body` with `pairs` put back into the halves, in the order `_pairs_blanked` took them out."""
    held = iter(pairs)

    def rewrite(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, rewrite)
        if isinstance(node, ir.Node) and any(field.name == "pair" for field in dataclasses.fields(node)):
            return _with_pair(node, next(held))
        return node

    return rewrite(body)


def _grouped(grammar: dict[str, ir.Prod]) -> dict[str, int]:
    """
    `{name: group}`. Productions that behave alike share a group. Alike is the same parameters and the same body. The
    comparison blanks the `pair` field of the nodes that hold one, such as `PushCodeAction` and `PushIndentAction`. A
    reference means the group of the production it names, rather than the name itself. A pair of loops differing only in
    the names of their helpers then come out alike. So does a pair differing only in which pair they hold. A comparison
    of the bodies as written misses both. The groups are the coarsest partition that stays stable under that comparison.
    The first round puts the productions in a single group. A round splits a group where a difference shows, and the
    rounds end on a round that splits nothing.
    """

    def blanked(body: ir.Node) -> tuple[ir.Node, tuple[str, ...]]:
        """Return `body` with its reference names blanked, beside those names in walk order."""
        held: list[str] = []

        def rewrite(node: ir.Node) -> ir.Node:
            node = ir.rebuilt(node, rewrite)
            call = ir.AS_A_CALL(node)
            if call is None:
                return node
            held.append(call.name)
            return dataclasses.replace(call, name="#")

        return rewrite(body), tuple(held)

    # `shapes` holds the bodies with their pairs and reference names blanked, beside those names.
    shapes = {name: blanked(_pairs_blanked(production.body)[0]) for name, production in grammar.items()}
    ordered = sorted(grammar)
    block = dict.fromkeys(grammar, 0)
    for _round in ir.rounds("the sweep's refinement of duplicate bodies"):
        signatures: dict[object, int] = {}
        refined: dict[str, int] = {}
        for name in ordered:
            shape, held = shapes[name]
            signature = (grammar[name].params, shape, tuple(block.get(one, -1) for one in held))
            refined[name] = signatures.setdefault(signature, len(signatures))
        if refined == block:
            return block
        block = refined
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _merged(grammar: dict[str, ir.Prod], keep: Container[str]) -> tuple[dict[str, ir.Prod], dict[str, str]]:
    """
    Return `(grammar, redirects)`. The returned grammar calls productions that behave alike by a single name. A call to
    a group's member is a call to any other member of that group. A reference to a duplicate therefore becomes a
    reference to the kept production. A production the parse enters by name stays. A redirect cannot reach past that
    name. A group may hold a pair of productions the parse enters by name, and both of those productions stay.

    The result still holds the productions that go. The kept scope actions take the pairs of the productions that went.
    A kept half holds the pairs that the replaced halves held.
    """
    block = _grouped(grammar)
    kept_name: dict[int, str] = {}
    for name in sorted(grammar, key=lambda name: (name not in keep, name)):
        kept_name.setdefault(block[name], name)
    canonical = {
        name: kept_name[block[name]] for name in grammar if name not in keep and kept_name[block[name]] != name
    }
    if not canonical:
        return grammar, {}
    gone: dict[str, list[str]] = {}
    for name, landed in canonical.items():
        gone.setdefault(landed, []).append(name)
    united = {}
    for landed, names in gone.items():
        held = [_pairs_blanked(grammar[one].body)[1] for one in (landed, *names)]
        united[landed] = tuple(frozenset().union(*(one[at] for one in held)) for at in range(len(held[0])))
    swept = {
        name: dataclasses.replace(p, body=_pairs_written(p.body, united[name])) if name in united else p
        for name, p in grammar.items()
    }
    # `Prod.renamed` does the renaming. A node class renames the names its own fields hold.
    return {name: p.renamed(canonical) for name, p in swept.items()}, canonical


def _flattened(node: ir.Node) -> ir.Node:
    """
    `node` with what a transformation leaves in its shape taken out. A sequence or a choice of a single item is that
    item. A nested sequence or choice of the same kind is its items in place. An `<empty>` in a sequence goes. The
    sequence matches at that place and moves nothing.

    The flattening changes nothing the grammar matches or emits. `<empty>` is the continuation itself, and concatenation
    and ordered choice are both associative.

    A choice of *nothing* is not litter and stays, save inside another choice, where it adds no item. It is the path no
    input takes.

    An empty match among a way's actions is litter of the same kind as an empty match in a sequence, and goes on the
    same ground. It takes no character and does nothing. The way matches what the way matched with the empty in place.
    """
    return _FLATTENED(ir.rebuilt(node, _flattened))


def _flattened_way(node: ir.AlternativeState) -> ir.Node:
    """`node` without the empty matches among its actions."""
    actions = tuple(action for action in node.actions if ir.AS_AN_EMPTY(action) is None)
    return node if len(actions) == len(node.actions) else dataclasses.replace(node, actions=actions)


def _flattened_sequence(node: ir.SeqTree) -> ir.Node:
    """`node` with nested sequences spliced in and the empty matches gone. A sequence of a single item is that item."""
    items = tuple(held for item in node.items for held in ir.ITEMS_IN_A_SEQUENCE(item) if ir.AS_AN_EMPTY(held) is None)
    if not items:
        return ir.EmptyTree()
    return items[0] if len(items) == 1 else ir.SeqTree(items=items)


def _flattened_alternation(node: ir.AltTree) -> ir.Node:
    """`node` with nested alternations spliced in. A choice of a single item is that item. A choice of none stays."""
    if not node.items:
        return node
    items = tuple(held for item in node.items for held in ir.ITEMS_IN_AN_ALTERNATION(item))
    return items[0] if len(items) == 1 else ir.AltTree(items=items)


# A node with the litter of a transformation taken out of its shape. `_flattened` asks it of a node after `rebuilt`
# flattens the node's children. Any node but a way, a sequence or a choice stays unchanged.
_FLATTENED: ir.Question[ir.Node] = ir.Question(
    "a node with the litter of a transformation taken out of its shape",
    {
        ir.AlternativeState: _flattened_way,
        ir.SeqTree: _flattened_sequence,
        ir.AltTree: _flattened_alternation,
        ir.kinds_other_than(ir.AlternativeState, ir.SeqTree, ir.AltTree): lambda node: node,
    },
)


def _cleaned(grammar: dict[str, ir.Prod], namer: _Namer | None = None) -> dict[str, ir.Prod]:
    """
    `grammar` with what a transformation leaves behind swept up. `_flattened` takes a body down to the shape it denotes.
    `_spliced` points a call to a production that only calls something else at that callee. `_inlined_called_ways`
    writes a callee offering a single way in at the call to it. `_merged` folds together the productions that behave
    alike. `_purged` drops a production no parse can enter. `_purged` runs last and sees what the earlier passes strand.

    Splicing feeds merging. Merging feeds splicing. A merge makes a pair of productions the same call, and a splice
    makes a pair of callers identical. The loop stops at a round that changes nothing. The sweep changes nothing the
    grammar matches or emits. A flattened body denotes what it denoted. A spliced production ran no action and made no
    decision. A merged production is the production kept, and it matches character for character.

    The flattening comes first. The merge reads shape rather than meaning. A pair of productions can say the same thing
    with a singleton alternation in different places. Those are structurally unequal and do not merge. Said flat they
    are the same node. Flattening therefore decides how much work the sweep does. A merge the flattening makes possible
    is a duplicate the sweep existed to find, rather than a duplicate the sweep invented.
    """
    keep = _entered_by_name(grammar)
    for _round in ir.rounds("the sweep of a transformation's leavings"):
        flat = {name: dataclasses.replace(p, body=_flattened(p.body)) for name, p in grammar.items()}
        spliced, _splices = _spliced(flat, keep)
        swept, _merges = _merged(_inlined_called_ways(spliced, namer), keep)
        if swept == grammar:
            return _purged(grammar)
        grammar = swept
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def stages(grammar: dict[str, ir.Prod]) -> list[tuple[str, dict[str, ir.Prod]]]:
    """
    The grammar after a step, as `(label, grammar)` pairs. The list opens with `("base", grammar)`. `check_normalize`
    runs a fixture against the last stage whose grammar can run it. Under `--bisect` it names the first stage that
    breaks the corpus. The steps share a single `_Namer`. A helper production a step mints takes the name `<base>_<N>`,
    and the `_Namer` counts `<N>` across the steps. A sweep cleans a step's grammar of the leavings the step made. The
    sweep flattens the production bodies. It splices out do-nothing productions. It inlines a callee with a single way
    in. It merges duplicates. It drops productions no parse can enter. The sweep does not run on the base grammar.

    A claim is not a step in this sense. It transforms nothing. It joins the list to say where a property already holds.
    A claim therefore repeats the stage it got rather than making a stage.

    A check refuses a step that leaves the grammar unchanged. The check reads the transform's output before the sweep
    runs.
    """
    namer = _Namer()
    namer.sees(grammar)
    result = [("base", grammar)]
    for step in STEPS:
        ir.say(f"    [{step.name}] over {len(grammar)} production(s)")
        if step.is_a_claim:
            result.append((step.name, grammar))  # A claim's stage holds the grammar of the stage before it.
            continue
        transform = step.transform
        assert transform is not None, f"[{step.name}] is no claim and transforms nothing"
        produced = transform(grammar, namer)
        if produced == grammar:
            raise AssertionError(f"the `{step.name}` step changed nothing. The grammar holds no shape it looks for.")
        grammar = _cleaned(produced, namer)
        namer.sees(grammar)
        result.append((step.name, grammar))
    return result


def _every_difference_is_between_character_sets(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that both sides of a `(---)` denote a character set.

    The base must match a single character. A subtracted side must have codepoints the check can work out.
    `chars.denote` reads the text a match takes, and not the code it holds. Annotations around a subtracted character
    therefore do not count against it.
    """
    return [
        f"{name}: a `(---)` takes characters from something that is not a character set"
        for name, production in grammar.items()
        for held in _held(production.body)
        if (node := ir.AS_A_DIFFERENCE(held)) is not None
        and not (
            ir.is_one_char(node.base, grammar) and all(_peek_spans(taken, grammar) is not None for taken in node.minus)
        )
    ]


_EVERY_DIFFERENCE_IS_BETWEEN_CHARACTER_SETS = _Invariant(
    "every-difference-is-between-character-sets", _every_difference_is_between_character_sets
)

# The count a shortest match runs up to. A difference takes a set of single characters. It can therefore remove only a
# match of a single character. Past that, how much more a way takes makes no difference to the reach of the subtraction.
_SHORTEST_CAP = 2


def _shortest_match(node: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str] = frozenset()) -> int:
    """
    The fewest characters `node` can match. The count runs no further than `_SHORTEST_CAP`.

    The count is a lower bound. A difference contributes its base's count. The exclusions only remove matches. A
    production reached again contributes the cap. A match that bottoms out is not the recursive way. `_SHORTEST_MATCH`
    is a `Question` naming the kinds. A `Question` raises on a kind its table leaves out.
    """
    return _SHORTEST_MATCH(node, grammar, seen)


def _shortest_of_ways(ways: Iterable[ir.Node], grammar: dict[str, ir.Prod], seen: frozenset[str]) -> int:
    """
    The fewest a choice can take. That is the least of the ways the choice offers, and the cap where it offers none.
    """
    return min((_shortest_match(way, grammar, seen) for way in ways), default=_SHORTEST_CAP)


def _shortest_counted(count: ir.Node, taken: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str]) -> int:
    """
    A counted repetition repeats a single turn its count times. `_SHORTEST_CAP` caps the length that results. The
    shortest match of a repetition is empty where its count is no positive integer.
    """
    times = getattr(count, "value", None)
    if not isinstance(times, int) or times <= 0:
        return 0
    return min(_SHORTEST_CAP, times * _shortest_match(taken, grammar, seen))


def _shortest_call(node: ir.RefCall, grammar: dict[str, ir.Prod], seen: frozenset[str]) -> int:
    """A call takes the fewest characters its callee takes. A recursion takes the cap."""
    if node.name in seen:
        return _SHORTEST_CAP
    return _shortest_match(grammar[node.name].body, grammar, seen | {node.name})


_SHORTEST_MATCH: ir.Question[int] = ir.Question(
    "the fewest characters a match can take, as an integer counted no further than `_SHORTEST_CAP`",
    {
        # A single character from the set.
        (ir.OneCharSet, ir.CharSet, ir.InvalidSet, ir.RangeSet): lambda node, grammar, seen: 1,
        ir.DiffSet: lambda node, grammar, seen: _shortest_match(node.base, grammar, seen),
        # An action or a guard takes nothing, and a repetition of none or more takes no turn.
        (*ir.CONSUMES_NOTHING, ir.OptTree, ir.StarTree): lambda node, grammar, seen: 0,
        # A consume takes at least a single character.
        ir.ConsumeSpanAction: lambda node, grammar, seen: 1,
        ir.SeqTree: lambda node, grammar, seen: min(
            _SHORTEST_CAP, sum(_shortest_match(item, grammar, seen) for item in node.items)
        ),
        ir.AltTree: lambda node, grammar, seen: _shortest_of_ways(node.items, grammar, seen),
        ir.CaseTree: lambda node, grammar, seen: _shortest_of_ways(
            tuple(branch.item for branch in node.branches) + ((node.default,) if node.default is not None else ()),
            grammar,
            seen,
        ),
        ir.PlusTree: lambda node, grammar, seen: _shortest_match(node.item, grammar, seen),
        ir.RepTree: lambda node, grammar, seen: _shortest_counted(node.count, node.item, grammar, seen),
        # A consume up to a limit takes a single character at least. Naming the limit here would be a lower bound that
        # is too high, and that is the unsound direction.
        ir.ConsumeLimitedSpanAction: lambda node, grammar, seen: 1,
        ir.BindTree: lambda node, grammar, seen: _shortest_match(node.cond, grammar, seen),
        ir.WRAPPERS: lambda node, grammar, seen: 0 if node.item is None else _shortest_match(node.item, grammar, seen),
        ir.RefCall: _shortest_call,
    },
)


def _difference_ways(base: ir.Node, grammar: dict[str, ir.Prod]) -> tuple[ir.Node, ...]:
    """
    The ways `base` offers, and the order is the order a choice writes them in. Those are the items of a choice, read
    through the reference that names it.

    A base that calls a production offers the callee's ways. The production the reference names stays for its own
    callers. A call passing arguments raises rather than splicing without them.
    """
    call = ir.AS_A_CALL(base)
    if call is None:
        return ir.ITEMS_IN_AN_ALTERNATION(base)
    if call.args:
        raise ValueError(f"a `(---)` takes characters from `{call.name}`. That call takes arguments.")
    return _difference_ways(grammar[call.name].body, grammar)


def _taken(ways: Sequence[ir.Node], minus: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
    """
    `ways` as a difference over their union. The answer is empty where `ways` is empty. `ways` is a run of ways taking a
    single character apiece.
    """
    if not ways:
        return ()
    return (ir.DiffSet(base=ways[0] if len(ways) == 1 else ir.AltTree(items=tuple(ways)), minus=minus),)


def _distribute_differences(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Take a difference into the ways of the item it subtracts from. The difference then holds a set and the sets it
    subtracts.

    A difference over a choice is the choice of the differences, and the ways keep their order. `(A | B) - m` is `(A -
    m) | (B - m)`. A run of ways taking a single character apiece takes the subtraction once, as the union those ways
    already write. That leaves a difference of character sets wherever the subtraction reaches anything.

    A way that takes more than a single character keeps its whole language. A subtracted set takes a single character.
    It can remove a match of a single character and nothing wider. The step removes exactly that. `nb-double-char` is an
    escape or a character. `ns-double-char` subtracts the whitespace. The escape alternative `c-ns-esc-char` opens with
    a backslash and matches no whitespace, and the subtraction lands on the character alternative.

    A way that may take a single character without being a set raises rather than getting a guess. The subtraction
    reaches that way and no set says how. A subtraction of anything but single characters raises the same way.
    """

    def distributed(held: ir.Node) -> ir.Node:
        rebuilt = ir.rebuilt(held, distributed)
        node = ir.AS_A_DIFFERENCE(rebuilt)
        if node is None or ir.is_one_char(node.base, grammar):
            return rebuilt
        if any(_peek_spans(taken, grammar) is None for taken in node.minus):
            raise ValueError("a `(---)` subtracts something that is not a character set")
        ways: list[ir.Node] = []
        run: list[ir.Node] = []
        for way in _difference_ways(node.base, grammar):
            if ir.is_one_char(way, grammar):
                run.append(way)
                continue
            if _shortest_match(way, grammar) < _SHORTEST_CAP:
                raise ValueError("a `(---)` reaches a way that may take a single character and is not a set")
            ways.extend(_taken(run, node.minus))
            ways.append(way)
            run = []
        ways.extend(_taken(run, node.minus))
        return ir.AltTree(items=tuple(ways)) if len(ways) != 1 else ways[0]

    return {
        name: dataclasses.replace(production, body=distributed(production.body)) for name, production in grammar.items()
    }


# A subtraction between a pair of sets is a set. The lowering then says that difference as a single `CharSet`. The
# parser gets a bit to test, rather than an algebra to walk.
_NO_DIFF_NODES = _absent("no-diff-nodes", ir.DiffSet)


def _every_peek_is_a_character_set(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a lookaround holds a character set.

    An `(exclude)` is no kind in `PEEKS`, and this check leaves it out.
    """
    return [
        f"{name}: a {type(node).__name__} holds a {type(item).__name__} rather than a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if (item := ir.PEEKED(node)) is not None and ir.AS_A_CHAR_SET(item) is None
    ]


_EVERY_PEEK_IS_A_CHARACTER_SET = _Invariant("every-peek-is-a-character-set", _every_peek_is_a_character_set)


def _peeked_question(node: ir.Node, grammar: dict[str, ir.Prod]) -> ir.Node | None:
    """
    Give back the question a peek of `node` asks. The parts of `node` that only shape the output come off it.

    `interpreter._try_probe` rewinds the tokens a probe emits and gives back only whether the pattern matched. An
    annotation inside a probe is therefore dead. `c-comment` is a `#` under the code its character takes. Peeking
    `c-comment` asks whether the character is a `#`. A peek reads through a name that it calls without arguments. A call
    that passes arguments is the question itself.
    """
    return _PEEKED_QUESTION(node, grammar)


def _asked_through_a_call(node: ir.RefCall, grammar: dict[str, ir.Prod]) -> ir.Node | None:
    """
    A call asks the question the production it names asks.

    A call passing arguments asks what its callee asks under those arguments. The callee's body does not say that
    without them. The question is then the call itself, and a reader of that question holds the name.
    """
    if node.args:
        return node
    return _peeked_question(grammar[node.name].body, grammar)


# A kind `_PEEKED_QUESTION` leaves out raises rather than coming back as its own question. Read that way, a shape nobody
# has looked at says "ask about this" and the reader believes it.
_PEEKED_QUESTION: ir.Question[ir.Node | None] = ir.Question(
    "the node a peek of a node asks about. The answer is the set or the call the peek reads down to, or the node "
    "itself.",
    {
        ir.RefCall: _asked_through_a_call,
        # A `(token)` reads through. `interpreter._try_probe` rewinds the tokens a probe emits.
        (ir.TokenWrapper, ir.Wrapper): lambda node, grammar: _peeked_question(node.item, grammar),
        # A set is already the question, however the grammar wrote it.
        (ir.AltTree, ir.CharSet, ir.DiffSet, ir.OneCharSet): lambda node, grammar: node,
    },
    untested=(ir.Wrapper,),
)


def _lower_char_sets(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Rewrite a character set as a single `CharSet`, the shape the parser asks its question in.

    The grammar writes a set of characters in the ways below. It is a character or a range. It is a union of those, or a
    base with exclusions. A union and a base with exclusions may denote the same characters. The parser tests a key that
    `Model` builds for a bit. The rewrite says the set once here, as the sorted disjoint intervals it denotes. The
    rewrite then leaves a single shape to convert.

    The rewrite takes a maximal set rather than the sets inside it. The intervals of a union belong to the union, and
    nobody asks about them apart. A bare reference stays where a match takes it. Inside a union or an exclusion the
    rewrite reads through the reference. A character set with a production of its own keeps that production, and the
    callers hold the reference. Inside a lookaround the rewrite reads through the reference instead. The rewrite also
    reads through an annotation on what the reference names. A peek holds the question whether the character falls in a
    given set. Neither a name nor a code is a question the machine can put to the input.
    """

    return {
        name: dataclasses.replace(production, body=_LOWERED_CHAR_SETS(production.body, grammar))
        for name, production in grammar.items()
    }


def _lowered_char_set(node: ir.Node, grammar: dict[str, ir.Prod]) -> ir.Node:
    """`node` said as a single `CharSet` where it denotes a set. A node denoting no set gets its parts lowered."""
    said = _as_char_set(node, grammar)
    if said is not None and ir.AS_A_CHAR_SET(said) is not None:
        return said
    return ir.rebuilt(node, lambda child: _LOWERED_CHAR_SETS(child, grammar))


def _lowered_peek(node: ir.LookGuard | ir.LookBehindGuard | ir.NegLookGuard, grammar: dict[str, ir.Prod]) -> ir.Node:
    """
    A lookaround holding the set its question denotes. A question denoting no set leaves the lookaround with its parts
    lowered. The question reads through an annotation and through a reference passing no arguments.
    """
    asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
    if asked is not None and ir.AS_A_CHAR_SET(asked) is not None:
        return dataclasses.replace(node, item=asked)
    return _lowered_char_set(node, grammar)


# The node says a character set as a single `CharSet`. A lookaround reads through an annotation and through a call
# passing no arguments to reach the set its item asks about. A call stays.
_LOWERED_CHAR_SETS: ir.Question[ir.Node] = ir.Question(
    "a node with its character sets said as single sets",
    {
        ir.PEEKS: _lowered_peek,
        ir.RefCall: lambda node, _grammar: node,
        ir.kinds_other_than(*ir.PEEKS, ir.RefCall): _lowered_char_set,
    },
)


def _every_character_question_is_a_character_set(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a question about a character is a `CharSet`. That is the shape the parser can take.

    A question is about a character when what it matches is a single character. That is a union, a difference, or a raw
    character node. A question about a character is also the item a lookaround peeks. A reference is a hold on the
    production where the grammar says the set. A match taking a reference is no fault. Inside a lookaround a reference
    is a fault. A peek holds the question rather than the hold. A guard over more than a single character asks nothing
    about a character, and the check passes it. An `(exclude)` is such a guard. A difference between a pair of
    multi-character productions is such a guard as well.
    """
    faults: list[str] = []
    for name, production in grammar.items():
        _CHARACTER_QUESTIONS(production.body, grammar, name, faults)
    return faults


def _asked_by_a_peek(
    node: ir.LookGuard | ir.NegLookGuard, grammar: dict[str, ir.Prod], owner: str, faults: list[str]
) -> None:
    """
    A peek's question. A peek of a single character must hold a character set. This records a fault for any other peek
    of a single character. A wider question goes to `_asked_where_it_sits`.
    """
    asked = _peeked_question(node.item, grammar)
    assert asked is not None, f"{owner}: a peek asks about nothing"
    if not ir.is_one_char(asked, grammar):
        _asked_where_it_sits(node, grammar, owner, faults)
        return
    if ir.AS_A_CHAR_SET(node.item) is None:
        kinds = (type(node).__name__, type(node.item).__name__)
        faults.append(f"{owner}: a {kinds[0]} asks about a character as a {kinds[1]}")


def _asked_where_it_sits(node: ir.Node, grammar: dict[str, ir.Prod], owner: str, faults: list[str]) -> None:
    """
    The function judges a node no earlier case takes. A match of a single character is a set missing its `CharSet`. The
    function appends a fault for such a match. It walks into a node holding parts.
    """
    if ir.is_one_char(node, grammar):
        faults.append(f"{owner}: a {type(node).__name__} is a character set and is not a `CharSet`")
        return
    ir.rebuilt(node, lambda child: (_CHARACTER_QUESTIONS(child, grammar, owner, faults), child)[1])


# The walk raises on a kind `_CHARACTER_QUESTIONS` leaves out, and does not walk into it.
_CHARACTER_QUESTIONS: ir.Question[None] = ir.Question(
    "nothing, appending to `faults` a line per question about a character written as anything but a `CharSet`",
    {
        # A `CharSet` is the shape the parser takes, and the walk stops there.
        ir.CharSet: lambda node, grammar, owner, faults: None,
        ir.PEEKS: _asked_by_a_peek,
        ir.RefCall: lambda node, grammar, owner, faults: None,
        (
            # `CONSUMES_NOTHING` spans categories. The empty match is a tree, and the names below hold it.
            *(kind for kind in ir.CONSUMES_NOTHING if kind not in (*ir.PEEKS, ir.EmptyTree)),
            *(kind for kind in ir.CONSUMING if kind is not ir.CharSet),
            *ir.VALUE_KINDS,
            *ir.WRAPPERS,
            *ir.TREES,
            *ir.STATES,
            *ir.PARTS,
        ): _asked_where_it_sits,
    },
)


# `_CHARACTER_QUESTIONS` above walks for this invariant. The invariant sits beside that walk rather than beside the
# invariant's checker.
_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET = _Invariant(
    "every-character-question-is-a-character-set", _every_character_question_is_a_character_set
)


def _as_char_set(node: ir.Node | None, grammar: dict[str, ir.Prod]) -> ir.Node | None:
    """
    `node` said as a `CharSet` where `node` is a character set. `node` itself otherwise.

    The lowering step and the code that later builds a gate both ask this function whether `node` is a character set.
    This reads through a reference rather than keeping it. In a peek the reference is the question "is the character one
    of these", and not the hold on a production a match needs. An alternation of such references is a set like any
    other.
    """
    if node is None or not ir.is_one_char(node, grammar):
        return node
    spans = _peek_spans(node, grammar)
    return node if spans is None else _spans_node(spans)


def _spans_node(spans: Iterable[tuple[int, int]]) -> ir.CharSet:
    """
    Answer `spans` as a `CharSet`. A gate peeks at such a set, and a take consumes a character out of one. That is the
    shape a character question takes.

    The rewrite keeps the invalid byte's `(-1, -1)` apart from the characters. The interval is a unit no character class
    holds. It overlaps itself and nothing besides. Coalescing it with a run starting at the lowest codepoint would say
    the parser accepts a character there.
    """
    invalid = [span for span in spans if span[0] < 0]
    return ir.CharSet(tuple([(-1, -1)] * bool(invalid) + chars.merged_spans([s for s in spans if s[0] >= 0])))


def _peek_spans(peek: ir.Node, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]] | None:
    """
    The codepoint intervals `peek` accepts, or `None` where the grammar leaves the set open. The invalid-byte class is
    the interval `(-1, -1)`. That is a unit no character holds, and the unit merges with no codepoint interval. An
    alternation may hold the unit beside character classes. Such an alternation yields the intervals of those classes
    plus the unit.
    """
    return _PEEK_SPANS(peek, grammar)


def _united_peek_spans(peek: ir.AltTree, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]] | None:
    """
    Union the intervals of an alternation rather than denote them. The invalid byte has no denotation. The byte is a
    unit no character holds. An alternation holding the `InvalidSet` node denotes nothing and still admits. A `CharSet`
    drops the byte's interval from its denotation. An alternation may hold the byte as the `InvalidSet` node, or as the
    interval a `CharSet` says it with.
    """
    gathered = []
    for item in peek.items:
        admitted = _peek_spans(item, grammar)
        if admitted is None:
            return None
        gathered += admitted
    invalid = [span for span in gathered if span[0] < 0]
    return [(-1, -1)] * bool(invalid) + chars.merged_spans([span for span in gathered if span[0] >= 0])


def _denoted_peek_spans(peek: ir.Node, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]] | None:
    """A peek's intervals by what it denotes. The answer is `None` where the grammar leaves the set open."""
    denotation = chars.denote(grammar, peek)
    return None if denotation is None else chars.spans(denotation)


_PEEK_SPANS: ir.Question[list[tuple[int, int]] | None] = ir.Question(
    "the codepoint intervals a peek admits, or none where the grammar leaves its set open",
    {
        ir.CharSet: lambda peek, grammar: [tuple(span) for span in peek.spans],
        ir.InvalidSet: [(-1, -1)],
        ir.AltTree: _united_peek_spans,
        (ir.OneCharSet, ir.DiffSet, ir.RangeSet, ir.RefCall): _denoted_peek_spans,
    },
)


def _held(node: ir.Node) -> Iterable[ir.Node]:
    """
    Yield `node` and the nodes its fields hold. This walks the fields rather than going through the generic walker. The
    generic walker `validate_grammar.walk` reaches the same nodes through `chars.children`. Those nodes include a
    `ParamValue` that a field holds directly. `_held` yields that `ParamValue` too.
    """
    yield node
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        for item in value if isinstance(value, tuple) else (value,):
            if isinstance(item, ir.Node):
                yield from _held(item)


def _parameter_uses(grammar: dict[str, ir.Prod], wanted: Iterable[str]) -> list[str]:
    """
    The places that still hold a parameter in `wanted`. Such a place is a production declaring a parameter. It is also a
    call passing an argument at a position where the callee declares a parameter in `wanted`. It is also an expression
    reading a parameter.
    """
    faults = []
    for name, production in grammar.items():
        faults += [f"{name}: declares `{param}`" for param in production.params if param in wanted]
        for node in _held(production.body):
            faults += [f"{name}: {said}" for said in _PARAMETER_USES(node, grammar, frozenset(wanted))]
    return faults


def _passed_parameters(node: ir.RefCall, grammar: dict[str, ir.Prod], wanted: frozenset[str]) -> list[str]:
    """
    Answer phrases of the form "passes `p` to `callee`". A phrase names a parameter of `wanted` that a call passes. The
    phrases follow the positions where the callee declares the parameters.
    """
    callee = grammar.get(node.name)
    declared = () if callee is None else callee.params
    return [
        f"passes `{declared[position]}` to `{node.name}`"
        for position in range(min(len(node.args), len(declared)))
        if declared[position] in wanted
    ]


# A node's uses of the parameters `wanted` names. A call passes a parameter, and a parameter reads itself. A node of
# another kind uses no parameter itself. A walk reaches the nodes such a node holds.
_PARAMETER_USES: ir.Question[list[str]] = ir.Question(
    "a node's uses of a set of parameters",
    {
        ir.RefCall: _passed_parameters,
        ir.ParamValue: lambda node, _grammar, wanted: [f"reads `{node.name}`"] if node.name in wanted else [],
        ir.kinds_other_than(ir.RefCall, ir.ParamValue): lambda node, _grammar, _wanted: [],
    },
)


# `specialize` establishes this invariant, and the phase finishes once it holds. The grammar then holds neither the
# chomping `t` nor the block scalar's indentation mode `i`. A production sets both by matching, and a production further
# away reads them. A read of either means nothing until the reader knows the caller. The same specialization settles
# both.
_NO_I_T_PARAMETERS = _Invariant("no-i-t-parameters", lambda grammar: _parameter_uses(grammar, {"i", "t"}))

# The invariant of `drop-f` covers the block scalar's leading-empty floor.
_NO_F_PARAMETER = _Invariant("no-f-parameter", lambda grammar: _parameter_uses(grammar, {"f"}))

# The invariant of `drop-m` covers the detected indent.
_NO_M_PARAMETER = _Invariant("no-m-parameter", lambda grammar: _parameter_uses(grammar, {"m"}))

# The invariant of `drop-n` covers the indentation. The parse's stack holds it.
_NO_N_PARAMETER = _Invariant("no-n-parameter", lambda grammar: _parameter_uses(grammar, {"n"}))


def _every_indentation_change_is_pushed(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a push and a pop guard a call changing the indentation. Such a call measures against a level, or it
    establishes a level a later item reads.

    The guard forms below serve a measuring call and an establishing call alike.
    """
    faults = []
    establishing = _establishing(grammar)
    for name, production in grammar.items():
        sequences = [sequence for held in _held(production.body) if (sequence := ir.AS_A_SEQUENCE(held)) is not None]
        guarded = set()
        for sequence in sequences:
            for position, item in enumerate(sequence.items):
                before = sequence.items[position - 1] if position else None
                after = sequence.items[position + 1] if position + 1 < len(sequence.items) else None
                is_pushed_before = before is not None and ir.AS_A_PUSH_INDENT(before) is not None
                is_popped_after = after is not None and ir.AS_A_POP_INDENT(after) is not None
                is_pushed_after = after is not None and ir.AS_A_PUSH_INDENT(after) is not None
                is_popped_last = ir.AS_A_POP_INDENT(sequence.items[-1]) is not None
                if is_pushed_before and is_popped_after:
                    guarded.add(id(item))  # A push directly precedes this item, and a pop directly follows it.
                if is_pushed_after and is_popped_last:
                    guarded.add(id(item))  # A push directly follows this item, and a pop ends the sequence.
        for held in _held(production.body):
            call = ir.AS_A_CALL(held)
            if call is None or id(call) in guarded:
                continue
            if _pushed_level(grammar, call) is not None:
                faults.append(f"{name}: calls `{call.name}` against an indentation nothing pushes")
        for sequence in sequences:
            for position, item in enumerate(sequence.items):
                call = ir.AS_A_CALL(item)
                if call is None or id(call) in guarded:
                    continue
                if call.name not in establishing or not _is_by_reference(grammar, call):
                    continue
                if any(_is_using(later, "n") for later in sequence.items[position + 1 :]):
                    faults.append(f"{name}: reads the indentation `{call.name}` establishes where nothing holds it")
    return faults


_EVERY_INDENTATION_CHANGE_IS_PUSHED = _Invariant(
    "every-indentation-change-is-pushed", _every_indentation_change_is_pushed
)


def _replaced(node: ir.Node, swap: Callable[[ir.Node], ir.Node]) -> ir.Node:
    """
    `node` with `swap` applied to `node` and to the fields it holds. This walks the fields itself.

    The generic walker `ir.rebuilt` does not visit a `ParamValue` a field holds directly. `ir._IS_REBUILT_INTO` answers
    no for a `ParamValue`. That is where a read hides. A rewrite of the reads therefore goes through this rather than
    through the generic walker.
    """
    node = swap(node)
    if not isinstance(node, ir.Node):
        return node
    changed: dict[str, object] = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, tuple):
            items = tuple(_replaced(item, swap) if isinstance(item, ir.Node) else item for item in value)
            if items != value:
                changed[field.name] = items
        elif isinstance(value, ir.Node):
            held = _replaced(value, swap)
            if held is not value:
                changed[field.name] = held
    return dataclasses.replace(node, **changed) if changed else node


def _reshaping(what: str, kind: type, reshape: Callable[..., ir.Node]) -> ir.Question[ir.Node]:
    """
    The question reshaping a node of `kind` through `reshape`. `what` says the question in words. A node of another kind
    keeps its form. The values a caller threads through reach `reshape` after the node.
    """
    return ir.Question(what, {kind: reshape, ir.kinds_other_than(kind): lambda node, *_given: node})


def _call_read_off(node: ir.RefCall, _param: str, _held: ir.Node, positions: Mapping[str, int]) -> ir.Node:
    """`node` without the argument at the position `positions` names for its callee."""
    position = positions.get(node.name, len(node.args))
    if position >= len(node.args):
        return node
    return dataclasses.replace(node, args=node.args[:position] + node.args[position + 1 :])


# A node with a parameter taken off. A read of the parameter becomes the node holding its value. A call drops the
# argument at the position `positions` holds for its callee. A call with no such argument keeps its form. A node of
# another kind keeps its form.
_READ_OFF: ir.Question[ir.Node] = ir.Question(
    "a node with a parameter taken off",
    {
        ir.ParamValue: lambda node, param, held, _positions: held if node.name == param else node,
        ir.RefCall: _call_read_off,
        ir.kinds_other_than(ir.ParamValue, ir.RefCall): lambda node, _param, _held, _positions: node,
    },
)


def _read_off(param: str, held: ir.Node) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    Take the declaration of `param` off the productions and its argument off the calls. It turns a read of the parameter
    into `held`. `held` is the node that holds the value. It is a read of a global's stack, or a read of the indentation
    on top of the parse's stack.

    The `(clear)` holds the global to a construct at a time. `interpreter._try_clear` pops the global's stack.
    `interpreter._evaluated_global` raises on a read past the region with an empty stack. A read past the region with an
    outer value on the stack returns that value. `ASKED` counts that read. The transform does not touch the writes. A
    `(set)` and a `(clear)` name the parameter as a string, as `SetVarAction` holds `param`. `interpreter._try_write`
    and `interpreter._try_clear` reach the slot through that string once no production declares it.
    """

    def transform(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
        positions = {
            name: production.params.index(param) for name, production in grammar.items() if param in production.params
        }

        def swap(node: ir.Node) -> ir.Node:
            return _READ_OFF(node, param, held, positions)

        return {
            name: dataclasses.replace(
                production,
                params=tuple(one for one in production.params if one != param),
                body=_replaced(production.body, swap),
            )
            for name, production in grammar.items()
        }

    return transform


def _pushed_level(grammar: dict[str, ir.Prod], node: ir.Node) -> ir.Node | None:
    """
    `_pushed_level` returns the argument a call hands its callee as `n`. A call passing `n` itself keeps the indentation
    in force and pushes nothing. `_pushed_level` returns `None` for such a call. Any other argument pushes an
    indentation of its own, such as `m` or a difference. `_pushed_level` also returns `None` where `node` is no call
    into a production taking `n`, or where the call omits that argument.
    """
    call = ir.AS_A_CALL(node)
    if call is None:
        return None
    callee = grammar.get(call.name)
    if callee is None or "n" not in callee.params:
        return None
    position = callee.params.index("n")
    if position >= len(call.args):
        return None
    level = call.args[position]
    named = ir.AS_A_PARAMETER(level)
    return None if named is not None and named.name == "n" else level


def _span_consumes(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a run over a character class as a consume. The parse enters the consume on the class. `x+` is the guard that
    the class is in front. The span comes behind it. `x*` is that same way beside a second way. The second way runs
    where the class is not in front.

    Such a run takes a value the input decides rather than a way the parse chooses. A span with the class in front takes
    at least a character of the class, as a `PlusTree` does.

    A `StarTree`'s second way asks that the class is *not* there rather than matching empty beside it. The consume that
    replaces the run is possessive. The consume takes the run where the class is in front, and no shorter match remains
    to hand back. An empty way would be exactly that shorter match. `s-indent-le` is a run of spaces judged against the
    indentation afterwards. A run too long would fail the judgement. The fallback to no spaces would then pass that same
    judgement. The character tells the ways apart. Neither way is reachable where the parse took the other, and the
    alternation is the consume.

    The gate takes the guard rather than the class itself, though either admits the same text. `is_one_char` admits a
    class naming a production. Such a class is a `RefCall`. `AS_A_CALL` reads a `RefCall` as a call, and
    `mint-continuations` ends a way at a call. The span would then land in a continuation entered after the call
    returned. The guard saying the run takes a character would sit a production away from the run. A guard is no call.
    It stays among the actions beside the span. `_split_parts` reads the guard as one of `CONSUMES_NOTHING` and the
    consume as one of `CONSUMING`. The consume is then the part with no empty way.

    A counted repetition is the same run bounded rather than ended. `x{n}` is a run of up to `n` characters of the set.
    The guard asks whether the run reached `n`. The taking cannot fail. It takes what is there and says how much. A
    count falling short is therefore a question asked past the run, rather than a way failing on what the way performs.
    A count the parse works out is a pair of ways. The first is the run entered on a character of the set. At a count of
    none or below, the second way is the run that takes no character and says so. That second way performs
    `ConsumeNoCharAction` rather than nothing at all, as a `StarTree`'s second way does. A way that performs nothing
    still reports a length. That length is the length the last run left.

    A run over anything else stays. The run is a way, and the empty match inside it is a question about the parse rather
    than about a consume.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _SPANNED(ir.rebuilt(node, lowered), grammar)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _spanned_count(node: ir.RepTree, grammar: dict[str, ir.Prod]) -> ir.Node:
    """
    A counted repetition of a character as a consume of up to the count. A guard past the taking asks whether the run
    reached the count. The consume cannot fail, and the guard may refuse. A repetition of a wider match keeps its form.
    """
    if not ir.is_one_char(node.item, grammar):
        return node
    asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
    taking = ir.SeqTree(
        items=(
            ir.LookGuard(item=_a_set(asked)),
            ir.ConsumeLimitedSpanAction(set=node.item, limit=node.count),
            ir.DidMatchFullSpanGuard(),
        )
    )
    times = getattr(node.count, "value", None)
    if times is not None:
        return taking if isinstance(times, int) and times > 0 else ir.ConsumeNoCharAction()
    return ir.AltTree(
        items=(
            ir.SeqTree(items=(ir.IsLessThanGuard(a=ir.LitValue(value=0), b=node.count), taking)),
            ir.SeqTree(items=(ir.IsLessEqualGuard(a=node.count, b=ir.LitValue(value=0)), ir.ConsumeNoCharAction())),
        )
    )


def _spanned_run(node: ir.PlusTree | ir.StarTree, grammar: dict[str, ir.Prod]) -> ir.SeqTree | None:
    """
    The way that takes the span of a run of a character. A guard on that character opens the way. A run of a wider match
    gives `None`.
    """
    if not ir.is_one_char(node.item, grammar):
        return None
    asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
    return ir.SeqTree(items=(ir.LookGuard(item=_a_set(asked)), ir.ConsumeSpanAction(set=node.item)))


def _spanned_plus(node: ir.PlusTree, grammar: dict[str, ir.Prod]) -> ir.Node:
    """A run taking at least a character, as its span. A run of a wider match keeps its form."""
    taken = _spanned_run(node, grammar)
    return node if taken is None else taken


def _spanned_star(node: ir.StarTree, grammar: dict[str, ir.Prod]) -> ir.Node:
    """
    A run that may take nothing, as its span beside a way that takes none. A run of a wider match keeps its form.

    The way taking none performs `ConsumeNoCharAction`. `(match)` past a way that performed nothing is the length the
    last run left. That is the answer of another run.
    """
    taken = _spanned_run(node, grammar)
    if taken is None:
        return node
    asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
    none = ir.SeqTree(items=(ir.NegLookGuard(item=_a_set(asked)), ir.ConsumeNoCharAction()))
    return ir.AltTree(items=(taken, none))


# A node with its runs of a character written as consumes. A counted repetition and both kinds of run reshape. A node of
# another kind keeps its form.
_SPANNED: ir.Question[ir.Node] = ir.Question(
    "a node with its runs of a character written as consumes",
    {
        ir.RepTree: _spanned_count,
        ir.PlusTree: _spanned_plus,
        ir.StarTree: _spanned_star,
        ir.kinds_other_than(ir.RepTree, ir.PlusTree, ir.StarTree): lambda node, _grammar: node,
    },
)


def _every_character_run_is_a_span(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no repetition is over a character class. A run of characters is a value the consume decides, rather than
    a way the parse repeats.

    `ir.repeated` says what repeats, rather than a list of kinds kept here.
    """
    return [
        f"{name}: repeats a character class instead of consuming it"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Node) and (item := ir.repeated(node)) is not None and ir.is_one_char(item, grammar)
    ]


_EVERY_CHARACTER_RUN_IS_A_SPAN = _Invariant("every-character-run-is-a-span", _every_character_run_is_a_span)

# The repetitions the vendored notation writes are gone. The grammar writes them as ways instead, the way `lower-runs`
# says. Past here the grammar holds no `StarTree` and no `PlusTree`. The counted `RepTree` is not one of these. That
# kind takes the number of turns the count names rather than as many as it can. `span-consumes` has already written a
# count of a single character as a consume. A count of a wider match keeps its form.
_NO_STAR_OR_PLUS_NODES = _absent("no-star-or-plus-nodes", ir.StarTree, ir.PlusTree)


# `lower-wraps` runs in `lower-scopes`. A scope that holds what it covers becomes the pair bracketing that scope. A
# `(wrap)` says so outright. A `(wrap)` is a node rather than a pair of markers. A `begin` cannot then lose its `end`.
# After `lower-wraps`, the begin marker and the end marker become an `EmitAction` holding the marker's code. Neither
# marker names a pair. `check_markers` pairs the markers by their codes.
_NO_WRAP_NODES = _absent("no-wrap-nodes", ir.Wrapper)


def _lower_wraps(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(wrap)` as the pair of markers it names. `Wrapper(begin, end, item)` becomes `SeqTree(EmitAction(begin),
    item, EmitAction(end))`.

    The node is sugar for the sequence this step writes. `interpreter._try_marker` emits a marker.

    The markers come out on either side of the item in a single way of a single production.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_WRAP(ir.rebuilt(node, lowered))

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# Writes a `(wrap)` out as the markers it names. The begin marker comes before the item, and the end marker comes after
# it.
_LOWERED_WRAP = _reshaping(
    "a `(wrap)` as the pair of markers it names",
    ir.Wrapper,
    lambda node: ir.SeqTree(items=(ir.EmitAction(code=node.begin), node.item, ir.EmitAction(code=node.end))),
)


# `lower-windows` runs in `lower-scopes`. The `(max)` window is the pair that opens and closes it.
_NO_MAX_NODES = _absent("no-max-nodes", ir.MaxWrapper)


def _lower_windows(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(max)` as the window pair. `Max(limit, message, x)` becomes `OpenWindowAction(limit, message, pair) x
    CloseWindowAction(pair)`.

    A window inside another keeps the outer budget. `interpreter._try_open_window` applies the outermost window only.
    The step leaves no `MaxWrapper` at all.

    A `(max)` with nothing in it is the vendored grammar's bare length note. The vendored grammar writes such a note in
    front of a production. libyeast writes the bound around a production instead. The step raises an error on such a
    `(max)` rather than opening a window.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_WINDOW(ir.rebuilt(node, lowered), namer)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _lowered_window(node: ir.MaxWrapper, namer: _Namer) -> ir.Node:
    """`node` as the window pair around its item. A `(max)` with nothing in it raises."""
    if node.item is None:
        raise ValueError("a `(max)` with nothing in it is a length note, and the pipeline places none")
    pair = namer.pair()
    return ir.SeqTree(
        items=(
            ir.OpenWindowAction(limit=node.limit, message=_a_message(node), pair=pair),
            node.item,
            ir.CloseWindowAction(pair=pair),
        )
    )


# A `(max)` as the window pair around the item it bounds.
_LOWERED_WINDOW = _reshaping("a `(max)` as the window pair", ir.MaxWrapper, _lowered_window)


# `lower-commits` runs in `lower-scopes`. The region catching a failed cut is the pair that opens and closes it.
_NO_COMMIT_NODES = _absent("no-commit-nodes", ir.CommitWrapper)


def _lower_commits(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(commit)` as the message pair. `CommitWrapper(message, x)` becomes `PushMessageAction(message, pair) x
    PopMessageAction(pair)`, and the push and the pop share a `pair` from the namer.

    The parser sets `emitter.failing` to the commit's message when the guarded item does not reach its own end and no
    earlier message holds `emitter.failing`. A continuation that fails past a matched item backtracks like any other
    match. The commitment does not reach past that item, and the pair enforces the same bound. The push records a region
    and the pop marks it reached. The parse may unwind to a push while the region of that push stays open. Such an
    unwind is the error. The interpreter says so where it implements the push. The pop goes where the `(commit)` scope
    ends.

    The pair changes where the pushed region's record lives. The wrapper keeps that record in a Python local. The pair
    keeps it on `emitter.stack`. The stack survives a split of the pair across a call.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_COMMIT(ir.rebuilt(node, lowered), namer)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _lowered_commit(node: ir.CommitWrapper, namer: _Namer) -> ir.Node:
    """`node` as the message pair around its item."""
    pair = namer.pair()
    return ir.SeqTree(
        items=(ir.PushMessageAction(message=node.message, pair=pair), node.item, ir.PopMessageAction(pair=pair))
    )


# A `(commit)` as the message pair around the item it guards.
_LOWERED_COMMIT = _reshaping("a `(commit)` as the message pair", ir.CommitWrapper, _lowered_commit)


# A `(token)` as its item. A probe tests the characters and opens no token.
_UNTOKENED = _reshaping("a `(token)` as its item", ir.TokenWrapper, lambda node: node.item)


def _forbids_a_copy(node: ir.ExcludeAtAction | ir.SetForbiddenAction, copies: Mapping[str, str]) -> ir.Node:
    """`node` forbidding the probe copies `copies` names. A node forbidding nothing keeps its form."""
    return node if node.item is None else dataclasses.replace(node, item=node.item.renamed(copies))


# A node whose forbidden match names the probe copies. An `(exclude)` and the action setting what is forbidden name a
# match. A node of another kind keeps its form.
_FORBIDS_A_COPY: ir.Question[ir.Node] = ir.Question(
    "a node whose forbidden match names the probe copies",
    {
        (ir.ExcludeAtAction, ir.SetForbiddenAction): _forbids_a_copy,
        ir.kinds_other_than(ir.ExcludeAtAction, ir.SetForbiddenAction): lambda node, _copies: node,
    },
)


def _lowered_token(node: ir.TokenWrapper, namer: _Namer) -> ir.Node:
    """`node` as the code pair around its item."""
    pair = namer.pair()
    return ir.SeqTree(items=(ir.PushCodeAction(code=node.code, pair=pair), node.item, ir.PopCodeAction(pair=pair)))


# A `(token)` as the code pair around the item it covers.
_LOWERED_TOKEN = _reshaping("a `(token)` as the code pair", ir.TokenWrapper, _lowered_token)

# An optional as the choice of its item and `<empty>`.
_LOWERED_OPTIONAL = _reshaping(
    "an optional as the choice of its item and `<empty>`",
    ir.OptTree,
    lambda node: ir.AltTree(items=(node.item, ir.EmptyTree())),
)

# A run written out by `said`, the closure of `_lower_runs` that mints the loop. A node of another kind keeps its form.
_LOWERED_RUN: ir.Question[ir.Node] = ir.Question(
    "a run written out as the ways it offers",
    {
        ir.RUNS: lambda node, name, number, said: said(name, number, node),
        ir.kinds_other_than(*ir.RUNS): lambda node, _name, _number, _said: node,
    },
)


def _mint_forbidden_probes(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    The pattern an exclusion forbids gets a copy of its own. The copy holds only what the pattern matches and what it
    answers.

    The pattern answers whether the forbidden text is there. `_try_probe` goes back under either answer. A `(token)`
    inside the pattern therefore gives a code to characters that no token keeps. The copy drops the annotation and
    matches the same text.

    This mints a copy rather than rewriting the original. A way inside the pattern and a way outside it reach the same
    production. `c-directives-end` is the `---` a document really opens with, and the `---` a plain scalar must not run
    into. The parse matches the second to throw it away, and keeps the first. The whole reach that minting touches gets
    a copy, and the callers inside that reach name the copies.
    """
    copies = {name: namer.fresh(name) for name in sorted(_forbidden_productions(grammar))}

    def tested(node: ir.Node) -> ir.Node:
        return _UNTOKENED(ir.rebuilt(node, tested))

    def forbids(node: ir.Node) -> ir.Node:
        return _FORBIDS_A_COPY(ir.rebuilt(node, forbids), copies)

    minted = {
        copy: dataclasses.replace(grammar[name], name=copy, body=tested(grammar[name].body.renamed(copies)))
        for name, copy in copies.items()
    }
    written = {
        name: dataclasses.replace(production, body=forbids(production.body)) for name, production in grammar.items()
    }
    return {**written, **minted}


# `lower-tokens` runs in `lower-scopes`. A push sets the code of the characters after it (`_try_push_code`). Its pop
# restores the code that push displaced (`_try_pop_code`).
_NO_TOKEN_NODES = _absent("no-token-nodes", ir.TokenWrapper)


def _lower_tokens(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(token)` as the code pair. `TokenWrapper(code, x)` becomes `PushCodeAction(code, pair) x
    PopCodeAction(pair)`.

    An annotation says what code the characters consumed within it take. `_try_push_code` and `_try_pop_code` cut the
    run at both edges. The characters before and after fall into tokens of their own. The push sets the code, and the
    pop takes back the code the push displaced.

    The push puts the displaced code on the stack the parse holds. `_popped` takes back the entry on top. It refuses a
    top entry that another pair put there.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_TOKEN(ir.rebuilt(node, lowered), namer)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _reached_forbidden(
    node: ir.Node, forbidden: ir.Node | None, reached: dict[str, set[ir.Node | None]]
) -> ir.Node | None:
    """
    The set forbidden where `node` ends. This records in `reached` the set that holds on entry to a call `node` makes.

    A sequence passes the set along its parts. An `(exclude)` sets the set for the parts behind it. Any other shape
    hands its parts what reaches the shape. A branch of a choice therefore takes no exclusion from its sibling and hands
    none on.
    """
    return _REACHED_FORBIDDEN(node, forbidden, reached)


def _forbidden_along_a_run(
    node: ir.SeqTree, forbidden: ir.Node | None, reached: dict[str, set[ir.Node | None]]
) -> ir.Node | None:
    """
    The set forbidden where a sequence ends. A part takes the set the part before it left. The order is the order the
    parts perform in.
    """
    for item in node.items:
        forbidden = _reached_forbidden(item, forbidden, reached)
    return forbidden


def _forbidden_at_a_call(
    node: ir.RefCall, forbidden: ir.Node | None, reached: dict[str, set[ir.Node | None]]
) -> ir.Node | None:
    """The callee of a call gets what reaches the call. The call itself forbids nothing."""
    reached[node.name].add(forbidden)
    return forbidden


def _forbidden_at_the_whole(
    node: ir.Node, forbidden: ir.Node | None, reached: dict[str, set[ir.Node | None]]
) -> ir.Node | None:
    """A node hands its parts the set that reaches the node."""
    ir.rebuilt(node, lambda child: (_reached_forbidden(child, forbidden, reached), child)[1])
    return forbidden


# The walk raises on a kind missing from `_REACHED_FORBIDDEN`. The walk holds no default for such a kind. A default
# would hand a shape's parts what reaches the whole. A shape passing the set along would lose an exclusion set halfway
# through.
_REACHED_FORBIDDEN: ir.Question[ir.Node | None] = ir.Question(
    "the node forbidden where a match ends. That node reaches the actions following the match.",
    {
        ir.SeqTree: _forbidden_along_a_run,
        ir.ExcludeAtAction: lambda node, forbidden, reached: node.item,
        ir.RefCall: _forbidden_at_a_call,
        (
            *(kind for kind in ir.CONSUMES_NOTHING if kind is not ir.ExcludeAtAction),
            *ir.CONSUMING,
            *ir.VALUE_KINDS,
            *ir.WRAPPERS,
            ir.AltTree,
            ir.BindTree,
            ir.FailTree,
        ): _forbidden_at_the_whole,
    },
)


def _forbidden_entries(grammar: dict[str, ir.Prod]) -> dict[str, set[ir.Node | None]]:
    """
    Answer `{name: sets}`. The sets hold what may not match where the parse enters a production. `None` is among them
    where the parse enters with nothing forbidden.

    This is a least fixpoint over the calls. A parse enters by name with nothing forbidden. A call passes in the set the
    call site forbids.
    """
    entered = _entered_by_name(grammar)
    callers = _callers(grammar)
    callees = _inverted(callers)
    walked: dict[tuple[str, ir.Node | None], dict[str, set[ir.Node | None]]] = {}

    def recorded(caller: str, forbidden: ir.Node | None) -> dict[str, set[ir.Node | None]]:
        # Walk `caller` under `forbidden` once. Record per callee the sets that walk enters.
        if (caller, forbidden) not in walked:
            walked[(caller, forbidden)] = collections.defaultdict(set)
            _reached_forbidden(grammar[caller].body, forbidden, walked[(caller, forbidden)])
        return walked[(caller, forbidden)]

    def answer(name: str, known: Mapping[str, frozenset[ir.Node | None]]) -> frozenset[ir.Node | None]:
        held: set[ir.Node | None] = {None} if name in entered else set()
        for caller in callers.get(name, ()):
            for forbidden in known[caller]:
                held |= recorded(caller, forbidden).get(name, set())
        return frozenset(held)

    settled = ir.fixed_point(
        "the forbidden node where a parse enters a production",
        {name: frozenset[ir.Node | None]() for name in grammar},
        answer,
        callees,
    )
    return {name: set(held) for name, held in settled.items()}


# The function giving the name a production's call enters while a forbidden set is in force.
_Told = Callable[[str, ir.Node | None], str]

# A function lowering a node while a forbidden set, or `None`, is in force.
_LoweredUnder = Callable[[ir.Node, ir.Node | None], ir.Node]


def _excluded_call(node: ir.RefCall, forbidden: ir.Node | None, told: _Told, _lowered: _LoweredUnder) -> ir.Node:
    """`node` calling the name `told` gives for the set `forbidden`."""
    return dataclasses.replace(node, name=told(node.name, forbidden))


def _excluded_sequence(node: ir.SeqTree, forbidden: ir.Node | None, _told: _Told, lowered: _LoweredUnder) -> ir.Node:
    """`node` with its first `(exclude)` as two `SetForbiddenAction` writes around the rest of the way."""
    items: list[ir.Node] = []
    for index, item in enumerate(node.items):
        excluded = ir.AS_AN_EXCLUSION(item)
        if excluded is not None:
            rest = lowered(ir.SeqTree(items=node.items[index + 1 :]), excluded.item)
            return ir.SeqTree(
                items=(
                    *items,
                    ir.SetForbiddenAction(item=excluded.item),
                    *getattr(rest, "items"),
                    ir.SetForbiddenAction(item=forbidden),
                )
            )
        items.append(lowered(item, forbidden))
    return ir.SeqTree(items=tuple(items))


# A call enters the copy for the forbidden set in force. A sequence writes its first `(exclude)` as two
# `SetForbiddenAction` nodes. A node of another kind gets rebuilt with its parts lowered.
_LOWERED_EXCLUSION: ir.Question[ir.Node] = ir.Question(
    "a node with its `(exclude)` actions lowered to `SetForbidden` actions",
    {
        ir.RefCall: _excluded_call,
        ir.SeqTree: _excluded_sequence,
        ir.kinds_other_than(ir.RefCall, ir.SeqTree): lambda node, forbidden, _told, lowered: ir.rebuilt(
            node, lambda child: lowered(child, forbidden)
        ),
    },
)


def _lower_exclusions(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write an `(exclude)` as the pair of writes that bound it. `ExcludeAtAction(x)` over the rest of a way becomes
    `SetForbiddenAction(x)` in its place and `SetForbiddenAction(<the set in force around the way>)` where the way ends.

    The exclusion in force reaches `lowered` as `forbidden`.

    An exclusion is in force until the way holding it ends.

    The closing write restores the forbidden set in force around the way. The step keeps no stack. The set is a single
    value for the parse. A pair of callers may reach a production holding an exclusion in different states. That
    production cannot name a single set. The first state in sorted order keeps the production's name. The step writes a
    fresh copy for the later states. A caller enters the production for its own state. `_monomorphize` makes the same
    specialization of a parameter. This step makes it of the forbidden set, and that set is no parameter.
    """
    entries = _forbidden_entries(grammar)
    held = {
        name
        for name, production in grammar.items()
        if any(ir.AS_AN_EXCLUSION(n) is not None for n in _held(production.body))
    }
    copies = {}  # `copies` maps a `(name, forbidden)` pair to the name a call enters in that state.
    for name in sorted(held):
        for forbidden in sorted(entries[name], key=str)[1:]:
            copies[(name, forbidden)] = namer.fresh(name)

    def told(name: str, forbidden: ir.Node | None) -> str:
        return copies.get((name, forbidden), name)

    def lowered(node: ir.Node, forbidden: ir.Node | None) -> ir.Node:
        return _LOWERED_EXCLUSION(node, forbidden, told, lowered)

    written = {}
    for name, production in grammar.items():
        for forbidden in sorted(entries[name] or {None}, key=str):
            under = told(name, forbidden)
            written[under] = dataclasses.replace(production, name=under, body=lowered(production.body, forbidden))
    return written


# The property the lowering above makes true. The lowering rewrites an `ExcludeAtAction` into a pair of
# `SetForbiddenAction`s. The grammar holds no `ExcludeAtAction` after that.
_NO_EXCLUDE_AT_NODES = _absent("no-exclude-at-nodes", ir.ExcludeAtAction)


def _lower_runs(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say the repetitions as ways instead. Those are a turn, a recursion taking the turns behind it, and a settled region
    around the turns after the first. `x*` offers the untaken turn beside them. `x+` withholds that turn.

    A turn takes a character. A turn taking none would repeat for ever, and a run ends where the parse does not take a
    turn. The region settles the turns it holds. A failure past its close gives the whole run up. The parse does not
    take fewer turns, and it does not take a turn another way. The run is therefore the longest match. The grammar
    therefore fixes the backtrack points a run has. The first turn is outside the region, and the turns after it are in.

    The turn is a production of its own, and both places call that production. The recursion is a production too. The
    guard past the turn cuts the way.

    A run over a character class is the same operation said as the consume a parser makes of it. `span-consumes` has
    already written that as a `ConsumeSpanAction`. A run over anything else repeats a way, and `_lower_runs` says a
    repeated way as ways.
    """
    minted = {}

    def said(name: str, number: int, node: ir.PlusTree | ir.StarTree) -> ir.Node:
        """The ways the run `node` becomes."""
        # The recursion is a production. The turn is a production too, where it is not already a call.
        loop = namer.fresh(name)
        called = ir.AS_A_CALL(node.item)
        if called is not None:
            turn = called
        else:
            held = namer.fresh(name)
            minted[held] = ir.Prod(number, held, (), node.item)
            turn = ir.RefCall(name=held, args=())
        taking, pair = namer.pair(), namer.pair()
        takes = (
            ir.StartMustConsumeAction(pair=taking),
            turn,
            ir.DidConsumeSinceOpenGuard(pair=taking),
            ir.EndMustConsumeAction(pair=taking),
        )
        minted[loop] = ir.Prod(
            number,
            loop,
            (),
            ir.AltTree(items=(ir.SeqTree(items=(*takes, ir.RefCall(name=loop, args=()))), ir.EmptyTree())),
        )
        rest = (ir.PushBackTrackAction(pair=pair), ir.RefCall(name=loop, args=()), ir.PopBackTrackAction(pair=pair))
        taken = ir.SeqTree(items=(*takes, *rest))
        return taken if ir.AS_A_PLUS(node) is not None else ir.AltTree(items=(taken, ir.EmptyTree()))

    def lowered(name: str, number: int, node: ir.Node) -> ir.Node:
        return _LOWERED_RUN(ir.rebuilt(node, lambda child: lowered(name, number, child)), name, number, said)

    runs = {
        name: dataclasses.replace(production, body=lowered(name, production.number, production.body))
        for name, production in grammar.items()
    }
    return {**runs, **minted}


def _lower_optionals(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write an optional as an alternation instead. `x?` becomes `x | <empty>`. The empty way then sits beside the way that
    reads. It hides inside no node.

    The interpreter says the match is the same. An `OptTree` tries its item with the continuation behind that item. The
    `OptTree` rewinds where that fails, and takes the continuation without the item. That is an alternation of the item
    and `<empty>`. The item comes first. The item takes as much as before, and the step needs no knowledge of the
    continuation.

    This writes the `<empty>` even where the item can already take nothing. The optional offers the item's ways and
    *then* an empty match.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_OPTIONAL(ir.rebuilt(node, lowered))

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# An optional hides no empty match once the step removing optional nodes runs. The `lower-repetitions` phase settles
# this property. A `StarTree` hides an empty match as well, and this property leaves it out. The step `lower-runs`
# writes a `StarTree` as ways. A run is possessive.
_NO_OPT_NODES = _absent("no-opt-nodes", ir.OptTree)


def _inner_ways(node: ir.Node) -> tuple[ir.Node, ...]:
    """
    The matches `node` holds, as ways in their own right. The walk goes on into these whether or not it counted the
    holder.
    """
    return _INNER_WAYS(node)


_INNER_WAYS: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the tuple of nodes a body offers as its ways. A way is a match in its own right.",
    {
        ir.ChoiceState: lambda node: node.alternatives,
        ir.AltTree: lambda node: node.items,
        ir.SeqTree: lambda node: (node,),
        ir.RecoverWrapper: lambda node: (node.item, node.recovery),
        ir.BindTree: lambda node: (node.cond,),  # The matches a body offers as its ways.
    },
)


# Whether an item holds a match. `ir.Question` raises on an item of a kind neither family names.
_DOES_AN_ITEM_HOLD_A_MATCH: ir.Question[bool] = ir.Question(
    "whether an item holds a match", {ir.LEAF_ITEMS: False, ir.HOLDS_A_MATCH: True}
)

# The ways a production's body opens. A body of another kind is a single way.
_OPENED_WAYS: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the ways a production's body opens",
    {ir.BODY_KINDS: _inner_ways, ir.kinds_other_than(*ir.BODY_KINDS): lambda body: (body,)},
)


def _nested_matches(grammar: dict[str, ir.Prod], as_reported: Callable[[ir.Node], ir.Node | None]) -> list[str]:
    """
    Items in a way that hold a match. Those are the shapes the machine has no single step for.

    This is the count the phase keeps. A body of a kind `ir.BODY_KINDS` names opens into its ways. A body of another
    kind is a single way. A way is a run of items. An item is a call or an action. An item is also a guard, a character
    taken, or an empty match. The actions, the guards and the empty match are the kinds `ir.CONSUMES_NOTHING` names. An
    item of a kind `ir.HOLDS_A_MATCH` names holds a match inside itself. A state machine reads such a shape only once
    that shape is gone from where an item sits. `lower-bind` writes a binding as a run of its match and then an action.
    `as_reported` narrows the count to a single kind. That is how a step's invariant reads its share of the phase's
    count through the same walk. The walk goes into a holder either way, and it finds a nested match wherever that match
    sits.

    This counts a nested match where it sits rather than at the outermost only. Lifting a match therefore takes a single
    item off the count, and uncovers no fault the walk had not already read. The walk raises on an item of a kind that
    neither `ir.LEAF_ITEMS` nor `ir.HOLDS_A_MATCH` names.
    """
    faults: list[str] = []

    def item(node: ir.Node, owner: str) -> None:
        if not _DOES_AN_ITEM_HOLD_A_MATCH(node):
            return
        if as_reported(node) is not None:
            faults.append(f"{owner}: a {type(node).__name__} sits where an item does and holds a match")
        for inner in _inner_ways(node):
            way(inner, owner)

    def way(node: ir.Node, owner: str) -> None:
        for part in _items_of_way(node):
            item(part, owner)

    for name, production in grammar.items():
        for opened in _OPENED_WAYS(production.body):
            way(opened, name)
    return faults


_EVERY_SUB_ITEM_IS_ONE_STEP = _Invariant(
    "every-sub-item-is-one-step", lambda grammar: _nested_matches(grammar, lambda node: node)
)

# The phase's first share. A choice is where the machine has a state. A choice inside a way has no place to be that
# state. A production of its own is that state, and the way holds the call.
_EVERY_CHOICE_IS_A_PRODUCTION = _Invariant(
    "every-choice-is-a-production", lambda grammar: _nested_matches(grammar, ir.AS_AN_ALTERNATION)
)

# A recovery is a `RecoverWrapper`, a handler over a match. `lift-recoveries` gives a recovery a production of its own.
_EVERY_RECOVERY_IS_A_PRODUCTION = _Invariant(
    "every-recovery-is-a-production", lambda grammar: _nested_matches(grammar, ir.AS_A_RECOVERY)
)

# A binding is a match and the write that follows it. The vocabulary already writes the match and the write apart.
_NO_BIND_NODES = _absent("no-bind-nodes", ir.BindTree)


def _is_bounded_question(node: ir.Node, grammar: dict[str, ir.Prod], entered: frozenset[str] = frozenset()) -> bool:
    """
    Whether `node` asks what a bounded number of the machine's steps answers.

    Repetition and recursion make a question unbounded. A call costs what its callee costs. A call to a name outside the
    grammar costs without bound. A call reaching itself costs without bound. Another node costs what its parts cost.
    """
    return _IS_BOUNDED_QUESTION(node, grammar, entered)


def _is_bounded_call(node: ir.RefCall, grammar: dict[str, ir.Prod], entered: frozenset[str]) -> bool:
    """Whether the callee of `node` asks a bounded question. A call reaching itself costs without bound."""
    if node.name in entered or node.name not in grammar:
        return False
    return _is_bounded_question(grammar[node.name].body, grammar, entered | {node.name})


def _are_bounded_parts(node: ir.Node, grammar: dict[str, ir.Prod], entered: frozenset[str]) -> bool:
    """Whether the parts `node` holds ask bounded questions."""
    held: list[ir.Node] = []

    def seen(child: ir.Node) -> ir.Node:
        held.append(child)
        return child

    ir.rebuilt(node, seen)
    return all(_is_bounded_question(child, grammar, entered) for child in held)


# A repetition takes a step per match. Its cost grows with the input. A `ConsumeSpanAction` costs a single step.
_IS_BOUNDED_QUESTION: ir.Question[bool] = ir.Question(
    "whether a node asks what a bounded number of the machine's steps answers",
    {
        ir.REPETITIONS: False,
        ir.RefCall: _is_bounded_call,
        ir.kinds_other_than(*ir.REPETITIONS, ir.RefCall): _are_bounded_parts,
    },
)


def _every_exclusion_is_bounded(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that an `(exclude)` asks a question a bounded number of the machine's steps can answer.

    `interpreter._is_forbidden_here` asks an `(exclude)` at a start of line. `c-forbidden` is a line beginning `---`
    (`c-directives-end`) or `...` (`c-document-end`). A break, white space (`s-white`) or the end follows.
    """
    return [
        f"{name}: an `(exclude)` asks what no bounded run of steps answers"
        for name, production in grammar.items()
        for node in _held(production.body)
        if (excluded := ir.AS_AN_EXCLUSION(node)) is not None and not _is_bounded_question(excluded.item, grammar)
    ]


_EVERY_EXCLUSION_IS_BOUNDED = _Invariant("every-exclusion-is-bounded", _every_exclusion_is_bounded)


def _does_every_part_only_match_and_ask(node: ir.Node) -> bool:
    """Whether the parts `node` holds only match and ask."""
    held: list[ir.Node] = []

    def seen(child: ir.Node) -> ir.Node:
        held.append(child)
        return child

    ir.rebuilt(node, seen)
    return all(_ONLY_MATCHES_AND_ASKS(child) for child in held)


# The kinds an exclusion's pattern may hold, asked of a single node rather than of the productions it calls. A call
# names a production, and the probe asks about the call rather than that production. `_forbidden_productions` reaches
# the production in its own right.
_ONLY_MATCHES_AND_ASKS: ir.Question[bool] = ir.Question(
    "whether what an exclusion forbids only matches and asks",
    {
        # A match takes characters and says whether they were there. The probe wants exactly that.
        (ir.OneCharSet, ir.CharSet, ir.ConsumeCharAction, ir.ConsumeSpanAction, ir.RangeSet): True,
        # A guard reads where the parse is and leaves it there, and an empty match does neither. A run that took no
        # character is an empty match. It says how long the match was, and the match was nothing.
        (
            ir.ConsumeNoCharAction,
            ir.IsLessEqualGuard,
            ir.EmptyTree,
            ir.EndOfStreamGuard,
            ir.StartOfLineGuard,
        ): True,
        ir.RefCall: True,
        # A shape holding parts takes the characters its parts take. A difference and a lookaround are among them. Both
        # ask about a match of their own.
        (
            ir.AltTree,
            ir.AlternativeState,
            ir.ChoiceState,
            ir.DiffSet,
            ir.GatePart,
            ir.LookGuard,
            ir.NegLookGuard,
            ir.SeqTree,
        ): _does_every_part_only_match_and_ask,
        # A `(token)` says what code the characters under it take.
        ir.TokenWrapper: False,
    },
)


# The productions a node's forbidden match names. An `(exclude)` and the action setting what is forbidden name a match.
_FORBIDDEN_NAMES: ir.Question[Iterable[str]] = ir.Question(
    "the productions a node's forbidden match names",
    {
        (ir.ExcludeAtAction, ir.SetForbiddenAction): lambda node: node.references(),
        ir.kinds_other_than(ir.ExcludeAtAction, ir.SetForbiddenAction): (),
    },
)


def _forbidden_productions(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions an exclusion's pattern reaches, and the productions those reach. `interpreter._is_forbidden_here`
    runs these productions at a start of line, to answer whether the forbidden text is there.
    """
    seen = set()
    worklist = [
        name for production in grammar.values() for node in _held(production.body) for name in _FORBIDDEN_NAMES(node)
    ]
    while worklist:
        name = worklist.pop()
        if name in seen or name not in grammar:
            continue
        seen.add(name)
        worklist.extend(grammar[name].references())
    return seen


def _every_forbidden_only_matches_and_asks(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that what an exclusion forbids only matches characters and asks questions.

    `interpreter._is_forbidden_here` runs the pattern at a start of line through `_try_probe`, and the probe rewinds.
    The pattern may take characters and ask questions, and do no more.

    This asks the productions the pattern reaches, rather than the sites naming it. A pattern reached from more than a
    single site is a single thing to check, and a step that copies a site copies no fault.
    """
    return [
        f"{name}: an exclusion forbids a node that does more than match and ask"
        for name in sorted(_forbidden_productions(grammar))
        if not _ONLY_MATCHES_AND_ASKS(grammar[name].body)
    ]


_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS = _Invariant(
    "every-forbidden-only-matches-and-asks", _every_forbidden_only_matches_and_asks
)


def _no_choice_of_choices(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no way of a choice is a call to a choice.

    `a | P | c` with `P` as `d | e` shows fewer ways than it decides between. The hidden way sits behind a call. The
    outer choice can see nothing of it. `a | d | e | c` writes the same ways. The ways then sit where the choice can ask
    about them.

    The check reads the tree's form. It looks for an `AltTree` anywhere in a production's body with a way that calls a
    production whose body is an `AltTree`.
    """
    return [
        f"{name}: a way of a choice that is a call to a choice"
        for name, production in grammar.items()
        for node in _held(production.body)
        if (alternation := ir.AS_AN_ALTERNATION(node)) is not None
        for way in alternation.items
        if (call := ir.AS_A_CALL(way)) is not None and ir.AS_AN_ALTERNATION(grammar[call.name].body) is not None
    ]


_NO_CHOICE_OF_CHOICES = _Invariant("no-choice-of-choices", _no_choice_of_choices)


def _no_sequence_of_sequences(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no item of a way, bar the last, is a call to a run of items.

    `a P c` with `P` as `d e` shows fewer items than the parse performs. `a d e c` writes the same run. The parts then
    sit where the way holds them.

    The check spares a call last in the way. A call earlier in the way hides a run.

    The check reads the tree's form. There the fault is a `SeqTree` holding an item that names a `SeqTree`.
    """
    return [
        f"{name}: an item of a way that is a call to a run of items"
        for name, production in grammar.items()
        for node in _held(production.body)
        if (sequence := ir.AS_A_SEQUENCE(node)) is not None
        for item in sequence.items[:-1]
        if (call := ir.AS_A_CALL(item)) is not None and ir.AS_A_SEQUENCE(grammar[call.name].body) is not None
    ]


_NO_SEQUENCE_OF_SEQUENCES = _Invariant("no-sequence-of-sequences", _no_sequence_of_sequences)

# A way is a gate, actions and the calls it hands control to. An empty match is none of those. It asks nothing. It does
# nothing and calls nothing. The machine's form holds no empty match. The step that builds the ways handles an empty
# match.
_NO_EMPTY_NODES = _absent("no-empty-nodes", ir.EmptyTree)


def _items_of_way(way: ir.Node) -> tuple[ir.Node, ...]:
    """
    The items a way *performs*. This is **not** its gate.

    An alternative holds the actions, the call and the continuation in named fields. A sequence says them in a row
    instead. A question that walks a way wants the parts in the order the parse performs them. Both forms answer alike.
    A recovery is not an item. The call under a recovery is an item. An `AlternativeState` holds its recovery in
    `recover`. That recovery catches a cut rather than running among the parts the way performs.

    Leaving the gate out is wrong for a question about the parse's own run. The gate decides whether the parse enters
    the way at all. A walk that cannot see the gate reports what a way would do under any input. `_parts_of_way` answers
    a question about the parse's own run instead. This is for callers that mean the performing parts and no more.
    """
    return _ITEMS_OF_WAY(way)


# The items a way performs. A node of another kind than an alternative or a sequence is a way of a single item.
_ITEMS_OF_WAY: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the items a way performs",
    {
        ir.AlternativeState: lambda way: (*way.actions, *_calls_of_way(way)),
        ir.SeqTree: lambda way: way.items,
        ir.kinds_other_than(ir.AlternativeState, ir.SeqTree): lambda way: (way,),
    },
)


def _calls_of_way(way: ir.AlternativeState) -> tuple[ir.RefCall, ...]:
    """
    The calls `way` makes, in the order the way makes them. The way makes a tail call with nothing behind it. Or the way
    makes a call and then continues past it. Or the way makes no call.
    """
    return tuple(held for held in (way.first, way.second) if held is not None)


def _first_call_of_way(way: ir.AlternativeState) -> ir.RefCall | None:
    """
    The call `way` makes first, or `None` where it makes none.

    The answer is `first` where a way makes a call and then continues past it. The answer is `second` otherwise. That is
    the call itself where the way is a tail call, and `None` where the way makes no call.
    """
    return way.first if way.first is not None else way.second


def _parts_of_way(way: ir.Node) -> tuple[ir.Node, ...]:
    """
    The parts a question about a way should walk. Those are the guards an alternative's gate asks, then what the way
    performs, in the order the parse arrives at them. A way of another kind gives only what it performs.

    The parse asks a gate before entering the way. A question that leaves the gate out answers about a way the parse may
    not take. The gate's guards take no width and sit among a way's parts as readily as an action would. A walk over a
    way's parts therefore asks the guards the question it already asks of the way's actions.

    A `GatePart` holds the peek as a `LookGuard` among its guards. The peek is therefore among a way's parts.
    """
    return _PARTS_OF_WAY(way)


# The parts of a way. An alternative's gate asks guards ahead of the items. A way of another kind gives its items and no
# guard.
_PARTS_OF_WAY: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the parts a question about a way walks",
    {
        ir.AlternativeState: lambda way: (*way.gate.guards, *_items_of_way(way)),
        ir.kinds_other_than(ir.AlternativeState): _items_of_way,
    },
)


def _ways_or_items(node: ir.Node) -> tuple[ir.Node, ...]:
    """
    The ways that a choice offers, or the items that an alternative performs. A walk calls this to descend in the
    machine's form and in the tree's form alike.

    A choice says its ways as `alternatives` in the machine's form, and as `items` in the tree's form. An alternative
    says its parts by name. A sequence says them in a row.
    """
    return _WAYS_OR_ITEMS(node)


# The ways a choice offers in either form, and the items of a node of another kind.
_WAYS_OR_ITEMS: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the ways a choice offers, or the items an alternative performs",
    {
        ir.ChoiceState: lambda node: node.alternatives,
        ir.AltTree: lambda node: node.items,
        ir.kinds_other_than(ir.ChoiceState, ir.AltTree): _items_of_way,
    },
)


def _way_items(body: ir.Node) -> tuple[tuple[ir.Node, ...], ...]:
    """The ways `body` opens, as runs of items. `one-step-per-item` leaves a body holding these ways."""
    return tuple(_items_of_way(way) for way in _OPENED_WAYS(body))


def _a_way_is_actions_a_call_and_a_continuation(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way is actions, the call it hands control to, and a production that continues.

    The continuation holds what comes past the first call. A second call is where to continue, rather than a third thing
    to do.

    This counts by the way rather than by the item in the wrong place.
    """
    faults = []
    for name, production in grammar.items():
        for items in _way_items(production.body):
            calls = 0
            for item in items:
                if ir.AS_A_CALL(item) is not None:
                    calls += 1
                elif calls:
                    faults.append(f"{name}: a way goes on doing things past the call it hands control to")
                    break
            else:
                if calls > 2:
                    faults.append(
                        f"{name}: a way hands control on more than twice, where an edge is one push and a jump"
                    )
    return faults


_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION = _Invariant(
    "a-way-is-actions-a-call-and-a-continuation", _a_way_is_actions_a_call_and_a_continuation
)


def _every_body_is_a_choice_or_a_set(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a body is a choice or a set.

    A terminal is a set of characters and no more. Anything else is a `ChoiceState`, an ordered list of alternatives. An
    alternative is an `AlternativeState`. An `AlternativeState` holds a gate and actions. An `AlternativeState` may also
    hold a call and a continuation past that call.

    This checks the body and the alternatives of its choice. Within an alternative it reads the actions, the call and
    the continuation. It reads no deeper. An alternative's actions hold no call, and what it calls is a name.
    """
    faults = []
    for name, production in grammar.items():
        body = production.body
        if ir.AS_A_CHAR_SET(body) is not None:
            continue
        choice = ir.AS_A_CHOICE(body)
        if choice is None:
            faults.append(f"{name}: a body that is neither a set nor a choice of alternatives")
            continue
        for held_way in choice.alternatives:
            alternative = ir.AS_AN_ALTERNATIVE(held_way)
            if alternative is None:
                faults.append(f"{name}: a choice holding what is not an alternative")
            elif any(ir.AS_A_CALL(action) is not None for action in alternative.actions):
                faults.append(f"{name}: an alternative doing a call among its actions")
            elif any(
                held is not None and ir.AS_A_CALL(held) is None for held in (alternative.first, alternative.second)
            ):
                faults.append(f"{name}: an alternative handing control to a node that is not a name")
    return faults


_EVERY_BODY_IS_A_CHOICE_OR_A_SET = _Invariant("every-body-is-a-choice-or-a-set", _every_body_is_a_choice_or_a_set)


def _guard_past_an_action_at(actions: Sequence[ir.Node]) -> int | None:
    """The place a guard first comes past an action among `actions`, or `None` where none does."""
    acted = False
    for index, action in enumerate(actions):
        if ir.IS_A_GUARD(action):
            if acted:
                return index
        else:
            acted = True
    return None


def _no_guard_comes_past_an_action(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no guard comes past an action. A way then asks its guards first. The performing parts come next. The call
    and where it continues come last, as `first` and `second` of `AlternativeState`.

    A guard decides. A guard reached past an action decides nothing the parse can act on. The action has already
    happened. A way holding no guard at all passes trivially.
    """
    return [
        f"{name}: a guard comes past an action the way already performed"
        for name, production in grammar.items()
        if (choice := ir.AS_A_CHOICE(production.body)) is not None
        for way in choice.alternatives
        if _guard_past_an_action_at(way.actions) is not None
    ]


_NO_GUARD_COMES_PAST_AN_ACTION = _Invariant("no-guard-comes-past-an-action", _no_guard_comes_past_an_action)


def _guards_in_force(parts: Sequence[ir.Node], at: int, entering: Iterable[ir.Node] = ()) -> tuple[ir.Node, ...]:
    """
    The guards that hold where `parts[at]` sits. This is the accessor for "what the parse has asked about this
    position".

    The places that record a question about the position agree. The first holds the questions a way entering this
    production asked where it entered. `_asked_where_entered` works those out, and a caller passes them in. The second
    holds the questions the gate of the way asks. That gate sits among the parts `_parts_of_way` gives. The third holds
    the questions a guard already passed over asked. A question that moved from any of those places to any other is the
    same question. A single walk therefore reads those places.

    A part that takes characters clears the guards in force. Past a take the parse is somewhere else. A question asked
    before the take was about a position the parse has left.
    """
    held = list(entering)
    for item in parts[:at]:
        if ir.IS_CONSUMING(item):
            held = []
        elif ir.IS_A_GUARD(item):
            held.append(item)
    return tuple(held)


def _admits(guard: ir.Node, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    Answer the states in which `guard` lets a parse through, as a `spaces.SubSpace`.

    A guard takes no width. It says something about the state the parse is in, and no more. A subspace can therefore
    hold as much of a guard as the axes allow. The answer is sound rather than decisive. A constraint the answer states
    is one the guard really makes, and a guard the axes cannot put admits throughout. A way's accepted space therefore
    holds the states the way can succeed in, and may hold more. A state that a gate admits and the space refuses is a
    hole the grammar really has.

    A pair of shapes admit throughout, and both are losses rather than truths. The guard constrains something the answer
    does not say. The first is a set a peek does not pin down. `_peek_spans` answers `None` for such a set. The second
    is a look-behind at any set but `ns-char`. `ns-char` is the set the `is_after_ns_char` axis names. A comparison is
    neither. A comparison the grammar makes is an axis. This function raises on a comparison the grammar does not make.
    Such a comparison does not admit throughout.
    """
    return _ADMITS(guard, grammar)


def _peeked_admits(guard: ir.LookGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states a lookahead admits. Those are the characters its set holds. A set the peek does not pin down admits
    throughout.
    """
    spans = _peek_spans(guard.item, grammar)
    return spaces.COMPLETE if spans is None else spaces.characters(spans)


def _refused_admits(guard: ir.NegLookGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states a negative lookahead admits. Those are the characters the set does not hold, and the end of the stream. A
    set the peek does not pin down admits throughout.
    """
    spans = _peek_spans(guard.item, grammar)
    return spaces.COMPLETE if spans is None else spaces.not_characters(spans)


def _behind_admits(guard: ir.LookBehindGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states a look-behind admits. Those turn on whether the character behind is an `ns-char`. A look-behind at a set
    other than `ns-char` asks about no axis `GuardAnswers` names. Such a look-behind admits throughout.
    """
    spans = _peek_spans(guard.item, grammar)
    named = _peek_spans(ir.RefCall(name="ns-char", args=()), grammar)
    admits = spans is not None and spans == named
    return spaces.where(is_after_ns_char=True) if admits else spaces.COMPLETE


def _quantity(value: ir.Node) -> str | None:
    """
    The parse's quantity `value` reads. The name is a short English phrase. The answer is `None` where `value` reads no
    quantity.

    The indentation and the floor read either way.
    """
    if value == ir.LitValue(value=0):
        return "zero"
    if value in (ir.IndentValue(), ir.ParamValue(name="n")):
        return "the indentation"
    if value == ir.ColumnValue():
        return "the column"
    if value == ir.LenValue(arg=ir.MatchValue()):
        return "the consumed length"
    if value in (ir.GlobalValue(name="f"), ir.ParamValue(name="f")):
        return "the floor"
    return None


# An entry names the axis of a grammar comparison and the side that comparison admits. The comparisons are here, and
# they are exact. A `GuardAnswers` says how the quantities compare. A comparison lets a parse through in exactly the
# answers that agree with it, no more and no fewer.
_COMPARES = {
    ("zero", "<", "the indentation"): ("is_indented", True),
    ("the indentation", "<=", "zero"): ("is_indented", False),
    ("zero", "<", "the column"): ("is_at_line_start", False),
    ("the indentation", "<", "the column"): ("is_not_too_indented", False),
    ("the indentation", "<", "the consumed length"): ("is_consumed_length_past_the_indent", True),
    ("the consumed length", "<=", "the indentation"): ("is_consumed_length_past_the_indent", False),
    ("the consumed length", "<", "the indentation"): ("is_consumed_length_under_the_indent", True),
    ("the indentation", "<=", "the consumed length"): ("is_consumed_length_under_the_indent", False),
    ("the floor", "<=", "the column"): ("is_column_at_least_the_floor", True),
    ("the floor", "<=", "the indentation"): ("is_indent_at_least_the_floor", True),
}

# The module writes a quantity in this form for a comparison it mints rather than reads. A quantity gets a form apiece.
_QUANTITY_NODES = {
    "zero": ir.LitValue(value=0),
    "the indentation": ir.IndentValue(),
    "the column": ir.ColumnValue(),
    "the consumed length": ir.LenValue(arg=ir.MatchValue()),
    "the floor": ir.GlobalValue(name="f"),
}


def _says(axis: str, is_answered: bool) -> ir.Node | None:
    """
    The guard saying `axis` is `is_answered`. The answer is `None` where nothing the grammar can write says it.
    """
    if (axis, is_answered) == ("is_at_line_start", True):
        return ir.StartOfLineGuard()
    for (first, said, second), held in _COMPARES.items():
        if held == (axis, is_answered):
            kind = ir.IsLessThanGuard if said == "<" else ir.IsLessEqualGuard
            return kind(a=_QUANTITY_NODES[first], b=_QUANTITY_NODES[second])
    return None


def _compared_admits(
    guard: ir.IsLessThanGuard | ir.IsLessEqualGuard, said: str, _grammar: dict[str, ir.Prod]
) -> spaces.SubSpace:
    """
    A comparison admits an answer that agrees with the question it asks.

    A shape `_COMPARES` lacks raises a `ValueError`. A guard that admits throughout joins the guards that constrain
    nothing. Such a guard hides a case the grammar cannot decide.
    """
    shape = (_quantity(guard.a), said, _quantity(guard.b))
    if shape not in _COMPARES:
        raise ValueError(f"no axis says whether {shape[0]} is {said} {shape[2]}")
    axis, answered = _COMPARES[shape]
    return spaces.where(**{axis: answered})


def _is_less_than_admits(guard: ir.IsLessThanGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """The states a `(<)` admits. `_compared_admits` answers under the axis the quantities name."""
    return _compared_admits(guard, "<", grammar)


def _is_less_equal_admits(guard: ir.IsLessEqualGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """The states a `(<=)` admits. `_is_less_than_admits` reads a `(<)` the same way."""
    return _compared_admits(guard, "<=", grammar)


_ADMITS: ir.Question[spaces.SubSpace] = ir.Question(
    "the states a guard lets a parse through in, as a `spaces.SubSpace` of the states the guard really admits",
    {
        ir.LookGuard: _peeked_admits,
        ir.NegLookGuard: _refused_admits,
        ir.LookBehindGuard: _behind_admits,
        ir.IsLessThanGuard: _is_less_than_admits,
        ir.IsLessEqualGuard: _is_less_equal_admits,
        ir.StartOfLineGuard: lambda guard, grammar: spaces.where(is_at_line_start=True),
        ir.EndOfStreamGuard: lambda guard, grammar: spaces.characters(is_at_end=True),
        ir.DidMatchFullSpanGuard: lambda guard, grammar: spaces.where(did_match_full_span=True),
        ir.DidConsumeSinceOpenGuard: lambda guard, grammar: spaces.where(did_consume_since_open=True),
    },
)


def _accepted_part(part: ir.Node, grammar: dict[str, ir.Prod], known: Mapping[str, spaces.SubSpace]) -> spaces.SubSpace:
    """
    The states that a part of a way takes a character in, as a `spaces.SubSpace`. The answer is `NOWHERE` where the part
    takes none.

    A call answers from `known`, what the fixpoint below has reached for its production. A take of a set answers with
    that set. A consume answers with the set it names. A trimmed run names that set `full`.
    """
    return _ACCEPTED_PART(part, grammar, known)


def _accepted_characters(taken: ir.Node, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states a take of a set runs in. Those are the characters the set holds. A set nothing pins down runs at any
    character.
    """
    spans = _peek_spans(taken, grammar)
    return spaces.ANY_CHARACTER if spans is None else spaces.characters(spans)


_ACCEPTED_PART: ir.Question[spaces.SubSpace] = ir.Question(
    "the states a part of a way consumes a character in, as a `spaces.SubSpace`",
    {
        ir.RefCall: lambda part, grammar, known: known[part.name],
        ir.CONSUMES_NOTHING: lambda part, grammar, known: spaces.NOWHERE,
        # A set that is the take itself names its own characters.
        ir.CharSet: lambda part, grammar, known: _accepted_characters(part, grammar),
        # A consume names the characters it takes, under any gate in front. A reader takes the field holding the set by
        # name, and the kind of consume decides that name. A trimmed run names the field `full`, and another kind names
        # it `set`.
        (ir.ConsumeCharAction, ir.ConsumeLimitedSpanAction, ir.ConsumeSpanAction): (
            lambda part, grammar, known: _accepted_characters(part.set, grammar)
        ),
        ir.ConsumeTrimmedSpanAction: lambda part, grammar, known: _accepted_characters(part.full, grammar),
    },
)


def _accepted_way(
    way: ir.Node, grammar: dict[str, ir.Prod], known: Mapping[str, spaces.SubSpace], ways: _Ways
) -> spaces.SubSpace:
    """
    The states `way` consumes a character in, as a `spaces.SubSpace`.

    Read off what the way consumes and no more. **A guard says nothing here.** `_admits` answers what admits a way. A
    consume names the set the grammar wrote it to take.

    This is empty where a way consumes nothing at all. Whether a way can do so is `_split_ways`' answer already.
    """
    return _accepted_parts(_parts_of_way(way), grammar, known, ways)


def _accepted_parts(
    parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], known: Mapping[str, spaces.SubSpace], ways: _Ways
) -> spaces.SubSpace:
    """
    The states a run of a way's parts consumes a character in, as a `spaces.SubSpace`.

    A part contributes the states it consumes a character in. A part that must consume a character ends the walk. A part
    that may consume nothing leaves the walk going, and what sits behind it comes in too. The walk reads `ways` only to
    decide whether to walk past a part.
    """
    consumes = spaces.NOWHERE
    for part in parts:
        if ir.IS_A_GUARD(part):
            continue
        consumes = consumes | _accepted_part(part, grammar, known)
        if not _is_nullable(part, grammar, ways):
            break
    return consumes


def _accepted_body(
    body: ir.Node, grammar: dict[str, ir.Prod], known: Mapping[str, spaces.SubSpace], ways: _Ways
) -> spaces.SubSpace:
    """
    The states a production's body can begin taking a character in. That is the union of the ways where a body offers
    ways, and the characters a body holds where the body is a set. A set the grammar leaves open answers at any
    character. A body of another kind makes the `Question` behind `_PEEK_SPANS` raise. That says nothing rather than
    something untrue.
    """
    choice = ir.AS_A_CHOICE(body)
    if choice is not None:
        space = spaces.NOWHERE
        for way in choice.alternatives:
            space = space | _accepted_way(way, grammar, known, ways)
        return space
    spans = _peek_spans(body, grammar)
    return spaces.ANY_CHARACTER if spans is None else spaces.characters(spans)


def _accepted_spaces(grammar: dict[str, ir.Prod]) -> dict[str, spaces.SubSpace]:
    """
    `{name: spaces.SubSpace}`, the states a production can begin taking a character in.

    This is a least fixed point from `NOWHERE`. A production reaching back to itself contributes nothing until some way
    of it says otherwise.

    A production's answer can change only where a callee's has. A callee that has just grown puts its callers back on
    the worklist of `ir.fixed_point`.
    """
    ways = _split_ways(grammar)
    return ir.fixed_point(
        "the states a production can begin taking a character in",
        {name: spaces.NOWHERE for name in grammar},
        lambda name, known: _accepted_body(grammar[name].body, grammar, known, ways),
        _callers(grammar),
    )


def _callers(grammar: dict[str, ir.Prod]) -> dict[str, set[str]]:
    """`{callee: the names of the productions calling it}`. The map holds the callees `grammar` defines."""
    callers: dict[str, set[str]] = {}
    for name, production in grammar.items():
        for node in _held(production.body):
            if (call := ir.AS_A_CALL(node)) is not None and call.name in grammar:
                callers.setdefault(call.name, set()).add(name)
    return callers


def _inverted(named: Mapping[str, Iterable[str]]) -> dict[str, set[str]]:
    """`{held: the keys of `named` holding it}`. The inverse of `_callers` gives the names a production calls."""
    inverse: dict[str, set[str]] = {}
    for key, held in named.items():
        for one in held:
            inverse.setdefault(one, set()).add(key)
    return inverse


def _can_be_refused(node: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str] = frozenset()) -> bool:
    """
    Whether some input makes `node` fail and hands it back. Matching and raising are the other outcomes.

    This says whether a parse can reach the ways behind a way. A choice goes on to the next way exactly where the parse
    hands this one back. A way that no input refuses is therefore the last way the machine takes. Matching and raising
    both stop the choice, and neither leaves the way behind anything to enter on.

    `seen` holds the productions the walk is already inside. A recursion reached again says nothing new.
    """
    return _CAN_BE_REFUSED(node, grammar, seen)


def _can_a_way_be_refused(way: ir.AlternativeState, grammar: dict[str, ir.Prod], seen: frozenset[str]) -> bool:
    """
    Whether some input makes a way fail and hands it back. This reads the items in turn. It stops at a `(cut)`.

    A gate sits in front of the way. A way whose gate holds a guard is therefore refused wherever the gate declines.
    Past a `CutAction`, or inside a region a `PushMessageAction` opened, a failure is the message rather than a way
    handed back. The walk stops at a `(cut)` and passes over a committed item. A failure travelling out of a region that
    has not closed takes the message the region names. A failure out of a region that has closed goes back like any
    other.
    """
    if _does_a_gate_decide(way):
        return True
    open_regions = 0
    for item in map(_unheld, _items_of_way(way)):
        open_regions += ir.REGIONS_OPENED(item)
        if ir.AS_A_CUT(item) is not None:
            return False  # A failure past a `CutAction` is the error the cut names.
        if ir.AS_A_COMMIT(item) is not None:
            continue  # A failure inside a `CommitWrapper` raises the commit's error.
        if not open_regions and _can_be_refused(item, grammar, seen):
            return True
    return False


def _does_a_gate_decide(way: ir.Node) -> bool:
    """
    Whether the way is an alternative whose gate holds a guard. `interpreter._try_way` enters such a way only where the
    gate holds, and asks the gate before the way runs.
    """
    return (alternative := ir.AS_AN_ALTERNATIVE(way)) is not None and bool(alternative.gate.guards)


_CAN_BE_REFUSED: ir.Question[bool] = ir.Question(
    "whether some input makes a match fail and hands it back. Matching and raising are the other outcomes.",
    {
        # An action does not go back. A gate that admitted an action and then saw it fail lied. `interpreter._try_span`
        # raises on such a lie rather than declining.
        (
            *ir.ACTIONS,
            ir.ConsumeCharAction,
            ir.ConsumeLimitedSpanAction,
            ir.ConsumeSpanAction,
            ir.ConsumeTrimmedSpanAction,
            ir.EmptyTree,
            *ir.VALUE_KINDS,
        ): False,
        # A guard declines. A bare set is a match here rather than a guard, and declines too. `no-char-set-is-an-item`
        # ends the bare sets.
        (
            ir.CharSet,
            ir.DiffSet,
            ir.FailTree,
            ir.InvalidSet,
            ir.OneCharSet,
            ir.RangeSet,
            *ir.GUARDS,
        ): True,
        (ir.OptTree, ir.StarTree): False,
        ir.PlusTree: lambda node, grammar, seen: _can_be_refused(node.item, grammar, seen),
        # A count of none takes nothing under any input. any other count turns on what it repeats.
        ir.RepTree: lambda node, grammar, seen: not (
            (literal := ir.AS_A_LITERAL_VALUE(node.count)) is not None
            and isinstance(literal.value, int)
            and literal.value <= 0
        )
        and _can_be_refused(node.item, grammar, seen),
        # A recursion reached again says nothing new. The walk is already judging that production further out.
        ir.RefCall: lambda node, grammar, seen: node.name not in seen
        and _can_be_refused(grammar[node.name].body, grammar, seen | {node.name}),
        (ir.AlternativeState, ir.SeqTree): _can_a_way_be_refused,
        # A choice goes back only where the ways it offers go back. A way that matches makes the choice match.
        (ir.AltTree, ir.ChoiceState): lambda node, grammar, seen: all(
            _can_be_refused(way, grammar, seen) for way in _ways_or_items(node)
        ),
        # `interpreter._try_committed` records an error where the commit's item fails. the region does not go back.
        ir.CommitWrapper: False,
        (ir.MaxWrapper, ir.RecoverWrapper, ir.TokenWrapper, ir.Wrapper): lambda node, grammar, seen: (
            node.item is not None and _can_be_refused(node.item, grammar, seen)
        ),
        ir.BindTree: lambda node, grammar, seen: _can_be_refused(node.cond, grammar, seen),
        # `_monomorphize` settles a switch. A switch goes back only where the branches it could take go back.
        ir.CaseTree: lambda node, grammar, seen: all(
            _can_be_refused(item, grammar, seen)
            for item in [branch.item for branch in node.branches] + ([node.default] if node.default else [])
        ),
    },
)

# The ways a choice offers in either form. A node of another kind offers none.
_CHOICE_WAYS: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the ways a choice offers",
    {
        ir.ChoiceState: lambda node: node.alternatives,
        ir.AltTree: lambda node: node.items,
        ir.kinds_other_than(ir.ChoiceState, ir.AltTree): (),
    },
)


def _every_option_is_reachable(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that some input reaches a way a choice offers. An input can refuse the ways in front of such a way.

    A choice may hold a single way that no input refuses, and that way comes last. There it is the fallthrough a choice
    ends in.
    """
    faults = []
    for name, production in grammar.items():
        for node in _held(production.body):
            for way in _CHOICE_WAYS(node)[:-1]:
                if not _can_be_refused(way, grammar):
                    faults.append(f"{name}: a way no input refuses, with a way behind it")
    return faults


_EVERY_OPTION_IS_REACHABLE = _Invariant("every-option-is-reachable", _every_option_is_reachable)

# Whether a body opens ways of its own. A choice, an alternation and a recovery do. A body of another kind is a way.
_DOES_OPEN_WAYS: ir.Question[bool] = ir.Question(
    "whether a body opens ways of its own",
    {
        (ir.AltTree, ir.ChoiceState, ir.RecoverWrapper): True,
        ir.kinds_other_than(ir.AltTree, ir.ChoiceState, ir.RecoverWrapper): False,
    },
)


def _mint_continuations(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Give the parts past a way's call a production of their own. A way is then actions, a call, and where to continue.

    `a P1 b P2 c` becomes `a`, the call `P1`, and a call to a minted production. That production holds `b P2 c`. That
    splits the same way until no part comes past a call. A way ending in a pair of calls is already that shape. The
    second call is where to continue rather than a third thing to do. This function mints no production for the second
    call.

    A scope opened before the call and closed after it comes apart here. `lower-scopes` already split that scope apart.
    A Push and Pop pair sits on the stack the parse holds, rather than in the frame of the running match.
    `interpreter._popped` takes the pop's entry off that stack. The `PushCodeAction` stays in the way, and its
    `PopCodeAction` moves into the continuation. The pair still finds one another.
    """
    minted = {}

    def split(items: tuple[ir.Node, ...], owner: str) -> tuple[ir.Node, ...]:
        for index, item in enumerate(items):
            if ir.AS_A_CALL(item) is None:
                continue
            rest = items[index + 1 :]
            if not rest or (len(rest) == 1 and ir.AS_A_CALL(rest[0]) is not None):
                return items
            name = namer.fresh(owner)
            parts = split(rest, owner)
            minted[name] = ir.Prod(
                grammar[owner].number, name, (), parts[0] if len(parts) == 1 else ir.SeqTree(items=parts)
            )
            return (*items[: index + 1], ir.RefCall(name=name, args=()))
        return items

    def way(node: ir.Node, owner: str) -> ir.Node:
        items = split(ir.ITEMS_IN_A_SEQUENCE(node), owner)
        return items[0] if len(items) == 1 else ir.SeqTree(items=items)

    def body(node: ir.Node, owner: str) -> ir.Node:
        if _DOES_OPEN_WAYS(node):
            return _with_inner_ways(node, lambda opened: way(opened, owner))
        return way(node, owner)

    split_ways = {
        name: dataclasses.replace(production, body=body(production.body, name)) for name, production in grammar.items()
    }
    return {**split_ways, **minted}


def _as_alternative(way: ir.Node, recovery: ir.Node | None = None) -> ir.AlternativeState:
    """
    Represent a way of a body in the machine's form. The alternative holds the actions the way performs, the call the
    way hands control to, and where the way continues.

    A way with a single call is a tail-goto, and that call is where it continues. With a pair, the first is the call the
    way comes back from and the second is where the way goes then.

    This function leaves the gate of the alternative empty.
    """
    items = ir.ITEMS_IN_A_SEQUENCE(way)
    calls = tuple(call for item in items if (call := ir.AS_A_CALL(item)) is not None)
    actions = tuple(item for item in items if ir.AS_A_CALL(item) is None)
    first, second = (calls[0], calls[1]) if len(calls) > 1 else (None, calls[0] if calls else None)
    return ir.AlternativeState(gate=ir.GatePart(), actions=actions, first=first, second=second, recover=recovery)


def _build_alternatives(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a body in the words of the machine. That is a set of characters, or the ordered list of alternatives the parse
    takes one of.

    A way opens on a gate. An `AlternativeState` holds the way. Its actions follow the gate. A way holds at most a call
    and the continuation past it. A tail call sets only `second`. `interpreter._try_way` runs the gate's guards, then
    the actions, then the call with its continuation.
    """

    return {
        name: dataclasses.replace(production, body=_AS_A_MACHINE_BODY(production.body))
        for name, production in grammar.items()
    }


# A body in the machine's form. A set stays a set. A recovery becomes a single alternative that holds the recovery. An
# alternation becomes an alternative per way. A body of another kind becomes a single alternative.
_AS_A_MACHINE_BODY: ir.Question[ir.Node] = ir.Question(
    "a body in the machine's form",
    {
        ir.CharSet: lambda body: body,
        ir.RecoverWrapper: lambda body: ir.ChoiceState(
            alternatives=(_as_alternative(body.item, recovery=body.recovery),)
        ),
        ir.AltTree: lambda body: ir.ChoiceState(alternatives=tuple(_as_alternative(way) for way in body.items)),
        ir.kinds_other_than(ir.CharSet, ir.RecoverWrapper, ir.AltTree): lambda body: ir.ChoiceState(
            alternatives=(_as_alternative(body),)
        ),
    },
)


def _every_ungated_way_has_actions_or_a_call(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no ungated way both acts and calls through `first`. `_ungated_ways` picks the ungated ways.

    A guard crosses an action, or it stays put. `GUARD_CROSSES_ACTION` says which. An action in front of a guard can
    refuse that guard.

    This check asks nothing of a way that has a gate. The parse enters such a way on the questions that way asks.
    `interpreter._try_way` performs the actions of a way, and then makes its calls. `every-conditional-way-is-gated` at
    none leaves no ungated way, and this check then has no way to judge.

    This check counts only `first` as a call. In an `AlternativeState`, `first` is the call and `second` the
    continuation. A `second` with no `first` is a tail call. A way that acts and then makes a tail call through `second`
    passes.
    """
    return [
        f"{name}: an ungated way acts before it calls. A guard of the callee cannot reach those actions."
        for name, way in _ungated_ways(grammar)
        if way.first is not None and way.actions
    ]


_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL = _Invariant(
    "every-ungated-way-has-actions-or-a-call", _every_ungated_way_has_actions_or_a_call
)

# The alternatives of a choice. A body of another kind holds none.
_ALTERNATIVES: ir.Question[tuple[ir.AlternativeState, ...]] = ir.Question(
    "the alternatives of a choice",
    {ir.ChoiceState: lambda node: node.alternatives, ir.kinds_other_than(ir.ChoiceState): ()},
)

# A choice with `told` applied to its ways. A body of another kind keeps its form.
_WAYS_RETOLD = _reshaping(
    "a choice with its ways rewritten",
    ir.ChoiceState,
    lambda node, told: dataclasses.replace(node, alternatives=tuple(told(way) for way in node.alternatives)),
)

# A choice with a way replaced by the run of ways `told` gives for it. A body of another kind keeps its form.
_WAYS_SPLIT = _reshaping(
    "a choice with its ways split",
    ir.ChoiceState,
    lambda node, told: dataclasses.replace(
        node, alternatives=tuple(one for way in node.alternatives for one in told(way))
    ),
)


def _mint_call_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    A way makes a call after its actions. This function moves that call into a minted state. The minted state's way
    holds the call and its return point.

    The gate and the actions stay on the way. The way ends on a tail call to the minted state. The call's recovery moves
    with the call into the minted state's way.
    """
    minted = {}

    def told(name: str, way: ir.AlternativeState) -> ir.AlternativeState:
        if way.first is None or not way.actions:
            return way
        held = namer.fresh(name)
        minted[held] = ir.Prod(
            grammar[name].number,
            held,
            (),
            ir.ChoiceState(
                alternatives=(
                    ir.AlternativeState(gate=ir.GatePart(), first=way.first, second=way.second, recover=way.recover),
                )
            ),
        )
        return ir.AlternativeState(gate=way.gate, actions=way.actions, second=ir.RefCall(name=held, args=()))

    cut = {
        name: dataclasses.replace(
            production, body=_WAYS_RETOLD(production.body, lambda way, owner=name: told(owner, way))
        )
        for name, production in grammar.items()
    }
    return {**cut, **minted}


def _mint_guard_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Give the parts from a way's first late guard onward a state of their own. The way keeps its gate and the parts up to
    there, then hands control on. The guard and what sits behind it are the minted state's way.

    This is a cut and not a reordering. A way's parts run in order. The parse enters the tail as a production where the
    way left off. The tail therefore runs the same parts in the same order. The guard asks at exactly the position it
    asked at before. This has to know nothing of an action's take or of its writes, and no condition says which guards
    may go.

    The way keeps its gate, the actions before the guard and a tail call. The calls the way made and the recovery riding
    them go with the tail.

    This runs until nothing moves. A minted state holds the guard at its head, and the tail follows. That tail may ask
    late again.
    """

    def told(name: str, way: ir.AlternativeState) -> tuple[ir.AlternativeState, dict[str, ir.Prod]]:
        at = _guard_past_an_action_at(way.actions)
        if at is None:
            return way, {}
        held = namer.fresh(name)
        minted = {
            held: ir.Prod(
                grammar[name].number,
                held,
                (),
                ir.ChoiceState(
                    alternatives=(
                        ir.AlternativeState(
                            gate=ir.GatePart(),
                            actions=way.actions[at:],
                            first=way.first,
                            second=way.second,
                            recover=way.recover,
                        ),
                    )
                ),
            )
        }
        return (
            ir.AlternativeState(gate=way.gate, actions=way.actions[:at], second=ir.RefCall(name=held, args=())),
            minted,
        )

    for _round in ir.rounds("mint-guard-states"):
        cut: dict[str, ir.Prod] = {}
        minted: dict[str, ir.Prod] = {}
        for name, production in grammar.items():
            choice = ir.AS_A_CHOICE(production.body)
            if choice is None:
                cut[name] = production
                continue
            ways: list[ir.AlternativeState] = []
            for way in choice.alternatives:
                held, made = told(name, way)
                ways.append(held)
                minted.update(made)
            cut[name] = dataclasses.replace(production, body=ir.ChoiceState(alternatives=tuple(ways)))
        settled = {**cut, **minted}
        if settled == grammar:
            return grammar
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _every_guard_is_in_a_gate(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a guard a way asks sits in the gate rather than among the actions.

    The parse asks the gate of an `AlternativeState` where it enters the way. `no-guard-comes-past-an-action` checks
    that a guard a way holds sits in the run before the way performs anything. The guard and the gate therefore sit at
    the same position. A guard left among the actions asks its question only when the parse enters the way. The gate of
    the `AlternativeState` decides that entering.

    The check reports a fault per guard rather than per way. A way holding a pair of guards owes a pair of moves.
    """
    return [
        f"{name}: a way asks a guard among its actions. The gate of that way asks at the same position."
        for name, production in grammar.items()
        if (choice := ir.AS_A_CHOICE(production.body)) is not None
        for way in choice.alternatives
        for action in way.actions
        if ir.IS_A_GUARD(action)
    ]


_EVERY_GUARD_IS_IN_A_GATE = _Invariant("every-guard-is-in-a-gate", _every_guard_is_in_a_gate)


def _hoist_guards_to_gates(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Move the guards that open a way's actions into the gate.

    A guard takes no width. `interpreter._MATCHED` runs a comparison, `<start-of-line>` and `<end-of-stream>` without
    moving the position. `interpreter._try_probe` rewinds the position after a lookahead asks. Asking at the gate and
    asking where the guard sits are the same question. An action between the gate and the guard could change the answer.
    `no-guard-comes-past-an-action` leaves a guard in the run before the way performs anything, and no action sits
    between the gate and the guard. The step therefore needs no account of which action writes a value a comparison
    reads, and no account of the commits and their places.

    The step puts the question in the gate, where a caller can see it. A caller reaches a guard among the actions only
    after it enters the way. In the gate the guard decides that entering.
    """

    def hoisted(way: ir.AlternativeState) -> ir.AlternativeState:
        moving = []
        for action in way.actions:
            if not ir.IS_A_GUARD(action):
                break
            moving.append(action)
        if not moving:
            return way
        return dataclasses.replace(
            way,
            gate=ir.GatePart(guards=(*way.gate.guards, *moving)),
            actions=way.actions[len(moving) :],
        )

    return {
        name: dataclasses.replace(production, body=_WAYS_RETOLD(production.body, hoisted))
        for name, production in grammar.items()
    }


def _merge_gate_peeks(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a gate's questions about the character in front of it as one.

    A pair of peeks in a gate ask about the same character at the same position. The sets therefore say between them
    what a single set says. The `LookGuard`s together admit the intersection of their sets. The set any `NegLookGuard`
    refuses stays refused. A gate holding both admits the first less the second. The gate keeps its other guards.

    A gate may read ahead twice where no single set says both reads. Such a gate is a fault rather than a shape to leave
    in place. An `EndOfStreamGuard` holds no set. It does not fold into a `LookGuard`. It does not sit beside a
    `LookGuard` either. The machine would ask twice about a character the parse reads once. The grammar would not say
    which answer the machine acts on.
    """

    def merged(way: ir.AlternativeState) -> ir.AlternativeState:
        ahead = [guard for guard in way.gate.guards if ir.IS_LOOKING_AHEAD(guard)]
        looks = [look for guard in ahead if (look := ir.AS_A_LOOK(guard)) is not None]
        nots = [negative for guard in ahead if (negative := ir.AS_A_NEGATIVE_LOOK(guard)) is not None]
        if len(ahead) < 2:
            return way
        if len(looks) + len(nots) != len(ahead):
            kinds = ", ".join(sorted(type(guard).__name__ for guard in ahead))
            raise AssertionError(
                f"a gate reads ahead as {kinds}. That form asks a pair of questions about a single character."
            )
        rest = [
            guard for guard in way.gate.guards if ir.AS_A_LOOK(guard) is None and ir.AS_A_NEGATIVE_LOOK(guard) is None
        ]
        refused = chars.merged_spans([span for guard in nots for span in getattr(guard.item, "spans")])
        if looks:
            admitted = list(getattr(looks[0].item, "spans"))
            for guard in looks[1:]:
                admitted = chars.intersected_spans(admitted, list(getattr(guard.item, "spans")))
            asked: ir.Node = ir.LookGuard(item=_spans_node(chars.subtracted_spans(admitted, refused)))
        else:
            asked = ir.NegLookGuard(item=_spans_node(refused))
        return dataclasses.replace(way, gate=ir.GatePart(guards=(*rest, asked)))

    return {
        name: dataclasses.replace(production, body=_WAYS_RETOLD(production.body, merged))
        for name, production in grammar.items()
    }


def _every_gate_looks_ahead_at_most_once(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a gate reads the character in front of the parse at most once.

    The lookahead guards ask about the same character at the same position.

    This covers the lookahead. A look behind asks about something else. A guard on the column or on the indentation asks
    about something else too. A gate may hold such a guard beside its lookahead.
    """
    return [
        f"{name}: a gate reads what is in front of it {held} times, where once would say the same"
        for name, production in grammar.items()
        if (choice := ir.AS_A_CHOICE(production.body)) is not None
        for way in choice.alternatives
        for held in (sum(ir.IS_LOOKING_AHEAD(guard) for guard in way.gate.guards),)
        if held > 1
    ]


_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE = _Invariant(
    "every-gate-looks-ahead-at-most-once", _every_gate_looks_ahead_at_most_once
)


def _decided_ways(grammar: dict[str, ir.Prod]) -> dict[str, tuple[ir.AlternativeState, ...]]:
    """
    The ways a production decides between, as `{name: ways}`.

    A body offering a single way is no decision. A caller reaches such a way by calling the production rather than by
    picking a way. The last way of a choice is that choice's else. The parse enters that way where the ways in front
    refused, rather than on a question of its own. A machine has to tell the ways in front apart. This function names
    those ways once, and callers reuse that answer.
    """
    return {
        name: choice.alternatives[:-1]
        for name, production in grammar.items()
        if (choice := ir.AS_A_CHOICE(production.body)) is not None and len(choice.alternatives) > 1
    }


def _ungated_ways(grammar: dict[str, ir.Prod]) -> list[tuple[str, ir.AlternativeState]]:
    """
    The gateless ways a production decides to enter, as `(name, way)` pairs.

    This function says once what "ungated" means, and callers reuse that answer. An ungated way is a way a production
    decides between. Its own gate holds no guard, and some path into the production asked none. The parse enters the
    production where the way begins. A guard asked there is a guard asked at the way's start.
    """
    entering = _asked_where_entered(grammar)
    return [
        (name, way)
        for name, ways in _decided_ways(grammar).items()
        for way in ways
        if not way.gate.guards and not all(entering[name])
    ]


def _every_conditional_way_is_gated(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way something decides to enter has a gate.

    The question is separate from the place somebody asked it. Take a guard the ways entering this production asked.
    That guard asks at the position where the parse enters this way. `_asked_where_entered` says so. A hoist that takes
    a guard up to the callers moves the guard rather than losing it. A way with a guard in its gate passes. A way with
    an empty gate fails where a path into its production asked nothing. `every-consume-is-protected-by-a-gate` accepts a
    question that a call further up asked in the same way.

    A pair of shapes are not a decision. This asks nothing of that pair. The first is a way nothing chooses to enter.
    That is a body offering a single way. A caller reaches it by calling the production and not by picking a way. The
    second is the last way of a choice. `_decided_ways` calls that way the choice's else. An empty gate there is the
    fallthrough. The parse still enters the choice's last way where no gate fired.

    Whether the gates of a choice tell its ways apart is a later question.
    """
    return [
        f"{name}: a way nothing has asked anything about, where another way sits behind it"
        for name, _way in _ungated_ways(grammar)
    ]


_EVERY_CONDITIONAL_WAY_IS_GATED = _Invariant("every-conditional-way-is-gated", _every_conditional_way_is_gated)


# The state the questions below share, per grammar somebody has asked them about. `kept_for` keeps the grammar itself
# beside the tables. A table is a fixed point over the whole grammar.
_TABLES: ir.Kept = {}


def _leaf_tables(
    grammar: dict[str, ir.Prod],
) -> tuple[dict[str, spaces.SubSpace], _Ways, _Entered]:
    """
    Work out `(accepted, ways, entering)` once for a grammar and keep the result. `accepted` holds the states a
    production begins taking in. `ways` says whether a production reads, matches empty, or does both. `entering` holds
    the questions the parse asks on entry.
    """
    return ir.kept_for(
        _TABLES, grammar, lambda: (_accepted_spaces(grammar), _split_ways(grammar), _asked_where_entered(grammar))
    )


def _ways_consuming_by_themselves(grammar: dict[str, ir.Prod], ways: _Ways) -> list[tuple[str, ir.AlternativeState]]:
    """
    The ways whose first action is a consume of their own, as `(name, way)` pairs.

    The parse enters such a way on what that action reads. The walk stops there. A call or a continuation may follow
    that action, and neither changes the answer. A way that consumes and then calls therefore answers exactly as a way
    that only consumes.

    This leaves out a way whose first take belongs to a callee. The set of characters such a way consumes belongs to
    that callee, and the callee's ways say it. That is a wider question than this one asks.
    """
    found = []
    for name, production in grammar.items():
        choice = ir.AS_A_CHOICE(production.body)
        if choice is None:
            continue
        for way in choice.alternatives:
            for part in _parts_of_way(way):
                if ir.IS_A_GUARD(part):
                    continue
                if not _is_nullable(part, grammar, ways):
                    if ir.AS_A_CALL(part) is None:
                        found.append((name, way))
                    break
    return found


def _gated_by(path: Iterable[ir.Node], way: ir.AlternativeState, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states in which a parse may enter `way` along a path into its production, as a `spaces.SubSpace`.

    `_asked_where_entered` hands back the paths into a production, and `path` is one of them. `_entry_and_exit_spaces`,
    `spaces_of_ways` and `_does_reach_nothing` pass the empty path instead, and the way's gate then decides the set.
    That path holds the guards the ways leading here asked since the last take. A caller that consumed before its call
    asks about somewhere else. The parse asks the way's gate at that same position, and that gate is part of the set
    admitting the way. The set therefore takes the path's guards and the gate together.
    """
    space = spaces.COMPLETE
    for guard in (*path, *way.gate.guards):
        space = space & _admits(guard, grammar)
    return space


def _accepted_and_gated_charsets_are_equal(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way consuming a character of its own consumes exactly the characters that admit that way.

    A difference means the parse enters the way on a character the way cannot consume. The difference can also mean the
    way consumes a character nothing brings.

    Read over the paths together rather than a path at a time. A single path may be narrower than the way consumes, and
    that is ordinary.

    The comparison runs answer by answer, and on the characters only. A path may reach a way under fewer answers than
    the way has an opinion about. A caller may know the parse is at a line start where the way asks only about the
    character. The caller then knows more than the way, and the caller and the way do not disagree. An answer nothing
    reaches goes uncompared.

    This asks the ways whose first consume is their own, under any actions past that consume. A way consuming no
    character goes unasked. There are no characters for its gate to be equal to. A way whose first consume belongs to a
    callee goes unasked too.
    """
    accepted, ways, entering = _leaf_tables(grammar)
    faults = []
    for name, way in _ways_consuming_by_themselves(grammar, ways):
        accept = _accepted_way(way, grammar, accepted, ways)
        gated = spaces.NOWHERE
        for path in entering[name]:
            gated = gated | _gated_by(path, way, grammar)
        for guard_answers in spaces.ALL_GUARD_ANSWERS:
            entered, taken = gated.under(guard_answers), accept.under(guard_answers)
            if entered and (entered.spans, entered.is_at_end) != (taken.spans, taken.is_at_end):
                faults.append(f"{name}: a parse enters a way on characters the way does not consume")
                break
    return faults


_ACCEPTED_AND_GATED_CHARSETS_ARE_EQUAL = _Invariant(
    "accepted-and-gated-charsets-are-equal", _accepted_and_gated_charsets_are_equal
)


def _does_the_same(one: ir.AlternativeState, other: ir.AlternativeState) -> bool:
    """
    Whether a pair of ways of a choice do the same thing. The gate the parse enters on makes no difference.

    A pair of ways alike but for their gates are a single way reached along a pair of routes.
    """
    return dataclasses.replace(one, gate=ir.GatePart()) == dataclasses.replace(other, gate=ir.GatePart())


def _entered_in(
    name: str, way: ir.AlternativeState, grammar: dict[str, ir.Prod], entering: _Entered
) -> spaces.SubSpace:
    """
    The states that a parse may enter `way` in, as a `spaces.SubSpace`. That is the gate crossed with the paths reaching
    the way.
    """
    held = spaces.NOWHERE
    for path in entering[name]:
        held = held | _gated_by(path, way, grammar)
    return held


def _no_choice_ways_partially_overlap(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that the parse enters a pair of ways something decides between in the same states, or in none of the same.

    **Apart** is where no state enters both and the input itself says which to take. **Together** is where the states
    enter both. The input says nothing there. Something else has to tell the ways apart. **Half apart** is where the
    machine has to tell a state in the overlap from a state just outside it.

    A grammar passing this check holds no pair half apart, except a pair of ways that do the same thing but for their
    gates. The parse enters both ways of any other pair in exactly the same states, or in none of the same.

    This reads a way's gate as `accepted-and-gated-charsets-are-equal` reads it. The answer crosses the states the way's
    gate admits with the states the paths into its production ask for together. A guard that a call hoists up asks at
    the position where the way begins. The guard narrows the ways of the choice alike, and an overlap outside it is an
    overlap no parse reaches.

    The choice's else is not among the pair. This asks nothing of it. The parse enters the else in the states the ways
    in front left. That is a shape rather than a question. A machine has to tell apart the ways it decides between. The
    else is where the machine has failed at that.

    A gate is a set of characters at this position crossed with the state the parse is in. A `GuardAnswers` holds the
    state, as booleans beside the characters. `_ADMITS` names the kinds a guard can hold.
    """
    faults = []
    spaces_of, decided = _spaces_paths_enter_ways_in(grammar), _decided_ways(grammar)
    for name, held in spaces_of.items():
        ways = decided[name]
        faults += [
            f"{name}: a parse enters a way in some of the states that admit the way behind, and not all of them"
            for at, space in enumerate(held)
            if any(
                space & other and space != other and not _does_the_same(ways[at], ways[behind])
                for behind, other in enumerate(held[at + 1 :], at + 1)
            )
        ]
    return faults


_NO_CHOICE_WAYS_PARTIALLY_OVERLAP = _Invariant("no-choice-ways-partially-overlap", _no_choice_ways_partially_overlap)


# The states a parse enters a decided way in. `_SPACES_PATHS_ENTER_WAYS_IN` holds a table per grammar. The walk behind a
# table is expensive. `_SPACES_PATHS_ENTER_WAYS_IN` keeps the tables through `kept_for`, the way `_TABLES` keeps the
# tables of `_leaf_tables`, and no asker walks again.
_SPACES_PATHS_ENTER_WAYS_IN: ir.Kept = {}


def _spaces_paths_enter_ways_in(grammar: dict[str, ir.Prod]) -> dict[str, list[spaces.SubSpace]]:
    """
    `{name: [space]}`, the states a parse enters a decided way in, in the order the ways come.

    This is the expensive walk of a grammar. The paths into a production answer where a parse enters its ways. Anything
    asking about the ways of a choice reads this rather than walking again.
    """

    def worked_out() -> dict[str, list[spaces.SubSpace]]:
        _accepted, _ways, entering = _leaf_tables(grammar)
        return {
            name: [_entered_in(name, way, grammar, entering) for way in ways]
            for name, ways in _decided_ways(grammar).items()
        }

    return ir.kept_for(_SPACES_PATHS_ENTER_WAYS_IN, grammar, worked_out)


def _conflicts(grammar: dict[str, ir.Prod]) -> list[tuple[str, int, int]]:
    """
    `(name, at, behind)`. A way that a gate must choose between sits beside a way behind it. The parse enters both in
    exactly the same states. The pair of ways do different things there. There is an entry per way behind. A way appears
    here once per way the machine cannot tell it from.

    A pair of ways doing the same thing is no conflict, and the pair gets no entry here. A way the parse enters in no
    state at all conflicts with nothing. A pair of ways alike but for their gates is a single way reached along a pair
    of routes, and `_does_the_same` says so. The same thing happens when either gate answers first, and the input
    decides nothing between the gates.

    A caller wanting to know what conflicts reads this. `every-choice-way-is-different` counts the ways.
    `every-conflict-is-a-tail-call` asks where a caller reaches the productions holding the conflicting ways.
    """
    held, decided = [], _decided_ways(grammar)
    for name, spaced in _spaces_paths_enter_ways_in(grammar).items():
        ways = decided[name]
        for at, space in enumerate(spaced):
            if not space:
                continue
            held += [
                (name, at, behind)
                for behind, other in enumerate(spaced[at + 1 :], at + 1)
                if space == other and not _does_the_same(ways[at], ways[behind])
            ]
    return held


def _conflicted_ways(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    The production holding a way that the parse enters in exactly the same states as a way behind it, where the pair do
    different things there. The list holds the production's name once per such way. A pair of conflicting ways names its
    production once.

    `_conflicts` gives the conflicts. A way appears there once per way the machine cannot tell it from. Here a way
    appears once where a conflicting way sits behind it. The deduplication turns on where the way sits in the
    production. An asker counts ways and reports the name rather than the position, and this function hands back the
    name.
    """
    return [name for name, _at in dict.fromkeys((name, at) for name, at, _behind in _conflicts(grammar))]


def _offered_ways(body: ir.Node) -> tuple[ir.AlternativeState, ...]:
    """
    The ways that a body offers the machine to decide between. A body holding the characters a terminal takes offers
    none.
    """
    return _OFFERED_WAYS(body)


_OFFERED_WAYS: ir.Question[tuple[ir.AlternativeState, ...]] = ir.Question(
    "the ways a canonical body offers, as a tuple",
    {
        ir.ChoiceState: lambda node: node.alternatives,
        ir.CharSet: lambda _node: (),
    },
)


def _every_choice_way_is_different(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no pair of ways something decides between shares a state, unless the pair do the same thing.

    `no-choice-ways-partially-overlap` leaves a pair sharing their states or sharing none.

    This counts per way rather than per pair. This asks nothing of the choice's else. `_decided_ways` drops the else.
    """
    return [
        f"{name}: a parse enters a way in the states it enters the way behind" for name in _conflicted_ways(grammar)
    ]


_EVERY_CHOICE_WAY_IS_DIFFERENT = _Invariant("every-choice-way-is-different", _every_choice_way_is_different)


def _conflicted_productions(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions a machine cannot find its way through, as names.

    A production holding a pair of ways the parse enters in the same states, where the pair do different things there,
    is such a production. So is a production whose way ends in such a call. A way ending in a call hands its end to the
    callee. A way ending in such a call is undecidable in the same way. Reaching such a production anywhere else does
    not spread it. A call with something behind it comes back. The `second` of an `AlternativeState` holds what the call
    comes back to.
    """
    return _ending_in(grammar, set(_conflicted_ways(grammar)))


def _ending_in(grammar: dict[str, ir.Prod], held: set[str]) -> set[str]:
    """
    `held` plus the productions offering an `AlternativeState` whose `second` is one of them. The function then repeats
    over the productions it added.
    """
    held = set(held)
    while True:
        grew = {
            name
            for name, production in grammar.items()
            if name not in held
            for way in _offered_ways(production.body)
            if way.second is not None and way.second.name in held
        }
        if not grew:
            return held
        held |= grew


def _every_conflict_is_a_tail_call(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    A call to a conflicting production must end its way.

    This check returns a message per way that continues past such a call.
    """
    conflicting = _conflicted_productions(grammar)
    return [
        f"{name}: a way continues past {way.first.name}. The machine cannot walk through that node."
        for name, production in grammar.items()
        for way in _offered_ways(production.body)
        if way.first is not None and way.second is not None and way.first.name in conflicting
    ]


_EVERY_CONFLICT_IS_A_TAIL_CALL = _Invariant("every-conflict-is-a-tail-call", _every_conflict_is_a_tail_call)


def _productions_decided_from_outside(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions a continuation has to move into, and the productions that sit between the continuation and one of
    them.

    Such productions hold a pair of ways alike until the pair continues. That is the pair a continuation is any use to.
    A pair that parts at a call differs by that call rather than by anything behind it. Some input refusing the
    continuation lets the parse settle the pair at that position. The parse takes the way in front, hands its
    continuation back, and reaches the way behind. The parse also takes that way where nothing past what the pair shares
    can come back. The production sitting between the continuation and its target holds nothing that tells the pair
    apart. It sits past the whole call, in a frame neither way can see.

    The answer also holds a production with a way ending in such a call. The continuation travels through such a
    production. The way ending in such a call holds nothing behind it either. The continuation has to come from further
    up again. `_lowered_once` makes copies of these productions on the way down, and those copies pass the continuation
    along.
    """
    held: set[str] = set()
    decided = _decided_ways(grammar)
    for name, at, behind in _conflicts(grammar):
        one, other = decided[name][at], decided[name][behind]
        assert one.second is not None, f"{name}: a conflicted way has no continuation"
        if _is_alike_and_continues_elsewhere(name, one, other) and not _can_be_refused(
            grammar[one.second.name].body, grammar
        ):
            held.add(name)
    return _ending_in(grammar, held)


def _lower_continuations_into_conflicts(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a call to a conflicting thing that continues somewhere. Restate the call as a call to a copy of that thing. Move
    the continuation inside that copy. `A = |actA ->B contA| |...|` with `B` conflicting becomes `A = |actA ->B'|`. `B'`
    is `B` with `contA` run past the body of `B`.

    A conflict the machine cannot find its way through is settled by what comes after. The caller holds that remainder
    until this moves it. Once this moves it, the ways of the copy end where the whole call ends.

    This recurses the continuation into the callee rather than pushing it in front. A way of `B` ending in nothing
    continues to `contA`. A way ending in `R` continues to `R` with `contA` inside it the same way. A tail therefore
    stays a tail, and this adds no frame. A copy a pass makes takes the same continuation. That holds the copies to a
    copy per production and continuation, rather than a copy per stack a parse could hold. The same bound ends the
    recursion through the ways. This mints a copy once for a name and a continuation. A way may reach a name that this
    step is already copying under the same continuation. Such a way takes the copy already begun. A terminal offers no
    way to continue from, and this cannot copy it. This raises at a call to a terminal.

    This moves into the conflicts that nothing inside can settle. Those are the conflicts whose ways part over a
    continuation no input hands back. A production between such a conflict and a call site holding a continuation is
    here too. Some input handing the continuation back reaches the way behind, and the parse settles the pair at that
    position. A move buys nothing there. This leaves a call to such a conflict in place.

    This leaves a way with a recovery untouched.

    This runs until nothing moves. A pass takes a continuation down through a call. The copies a pass makes take
    continuations from outside the copies. A pass therefore leaves the same question a level in. The loop stops when a
    pass leaves the grammar unchanged. `ir.rounds` raises where the rounds run out first.

    A pass ends with a sweep of the productions the parse neither enters by name nor reaches from a production it
    enters. The step returns the grammar the last pass left unchanged.
    """
    for _round in ir.rounds("lower-continuations-into-conflicts"):
        settled = _purged(_lowered_once(grammar, namer))
        namer.sees(settled)
        if settled == grammar:
            return grammar
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _lowered_once(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    A single pass of `lower-continuations-into-conflicts`. This takes in the continuations a call of such a conflict may
    take.

    A copy keeps the tail that made it, and takes no fresh tail while this runs. A fresh tail would call a conflict with
    a continuation minted here. A minted continuation is a name the memo has not seen. The copies would then run a copy
    per stack rather than a copy per production and continuation. That is unbounded.
    """
    wanted = _productions_decided_from_outside(grammar)
    minted: dict[str, ir.Prod] = {}
    made: dict[tuple[str, str | None], str] = {}

    def variant(name: str, tail: str | None) -> str:
        """`name` with `tail` run past what that production does. This mints the copy once per pair."""
        if (name, tail) in made:
            return made[name, tail]
        made[name, tail] = fresh = namer.fresh(name)
        held = grammar[name]
        ways = _offered_ways(held.body)
        if not ways:
            raise ValueError(f"{name}: a copy wanted of a terminal. A terminal holds no way for a continuation to end.")
        minted[fresh] = ir.Prod(
            held.number, fresh, (), ir.ChoiceState(alternatives=tuple(after(way, tail) for way in ways))
        )
        return fresh

    def after(way: ir.AlternativeState, tail: str | None) -> ir.AlternativeState:
        """`way` with `tail` run past where the way ends. The tail continues or recurses into the callee."""
        continues = tail if way.second is None else variant(way.second.name, tail)
        assert continues is not None, "a way continues to nothing"
        return dataclasses.replace(way, second=ir.RefCall(name=continues, args=()))

    def lowered(way: ir.AlternativeState) -> ir.AlternativeState:
        # `AlternativeState` refuses a way holding `first` with no `second`. So the test of `way.second is None` in
        # `lowered` is dead code.
        if way.first is None or way.recover is not None:
            return way
        if way.first.name not in wanted or way.second is None:
            return way
        # `lowered` returns a tail call unchanged. A tail call sets `second` and leaves `first` empty.
        return dataclasses.replace(
            way, first=None, second=ir.RefCall(name=variant(way.first.name, way.second.name), args=())
        )

    written = {
        name: (
            dataclasses.replace(production, body=ir.ChoiceState(alternatives=tuple(lowered(way) for way in ways)))
            if (ways := _offered_ways(production.body))
            else production
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _is_alike_up_to_where_it_continues(name: str, one: ir.AlternativeState, other: ir.AlternativeState) -> bool:
    """
    Whether a pair of ways ask the same gate and do the same actions. They must also make the same call and ride the
    same recovery. The pair parts past that much and no further.

    A pair agreeing on the gate, the call and the recovery but doing the same actions in a different order raises rather
    than answering. The actions are a run and not a set. The emission in front of a code push is not the emission behind
    it. A pair of orders is therefore a pair of different things, and a question written for ways that agree has no
    answer about them.
    """
    if (one.gate, one.first, one.recover) != (other.gate, other.first, other.recover):
        return False
    if one.actions != other.actions:
        if sorted(map(repr, one.actions)) == sorted(map(repr, other.actions)):
            raise ValueError(f"{name}: a pair of ways doing the same actions in a different order")
        return False
    return True


def _is_alike_and_continues_elsewhere(name: str, one: ir.AlternativeState, other: ir.AlternativeState) -> bool:
    """
    Whether a pair of ways are alike up to where they continue, and both continue to different places.

    This strengthens `_is_alike_up_to_where_it_continues` rather than negating it. A pair alike up to there may both
    continue to nothing, or a single one of them may. Neither is a pair a continuation could tell apart.
    """
    return (
        _is_alike_up_to_where_it_continues(name, one, other)
        and one.second is not None
        and other.second is not None
        and one.second != other.second
    )


# The bookkeeping of a parse answers with bits, and an action may set those bits. A reader takes the bits off a
# `spaces.GuardAnswers`. A move that leaves a bit untouched says so by handing that bit back.
def _bits_of(guard_answers: spaces.GuardAnswers) -> tuple[bool, ...]:
    """The bits `guard_answers` holds, in the order `spaces.BOOKKEEPING_AXES` names them."""
    return tuple(getattr(guard_answers, axis) for axis in spaces.BOOKKEEPING_AXES)


def _bits_with_consumed_since_open(
    guard_answers: spaces.GuardAnswers, did_consume_since_open: bool
) -> tuple[bool, ...]:
    """The bits `guard_answers` holds. `did_consume_since_open` replaces the bit of that name."""
    held = list(_bits_of(guard_answers))
    held[spaces.BOOKKEEPING_AXES.index("did_consume_since_open")] = did_consume_since_open
    return tuple(held)


_LINE_BREAK_SPANS = ((wire.LINE_FEED, wire.LINE_FEED), (wire.CARRIAGE_RETURN, wire.CARRIAGE_RETURN))  # cut out first.
_BYTE_ORDER_MARK_SPANS = ((wire.BYTE_ORDER_MARK, wire.BYTE_ORDER_MARK),)  # cut out next, from what the breaks left.


def _taken_apart(
    spans: Sequence[tuple[int, int]],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """
    Split a consumed set by the kind of take into `(the breaks, the mark, the rest)`, and give a kind as intervals.

    A set may hold more than a single kind. The scalar content classes hold the byte-order mark in the upper range of
    `nb-json` beside the content characters. A consume of such a set therefore reads as any of the kinds.

    That is deliberately wider than the parse for the mark. A mark takes no column only where the consume names it and
    no more. `interpreter._is_only_the_mark` asks the set rather than the character. A set holding the mark among a
    scalar's content therefore takes a column there. The checker below admits both outcomes for such a set. The checker
    sees the characters of a set. The grammar wrote the consume for a particular kind. The checker cannot see which
    kind.
    """
    breaks = chars.intersected_spans(spans, list(_LINE_BREAK_SPANS))
    mark = chars.intersected_spans(spans, list(_BYTE_ORDER_MARK_SPANS))
    rest = chars.subtracted_spans(
        chars.subtracted_spans(list(spans), list(_LINE_BREAK_SPANS)), list(_BYTE_ORDER_MARK_SPANS)
    )
    return breaks, mark, rest


def _ns_answers(spans: Sequence[tuple[int, int]], grammar: dict[str, ir.Prod]) -> tuple[bool, ...]:
    """
    The answers `is_after_ns_char` may give past a take of `spans`. The set settles the answer, and there is a single
    one. The set may leave it open, and then both answers come back.
    """
    named = _peek_spans(ir.RefCall(name="ns-char", args=()), grammar) if "ns-char" in grammar else None
    if named is None:
        return (False, True)
    outside = chars.subtracted_spans(list(spans), list(named))
    inside = chars.intersected_spans(spans, list(named))
    return (True,) if not outside else (False,) if not inside else (False, True)


def _consumed(
    spans: Sequence[tuple[int, int]],
    limit: int,
    grammar: dict[str, ir.Prod],
    behind: Sequence[tuple[int, int]] | None = None,
) -> _Moved:
    """
    The answers a consume of `spans` leaves a parse with. `limit` bounds how many characters the consume may take.

    A break puts the parse at the start of the next line. A break empties the consumed length. A mark takes no column. A
    mark keeps the line start unchanged. The consumed length after a mark stays open up to the column. A character that
    is neither a break nor the mark advances the column by the characters taken. That count is the consumed length.

    `behind` names what may sit behind the parse after the consume. A caller passes `behind` where that set is narrower
    than the set the consume takes. A trimmed consume gives its trailing characters back. The parse therefore has no
    such character behind it. A trimmed consume is the caller that passes `behind`. `check_dead_code` declares a trimmed
    consume unbuilt.
    """
    breaks, mark, rest = _taken_apart(spans)
    answers = _ns_answers(rest if behind is None else behind, grammar) if rest else ()

    def move(
        guard_answers: spaces.GuardAnswers, held: spaces._Quantities
    ) -> Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]:
        if breaks:
            yield (True, False, False, True), held._replace(column=0, consumed_length=0)
        if mark:
            # A mark takes no column. The consumed length ranges up to the column.
            for reach in range(0, held.column + 1):
                for full in (False,) if guard_answers.is_at_line_start else (False, True):
                    yield (guard_answers.is_at_line_start, False, full, True), held._replace(consumed_length=reach)
        if rest:
            for taken in range(1, min(limit, spaces.TOP - held.column) + 1):
                for is_ns in answers:
                    for full in (False, True):
                        yield (
                            (False, is_ns, full, True),
                            held._replace(column=held.column + taken, consumed_length=taken),
                        )

    return spaces.reached_by(move), True


def _moved(quantity: str, how: Callable[[spaces._Quantities], Iterable[int]]) -> _Moved:
    """
    The answers a write of a parse quantity leaves a parse with. `how` says what the quantity becomes, out of the tuple
    it started in.
    """

    def move(
        guard_answers: spaces.GuardAnswers, held: spaces._Quantities
    ) -> Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]:
        for value in how(held):
            yield _bits_of(guard_answers), held._replace(**{quantity: value})

    return spaces.reached_by(move), False


# The answers an action that moves no axis leaves a parse with. That is exactly where the action found the parse.
_MOVES_NOTHING = ({answers: frozenset({answers}) for answers in spaces.ALL_GUARD_ANSWERS}, False)


def _any_indent(_quantities: spaces._Quantities) -> range:
    """The indentations from the root's `-1` up to the top. A write whose value nothing says comes to this."""
    return range(-1, spaces.TOP + 1)


def _any_length(_quantities: spaces._Quantities) -> Iterable[int]:
    """
    The values a length may hold. They run from none up to the top. The empty length is the floor, and the top is
    `spaces.TOP`, the value a quantity saturates at.
    """
    return range(0, spaces.TOP + 1)


def _a_message(node: ir.MaxWrapper) -> str:
    """
    The error a `(max)` window names. A `(max)` wrapping a match names the error. A bare length note names none.
    """
    assert node.message is not None, "a `(max)` around a match names no error"
    return node.message


def _a_set(node: ir.Node | None) -> ir.Node:
    """`node`, for a caller that has established a character set for it."""
    assert node is not None, "a peek over nothing"
    return node


def _set_of(action: ir.Node) -> ir.Node:
    """The characters that a consume names. This reads the action rather than the guard in front."""
    held = getattr(action, "set", None)
    assert held is not None, f"a reader asks {type(action).__name__} for the set it consumes, and the node names none"
    return held


def _taken_spans(item: ir.Node, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]]:
    """The intervals a consume's set names. `spaces.ALL_CHARACTERS` answers where the grammar leaves the set open."""
    spans = _peek_spans(item, grammar)
    return list(spaces.ALL_CHARACTERS) if spans is None else list(spans)


def _took_one_character(action: ir.ConsumeCharAction, grammar: dict[str, ir.Prod]) -> _Moved:
    """The answers a parse holds after it takes the character the gate found."""
    return _consumed(_taken_spans(action.set, grammar), 1, grammar)


def _took_no_character(_action: ir.Node, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers a consume that took nothing leaves a parse with. The match has no length, and the parse has not moved.
    """
    return _moved("consumed_length", lambda _quantities: (0,))


def _took_a_run(action: ir.ConsumeSpanAction, grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers a maximal consume of the set leaves a parse with. `spaces.TOP` bounds the run. The column stops at
    `spaces.TOP`.
    """
    return _consumed(_taken_spans(action.set, grammar), spaces.TOP, grammar)


def _took_a_limited_run(action: ir.ConsumeLimitedSpanAction, grammar: dict[str, ir.Prod]) -> _Moved:
    """The answers up to `limit` characters of the set leave a parse with. The consume takes a character at least."""
    said = getattr(action.limit, "value", None)
    limit = said if isinstance(said, int) else spaces.TOP
    return _consumed(_taken_spans(action.set, grammar), min(limit, spaces.TOP), grammar)


def _took_a_trimmed_run(action: ir.ConsumeTrimmedSpanAction, grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers a consume of `full` leaves a parse with. The consume gives its trailing `trim` back. The parse then has
    no `trim` behind it.
    """
    spans = _taken_spans(action.full, grammar)
    behind = chars.subtracted_spans(list(spans), _taken_spans(action.trim, grammar))
    return _consumed(spans, spaces.TOP, grammar, behind=behind)


def _pushed_indent(action: ir.PushIndentAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """A push of `action`'s level sets the indentation `n`. A push of a null level moves no axis."""
    level = action.level
    if level == ir.LitValue(value=None):
        # `interpreter._indent` refuses to read a null indentation. A null push maps a guard answer in
        # `spaces.ALL_GUARD_ANSWERS` to itself.
        return _MOVES_NOTHING
    said = getattr(level, "value", None)
    if isinstance(said, int):
        return _moved("n", lambda _quantities: (said,))
    if level == ir.ColumnValue():
        return _moved("n", lambda quantities: (quantities.column,))
    if level == ir.GlobalValue(name="f"):
        return _moved("n", lambda quantities: (quantities.floor,))
    if level == ir.AddValue(a=ir.IndentValue(), b=ir.LitValue(value=1)):
        return _moved("n", lambda quantities: (quantities.n + 1,))
    if level == ir.SubValue(a=ir.IndentValue(), b=ir.LitValue(value=1)):
        return _moved("n", lambda quantities: (quantities.n - 1,))
    if level == ir.AddValue(a=ir.IndentValue(), b=ir.GlobalValue(name="m")):
        # A push of `n+m` takes the detected indent `m` as no less than `0`. That push sets `n` to a value from `n` up
        # to `spaces.TOP`.
        return _moved("n", lambda quantities: range(quantities.n, spaces.TOP + 1))
    raise ValueError(f"this question covers no indentation of {level}")


def _popped_indent(action: ir.PopIndentAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers a parse holds once it takes off the indentation `action` names. `action` undoes a push, and that push
    displaced an indentation. This function restores that indentation.

    The push worked the level out *from the indentation this restores*. A level can name that indentation. A level of
    the indentation plus or minus `1` runs backward to the exact indentation. A level of the indentation plus `m`
    restores an indentation from `-1` up to the held `n`. A literal `None` level moves no axis. Any other level says
    nothing of the indentation the push displaced. The same push runs over a wider indentation, an equal indentation and
    a narrower one. This function therefore opens `n` to the range from `-1` up to `spaces.TOP`.
    """
    level = action.level
    if level == ir.LitValue(value=None):
        return _MOVES_NOTHING
    if level == ir.AddValue(a=ir.IndentValue(), b=ir.LitValue(value=1)):
        return _moved("n", lambda quantities: (quantities.n - 1,))
    if level == ir.SubValue(a=ir.IndentValue(), b=ir.LitValue(value=1)):
        return _moved("n", lambda quantities: (quantities.n + 1,))
    if level == ir.AddValue(a=ir.IndentValue(), b=ir.GlobalValue(name="m")):
        return _moved("n", lambda quantities: range(-1, quantities.n + 1))
    return _moved("n", _any_indent)


def _opened_a_turn(_action: ir.Node, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers opening a turn that must take a character leaves a parse with. The parse has taken nothing since that
    turn opened.
    """

    def move(
        guard_answers: spaces.GuardAnswers, quantities: spaces._Quantities
    ) -> Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]:
        yield _bits_with_consumed_since_open(guard_answers, False), quantities

    return spaces.reached_by(move), False


def _closed_a_turn(_action: ir.Node, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    Update the answers a parse holds after closing a turn that must take a character. Answer again for the region around
    that turn.

    Positions of opens do not fall going up the stack. `interpreter._try_open_turn` pushes the position it opens at. A
    character taken since the inner open counts as one taken since the outer open. An outer open already answering true
    keeps answering true. An outer open keeps its own answer where the parse takes nothing since the inner open closes.
    """

    def move(
        guard_answers: spaces.GuardAnswers, quantities: spaces._Quantities
    ) -> Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]:
        yield _bits_with_consumed_since_open(guard_answers, True), quantities
        if not guard_answers.did_consume_since_open:
            yield _bits_with_consumed_since_open(guard_answers, False), quantities

    return spaces.reached_by(move), False


def _changed_nothing(_action: ir.Node, _grammar: dict[str, ir.Prod]) -> _Moved:
    """An action that moves no axis leaves the answers of a parse unchanged."""
    return _MOVES_NOTHING


def _wrote_a_variable(action: ir.SetVarAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers `action`'s write leaves a parse with. The write moves an axis only where what it writes is the floor.
    """
    if action.param != "f":
        return _MOVES_NOTHING
    literal = ir.AS_A_LITERAL_VALUE(action.value)
    if literal is None:
        raise ValueError(f"this question covers no floor of {action.value}")
    written = literal.value
    assert isinstance(written, int), f"the floor becomes {written!r}, and that value is no indentation"
    return _moved("floor", lambda _quantities: (written,))


def _cleared_a_variable(action: ir.ClearVarAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    Clearing a variable ends the value the `action`'s slot held. After the clear the slot holds no value. Clearing `f`
    opens the floor axis to the lengths `_any_length` gives, and the move takes no character. Clearing any other
    variable moves no axis. `interpreter._try_clear` empties the slot. A global's stack gives up only its top value, and
    a value beneath the top stays. `interpreter._evaluated_global` counts a read past the clear that finds a value
    beneath the top, and faults on a read finding the stack empty.
    """
    return _MOVES_NOTHING if action.param != "f" else _moved("floor", _any_length)


def _raised_the_floor(action: ir.IncreaseAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """Give the answers a parse holds once the floor rises to the column. `(increase)` raises the floor that way."""
    if action.param != "f":
        raise ValueError(f"a step increases {action.param} rather than the floor")
    return _moved("floor", lambda quantities: (max(quantities.floor, quantities.column),))


# An action changes the states a parse may be in. The table does not trust the name of a kind. The kinds
# `_changed_nothing` handles write no axis of `spaces.GuardAnswers`. Some build the token. Some decide where a failure
# goes. Some open or close a consume budget. Some forbid a match at a line start. A kind the table does not name raises.
# An action added to the IR therefore gets an answer here. It does not pass as an action that changes nothing.
_AFTER_ACTION: ir.Question[_Moved] = ir.Question(
    "the answers performing an action leaves a parse with, beside whether it took a character",
    {
        ir.ConsumeCharAction: _took_one_character,
        ir.ConsumeNoCharAction: _took_no_character,
        ir.ConsumeSpanAction: _took_a_run,
        ir.ConsumeLimitedSpanAction: _took_a_limited_run,
        ir.ConsumeTrimmedSpanAction: _took_a_trimmed_run,
        ir.PushIndentAction: _pushed_indent,
        ir.PopIndentAction: _popped_indent,
        ir.SetVarAction: _wrote_a_variable,
        ir.ClearVarAction: _cleared_a_variable,
        ir.IncreaseAction: _raised_the_floor,
        ir.StartMustConsumeAction: _opened_a_turn,
        ir.EndMustConsumeAction: _closed_a_turn,
        ir.EmitAction: _changed_nothing,
        ir.PushCodeAction: _changed_nothing,
        ir.PopCodeAction: _changed_nothing,
        ir.OpenProvisionalAction: _changed_nothing,
        ir.MarkProvisionalAction: _changed_nothing,
        ir.RetypeProvisionalAction: _changed_nothing,
        ir.InjectBeforeAction: _changed_nothing,
        ir.CommitProvisionalAction: _changed_nothing,
        ir.ErrorAction: _changed_nothing,
        # `spaces.GuardAnswers` leaves out the place a failure goes.
        ir.PushMessageAction: _changed_nothing,
        ir.PopMessageAction: _changed_nothing,
        ir.PushRecoveryAction: _changed_nothing,
        ir.PopRecoveryAction: _changed_nothing,
        ir.PushBackTrackAction: _changed_nothing,
        ir.PopBackTrackAction: _changed_nothing,
        ir.CutAction: _changed_nothing,
        # `spaces.GuardAnswers` also leaves out the consume budget and what may not match at a line start.
        ir.OpenWindowAction: _changed_nothing,
        ir.CloseWindowAction: _changed_nothing,
        ir.SetForbiddenAction: _changed_nothing,
        ir.ExcludeAtAction: _changed_nothing,
    },
)


# The answers an action leaves a parse with, kept per grammar and per action. Working an answer out walks
# `spaces.ALL_GUARD_ANSWERS`. A consume walks it through `spaces.reached_by`, along with the tuples an entry holds. The
# action and the place the parse reached decide the answer. So does the grammar that resolves the sets the action names.
# The space the action runs in does not come into it. This therefore works an answer out once and reads it per space
# after. In `spaces.after` the space only says which answers are live and what characters ride along. This keeps its
# answers against the grammar the way `_TABLES` keeps `_leaf_tables`. An action is a value, and `ir.kept_for` compares
# it by value.
_WHERE_AN_ACTION_LEAVES_A_PARSE: ir.Kept = {}

# A cache of the space an action leaves behind. `kept_for` keys it on the grammar's identity, the action and the space.
# `after_action` keeps the result of `spaces.after` here.
_WHERE_AN_ACTION_LEAVES_A_SPACE: ir.Kept = {}


def after_action(action: ir.Node, space: spaces.SubSpace, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    Perform `action` in `space` and answer the result. A parse at a place of `space` reaches a place of the answer by
    performing `action`.
    """
    if not space:
        return space
    leaves = ir.kept_for(_WHERE_AN_ACTION_LEAVES_A_PARSE, grammar, lambda: _AFTER_ACTION(action, grammar), under=action)
    return ir.kept_for(
        _WHERE_AN_ACTION_LEAVES_A_SPACE, grammar, lambda: spaces.after(space, *leaves), under=(action, space)
    )


def _after_actions(actions: Iterable[ir.Node], space: spaces.SubSpace, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """`space` with a run of actions performed in it, in the order the run holds."""
    for action in actions:
        space = after_action(action, space, grammar)
    return space


def _through_a_way(
    way: ir.AlternativeState,
    space: spaces.SubSpace,
    _entered: dict[str, spaces.SubSpace],
    left: dict[str, spaces.SubSpace],
    grammar: dict[str, ir.Prod],
) -> tuple[spaces.SubSpace, list[tuple[str, spaces.SubSpace]]]:
    """
    `(where the way leaves the parse, what it hands its calls)`. `_through_a_way` takes `space` along the way's parts.

    The actions come first, then the call, then what the way continues to. A part starts where the part in front left
    the parse. A call leaves the exit of the callee rather than a walk into it. The fixpoint below supplies that exit.
    """
    handed = []
    space = _after_actions(way.actions, space, grammar)
    for call in _calls_of_way(way):
        handed.append((call.name, space))
        space = left.get(call.name, spaces.NOWHERE)
    return space, handed


def _entered_by_matching(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions a parse enters by matching at the parse's position, rather than by a way calling them, and what
    those reach.

    An `AlternativeState` calls the productions in its `first` and `second`. A way matches anything else it names. A
    lookaround asks about such an item. An exclusion forbids such an item. `interpreter._is_forbidden_here` probes an
    exclusion at the line starts, where the grammar names it in no way. A cut unwinds to such an item as its recovery. A
    call reaches none of those. The caller therefore does not say where a parse enters such a production. A parse can
    begin it at any position.
    """
    named = set()
    for production in grammar.values():
        ways = _offered_ways(production.body)
        if not ways:
            continue
        for way in ways:
            for part in (way.gate, *way.actions, *(held for held in (way.recover,) if held is not None)):
                named |= {held for held in part.references() if held in grammar}
    return _referenced_from(named, grammar)


def _referenced_from(named: set[str], grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The names in `named` that `grammar` holds, the productions those reference, and the productions those reach in turn.
    A name `grammar` lacks stays out.

    The name says that the walk follows the `references` of a production. The name does not say reaching.
    `check_dead_code` walks reaching for definitions and modules. A pair of walks is a pair of questions, and a shared
    name is how a reader comes to take them for one.
    """
    held, worklist = set(), list(named)
    while worklist:
        name = worklist.pop()
        if name in held or name not in grammar:
            continue
        held.add(name)
        worklist.extend(grammar[name].references())
    return held


def _entry_and_exit_spaces(
    grammar: dict[str, ir.Prod],
) -> tuple[dict[str, spaces.SubSpace], dict[str, spaces.SubSpace]]:
    """
    `(entry, exit)`, both as `{name: spaces.SubSpace}`. The first is where a parse may be on entering a production. The
    second is where a parse is when that production hands control back.

    The pair is a single question. A production's entry is the states the sites calling it are in. The ways making those
    calls leave the parse in those states. The exits of the productions a way calls decide what the way leaves. Entry
    therefore grows downward from the productions a parse enters by name, and exit grows upward from the ways that call
    nothing. The pair feeds itself. Exit starts empty. Both grow until neither moves.

    A body that offers no way is the characters a terminal takes. Such a body leaves its entry with a character
    consumed.

    This reads a parse starting at the root. Entry starts at the productions a parse enters by name and the productions
    a match enters. A production that neither those starts nor a call reaches has no entry.
    """
    named = _entered_by_name(grammar) | _entered_by_matching(grammar)
    entry = {name: (spaces.COMPLETE if name in named else spaces.NOWHERE) for name in grammar}
    left = {name: spaces.NOWHERE for name in grammar}
    # The function computes the gate of a way and the take of a terminal before the loop starts.
    gates = {
        (name, at): _gated_by((), way, grammar)
        for name, production in grammar.items()
        for at, way in enumerate(_offered_ways(production.body))
    }
    taken = {
        name: _consumed(_taken_spans(production.body, grammar), 1, grammar)
        for name, production in grammar.items()
        if not _offered_ways(production.body)
    }
    # A production whose exit grows puts its callers back on the worklist. The whole grammar does not go round again.
    callers: dict[str, set[str]] = {name: set() for name in grammar}
    for name, production in grammar.items():
        for way in _offered_ways(production.body):
            for call in _calls_of_way(way):
                if call.name in callers:
                    callers[call.name].add(name)
    worklist, waiting = collections.deque(grammar), set(grammar)
    while worklist:
        name = worklist.popleft()
        waiting.discard(name)
        ways = _offered_ways(grammar[name].body)
        if not ways:
            here = spaces.after(entry[name], *taken[name])
        else:
            here = spaces.NOWHERE
            for at, way in enumerate(ways):
                space = entry[name] & gates[name, at]
                if not space:
                    continue
                space, handed = _through_a_way(way, space, entry, left, grammar)
                for called, reached in handed:
                    if called in entry and not entry[called].does_hold(reached):
                        entry[called] = entry[called] | reached
                        if called not in waiting:
                            worklist.append(called)
                            waiting.add(called)
                here = here | space
        if not left[name].does_hold(here):
            left[name] = left[name] | here
            for caller in callers[name]:
                if caller not in waiting:
                    worklist.append(caller)
                    waiting.add(caller)
    return entry, left


def _moved_by(grammar: dict[str, ir.Prod], action: ir.Node) -> _Moved:
    """
    The answer `_AFTER_ACTION` gives `action` in `grammar`. `_actions_worked_out` runs this in a worker that `spread`
    forks.
    """
    return _AFTER_ACTION(action, grammar)


def _actions_worked_out(grammar: dict[str, ir.Prod]) -> None:
    """
    Work out `_AFTER_ACTION` for the actions the offered ways of `grammar` hold, and keep the answers in
    `_WHERE_AN_ACTION_LEAVES_A_PARSE`. `gate.spread` shares the actions out over the cores. `after_action` then reads an
    answer from that store rather than working it out.
    """
    actions = list(
        dict.fromkeys(
            action
            for production in grammar.values()
            for way in _offered_ways(production.body)
            for action in way.actions
        )
    )
    for action, moved in zip(actions, gate.spread(_moved_by, grammar, actions)):

        def answered(held: _Moved = moved) -> _Moved:
            return held

        ir.kept_for(_WHERE_AN_ACTION_LEAVES_A_PARSE, grammar, answered, under=action)


def spaces_of_ways(
    grammar: dict[str, ir.Prod],
) -> dict[int, tuple[str, int, spaces.SubSpace, spaces.SubSpace]]:
    """
    `{id(the way): (its production's name, where it stands among that production's ways, its entry, its exit)}`.

    The gate `check_normalize._spaces_held` checks a parse against these spaces. This function keys a way's spaces by
    the identity of that way. Whoever holds a way in hand can then ask about it. The class `ir.AlternativeState` of a
    way is a frozen dataclass. Productions offering the same way hold equal ways. Keying by value would hand the spaces
    of a production back for the way of another. A way's entry is its production's entry crossed with the way's gate. A
    way's exit is its entry taken along the way's parts. A call in the way resumes from the exit of its callee.
    """
    _actions_worked_out(grammar)
    entry, left = _entry_and_exit_spaces(grammar)
    held = {}
    for name, production in grammar.items():
        for at, way in enumerate(_offered_ways(production.body)):
            entered = entry[name] & _gated_by((), way, grammar)
            leaves = _through_a_way(way, entered, entry, left, grammar)[0] if entered else entered
            held[id(way)] = (name, at, entered, leaves)
    return held


def _every_path_reaches_a_leaf_way(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a path into a production of leaf ways reaches one of them.

    A path gathers the guards asked on the way here, and the parse asks a way's gate with them. The path does not take a
    way whose gate cannot hold beside those guards. A path that can take *none* of the leaf ways is a call no input can
    complete, and this counts it.

    This asks the productions whose ways are leaf ways throughout, the productions that answer by themselves. A way
    whose gate a path rules out counts as reached where its production reaches some other way. This asks a way taking no
    character as much as a way taking a run.
    """
    _accepted, _ways, entering = _leaf_tables(grammar)
    faults = []
    for name, production in grammar.items():
        ways = choice.alternatives if (choice := ir.AS_A_CHOICE(production.body)) is not None else ()
        if not ways or any(way.first is not None or way.second is not None for way in ways):
            continue
        for path in entering[name]:
            if not any(_gated_by(path, way, grammar) for way in ways):
                faults.append(f"{name}: a path into the production reaches no way the production offers")
    return faults


_EVERY_PATH_REACHES_A_LEAF_WAY = _Invariant("every-path-reaches-a-leaf-way", _every_path_reaches_a_leaf_way)


class _Crossing:
    """
    Whether a guard that follows an action may move in front of that action. An entry names a pair of kinds.

    A pair the table leaves out refuses the move, and the table records that it came up. The gate reports both of these
    as faults.

    - **a pair consulted that the table does not name**.
    - **a pair the table names that nothing consulted**.

    A named pair says `True` where asking early is the same question. It says `False` where asking early differs. An
    entry holds a function where the kinds do not settle the pair. Such an entry counts as named. A run that asks
    nothing about the pair leaves the entry in `unconsulted`.
    """

    def __init__(self, told: Mapping[tuple[str, str], "bool | Callable[..., bool]"]) -> None:
        self._told = dict(told)
        self._asked: set[tuple[str, str]] = set()
        self._unnamed: set[tuple[str, str]] = set()

    def may_cross(self, guard: ir.Node, action: ir.Node, grammar: dict[str, ir.Prod]) -> bool:
        """Whether a parse may ask `guard` in front of `action`. The table records the pair either way."""
        pair = (type(guard).__name__, type(action).__name__)
        if pair not in self._told:
            self._unnamed.add(pair)
            return False
        self._asked.add(pair)
        held = self._told[pair]
        return held(guard, action, grammar) if callable(held) else held

    def unnamed(self) -> list[tuple[str, str]]:
        """The pairs that something consulted and the table does not name."""
        return sorted(self._unnamed)

    def unconsulted(self) -> list[tuple[str, str]]:
        """The pairs the table names and nothing consulted."""
        return sorted(set(self._told) - self._asked)


def _may_cross_every(pairs: Iterable[tuple[ir.Node, ir.Node]], grammar: dict[str, ir.Prod]) -> bool:
    """
    Whether the table refuses no `(guard, action)` pair in `pairs`.

    This asks a pair behind a refusal too. The table's faults then name the pairs the table is missing.
    """
    answered = [GUARD_CROSSES_ACTION.may_cross(guard, action, grammar) for guard, action in pairs]
    return all(answered)


def _does_name_the_slot(node: object, param: str) -> bool:
    """Whether `node` reads the slot `param` names, as a value the parse holds or as a parameter a call passes."""
    return isinstance(node, ir.Node) and _DOES_NAME_THE_SLOT(node, param)


# Whether a node reads the slot a parameter names. A node of another kind than a slot asks the fields it holds.
_DOES_NAME_THE_SLOT: ir.Question[bool] = ir.Question(
    "whether a node reads the slot a parameter names",
    {
        (ir.GlobalValue, ir.ParamValue): lambda node, param: node.name == param,
        ir.kinds_other_than(ir.GlobalValue, ir.ParamValue): lambda node, param: any(
            _does_name_the_slot(getattr(node, field.name), param) for field in dataclasses.fields(node)
        ),
    },
)


def _does_read_nothing_the_write_writes(
    guard: ir.Node, action: ir.SetVarAction | ir.ClearVarAction, _grammar: dict[str, ir.Prod]
) -> bool:
    """
    Whether `guard` reads nothing `action` writes.

    A comparison such as `ir.IsLessThanGuard` names a pair of values in `a` and `b`, and a write names a slot in
    `param`. The write is no business of a guard naming something else. The answer is a fact about the pair in hand and
    not about their kinds.
    """
    return not _does_name_the_slot(guard, action.param)


def _does_read_none_of_the_forbidden(
    guard: ir.Node, action: ir.SetForbiddenAction, grammar: dict[str, ir.Prod]
) -> bool:
    """
    Whether `guard` reads nothing `action` forbids.

    The parse asks the refusal of a character it takes at a start of line. A lookahead runs through
    `interpreter._try_probe`, and a take inside the probe asks `_is_forbidden_here` as well. The lookahead therefore
    reads the set in force where the parse asks the guard. Moving the guard across the write changes that set. Whether
    the guard reads that set is a fact about the states the guard admits and the states in which the write's item takes
    a character. The guard admits a range of states. A `SubSpace` holds a range of characters per guard answer. The
    write's forbidden range can start at a different character than the guard's range does. The ranges may fail to
    cross, and the guard then answers alike either side of the write.

    The range of states the guard admits errs wide. A pair that does not cross in it does not cross at all. A write that
    forbids nothing answers false. The write displaces the value that was there, and this action names no replacement.
    """
    if action.item is None:
        return False
    accepted, _ways, _entering = _leaf_tables(grammar)
    return not _admits(guard, grammar) & _accepted_part(action.item, grammar, accepted)


# The actions a parse may ask a guard in front of. An entry maps `(guard, action)` to whether asking the guard there is
# the same question. An entry may instead map to the function deciding that for the pair the walk is holding. A pair
# absent is a pair nobody has worked out. Consulting the table for such a pair refuses the move and records the pair as
# a fault.
GUARD_CROSSES_ACTION = _Crossing(
    {
        # A window bounds what a committed consume may take. It bounds no more than that. A lookaround reads past its
        # edge, as `_try_probe` shows.
        ("LookGuard", "CloseWindowAction"): True,
        ("NegLookGuard", "CloseWindowAction"): True,
        ("LookGuard", "OpenWindowAction"): True,
        # A lookaround reads the input rather than a variable. Either side therefore asks the same question, and
        # `_does_read_nothing_the_write_writes` answers where a comparison reads one.
        ("LookGuard", "ClearVarAction"): True,
        ("IsLessEqualGuard", "SetVarAction"): _does_read_nothing_the_write_writes,
        ("IsLessThanGuard", "SetVarAction"): _does_read_nothing_the_write_writes,
        ("LookGuard", "SetVarAction"): True,
        ("NegLookGuard", "SetVarAction"): True,
        # Taking a character moves the parse. A lookaround in front of a take asks about a different character.
        ("LookGuard", "ConsumeCharAction"): False,
        ("NegLookGuard", "ConsumeCharAction"): False,
        ("LookGuard", "ConsumeSpanAction"): False,
        ("NegLookGuard", "ConsumeSpanAction"): False,
        # The guard behind a run of a limited span asks what that run took. In front of the run it would read an earlier
        # run.
        ("DidMatchFullSpanGuard", "ConsumeLimitedSpanAction"): False,
        # A comparison behind a run can read the run's length. In front of the run it would read an earlier length.
        ("IsLessEqualGuard", "ConsumeSpanAction"): False,
        # A committed region is a scope. A guard crossing either end, or a `CutAction`, lands on the other side of the
        # line where a refusal changes into the error the region names.
        ("LookGuard", "CutAction"): False,
        ("LookGuard", "PopMessageAction"): False,
        ("IsLessEqualGuard", "PushMessageAction"): False,
        ("LookGuard", "PushMessageAction"): False,
        # These actions write the value the parse hands back. A question about the input or about a count reads none of
        # it.
        ("EndOfStreamGuard", "EmitAction"): True,
        ("LookGuard", "EmitAction"): True,
        ("NegLookGuard", "EmitAction"): True,
        ("StartOfLineGuard", "EmitAction"): True,
        # A code is the token under construction, and neither the input nor a count. A guard that only reads therefore
        # passes either end.
        ("IsLessEqualGuard", "PopCodeAction"): True,
        ("IsLessThanGuard", "PopCodeAction"): True,
        ("LookGuard", "PopCodeAction"): True,
        ("NegLookGuard", "PopCodeAction"): True,
        ("IsLessEqualGuard", "PushCodeAction"): True,
        ("IsLessThanGuard", "PushCodeAction"): True,
        ("LookGuard", "PushCodeAction"): True,
        ("NegLookGuard", "PushCodeAction"): True,
        ("StartOfLineGuard", "PushCodeAction"): True,
        # A turn that must take a character records where it began. A comparison, a lookaround and a start-of-line
        # question read none of that.
        ("IsLessEqualGuard", "StartMustConsumeAction"): True,
        ("LookGuard", "StartMustConsumeAction"): True,
        ("IsLessThanGuard", "StartMustConsumeAction"): True,
        ("NegLookGuard", "StartMustConsumeAction"): True,
        ("StartOfLineGuard", "StartMustConsumeAction"): True,
        # A settled region's close says where a later failure goes, and a guard reads none of that.
        ("LookGuard", "PopBackTrackAction"): True,
        # A turn that must take a character asks where the parse is against its own open. A take changes that answer. A
        # committed region's close changes it as well.
        ("DidConsumeSinceOpenGuard", "ConsumeSpanAction"): False,
        ("DidConsumeSinceOpenGuard", "EmitAction"): True,
        ("DidConsumeSinceOpenGuard", "PopCodeAction"): True,
        ("DidConsumeSinceOpenGuard", "PopMessageAction"): False,
        ("DidConsumeSinceOpenGuard", "PushCodeAction"): True,
        # The indentation the parse holds is neither the input nor where the parse is in the line. These guards read the
        # input and where the parse is in the line.
        ("EndOfStreamGuard", "PushIndentAction"): True,
        ("LookGuard", "PushIndentAction"): True,
        ("NegLookGuard", "PushIndentAction"): True,
        ("StartOfLineGuard", "PushIndentAction"): True,
        # A lookahead matches its item through the refusal at a start of line. `_does_read_none_of_the_forbidden`
        # decides the pair. The other guards match nothing and read none of it.
        ("EndOfStreamGuard", "SetForbiddenAction"): True,
        ("LookGuard", "SetForbiddenAction"): _does_read_none_of_the_forbidden,
        ("StartOfLineGuard", "SetForbiddenAction"): True,
    }
)


def _every_way_takes_at_most_once(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way takes characters at most once. The way then takes what its own gate found.

    A gate cannot test a second take inside a single way.
    """
    return [
        f"{name}: a way takes twice, and its gate can speak for only the first"
        for name, production in grammar.items()
        if (choice := ir.AS_A_CHOICE(production.body)) is not None
        for way in choice.alternatives
        if sum(ir.IS_CONSUMING(item) for item in _items_of_way(way)) > 1
    ]


_EVERY_WAY_TAKES_AT_MOST_ONCE = _Invariant("every-way-takes-at-most-once", _every_way_takes_at_most_once)


def _mint_consume_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Cut a way where it would take a second character on the strength of the first. `A = |g ... take ... CharSet rest|`
    becomes `A = |g ... take ... ->A'|` with `A' = |- CharSet rest|`.

    The way's gate cannot ask about a set behind a take. `GUARD_CROSSES_ACTION` refuses a look across a
    `ConsumeCharAction`. The way holds more than a single take and has a single gate. A state of its own gives the
    remaining parts a position where that state's gate can answer. `split-consumes-into-gates` puts the question there.

    This runs until nothing moves. The minted state starts at the second take. A longer run puts a third take in that
    state too. The state minted for a cut is therefore a way to cut again, a take at a time, until a way holds a single
    take.
    """
    for _round in ir.rounds("mint-consume-states"):
        settled = _cleaned(_minted_consume_states(grammar, namer), namer)
        namer.sees(settled)
        if settled == grammar:
            return grammar
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _minted_consume_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """A single pass of `mint-consume-states`. This cuts a way holding a second take, at the position of that take."""
    minted = {}

    def told(name: str, way: ir.AlternativeState) -> ir.AlternativeState:
        taken = False
        for at, action in enumerate(way.actions):
            if ir.IS_CONSUMING(action) and taken:
                break
            taken = taken or ir.IS_CONSUMING(action)
        else:
            return way
        held = namer.fresh(name)
        minted[held] = ir.Prod(
            grammar[name].number,
            held,
            (),
            ir.ChoiceState(
                alternatives=(
                    ir.AlternativeState(
                        gate=ir.GatePart(),
                        actions=way.actions[at:],
                        first=way.first,
                        second=way.second,
                        recover=way.recover,
                    ),
                )
            ),
        )
        return dataclasses.replace(
            way, actions=way.actions[:at], first=None, second=ir.RefCall(name=held, args=()), recover=None
        )

    written = {
        name: dataclasses.replace(
            production, body=_WAYS_RETOLD(production.body, lambda way, owner=name: told(owner, way))
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _asked_where_entered(grammar: dict[str, ir.Prod]) -> _Entered:
    """
    `{name: paths}`. A name holds the paths into the production. A path is the set of guards asked where the parse
    enters the production.

    A gate tests the position where the parse enters the way. The way performs its actions and then may make a call. A
    call behind actions that consume nothing sits at that same position. A question the caller asked therefore holds
    where the callee begins, and it holds however many calls deep.

    The answer keeps the paths apart rather than taking the guards they share. A site then says which it wants.
    `_gated_by` reads a path into a subspace, and a caller such as `_entered_in` unions the subspaces of the paths into
    the production.

    A path is a set. A gate holds no order between its guards. A pair of paths asking the same questions is a single
    path.

    A path that has moved contributes the empty path. A caller taking a character before the call asks about somewhere
    else. A production entered by name has no caller to ask, and neither has a production nothing calls. Both come back
    as the empty path, and no guard sits where they begin. The walk computes a least fixed point. The knowledge at a
    call site includes the knowledge where the caller itself began.
    """

    def worked_out() -> _Entered:
        entered = _entered_by_name(grammar)
        nothing: frozenset[frozenset[ir.Node]] = frozenset({frozenset()})
        # Record who enters what as `{callee: [(caller, the guards asked there, whether the parse has moved)]}`.
        sites: dict[str, list[tuple[str, frozenset[ir.Node], bool]]] = {}
        for holder, production in grammar.items():
            for way in _ALTERNATIVES(production.body):
                call = _first_call_of_way(way)
                if call is not None and call.name in grammar:
                    has_moved = any(ir.IS_CONSUMING(action) for action in way.actions)
                    sites.setdefault(call.name, []).append((holder, frozenset(way.gate.guards), has_moved))
        entering: dict[str, set[str]] = {}
        for callee, held in sites.items():
            for holder, _guards, _has_moved in held:
                entering.setdefault(holder, set()).add(callee)

        def answer(name: str, known: Mapping[str, _Paths]) -> _Paths:
            if name in entered:
                return nothing
            paths: set[frozenset[ir.Node]] = set()
            for holder, guards, has_moved in sites.get(name, ()):
                if has_moved:
                    paths.add(frozenset())
                else:
                    paths |= {above | guards for above in known[holder] or nothing}
            return frozenset(paths) or nothing

        return ir.fixed_point(
            "the guards asked where a parse enters a production",
            {name: frozenset[frozenset[ir.Node]]() for name in grammar},
            answer,
            entering,
        )

    return ir.kept_for(_ENTERED, grammar, worked_out)


# `_asked_where_entered` keeps an answer per grammar somebody has asked it about. An entry holds the grammar itself
# beside the answer. `kept_for` checks the kept grammar, and that check stops a reuse of the key under it.
_ENTERED: ir.Kept = {}


def _no_char_set_is_an_item(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no way holds a character set among what it performs.

    A question and a taking are separate things. A `CharSet` among what the machine performs is both at once. The set
    asks whether the character belongs to it, and takes the character in the same breath. The machine then reaches the
    answer only by entering the way. The answer should decide whether the machine enters the way. After the split, the
    set is the question a gate holds, and `ConsumeCharAction` is the taking.

    The set stays inside the `LookGuard` of a gate and as the class a consume runs over. The set stops appearing among
    the actions as a match of its own.
    """
    # A production that is a character set is the class itself. A call to it asks and takes in a single step. That call
    # is the fault.
    faults = [
        f"{name}: a way calls a character set that asks and takes in a single step"
        for name, production in grammar.items()
        for way in _ALTERNATIVES(production.body)
        for item in _items_of_way(way)
        if (call := ir.AS_A_CALL(item)) is not None
        and call.name in grammar
        and ir.AS_A_CHAR_SET(grammar[call.name].body) is not None
    ]
    return faults + [
        f"{name}: a way asks a character set and takes the character in a single step"
        for name, production in grammar.items()
        for way in _ALTERNATIVES(production.body)
        for item in _items_of_way(way)
        if ir.AS_A_CHAR_SET(item) is not None
    ]


_NO_CHAR_SET_IS_AN_ITEM = _Invariant("no-char-set-is-an-item", _no_char_set_is_an_item)


def _does_ask_about_a_run(production: ir.Prod) -> bool:
    """
    Whether any way of `production` asks about a run before performing anything. The callers have to have made that run.

    A way asks in the gate, or first among the things the way performs. Those are the same claim on a caller.
    """
    return any(
        ir.AS_A_FULL_SPAN_GUARD(held) is not None
        for way in _ALTERNATIVES(production.body)
        for held in (*way.gate.guards, *way.actions[:1])
    )


def _every_span_question_follows_its_run(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a question about a limited run asks where that run left the parse.

    The guard reads what the action in front of it did and no more. That action has to be the run. The run is the item
    before the question, where the question sits among the things a way performs. The question may instead head a state
    of its own. The run is then what a way calling that state performs last, and the call is the first that way makes.

    A guard anywhere else reads an earlier run or reads nothing. `interpreter._try_did_fill_span` raises where the guard
    reads nothing.
    """
    faults = []
    asking = {name for name, production in grammar.items() if _does_ask_about_a_run(production)}
    for name, production in grammar.items():
        for way in _ALTERNATIVES(production.body):
            # The caller makes a run that sits at the head of a way. The walk over call sites below checks that.
            # Elsewhere the run must sit in this way.
            for at, item in enumerate(way.actions[1:], start=1):
                if ir.AS_A_FULL_SPAN_GUARD(item) is not None and ir.AS_A_LIMITED_SPAN(way.actions[at - 1]) is None:
                    faults.append(f"{name}: a way asks about a run where no run comes in front of it")
            calls = _calls_of_way(way)
            if not calls or calls[0].name not in asking:
                continue
            if not way.actions or ir.AS_A_LIMITED_SPAN(way.actions[-1]) is None:
                faults.append(f"{name}: hands control to a question about a run it did not make")
    return faults


_EVERY_SPAN_QUESTION_FOLLOWS_ITS_RUN = _Invariant(
    "every-span-question-follows-its-run", _every_span_question_follows_its_run
)


@dataclasses.dataclass(frozen=True)
class _CallSite:
    """A call a way of `caller` makes. `rest` holds the calls of `way` past `call`."""

    caller: str
    way: ir.AlternativeState
    call: ir.RefCall
    rest: tuple[ir.Node, ...]


def _call_sites(grammar: dict[str, ir.Prod]) -> dict[str, list[_CallSite]]:
    """`{callee: the call sites of the callee}`. The map holds the callees `grammar` defines that a way calls."""
    sites: dict[str, list[_CallSite]] = {}
    for caller, production in grammar.items():
        for way in _ALTERNATIVES(production.body):
            calls = _calls_of_way(way)
            for at, call in enumerate(calls):
                if call.name in grammar:
                    sites.setdefault(call.name, []).append(_CallSite(caller, way, call, calls[at + 1 :]))
    return sites


def _are_nullable(parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways) -> bool:
    """Whether a run of a way's parts can take no character."""
    return all(_is_nullable(part, grammar, ways) for part in parts)


def _accepted_past(
    parts: tuple[ir.Node, ...],
    after: spaces.SubSpace,
    grammar: dict[str, ir.Prod],
    known: Mapping[str, spaces.SubSpace],
    ways: _Ways,
) -> spaces.SubSpace:
    """
    The states the parse can take a character in past a point of a way. `parts` are the parts of the way past that
    point. `after` holds the states past the way. The answer adds `after` where `_are_nullable` finds that `parts` may
    consume nothing.
    """
    space = _accepted_parts(parts, grammar, known, ways)
    return space | after if _are_nullable(parts, grammar, ways) else space


def _follow_seeds(grammar: dict[str, ir.Prod]) -> dict[str, spaces.SubSpace]:
    """
    `{name: spaces.SubSpace}`, what follows a production the parse enters without a call. A production the parse enters
    by name ends the parse, and the end of the stream follows it. A production the parse enters by matching can be
    followed by anything. A production the parse enters only by a call gets `NOWHERE`.
    """
    seeds = {name: spaces.NOWHERE for name in grammar}
    for name in _entered_by_name(grammar):
        seeds[name] = spaces.characters(is_at_end=True)
    for name in _entered_by_matching(grammar):
        seeds[name] = spaces.COMPLETE
    return seeds


def _follows(
    grammar: dict[str, ir.Prod], known: Mapping[str, spaces.SubSpace], ways: _Ways
) -> dict[str, spaces.SubSpace]:
    """
    `{name: spaces.SubSpace}`, the states the parse can take a character in where a production has matched.

    A call site contributes what its way does past the call. The map is a least fixed point seeded from `_follow_seeds`.
    A caller whose answer grows puts its callees back on the worklist.
    """
    sites = _call_sites(grammar)
    seeds = _follow_seeds(grammar)

    def answer(name: str, follows: Mapping[str, spaces.SubSpace]) -> spaces.SubSpace:
        space = seeds[name]
        for site in sites.get(name, ()):
            space = space | _accepted_past(site.rest, follows[site.caller], grammar, known, ways)
        return space

    return ir.fixed_point(
        "the states the parse can take a character in where a production has matched",
        seeds,
        answer,
        _inverted(_callers(grammar)),
    )


# The counted runs a part of a way makes.
_COUNTED_RUNS: ir.Question[tuple[ir.ConsumeLimitedSpanAction, ...]] = ir.Question(
    "the counted runs a part of a way makes",
    {
        ir.ConsumeLimitedSpanAction: lambda node: (node,),
        (
            *(kind for kind in ir.CONSUMING if kind is not ir.ConsumeLimitedSpanAction),
            *ir.ACTIONS,
            *ir.GUARDS,
            ir.RefCall,
        ): (),
    },
)

# A part of a way as the counted run a rewrite of that part expects.
_AS_THE_COUNTED_RUN: ir.Question[ir.ConsumeLimitedSpanAction] = ir.Question(
    "a part of a way as the counted run a rewrite of that part expects",
    {ir.ConsumeLimitedSpanAction: lambda node: node},
)

# Whether a part of a way asks whether the run in front of it filled.
_DOES_ASK_IF_THE_RUN_FILLED: ir.Question[bool] = ir.Question(
    "whether a part of a way asks whether the run in front of it filled",
    {
        ir.DidMatchFullSpanGuard: True,
        (
            *(kind for kind in ir.GUARDS if kind is not ir.DidMatchFullSpanGuard),
            *ir.ACTIONS,
            *ir.CONSUMING,
            ir.RefCall,
        ): False,
    },
)


@dataclasses.dataclass(frozen=True)
class _UnfollowedRun:
    """
    A counted run in `owner` whose follower takes none of the run's characters. The run is the part at `at` among the
    parts of `way`. `site` names the call site of `owner` that holds the follower. `is_entry` marks a follower that
    comes after the parse enters `owner` without a call. The run's own way holds the follower where `site` is None and
    `is_entry` is false.
    """

    owner: str
    way: ir.AlternativeState
    at: int
    site: _CallSite | None = None
    is_entry: bool = False


def _unfollowed_runs(grammar: dict[str, ir.Prod]) -> list[_UnfollowedRun]:
    """
    The counted runs whose follower takes none of the run's characters. A run can end its production. A call site of the
    production then says what follows the run. This function reads a call site as a place of its own.
    """
    known = _accepted_spaces(grammar)
    ways = _split_ways(grammar)
    follows = _follows(grammar, known, ways)
    sites = _call_sites(grammar)
    seeds = _follow_seeds(grammar)
    unfollowed = []
    for name, production in grammar.items():
        for way in _ALTERNATIVES(production.body):
            parts = _parts_of_way(way)
            for at, run in ((at, run) for at, part in enumerate(parts) for run in _COUNTED_RUNS(part)):
                taken = _accepted_part(run, grammar, known)
                rest = parts[at + 1 :]
                if not _are_nullable(rest, grammar, ways):
                    if not _accepted_parts(rest, grammar, known, ways) & taken:
                        unfollowed.append(_UnfollowedRun(name, way, at))
                    continue
                if seeds[name] and not _accepted_past(rest, seeds[name], grammar, known, ways) & taken:
                    unfollowed.append(_UnfollowedRun(name, way, at, is_entry=True))
                for site in sites.get(name, ()):
                    after = _accepted_past(site.rest, follows[site.caller], grammar, known, ways)
                    if not _accepted_past(rest, after, grammar, known, ways) & taken:
                        unfollowed.append(_UnfollowedRun(name, way, at, site))
    return unfollowed


def _unfollowed_fault(run: _UnfollowedRun) -> str:
    """The fault `_every_counted_run_can_be_followed_by_its_character` reports for `run`."""
    if run.site is not None:
        return f"{run.site.caller}: the way past the call of {run.owner} takes none of its run's characters"
    if run.is_entry:
        return f"{run.owner}: an entry's follow takes none of its run's characters"
    return f"{run.owner}: the way past a counted run takes none of its characters"


def _every_counted_run_can_be_followed_by_its_character(grammar: dict[str, ir.Prod]) -> list[str]:
    """Check that what follows a counted run can begin with a character of the run."""
    return [_unfollowed_fault(run) for run in _unfollowed_runs(grammar)]


_EVERY_COUNTED_RUN_CAN_BE_FOLLOWED_BY_ITS_CHARACTER = _Invariant(
    "every-counted-run-can-be-followed-by-its-character", _every_counted_run_can_be_followed_by_its_character
)


# Whether a part of a way can commit the parse before the part takes a character. `commits` answers that question of a
# production.
_CAN_COMMIT_FIRST: ir.Question[bool] = ir.Question(
    "whether a part of a way can commit the parse before it takes a character",
    {
        # A `(cut)` commits. The open of a committed region commits too.
        (ir.CutAction, ir.PushMessageAction): True,
        ir.RefCall: lambda node, commits: commits[node.name],
        (
            *(kind for kind in ir.ACTIONS if kind not in (ir.CutAction, ir.PushMessageAction)),
            *ir.CONSUMING,
            *ir.GUARDS,
        ): False,
    },
)


def _can_commit_first_in(
    parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways, commits: Mapping[str, bool]
) -> bool:
    """
    Whether a run of a way's parts can commit the parse before the run takes a character. `commits` answers that
    question of a production.
    """
    for part in parts:
        if _CAN_COMMIT_FIRST(part, commits):
            return True
        if not _is_nullable(part, grammar, ways):
            return False
    return False


def _commits_first(grammar: dict[str, ir.Prod], ways: _Ways) -> dict[str, bool]:
    """
    The function answers a `{name: bool}` map and computes it as a least fixed point. The map says whether a production
    can commit the parse before the production takes a character.
    """
    return ir.fixed_point(
        "whether a production can commit the parse before it takes a character",
        {name: False for name in grammar},
        lambda name, commits: any(
            _can_commit_first_in(_parts_of_way(way), grammar, ways, commits)
            for way in _ALTERNATIVES(grammar[name].body)
        ),
        _callers(grammar),
    )


def _can_commit_past(
    parts: tuple[ir.Node, ...],
    owner: str,
    grammar: dict[str, ir.Prod],
    ways: _Ways,
    commits: Mapping[str, bool],
    commit_follows: Mapping[str, bool],
) -> bool:
    """
    Whether the parse can commit before it takes a character, past a point of a way of `owner`. `parts` are the parts of
    the way past that point. A part that can commit first makes the answer true where only nullable parts come before
    it. Otherwise nullable `parts` defer to `commit_follows` of `owner`.
    """
    if _can_commit_first_in(parts, grammar, ways, commits):
        return True
    return _are_nullable(parts, grammar, ways) and commit_follows[owner]


def _commit_follows(grammar: dict[str, ir.Prod], ways: _Ways, commits: Mapping[str, bool]) -> dict[str, bool]:
    """
    `{name: bool}`, whether the parse can commit before it takes a character where a production has matched. The answer
    is a least fixed point over the call sites. A caller whose answer grows puts its callees back on the worklist.
    """
    sites = _call_sites(grammar)
    return ir.fixed_point(
        "whether the parse can commit before it takes a character where a production has matched",
        {name: False for name in grammar},
        lambda name, follows: any(
            _can_commit_past(site.rest, site.caller, grammar, ways, commits, follows) for site in sites.get(name, ())
        ),
        _inverted(_callers(grammar)),
    )


# `WINDOWS_OPENED` gives the change a node makes to the depth of open `(max)` windows. A depth of `_MANY_WINDOWS` means
# itself or a deeper depth.
_MANY_WINDOWS = 2

# A set of depths of open windows. The parse may be at any of them.
_Depths = frozenset[int]


def _window_depth_change_through(actions: Iterable[ir.Node]) -> int:
    """The change `actions` make to the depth of open windows."""
    return sum(ir.WINDOWS_OPENED(action) for action in actions)


def _window_depths_shifted(depths: _Depths, shifts: Iterable[int]) -> _Depths:
    """
    The depths of open windows past a shift by any of `shifts`. A shift that closes more windows than are open gives no
    depth. `_MANY_WINDOWS` means that depth or any deeper one.
    """
    shifted = set()
    for depth in depths:
        for shift in shifts:
            if depth < _MANY_WINDOWS:
                lowest = highest = depth + shift
            else:
                lowest, highest = _MANY_WINDOWS + min(shift, 0), _MANY_WINDOWS
            shifted |= {min(past, _MANY_WINDOWS) for past in range(max(lowest, 0), highest + 1)}
    return frozenset(shifted)


def _window_depth_changes_of_way(way: ir.AlternativeState, changes: Mapping[str, frozenset[int]]) -> frozenset[int]:
    """The changes `way` can make to the depth of open windows. `changes` gives the changes of a production."""
    changed = frozenset({_window_depth_change_through(way.actions)})
    for call in _calls_of_way(way):
        changed = frozenset(before + after for before in changed for after in changes[call.name])
    return changed


def _window_depth_changes(grammar: dict[str, ir.Prod]) -> dict[str, frozenset[int]]:
    """
    `{name: changes}`, the changes a production can make to the depth of open windows. A least fixed point. A callee
    whose changes grow puts its callers back on the worklist.
    """
    return ir.fixed_point(
        "the changes a production makes to the depth of open windows",
        {name: frozenset() for name in grammar},
        lambda name, changes: _WINDOW_DEPTH_CHANGES_OF_BODY(grammar[name].body, changes),
        _callers(grammar),
    )


# The changes a body can make to the depth of open windows. `changes` gives the changes of a production.
_WINDOW_DEPTH_CHANGES_OF_BODY: ir.Question[frozenset[int]] = ir.Question(
    "the changes a body can make to the depth of open windows",
    {
        ir.ChoiceState: lambda node, changes: frozenset().union(
            *(_window_depth_changes_of_way(way, changes) for way in node.alternatives)
        ),
        # A set takes a character and opens or closes no window.
        ir.CharSet: frozenset({0}),
    },
)


def _window_depths_at_call(
    site: _CallSite, entered: Mapping[str, _Depths], changes: Mapping[str, frozenset[int]]
) -> _Depths:
    """The depths of open windows where the parse makes the call `site` names."""
    depths = _window_depths_shifted(entered[site.caller], {_window_depth_change_through(site.way.actions)})
    if site.call is site.way.second and site.way.first is not None:
        depths = _window_depths_shifted(depths, changes[site.way.first.name])
    return depths


def _window_depths_entered(grammar: dict[str, ir.Prod], changes: Mapping[str, frozenset[int]]) -> dict[str, _Depths]:
    """
    `{name: depths}`, the depths of open windows on entering a production. The map is a least fixed point. The parse can
    enter a production by name or by matching. The parse then holds no window open. A caller whose depths grow puts its
    callees back on the worklist.
    """
    sites = _call_sites(grammar)
    seeds: dict[str, _Depths] = {name: frozenset() for name in grammar}
    for name in _entered_by_name(grammar) | _entered_by_matching(grammar):
        seeds[name] = frozenset({0})

    def answer(name: str, entered: Mapping[str, _Depths]) -> _Depths:
        depths = seeds[name]
        for site in sites.get(name, ()):
            depths = depths | _window_depths_at_call(site, entered, changes)
        return depths

    return ir.fixed_point(
        "the depths of open windows on entering a production",
        seeds,
        answer,
        _inverted(_callers(grammar)),
    )


def _is_refinable(
    run: _UnfollowedRun,
    grammar: dict[str, ir.Prod],
    ways: _Ways,
    commits: Mapping[str, bool],
    commit_follows: Mapping[str, bool],
    changes: Mapping[str, frozenset[int]],
    entered: Mapping[str, _Depths],
) -> bool:
    """
    Whether `_refined_way` keeps the parse of `run` at its call site. The conditions are these. A question whether the
    run filled comes right past the run. The follower cannot commit the parse before it takes a character. The parse
    holds no `(max)` window open where the run takes, as `WINDOWS_OPENED` counts them.
    """
    site = run.site
    if site is None:
        return False
    parts = _parts_of_way(run.way)
    if run.at + 1 >= len(parts) or not _DOES_ASK_IF_THE_RUN_FILLED(parts[run.at + 1]):
        return False
    if _can_commit_first_in(parts[run.at + 1 :], grammar, ways, commits):
        return False
    if _can_commit_past(site.rest, site.caller, grammar, ways, commits, commit_follows):
        return False
    depths = _window_depths_at_call(site, entered, changes)
    depths = _window_depths_shifted(depths, {_window_depth_change_through(parts[: run.at])})
    return depths <= frozenset({0})


def _refined_way(way: ir.AlternativeState, at: int) -> ir.AlternativeState:
    """
    The answer copies `way` and writes the counted run at part `at` as a maximal run. A pair of guards past the run asks
    that its length is the count. The pair takes the place of the question whether the run filled.
    """
    index = at - len(way.gate.guards)
    run = _AS_THE_COUNTED_RUN(way.actions[index])
    length = ir.LenValue(arg=ir.MatchValue())
    taking = (
        ir.ConsumeSpanAction(set=run.set),
        ir.IsLessEqualGuard(a=length, b=run.limit),
        ir.IsLessEqualGuard(a=run.limit, b=length),
    )
    return dataclasses.replace(way, actions=(*way.actions[:index], *taking, *way.actions[index + 2 :]))


def _refine_counted_runs(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Point a call site at a refined copy of the production it calls. The copy writes a counted run as a maximal run and a
    pair of guards on its length. A site gets the copy where `_is_refinable` holds for the runs the copy refines.
    """
    ways = _split_ways(grammar)
    commits = _commits_first(grammar, ways)
    commit_follows = _commit_follows(grammar, ways, commits)
    changes = _window_depth_changes(grammar)
    entered = _window_depths_entered(grammar, changes)
    refined: dict[_CallSite, set[_RunAt]] = {}
    for run in _unfollowed_runs(grammar):
        if _is_refinable(run, grammar, ways, commits, commit_follows, changes, entered):
            assert run.site is not None, "`_is_refinable` holds for a run with no call site"
            refined.setdefault(run.site, set()).add((run.way, run.at))
    result = dict(grammar)
    copies: dict[tuple[str, frozenset[_RunAt]], str] = {}
    redirected: dict[str, dict[ir.AlternativeState, ir.AlternativeState]] = {}
    for site, runs in refined.items():
        owner = site.call.name
        key = (owner, frozenset(runs))
        if key not in copies:
            copies[key] = namer.fresh(owner)
            result[copies[key]] = _refined_copy(grammar[owner], key[1])
        redirects = redirected.setdefault(site.caller, {})
        caller_way = redirects.get(site.way, site.way)
        call = dataclasses.replace(site.call, name=copies[key])
        if site.call is site.way.first:
            redirects[site.way] = dataclasses.replace(caller_way, first=call)
        else:
            redirects[site.way] = dataclasses.replace(caller_way, second=call)
    for caller, redirects in redirected.items():
        result[caller] = _redirected(grammar[caller], redirects)
    return result


# The way holding a counted run, and the place of the run among the parts of that way.
_RunAt = tuple[ir.AlternativeState, int]


def _refined_copy(production: ir.Prod, runs: frozenset[_RunAt]) -> ir.Prod:
    """A copy of `production`. `_refined_way` rewrites the counted runs that `runs` names."""

    def refined(way: ir.AlternativeState) -> ir.AlternativeState:
        # Refining the last run of a way first leaves the positions of its earlier runs unmoved.
        for at in sorted((at for held, at in runs if held == way), reverse=True):
            way = _refined_way(way, at)
        return way

    return dataclasses.replace(production, body=_WAYS_RETOLD(production.body, refined))


def _redirected(production: ir.Prod, redirects: Mapping[ir.AlternativeState, ir.AlternativeState]) -> ir.Prod:
    """A copy of `production`. `redirects` maps a way to the way the copy holds in its place."""
    return dataclasses.replace(production, body=_WAYS_RETOLD(production.body, lambda way: redirects.get(way, way)))


def _every_consume_is_protected_by_a_gate(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a gate protects a take. The gate must hold a `LookGuard` asked since the parse last took characters. A
    take of a character set needs no gate. The `LookGuard` sits in the gate of the way or among the actions before the
    take. The parse may instead ask the `LookGuard` on entering the way.

    A question that the entering ways asked holds here, at the position where they enter. `_asked_where_entered` says
    so. A guard can therefore move up to the choice that needs one, and the protected take keeps its warrant.
    """
    faults = []
    entering = _asked_where_entered(grammar)
    for name, production in grammar.items():
        body = production.body
        # A body that is not a choice offers no way to read a gate off. It reads as a way with an empty gate.
        ways = (
            choice.alternatives
            if (choice := ir.AS_A_CHOICE(body)) is not None
            else (ir.AlternativeState(gate=ir.GatePart(), actions=tuple(_held(body))),)
        )
        for way in ways:
            parts = _parts_of_way(way)
            # A path into the way must have asked. A pair of paths need not have asked the same thing. A path with no
            # look fails unless the way asks one.
            faults += [f"{name}: a way takes what no gate found"] * sum(
                ir.IS_CONSUMING(item)
                and ir.AS_A_CHAR_SET(item) is None
                and not all(
                    any(ir.AS_A_LOOK(guard) is not None for guard in _guards_in_force(parts, at, path))
                    for path in entering[name] or (frozenset(),)
                )
                for at, item in enumerate(parts)
            )
    return faults


_EVERY_CONSUME_IS_PROTECTED_BY_A_GATE = _Invariant(
    "every-consume-is-protected-by-a-gate", _every_consume_is_protected_by_a_gate
)


def _split_consumes_into_gates(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say the asking and the taking as separate things. `A = |g CharSet(S) rest|` becomes `A = |g Look(S) ConsumeChar
    rest|`. This is a rewrite rather than a question. It reshapes a `CharSet` among a way's actions, and hands back the
    untouched parts.

    A character set among a way's actions asks whether the character belongs to the set. The same set then takes the
    character. A caller learns the answer only by entering the way. The answer should decide whether the caller enters.
    The split puts the question in the gate, where a caller sees it. `ConsumeCharAction` then takes the character the
    gate found.

    The question moves in front of the actions that came before it, where the set is not the first thing the way does.
    `GUARD_CROSSES_ACTION` says whether the question may cross those actions. A pass moves a single set per way. A
    second would have to cross the take the first left behind, and a character already taken is a different position.

    A call to a character-set production says the same thing indirectly. `b-break`'s first way is `b-line-feed`. That
    production is `LF` and no more. The split swaps the call for its set in a way whose actions do not consume. The
    split then treats that set like any other. A way whose actions consume keeps its call. That call aims at a minted
    way asking for the set. The production stays put. A consume holds the production's set as what it runs over.
    """

    wrapped: dict[str, str | ir.Prod] = {}

    def gated(name: str, body: ir.Node) -> str:
        """The name of the way asking for `body` and taking it. The first call for the class mints it."""
        if name not in wrapped:
            held = namer.fresh(name)
            wrapped[name] = held
            wrapped[held] = ir.Prod(
                grammar[name].number,
                held,
                (),
                ir.ChoiceState(
                    alternatives=(
                        ir.AlternativeState(
                            gate=ir.GatePart(guards=(ir.LookGuard(item=body),)),
                            actions=(ir.ConsumeCharAction(set=body),),
                        ),
                    )
                ),
            )
        minted = wrapped[name]
        assert isinstance(minted, str), f"`{name}` holds {minted!r} rather than a gated name"
        return minted

    def pulled(way: ir.AlternativeState) -> ir.AlternativeState:
        """`way` has the calls to a character set written as that set, or aimed at a way that asks for it."""
        for _round in ir.rounds("the classes a way calls"):
            settled = _pulled_once(way)
            if settled == way:
                return way
            way = settled
        raise AssertionError("`ir.rounds` raises where the rounds run out")

    def _pulled_once(way: ir.AlternativeState) -> ir.AlternativeState:
        """A round moves the first slot whose call reaches a character set. The rounds repeat until no call moves."""
        for slot in ("first", "second"):
            held: ir.RefCall | None = getattr(way, slot)
            if held is None or held.name not in grammar or way.recover is not None:
                continue
            body = grammar[held.name].body
            if ir.AS_A_CHAR_SET(body) is None:
                continue
            # A way takes at most once. A way that already takes aims the call at a way asking for the class. The class
            # stays as the set a consume runs over.
            if any(ir.IS_CONSUMING(action) for action in way.actions):
                return _with_call(way, slot, ir.RefCall(name=gated(held.name, body), args=held.args))
            if slot == "first" and way.second is not None:
                return dataclasses.replace(way, actions=(*way.actions, body), first=None)
            return _with_call(dataclasses.replace(way, actions=(*way.actions, body)), slot, None)
        return way

    def told(way: ir.AlternativeState) -> ir.AlternativeState:
        at = next((index for index, action in enumerate(way.actions) if ir.AS_A_CHAR_SET(action) is not None), None)
        if at is None:
            return way
        asked = ir.LookGuard(item=way.actions[at])
        if not _may_cross_every(((asked, action) for action in way.actions[:at]), grammar):
            return way
        return dataclasses.replace(
            way,
            gate=ir.GatePart(guards=(*way.gate.guards, asked)),
            actions=(*way.actions[:at], ir.ConsumeCharAction(set=way.actions[at]), *way.actions[at + 1 :]),
        )

    written = {
        name: dataclasses.replace(production, body=_WAYS_RETOLD(production.body, lambda way: told(pulled(way))))
        for name, production in grammar.items()
    }
    return {**written, **{name: held for name, held in wrapped.items() if isinstance(held, ir.Prod)}}


def _called_first(way: ir.AlternativeState, grammar: dict[str, ir.Prod]) -> str | None:
    """
    The name of the production `way` enters first, where that production offers ways. Anything else is `None`.

    `first` is the call and `second` is where the way continues from it. A way therefore enters `first`, or `second`
    where the way is a tail call.
    """
    held = _first_call_of_way(way)
    if held is None or held.name not in grammar:
        return None
    return held.name if ir.AS_A_CHOICE(grammar[held.name].body) is not None else None


def _asked_by_every_way(name: str, grammar: dict[str, ir.Prod]) -> set[ir.Node]:
    """
    Answer the guards the ways of `name` share.

    A production offering a single way is the plain case. The gate of that way is the answer. A way may ask a guard its
    siblings lack. That guard tells the way from its siblings.

    A body that is not a choice offers no ways. It asks nothing a caller could ask instead.
    """
    choice = ir.AS_A_CHOICE(grammar[name].body)
    if choice is None:
        return set()
    ways = choice.alternatives
    shared = set(ways[0].gate.guards)
    for way in ways[1:]:
        shared &= set(way.gate.guards)
    return shared


def _inlined_called_ways(grammar: dict[str, ir.Prod], namer: _Namer | None = None) -> dict[str, ir.Prod]:
    """
    Put a production offering a single way at the call to it. `A = |gA ->B sA|` with `B = |fB sB|` becomes `A = |gA fB
    T|` with `T = |sB sA|`.

    A call to a production offering a single way is unconditional. Entering the production is running that way. The work
    of that way can therefore go into the site the call had.

    This runs wherever the call appears, and not at a lone site only. A callee a pair of ways reach goes into both, and
    both then run a copy of the callee's way. A callee no site still calls goes away in the sweep.

    This runs only where `B` asks nothing. The parse asks the gate of `B` where it enters `B`. That position is past
    what the caller performs. A gate left in the middle of a way is a question the parse asks after entering the way.
    `every-guard-is-in-a-gate` forbids that.

    This runs only where neither way has a recovery. A recovery rides the push its call makes, and that call changes
    here.

    A way holds a call and a continuation. Inlining brings the callee's call, the callee's continuation and the caller's
    continuation, and the way has a pair of slots. This re-associates the calls and the continuations rather than
    refusing. `(D E) C` is `D (E C)`. The way calls `D` and continues to a state holding `E` and then `C`. That is the
    right-oriented shape, a call and a tail. The state minted for the tail is therefore not the state the inlining
    removed. The call to `B` goes.

    This leaves a way needing a minted state untouched where the caller passes no namer.
    """
    minted = {}

    def inlined(way: ir.AlternativeState) -> ir.AlternativeState:
        # The call is `first`, or `second` where the way makes none of its own. A tail call counts as a call.
        held = _first_call_of_way(way)
        if held is None or way.recover is not None:
            return way
        choice = ir.AS_A_CHOICE(grammar[held.name].body)
        if choice is None or len(choice.alternatives) != 1:
            return way
        [only] = choice.alternatives
        if only.gate.guards or only.recover is not None:
            return way
        # Return the way unchanged where the callee's actions hold a guard.
        if any(ir.IS_A_GUARD(action) for action in only.actions):
            return way
        actions = (*way.actions, *only.actions)
        if not actions and only.first is None and only.second is None:
            return way  # The way would then perform nothing and call nothing.
        if actions and only.first is not None:
            return way
        if sum(ir.IS_CONSUMING(action) for action in actions) > 1:
            # A way takes once. `mint-consume-states` cuts a way that would take twice. Inlining would undo that cut.
            return way
        # The way continues here once the callee has run. The tail call of a way continues to nothing.
        continues = None if way.first is None else way.second
        if continues is None:
            return dataclasses.replace(way, actions=actions, first=only.first, second=only.second)
        if only.second is None:
            return dataclasses.replace(way, actions=actions, first=only.first, second=continues)
        if namer is None:
            return way
        tail = namer.fresh(held.name)
        minted[tail] = ir.Prod(
            grammar[held.name].number,
            tail,
            (),
            ir.ChoiceState(
                alternatives=(ir.AlternativeState(gate=ir.GatePart(), first=only.second, second=continues),)
            ),
        )
        return dataclasses.replace(way, actions=actions, first=only.first, second=ir.RefCall(name=tail, args=()))

    written = {
        name: dataclasses.replace(production, body=_WAYS_RETOLD(production.body, inlined))
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _hoist_guards_to_callers(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Ask a callee's guards where the parse enters its caller, rather than at the call. `A = |gA actA ->B|` with `B = |gB
    actB ...|` becomes `A = |gA gB actA ->B'|`. `B'` is `B` without the guards that moved.

    A guard takes nothing, and no kind in `GUARDS` sits in `CONSUMING`. A guard's position therefore changes what the
    parse decides and not what it matches. Anything between the call and the entry could change the answer, and `actA`
    sits between. `GUARD_CROSSES_ACTION` says whether a guard may come in front of an action rather than behind it. A
    guard moves where the actions between admit it. A guard the actions refuse stays put.

    `_asked_by_every_way` says what moves. A callee offering a single way gives its gate. A callee offering more gives
    the guards its ways share. The parse asks those before choosing any way. A guard in a way its siblings lack stays.
    That guard tells the way from its siblings.

    The result is `B'` rather than `B`. Callers share a callee, and a guard taken out for the sake of a caller is a
    guard the other callers stop asking. This mints `B'` per callee and per set of guards taken. The sites that took
    those guards call `B'`. A site that took nothing goes on calling `B`.

    This runs until nothing moves. A caller that has taken a guard is a callee its own callers can take that guard from.
    The questions therefore climb until they reach a way that performs something the guard may not cross, or a choice
    whose ways ask different things.
    """
    for _round in ir.rounds("hoist-guards-to-callers"):
        settled = _cleaned(_hoisted_once(grammar, namer))
        namer.sees(settled)
        if settled == grammar:
            return grammar
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _hoisted_once(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    A single pass of `hoist-guards-to-callers`. A caller's way takes over guards from the production the way enters. The
    way then calls a minted copy of that production without those guards.
    """
    minted, named = {}, {}

    def without(name: str, taken: frozenset[ir.Node]) -> str:
        """The name of `name` with `taken` gone from its ways. This mints the name where a site first wants it."""
        key = (name, taken)
        if key not in named:
            held = namer.fresh(name)
            named[key] = held
            minted[held] = dataclasses.replace(
                grammar[name],
                name=held,
                body=ir.ChoiceState(
                    alternatives=tuple(
                        dataclasses.replace(way, gate=ir.GatePart(guards=tuple(set(way.gate.guards) - taken)))
                        for way in getattr(grammar[name].body, "alternatives")
                    )
                ),
            )
        return named[key]

    def told(way: ir.AlternativeState) -> ir.AlternativeState:
        called = _called_first(way, grammar)
        if called is None:
            return way
        taken = frozenset(
            guard
            for guard in _asked_by_every_way(called, grammar)
            if _may_cross_every(((guard, action) for action in way.actions), grammar)
        )
        if not taken:
            return way
        first = _first_call_of_way(way)
        assert first is not None, "a way with a called production makes no call"
        held = ir.RefCall(name=without(called, taken), args=first.args)
        moved = dataclasses.replace(way, gate=ir.GatePart(guards=(*way.gate.guards, *taken)))
        return (
            dataclasses.replace(moved, first=held) if way.first is not None else dataclasses.replace(moved, second=held)
        )

    written = {
        name: dataclasses.replace(production, body=_WAYS_RETOLD(production.body, told))
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _does_ask_after_an_empty_turn(performed: Sequence[ir.Node], way: ir.AlternativeState) -> bool:
    """
    Whether `way`'s gate asks after a turn that `performed` opened and took nothing in.

    A turn that must take a character is an open, a guard that reads it, and a close. `StartMustConsumeAction` records
    where it began. `DidConsumeSinceOpenGuard` asks whether the parse has taken anything since. `EndMustConsumeAction`
    takes the open off. Together they stop a run over a body matching empty from spinning. Such an open may sit among
    the actions a path performed, and no take follows the open among those actions. The guard then answers no under any
    input. The gate refuses any input, and the path is a path no parse takes.
    """
    for held_guard in way.gate.guards:
        guard = ir.AS_A_SINCE_OPEN_GUARD(held_guard)
        if guard is None:
            continue
        for at, action in enumerate(performed):
            start = ir.AS_A_MUST_CONSUME_START(action)
            if start is not None and start.pair == guard.pair:
                return not any(ir.IS_CONSUMING(held) for held in performed[at + 1 :])
    return False


def _flatten_ungated_call_trees(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a way that nothing has gated as the paths that it is, a gated way per path. `A = |actA ->B contA| |...|` with `B
    = |gB actB fB contB|` becomes `A = |gB actA actB fB M| |...|`. `M` runs `contB` and then `contA`.

    This step rewrites a way rather than answering a question. It reshapes `ir.ChoiceState` and hands back a body of any
    other kind.

    A way with no gate hands control on. That way hands control to a choice, and a way of that choice may lack a gate as
    well. The walk leaves the way, goes through the calls and the continuations, and reaches a gate. The first question
    past the way is the question that decides entry into the way. The count of calls down to that question does not
    matter. A path written out here is that gate, what the parse performed on the way to it, and where the path goes
    from there.

    The walk goes through the continuations as well as the calls. A way holding no call is not the end of a path. The
    innermost thing still pending runs next, and the gate is in there. A path ends only where a way has a gate. An
    ending anywhere else is a tree this step cannot flatten, and this step raises rather than passing over it. Such a
    path ends with nothing pending, on a body that is not a choice, or back at a production already entered. This step
    also raises on a way along the path that holds a recovery.

    This step keeps the order of the paths. The paths go into the site the way had, depth first and left to right. That
    is the order the machine would have reached those gates in.

    The path's action goes behind the gate. That is a move and not a copy. `GUARD_CROSSES_ACTION` says whether the parse
    may ask a guard in front of an action rather than behind it. A way holding a path whose gate may not come up does
    not change. The other paths of that way stay out as well. A way left with no path after the drops does not change
    either.

    This step drops a path whose gate asks after a turn the path itself opened and took nothing in. The guard asks
    whether the parse took anything since that open. The guard therefore refuses on such a path, and the input does not
    matter. The way holding the path fails there and falls to the way behind it. With the path gone the way reaches its
    refusal sooner. Cancelling the turn and its guard would make the path succeed. A run over a body that matches empty
    input would then spin forever.

    This step runs a remainder of more than a single call in states of its own. Past the call the leaf makes comes the
    continuation the leaf holds. Then come the pending continuations innermost first, and the outermost runs last. That
    is a chain of states. A state holds a call and where to go after it. The paths that end the same way share that
    chain.
    """
    minted: dict[str, ir.Prod] = {}
    named: dict[ir.Node, str] = {}
    wanted = {id(way) for _name, way in _ungated_ways(grammar)}

    def spine(calls: Sequence[ir.RefCall], owner: str) -> ir.RefCall | None:
        """calls run in turn. A call is the call a way continues to."""
        if len(calls) < 2:
            return calls[0] if calls else None
        body = ir.ChoiceState(
            alternatives=(ir.AlternativeState(gate=ir.GatePart(), first=calls[0], second=spine(calls[1:], owner)),)
        )
        held = named.get(body)
        if held is None:
            held = named[body] = namer.fresh(owner)
            minted[held] = ir.Prod(grammar[owner].number, held, (), body)
        return ir.RefCall(name=held, args=())

    def paths(
        owner: str, way: ir.AlternativeState
    ) -> list[tuple[tuple[ir.Node, ...], ir.AlternativeState, tuple[ir.RefCall, ...]]]:
        """The paths out of `way`, as `(what it performs before the gate, the gated way, what is left pending)`."""
        found: list[tuple[tuple[ir.Node, ...], ir.AlternativeState, tuple[ir.RefCall, ...]]] = []

        def walk(
            one: ir.AlternativeState,
            entered: frozenset[str],
            performed: tuple[ir.Node, ...],
            pending: tuple[ir.RefCall, ...],
        ) -> None:
            if one.recover is not None:
                raise ValueError(f"{owner}: a recovery rides a call a way that nothing has gated reaches")
            if _does_ask_after_an_empty_turn(performed, one):
                return
            if one.gate.guards:
                found.append((performed, one, pending))
                return
            done = (*performed, *one.actions)
            left = (*pending, one.second) if one.first is not None and one.second is not None else pending
            call: ir.RefCall | None = one.first if one.first is not None else one.second
            if call is None:
                if not left:
                    raise ValueError(f"{owner}: a path reaches no gate at all and leaves nothing pending")
                call, left = left[-1], left[:-1]
            if call.name in entered:
                raise ValueError(f"{owner}: a path reaching {call.name} a second time before any gate")
            choice = ir.AS_A_CHOICE(grammar[call.name].body)
            if choice is None:
                raise ValueError(f"{owner}: a path reaches {call.name}. That production offers no way for a gate.")
            for opened in choice.alternatives:
                walk(opened, entered | {call.name}, done, left)

        walk(way, frozenset({owner}), (), ())
        return found

    def told(owner: str, way: ir.AlternativeState) -> tuple[ir.AlternativeState, ...]:
        if id(way) not in wanted:
            return (way,)
        walked = paths(owner, way)
        # A way outside the ungated ways stays as written.
        if not walked:
            return (way,)
        if not _may_cross_every(
            (
                (guard, action)
                for performed, leaf, _pending in walked
                for guard in leaf.gate.guards
                for action in performed
            ),
            grammar,
        ):
            return (way,)

        def flattened(
            performed: tuple[ir.Node, ...], leaf: ir.AlternativeState, pending: tuple[ir.RefCall, ...]
        ) -> ir.AlternativeState:
            """The way a path makes. It holds the gate, what the path performs, and the calls left to run."""
            calls = [held for held in (leaf.second, *reversed(pending)) if held is not None]
            first = leaf.first
            if first is None and len(calls) > 1:
                first, calls = calls[0], calls[1:]
            return dataclasses.replace(
                leaf, actions=(*performed, *leaf.actions), first=first, second=spine(calls, owner)
            )

        return tuple(flattened(*held) for held in walked)

    written = {
        name: dataclasses.replace(
            production, body=_WAYS_SPLIT(production.body, lambda way, owner=name: told(owner, way))
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _atoms(held: Iterable[spaces.SubSpace]) -> list[spaces.SubSpace]:
    """
    `held`'s subspaces cut one another into pieces. The pieces do not overlap. The pieces cover between them what the
    subspaces cover.

    A given space is the union of the pieces inside it. A way on a space therefore becomes a way per piece and gives up
    no state. The cutting builds the pieces rather than an enumeration. A space in turn splits a piece it overlaps into
    the part the space holds and the part the space leaves. The remainder becomes a piece of its own.
    """
    parts: list[spaces.SubSpace] = []
    for space in held:
        fresh, rest = [], space
        for part in parts:
            shared = part & space
            if shared:
                fresh += [one for one in (shared, part - space) if one]
                rest = rest - part
            else:
                fresh.append(part)
        parts = fresh + [rest] if rest else fresh
    return parts


def _told_apart(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    Answer `spans`, and tell the invalid byte apart from the characters again.

    A subspace holds the byte that begins no character as a unit of its own. The codepoints sit beside that byte, as
    `ALL_CHARACTERS` shows. `merged_spans` coalesces the adjacent runs of a subspace, and the byte comes out in a single
    interval with codepoint `0`. A set the parser asks keeps the byte and the codepoints apart. `_spans_node` turns a
    run starting below `0` into `(-1, -1)`. The codepoints in that run drop out.
    """
    return [
        held
        for low, high in spans
        for held in (((-1, -1), (0, high)) if low < 0 <= high else ((-1, -1),) if low < 0 else ((low, high),))
    ]


def _pinned(space: spaces.SubSpace) -> dict[str, bool]:
    """
    Answer `{axis: answer}` for an axis where the answers `space` admits agree. A gate for `space` would have to pin
    those axes.
    """
    live = [guard_answers for guard_answers in spaces.ALL_GUARD_ANSWERS if space.under(guard_answers)]
    return {
        axis: getattr(live[0], axis)
        for axis in spaces.AXES
        if live and all(getattr(guard_answers, axis) == getattr(live[0], axis) for guard_answers in live)
    }


def _gate_for(atom: spaces.SubSpace, wider: spaces.SubSpace, grammar: dict[str, ir.Prod]) -> tuple[ir.Node, ...]:
    """
    The guards to add to a gate admitting `wider`. The gate with those guards admits exactly `atom`, and `atom` sits
    inside `wider`.

    This synthesises the guards and then checks them. `_admits` reads the guards back, and the result intersected with
    what the way already admits must be the atom itself. An atom this cannot say raises rather than coming back
    approximated. A gate wider than the atom would leave the ways it cut overlapping.

    The guards say a pair of things. The axes come first, a guard per axis the atom pins that the way leaves open. This
    asks for no axis the way already fixes, and leaves to the check an axis no guard can say. The characters come
    second. The function tries character guards in order. The first is no guard. The next is a lookahead at the atom's
    characters. The next is the end-of-stream question. The last refuses the characters outside the atom. The function
    tries the refusal only where the atom leaves a character out. The function keeps the first that admits exactly the
    atom.
    """
    admitted = next(iter({one for one in atom.admitted if one}), None)
    if admitted is None:
        raise ValueError("an atom holding no state at all")
    # The gate asks about an axis only where the axis narrows the space. `n <= 0` implies `n <= len(match)`. A second
    # test of that would ask a settled question at run time.
    asked: tuple[ir.Node, ...] = ()
    held = wider
    for axis, answered in _pinned(atom).items():
        guard = _says(axis, bool(answered)) if _pinned(held).get(axis) != answered else None
        if guard is not None:
            asked, held = (*asked, guard), held & _admits(guard, grammar)
    others = chars.subtracted_spans(list(spaces.ALL_CHARACTERS), list(admitted.spans))
    tried: tuple[tuple[ir.Node, ...], ...] = (
        (),
        (ir.LookGuard(item=_spans_node(_told_apart(admitted.spans))),),
        (ir.EndOfStreamGuard(),),
    ) + (((ir.NegLookGuard(item=_spans_node(_told_apart(others))),),) if others else ())
    for one in tried:
        guards = asked + one
        space = wider
        for guard in guards:
            space = space & _admits(guard, grammar)
        if space == atom:
            return guards
    raise ValueError(f"no gate says the states {atom} and no others")


def _taking(
    way: ir.AlternativeState,
    atom: spaces.SubSpace,
    owner: str,
    grammar: dict[str, ir.Prod],
    namer: _Namer,
    minted: dict[str, ir.Prod],
) -> ir.AlternativeState:
    """
    `way` with its take narrowed to the set `atom` admits. A `way` with no take comes back unchanged. So does a take
    whose set the grammar leaves open. So does a take whose set is the atom's set.

    This replaces the set of a single-character take with the set the atom admits. A run is not narrowed. A run takes
    its own set past the first character, and a set cut down would be a different run. This peels the first character
    off instead. That gives a single character from the atom, then the run again or nothing. Those write the same
    maximal run in a pair of places. The run is a `ConsumeSpanAction`, and `interpreter._try_span` runs it. The run
    takes at least a character and stops where its set stops.

    This raises on any other take. `CONSUMING` lists a limited run as such a take. Peeling a character from under a
    limit would leave the limit counting what it does not bound.
    """
    admitted = next(iter({one for one in atom.admitted if one}))
    at = next((index for index, action in enumerate(way.actions) if ir.IS_CONSUMING(action)), None)
    if at is None:
        return way
    taken = way.actions[at]
    spans = _peek_spans(_set_of(taken), grammar)
    if spans is None or tuple(map(tuple, spans)) == admitted.spans:
        return way
    char = ir.AS_A_CONSUME_CHAR(taken)
    if char is not None:
        narrowed = dataclasses.replace(char, set=_spans_node(admitted.spans))
        return dataclasses.replace(way, actions=(*way.actions[:at], narrowed, *way.actions[at + 1 :]))
    if ir.AS_A_CONSUME_SPAN(taken) is None:
        raise ValueError(f"{owner}: a parse enters a {type(taken).__name__} on fewer characters than it takes")
    rest = ir.ChoiceState(
        alternatives=(
            ir.AlternativeState(
                gate=ir.GatePart(guards=(ir.LookGuard(item=_spans_node(spans)),)),
                actions=(taken, *way.actions[at + 1 :]),
                first=way.first,
                second=way.second,
                recover=way.recover,
            ),
            ir.AlternativeState(
                gate=ir.GatePart(),
                actions=way.actions[at + 1 :],
                first=way.first,
                second=way.second,
                recover=way.recover,
            ),
        )
    )
    held = namer.fresh(owner)
    minted[held] = ir.Prod(grammar[owner].number, held, (), rest)
    return dataclasses.replace(
        way,
        actions=(*way.actions[:at], ir.ConsumeCharAction(set=_spans_node(admitted.spans))),
        first=None,
        second=ir.RefCall(name=held, args=()),
        recover=None,
    )


def _does_reach_nothing(way: ir.AlternativeState, atom: spaces.SubSpace, grammar: dict[str, ir.Prod]) -> bool:
    """
    Whether `way` calls a production whose ways it cannot reach. The parse enters the way in `atom`, and the way takes
    nothing before the call.

    The parse enters such a way and fails on any input.

    This holds where the way takes nothing first. Past a take the parse is somewhere else, and the gate tested the
    earlier position. The gate then has no say over the callee's entry.
    """
    if any(ir.IS_CONSUMING(action) for action in way.actions):
        return False
    called = _first_call_of_way(way)
    if called is None or called.name not in grammar:
        return False
    choice = ir.AS_A_CHOICE(grammar[called.name].body)
    if choice is None:
        return False
    return not any(atom & _gated_by((), one, grammar) for one in choice.alternatives)


def _split_overlapping_ways(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Cut the ways of a choice apart where they half overlap. Afterwards a pair of ways enters in the same states or in
    disjoint states, unless the pair is alike except for its gates.

    `_atoms` cuts the states of the overlapping ways into atoms. A pair of atoms is apart or the same. The atoms cover
    between them what the ways cover. A way holding a pair of atoms or more becomes a piece per atom inside it. A piece
    keeps the gate of its way. The piece gains the guards that say its atom. `_does_reach_nothing` names the pieces the
    cutting drops.

    This runs over the whole choice at once. A way's pieces take the place of that way. The pieces follow the order
    `_atoms` made. The first way admitting a state is still the way that admitted it before, except in an atom whose
    piece the cutting drops.

    The cutting skips the else of the choice.

    `_taking` narrows the take of a piece to the characters of its atom.
    """
    minted: dict[str, ir.Prod] = {}
    _accepted, _splits, entering = _leaf_tables(grammar)

    def told(name: str, production: ir.Prod) -> ir.Prod:
        ways = getattr(production.body, "alternatives")[:-1]
        held = [_entered_in(name, way, grammar, entering) for way in ways]
        # A way cuts where its space overlaps the space of another way and the pair differ. A pair alike but for their
        # gates does not cut.
        cutting = {
            at
            for at, one in enumerate(held)
            for other, two in enumerate(held)
            if at != other and (one & two) and one != two and not _does_the_same(ways[at], ways[other])
        }
        if not cutting:
            return production
        parts = _atoms([held[at] for at in sorted(cutting)])
        opened = []
        for way, space in zip(ways, held):
            inside = [atom for atom in parts if (atom & space) == atom and atom]
            if len(inside) < 2:
                opened.append(way)
                continue
            for atom in inside:
                if _does_reach_nothing(way, atom, grammar):
                    continue
                cut = _taking(way, atom, name, grammar, namer, minted)
                guards = _gate_for(atom, space, grammar)
                piece = dataclasses.replace(cut, gate=ir.GatePart(guards=(*cut.gate.guards, *guards)))
                # The list drops a piece equal to a way or a piece already in it.
                if piece not in opened:
                    opened.append(piece)
        return dataclasses.replace(
            production, body=ir.ChoiceState(alternatives=(*opened, getattr(production.body, "alternatives")[-1]))
        )

    written = {
        name: (
            told(name, production)
            if (choice := ir.AS_A_CHOICE(production.body)) is not None and len(choice.alternatives) > 1
            else production
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _unheld(node: ir.Node) -> ir.Node:
    """
    `node` with the scopes written around it stripped. `_unheld` drops the bound a `MaxWrapper` sets. `_unheld` drops
    the recovery a `RecoverWrapper` names.

    A `(token)` around a character is a character taken and a code given to it. A question about the items of a way has
    to see that character. A `(commit)` stays. The question asks about a commit's word on failing.
    """
    while (inner := _HELD_MATCH(node)) is not node:
        node = inner
    return node


_HELD_MATCH: ir.Question[ir.Node] = ir.Question(
    "the match a node holds inside its scope, or the node itself where the scope holds none",
    {
        # A window with nothing in it is a bound rather than a scope around a match. The node comes back unchanged.
        (ir.MaxWrapper, ir.RecoverWrapper, ir.TokenWrapper, ir.Wrapper): lambda node: (
            node.item if node.item is not None else node
        ),
        (
            *ir.CONSUMING,
            *ir.CONSUMES_NOTHING,
            *ir.RUNS,
            ir.AltTree,
            ir.AlternativeState,
            ir.BindTree,
            ir.ChoiceState,
            ir.CommitWrapper,
            ir.OptTree,
            ir.RefCall,
            ir.RepTree,
            ir.SeqTree,
        ): lambda node: node,
    },
)


def _flatten_called_alternations(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    A way of an outer choice may call an inner choice. Such a way becomes the inner choice's ways. `_flattened_once`
    keeps a call that passes an argument. It keeps a call to a choice that takes a parameter or can reach back. Those
    ways go into the site the call had.

    Take `a | P | c` with `P` written `d | e`. The choice `a | P | c` decides between more ways than it shows. Written
    out, `a | P | c` reads `a | d | e | c`. Those are the same ways, tried in the same order.

    `_flattened_calls` inlines an inner choice's ways into the outer `AltTree`. An `AltTree`'s parts are the ways a
    choice offers.
    """
    return _flattened_calls(grammar, ir.AS_AN_ALTERNATION)


def _flatten_called_sequences(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    An item of a way that calls a run of items becomes those items, at the site the call had.

    `_flattened_calls` flattens the calls of a `SeqTree`. The parts of that node are the items a way performs. A call
    last in the way stays. `mint-continuations` gives the continuation past a way's call a production of its own.
    """
    return _flattened_calls(grammar, ir.AS_A_SEQUENCE, does_flatten_last=False)


def _reached_within(grammar: dict[str, ir.Prod], as_kind: "_AsAGroup") -> dict[str, set[str]]:
    """
    `{name: {name}}`, the productions a name reaches directly inside a node `as_kind` gives, and what those reach in
    turn.

    This tells a flattening that would end from a flattening that would run forever. A body written out into a body that
    can reach back writes itself out again round after round.
    """
    direct: dict[str, set[str]] = {name: set() for name in grammar}
    for name, production in grammar.items():
        for node in _held(production.body):
            group = as_kind(node)
            for held in group.items if group is not None else ():
                call = ir.AS_A_CALL(held)
                if call is not None:
                    direct[name].add(call.name)

    def answer(name: str, known: Mapping[str, frozenset[str]]) -> frozenset[str]:
        return frozenset(direct[name]).union(*(known[callee] for callee in direct[name]))

    reaches = ir.fixed_point(
        "the nodes a production reaches inside a kind",
        {name: frozenset[str]() for name in grammar},
        answer,
        _inverted(direct),
    )
    return {name: set(reached) for name, reached in reaches.items()}


def _flattened_once(
    node: ir.Node,
    owner: str,
    grammar: dict[str, ir.Prod],
    as_kind: _AsAGroup,
    reaches: dict[str, set[str]],
    does_flatten_last: bool,
) -> ir.Node:
    """
    `_flattened_once` replaces a call in a group of kind `as_kind` with the items of the callee's group. A call with
    arguments stays. A callee with parameters keeps its call. A call to `owner` stays. A callee that can reach itself or
    `owner` keeps its call. The last item stays unless `does_flatten_last` holds.

    `as_kind` names the kind of group. A node of another kind flattens only its children.
    """
    node = ir.rebuilt(node, lambda child: _flattened_once(child, owner, grammar, as_kind, reaches, does_flatten_last))
    group = as_kind(node)
    if group is None:
        return node
    parts: list[ir.Node] = []
    for at, held in enumerate(group.items):
        call = ir.AS_A_CALL(held)
        called = grammar[call.name] if call is not None and not call.args else None
        inner = as_kind(called.body) if called is not None and not called.params else None
        # Writing out a callee that can reach itself unrolls its cycle a turn and writes the way back in. The cycle need
        # not run through the body being rewritten.
        does_unroll_a_cycle = call is not None and (
            call.name == owner or owner in reaches[call.name] or call.name in reaches[call.name]
        )
        is_kept = not does_flatten_last and at == len(group.items) - 1
        if inner is None or does_unroll_a_cycle or is_kept:
            parts.append(held)
        else:
            parts += list(inner.items)
    return dataclasses.replace(group, items=tuple(parts))


def _flattened_calls(
    grammar: dict[str, ir.Prod], as_kind: _AsAGroup, does_flatten_last: bool = True
) -> dict[str, ir.Prod]:
    """
    Write out a call as the items of the callee's group, and return the grammar. Such a call is an item of a group that
    `as_kind` gives. This runs to a fixpoint. A body written out can hold such a call itself. This keeps a call whose
    callee can reach itself or the body holding the call.

    This runs where the call is a whole part and takes no argument, and the callee takes no parameter. The last item
    stays unless `does_flatten_last` holds.
    """
    for _round in ir.rounds("the flattening of a call that is the entire kind node"):
        reaches = _reached_within(grammar, as_kind)
        settled = {
            name: dataclasses.replace(
                production, body=_flattened_once(production.body, name, grammar, as_kind, reaches, does_flatten_last)
            )
            for name, production in grammar.items()
        }
        if settled == grammar:
            return settled
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _with_inner_ways(node: ir.Node, rebuilt: Callable[[ir.Node], ir.Node]) -> ir.Node:
    """
    `node` rebuilt from the matches it holds. `rebuilt` says what a match becomes. This is the transform's mirror of
    `_inner_ways`.
    """
    return _WITH_INNER_WAYS(node, rebuilt)


# `_INNER_WAYS` says which matches a node holds, and this says how to put them back.
_WITH_INNER_WAYS: ir.Question[ir.Node] = ir.Question(
    "a node rebuilt from the matches it holds",
    {
        ir.AltTree: lambda node, rebuilt: dataclasses.replace(node, items=tuple(rebuilt(way) for way in node.items)),
        ir.SeqTree: lambda node, rebuilt: rebuilt(node),  # A sequence is a single way, and rebuilds whole.
        # A match that nothing makes holds none, and has no inside to give a production to.
        ir.FailTree: lambda node, rebuilt: node,
        ir.RecoverWrapper: lambda node, rebuilt: dataclasses.replace(
            node, item=rebuilt(node.item), recovery=rebuilt(node.recovery)
        ),
        # A binding puts a value in scope for a match, and the condition is that match.
        ir.BindTree: lambda node, rebuilt: dataclasses.replace(node, cond=rebuilt(node.cond)),
    },
)


def _lifting(
    as_lifted: Callable[[ir.Node], ir.Node | None],
) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    Give a node `as_lifted` selects inside a way a production of its own, and put a call to that production in place of
    the node.

    A choice is where the parse decides. A machine decides in a state. In the middle of a way, a choice has no state of
    its own. The state is the production, and the items a way holds run inside that production. A minted production is a
    state a call reaches and comes back from, and the call sits in the way as an item like any other. The minted
    production keeps the lifted node as its body. The step rebuilds the ways inside that node.
    """

    def transform(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
        minted = {}

        def item(node: ir.Node, owner: str) -> ir.Node:
            if ir.IS_A_LEAF_ITEM(node):
                return node
            if as_lifted(node) is not None:
                name = namer.fresh(owner)
                minted[name] = ir.Prod(
                    grammar[owner].number, name, (), _with_inner_ways(node, lambda way: rebuilt(way, owner))
                )
                return ir.RefCall(name=name, args=())
            return _with_inner_ways(node, lambda way: rebuilt(way, owner))

        def rebuilt(node: ir.Node, owner: str) -> ir.Node:
            sequence = ir.AS_A_SEQUENCE(node)
            if sequence is not None:
                return dataclasses.replace(sequence, items=tuple(item(part, owner) for part in sequence.items))
            return item(node, owner)

        def body(node: ir.Node, owner: str) -> ir.Node:
            if _DOES_OPEN_WAYS(node):
                return _with_inner_ways(node, lambda way: rebuilt(way, owner))
            return rebuilt(node, owner)

        lifted = {
            name: dataclasses.replace(production, body=body(production.body, name))
            for name, production in grammar.items()
        }
        return {**lifted, **minted}

    return transform


# A binding as its match and the write that follows. A node of another kind keeps its form.
_LOWERED_BIND = _reshaping(
    "a binding as its match and the write that follows",
    ir.BindTree,
    lambda node: ir.SeqTree(items=(node.cond, ir.SetVarAction(param=node.param, value=node.value))),
)


def _lower_bind(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a binding as the match the binding binds for, and the write that follows. `BindTree(cond, param, value)`
    becomes `SeqTree((cond, SetVarAction(param, value)))`.

    A binding is not a scope over the match. A binding matches `cond`, works the value out where that match ends, and
    writes the value. `c-indentation-indicator` holds bindings. The binding over `ns-dec-digit` sets `m` to `(atoi)`
    over the matched digit. That is a match and then an action. The interpreter says so twice over. A binding whose
    condition has matched does exactly what `SetVarAction` does. The binding undoes the write where what follows fails,
    and the condition can then try its next way.
    """

    def lowered(node: ir.Node) -> ir.Node:
        return _LOWERED_BIND(ir.rebuilt(node, lowered))

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _effects(
    node: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str] = frozenset()
) -> tuple[object, ...] | None:
    """
    `_effects` lists the actions, tokens and effectful calls of `node` in the order a parse performs them. A guard adds
    nothing. The answer is `None` for a match that takes a character. The answer is `None` for ways that disagree. A
    recursion and a kind the walk does not flatten give `None`.
    """
    return _EFFECTS(node, grammar, seen)


def _effects_in_order(
    items: Iterable[ir.Node], grammar: dict[str, ir.Prod], seen: frozenset[str]
) -> tuple[object, ...] | None:
    """The effects of `items` matched in order. The answer is `None` where an item has no answer."""
    found: tuple[object, ...] = ()
    for item in items:
        more = _effects(item, grammar, seen)
        if more is None:
            return None
        found += more
    return found


def _effects_shared(
    ways: Iterable[ir.Node], grammar: dict[str, ir.Prod], seen: frozenset[str]
) -> tuple[object, ...] | None:
    """The effects of a choice among `ways`. The answer is `None` unless the ways agree."""
    answers = {_effects(way, grammar, seen) for way in ways}
    return answers.pop() if len(answers) == 1 else None


def _effects_of_call(node: ir.RefCall, grammar: dict[str, ir.Prod], seen: frozenset[str]) -> tuple[object, ...] | None:
    """The effects of a call. A callee with effects gives the call itself. A recursion reached again gives `None`."""
    if node.name in seen:
        return None
    inner = _effects(grammar[node.name].body, grammar, seen | {node.name})
    if inner is None:
        return None
    return (node,) if inner else ()


def _effects_of_token(
    node: ir.TokenWrapper, grammar: dict[str, ir.Prod], seen: frozenset[str]
) -> tuple[object, ...] | None:
    """The effects of a token. The token is an effect around the effects of its item."""
    inner = _effects(node.item, grammar, seen)
    return None if inner is None else ((node.code, inner),)


# `_lifted_commit` asks this of the items in front of a commit.
_EFFECTS: ir.Question[tuple[object, ...] | None] = ir.Question(
    "the actions, tokens and calls a match performs, where the match takes no character",
    {
        ir.ACTIONS: lambda node, grammar, seen: (node,),
        ir.GUARDS: (),
        ir.EmptyTree: (),
        ir.CONSUMING: None,
        ir.SeqTree: lambda node, grammar, seen: _effects_in_order(node.items, grammar, seen),
        ir.AltTree: lambda node, grammar, seen: _effects_shared(node.items, grammar, seen),
        ir.TokenWrapper: _effects_of_token,
        ir.RefCall: _effects_of_call,
        (
            ir.AlternativeState,
            ir.BindTree,
            ir.CaseTree,
            ir.ChoiceState,
            ir.CommitWrapper,
            ir.FailTree,
            ir.MaxWrapper,
            ir.OptTree,
            ir.RecoverWrapper,
            ir.RepTree,
            ir.TrimStarTree,
            ir.Wrapper,
            *ir.RUNS,
        ): None,
    },
)


def _is_nullable(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> bool:
    """
    Whether `node` has a way that takes no character. `ways` answers that question of a production, and this function
    answers it of a node of another kind. This function knows nothing of a `ChoiceState`.

    This is a question rather than a rewrite. `_split` answers the same question in the second member of its pair. This
    `Question` raises on a kind the table does not name, and a run of the corpus reports a handled kind nothing reached.
    That report leaves out the kinds the table calls untested.
    """
    return _IS_NULLABLE(node, grammar, ways)


def _is_way_nullable(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> bool:
    """
    Whether a way consumes no character. That holds where the parts of the way consume none.
    """
    return _are_nullable(_parts_of_way(node), grammar, ways)


_IS_NULLABLE: ir.Question[bool] = ir.Question(
    "whether a match can take no character",
    {
        # A consume is in the first of these.
        ir.CONSUMING: False,
        ir.CONSUMES_NOTHING: True,
        ir.RefCall: lambda node, grammar, ways: ways[node.name][1],
        (ir.AlternativeState, ir.SeqTree): _is_way_nullable,
        ir.AltTree: lambda node, grammar, ways: any(_is_nullable(way, grammar, ways) for way in node.items),
        # A tree of none or more takes nothing by taking no turn. A tree of at least a turn takes nothing where the turn
        # does.
        (ir.OptTree, ir.StarTree): True,
        ir.PlusTree: lambda node, grammar, ways: _is_nullable(node.item, grammar, ways),
        ir.BindTree: lambda node, grammar, ways: _is_nullable(node.cond, grammar, ways),
        ir.WRAPPERS: lambda node, grammar, ways: node.item is None or _is_nullable(node.item, grammar, ways),
    },
    # The provisional actions, `InjectBeforeAction` and `MaxWrapper` fall past this question.
    untested=(
        ir.CommitProvisionalAction,
        ir.InjectBeforeAction,
        ir.MarkProvisionalAction,
        ir.MaxWrapper,
        ir.OpenProvisionalAction,
        ir.RetypeProvisionalAction,
    ),
    # A choice arrives as the `AltTree` the grammar wrote before the re-encode. The canonical form reads a way at a
    # time, and no family here says what a choice takes.
    unknown=(ir.ChoiceState,),
)


def _split(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    `(reads, empty)`. `reads` says whether `node` has a way that takes a character. `empty` says whether `node` has a
    way that takes none. `ways` gives the pair for a production. A call reads that pair and does not walk into its
    callee.
    """
    return _SPLIT(node, grammar, ways)


def _split_run(node: ir.StarTree | ir.PlusTree | ir.TrimStarTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A run's `(reads, empty)`. A run of none or more takes nothing by taking no turn. A run that must take a turn takes
    nothing only where its turn can.
    """
    reads, empty = _split(getattr(node, "item"), grammar, ways)
    if ir.AS_A_STAR(node) is not None:
        return reads, True
    if not empty:
        return True, False
    return reads, True


# Whether a scope is the error where its item cannot match. A commit is such a scope. A recovery is another.
_IS_THE_ERROR_WHERE_ITS_ITEM_FAILS: ir.Question[bool] = ir.Question(
    "whether a scope is the error where its item cannot match",
    {
        (ir.CommitWrapper, ir.RecoverWrapper): True,
        ir.kinds_other_than(ir.CommitWrapper, ir.RecoverWrapper): False,
    },
)


def _split_holder(
    node: ir.TokenWrapper | ir.Wrapper | ir.CommitWrapper | ir.MaxWrapper | ir.RecoverWrapper,
    grammar: dict[str, ir.Prod],
    ways: _Ways,
) -> _Split:
    """The `(reads, empty)` of the item a scope holds. A `(max)` with no item takes nothing."""
    if node.item is None:
        return False, True
    reads, empty = _split(node.item, grammar, ways)
    if _IS_THE_ERROR_WHERE_ITS_ITEM_FAILS(node) and reads and empty:
        raise ValueError(f"a {type(node).__name__.lower()} hides both an empty match and a consuming match")
    return reads, empty


def _split_parts(parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    The `(reads, empty)` of parts matched in order. The parts read where a part reads after parts that took nothing. A
    part that must take a character ends the walk, and the parts then have no empty way.
    """
    reads = False
    for part in parts:
        part_reads, part_empty = _split(part, grammar, ways)
        reads = reads or part_reads
        if not part_empty:
            return reads, False
    return reads, True


def _split_choice(ways_of: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    The `(reads, empty)` of a choice among `ways_of`. A choice reads where a way of it reads. A choice can take nothing
    where a way of it can.
    """
    halves = [_split(way, grammar, ways) for way in ways_of]
    return any(reads for reads, _empty in halves), any(empty for _reads, empty in halves)


def _split_counted(node: ir.RepTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A counted repetition's `(reads, empty)`. A positive literal count reads. A literal count that is not positive
    matches the empty input. A computed count has both ways.
    """
    _reads, empty = _split(node.item, grammar, ways)
    if empty:
        raise ValueError(f"a repetition of `{node.item}` takes a turn that may take nothing, and cannot be split")
    times = getattr(node.count, "value", None)
    if times is not None:
        is_positive = isinstance(times, int) and times > 0
        return is_positive, not is_positive
    return True, True


def _lifted_commit(body: ir.Node, grammar: dict[str, ir.Prod]) -> ir.Node:
    """
    `body` with a commit lifted off. The commit sits over `body` entire, or over the last item of a sequence. The items
    before that commit have `_effects`, and `_can_be_refused` answers no for them. A `body` without a commit to lift
    comes back unchanged.
    """
    committed = ir.AS_A_COMMIT(body)
    if committed is not None:
        return committed.item
    items = sequence.items if (sequence := ir.AS_A_SEQUENCE(body)) is not None else ()
    last = ir.AS_A_COMMIT(items[-1]) if items else None
    leading = ir.SeqTree(items=items[:-1])
    if last is None or _effects(leading, grammar) is None or _can_be_refused(leading, grammar):
        return body
    return ir.SeqTree(items=items[:-1] + (last.item,))


_SPLIT: ir.Question[_Split] = ir.Question(
    "whether a match has a way that takes a character, beside whether it has a way that takes none",
    {
        ir.CONSUMING: lambda node, grammar, ways: (True, False),
        ir.CONSUMES_NOTHING: lambda node, grammar, ways: (False, True),
        ir.RefCall: lambda node, grammar, ways: ways[node.name],
        ir.SeqTree: lambda node, grammar, ways: _split_parts(node.items, grammar, ways),
        ir.AlternativeState: lambda node, grammar, ways: _split_parts(_items_of_way(node), grammar, ways),
        ir.AltTree: lambda node, grammar, ways: _split_choice(node.items, grammar, ways),
        ir.ChoiceState: lambda node, grammar, ways: _split_choice(node.alternatives, grammar, ways),
        ir.CaseTree: lambda node, grammar, ways: _split_choice(
            [branch.item for branch in node.branches] + ([node.default] if node.default is not None else []),
            grammar,
            ways,
        ),
        ir.RUNS: _split_run,
        ir.RepTree: _split_counted,
        ir.BindTree: lambda node, grammar, ways: _split(node.cond, grammar, ways),
        ir.WRAPPERS: _split_holder,
        ir.VALUE_KINDS: lambda node, grammar, ways: (False, True),
        ir.OptTree: lambda node, grammar, ways: (_split(node.item, grammar, ways)[0], True),
    },
    # `check_dead_code._KEPT_THOUGH_DEAD` keeps the provisional actions, and no step builds one.
    untested=(
        ir.CommitProvisionalAction,
        ir.InjectBeforeAction,
        ir.MarkProvisionalAction,
        ir.OpenProvisionalAction,
        ir.RetypeProvisionalAction,
    ),
)


def _split_ways(grammar: dict[str, ir.Prod]) -> _Ways:
    """
    `{name: (reads, empty)}`. The entry says whether a production has a way that takes a character, and whether it has a
    way that takes none. The production's own commit comes off its body first.

    This is a least fixed point. A reference can reach back to its own production. A production takes nothing to match
    until some way of it says so. A recursion contributes only the halves that some other way has already given its
    production.
    """
    return ir.fixed_point(
        "whether a production reads, and whether it matches empty",
        {name: (False, False) for name in grammar},
        lambda name, ways: _split(_lifted_commit(grammar[name].body, grammar), grammar, ways),
        _callers(grammar),
    )


def _does_take_none_only_at_the_end(
    node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways, seen: frozenset[str] = frozenset()
) -> bool:
    """
    Whether `node` taking no character means the input has ended.

    `l-unparsed` says this outright. Its turns take any character there is, and a run is possessive. The place a turn
    cannot go is therefore past the last character. The rule writes that as a run of at least a turn, or the end. A walk
    for the nodes a production reaches with nothing taken stops where it reaches such a rule.

    This decides rather than recognises, and a `Question` makes the decision. The `Question` therefore raises on a kind
    the table does not name. Falling through to "this does not stop the walk" would make the answer a property of which
    shapes the question happened to name. A step that rewrote such a shape would then move the count while saying
    nothing about the grammar.
    """
    return _TAKES_NONE_ONLY_AT_THE_END(node, grammar, ways, seen)


def _has_empty_ways_that_end_the_input(
    node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways, seen: frozenset[str]
) -> bool:
    """A choice's answer. The choice has an empty way, and its empty ways mean the input has ended."""
    empty = [way for way in _ways_or_items(node) if _is_nullable(way, grammar, ways)]
    return bool(empty) and all(_does_take_none_only_at_the_end(way, grammar, ways, seen) for way in empty)


def _does_a_part_end_the_input(
    node: ir.AlternativeState | ir.SeqTree, grammar: dict[str, ir.Prod], ways: _Ways, seen: frozenset[str]
) -> bool:
    """
    Whether some part of a way says the input has ended. The answer reads the guards of the way's gate beside what the
    way performs.
    """
    return any(_does_take_none_only_at_the_end(part, grammar, ways, seen) for part in _parts_of_way(node))


_TAKES_NONE_ONLY_AT_THE_END: ir.Question[bool] = ir.Question(
    "whether a match that takes no character means the input has ended",
    {
        # The question that says so sits here, for any way that asks it. The question sits in a gate past
        # `hoist-guards-to-gates`. Before that step, the question sits among the actions.
        ir.EndOfStreamGuard: True,
        ir.RefCall: lambda node, grammar, ways, seen: node.name not in seen
        and _does_take_none_only_at_the_end(grammar[node.name].body, grammar, ways, seen | {node.name}),
        (ir.AltTree, ir.ChoiceState): _has_empty_ways_that_end_the_input,
        (ir.AlternativeState, ir.SeqTree): _does_a_part_end_the_input,
        # A reference, a choice and a sequence ask their parts. Anything else says nothing about where the input ends. A
        # match takes characters and an action leaves something behind. The other guards ask about something else.
        (
            *ir.ACTIONS,
            *(guard for guard in ir.GUARDS if guard is not ir.EndOfStreamGuard),
            *ir.CONSUMING,
            *ir.RUNS,
            *ir.WRAPPERS,
            *ir.VALUE_KINDS,
            ir.BindTree,
            ir.CaseTree,
            ir.EmptyTree,
            ir.OptTree,
            ir.RepTree,
        ): False,
    },
)


def _every_end_of_stream_sits_in_a_gate(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that an end of stream sits in a gate. A way's gate is where an `EndOfStreamGuard` belongs.

    Whether a character is there is a question. A question belongs where a way asks its questions. The grammar says
    outright where the input may end. The machine reaches the end as a way the grammar offered, rather than as a match
    it fell into. A guard among the actions would ask where the parse has already entered the way. A guard in a body
    offering no ways would ask where nothing chooses on the answer.

    The actions following the question are not this invariant's business. A way that reaches the end still has the
    wrapping up to do, and may hand that on to a continuation the way any other does. A way that takes no character and
    has no gate on the end is no end of stream at all. Such a way is an empty match. An empty match falls past the last
    character as anywhere else. The parts following an empty way tell that way from its neighbours, and the end says
    nothing.
    """

    def ends(node: ir.Node) -> int:
        return sum(ir.AS_AN_END_OF_STREAM(held) is not None for held in _held(node))

    faults = []
    for name, production in grammar.items():
        choice = ir.AS_A_CHOICE(production.body)
        if choice is None:
            faults += [f"{name}: an end of stream sits in a body that offers no ways"] * ends(production.body)
            continue
        for way in choice.alternatives:
            faults += [f"{name}: an end of stream sits among what a way performs"] * sum(
                ends(part) for part in _items_of_way(way)
            )
    return faults


_EVERY_END_OF_STREAM_SITS_IN_A_GATE = _Invariant(
    "every-end-of-stream-sits-in-a-gate", _every_end_of_stream_sits_in_a_gate
)


def _entered_unconsumed(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> set[str]:
    """
    The productions `node` can enter with nothing taken. That is its left corner, as names.

    The walk follows a guard like anything else. A guard tests where the parse is, and the node a guard reaches sits at
    that same position. A recovery is different. The parse enters the `recovery` of a `RecoverWrapper` where an
    abandoned parse stopped, rather than where the recovery's rule began. `WALKED_UNCONSUMED` lists the `(recover)`
    wrapper. A `(recover)` contributes what its item does and no more.

    The walk takes a way's gate along with its parts, and the questions the gate asks count here too. A gate admitting
    just the end of the input stops the walk at its `EndOfStreamGuard`. A parse with input left does not enter that way.
    This walk covers the nodes a parse arrives at and can go on from.

    A `DidConsumeSinceOpenGuard` stops the walk on the reverse ground. That guard asks for a character taken since its
    own open. A parse reaching the guard therefore took a character, and what sits behind the guard is past where the
    parse began. A run said as a recursion then reaches itself only after it has moved. The guard is the proof, and the
    turn it guards may be anything at all.
    """
    return _ENTERED_UNCONSUMED(node, grammar, ways)


def _entered_by_parts(parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways) -> set[str]:
    """
    The productions a run of parts enters with nothing taken. The walk takes the parts in turn. The walk stops after a
    part that must take a character. The walk stops after a part taking nothing only at the end of the input. The walk
    stops after a `DidConsumeSinceOpenGuard`.
    """
    reached: set[str] = set()
    for item in parts:
        reached |= _entered_unconsumed(item, grammar, ways)
        if ir.AS_A_SINCE_OPEN_GUARD(item) is not None:
            break
        # A part taking nothing only at the end consumes a character unless the input has ended. A part after it starts
        # at a later position.
        if not _is_nullable(item, grammar, ways) or _does_take_none_only_at_the_end(item, grammar, ways):
            break
    return reached


# The productions a kind enters at its own position. `ir.WALKED_UNCONSUMED` and the takes-nothing group both name the
# lookarounds and the exclusion. That group holds the lookarounds through `ir.GUARDS` and the exclusion through
# `ir.ACTIONS`. This binding walks into what a lookaround or the exclusion holds. The group below leaves out the kinds
# `ir.WALKED_UNCONSUMED` names. A question refuses a kind that its table names twice.
_ENTERED_UNCONSUMED: ir.Question[set[str]] = ir.Question(
    "the set of production names a match can enter with nothing taken",
    {
        ir.RefCall: lambda node, grammar, ways: {node.name},
        (ir.AltTree, ir.ChoiceState): lambda node, grammar, ways: {
            name for item in _ways_or_items(node) for name in _entered_unconsumed(item, grammar, ways)
        },
        ir.AlternativeState: lambda node, grammar, ways: _entered_by_parts(_parts_of_way(node), grammar, ways),
        ir.SeqTree: lambda node, grammar, ways: _entered_by_parts(node.items, grammar, ways),
        ir.BindTree: lambda node, grammar, ways: _entered_unconsumed(node.cond, grammar, ways),
        ir.WALKED_UNCONSUMED: lambda node, grammar, ways: (
            set() if node.item is None else _entered_unconsumed(node.item, grammar, ways)
        ),
        # A character question reaches no production at its own position. Nor does a zero-width action other than the
        # exclusion.
        tuple(
            kind for kind in (*ir.CONSUMING, *ir.CONSUMES_NOTHING) if kind not in ir.WALKED_UNCONSUMED
        ): lambda node, grammar, ways: set(),
    },
)


def _no_production_reaches_itself_unconsumed(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no production reaches itself with nothing taken. A parse arriving there cannot go on.

    A production reaching itself at the same position runs for ever.

    The stream and the recovery are mutually recursive by design under a resuming policy. `l-recover` is `l-unparsed`
    and then the stream again. A resumed document can therefore fail again without a second mechanism for it.
    `l-unparsed` keeps that pair from going round for ever. `l-unparsed` is a guard and a possessive run. The run's
    turns accept any character. The run takes nothing where the input has ended, and there `<end-of-stream>` answers
    rather than the stream continuing. `_does_take_none_only_at_the_end` states that behavior as a name. This function's
    walk therefore stops after `l-unparsed` rather than exempting the pair. A start state is a production like any other
    once a call reaches it.
    """
    ways = _split_ways(grammar)
    edges = {name: _entered_unconsumed(production.body, grammar, ways) for name, production in grammar.items()}
    held = _names_that_reach_themselves(edges)
    return [
        f"{name}: reaches itself with nothing taken, and a parse that arrives there cannot go on"
        for name in grammar
        if name in held
    ]


def _names_that_reach_themselves(edges: Mapping[str, Iterable[str]]) -> set[str]:
    """
    The names of `{name: the names it reaches in a step}` that reach themselves along a step or more. Those are the
    names in a circle of more than a single name, plus the names that are their own step.
    """
    return {name for circle in _circles(edges) for name in circle if len(circle) > 1 or name in edges.get(name, ())}


def _circles(edges: Mapping[str, Iterable[str]]) -> tuple[tuple[str, ...], ...]:
    """
    The circles of `{name: the names it reaches in a step}`. Those are the graph's strongly connected components, as
    tuples. A circle comes after the circles it reaches.

    Tarjan's algorithm finds the circles and their order in a single walk of the edges.
    """
    circles = []
    index, low, reached = {}, {}, 0
    stack, is_on_stack = [], set()
    for root in edges:
        if root in index:
            continue
        index[root] = low[root] = reached
        reached += 1
        stack.append(root)
        is_on_stack.add(root)
        walk = [(root, iter(sorted(edges[root])))]
        while walk:
            name, steps = walk[-1]
            for far in steps:
                if far not in index:
                    index[far] = low[far] = reached
                    reached += 1
                    stack.append(far)
                    is_on_stack.add(far)
                    walk.append((far, iter(sorted(edges.get(far, ())))))
                    break
                if far in is_on_stack:
                    low[name] = min(low[name], index[far])
            else:
                walk.pop()
                if walk:
                    above = walk[-1][0]
                    low[above] = min(low[above], low[name])
                if low[name] == index[name]:
                    circle = []
                    while True:
                        far = stack.pop()
                        is_on_stack.discard(far)
                        circle.append(far)
                        if far == name:
                            break
                    circles.append(tuple(circle))
    return tuple(circles)


# The module registers this checker under `no-production-reaches-itself-unconsumed`. `_names_that_reach_themselves`
# finds the loops among the circles `_circles` walks for. The checker above reports what that walk found.
_NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED = _Invariant(
    "no-production-reaches-itself-unconsumed", _no_production_reaches_itself_unconsumed
)


def _hold_established_indents(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Make an established indentation a level on the parse's stack rather than something a call hands back.

    This is a rewrite rather than a question. It reshapes the `ir.SeqTree` nodes in the bodies of the grammar's
    productions. A node of another kind keeps its form.

    A production may write the indentation `n`. The value travels back out through the calls that passed the parameter
    itself. That is a write whose readers sit a production away. This step inlines the chain until the write and its
    reader are in the same way. The write is the push. The way's pop later takes that push back.

    Inlining makes the pair local. The callee runs under the indentation of the caller. The argument is the parameter.
    The value the callee left in `n` stays in that same `n`.
    """

    # `_establishing` finds the productions that hand an indentation back. This step calls `_establishing` once, before
    # it reshapes any node.
    establishing = _establishing(grammar)

    def does_establish(node: ir.Node) -> bool:
        """Whether `node` passes `n` itself to a production that hands an indentation back."""
        call = ir.AS_A_CALL(node)
        return call is not None and call.name in establishing and _is_by_reference(grammar, call)

    def spliced(items: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
        """`items` with a call handing an indentation back replaced by what that production does."""
        while any(does_establish(item) for item in items):
            held = []
            for item in items:
                if not does_establish(item):
                    held.append(item)
                    continue
                held += ir.ITEMS_IN_A_SEQUENCE(grammar[getattr(item, "name")].body)
            items = tuple(held)
        return items

    def bounded(items: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
        """`items` with the indentation's write turned into the push that its way takes back."""
        for position, item in enumerate(items):
            written = ir.AS_A_SET_VAR(item)
            if written is not None and written.param == "n":
                rest, pair = bounded(items[position + 1 :]), namer.pair()
                return (
                    items[:position]
                    + (ir.PushIndentAction(level=written.value, pair=pair),)
                    + rest
                    + (ir.PopIndentAction(level=None, pair=pair),)
                )
        return items

    def held(node: ir.Node) -> ir.Node:
        return _ITEMS_RETOLD(ir.rebuilt(node, held), lambda items: bounded(spliced(items)))

    return {name: dataclasses.replace(production, body=held(production.body)) for name, production in grammar.items()}


# A sequence with its items rewritten by `told`. A node of another kind keeps its form.
_ITEMS_RETOLD = _reshaping(
    "a sequence with its items rewritten", ir.SeqTree, lambda node, told: ir.SeqTree(items=told(node.items))
)


def _no_production_writes_the_indentation(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a production does not write the indentation.
    """
    return [
        f"{name}: writes the indentation for its caller to read back"
        for name, production in grammar.items()
        for node in _held(production.body)
        if (written := ir.AS_A_SET_VAR(node)) is not None and written.param == "n"
    ]


_NO_PRODUCTION_WRITES_THE_INDENTATION = _Invariant(
    "no-production-writes-the-indentation", _no_production_writes_the_indentation
)


def _push_indents(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say where the indentation changes. The parse then holds the indentation on the stack, rather than a call passing the
    value.

    A call may run against an indentation other than the level in force. Such a call enters under that indentation and
    gives it back on return. The push therefore goes before the call, and the pop goes behind it. The pair sits inside a
    single way of a single production. The production knows the level there. A call handing the parameter itself enters
    under the level already pushed. Such a call pushes nothing. `hold-established-indents` has already made a push of an
    indentation that a call would have established.

    The parameter stays beside the stack.
    """

    def pushed(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, pushed)
        level = _pushed_level(grammar, node)
        if level is None:
            return node
        pair = namer.pair()
        return ir.SeqTree(
            items=(ir.PushIndentAction(level=level, pair=pair), node, ir.PopIndentAction(level=None, pair=pair))
        )

    return {name: dataclasses.replace(production, body=pushed(production.body)) for name, production in grammar.items()}


def _establishing(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    `_establishing` returns productions handing indentation back to their caller. Those are the productions writing
    indentation, along with the productions calling those with the parameter itself. A call passing the parameter itself
    takes the write out. The returned set is a least fixpoint. A production at the head of a call chain establishes
    through that set.
    """
    writing = {
        name
        for name, production in grammar.items()
        if any(
            (written := ir.AS_A_SET_VAR(node)) is not None and written.param == "n" for node in _held(production.body)
        )
    }
    by_reference: dict[str, set[str]] = {name: set() for name in grammar}
    for name, production in grammar.items():
        for node in _held(production.body):
            if (call := ir.AS_A_CALL(node)) is not None and call.name in grammar and _is_by_reference(grammar, call):
                by_reference[name].add(call.name)
    establishes = ir.fixed_point(
        "the productions that establish n by writing it or by calling a production that does",
        {name: name in writing for name in grammar},
        lambda name, known: name in writing or any(known[callee] for callee in by_reference[name]),
        _inverted(by_reference),
    )
    return {name for name, does_establish in establishes.items() if does_establish}


def _is_by_reference(grammar: dict[str, ir.Prod], node: ir.RefCall) -> bool:
    """Whether the call `node` hands the indentation itself. A callee's write reaches the caller through such a call."""
    callee = grammar.get(node.name)
    if callee is None or "n" not in callee.params:
        return False
    position = callee.params.index("n")
    if position >= len(node.args):
        return False
    named = ir.AS_A_PARAMETER(node.args[position])
    return named is not None and named.name == "n"


def _is_reading(grammar: dict[str, ir.Prod], production: ir.Prod, param: str) -> bool:
    """
    Whether `production` reads `param`. A `BindTree` write says where a value begins and a pass moves it. Neither is a
    read.

    A parameter passed bare to the callee's parameter of the same name is by reference. The `ParamValue` in that
    argument is therefore the pass, and not a use of the value here. A `ParamValue` inside an argument that works
    something out is a read like any other.
    """
    passed = set()
    for node in _held(production.body):
        call = ir.AS_A_CALL(node)
        if call is None:
            continue
        callee = grammar.get(call.name)
        declared = () if callee is None else callee.params
        for position in range(min(len(call.args), len(declared))):
            named = ir.AS_A_PARAMETER(call.args[position])
            if declared[position] == param and named is not None and named.name == param:
                passed.add(id(named))
    return any(
        (named := ir.AS_A_PARAMETER(node)) is not None and named.name == param and id(node) not in passed
        for node in _held(production.body)
    )


def _is_bounded(production: ir.Prod, param: str) -> bool:
    """Whether `production` says where `param` stops applying. Its way ends on the clear."""
    items = ir.ITEMS_IN_A_SEQUENCE(production.body)
    return bool(items) and (cleared := ir.AS_A_CLEAR_VAR(items[-1])) is not None and cleared.param == param


def _clear_reads(param: str) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    The transform gives `param` an end. The consuming production clears the value on return, once its calls have
    returned.

    The reader clears the value, and the writer leaves it in place.
    """

    def transform(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
        def bounded(production: ir.Prod) -> ir.Prod:
            if not _is_reading(grammar, production, param) or _is_bounded(production, param):
                return production
            items = ir.ITEMS_IN_A_SEQUENCE(production.body)
            return dataclasses.replace(production, body=ir.SeqTree(items=items + (ir.ClearVarAction(param=param),)))

        return {name: bounded(production) for name, production in grammar.items()}

    return transform


def _a_read_is_bounded(param: str) -> _Invariant:
    """
    The invariant is that a production reading `param` says where the value stops.

    `interpreter._evaluated_global` counts a read where the global's stack and its slot differ. `check_normalize._check`
    refuses the count.
    """

    def test(grammar: dict[str, ir.Prod]) -> list[str]:
        return [
            f"{name}: reads `{param}` and does not say where the value stops"
            for name, production in grammar.items()
            if _is_reading(grammar, production, param) and not _is_bounded(production, param)
        ]

    return _Invariant(f"a-read-of-{param}-is-bounded", test)


# `at-the-start` holds no steps of its own.
#
# The grammar arrives already holding `_EVERY_CONDITIONAL_WAY_IS_GATED`. The phase claims it with a step that has no
# transform. The grammar holds no conditional way until `build-alternatives` says a way by its parts. That step leaves a
# way with an empty gate.
_AT_THE_START = _Phase("at-the-start", establishes=_EVERY_CONDITIONAL_WAY_IS_GATED)

# The specialization takes `_NO_I_T_PARAMETERS` to none. The grammar then declares, passes and reads neither the
# chomping nor the block scalar's indentation mode. The chomping and the indentation mode stay data-dependent until the
# setters become switches. The specialization cannot reach them before then.
_SPECIALIZE = _Phase(
    "specialize",
    settles=_NO_I_T_PARAMETERS,
    steps=(
        # The step writes a setter of a finite parameter as a `(case)` on that parameter. A setter is a choice of
        # bindings. The bindings write literals to a single finite parameter. `_finite_setter` finds a setter from the
        # body of a production. A branch of the case holds the condition its binding matched. In a production that reads
        # the parameter without declaring it, the tail from the first read becomes a choice over the parameter's values.
        # The ways of that choice hold the literal in place of the read, in the order the setter tried the values.
        _Step("lift-setters", _lift_setters, reduces=_FINITE_PARAMETERS_ARE_ALWAYS_LITERAL),
        # The step makes a copy of a production per combination of context values the walk from the root reaches. A copy
        # evaluates its `(case)` and `(flip)` nodes down to the values that copy fixes. The copy drops the finite
        # parameters from its signature. The walk finds a callee's values in the arguments of the calls to it. A value a
        # copy fixes is a literal in its body. The parse holds no such value.
        _Step(
            "monomorphize",
            _monomorphize,
            settles=(
                _absent("no-context-case", ir.CaseTree, ir.FlipValue),
                _FINITE_PARAMETERS_ARE_ALWAYS_LITERAL,
                _NO_I_T_PARAMETERS,
                # The count reaches none once the specialization has run, and no earlier.
                _EVERY_OPTION_IS_REACHABLE,
            ),
        ),
    ),
)

# The pruning takes `no-fails` to none. A grammar a later step reads holds no `ir.FailTree`.
_NO_FAILS = _absent("no-fails", ir.FailTree)

# The phase takes `_NO_FAILS` to none. The grammar then holds no match that no input makes. A `<fail>` is a branch of a
# live `(case)` until the specialization has run. Such a branch differs from the production it belongs to. The phase
# therefore follows the specialization.
_PRUNE_FAILURES = _Phase(
    "prune-failures",
    settles=_NO_FAILS,
    steps=(
        # The step takes out the ways that no input matches. Specialization leaves such a way where a `(case)` names
        # `<fail>` for a value. A body that becomes `<fail>` makes a call of it fail too. The rounds repeat until no
        # body changes. A way that no input takes changes no parse.
        _Step("prune-failures", _prune_failures, settles=_NO_FAILS),
    ),
)

# The phase takes `_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET` to none. A question about a character is a `CharSet`. A
# set the context picks denotes nothing until the specialization has bound the context. The phase therefore follows the
# specialization. A distribution step runs first and leaves a set on both sides of a subtraction.
_CHARACTER_SETS = _Phase(
    "character-sets",
    settles=_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET,
    steps=(
        # The step takes a difference into the ways of the item it subtracts from. `(A | B) - m` becomes `(A - m) | B`
        # where `A` takes a single character and `B` takes more, and the ways keep their order. A subtracted set takes a
        # single character. A way that takes more than a single character therefore keeps its whole language. The step
        # raises on a way that may take a single character and is not a set.
        _Step("distribute-differences", _distribute_differences, settles=_EVERY_DIFFERENCE_IS_BETWEEN_CHARACTER_SETS),
        # A second step writes a character set as a single `CharSet` of sorted disjoint intervals. A union, a range and
        # a difference of sets become that form. The `CharSet` holds the characters the form it replaces holds. A
        # production that is a character set keeps its name. Its callers still call it.
        _Step(
            "lower-char-sets",
            _lower_char_sets,
            settles=(_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET, _NO_DIFF_NODES, _EVERY_PEEK_IS_A_CHARACTER_SET),
        ),
    ),
)

# The phase takes `_NO_F_PARAMETER` to none. The block scalar's leading-empty floor is a single value for the parse,
# rather than a value a call passes. The clear gives that value an end first.
_DROP_F = _Phase(
    "drop-f",
    settles=_NO_F_PARAMETER,
    steps=(
        # The step ends the value of `f` where a production that reads it returns. The step appends a `(clear)` of `f`
        # to such a body, unless the body already ends on that clear. A call passing `f` as itself does not read it.
        _Step("clear-f", _clear_reads("f"), settles=_a_read_is_bounded("f")),
        # The step takes the parameter `f` off the productions and the calls. A read of `f` becomes a read of the global
        # `f`. The `(clear)` from `clear-f` ends the global's value. `interpreter._evaluated_global` raises on a read
        # past that end with an empty stack. `interpreter._evaluated_global` counts a read past that end with an outer
        # value on the stack. `check_normalize._check` refuses the count.
        _Step("read-global-f", _read_off("f", ir.GlobalValue(name="f")), settles=_NO_F_PARAMETER),
    ),
)

# The phase takes `_NO_M_PARAMETER` to none. The detected indent is a single value for the parse. A clear and a drop
# cover the phase.
_DROP_M = _Phase(
    "drop-m",
    settles=_NO_M_PARAMETER,
    steps=(
        # The step ends the value of `m` the way `clear-f` ends the value of `f`.
        _Step("clear-m", _clear_reads("m"), settles=_a_read_is_bounded("m")),
        # The step takes `m` off the way `read-global-f` takes `f` off. A read of `m` becomes a read of the global `m`.
        _Step("read-global-m", _read_off("m", ir.GlobalValue(name="m")), settles=_NO_M_PARAMETER),
    ),
)

# The phase takes `_NO_N_PARAMETER` to none. The indentation is on the parse's stack. A call does not pass it. The phase
# puts the pushes of the indentation in first, and the parameter stays beside those pushes. Dropping the parameter
# leaves the stack to answer.
_DROP_N = _Phase(
    "drop-n",
    settles=_NO_N_PARAMETER,
    steps=(
        # The step inlines a call that hands the indentation back to its caller. Such a call passes `n` as itself to a
        # production that writes `n`. The inlining goes on until the write of `n` sits in the way that reads it. The
        # step then writes that write as a push of the indentation. The way ends on the matching pop.
        _Step("hold-established-indents", _hold_established_indents, settles=_NO_PRODUCTION_WRITES_THE_INDENTATION),
        # The step puts a push of the indentation before a call that runs under an indentation of its own. The pop goes
        # after the call. The push and the pop sit in the same way. A call passing `n` as itself pushes nothing.
        _Step("push-indents", _push_indents, settles=_EVERY_INDENTATION_CHANGE_IS_PUSHED),
        # The step takes `n` off the way `read-global-f` takes `f` off. A read of `n` becomes a read of the indentation
        # on top of the stack.
        _Step("read-indents", _read_off("n", ir.IndentValue()), settles=_NO_N_PARAMETER),
    ),
)

# The phase takes `_NO_OPT_NODES` and `_NO_STAR_OR_PLUS_NODES` to none. A node then hides no match that may take none.
# An empty match is a way beside the way that reads. A repetition is a consume of a class, or the ways `lower-runs`
# makes.
_LOWER_REPETITIONS = _Phase(
    "lower-repetitions",
    settles=(_NO_OPT_NODES, _NO_STAR_OR_PLUS_NODES),
    steps=(
        # The step writes an optional as a choice between its item and `<empty>`. The rewrite of an optional reads only
        # that optional. The choice holds the item ahead of `<empty>`. `interpreter._try_alternation` tries the items of
        # a choice in the order held. `invariant_faults` faults the step if the grammar it gets holds no optional.
        _Step("lower-optionals", _lower_optionals, settles=_NO_OPT_NODES),
        # The claim `holds-once-optionals-are-ways` comes behind this step and starts the check
        # `no-production-reaches-itself-unconsumed`. The check cannot run any earlier. Before this step the grammar
        # still holds optionals, and the check does not handle an optional.
        _Step("holds-once-optionals-are-ways", establishes=_NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED),
        # The step writes a run over a character class as a consume of that class. `x+` becomes a guard that the class
        # is in front. The span follows the guard. `x*` adds a way for a parse with no character of the class in front.
        # `interpreter._try_span` takes the longest run.
        _Step(
            "span-consumes",
            _span_consumes,
            settles=(_EVERY_CHARACTER_RUN_IS_A_SPAN, _EVERY_EXCLUSION_IS_BOUNDED),
            reduces=_NO_STAR_OR_PLUS_NODES,
            # The step makes the run and its question side by side. A later step may put nothing between the pair. This
            # site states that guarantee.
            establishes=_EVERY_SPAN_QUESTION_FOLLOWS_ITS_RUN,
        ),
        # The step writes a repetition as ways. A turn is a production of its own. A recursion takes the turns behind
        # it. A settled region holds the turns past the first. A failure past the region gives the whole run up. The
        # ways therefore take the longest match, as the run did.
        _Step("lower-runs", _lower_runs, settles=_NO_STAR_OR_PLUS_NODES),
    ),
)

# The phase takes `_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS` to none. `interpreter._is_forbidden_here` matches an
# exclusion's pattern through `_try_probe`, and the probe rewinds the match. An exclusion forbids a pattern. The step
# gives that pattern a copy that matches and asks. The phase runs before `lower-scopes` lowers a `(token)`. The copy
# drops the `(token)` nodes.
_FORBIDDEN_PROBES = _Phase(
    "forbidden-probes",
    settles=_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS,
    steps=(
        # An exclusion forbids a pattern. The step gives that pattern a copy of its own. The copy drops the `(token)`
        # nodes and matches the same text. The productions the pattern reaches get copies too. The calls inside the
        # copies name the copies. The originals keep their tokens for the ways outside the pattern.
        _Step("mint-forbidden-probes", _mint_forbidden_probes, settles=_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS),
    ),
)

# The phase takes the scope nodes to none. `_NO_WRAP_NODES`, `_NO_MAX_NODES` and `_NO_COMMIT_NODES` go.
# `_NO_TOKEN_NODES` and `_NO_EXCLUDE_AT_NODES` go with them. A scope is then the pair of writes that bound it, and no
# node holds what a scope covers. A step takes a single kind. The wrap and the window write both halves. The commit and
# the token write both halves. The halves of a window, a commit and a token name the pair they belong to. The wrap's
# markers name none. The exclusion names no pair. The set is a single value for the parse. The exclusion's closing write
# names the set in force around the way. It takes no open back.
_LOWER_SCOPES = _Phase(
    "lower-scopes",
    settles=(_NO_WRAP_NODES, _NO_MAX_NODES, _NO_COMMIT_NODES, _NO_TOKEN_NODES, _NO_EXCLUDE_AT_NODES),
    steps=(
        # The step writes `Wrap(begin, end, x)` as `Emit(begin) x Emit(end)`. `interpreter._try_marker` emits a marker.
        # The markers sit in the same way.
        _Step("lower-wraps", _lower_wraps, settles=_NO_WRAP_NODES),
        # The step writes `Max(limit, message, x)` as `OpenWindow(limit, message) x CloseWindow()`.
        # `interpreter._try_open_window` applies the outermost window only. The step raises on a `(max)` with nothing in
        # it.
        _Step("lower-windows", _lower_windows, settles=_NO_MAX_NODES),
        # The step writes `Commit(message, x)` as `PushMessage(message) x PopMessage()`.
        # `interpreter._try_open_committed` and `_try_close_committed` run the pair.
        _Step("lower-commits", _lower_commits, settles=_NO_COMMIT_NODES),
        # The step writes `Token(code, x)` as `PushCode(code) x PopCode()`. `interpreter._try_push_code` puts the code
        # on the stack. `_try_pop_code` takes back the code the push displaced.
        _Step("lower-tokens", _lower_tokens, settles=_NO_TOKEN_NODES),
        # The step writes an `(exclude)` as a pair of `SetForbidden` actions. The first sets the forbidden pattern where
        # the exclusion sat. The second sets the pattern in force where the way ends. A production holding an exclusion
        # and entered under a pair of forbidden sets gets a copy per set. The forbidden set is a single value the parse
        # holds.
        _Step("lower-exclusions", _lower_exclusions, settles=_NO_EXCLUDE_AT_NODES),
    ),
)

# The phase takes `_EVERY_SUB_ITEM_IS_ONE_STEP` to none. An item in a way is a single thing the machine does there. A
# shape holding a match inside therefore becomes a production of its own. The lifts settle the kinds they name. The pair
# lower this count together, and `lower-bind` takes the count to none.
_ONE_STEP_PER_ITEM = _Phase(
    "one-step-per-item",
    settles=_EVERY_SUB_ITEM_IS_ONE_STEP,
    steps=(
        # The step gives a choice inside a way a production of its own. The way calls that production where the choice
        # sat. A call runs the choice's ways in the order they had. The step mints a name and no value.
        _Step(
            "lift-choices",
            _lifting(ir.AS_AN_ALTERNATION),
            settles=_EVERY_CHOICE_IS_A_PRODUCTION,
            reduces=_EVERY_SUB_ITEM_IS_ONE_STEP,
        ),
        # The step gives a `(recover)` inside a way a production of its own, the way `lift-choices` lifts a choice.
        _Step(
            "lift-recoveries",
            _lifting(ir.AS_A_RECOVERY),
            settles=_EVERY_RECOVERY_IS_A_PRODUCTION,
            reduces=_EVERY_SUB_ITEM_IS_ONE_STEP,
        ),
        # The step writes `BindTree(cond, param, value)` as `SeqTree((cond, SetVarAction(param, value)))`. A binding
        # whose condition matched writes the value as `SetVarAction` does. The value goes to the variable the binding
        # named.
        _Step("lower-bind", _lower_bind, settles=(_EVERY_SUB_ITEM_IS_ONE_STEP, _NO_BIND_NODES)),
    ),
)

# The phase takes `_NO_CHOICE_OF_CHOICES` to none. A way of a choice is then no call to a choice. A gate can then go on
# the way itself rather than on a call below it. The phase runs before the split into a call and a continuation. A
# choice written out here is a choice the phases behind this see whole. The same phase flattens a run of items nested
# inside a way's own items. That lowers `no-sequence-of-sequences`, and `build-alternatives` takes it to none.
_FLATTEN_CALLS = _Phase(
    "flatten-calls",
    settles=_NO_CHOICE_OF_CHOICES,
    steps=(
        # The step writes a way that calls a choice as the ways of that choice. `a | P | c` with `P` as `d | e` becomes
        # `a | d | e | c`. The ways keep their order.
        _Step("flatten-called-alternations", _flatten_called_alternations, settles=_NO_CHOICE_OF_CHOICES),
        # The step writes an item that calls a run of items as those items. A call passing an argument stays a call. So
        # does a call to a callee taking a parameter or reaching back. A call last in the way stays a call.
        _Step("flatten-called-sequences", _flatten_called_sequences, reduces=_NO_SEQUENCE_OF_SEQUENCES),
    ),
)

# The phase takes `_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION` to none. A way does its actions, hands control to a
# single production, and says where the path continues. The parts past the call continue.
_MINT_CONTINUATIONS = _Phase(
    "mint-continuations",
    settles=_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION,
    steps=(
        # The step gives the parts past a way's first call a production of their own, unless those parts are empty or a
        # single call. The way ends on that call and a call of the new production. A push and its pop may then sit in
        # different productions. `interpreter._popped` takes the entry off the top of the stack the parse holds.
        _Step("mint-continuations", _mint_continuations, settles=_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION),
    ),
)

# The phase takes `_EVERY_BODY_IS_A_CHOICE_OR_A_SET` to none. A body then speaks the machine's words. A body is a set of
# characters, or the ordered list of alternatives the parse takes one of. `lower-repetitions` already said a repetition
# as the ways it holds. `no-sequence-of-sequences` reaches none with the bodies built here.
_BUILD_ALTERNATIVES = _Phase(
    "build-alternatives",
    settles=_EVERY_BODY_IS_A_CHOICE_OR_A_SET,
    steps=(
        # The step writes a body as a set or as a `ChoiceState` of alternatives. A way of the body becomes an
        # alternative. The alternative names the way's actions, call and continuation in fields of their own.
        # `interpreter._try_way` runs an alternative as the sequence it replaces. The gates start empty.
        _Step(
            "build-alternatives",
            _build_alternatives,
            settles=(
                _EVERY_BODY_IS_A_CHOICE_OR_A_SET,
                _NO_SEQUENCE_OF_SEQUENCES,
                _NO_EMPTY_NODES,
                _EVERY_CONSUME_IS_PROTECTED_BY_A_GATE,
            ),
            lapses={
                "every-conditional-way-is-gated": "this step is where ways to gate first exist. a body becomes the "
                "ordered list of alternatives the parse chooses among, and it is the first thing anything has to "
                "tell apart. none of the ways asks a question. the debt starts here. a step moving a question "
                "into a gate pays it down."
            },
            # The step builds a way with a gate. The step decides the shape of that gate.
            establishes=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE,
        ),
    ),
)

# The phase takes `_EVERY_COUNTED_RUN_CAN_BE_FOLLOWED_BY_ITS_CHARACTER` to none.
_REFINE_COUNTED_RUNS = _Phase(
    "refine-counted-runs",
    settles=_EVERY_COUNTED_RUN_CAN_BE_FOLLOWED_BY_ITS_CHARACTER,
    steps=(
        # The step settles `_EVERY_COUNTED_RUN_CAN_BE_FOLLOWED_BY_ITS_CHARACTER`. A call site whose follower takes none
        # of a run's characters calls a minted copy. The copy takes a maximal run and asks that its length is the count.
        # A site keeps the original where `_is_refinable` does not hold. The rewrite is local and mechanical. The step
        # redirects a call site and mints a copy per callee and set of runs. The tables that decide where the rewrite
        # applies read the whole grammar. The length the guards read is the count of characters the last consume took.
        # The interpreter keeps that count in a field of `Emitter`.
        _Step(
            "refine-counted-runs",
            _refine_counted_runs,
            settles=_EVERY_COUNTED_RUN_CAN_BE_FOLLOWED_BY_ITS_CHARACTER,
            lapses={
                "every-conditional-way-is-gated": "the copy a site calls holds the ungated ways of its original. "
                "`flatten-ungated-call-trees` settles those ways with the rest of the debt."
            },
        ),
    ),
)

# The phase takes `_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL` to none. A way with no gate is a way still awaiting one. A
# call past the way's actions puts the callee's guards out of reach of where the parse enters the way. A call with a
# state of its own puts the callee's guards and the call's entry at the same position.
_CALL_STATES = _Phase(
    "call-states",
    settles=_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL,
    steps=(
        # The step cuts a way at a call that comes past the way's actions. A new state makes that call first. The way
        # ends on a tail call of the new state. An `AlternativeState` with no `first` makes a tail call. A tail call
        # pushes nothing.
        _Step("mint-call-states", _mint_call_states, settles=_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL),
    ),
)

# The phase takes `_NO_GUARD_COMES_PAST_AN_ACTION` and `_EVERY_GUARD_IS_IN_A_GATE` to none. A question a way asks sits
# in the way's gate, and what the way performs comes behind that gate. The grammar says outright whether a way's part is
# a question or an action. The parts run in order. The phase cuts a way that would ask a guard past an action. The
# guards the way holds move into its gate. The questions about the character in front combine into a single set.
#
# The phase does not take those gates out to the ways that call them. The gate stays inside the way and covers what the
# way performs, not what the caller performs before the call.
_GUARDS_INTO_GATES = _Phase(
    "guards-into-gates",
    settles=(_NO_GUARD_COMES_PAST_AN_ACTION, _EVERY_GUARD_IS_IN_A_GATE),
    steps=(
        # The step cuts a way at its first guard past an action. A new state holds the guard and the parts behind it.
        # The way ends on a tail call of that state. The parts run in the order they had. The guard asks at the position
        # it asked at before.
        _Step("mint-guard-states", _mint_guard_states, settles=_NO_GUARD_COMES_PAST_AN_ACTION),
        # The step moves the guards at the head of a way's actions into the way's gate. A guard takes no width. A guard
        # at the head of the actions and the gate therefore ask at the same position.
        _Step(
            "hoist-guards-to-gates",
            _hoist_guards_to_gates,
            settles=(_EVERY_GUARD_IS_IN_A_GATE, _EVERY_END_OF_STREAM_SITS_IN_A_GATE),
            reduces=_EVERY_CONDITIONAL_WAY_IS_GATED,
            lapses={
                "every-gate-looks-ahead-at-most-once": "a guard moved into a gate sits beside the characters that gate "
                "already asked. the gate then looks ahead twice at the character in front. merging the pair into a "
                "single set makes it a single question again."
            },
        ),
        # The step writes the peeks of a gate as a single peek. A pair of `LookGuard`s admits the intersection of their
        # sets. A `NegLookGuard` takes its set away from the set the gate admits. `_LOOKS_AHEAD` counts an
        # `EndOfStreamGuard` as a peek. The step raises on a gate holding an `EndOfStreamGuard` beside another peek.
        _Step("merge-gate-peeks", _merge_gate_peeks, settles=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    ),
)

# The phase takes `_NO_CHAR_SET_IS_AN_ITEM` to none. A gate asks for a character where a caller can see the gate. A
# `ConsumeCharAction` then takes the character on the gate's word. The phase makes the gates that ask for a consumed
# character. `_GUARDS_INTO_GATES` gates the other guards earlier. A hoist moves a question that already exists. This
# phase runs before hoisting could produce such a question. A pair of steps does the work. The phase cuts a way that
# takes a character on the strength of an earlier one. The phase repeats the cut until a way takes a single character. A
# way then takes at most once. The question sits where the parse enters the way.
_CONSUMES_BEHIND_GATES = _Phase(
    "consumes-behind-gates",
    settles=_NO_CHAR_SET_IS_AN_ITEM,
    steps=(
        # The step cuts a way at its second take. A new state holds the second take and the parts behind it. The way
        # ends on a tail call of that state. The rounds repeat until a way holds a single take.
        _Step("mint-consume-states", _mint_consume_states, settles=_EVERY_WAY_TAKES_AT_MOST_ONCE),
        # The step writes a `CharSet` among a way's actions as a `LookGuard` in the gate and a `ConsumeCharAction` in
        # its place. `GUARD_CROSSES_ACTION` decides whether the guard may come in front of the actions before it. A call
        # to a production that is a set becomes that set first. A way that already takes instead calls a minted way that
        # asks for the set and takes it.
        _Step(
            "split-consumes-into-gates",
            _split_consumes_into_gates,
            settles=_NO_CHAR_SET_IS_AN_ITEM,
            reduces=_EVERY_CONDITIONAL_WAY_IS_GATED,
        ),
    ),
)

# The phase is for `_EVERY_CONDITIONAL_WAY_IS_GATED`. A question on the input decides whether the parse enters a way.
# The phase moves questions that already exist. A callee gives up the guards its ways share. The guards land in the gate
# of the caller's way that calls the callee. The callee still enters under what it gave up. `_asked_where_entered` says
# so. The move keeps a take protected where the moved guard protected that take. `_every_consume_is_protected_by_a_gate`
# counts the guards asked where the parse enters the way. The step that reaches down for gates settles the invariant.
# `unsettled_invariants` reports what remains broken at the end.
#
# The phase opens on a claim rather than a transform, and the claim names a pair of invariants. The first invariant
# compares the characters a way consumes with the characters that admit the way. The first invariant skips a way whose
# first consume belongs to a callee. The second says a path into a production of leaf ways reaches one of them. The
# grammar arrives holding both, before any step moves a guard.
#
# A pair of steps does the moving, and they move in opposite directions. A guard climbs to the callers that enter it. A
# way nothing has gated reaches down for the gates of the productions it runs. The step reaching down takes a whole tree
# at once, and no step here writes a callee's ways out a level at a time.
_GATE_THE_WAYS = _Phase(
    "gate-the-ways",
    establishes=(_ACCEPTED_AND_GATED_CHARSETS_ARE_EQUAL, _EVERY_PATH_REACHES_A_LEAF_WAY),
    settles=_EVERY_CONDITIONAL_WAY_IS_GATED,
    steps=(
        # The step climbing up moves the guards the ways of a callee share into the gate of a way that calls it.
        # `GUARD_CROSSES_ACTION` decides whether a guard may come in front of the caller's actions. The caller then
        # calls a copy of the callee without the moved guards. A caller that took no guard keeps the original.
        _Step("hoist-guards-to-callers", _hoist_guards_to_callers, reduces=_EVERY_CONDITIONAL_WAY_IS_GATED),
        # The step reaching down writes a way with no gate as a gated way per path into the productions it runs. A path
        # ends at the first gate it reaches. The step raises on a path that reaches a production a second time before
        # any gate.
        _Step("flatten-ungated-call-trees", _flatten_ungated_call_trees, settles=_EVERY_CONDITIONAL_WAY_IS_GATED),
    ),
)

# The phase takes `_NO_CHOICE_WAYS_PARTIALLY_OVERLAP` to none. Afterwards the parse enters a pair of ways in the same
# states, or in no shared state, unless the pair is alike but for its gates. The phase follows the gating phase. A way
# with no gate enters anywhere. The phase could take nothing apart from such a way.
_SPLIT_OVERLAPS = _Phase(
    "split-overlaps",
    settles=_NO_CHOICE_WAYS_PARTIALLY_OVERLAP,
    steps=(
        # The step cuts the ways of a choice into atoms of the states that admit them. A way holding a pair of atoms or
        # more becomes a way per atom inside it, and that way gains the guards that say the atom. The step drops a piece
        # that reaches nothing. The pieces keep the order of the ways. The first way to admit a state is therefore the
        # way that admitted it before. The choice's last way stays whole.
        _Step(
            "split-overlapping-ways",
            _split_overlapping_ways,
            settles=_NO_CHOICE_WAYS_PARTIALLY_OVERLAP,
            lapses={
                "every-gate-looks-ahead-at-most-once": "a piece's gate gains the characters its atom holds and "
                "the characters the way already asked. the gate then looks ahead twice at the character in front. "
                "merging the pair into a single set makes it a single question again."
            },
        ),
        # The step merges the peeks the cutting put in a gate, the way `merge-gate-peeks` merges them.
        _Step("merge-gate-peeks-2", _merge_gate_peeks, settles=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    ),
)

# The phases appear in the order the pipeline runs them. A phase holds the steps that serve it. A step declares its
# invariants. A step with none may name a reason under `untestable` instead.
_PHASES = (
    _AT_THE_START,
    _SPECIALIZE,
    _PRUNE_FAILURES,
    _CHARACTER_SETS,
    _DROP_F,
    _DROP_M,
    _DROP_N,
    _LOWER_REPETITIONS,
    _FORBIDDEN_PROBES,
    _LOWER_SCOPES,
    _ONE_STEP_PER_ITEM,
    _FLATTEN_CALLS,
    _MINT_CONTINUATIONS,
    _BUILD_ALTERNATIVES,
    _REFINE_COUNTED_RUNS,
    _CALL_STATES,
    _GUARDS_INTO_GATES,
    _CONSUMES_BEHIND_GATES,
    _GATE_THE_WAYS,
    _SPLIT_OVERLAPS,
)

# `STEPS` lists the steps of the phases in a single run. A reader of the pipeline takes the steps in this order.
#
# `_lower_continuations_into_conflicts` is in the module and in no phase. It serves `_EVERY_CONFLICT_IS_A_TAIL_CALL`.
# That invariant says the parse enters a conflicting production at the end of a way. A conflict is a production the
# machine cannot steer through. The decision between that production's ways then sits inside it. `OWED` holds that
# invariant, and the run counts it at the end.
STEPS = [step for phase in _PHASES for step in phase.flattened()]

# The name of a phase of the pipeline, in the order `_PHASES` gives them. Prose cites a phase by its name, and
# `check_documents` verifies such a citation against this.
PHASE_NAMES = tuple(phase.name for phase in _PHASES)

# The run counts these invariants at the end, beside the invariants the steps name.
OWED = (_EVERY_CHOICE_WAY_IS_DIFFERENT, _EVERY_CONFLICT_IS_A_TAIL_CALL)


def invariants_by_name() -> dict[str, _Invariant]:
    """The invariants the steps name and those still owed, keyed by the name they are cited by."""
    held = {one.name: one for step in STEPS for one in step.invariants}
    held.update({one.name: one for one in OWED})
    return held
