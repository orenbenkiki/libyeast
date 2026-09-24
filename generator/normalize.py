# SPDX-License-Identifier: MIT
"""
Normalize the grammar, a goal at a time.

The pipeline runs a sequence of phases. A phase owns an invariant and adds steps until nothing breaks it. The gate
enforces that invariant from the end of the phase. The law "none stays none" then makes a later step keep it. A phase
that finishes with a green corpus is a checkpoint worth landing.

`_PHASES` says what a phase is for, beside the steps that serve it. A second telling of the order is a second thing to
hold true, and it is the telling rather than the list that goes stale. `invariant_faults` holds `STEPS` to the law.
`unsettled_invariants` says what the last stage still breaks. `untested_steps` says which steps promise something
nothing checks. The pipeline's claims are then answerable rather than described.

**A question that dispatches on node kinds names the kinds it accepts and raises on any other kind.** It has no trailing
default. A `return None` catches a kind nobody thought about. So do `return True`, `continue` and `break`. A default
answer covering a form the question does not recognise is not caution. It reports the blindness of the question as a
fact about the grammar. A count taken from it then reads plausible and wrong. `_is_nullable` and `_split` are
`ir.Question`s for that reason. So are `_shortest_match` and `_entered_unconsumed`. A question written beside them
follows the same rule.

**And a caller asks a question in a single place.** `_gated_by` states where a parse may enter a way, and
`_accepted_way` says what a way consumes. `_is_nullable` says whether a match can consume nothing. `_split` names a
match's consuming half beside its empty half. `_items_of_way` says what a way performs, and `_parts_of_way` says what a
question about a way walks. `after_action` says where performing an action leaves a parse. `_entry_and_exit_spaces` says
where a parse enters a production and where it leaves. A step or a check that needs one of these calls it rather than
walking the grammar again. Separate walks answering the same question drift. The drift shows up as a count that moves
where nothing about the grammar did.

**The askers of a way's entry ask different questions.** `spaces_of_ways` joins a production's entry to the gate the way
holds. The call graph grows that entry. `_spaces_paths_enter_ways_in` reads `_leaf_tables` and unions what the paths
into the production ask. The first names the states a parse reaching this production may be in. The second says what the
paths into it ask. Either asker can tell a way apart where the other cannot. Making either asker serve the other means
reconciling them rather than keeping both.
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

# The ways into a production. A way holds the guards a parse asks along it.
_Paths = frozenset[frozenset[ir.Node]]

# The guards a parse asks on entering a production. A key is the production name.
_Entered = dict[str, _Paths]

# A match's ways that take a character beside its ways that take none. A half is `None` where the match has none.
_Split = tuple[ir.Node | None, ir.Node | None]

# The split of a canonical way or choice. A half holds its own ways. A half holding more than a single way says them as
# a choice.
_WaySplit = tuple[ir.ChoiceState | None, ir.ChoiceState | ir.AlternativeState | None]

# A production name gives what that production can do. A production reads, or takes none, or does both under the entry a
# caller reaches.
_Ways = dict[str, tuple[bool, bool, bool]]

# The guard answers before an action, mapped to the guard answers after the action. The flag says whether the action
# took a character.
_Moved = tuple[dict[spaces.GuardAnswers, frozenset[spaces.GuardAnswers]], bool]


@dataclasses.dataclass(frozen=True)
class _Invariant:
    """
    An invariant names a property the grammar must satisfy and counts the places where the grammar breaks that property.

    A fault reads as the invariant's name. A step naming an invariant that another step names reduces the count of that
    invariant rather than a count of its own. `test` takes a grammar and gives back an error string per place the
    grammar breaks the invariant. The list's length is the count, and the strings say where.

    A test asks about a grammar rather than changing it. The counting pass can therefore hand the same grammars to the
    invariants at once, in the order the cores take them. The counting pass reads the grammar before and after a test
    runs, and holds the test to asking rather than changing. A production is frozen. A grammar can differ only where a
    test put a new production in it. Such a test answers about a grammar the other invariants did not get.
    """

    name: str
    test: Callable[[dict[str, ir.Prod]], list[str]]

    def __call__(self, grammar: dict[str, ir.Prod]) -> list[str]:
        """Run the test over `grammar` and answer with what it faults. A test that rebound a production raises."""
        held = {name: id(production) for name, production in grammar.items()}
        try:
            return self.test(grammar)
        finally:
            # Held even where the test raises. A grammar a question could not answer about is one the next invariant is
            # handed all the same. A change left behind on the way out is the one nothing would otherwise see.
            if {name: id(production) for name, production in grammar.items()} != held:
                raise AssertionError(f"`{self.name}` changed the grammar rather than reading it")


class _Namer:
    """
    The namer mints fresh helper-production names of the form `<base>_<N>`. The `<N>` is the next count unused for that
    base.

    The steps thread a single namer through, and a base's count runs across them. A helper minted for `foo` takes the
    name `foo_1`, and `foo_2` follows. A helper minted while a later step processes `foo_3` comes out `foo_4` rather
    than `foo_3_1`. The base is `foo` with an `_<N>` suffix stripped. A pair of steps minting for the same base do not
    collide. The namer mints the pairs the scope actions name as well, on a single count for the whole pipeline rather
    than per base.

    The namer hands a name out once. The namer also hands out no name that a grammar it has seen already holds. A namer
    that had seen no grammar would hand back `foo_1` over the `foo_1` the grammar holds. A step writes what it mints
    into the same dict. The minting would then silently replace a production rather than adding a fresh production. A
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

        A single count serves the pairs. The count does not run per kind, per step, or per base the way the names do. A
        name has to differ from the names of its own base. A pair has to differ from the other pairs. A `PushCodeAction`
        pair cannot collide with a `PushIndentAction` pair even by accident.
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
    Whether `Param(param)` appears anywhere in `node`. This walks the fields itself.
    """
    if isinstance(node, ir.ParamValue):
        return node.name == param
    if not isinstance(node, ir.Node):
        return False
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
    """`node` with `Param(param)` replaced by `Lit(value)`."""
    if isinstance(node, ir.ParamValue) and node.name == param:
        return ir.LitValue(value)
    return ir.rebuilt(node, lambda child: _substitute(child, param, value))


def _finite_setter(node: ir.Node) -> tuple[str, list[str], dict[object, ir.Node]] | None:
    """
    `(param, [value, ...], {value: condition})` for a `node` that alternates `BindTree`s. The `BindTree`s match a
    condition and set a single finite parameter to a literal. That shape is a data-dependent setter of a finite
    parameter. Anything else is `None`. The values run in the alternation's order. The choice that lifts the setter
    tries them in that order.
    """
    if (
        not isinstance(node, ir.AltTree)
        or not node.items
        or not all(isinstance(item, ir.BindTree) for item in node.items)
    ):
        return None
    params = {getattr(item, "param") for item in node.items}
    written = [getattr(item, "value") for item in node.items]
    if len(params) != 1 or not all(_IS_A_LITERAL(one) for one in written):
        return None
    (param,) = params
    if param not in ir.FINITE_PARAMS:
        return None
    values = [one for one in (getattr(said, "value") for said in written) if isinstance(one, str)]
    if len(values) != len(written):
        return None
    return param, values, {said: getattr(item, "cond") for said, item in zip(values, node.items)}


def _dispatch(body: ir.Node, param: str, values: Sequence[str]) -> ir.Node:
    """
    `body` with the tail that uses `param` replaced by an ordered choice over `values`. A branch substitutes the literal
    for `Param(param)`. The tail runs from the first use of `param` to the end of the top-level sequence.
    """
    items = body.items if isinstance(body, ir.SeqTree) else (body,)
    first = next(index for index, item in enumerate(items) if _is_using(item, param))
    prefix, tail = items[:first], items[first:]
    choice = ir.AltTree(tuple(ir.SeqTree(tuple(_substitute(item, param, value) for item in tail)) for value in values))
    return ir.SeqTree(prefix + (choice,)) if prefix else choice


def _lift_setters(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Make a data-dependent finite parameter lexical. The parameter then monomorphizes like the context.

    The chomping `t` and the indentation mode `i` are the data-dependent ones. `c-chomping-indicator` sets `t` by
    matching an indicator, and the block scalar reads `t` further on through the env. `c-indentation-indicator` sets `i`
    by whether a digit is there, and the scalar's first content line reads `i`. `monomorphize` specializes a `(case)`. A
    setter writes the parameter instead of switching on it. A reader takes the value out of the env. `monomorphize`
    specializes neither. This inverts a setter into a `(case)` on its parameter that matches the condition for a given
    value. The inversion turns a production holding the parameter as a local out-parameter into an ordered choice over
    the parameter's values. A branch fixes the parameter to a literal it hands the setter and the reader alike. The
    parse tries the values in the setter's order. The value whose condition holds matches. The value flows as a value
    rather than stashed state.
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
    """Add to `direct` the finite parameters `node` reads, and to `references` the calls it makes."""
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
    relevant = {name: set(direct) for name, direct in reads.items()}
    # A worklist and not rounds. When a production's set grows, only its callers have anything to reconsider.
    callers: dict[str, set[str]] = {}
    for name in grammar:
        for callee, _passed in calls[name]:
            callers.setdefault(callee, set()).add(name)
    waiting = list(grammar)
    while waiting:
        name = waiting.pop()
        for callee, passed in calls[name]:
            inherited = relevant[callee] - passed
            if inherited - relevant[name]:
                relevant[name] |= inherited
                waiting.extend(callers.get(name, ()))
    return relevant


def _finite_value(expression: ir.Node, grammar: dict[str, ir.Prod], env: Mapping[str, object]) -> str | None:
    """
    A finite value expression over `c`, `t` or `r`, as its concrete value under `env`. This inlines a value function the
    way a call to it would. A value function such as `in-flow` maps a context to another.
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


def _monomorphize(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Specialize the lexical finite parameters away. Those are the context c. The walk reaches a production under a
    combination of context values. `_monomorphize` copies the production once per combination. The walk from the root
    follows references and finds the combinations that occur. A copy evaluates its `CaseTree` and `FlipValue` down to
    the values that copy fixes. The name becomes `ir.specialized`, and the signature drops those parameters. `n`, `m`
    and `f` stay parameters. So do the runtime state `t` and `r`.
    """
    result, done, pending = {}, set(), []

    def runtime_value(expression: ir.Node, env: Mapping[str, object]) -> ir.Node:
        """Reduce a runtime argument under `env`. The arithmetic over `n`, `m` and `f` stays."""
        if isinstance(expression, ir.RefCall):
            callee = grammar[expression.name]
            inner = {
                parameter: (
                    _finite_value(argument, grammar, env)
                    if parameter in ir.FINITE_PARAMS
                    else runtime_value(argument, env)
                )
                for parameter, argument in zip(callee.params, expression.args)
            }
            return runtime_value(callee.body, inner)
        if isinstance(expression, ir.FlipValue):
            return runtime_value(_branch(expression, env[expression.var]), env)
        if isinstance(expression, ir.ParamValue):
            held = env.get(expression.name, expression)
            assert isinstance(held, ir.Node), f"`{expression.name}` holds {held!r} rather than a node"
            return held
        return ir.rebuilt(expression, lambda inner: runtime_value(inner, env))

    def specialize(node: ir.Node, env: Mapping[str, object]) -> ir.Node:
        if isinstance(node, ir.CaseTree) and node.var in ir.FINITE_PARAMS:
            value = env.get(node.var)
            for branch in node.branches:
                if branch.value == value:
                    return specialize(branch.item, env)
            if node.default is not None:
                return specialize(node.default, env)
            # A value with no branch is a grammar saying nothing, which is not `<fail>` saying it matches nothing.
            raise ValueError(f"the case on {node.var} has no branch for {value!r} and no default")
        if isinstance(node, ir.RefCall):
            passed, args = {}, []
            for parameter, argument in zip(grammar[node.name].params, node.args):
                if parameter in ir.FINITE_PARAMS:
                    passed[parameter] = _finite_value(argument, grammar, env)
                else:
                    args.append(runtime_value(argument, env))
            # the callee inherits the ambient finite values and overrides the ones this reference passes. its copy is
            # named by the finite parameters its subtree depends on, at those values, an unset parameter as `None`.
            ambient = {x: passed[x] if x in passed else env.get(x) for x in relevant[node.name]}
            pending.append((node.name, ambient))
            return ir.RefCall(ir.specialized(node.name, ambient), tuple(args))
        return ir.rebuilt(node, lambda child: specialize(child, env))

    relevant = _relevant_finite(grammar)
    # the root is entered once per resume policy. that is the finite parameter the caller chooses rather than the
    # grammar settles. each policy's copy is a start state of the machine, made whether or not anything references it.
    for resume in annotated2ir.RESUMES:
        pending.append((ir.ROOT, {x: resume if x == "r" else ir.FINITE_DEFAULTS.get(x) for x in relevant[ir.ROOT]}))
    while pending:
        name, ambient = pending.pop()
        new_name = ir.specialized(name, ambient)
        if new_name in done:
            continue
        done.add(new_name)
        production = grammar[name]
        body = specialize(production.body, ambient)
        params = tuple(parameter for parameter in production.params if parameter not in ir.FINITE_PARAMS)
        result[new_name] = ir.Prod(production.number, new_name, params, body)
    return result


def _does_fail_outright(items: Iterable[ir.Node]) -> bool:
    """
    Whether a run of items can take no input at all. That is a `<fail>` among them that nothing has committed in front
    of.

    Past a cut, a refusal does not reach the choice above. The refusal is the error the cut names. A run whose `<fail>`
    sits behind a cut therefore fails differently from a run no input takes. Taking the first for the second would turn
    a raise into a way the parse quietly moves on from.
    """
    for item in items:
        if isinstance(item, ir.CutAction):
            return False
        if isinstance(item, ir.FailTree):
            return True
    return False


def _pruned(node: ir.Node, failing: set[str]) -> ir.Node:
    """
    Prune the parts of `node` that match nothing. The walk rewrites the parts first. The run holding a way then sees
    what that way became.

    This is a rewrite rather than a question. The rewrite says what a shape becomes where the shape holds a `<fail>`.
    Anything else comes back unchanged. A choice drops the ways nothing takes. The choice itself matches nothing where
    the drop leaves no way. A run holding such a way takes no input. A repetition of such a match takes no turn. The
    repetition is then the empty match where the grammar asks for no turn. The repetition matches nothing where the
    grammar asks for a turn. A call of a production that matches nothing matches nothing.

    A recovery is no recovery if nothing enters its handler. The cut that asked goes on unwinding to the handler above.
    The rewrite did the same with a handler that took no input. The rewrite keeps a commit, and it keeps a recovery's
    protected match. A refusal under the commit is the error the commit names. A refusal under the recovery is the
    handler's turn. A `<fail>` under the commit or the recovery means what the commit and the recovery say, and not what
    this rewrite decides.
    """
    node = ir.rebuilt(node, lambda child: _pruned(child, failing))
    if isinstance(node, ir.RecoverWrapper):
        return node.item if isinstance(node.recovery, ir.FailTree) else node
    if isinstance(node, ir.CommitWrapper):
        return node
    if isinstance(node, ir.RefCall):
        return ir.FailTree() if node.name in failing else node
    if isinstance(node, ir.AltTree):
        items = tuple(item for item in node.items if not isinstance(item, ir.FailTree))
        return dataclasses.replace(node, items=items) if items else ir.FailTree()
    if isinstance(node, ir.SeqTree):
        return ir.FailTree() if _does_fail_outright(node.items) else node
    if isinstance(node, (ir.OptTree, ir.StarTree, ir.TrimStarTree)):
        return ir.EmptyTree() if isinstance(_repeated(node), ir.FailTree) else node
    if isinstance(node, (ir.PlusTree, ir.RepTree, ir.BindTree, *ir.WRAPPERS)):
        return ir.FailTree() if isinstance(_repeated(node), ir.FailTree) else node
    return node


def _repeated(node: ir.Node) -> ir.Node | None:
    """The match a node holds inside it. The node is a binding, a repetition or a scope."""
    return _REPEATED(node)


# The kinds where a `<fail>` can be the entire match they hold. A commit and a recovery handle a refusal themselves and
# do not reach here. A kind writing its match some third way raises rather than guessing at an `item` the kind may not
# have. A `TrimStarTree` writes its match as `full`.
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

    Specialization puts such matches into the grammar. A `(case)` naming `<fail>` for a value leaves that value's copy
    unable to match. A caller offering that copy as a way offers a way nothing enters. Keeping such a way would give the
    machine a decision point no input reaches. A later question would have to cover a shape no input reaches.

    This is a fixed point over the productions. A body that becomes `<fail>` makes a call of it fail too. The rounds
    settle when no production's body changes.
    """
    for _round in ir.rounds("the ways nothing takes"):
        failing = {name for name, production in grammar.items() if isinstance(production.body, ir.FailTree)}
        settled = {
            name: dataclasses.replace(production, body=_pruned(production.body, failing))
            for name, production in grammar.items()
        }
        if settled == grammar:
            return grammar
        grammar = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _bound(node: ir.Node, mapping: Mapping[str, ir.Node]) -> ir.Node:
    """
    `node` with the `ParamValue`s the `mapping` names replaced by their expressions. A call then substitutes lexically.
    This walks the fields itself.
    """
    if isinstance(node, ir.ParamValue):
        return mapping.get(node.name, node)
    if not isinstance(node, ir.Node):
        return node
    changed: dict[str, object] = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, ir.Node):
            changed[field.name] = _bound(value, mapping)
        elif isinstance(value, tuple) and value and all(isinstance(item, ir.Node) for item in value):
            changed[field.name] = tuple(_bound(item, mapping) for item in value)
    return dataclasses.replace(node, **changed) if changed else node


# The rules a new step obeys. `_Step` below says what a declaration means. These say how to arrive at a step, and
# `.claude/hooks/step-rules.sh` asks for the argument beside the step that answers them.
#
# **A single thing per step.** A step doing more than that gets split. A split changes no grammar. A split buys a name
# on the corpus diff and a smaller rule to prove by eye. The pipeline is a list. A split costs nothing.
#
# **Make a pair of things alike first. Then compare with `==`.** A factoring may miss a shared prefix between a pair of
# ways. The step hoisting the shared part into a prefix is then missing. An equivalence rule that knows what a pair of
# different-looking chains mean is the wrong shape. Nobody can prove such a rule by eye. Such a rule hides a canonical
# form nobody had written down. A simplification that looks impossible is a missing normalizing step first, and an
# inherent conflict second.
#
# **A step singles out no production by name.** A step names a universal rule and applies it wherever that rule holds.
# A step does not name the productions it applies to. Ordering is where the temptation is sharpest. Under
# backtracking-with-commits the order of the alternatives is semantics. A step that cannot reach a form where the
# orderings compare equal asks what universal rule is missing, rather than which site to name.
#
# **A transformation is local and mechanical.** A step reads its own production's nodes, plus the grammar-wide tables
# that are themselves defined production by production. The correctness argument rests on that and no more. A step may
# not lean on a global property of the parse, even a property true by construction. The right rule states the same fact
# locally, usually in a device the grammar already owns.
#
# **What a transform produces is a global or a stack entry.** The parse holds a global singleton, possibly empty, or
# an entry in the unified stack. A value fitting neither is a value the C parser cannot hold.
# A step producing such a value has produced something that cannot run, and the corpus has no say in it.
#
# **Read the transform by hand against the semantics of the nodes it moves.** The gates are a net rather than a
# substitute. A transformation that moves an action into another production can change nothing the corpus sees. That
# happens wherever the sites it hits are identities. The transformation stays wrong at the next site.
#
# **The order of design is the invariant, then the measurement, then the way to achieve it.** The test comes before the
# transform, and somebody watches the test fail. A test that nobody saw fail proves nothing.
def _named(held: "_Invariant | tuple[_Invariant, ...]") -> tuple["_Invariant", ...]:
    """`held` as a tuple of invariants. A single invariant becomes the tuple holding it."""
    return (held,) if isinstance(held, _Invariant) else tuple(held)


@dataclasses.dataclass(frozen=True)
class _Step:
    """
    A single step of the pipeline.

    A step transforms the grammar and declares what it does to an invariant's count. `settles` means the step drives
    that count to none. The grammar the step gets breaks the invariant. `establishes` means the count reads none
    afterwards, and the invariant made no sense before the step ran. `reduces` means the step lowers a count that later
    steps lower further. The gate hoists work this way.

    A step declares `settles`, `establishes` and `reduces` directly, and derives none from another. Steps may share an
    `_Invariant` and a `transform` where they do the same work on different grounds.

    `lapses` maps an invariant name to a reason. It licenses this step to break what an earlier step settled.
    `invariant_faults` enforces the law. A settling step ends at none. A count does not rise, and a count at none stays
    there. The gate also compares those declarations against what the counts did.

    Steps match invariants by name. Steps naming a single invariant work a single count. A misspelt name reads as a step
    that reduces nothing.

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

    `settles` names the invariants the phase settles. Some step of the phase settles or establishes those. `establishes`
    names what the grammar already holds where the phase opens. `flattened` puts that claim in front of the steps. A
    phase that names nothing under either field raises.

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
        # step rules answered. The claim establishes what the phase names. It has no transform and no value.
        return (_Step(f"holds-{self.name}", establishes=self.establishes), *self.steps)


def _absent(name: str, *kinds: type) -> _Invariant:
    """
    An `_Invariant` that counts nodes of `kinds` left in the grammar. A lowering step rewrites that shape away. A later
    step may not write it back. A fault names the production and the node class.
    """

    def test(grammar: dict[str, ir.Prod]) -> list[str]:
        faults = []

        def walk(owner: str, node: ir.Node) -> None:
            if isinstance(node, kinds):
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
# of it as an argument. That call is no literal, and specialization turns it into a literal.
_IS_A_LITERAL: ir.Question[bool] = ir.Question(
    "whether a value is a literal",
    {
        ir.LitValue: True,
        (*(kind for kind in ir.VALUE_KINDS if kind is not ir.LitValue), ir.RefCall): False,
    },
)

# The walk that reaches the calls in a body. `_hands_a_finite_argument` decides the call itself, and a node of another
# kind takes the walk down to the calls under it.
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

    The project drives this count to none. At none, `_Step.invariants` drops its default value. A step must then name an
    invariant or say why it cannot. Until then this measures how much of the pipeline rests on the corpus and no gate. A
    step with a written `untestable` falls outside this count.
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
    """The count for a single `(stage, invariant)` pair. A worker runs it."""
    built, by_name = held
    at, named = asked
    return _counted(by_name[named], built[at][1])


def _counted_over(
    built: Sequence[tuple[str, dict[str, ir.Prod]]],
    by_name: Mapping[str, _Invariant],
    asked: Sequence[tuple[int, str]] | None = None,
) -> dict[tuple[int, str], int]:
    """
    `{(stage, invariant): count}`. `_counted_over` asks an invariant of a stage it applies to, and shares the asks out
    over the cores.

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

    `_counted` asks the invariant's question of the stages from the step naming the invariant onward. A question raises
    on a shape its mapping leaves out. A grammar the invariant's step has not yet reshaped holds such shapes. An
    invariant asked in front of its own step therefore raises here rather than answering. The step's `establishes` says
    where to start asking.
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
    The places the pipeline breaks its own law, as error strings. The list is empty where the law holds.

    A step's `test` counts the places the grammar breaks its invariant. The law over the stages holds this way. The
    count does not rise. The step that settles it leaves none. Past that it stays none. A step whose `lapses` names the
    invariant may break it, and says why. A step breaking an invariant without a lapse is a fault named at that step.

    The invariant is named by its test. Steps reducing a count therefore fall under a single law. A count runs from the
    first stage whose step names it. The stages before it are no business of the invariant.

    A step names an invariant it settles, only lowers, or claims. Take a step naming an invariant that already read none
    in the grammar it got. That step settles nothing. The step lowers no count and establishes no invariant. The step
    claims nothing. The law would then read the first step behind it as the step at fault. Such a property goes in as a
    claim. The claim says where the property holds, and pins it on no step.

    `counts` takes a count once. The laws below are comparisons over it. A count is a question over a whole grammar, and
    the laws want the same counts. A law wants a count not rising. A law wants a step taking a count to none. A law
    wants a step lowering a count it does not name. Taking a count at a law would read a grammar once per law.
    """
    faults, taken = [], set()
    by_name = {held.name: held for step in STEPS for held in step.invariants}
    # Where each invariant starts being a question. A step that establishes an invariant is asked from the stage it
    # hands on.
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
            # `establishes` is the one that says nothing of the state in front of it. It expects an already-none count,
            # rather than reading that count as a claim of work it did not do.
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
    # Read over the whole of `STEPS` rather than from the first step naming the invariant. A step that settles an
    # invariant it does not mention escapes the questions above.
    for index, step in enumerate(STEPS):
        for named, test in by_name.items():
            before, after = counts.get((index, named)), counts.get((index + 1, named))
            if before and after == 0 and not step.does_finish(test):
                faults.append(f"[{step.name}] takes `{named}` to none and does not say so")
    # Read from the first step naming the invariant. Before that, what a step does to the shape is construction.
    for named, test in by_name.items():
        first = _first_step_naming(named)
        for index in range(first, len(STEPS)):
            step = STEPS[index]
            before, after = counts.get((index, named)), counts.get((index + 1, named))
            if before is not None and after is not None and after < before and not _does_step_name(step, named):
                faults.append(f"[{step.name}] lowers `{named}` from {before} to {after} and does not name it")
    # A lapse is a reason for something that happens. A lapse nothing happens under is a claim the grammar has outgrown,
    # and it goes rather than standing as a licence nobody needs.
    for step in STEPS:
        for named in step.lapses:
            if named in by_name and (step.name, named) not in taken:
                faults.append(f"[{step.name}] declares a lapse of `{named}` and does not break it")
    # And the same the other way about. A step naming an invariant it neither lowers nor leaves at none claims work it
    # does not do.
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
    lapse still counts as unsettled. A lapse is work still owed. The step doing that work is unwritten. The pipeline
    finishes by emptying this list.

    `unsettled_invariants` reads `OWED` beside what the steps name. This counts an invariant that no phase has taken on.
    Nobody writes that count down by hand, and it cannot go stale.
    """
    named = {held.name: held for step in STEPS for held in step.invariants}
    named.update({held.name: held for held in OWED})
    counted = [(name, len(test(grammar))) for name, test in sorted(named.items())]
    return sorted(((name, count) for name, count in counted if count), key=lambda held: -held[1])


def _entered_by_name(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions that a parse enters without a call, as start states of their own. They are the root's copy under a
    resume policy, and the recovery a failed cut lands on. The resume policy is the parameter the caller chooses. A
    redirect cannot reach a production no caller names. These therefore keep their own names through a cleanup.
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
    `grammar` without the productions no parse can enter. That is dead-production elimination. The walk runs from the
    root down.
    """
    keep = _reachable(grammar)
    return {name: production for name, production in grammar.items() if name in keep}


def _spliced(grammar: dict[str, ir.Prod], keep: Container[str]) -> tuple[dict[str, ir.Prod], dict[str, str]]:
    """
    `grammar` with the do-nothing productions gone. Such a production has a body that is a single ungated call. The call
    has no action, no continuation and no recovery of its own. The production amounts to its callee. A reference to the
    production therefore becomes a reference to that callee, and the callee's parameters bind to the call's arguments. A
    chain of such productions collapses in a single pass. A production that reaches only itself stays.
    """
    passthroughs = {}
    for name, production in grammar.items():
        body = production.body
        if name in keep or not isinstance(body, ir.ChoiceState) or len(body.alternatives) != 1:
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
        while isinstance(reference, ir.RefCall) and reference.name in passthroughs and reference.name not in seen:
            seen.add(reference.name)
            inner = passthroughs[reference.name]
            reference = _bound(inner, dict(zip(grammar[reference.name].params, reference.args)))
        return reference

    def rewrite(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, rewrite)
        return resolved(node) if isinstance(node, ir.RefCall) else node

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
    comparison blanks the pairs the scope actions name. A reference means the group of the production it names, rather
    than the name itself. A pair of loops differing only in the names of their helpers then come out alike. So does a
    pair differing only in which pair they hold. Comparing the bodies as written cannot see that. The groups are the
    coarsest partition that stays stable under that checker. The groups start alike, a difference splits them, and the
    rounds end when a round splits nothing.
    """

    def blanked(body: ir.Node) -> tuple[ir.Node, tuple[str, ...]]:
        """`body` with the reference names blanked, and those names in the order the walk found them."""
        held: list[str] = []

        def rewrite(node: ir.Node) -> ir.Node:
            node = ir.rebuilt(node, rewrite)
            if isinstance(node, ir.RefCall):
                held.append(node.name)
                return dataclasses.replace(node, name="#")
            return node

        return rewrite(body), tuple(held)

    # A body with its names taken out, built once. A round changes what group a name is in, not where it stands.
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
    `grammar` with productions that behave alike written once. A call to a group's member is a call to any other member
    of that group. A reference to a duplicate therefore becomes a reference to the kept production. A production the
    parse enters by name stays. A redirect cannot reach past that name. A group may hold a pair of productions the parse
    enters by name, and both of those productions stay.

    The kept production replaces the productions that go. The kept scope actions take the pairs of the productions that
    went. A half holds the pairs the halves it replaced held. A close asking whether it shares a pair with the open on
    the stack reads that. That close pays the cost of the merge too. The grammar itself stopped telling a pair of pairs
    apart. A close cannot tell them apart either.
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
    # Each node renames what it holds. That is the same declaration reachability reads. A name is followed on the class
    # saying it holds one, and not on a walk recognising the node it is written in.
    return {name: p.renamed(canonical) for name, p in swept.items()}, canonical


def _flattened(node: ir.Node) -> ir.Node:
    """
    `node` with what a transformation leaves in its shape taken out. A sequence or a choice of a single item is that
    item. A nested sequence or choice of the same kind is its items in place. An `<empty>` in a sequence goes. The
    sequence matches at that place and moves nothing.

    The flattening changes nothing the grammar matches or emits. `<empty>` is the continuation itself, and concatenation
    and ordered choice are both associative.

    A choice of *nothing* is not litter and stays. It is the path no input takes. A specialization leaves that where a
    value has no branch.

    An empty match among a way's actions is litter of the same kind as an empty match in a sequence, and goes on the
    same ground. It takes no character and does nothing. The way matches what the way matched with the empty in place.
    """
    node = ir.rebuilt(node, _flattened)
    if isinstance(node, ir.AlternativeState) and any(isinstance(action, ir.EmptyTree) for action in node.actions):
        return dataclasses.replace(
            node, actions=tuple(action for action in node.actions if not isinstance(action, ir.EmptyTree))
        )
    if isinstance(node, ir.SeqTree):
        items = tuple(
            held
            for item in node.items
            for held in (item.items if isinstance(item, ir.SeqTree) else (item,))
            if not isinstance(held, ir.EmptyTree)
        )
        if not items:
            return ir.EmptyTree()
        return items[0] if len(items) == 1 else ir.SeqTree(items=items)
    if isinstance(node, ir.AltTree) and node.items:
        items = tuple(held for item in node.items for held in (item.items if isinstance(item, ir.AltTree) else (item,)))
        return items[0] if len(items) == 1 else ir.AltTree(items=items)
    return node


def _cleaned(grammar: dict[str, ir.Prod], namer: _Namer | None = None) -> dict[str, ir.Prod]:
    """
    `grammar` with what a transformation leaves behind swept up. `_flattened` takes a body down to the shape it denotes.
    `_spliced` takes out the productions that only call something else. `_merged` folds together the productions that
    write the very same thing. `_purged` drops a production no parse can enter. `_purged` runs last and sees what the
    earlier passes strand.

    Splicing feeds merging. Merging feeds splicing. A merge makes a pair of productions the same call, and a splice
    makes a pair of callers identical. The splice and the merge therefore run to a fixpoint. The sweep changes nothing
    the grammar matches or emits. A flattened body denotes what it denoted. A spliced production ran no action and made
    no decision. A merged production is the production kept, and it matches character for character.

    The flattening comes first. The merge reads shape rather than meaning. A pair of productions can say the same thing
    with a singleton alternation in different places. Those are structurally unequal and do not merge. Said flat they
    are the same node. Flattening therefore decides how much work the sweep does. A merge the flattening makes possible
    is a duplicate the sweep existed to find, rather than a duplicate the sweep invented.
    """
    keep = _entered_by_name(grammar)
    for _round in ir.rounds("the splice of do-nothing productions"):
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
    diffs the interpreter's token stream across those pairs, and names a step that changes the stream. The steps thread
    a single `Namer` through, and the helper productions they mint number `<base>_<N>` off a count shared across the
    steps. A sweep cleans a step's grammar of the leavings the step made. The leavings are the do-nothing productions,
    the duplicates, and the productions the root stopped reaching. A transformation that replaces a call site strands
    the callee. A do-nothing or a duplicate the sweep missed would hold the meter of the work still to decide above its
    honest floor. The sweep leaves the base grammar as the completeness gate froze it.

    A claim is not a step in this sense. It transforms nothing. It joins the list to say where a property already holds.
    A claim therefore repeats the stage it got rather than making a stage.

    A check refuses a step that leaves the grammar unchanged. A step goes idle when the shape it looks for stops
    arriving. An earlier step writes that shape differently. That is a regression in the step before it, and not a step
    to keep. The check reads the transform's output before the sweep runs. The check then judges a step on the step's
    work rather than on the sweep's work.
    """
    namer = _Namer()
    namer.sees(grammar)
    result = [("base", grammar)]
    for step in STEPS:
        ir.say(f"    [{step.name}] over {len(grammar)} production(s)")
        if step.is_a_claim:
            result.append((step.name, grammar))  # a claim leaves the grammar it was handed. the stage repeats
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

    The base must match a single character. A subtracted side must have codepoints the check can work out. Annotations
    around a subtracted character do not count against it. A difference reads the text a match takes, and not the code
    it holds.
    """
    return [
        f"{name}: a `(---)` takes characters from something that is not a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.DiffSet)
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
    names the kinds, and a kind it leaves out raises.
    """
    return _SHORTEST_MATCH(node, grammar, seen)


def _shortest_of_ways(ways: Iterable[ir.Node], grammar: dict[str, ir.Prod], seen: frozenset[str]) -> int:
    """
    The fewest a choice can take. That is the least of the ways the choice offers, and the cap where it offers none.
    """
    return min((_shortest_match(way, grammar, seen) for way in ways), default=_SHORTEST_CAP)


def _shortest_counted(count: ir.Node, taken: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str]) -> int:
    """
    A counted repetition takes the count times a single turn. It takes none where the parse may work the count out as
    none.
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

    A difference's base is a name, and the ways are therefore the callee's ways. The production the reference names
    stays for its own callers. A call passing arguments raises rather than splicing without them.
    """
    if isinstance(base, ir.RefCall):
        if base.args:
            raise ValueError(f"a `(---)` takes characters from `{base.name}`. That call takes arguments.")
        return _difference_ways(grammar[base.name].body, grammar)
    return base.items if isinstance(base, ir.AltTree) else (base,)


def _taken(ways: Sequence[ir.Node], minus: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
    """
    `ways` as a difference over their union. The answer is empty where the union is empty. `ways` is a run of ways
    taking a single character apiece.
    """
    if not ways:
        return ()
    return (ir.DiffSet(base=ways[0] if len(ways) == 1 else ir.AltTree(items=tuple(ways)), minus=minus),)


def _distribute_differences(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Take a difference into the ways of the item it subtracts from. The difference then holds a pair of sets.

    A difference over a choice is the choice of the differences, and the ways keep their order. `(A | B) - m` is `(A -
    m) | (B - m)`. A run of ways taking a single character apiece takes the subtraction once, as the union those ways
    already write. That leaves a difference of a pair of character sets wherever the subtraction reaches anything.

    A way that takes more than a single character keeps its whole language. A subtracted set takes a single character.
    It can remove a match of a single character and nothing wider. The step removes exactly that. `nb-double-char` is an
    escape or a character. `ns-double-char` subtracts the whitespace. The escape alternative matches no whitespace, and
    the subtraction lands on the character alternative.

    A way that may take a single character without being a set raises rather than getting a guess. The subtraction
    reaches that way and no set says how. A subtraction of anything but single characters raises the same way.
    """

    def distributed(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, distributed)
        if not isinstance(node, ir.DiffSet) or ir.is_one_char(node.base, grammar):
            return node
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
    Check that a lookaround holds a character set. The machine can then put its question to a single character.

    An `(exclude)` is no peek of this kind and is no business of this count. It asks about a line rather than about a
    character.
    """
    return [
        f"{name}: a {type(node).__name__} holds a {type(node.item).__name__} rather than a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.PEEKS) and not isinstance(node.item, ir.CharSet)
    ]


_EVERY_PEEK_IS_A_CHARACTER_SET = _Invariant("every-peek-is-a-character-set", _every_peek_is_a_character_set)


def _peeked_question(node: ir.Node, grammar: dict[str, ir.Prod]) -> ir.Node | None:
    """
    Give back the question a peek of `node` asks. The parts of `node` that only shape the output come off it. The
    machine puts the question that survives to the input.

    A probe emits nothing and gives back what it read. An annotation inside a probe is therefore dead. `c-comment` is a
    `#` under the code its character takes. Peeking `c-comment` asks whether the character is a `#`. A peek reads
    through a name. A match keeps the name. A match wants the caller's hold on a production, and a peek has no use for
    that hold.
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
# has looked at says "ask about this" and the reader believes it. A peek of a run would read as a peek of a character.
# The set holding it would be a set nothing established.
_PEEKED_QUESTION: ir.Question[ir.Node | None] = ir.Question(
    "the node a peek of a node asks about. The answer is the set or the call the peek reads down to, or the node "
    "itself.",
    {
        ir.RefCall: _asked_through_a_call,
        # A scope reads through. The markers a scope puts around what it holds shape the output, and a probe hands that
        # back.
        (ir.TokenWrapper, ir.Wrapper): lambda node, grammar: _peeked_question(node.item, grammar),
        # A set is already the question, however the grammar wrote it.
        (ir.AltTree, ir.CharSet, ir.DiffSet, ir.OneCharSet): lambda node, grammar: node,
    },
    # A `(wrap)` reads through exactly as an annotation does, and no peek in this grammar holds a `(wrap)`.
    untested=(ir.Wrapper,),
)


def _lower_char_sets(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Rewrite a character set as a single `CharSet`, the shape the parser asks its question in.

    The grammar writes a set of characters in the ways below. It is a character or a range. It is a union of those, or a
    base with exclusions. Both forms denote the same characters. The parser tests a key for a bit. The rewrite says the
    set once here, as the sorted disjoint intervals it denotes. The rewrite then leaves a single shape to convert, and
    the codegen walks no algebra.

    The rewrite also makes the form canonical. A pair of productions can denote the same characters differently, as a
    union written in either order. The pair are structurally unequal and do not merge. The merge reads shape rather than
    extension. The rewrite says the pair as intervals. The pair is then a single node, and the sweep merges that node.

    The rewrite takes a maximal set rather than the sets inside it. The intervals of a union belong to the union, and
    nobody asks about them apart. A reference stays where a match takes it. A character set with a production of its own
    keeps that production, and the callers hold the reference. The purge then takes nothing, and the fixtures stay
    reachable. Inside a lookaround the rewrite reads through the reference instead. The rewrite also reads through an
    annotation on what the reference names. A peek holds the question whether the character falls in a given set.
    Neither a name nor a code is a question the machine can put to the input.
    """

    def lowered(node: ir.Node) -> ir.Node:
        if isinstance(node, ir.PEEKS):
            asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
            if isinstance(asked, ir.CharSet):
                return dataclasses.replace(node, item=asked)
        if isinstance(node, ir.RefCall):
            return node  # its production is where the set is said, and this is the caller's hold on it
        said = _as_char_set(node, grammar)
        return said if isinstance(said, ir.CharSet) else ir.rebuilt(node, lowered)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


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
    """A peek's question. The parser takes the set a peek holds, where the peeked node is a single character."""
    asked = _peeked_question(node.item, grammar)
    assert asked is not None, f"{owner}: a peek asks about nothing"
    if not ir.is_one_char(asked, grammar):
        _asked_where_it_sits(node, grammar, owner, faults)
        return
    if not isinstance(node.item, ir.CharSet):
        kinds = (type(node).__name__, type(node.item).__name__)
        faults.append(f"{owner}: a {kinds[0]} asks about a character as a {kinds[1]}")


def _asked_where_it_sits(node: ir.Node, grammar: dict[str, ir.Prod], owner: str, faults: list[str]) -> None:
    """
    Anything else. A match of a single character is a set, and this counts it. This walks into a node holding parts.
    """
    if ir.is_one_char(node, grammar):
        faults.append(f"{owner}: a {type(node).__name__} is a character set and is not a `CharSet`")
        return
    ir.rebuilt(node, lambda child: (_CHARACTER_QUESTIONS(child, grammar, owner, faults), child)[1])


# The walk raises on a kind `_CHARACTER_QUESTIONS` leaves out, and does not walk into it.
_CHARACTER_QUESTIONS: ir.Question[None] = ir.Question(
    "nothing, appending to `faults` a line per question about a character written as anything but a `CharSet`",
    {
        ir.CharSet: lambda node, grammar, owner, faults: None,  # the shape the parser takes, and the walk is done.
        ir.PEEKS: _asked_by_a_peek,
        # a hold on the production where the grammar says the set.
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
    `spans` as the character set a gate peeks. That is the shape a character question takes.

    The rewrite keeps the invalid byte's `(-1, -1)` apart from the characters. The interval is a unit no character class
    holds. It overlaps itself and nothing besides. Coalescing it with a run starting at the lowest codepoint would say
    the parser accepts a character there.
    """
    invalid = [span for span in spans if span[0] < 0]
    return ir.CharSet(tuple([(-1, -1)] * bool(invalid) + chars.merged_spans([s for s in spans if s[0] >= 0])))


def _peek_spans(peek: ir.Node, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]] | None:
    """
    The codepoint intervals `peek` accepts, or `None` where the grammar leaves the set open. The invalid-byte class is
    the interval `(-1, -1)`. That is a unit no character class can also hold, and the unit overlaps only itself. An
    alternation holding the unit beside character classes is the classes' intervals with that unit. The recovery's
    any-byte peek is such an alternation.
    """
    return _PEEK_SPANS(peek, grammar)


def _united_peek_spans(peek: ir.AltTree, grammar: dict[str, ir.Prod]) -> list[tuple[int, int]] | None:
    """
    An alternation's intervals, unioned here rather than denoted. The invalid byte has no denotation. The byte is a unit
    no character holds. An alternation with the byte denotes nothing while admitting perfectly well. An alternation may
    hold the byte as the `InvalidSet` node, or as the interval a `CharSet` says it with.
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
    """A question's intervals by what it denotes. The answer is `None` where the grammar leaves the set open."""
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
    `node` and the nodes inside it. A field of any kind may hold them. This walks the fields rather than going through
    the generic walker. The generic walker skips a `ParamValue` a field holds directly, such as the parameters of
    `s-indent-floor`'s `Le(f, n)`.
    """
    yield node
    if isinstance(node, ir.Node):
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            for item in value if isinstance(value, tuple) else (value,):
                yield from _held(item)


def _parameter_uses(grammar: dict[str, ir.Prod], wanted: Iterable[str]) -> list[str]:
    """
    The places that still hold a parameter in `wanted`. Such a place is a production declaring a parameter. It is also a
    call passing a parameter at the position the callee declares. It is also an expression reading a parameter.

    A read of `t` means nothing until the reader knows the caller. A step after this one may read the grammar with no
    parameter left to resolve.
    """
    faults = []
    for name, production in grammar.items():
        faults += [f"{name}: declares `{param}`" for param in production.params if param in wanted]
        for node in _held(production.body):
            if isinstance(node, ir.RefCall):
                callee = grammar.get(node.name)
                declared = () if callee is None else callee.params
                for position in range(min(len(node.args), len(declared))):
                    if declared[position] in wanted:
                        faults.append(f"{name}: passes `{declared[position]}` to `{node.name}`")
            elif isinstance(node, ir.ParamValue) and node.name in wanted:
                faults.append(f"{name}: reads `{node.name}`")
    return faults


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
    Check that a push and its pop wrap a call that changes the indentation. Such a call measures against a level, or it
    establishes a level a later item reads.

    A push and a pop put a level where a read reaches that level without a call passing the level down. The check looks
    for the pair immediately around the call. The caller runs nothing between the push and the call, or between the call
    and the pop.
    """
    faults = []
    establishing = _establishing(grammar)
    for name, production in grammar.items():
        guarded = set()
        for node in _held(production.body):
            if not isinstance(node, ir.SeqTree):
                continue
            for position, item in enumerate(node.items):
                before = node.items[position - 1] if position else None
                after = node.items[position + 1] if position + 1 < len(node.items) else None
                if isinstance(before, ir.PushIndentAction) and isinstance(after, ir.PopIndentAction):
                    guarded.add(id(item))  # entered under a level this way pushes and takes back
                if isinstance(after, ir.PushIndentAction) and isinstance(node.items[-1], ir.PopIndentAction):
                    guarded.add(id(item))  # establishes a level, pushed where it returns and kept to the way's end
        for node in _held(production.body):
            if id(node) in guarded or not isinstance(node, ir.RefCall):
                continue
            if _pushed_level(grammar, node) is not None:
                faults.append(f"{name}: calls `{node.name}` against an indentation nothing pushes")
        for node in _held(production.body):
            if not isinstance(node, ir.SeqTree):
                continue
            for position, item in enumerate(node.items):
                if id(item) in guarded or not isinstance(item, ir.RefCall):
                    continue
                if item.name not in establishing or not _is_by_reference(grammar, item):
                    continue
                if any(_is_using(later, "n") for later in node.items[position + 1 :]):
                    faults.append(f"{name}: reads the indentation `{item.name}` establishes where nothing holds it")
    return faults


_EVERY_INDENTATION_CHANGE_IS_PUSHED = _Invariant(
    "every-indentation-change-is-pushed", _every_indentation_change_is_pushed
)


def _replaced(node: ir.Node, swap: Callable[[ir.Node], ir.Node]) -> ir.Node:
    """
    `node` with `swap` applied to `node` and to the fields it holds. This walks the fields itself.

    The generic walker takes a `ParamValue` as a value, and does not visit a `ParamValue` a field holds directly. That
    is where a read hides. A rewrite of the reads therefore goes through this rather than through the generic walker.
    """
    node = swap(node)
    if not isinstance(node, ir.Node):
        return node
    changed: dict[str, object] = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, tuple):
            items = tuple(_replaced(item, swap) for item in value)
            if items != value:
                changed[field.name] = items
        elif isinstance(value, ir.Node):
            held = _replaced(value, swap)
            if held is not value:
                changed[field.name] = held
    return dataclasses.replace(node, **changed) if changed else node


def _read_off(param: str, held: ir.Node) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    Take `param` off the productions and the calls. It takes the declaration off the productions and the argument off
    the calls. It turns a read of the parameter into `held`. `held` is where the value lives, a global's single slot or
    the stack the parse holds.

    A global holds where its value does not nest. The leading empty lines of a block scalar measure the floor, and that
    scalar's first content line reads it, a construct at a time. The `(clear)` holds the global to a construct at a
    time. A read past the region raises rather than answering. The stack holds where a read of the parameter agrees with
    the stack top. A check over the whole corpus found that. The transform does not touch the writes. A `(set)` and a
    `(clear)` name the parameter as a string, and reach the slot once no production declares it.
    """

    def transform(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
        positions = {
            name: production.params.index(param) for name, production in grammar.items() if param in production.params
        }

        def swap(node: ir.Node) -> ir.Node:
            if isinstance(node, ir.ParamValue) and node.name == param:
                return held
            if isinstance(node, ir.RefCall) and positions.get(node.name, len(node.args)) < len(node.args):
                position = positions[node.name]
                return dataclasses.replace(node, args=node.args[:position] + node.args[position + 1 :])
            return node

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
    The indentation `node` measures against, where that is not the indentation in force. The answer is `None` where they
    match.

    A call handing the parameter itself passes the indentation in force. The call changes nothing and pushes nothing. A
    call handing anything else enters under an indentation of its own. That is a sum, a column or a literal.
    """
    if not isinstance(node, ir.RefCall):
        return None
    callee = grammar.get(node.name)
    if callee is None or "n" not in callee.params:
        return None
    position = callee.params.index("n")
    if position >= len(node.args):
        return None
    level = node.args[position]
    return None if isinstance(level, ir.ParamValue) and level.name == "n" else level


def _span_consumes(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a run over a character class as a consume. The parse enters the consume on the class. `x+` is the guard that
    the class is in front. The span comes behind it. `x*` is that same way beside the way the class is not in front of.

    Such a run takes a value the input decides rather than a way the parse chooses. The codegen makes a single
    repeated-char-set call of such a consume. A span with the class in front takes at least a character of the class, as
    a `PlusTree` does.

    A `StarTree`'s second way asks that the class is *not* there rather than matching empty beside it. The consume that
    replaces the run is possessive. The consume takes the run where the class is in front, and no shorter match remains
    to hand back. An empty way would be exactly that shorter match. `s-indent-le` is a run of spaces judged against the
    indentation afterwards. A run too long would fail the judgement. The fallback to no spaces would then pass that same
    judgement. The character tells the ways apart. Neither way is reachable where the parse took the other, and the
    alternation is the consume.

    The gate takes the guard rather than the class itself, though either admits the same text. A match of the class is a
    call, and a call is where `mint-continuations` ends a way. The span would then land in a continuation entered after
    the call returned. The guard saying the run takes a character would sit a production away from the run. A guard is
    no call. It stays among the actions beside the span. That is where a hoist can lift the guard into the gate, and
    where `_split_seq` can read the guard as what makes the consume not empty.

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
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.RepTree) and ir.is_one_char(node.item, grammar):
            # Taken up to the count, with whether it reached the count asked past the taking. That is a consume which
            # cannot fail and a guard that may refuse, rather than a single action doing both.
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
                    ir.SeqTree(
                        items=(
                            ir.IsLessEqualGuard(a=node.count, b=ir.LitValue(value=0)),
                            ir.ConsumeNoCharAction(),
                        )
                    ),
                )
            )
        if isinstance(node, ir.RUNS) and ir.is_one_char(node.item, grammar):
            asked = _as_char_set(_peeked_question(node.item, grammar), grammar)
            taken = ir.SeqTree(items=(ir.LookGuard(item=_a_set(asked)), ir.ConsumeSpanAction(set=node.item)))
            if isinstance(node, ir.PlusTree):
                return taken
            # The way that takes none says so, rather than performing nothing. `(match)` past a way that performed
            # nothing is the length the last run left. That is a different run's answer.
            none = ir.SeqTree(items=(ir.NegLookGuard(item=_a_set(asked)), ir.ConsumeNoCharAction()))
            return ir.AltTree(items=(taken, none))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _every_character_run_is_a_span(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no repetition is over a character class. A run of characters is a value the consume decides, rather than
    a way the parse repeats.

    `ir.repeated` says what repeats, rather than a list of kinds kept here. A list goes stale the moment the grammar
    writes a repetition a new way. Once the named kinds went, the count would read none while character runs still
    remained.
    """
    return [
        f"{name}: repeats a character class instead of consuming it"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Node) and (item := ir.repeated(node)) is not None and ir.is_one_char(item, grammar)
    ]


_EVERY_CHARACTER_RUN_IS_A_SPAN = _Invariant("every-character-run-is-a-span", _every_character_run_is_a_span)

# The repetitions the vendored notation writes are gone. The grammar writes them as ways instead, the way `lower-runs`
# says. Past here the grammar repeats nothing. A question past here relies on that. The counted `RepTree` is not one of
# these. That kind takes the number of turns the count names rather than as many as it can. A later step lowers it.
_NO_STAR_OR_PLUS_NODES = _absent("no-star-or-plus-nodes", ir.StarTree, ir.PlusTree)


# `lower-wraps` runs in `lower-scopes`. A scope that holds what it covers becomes the pair bracketing that scope. A
# `(wrap)` says so outright. A `(wrap)` is a node rather than a pair of markers. A `begin` cannot then lose its `end`.
# After `lower-wraps`, the parse holds the guarantee instead. The parse holds a close to the open that shares its pair.
# `check_markers` still owes for the markers.
_NO_WRAP_NODES = _absent("no-wrap-nodes", ir.Wrapper)


def _lower_wraps(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(wrap)` as the pair of markers it names. `Wrap(begin, end, x)` becomes `Emit(begin) x Emit(end)`.

    A scope that holds what it covers has no place in an alternative. An alternative has a place for an action and none
    for a node enclosing a call. The node is sugar for the sequence this step writes. The interpreter emits the begin,
    matches the item, and emits the end.

    The markers come out adjacent in a single way of a single production. The markers stay adjacent until a split of the
    calls separates them.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Wrapper):
            return ir.SeqTree(items=(ir.EmitAction(code=node.begin), node.item, ir.EmitAction(code=node.end)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# `lower-windows` runs in `lower-scopes`. The `(max)` window is the pair that opens and closes it.
_NO_MAX_NODES = _absent("no-max-nodes", ir.MaxWrapper)


def _lower_windows(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(max)` as the window pair. `Max(limit, message, x)` becomes `OpenWindow(limit, message) x CloseWindow()`.

    Windows do not nest. The outermost applies. An inner window sits inside the budget the outer already bounds. The
    pair counts the opens, where a `MaxWrapper` asked whether an earlier open set a ceiling. The pair and the wrapper
    agree where the grammar writes a window in a single way. An open counting nothing sets no ceiling. The step
    therefore leaves no `MaxWrapper` at all. The step cannot lower the sites that want a window while leaving the other
    sites untouched.

    A `(max)` with nothing in it is the vendored grammar's bare length note. libyeast writes that around a production
    rather than in front of a production. The pipeline places none. The step raises an error on such a `(max)` rather
    than opening a window.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.MaxWrapper):
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
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# `lower-commits` runs in `lower-scopes`. The region catching a failed cut is the pair that opens and closes it.
_NO_COMMIT_NODES = _absent("no-commit-nodes", ir.CommitWrapper)


def _lower_commits(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(commit)` as the message pair. `Commit(message, x)` becomes `PushMessage(message) x PopMessage()`.

    The parser raises a commit error when the guarded item does not reach its own end. A continuation that fails past a
    matched item backtracks like any other match. The commitment does not reach past that item, and the pair enforces
    the same bound. The push records a region and the pop marks it reached. The parse may unwind to a push while the
    region of that push stays open. Such an unwind is the error. The interpreter says so where it implements the push.
    The pop goes where the `(commit)` scope ends.

    The pair changes where the pushed region's record lives. The wrapper keeps that record in a Python local. The pair
    keeps it on the list of open commitments the emitter holds. The list survives a split of the pair across a call.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.CommitWrapper):
            pair = namer.pair()
            return ir.SeqTree(
                items=(
                    ir.PushMessageAction(message=node.message, pair=pair),
                    node.item,
                    ir.PopMessageAction(pair=pair),
                )
            )
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _mint_forbidden_probes(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    The pattern an exclusion forbids gets a copy of its own. The copy holds only what the pattern matches and what it
    answers.

    The pattern answers whether the forbidden text is there. The parse goes back under either answer. A `(token)` inside
    the pattern therefore gives a code to characters that no token keeps. The copy drops the annotation and matches the
    same text.

    This mints a copy rather than rewriting the original. A way inside the pattern and a way outside it reach the same
    production. `c-directives-end` is the `---` a document really opens with, and the `---` a plain scalar must not run
    into. The parse matches the second to throw it away, and keeps the first. The whole reach that minting touches gets
    a copy, and the callers inside that reach name the copies. The sweep that follows minting collapses a copy that came
    out the same as its original.
    """
    copies = {name: namer.fresh(name) for name in sorted(_forbidden_productions(grammar))}

    def tested(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, tested)
        return node.item if isinstance(node, ir.TokenWrapper) else node

    def forbids(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, forbids)
        if isinstance(node, (ir.ExcludeAtAction, ir.SetForbiddenAction)) and node.item is not None:
            return dataclasses.replace(node, item=node.item.renamed(copies))
        return node

    minted = {
        copy: dataclasses.replace(grammar[name], name=copy, body=tested(grammar[name].body.renamed(copies)))
        for name, copy in copies.items()
    }
    written = {
        name: dataclasses.replace(production, body=forbids(production.body)) for name, production in grammar.items()
    }
    return {**written, **minted}


# `lower-tokens` runs in `lower-scopes`. A pair sets the code the characters of a run take, and a pair takes that code
# back.
_NO_TOKEN_NODES = _absent("no-token-nodes", ir.TokenWrapper)


def _lower_tokens(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a `(token)` as the code pair. `Token(code, x)` becomes `PushCode(code) x PopCode()`.

    An annotation does not make a token. An annotation says what code the characters consumed within it take. The
    annotation cuts the run at both edges. The characters before and after fall into tokens of their own. A half cuts,
    the push sets the code, and the pop takes back the code it displaced. The characters between them take the same code
    either way.

    The change is where the displaced code waits. The wrapper keeps it in a Python local. That local is the frame of the
    running match. The pair puts the code on the stack the parse holds. The halves can then end up in different
    productions once the step splits a way into a call and a continuation. A half names its pair on the same ground. A
    pop takes back the entry on top. A pop cut apart from its push would take back an entry another way had put there.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.TokenWrapper):
            pair = namer.pair()
            return ir.SeqTree(
                items=(ir.PushCodeAction(code=node.code, pair=pair), node.item, ir.PopCodeAction(pair=pair))
            )
        return node

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
    Answer `{name: sets}`. The sets hold what may not match at a start of line where the parse enters a production.
    `None` is among them where nothing may.

    This is a least fixpoint over the calls. A parse enters by name with nothing forbidden. A call passes in the set the
    call site forbids. The parse enters a production a single way, except `l-unparsed` under a line-bounded policy.
    Under a line-bounded policy the parse enters `l-unparsed` from the stream's recovery and from a block entry's
    recovery. The stream's recovery reaches `l-unparsed` with nothing forbidden, and the block entry's recovery reaches
    it inside a document.

    `ir.rounds` counts this, as it counts the fixpoints here. A fixpoint that stops settling says so. `ir.ROUNDS` is a
    backstop. `ir.deepest_rounds` reports how far a fixpoint really reaches.
    """
    entries: dict[str, set[ir.Node | None]] = {name: set() for name in grammar}
    for name in _entered_by_name(grammar):
        entries[name].add(None)
    for _round in ir.rounds("the forbidden node where a parse enters a production"):
        reached = {name: set(held) for name, held in entries.items()}
        for name, production in grammar.items():
            for forbidden in entries[name]:
                _reached_forbidden(production.body, forbidden, reached)
        if reached == entries:
            return entries
        entries = reached
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _lower_exclusions(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write an `(exclude)` as the pair of writes that bound it. `ExcludeAt(x)` over the rest of a way becomes
    `SetForbidden(x)` in its place and `SetForbidden(<what follows>)` where the way ends.

    The step is a rewrite rather than a question. `lowered` reshapes `ExcludeAtAction`. `RefCall` and `SeqTree` hand the
    forbidden set down. Any other kind gets rebuilt with its parts lowered.

    The exclusion in force reaches `lowered` as `forbidden`.

    An exclusion is in force until the production holding it returns. That is a frame's worth of scope, and the last
    scope `lower-scopes` has to take off. The writes say outright what the frame kept. A step that moves a way's parts
    into another production therefore takes the end with them. The exclusion covers no more than it did.

    The closing write names what follows. It does not take back what the opening write displaced. The set is a single
    value for the parse. A production holding an exclusion that a pair of callers reach in different states cannot name
    a single set. The step copies the production per state, and a caller enters the copy for its own state.
    `monomorphize` makes the same specialization of a parameter. This step makes it of the forbidden set, and that set
    is no parameter.
    """
    entries = _forbidden_entries(grammar)
    held = {
        name
        for name, production in grammar.items()
        if any(isinstance(n, ir.ExcludeAtAction) for n in _held(production.body))
    }
    copies = {}  # a `(name, forbidden)` to the name to call in that state
    for name in sorted(held):
        for forbidden in sorted(entries[name], key=str)[1:]:
            copies[(name, forbidden)] = namer.fresh(name)

    def told(name: str, forbidden: ir.Node | None) -> str:
        return copies.get((name, forbidden), name)

    def lowered(node: ir.Node, forbidden: ir.Node | None) -> ir.Node:
        if isinstance(node, ir.RefCall):
            return dataclasses.replace(node, name=told(node.name, forbidden))
        if isinstance(node, ir.SeqTree):
            items: list[ir.Node] = []
            for index, item in enumerate(node.items):
                if isinstance(item, ir.ExcludeAtAction):
                    rest = lowered(ir.SeqTree(items=node.items[index + 1 :]), item.item)
                    return ir.SeqTree(
                        items=(
                            *items,
                            ir.SetForbiddenAction(item=item.item),
                            *getattr(rest, "items"),
                            ir.SetForbiddenAction(item=forbidden),
                        )
                    )
                items.append(lowered(item, forbidden))
            return ir.SeqTree(items=tuple(items))
        return ir.rebuilt(node, lambda child: lowered(child, forbidden))

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

    The turn is a production of its own, and both places call that production. The recursion is a production too. A loop
    is a state the machine jumps to. Later phases shape the resulting `AltTree` and `SeqTree` like any other node. The
    guard past the turn cuts the way.

    A run over a character class is the same operation said as the consume a parser makes of it. `span-consumes` has
    already written that as a `ConsumeSpanAction`. A run over anything else repeats a way, and `_lower_runs` says a
    repeated way as ways.
    """
    minted = {}

    def said(name: str, number: int, node: ir.PlusTree | ir.StarTree) -> ir.Node:
        """`node` written out as the ways it offers. This mints the turn and the recursion as productions."""
        # A loop is a state the machine jumps to. The recursion is a production, and so is the turn where it is not
        # already a call.
        loop = namer.fresh(name)
        if isinstance(node.item, ir.RefCall):
            turn = node.item
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
        return taken if isinstance(node, ir.PlusTree) else ir.AltTree(items=(taken, ir.EmptyTree()))

    def lowered(name: str, number: int, node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lambda child: lowered(name, number, child))
        return said(name, number, node) if isinstance(node, ir.RUNS) else node

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

    This writes the `<empty>` even where the item can already take nothing, and the corpus bears that out. The optional
    offers the item's ways and *then* an empty match. The item offers its own ways. Its empty way may sit ahead of the
    ways that read. Dropping the second empty match there changes which way the parse prefers, and the corpus then
    parses differently. The empties phase later splits apart a pair of ways that both take nothing.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        return ir.AltTree(items=(node.item, ir.EmptyTree())) if isinstance(node, ir.OptTree) else node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# An optional hides no empty match once the step removing optional nodes runs. The empties phase counts such matches. A
# `StarTree` hides an empty match as well, and this property leaves it out. Lowering a `StarTree` changes the parse. A
# run is possessive. An alternation's empty way is a fallback the continuation can reach.
_NO_OPT_NODES = _absent("no-opt-nodes", ir.OptTree)


def _inner_ways(node: ir.Node) -> tuple[ir.Node, ...]:
    """The matches `node` holds, as ways in their own right. The walk goes on into these once it has counted one."""
    return _INNER_WAYS(node)


_INNER_WAYS: ir.Question[tuple[ir.Node, ...]] = ir.Question(
    "the tuple of nodes a body offers as its ways. A way is a match in its own right.",
    {
        ir.ChoiceState: lambda node: node.alternatives,
        ir.AltTree: lambda node: node.items,
        ir.SeqTree: lambda node: (node,),
        ir.RecoverWrapper: lambda node: (node.item, node.recovery),
        ir.BindTree: lambda node: (node.cond,),  # a binding, and the match it puts a value in scope for.
    },
)


def _nested_matches(grammar: dict[str, ir.Prod], reported: type | tuple[type, ...]) -> list[str]:
    """
    Items in a way that hold a match. Those are the shapes the machine has no single step for.

    This is the count the phase keeps. The phase's steps reduce that count between them. A body is a choice of ways or a
    run of a single way. A way is a run of items. An item is a call or an action. An item is also a guard, a character
    taken, or an empty match. Anything else holds a match inside itself. Such a shape has to become a production before
    a state machine reads that shape. `reported` narrows the count to a single kind. That is how a step's invariant
    reads its share of the phase's count through the same walk. The walk goes into a holder either way, and it finds a
    nested match wherever that match sits.

    This counts a nested match where it sits rather than at the outermost only. Lifting a match therefore takes a single
    item off the count, and uncovers no fault the walk had not already read. The walk raises on a kind `_INNER_WAYS`
    leaves out. An item covered by accident is a shape the machine would arrive at with no state to be in.
    """
    faults: list[str] = []

    def item(node: ir.Node, owner: str) -> None:
        if isinstance(node, ir.LEAF_ITEMS):
            return
        if isinstance(node, ir.HOLDS_A_MATCH):
            if isinstance(node, reported):
                faults.append(f"{owner}: a {type(node).__name__} sits where an item does and holds a match")
            for inner in _inner_ways(node):
                way(inner, owner)
            return
        raise TypeError(f"cannot tell whether {type(node).__name__} is an item the machine runs at that position")

    def way(node: ir.Node, owner: str) -> None:
        for part in _items_of_way(node):
            item(part, owner)

    for name, production in grammar.items():
        body = production.body
        for opened in _inner_ways(body) if isinstance(body, ir.BODY_KINDS) else (body,):
            way(opened, name)
    return faults


_EVERY_SUB_ITEM_IS_ONE_STEP = _Invariant(
    "every-sub-item-is-one-step", lambda grammar: _nested_matches(grammar, ir.HOLDS_A_MATCH)
)

# The phase's first share. A choice is where the machine has a state. A choice inside a way has no place to be that
# state. A production of its own is that state, and the way holds the call.
_EVERY_CHOICE_IS_A_PRODUCTION = _Invariant(
    "every-choice-is-a-production", lambda grammar: _nested_matches(grammar, ir.AltTree)
)

# A recovery is a handler over a match. An alternative holds such a handler on its edge. A recovery has no edge until
# this step makes the alternatives. Until then a recovery gets a production of its own. A later re-encode reads the body
# of that production as a way. The call is the item, and the recovery rides the push.
_EVERY_RECOVERY_IS_A_PRODUCTION = _Invariant(
    "every-recovery-is-a-production", lambda grammar: _nested_matches(grammar, ir.RecoverWrapper)
)

# A binding is a match and the write that follows it. The vocabulary already writes the match and the write apart.
_NO_BIND_NODES = _absent("no-bind-nodes", ir.BindTree)


def _is_bounded_question(node: ir.Node, grammar: dict[str, ir.Prod], entered: frozenset[str] = frozenset()) -> bool:
    """
    Whether `node` asks what a bounded number of the machine's steps answers.

    Repetition and recursion make a question unbounded. A call costs what its callee costs. A call reaching itself costs
    without bound. Anything else is a fixed run of steps, and a span is among those. A run of spaces measured against
    the indentation is a consume and a comparison. A loop over the same characters is a turn per character.
    """
    # A repetition costs a turn per character rather than a single step. A run taken whole is a value the input decides,
    # judged once. What it costs does not grow with what it takes.
    if isinstance(node, ir.REPETITIONS):
        return False
    if isinstance(node, ir.RefCall):
        if node.name in entered or node.name not in grammar:
            return False
        return _is_bounded_question(grammar[node.name].body, grammar, entered | {node.name})
    held: list[ir.Node] = []

    def seen(child: ir.Node) -> ir.Node:
        held.append(child)
        return child

    ir.rebuilt(node, seen)
    return all(_is_bounded_question(child, grammar, entered) for child in held)


def _every_exclusion_is_bounded(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that an `(exclude)` asks a question a bounded number of the machine's steps can answer.

    An `(exclude)` is a guard the parse holds. While the guard is in force, the parse tests it at the starts of line.
    The question has to be answerable where the parse asks it. `c-forbidden` is a line beginning `---` or `...`. A
    break, a space or the end follows. This pattern is a bounded run of steps. Whether the line sits at this indentation
    is another such question. Its run of spaces is a consume, and the parse judges that consume against the indentation
    once. A loop over those spaces would cost a turn per space, and would not answer in a bounded number of steps.
    """
    return [
        f"{name}: an `(exclude)` asks what no bounded run of steps answers"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.ExcludeAtAction) and not _is_bounded_question(node.item, grammar)
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
        # A `(token)` says what code the characters under it take. That is a mark on a stream the probe emits nothing
        # to.
        ir.TokenWrapper: False,
    },
)


def _forbidden_productions(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions an exclusion's pattern reaches, and the productions those reach. The parse runs these productions at
    a start of line, to answer whether the forbidden text is there.
    """
    seen = set()
    worklist = [
        name
        for production in grammar.values()
        for node in _held(production.body)
        if isinstance(node, (ir.ExcludeAtAction, ir.SetForbiddenAction))
        for name in node.references()
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

    The parse runs the pattern at a start of line. The parse then goes back to its old position. The pattern may take
    characters and ask questions, and do no more. An action leaves a mark. The parse discards the match and cannot undo
    that mark.

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
    about them. A gate can then go on a way.

    The check reads the tree's form. It looks for an `AltTree` with a way that names an `AltTree`. The re-encode leaves
    no `AltTree` nodes.
    """
    return [
        f"{name}: a way of a choice that is a call to a choice"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.AltTree)
        for way in node.items
        if isinstance(way, ir.RefCall) and isinstance(grammar[way.name].body, ir.AltTree)
    ]


_NO_CHOICE_OF_CHOICES = _Invariant("no-choice-of-choices", _no_choice_of_choices)


def _no_sequence_of_sequences(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no item of a way, bar the last, is a call to a run of items.

    `a P c` with `P` as `d e` shows fewer items than the parse performs. A question that walks a way to find what it
    does first therefore stops at the call rather than at `d`. `a d e c` writes the same run. The parts then sit where
    the way holds them.

    The check spares a call last in the way. An earlier phase mints the continuation past a way's call as a production
    of its own. A call in the middle hides a run.

    The check reads the tree's form. There the fault is a `SeqTree` holding an item that names a `SeqTree`. Past the
    re-encode there are no `SeqTree` nodes at all. The shape of the tree settles the check.
    """
    return [
        f"{name}: an item of a way that is a call to a run of items"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.SeqTree)
        for item in node.items[:-1]
        if isinstance(item, ir.RefCall) and isinstance(grammar[item.name].body, ir.SeqTree)
    ]


_NO_SEQUENCE_OF_SEQUENCES = _Invariant("no-sequence-of-sequences", _no_sequence_of_sequences)

# A way is a gate, actions and the calls it hands control to. An empty match is none of those. It asks nothing. It does
# nothing and calls nothing. A way that matches the empty input is the way with no gate, no action and no call. The
# machine's form holds no empty match. The step that builds the ways handles an empty match.
_NO_EMPTY_NODES = _absent("no-empty-nodes", ir.EmptyTree)


def _items_of_way(way: ir.Node) -> tuple[ir.Node, ...]:
    """
    The items a way *performs*. This is **not** its gate.

    An alternative holds the actions, the call and the continuation in named fields. A sequence says them in a row
    instead. A question that walks a way wants the parts in the order the parse performs them. Both forms answer alike.
    The item a recovery rides is not among them. It catches a cut rather than running among the parts the way performs.

    Leaving the gate out is right for a rewrite. A rewrite keeps the gate untouched. Leaving the gate out is wrong for a
    question about the parse's own run. The gate decides whether the parse enters the way at all. A walk that cannot see
    the gate reports what a way would do under any input. `_parts_of_way` answers that instead. This is for callers that
    mean the performing parts and no more.
    """
    if isinstance(way, ir.AlternativeState):
        return (*way.actions, *_calls_of_way(way))
    return way.items if isinstance(way, ir.SeqTree) else (way,)


def _calls_of_way(way: ir.AlternativeState) -> tuple[ir.RefCall, ...]:
    """
    The calls `way` makes, in the order the way makes them. The way makes a tail call with nothing behind it. Or the way
    makes a call and then continues past it.
    """
    return tuple(held for held in (way.first, way.second) if held is not None)


def _first_call_of_way(way: ir.AlternativeState) -> ir.RefCall | None:
    """
    The call `way` makes first, or `None` where it makes none.

    The answer is `first` where a way makes a call and then continues past it. The answer is `second` otherwise, where
    the way is a tail call and `second` is the call itself.
    """
    return way.first if way.first is not None else way.second


def _parts_of_way(way: ir.Node) -> tuple[ir.Node, ...]:
    """
    The parts a question about a way should walk. Those are the guards the gate asks, then what the way performs, in the
    order the parse arrives at them.

    The parse asks a gate before entering the way. A question that leaves the gate out answers about a way the parse may
    not take. The gate's guards take no width and sit among a way's parts as readily as an action would. A walk over a
    way's parts therefore asks the guards the question it already asks of the way's actions.

    The peek is not among a way's parts. The peek is the question about the character in front. A walk that took the
    peek for a part would read a peeked way as one that must take a character. A peek says that of the input rather than
    of the way. `_gated_by` reads the peek.
    """
    return (*way.gate.guards, *_items_of_way(way)) if isinstance(way, ir.AlternativeState) else _items_of_way(way)


def _ways_or_items(node: ir.Node) -> tuple[ir.Node, ...]:
    """
    The ways that a choice offers, or the items that an alternative performs. A walk calls this to descend in the
    machine's form and in the tree's form alike.

    A choice says its ways as `alternatives` in the machine's form, and as `items` in the tree's form. An alternative
    says its parts by name. A sequence says them in a row.
    """
    if isinstance(node, ir.ChoiceState):
        return node.alternatives
    return node.items if isinstance(node, ir.AltTree) else _items_of_way(node)


def _way_items(body: ir.Node) -> tuple[tuple[ir.Node, ...], ...]:
    """The ways `body` opens, as runs of items. `one-step-per-item` leaves a body holding these ways."""
    return tuple(_items_of_way(way) for way in (_inner_ways(body) if isinstance(body, ir.BODY_KINDS) else (body,)))


def _a_way_is_actions_a_call_and_a_continuation(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way is actions, the call it hands control to, and a production that continues.

    An edge of the machine is a push and a jump. The push is the continuation the machine will come back to. A way is
    therefore what the way does before handing control on, plus the call it hands to, plus the production that
    continues. The continuation holds what comes past the first call. A second call is where to continue, rather than a
    third thing to do.

    This counts by the way rather than by the item in the wrong place. The minting rewrites a way at a time. A way's
    tail after the split is a way of its own, and the same question goes to that way.
    """
    faults = []
    for name, production in grammar.items():
        for items in _way_items(production.body):
            calls = 0
            for item in items:
                if isinstance(item, ir.RefCall):
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
    Check that a body is a choice or a set. Those are the shapes the machine has a state for.

    A terminal is a set of characters and no more. Anything else is an ordered list of alternatives, a way of the
    machine apiece. A way is a gate to enter on and the actions it performs. An alternative is also the call it hands
    control to, and where the way continues past that call. The recovery rides the push. A loop is such an alternative
    too. The grammar says a run as the ways it holds. The state a run jumps back to is a choice like any other.

    This checks the shape throughout the body rather than at its top. A body is canonical where nothing inside it is the
    tree again. An alternative's actions hold no call, and what it calls is a name.
    """
    faults = []
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.CharSet):
            continue
        if not isinstance(body, ir.ChoiceState):
            faults.append(f"{name}: a body that is neither a set nor a choice of alternatives")
            continue
        for alternative in body.alternatives:
            if not isinstance(alternative, ir.AlternativeState):
                faults.append(f"{name}: a choice holding what is not an alternative")
            elif any(isinstance(action, ir.RefCall) for action in alternative.actions):
                faults.append(f"{name}: an alternative doing a call among its actions")
            elif any(
                held is not None and not isinstance(held, ir.RefCall)
                for held in (alternative.first, alternative.second)
            ):
                faults.append(f"{name}: an alternative handing control to a node that is not a name")
    return faults


_EVERY_BODY_IS_A_CHOICE_OR_A_SET = _Invariant("every-body-is-a-choice-or-a-set", _every_body_is_a_choice_or_a_set)


def _guard_past_an_action_at(actions: Sequence[ir.Node]) -> int | None:
    """The place a guard first comes past an action among `actions`, or `None` where none does."""
    acted = False
    for index, action in enumerate(actions):
        if isinstance(action, ir.GUARDS):
            if acted:
                return index
        else:
            acted = True
    return None


def _no_guard_comes_past_an_action(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no guard comes past an action. A way then asks its guards first. The performing parts come next. The call
    and where it continues come last.

    A guard decides. A guard reached past an action decides nothing the parse can act on. The action has already
    happened. Failing there kills the parse. It does not send the parse to another way. In front of the actions, a guard
    a way holds is a guard its gate could take. Behind them is a straight run of things that happen throughout. A way
    holding no guard at all passes trivially.

    A split can therefore cut a way into the part that takes characters and the part that takes none. Such a split walks
    the parts in order and hands a half the parts that belong to it. A guard sitting behind an action belongs to neither
    half. Putting a guard in either half would perform the action twice, or leave the action unperformed.
    """
    return [
        f"{name}: a guard comes past an action the way already performed"
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
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
        if isinstance(item, ir.CONSUMING):
            held = []
        elif isinstance(item, ir.GUARDS):
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

    The grammar has neither loss. A peek pins its set down, and both look-behinds ask about `ns-char`. The count of
    guards answering `COMPLETE` shows that the grammar holds no guard that admits throughout.
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
    The states a negative lookahead admits. Those are the characters the set does not hold. A set the peek does not pin
    down admits throughout.
    """
    spans = _peek_spans(guard.item, grammar)
    return spaces.COMPLETE if spans is None else spaces.not_characters(spans)


def _behind_admits(guard: ir.LookBehindGuard, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states a look-behind admits. Those turn on whether the character behind is an `ns-char`. `ns-char` is the set
    the grammar looks back at. Another set is no axis `GuardAnswers` names, and admits throughout.
    """
    spans = _peek_spans(guard.item, grammar)
    named = _peek_spans(ir.RefCall(name="ns-char", args=()), grammar)
    admits = spans is not None and spans == named
    return spaces.where(is_after_ns_char=True) if admits else spaces.COMPLETE


def _quantity(value: ir.Node) -> str | None:
    """
    The parse's quantity `value` reads. The name is a word. The answer is `None` where `value` reads no quantity.

    The indentation and the floor read either way. A read is the register the parse holds, or the parameter a call
    passed before the lowering that made it a register. Both forms are the same quantity.
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
# A reader takes the indentation and the floor either as the register the parse holds or as the parameter a call passed.
# By the time anything mints a comparison the parameters are gone.
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

    A shape `_COMPARES` admits throughout. It does not name a raise. The quantities run without bound, and no quantity
    is a coordinate. An axis exists where the grammar asks for one. A comparison asked here may name no axis the grammar
    states. Such a comparison is a question the space cannot put. A guard that admits throughout joins the guards that
    constrain nothing. Such a guard hides a case the grammar cannot decide.
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


def _accepted_part(part: ir.Node, grammar: dict[str, ir.Prod], known: dict[str, spaces.SubSpace]) -> spaces.SubSpace:
    """
    The states that a part of a way takes a character in, as a `spaces.SubSpace`. The answer is `NOWHERE` where the part
    takes none.

    A call answers from `known`, what the fixpoint below has reached for its production. A take of a set answers with
    that set. A take of the character its gate found answers throughout.
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
    way: ir.Node, grammar: dict[str, ir.Prod], known: dict[str, spaces.SubSpace], ways: _Ways
) -> spaces.SubSpace:
    """
    The states `way` consumes a character in, as a `spaces.SubSpace`.

    Read off what the way consumes and no more. **A guard says nothing here.** The gated space answers what admits a
    way. A consume names the set the grammar wrote it to take. The pair therefore comes off different halves of the way,
    and either can hold the other to account. Mixing the guard and the consume would narrow the answer by the gate in
    front, and the answer would then restate the gate. Yet a pair of answers that cannot disagree says nothing when they
    agree.

    A part contributes the states it consumes a character in. A part that must consume a character ends the walk. The
    walk reaches no part behind it. A part that may consume nothing leaves the walk going, and what sits behind it comes
    in too.

    This is empty where a way consumes nothing at all. That is the answer rather than a gap. A way that succeeds without
    consuming succeeds at any position. Whether a way can do so is `_split_ways`' answer already. This walk and
    `_split_ways` stay apart. A caller reaching such a callee continues and adds what sits behind it. The pair compose
    on that ground.
    """
    consumes = spaces.NOWHERE
    for part in _parts_of_way(way):
        if isinstance(part, ir.GUARDS):
            continue
        consumes = consumes | _accepted_part(part, grammar, known)
        if not _is_nullable(part, grammar, ways):
            break
    return consumes


def _accepted_body(
    body: ir.Node, grammar: dict[str, ir.Prod], known: dict[str, spaces.SubSpace], ways: _Ways
) -> spaces.SubSpace:
    """
    The states a production's body can begin taking a character in. That is the union of the ways where a body offers
    ways, and the characters a body holds where the body is a set. A body that is neither answers at any character. That
    says nothing rather than something untrue.
    """
    if isinstance(body, ir.ChoiceState):
        space = spaces.NOWHERE
        for way in body.alternatives:
            space = space | _accepted_way(way, grammar, known, ways)
        return space
    spans = _peek_spans(body, grammar)
    return spaces.ANY_CHARACTER if spans is None else spaces.characters(spans)


def _accepted_spaces(grammar: dict[str, ir.Prod]) -> dict[str, spaces.SubSpace]:
    """
    `{name: spaces.SubSpace}`, the states a production can begin taking a character in.

    This is a least fixed point from `NOWHERE`. A production reaching back to itself contributes nothing until some way
    of it says otherwise. A cycle in the grammar takes a character somewhere inside it. That take lifts the cycle, and
    the answers settle. Starting anywhere else would let a cycle decide itself. An unknown treated as a poison does not
    lift. A start that admits throughout would say a production accepts what nothing in it takes.

    A worklist drives this, rather than a sweep of the grammar per round. A production's answer can change only where a
    callee's has. A callee that has just grown therefore puts its callers back on the list, and the sweep does not ask
    the other names again. The sweep takes a name at a time, in the order the list gives them. A least fixed point does
    not depend on the order of the list.
    """
    ways = _split_ways(grammar)
    callers: dict[str, set[str]] = {}
    for name, production in grammar.items():
        for node in _held(production.body):
            if isinstance(node, ir.RefCall) and node.name in grammar:
                callers.setdefault(node.name, set()).add(name)
    known = {name: spaces.NOWHERE for name in grammar}
    pending = set(grammar)
    while pending:
        name = pending.pop()
        space = _accepted_body(grammar[name].body, grammar, known, ways)
        if space != known[name]:
            known[name] = space
            pending |= callers.get(name, set())
    return known


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
    Whether some input makes a way fail and hands it back. This reads the items in turn. It stops at what commits.

    A gate sits in front of the way. A way with a gate is therefore refused wherever the gate declines. Past a `(cut)`
    or inside a committed region a failure is the message rather than a way handed back. The walk stops counting there.
    A failure travelling out of a region that has not closed takes the message the region names. A failure out of a
    region that has closed goes back like any other.
    """
    if _does_a_gate_decide(way):
        return True
    open_regions = 0
    for item in map(_unheld, _items_of_way(way)):
        if isinstance(item, ir.PushMessageAction):
            open_regions += 1
        elif isinstance(item, ir.PopMessageAction):
            open_regions -= 1
        elif isinstance(item, ir.CutAction):
            return False  # past a cut a failure is the error it names. nothing behind it is handed back
        elif isinstance(item, ir.CommitWrapper):
            continue  # what it holds raises where it fails, the region being the same thing the pair writes
        elif not open_regions and _can_be_refused(item, grammar, seen):
            return True
    return False


def _does_a_gate_decide(way: ir.Node) -> bool:
    """
    Whether a gate sits in front of a way. The parse then enters the way only where the gate holds.

    A gate is a character question or a guard. Either is a question the machine asks before the way runs. A wrong input
    turns the way away and the choice goes on to the next. The behaviour of the way on entry does not matter.
    """
    return isinstance(way, ir.AlternativeState) and (bool(way.gate.guards))


_CAN_BE_REFUSED: ir.Question[bool] = ir.Question(
    "whether some input makes a match fail and hands it back. Matching and raising are the other outcomes.",
    {
        # An action does not go back. A gate that admitted an action and then saw it fail lied, and the interpreter
        # raises rather than declining.
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
            isinstance(node.count, ir.LitValue) and isinstance(node.count.value, int) and node.count.value <= 0
        )
        and _can_be_refused(node.item, grammar, seen),
        # A recursion reached again says nothing new. The walk already judged the way in at that position.
        ir.RefCall: lambda node, grammar, seen: node.name not in seen
        and _can_be_refused(grammar[node.name].body, grammar, seen | {node.name}),
        (ir.AlternativeState, ir.SeqTree): _can_a_way_be_refused,
        # A choice goes back only where the ways it offers go back. a way that matches is the choice matching.
        (ir.AltTree, ir.ChoiceState): lambda node, grammar, seen: all(
            _can_be_refused(way, grammar, seen) for way in _ways_or_items(node)
        ),
        # A committed region raises where what it holds fails. the region does not go back.
        ir.CommitWrapper: False,
        (ir.MaxWrapper, ir.RecoverWrapper, ir.TokenWrapper, ir.Wrapper): lambda node, grammar, seen: (
            node.item is not None and _can_be_refused(node.item, grammar, seen)
        ),
        ir.BindTree: lambda node, grammar, seen: _can_be_refused(node.cond, grammar, seen),
        # The specialization settles a switch. A switch goes back only where the branches it could take go back.
        ir.CaseTree: lambda node, grammar, seen: all(
            _can_be_refused(item, grammar, seen)
            for item in [branch.item for branch in node.branches] + ([node.default] if node.default else [])
        ),
    },
)


def _every_option_is_reachable(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that some input reaches a way a choice offers. An input can refuse the ways in front of such a way.

    A choice goes on to the next way exactly where the parse refuses the way in front. A way that no input refuses is
    therefore the last way the machine takes. Such a way matches throughout, or its failure is the error a commit names.
    A way behind it is a way nothing can enter. Backtracking hides the case where the way matches. The way matches and
    the continuation fails. The parse returns and tries the next way. A machine that does not return loses the ways
    behind.

    A choice may therefore hold a single such way, and it must come last. There it is the fallthrough a choice ends in.
    A pair of such ways is worse than undecidable. The second is unreachable. The grammar leaves which of the pair the
    author meant open.

    A different question asks what the gates *leave* undecided, and no invariant here asks it.
    """
    faults = []
    for name, production in grammar.items():
        for node in _held(production.body):
            offered = (
                node.alternatives
                if isinstance(node, ir.ChoiceState)
                else node.items if isinstance(node, ir.AltTree) else ()
            )
            for way in offered[:-1]:
                if not _can_be_refused(way, grammar):
                    faults.append(f"{name}: a way no input refuses, with a way behind it")
    return faults


_EVERY_OPTION_IS_REACHABLE = _Invariant("every-option-is-reachable", _every_option_is_reachable)


def _mint_continuations(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Give the parts past a way's call a production of their own. A way is then actions, a call, and where to continue.

    An edge of the machine is a push and a jump. The push says where to come back to, and the jump goes. The parts past
    the call therefore belong to that continuation rather than to the way. `a P1 b P2 c` becomes `a` and the call `P1`.
    A production of its own then holds `b P2 c`. That splits the same way until no part comes past a call. A way ending
    in a pair of calls is already that shape. The second call is where to continue rather than a third thing to do. This
    function mints no production for the second call.

    A scope opened before the call and closed after it comes apart here. `lower-scopes` already split that scope apart.
    A Push and Pop pair sits on the stack the parse holds, rather than in the frame of the running match. The
    `PushCodeAction` stays in the way, and its `PopCodeAction` moves into the continuation. The pair still finds one
    another.
    """
    minted = {}

    def split(items: tuple[ir.Node, ...], owner: str) -> tuple[ir.Node, ...]:
        for index, item in enumerate(items):
            if not isinstance(item, ir.RefCall):
                continue
            rest = items[index + 1 :]
            if not rest or (len(rest) == 1 and isinstance(rest[0], ir.RefCall)):
                return items
            name = namer.fresh(owner)
            parts = split(rest, owner)
            minted[name] = ir.Prod(
                grammar[owner].number, name, (), parts[0] if len(parts) == 1 else ir.SeqTree(items=parts)
            )
            return (*items[: index + 1], ir.RefCall(name=name, args=()))
        return items

    def way(node: ir.Node, owner: str) -> ir.Node:
        items = split(node.items if isinstance(node, ir.SeqTree) else (node,), owner)
        return items[0] if len(items) == 1 else ir.SeqTree(items=items)

    def body(node: ir.Node, owner: str) -> ir.Node:
        if isinstance(node, ir.BODY_KINDS) and not isinstance(node, ir.SeqTree):
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
    way comes back from and the second is where the way goes then. A recovery rides the member of the pair holding the
    call it protects. A way with a single call and a recovery is therefore a tail-goto like any other.

    The gate is empty here. The parse enters a way on a question about the character in front, and the hoist answers
    that. Until the hoist runs, the parse tries the alternatives in order.
    """
    items = way.items if isinstance(way, ir.SeqTree) else (way,)
    calls = tuple(item for item in items if isinstance(item, ir.RefCall))
    actions = tuple(item for item in items if not isinstance(item, ir.RefCall))
    first, second = (calls[0], calls[1]) if len(calls) > 1 else (None, calls[0] if calls else None)
    return ir.AlternativeState(gate=ir.GatePart(), actions=actions, first=first, second=second, recover=recovery)


def _build_alternatives(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a body in the words of the machine. That is a set of characters, or the ordered list of alternatives the parse
    takes one of.

    This is a change of form and not of meaning. By the time it runs, a body already *is* a choice of ways. A way is
    actions, a call and a continuation. The interpreter reads an alternative as exactly the sequence the tree wrote.
    That is the gate's guards, then the actions, then the call with its continuation. A question from here on rests on
    that.
    """

    def told(production: ir.Prod) -> ir.Prod:
        alternatives: tuple[ir.AlternativeState, ...]
        body = production.body
        if isinstance(body, ir.CharSet):
            return production
        if isinstance(body, ir.RecoverWrapper):
            alternatives = (_as_alternative(body.item, recovery=body.recovery),)
        elif isinstance(body, ir.AltTree):
            alternatives = tuple(_as_alternative(way) for way in body.items)
        else:
            alternatives = (_as_alternative(body),)
        return dataclasses.replace(production, body=ir.ChoiceState(alternatives=alternatives))

    return {name: told(production) for name, production in grammar.items()}


def _every_ungated_way_has_actions_or_a_call(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a way with no gate does actions or makes a call, and not both. A gate can then still reach the way.

    A way with no gate is a way still waiting for one. A pair of ways gives it one. The first hoists a guard up out of
    its callee. The second writes the callee's ways into it. Both need the callee's guards to reach where the parse
    enters the way. The way's actions tell the pair apart. A guard crosses an action, or it provably stays put.
    `GUARD_CROSSES_ACTION` says which. An action that sits in front of a guard can refuse it, and such a guard is a
    guard nothing can bring up. Such a way is a way nothing can gate.

    This check asks nothing of a way that has a gate. The parse enters such a way on the questions that way asks. The
    way performs its actions, and then calls. The machine runs that as a single edge. `every-conditional-way-is-gated`
    at none leaves no ungated way, and this check then has no way to judge.

    A continuation is not a call. The parse enters a continuation where the callee left off, rather than where the way
    began. A way that acts and then hands control on to a continuation therefore passes.
    """
    return [
        f"{name}: an ungated way acts before it calls. A guard of the callee cannot reach those actions."
        for name, way in _ungated_ways(grammar)
        if way.first is not None and way.actions
    ]


_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL = _Invariant(
    "every-ungated-way-has-actions-or-a-call", _every_ungated_way_has_actions_or_a_call
)


def _mint_call_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Give the call a way makes past its actions a state to itself. The way keeps its gate and hands control on. The call
    and where it comes back to are the minted state's way.

    A way has a gate, and the parse asks that gate where it enters the way. A call made past the way's actions is
    therefore a call whose callee the parse enters somewhere else. Minted, the call is the first thing its way does. The
    gate that admits the way and the gate that admits what it calls then sit at the same position.

    The way keeps its gate, the actions and a tail call. A tail call is no push, and the scopes close where they closed.
    The recovery goes with the call, and rides the push the minted state makes.
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

    cut = {}
    for name, production in grammar.items():
        body = production.body
        if not isinstance(body, ir.ChoiceState):
            cut[name] = production
            continue
        told_ways = tuple(told(name, way) for way in body.alternatives)
        cut[name] = dataclasses.replace(production, body=ir.ChoiceState(alternatives=told_ways))
    return {**cut, **minted}


def _mint_guard_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Give the parts from a way's first late guard onward a state of their own. The way keeps its gate and the parts up to
    there, then hands control on. The guard and what sits behind it are the minted state's way.

    This is a cut and not a reordering. That is why it is safe. A way's parts run in order. The parse enters the tail as
    a production where the way left off. The tail therefore runs the same parts in the same order. The guard asks at
    exactly the position it asked at before. A guard moved past a consume would ask about another character. This has to
    know nothing of an action's take or of its writes, and no condition says which guards may go.

    The way keeps its gate, the actions and a tail call. A tail call is no push, and the scopes close where they closed.
    The call the way made and the recovery riding it go with the tail.

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
            body = production.body
            if not isinstance(body, ir.ChoiceState):
                cut[name] = production
                continue
            ways: list[ir.AlternativeState] = []
            for way in body.alternatives:
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

    The parse asks a gate where it enters the way. `no-guard-comes-past-an-action` puts a guard a way holds in the run
    before the way performs anything. The guard and the gate therefore sit at the same position, and the gate is the
    half a caller can see. A guard left among the actions asks its question only when the parse enters the way. The gate
    decides that entering.

    The check reports a fault per guard rather than per way. A way holding a pair of guards owes a pair of moves.
    """
    return [
        f"{name}: a way asks a guard among its actions. The gate of that way asks at the same position."
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
        for action in way.actions
        if isinstance(action, ir.GUARDS)
    ]


_EVERY_GUARD_IS_IN_A_GATE = _Invariant("every-guard-is-in-a-gate", _every_guard_is_in_a_gate)


def _hoist_guards_to_gates(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Move the guards a way asks out of the actions and into the gate.

    A guard takes no width. Asking at the gate and asking where the guard sits are the same question. Anything between
    the pair could change the answer. `no-guard-comes-past-an-action` leaves a guard in the run before the way performs
    anything, and the run between is empty. That is the argument. This therefore needs no account of which action writes
    a value a comparison reads, and no account of the commits and their places.

    The gain is that the question sits in the field a caller can see. A caller reaches a guard among the actions only
    after it enters the way. In the gate the guard decides that entering. A lift can also take the guard to the ways
    that call the production.
    """

    def hoisted(way: ir.AlternativeState) -> ir.AlternativeState:
        moving = []
        for action in way.actions:
            if not isinstance(action, ir.GUARDS):
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=ir.ChoiceState(alternatives=tuple(hoisted(way) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }


def _merge_gate_peeks(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a gate's questions about the character in front of it as one.

    A pair of peeks in a gate ask about the same character at the same position. The sets therefore say between them
    what a single set says. The `LookGuard`s together admit the intersection of their sets. The set any `NegLookGuard`
    refuses stays refused. A gate holding both admits the first less the second. The gate keeps its other guards. They
    ask about the character behind, where the parse is in the line, and how the indentation compares.

    A gate may read ahead twice where no single set says both reads. Such a gate is a fault rather than a shape to leave
    in place. An `EndOfStreamGuard` holds no set. It does not fold into a `LookGuard`. It does not sit beside a
    `LookGuard` either. The machine would ask twice about a character the parse reads once. The grammar would not say
    which answer the machine acts on.
    """

    def merged(way: ir.AlternativeState) -> ir.AlternativeState:
        ahead = [guard for guard in way.gate.guards if isinstance(guard, ir.LOOKS_AHEAD)]
        looks = [guard for guard in ahead if isinstance(guard, ir.LookGuard)]
        nots = [guard for guard in ahead if isinstance(guard, ir.NegLookGuard)]
        if len(ahead) < 2:
            return way
        if len(looks) + len(nots) != len(ahead):
            kinds = ", ".join(sorted(type(guard).__name__ for guard in ahead))
            raise AssertionError(
                f"a gate reads ahead as {kinds}. That form asks a pair of questions about a single character."
            )
        rest = [guard for guard in way.gate.guards if not isinstance(guard, (ir.LookGuard, ir.NegLookGuard))]
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=dataclasses.replace(
                    production.body, alternatives=tuple(merged(way) for way in production.body.alternatives)
                ),
            )
        )
        for name, production in grammar.items()
    }


def _every_gate_looks_ahead_at_most_once(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a gate reads the character in front of the parse at most once.

    The lookahead guards ask about the same character at the same position. A pair of them in a gate is therefore a
    single question said twice. A pair of `LookGuard`s is the set they both admit. A `LookGuard` beside a `NegLookGuard`
    is the set the first admits and the second refuses. A pair of `NegLookGuard`s is the set neither admits. A gate that
    keeps them apart makes the machine ask twice what a single question answers. A question about the set admitting a
    way then takes the guards together first.

    This covers the lookahead. A look behind is about something else. So are where the parse is in the line and how the
    indentation compares. A gate may hold such a guard beside its lookahead.
    """
    return [
        f"{name}: a gate reads what is in front of it {held} times, where once would say the same"
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
        for held in (sum(isinstance(guard, ir.LOOKS_AHEAD) for guard in way.gate.guards),)
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
    refused, rather than on a question of its own. A machine has to tell the ways in front apart. This function tells
    those ways apart once, and callers reuse that answer.
    """
    return {
        name: production.body.alternatives[:-1]
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState) and len(production.body.alternatives) > 1
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

    A machine takes a way by asking the gate before entering. Take a way the gate says nothing about. The machine would
    have to try that way and give it back. The gate's contents come second. A guard is a question the machine can put
    where the way begins. A guard names the character in front or behind. The guard also asks whether more input
    remains, the line position, or how indentation compares. `every-peek-is-a-character-set` says the machine can answer
    at all. That invariant holds of a lookaround wherever it appears, and a gate is such a place.

    The question is separate from the place somebody asked it. Take a guard the ways entering this production asked.
    That guard asks at the position where the parse enters this way. `_asked_where_entered` says so. A hoist that takes
    a guard up to the callers moves the guard rather than losing it. A way is therefore gated where its gate holds a
    guard *or* the paths into that way asked one. `every-consume-is-protected-by-a-gate` accepts a question that a call
    further up asked in the same way.

    A pair of shapes are not a decision. This asks nothing of that pair. The first is a way nothing chooses to enter.
    That is a body offering a single way. A caller reaches it by calling the production and not by picking a way. A
    consuming production opens a scope, and its continuation pops that scope. This continuation is such a way. The root
    and the recovery are such ways as well. The second is the last way of a choice. `_decided_ways` calls that way the
    choice's else. An empty gate there is the fallthrough. The parse still enters the choice's last way where no gate
    fired.

    Whether the gates of a choice tell its ways apart is a later question.
    """
    return [
        f"{name}: a way nothing has asked anything about, where another way sits behind it"
        for name, _way in _ungated_ways(grammar)
    ]


_EVERY_CONDITIONAL_WAY_IS_GATED = _Invariant("every-conditional-way-is-gated", _every_conditional_way_is_gated)


# The state the questions below share, per grammar somebody has asked them about. That is the grammar itself and the
# tables. A table is a fixed point over the whole grammar, and the questions below read the tables stage by stage.
_TABLES: ir.Kept = {}


def _leaf_tables(
    grammar: dict[str, ir.Prod],
) -> tuple[dict[str, spaces.SubSpace], dict[str, tuple[bool, bool, bool]], _Entered]:
    """
    Work out `(accepted, ways, entering)` once for a grammar and keep the result. `accepted` holds the states a
    production begins taking in. `ways` says whether a production consumes or calls. `entering` holds the questions the
    parse asks on entry.
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
        if not isinstance(production.body, ir.ChoiceState):
            continue
        for way in production.body.alternatives:
            for part in _parts_of_way(way):
                if isinstance(part, ir.GUARDS):
                    continue
                if not _is_nullable(part, grammar, ways):
                    if not isinstance(part, ir.RefCall):
                        found.append((name, way))
                    break
    return found


def _gated_by(path: Iterable[ir.Node], way: ir.AlternativeState, grammar: dict[str, ir.Prod]) -> spaces.SubSpace:
    """
    The states in which a parse may enter `way` along a path into its production, as a `spaces.SubSpace`.

    `_asked_where_entered` hands back a path. That path holds the guards the ways leading here asked since the last
    take. A caller that consumed before its call asks about somewhere else. The parse asks the way's gate at that same
    position, and that gate is part of the set admitting the way. The set therefore takes the path's guards and the gate
    together.
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
    that is ordinary. `l-folded-content` decides whether a space is here, and its ways both continue into the same tail.
    The way that found a space consumes it. Past that the parse knows no more, and a space may follow again. The way
    that found none consumed nothing, and its `NegLook{' '}` still speaks where the parse enters the tail. The tail's
    gate admits a tab or a space. That gate is right on the first route and over-wide on the second. The tail consumes
    their union. A narrower union would say the way consumes characters nothing brings it. A wider union would say the
    parse enters the way on characters the way does not consume.

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

    A machine takes the first way whose gate holds and does not come back. A pair of ways alike but for their gates are
    therefore a single way reached along a pair of routes. `s-separate-lines` runs its flow prefix at a line start and
    at the end of the stream. Its ways perform the same actions and call the same productions. The same thing happens
    when either gate answers first, and the parse reads the same input. The input decides nothing here. The gates cannot
    become a single gate. A gate holds where its guards hold together, and the pair of ways asks that either gate hold.
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

    Of the ways a pair can relate, only apart and together are of any use. **Apart** is where no state enters both and
    the input itself says which to take. **Together** is where the states enter both. The input says nothing there.
    Something else has to tell the ways apart. **Half apart** is where the machine has to tell a state in the overlap
    from a state just outside it. That shape makes a machine ask twice where it should ask once. A later step would have
    to name the shape.

    A grammar passing this check holds no pair half apart. The parse enters both ways of a remaining pair in exactly the
    same states. The determinizing has to settle that shape, and can do so once rather than once per way it half-covers.

    This reads a way's gate as `accepted-and-gated-charsets-are-equal` reads it. The answer crosses the states the way's
    gate admits with the states the paths into its production ask for together. A guard that a call hoists up asks at
    the position where the way begins. The guard narrows the ways of the choice alike, and an overlap outside it is an
    overlap no parse reaches.

    The choice's else is not among the pair. This asks nothing of it. The else has no gate, and admits any state at all.
    Asked, the else would half overlap the ways in front of it that ask anything. Splitting could not settle that. The
    parse enters the else in the states the ways in front left. That is a shape rather than a question. A machine has to
    tell apart the ways it decides between. The else is where the machine has failed at that.

    The checker is exact over the grammar here. `_admits` puts the guards it holds without approximation. A gate is a
    set of characters at this position crossed with the state the parse is in. A `GuardAnswers` holds both. A pair read
    together is therefore together, rather than a guess the determinizing would inherit. A guard admitting throughout
    would make that reading a guess, and the grammar holds none. `_admits` names the kinds a guard can hold.
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


# The states a parse enters a decided way in. `_TABLES` holds a table per grammar. The walk behind a table is expensive,
# and the askers ask for it stage by stage. `_TABLES` keeps the tables the way `_leaf_tables` keeps its own, and no
# asker walks again.
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
    `(name, at, behind)`. A way that a gate must choose between sits beside the way behind it. The parse enters both in
    exactly the same states. The pair of ways do different things there. There is an entry per way behind. A way appears
    here once per way the machine cannot tell it from.

    A pair of ways doing the same thing is no conflict, and neither appears here. A way the parse enters in no state at
    all conflicts with nothing. A conflict needs a state where the machine has to choose, and that way has none. A pair
    of ways alike but for their gates is a single way reached along a pair of routes, and `_does_the_same` says so. The
    same thing happens when either gate answers first, and the input decides nothing between the gates.

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
    The production holding a way that the parse enters in exactly the same states as a way behind it. Such a way holds a
    name. A production holding a pair of them is therefore named twice.

    `_conflicts` gives the conflicts. A way appears there once per way the machine cannot tell it from. Here the way
    appears once. The deduplication turns on where the way sits in the production. An asker counts ways and reports the
    name rather than the position, and this function hands back the name.
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
    Check that no pair of ways something decides between shares a state.

    The input says nothing about which way to take where a pair share a state. A machine reading a character and asking
    a question of it gets the same answer on both. The machine takes the way that comes first and gives it back where
    that was wrong. The whole shape removes that backtracking, and the ways then have gates.

    `no-choice-ways-partially-overlap` leaves a pair sharing their states or sharing none. This question is simple on
    that ground. Without it a way could share some of another's states and not the whole set. Telling those apart would
    be a question about the overlap rather than about the ways.

    This counts per way rather than per pair. The gating count does the same. A way the parse enters in the states of a
    way behind it is a way no gate can point the machine to. Settling it settles that way. This asks nothing of the
    choice's else. The parse takes the else where no way in front fired, rather than on a character the input picks it
    by.
    """
    return [
        f"{name}: a parse enters a way in the states it enters the way behind" for name in _conflicted_ways(grammar)
    ]


_EVERY_CHOICE_WAY_IS_DIFFERENT = _Invariant("every-choice-way-is-different", _every_choice_way_is_different)


def _conflicted_productions(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The productions a machine cannot find its way through, as names.

    A production holding a pair of ways the parse enters in the same states is such a production. So is a production
    whose way ends in such a call. A way ending in a call hands its end to the callee. A way ending in such a call is
    undecidable in the same way. Reaching such a production anywhere else does not spread it. A call with something
    behind it comes back. The way holds what the call comes back to.
    """
    return _ending_in(grammar, set(_conflicted_ways(grammar)))


def _ending_in(grammar: dict[str, ir.Prod], held: set[str]) -> set[str]:
    """`held` plus the productions offering a way that ends in one of them. The sweep then goes on up."""
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
    Check that a way calls a conflicting production as its last step.

    A conflict the position cannot decide waits on the text behind it. The parse reads that text inside the call. A way
    that continues past the call keeps the continuation in its own frame. The call cannot reach that frame. The machine
    then chooses before it reads the text that decides. `every-choice-way-is-different` counts that backtracking a frame
    up.

    This check counts call sites. A step lowers the continuation of a call site into the callee. The call then ends the
    way. A conflicting production called last holds its own future. The flattening can then walk to the gate inside that
    production.
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

    A production with a way ending in such a call is here too. That is the road the continuation travels. The way ending
    in such a call holds nothing behind it either. The continuation has to come from further up again. This step makes
    copies on the way down, and those copies pass the continuation along.
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
    Say a call to a conflicting thing that continues somewhere. Restate the call as a call to that thing. Move the
    continuation inside that thing. `A = |actA ->B contA| |...|` with `B` conflicting becomes `A = |actA ->B'|`. `B'` is
    `B` with `contA` run past the body of `B`.

    A conflict the machine cannot find its way through is settled by what comes after. The caller holds that remainder
    until this moves it. Once this moves it, the ways of the copy end where the whole call ends. The gate telling those
    ways apart then sits inside the production holding them. A frame up, those ways could reach nothing of the gate.

    This recurses the continuation into the callee rather than pushing it in front. A way of `B` ending in nothing
    continues to `contA`. A way ending in `R` continues to `R` with `contA` inside it the same way. A tail therefore
    stays a tail, and this adds no frame. A copy a pass makes takes the same continuation. That holds the copies to a
    copy per production and continuation, rather than a copy per stack a parse could hold. The same bound ends the
    recursion through the ways. This mints a copy once for a name and a continuation. A way may reach a name that this
    step is already copying under the same continuation. Such a way takes the copy already begun. A terminal offers no
    way to continue from, and this cannot copy it. This raises there rather than write the push that call would need.

    This moves into the conflicts that nothing inside can settle. Those are the conflicts whose ways part over a
    continuation no input hands back. A production between such a conflict and a call site holding a continuation is
    here too. Some input handing the continuation back reaches the way behind, and the parse settles the pair at that
    position. A move buys nothing there. This moves into the conflicting production instead. That trades a call site for
    a copy of the sites inside the copy. A conflict over another gets a copy per conflict below it.

    This leaves a way with a recovery untouched. The recovery rides the call it protects. A cut unwinding out of that
    call stops at a place the frame fixes, and this would take that frame away.

    This runs until nothing moves. A pass takes a continuation down through a call. The copies a pass makes take
    continuations from outside the copies. A pass therefore leaves the same question a level in. The grammar holds a
    finite set of productions and continuations. The loop stops when no new pair of them appears.

    A pass ends with a sweep of the productions no root reaches. The step merges duplicates once the last pass ends.
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
        # `first` and no `second` is a shape `AlternativeState` refuses where it is built. A way with a `first` has a
        # `second`, and asking after both would be asking twice.
        if way.first is None or way.recover is not None:
            return way
        if way.first.name not in wanted or way.second is None:
            return way
        # A tail call, which is `second` alone. what was called holds what came after it, and nothing is behind it
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

    A pair of ways doing the same actions in a different order raises rather than answering. The actions are a run and
    not a set. The emission in front of a code push is not the emission behind it. A pair of orders is therefore a pair
    of different things, and a question written for ways that agree has no answer about them.
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
    """The bits `guard_answers` holds, in the order `spaces.GuardAnswers` names them."""
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

    A set may hold more than a single kind. The scalar content classes hold the byte-order mark in their upper range
    beside the content characters. A consume of such a set therefore reads as any of the kinds.

    That is deliberately wider than the parse for the mark. A mark takes no column only where the consume names it and
    no more. `interpreter._is_only_the_mark` asks the set rather than the character. A set holding the mark among a
    scalar's content therefore takes a column there. The checker below admits both outcomes for it. A space wider than
    the parse says a parse may reach a state it cannot reach. That costs the sharpness of the answer a reader takes off
    it, and cannot make an assertion fire. The checker sees the characters of a set. The grammar wrote the consume for a
    particular kind. The checker cannot see which kind.
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

    A break puts the parse at the start of the next line. The characters the line before consumed belong elsewhere. The
    mark takes no column and leaves the line start as it was, and a length reads across no mark. A character that is
    neither a break nor the mark advances the column by the characters taken. That count is the consumed length.

    `behind` names what may sit behind the parse after the consume. A caller passes `behind` where that set is narrower
    than the set the consume takes. A trimmed consume gives its trailing characters back. The parse therefore has no
    such character behind it. The caller passing `behind` is that consume. `check_dead_code` declares that kind unbuilt.
    """
    breaks, mark, rest = _taken_apart(spans)
    answers = _ns_answers(rest if behind is None else behind, grammar) if rest else ()

    def move(
        guard_answers: spaces.GuardAnswers, held: spaces._Quantities
    ) -> Iterable[tuple[tuple[bool, ...], tuple[int, ...]]]:
        if breaks:
            yield (True, False, False, True), held._replace(column=0, consumed_length=0)
        if mark:
            # A mark takes no column. The length is left open rather than said to be the mark's single character.
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
    The values a length may hold. They run from none up to the top. The empty length is the floor, and the top is the
    length the last consume took.
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
    """The answers a maximal consume of the set leaves a parse with. The limits of the line bound the run."""
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
    """The answers putting `action`'s level in force as the indentation leaves a parse with."""
    level = action.level
    if level == ir.LitValue(value=None):
        # Nothing may measure against a null, which `interpreter._indent` refuses. The axes reading the indentation are
        # left as they were rather than opened.
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
        # The detected indent is no less than `0`. what is pushed is no less than what it displaces
        return _moved("n", lambda quantities: range(quantities.n, spaces.TOP + 1))
    raise ValueError(f"this question covers no indentation of {level}")


def _popped_indent(action: ir.PopIndentAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    The answers a parse holds once it takes off the indentation `action` names. `action` undoes a push, and that push
    displaced an indentation. This function restores that indentation.

    The push worked the level out *from the indentation this restores*. A level can name that indentation. The
    arithmetic then runs backward and restores the indentation exactly. A level naming the column, the floor or a
    literal says nothing of the indentation the push displaced. The same push runs over a wider indentation, an equal
    indentation and a narrower one. This function therefore opens the axes reading the indentation.
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

    Positions of opens do not fall going up the stack. A character taken since the inner open counts as one taken since
    the outer open. An outer open already answering true keeps answering true. An outer open keeps its own answer where
    the parse takes nothing since the inner open closes. The parse took something before it reached the inner open.
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
    if not isinstance(action.value, ir.LitValue):
        raise ValueError(f"this question covers no floor of {action.value}")
    written = getattr(action.value, "value")
    assert isinstance(written, int), f"the floor becomes {written!r}, and that value is no indentation"
    return _moved("floor", lambda _quantities: (written,))


def _cleared_a_variable(action: ir.ClearVarAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """
    Clearing a variable ends the value the `action`'s slot held. Past the end of the slot nothing holds a value. The
    axes reading that slot then speak of nothing. Clearing opens the axes without freezing them. A pushed null
    indentation does the opposite. There the pop puts the value back, and the axes go on describing that value. A clear
    ends the value outright, and the next construct brings a different one. The interpreter raises on a read past the
    clear. The clear only says the space describes no value.
    """
    return _MOVES_NOTHING if action.param != "f" else _moved("floor", _any_length)


def _raised_the_floor(action: ir.IncreaseAction, _grammar: dict[str, ir.Prod]) -> _Moved:
    """Give the answers a parse holds once the floor rises to the column. `(increase)` raises the floor that way."""
    if action.param != "f":
        raise ValueError(f"a step increases {action.param} rather than the floor")
    return _moved("floor", lambda quantities: (max(quantities.floor, quantities.column),))


# An action changes the states a parse may be in. A kind here does not pass on the word of its name. The kinds
# `_changed_nothing` handles write no axis. The axes are the token under construction, what a failure comes to, and what
# may not match at a line start. A kind the table does not name raises. An action added to the IR therefore gets an
# answer here. It does not pass as an action that changes nothing.
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
        # The axis holds the token under construction and where the cuts fall in its runs.
        ir.EmitAction: _changed_nothing,
        ir.PushCodeAction: _changed_nothing,
        ir.PopCodeAction: _changed_nothing,
        ir.OpenProvisionalAction: _changed_nothing,
        ir.MarkProvisionalAction: _changed_nothing,
        ir.RetypeProvisionalAction: _changed_nothing,
        ir.InjectBeforeAction: _changed_nothing,
        ir.CommitProvisionalAction: _changed_nothing,
        ir.ErrorAction: _changed_nothing,
        # The axis holds the place a failure comes to and where the parse answers it.
        ir.PushMessageAction: _changed_nothing,
        ir.PopMessageAction: _changed_nothing,
        ir.PushRecoveryAction: _changed_nothing,
        ir.PopRecoveryAction: _changed_nothing,
        ir.PushBackTrackAction: _changed_nothing,
        ir.PopBackTrackAction: _changed_nothing,
        ir.CutAction: _changed_nothing,
        # The axis holds the budget a committed consume may not pass and what may not match at a line start.
        ir.OpenWindowAction: _changed_nothing,
        ir.CloseWindowAction: _changed_nothing,
        ir.SetForbiddenAction: _changed_nothing,
        ir.ExcludeAtAction: _changed_nothing,
    },
)


# The answers an action leaves a parse with, kept per grammar and per action. Working an answer out walks
# `spaces.ALL_GUARD_ANSWERS` and the tuples an entry holds. That is the cost of the whole checker. The action and the
# place the parse reached decide the answer. So does the grammar that resolves the sets the action names. The space the
# action runs in does not come into it. This therefore works an answer out once and reads it per space after. The space
# only says which answers are live and what characters ride along. `_TABLES` keeps this against the grammar the way it
# keeps `_leaf_tables`. An action is a value. The same action appears in a pair of stages where the reference under it
# denotes different sets. A cache holding just the action would answer the second stage with the spans of the first.
_WHERE_AN_ACTION_LEAVES_A_PARSE: ir.Kept = {}

# The answer in a space. The key is the grammar, the action and the space. Reading it off the table above is a union
# over the answers the space holds. That is cheap beside working the action out. The fixpoint asks it over and over
# while settling. A space that has stopped growing keeps reaching the same actions.
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

    A way names what it calls in `first` and `second`, and the fixpoint takes a space along those. A way matches
    anything else it names. A lookaround asks about such an item. An exclusion forbids such an item.
    `interpreter._is_forbidden_here` probes an exclusion at the line starts, where the grammar names it in no way. A cut
    unwinds to such an item as its recovery. A call reaches none of those. The caller therefore does not say where a
    parse enters such a production. A parse can begin it at any position.
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
    `named`, the productions it references, and the productions those reach in turn.

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
    nothing. The pair feeds itself. Both grow together from empty until neither moves.

    A body that offers no way is the characters a terminal takes. Such a body leaves its entry with a character
    consumed.

    This reads a parse starting at the root. Entry starts at the productions a parse enters by name and the productions
    a match enters. A production the root does not reach has no entry. Seeding that production with a start of its own
    would describe a caller starting there, rather than the machine that ships.
    """
    named = _entered_by_name(grammar) | _entered_by_matching(grammar)
    entry = {name: (spaces.COMPLETE if name in named else spaces.NOWHERE) for name in grammar}
    left = {name: spaces.NOWHERE for name in grammar}
    # What each way's own gate admits, and what a terminal takes, said once. Neither depends on how far the fixpoint has
    # got, and both cost a walk of the guards the way asks.
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
    # Who has to be worked out again when a production's exit grows. It is whoever calls it, and not the whole grammar
    # once per round.
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


def spaces_of_ways(
    grammar: dict[str, ir.Prod],
) -> dict[int, tuple[str, int, spaces.SubSpace, spaces.SubSpace]]:
    """
    `{id(the way): (its production's name, where it stands among that production's ways, its entry, its exit)}`.

    An assertion checks against these spaces. This function keys a way's spaces by the identity of that way. Whoever
    holds a way in hand can then ask about it. A way is a frozen dataclass. A pair of productions offering the same way
    are equal. Keying by value would hand the spaces of a production back for the way of another. A way's entry is its
    production's entry crossed with the way's gate. A way's exit is its entry taken along the way's parts. A call in the
    way resumes from the exit of its callee.
    """
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

    A path gathers the guards asked on the way here, and the parse asks a way's gate with them. Such a way's gate cannot
    hold beside those guards. The path therefore does not take that way. That is ordinary. `l-document-prefix` offers a
    way that takes a byte order mark and a way that takes nothing. The path reaching `l-document-prefix` at the end of
    the stream can only take the second way. The stream holds no mark there to take. A path that can take *none* of them
    is not ordinary. That is a call no input can complete, and this counts it.

    This asks the productions whose ways are leaf ways throughout, the productions that answer by themselves. A way
    whose gate a path rules out counts as reached where its production reaches some other way. This asks a way taking no
    character as much as a way taking a run.
    """
    _accepted, _ways, entering = _leaf_tables(grammar)
    faults = []
    for name, production in grammar.items():
        ways = production.body.alternatives if isinstance(production.body, ir.ChoiceState) else ()
        if not ways or any(way.first is not None or way.second is not None for way in ways):
            continue
        for path in entering[name]:
            if not any(_gated_by(path, way, grammar) for way in ways):
                faults.append(f"{name}: a path into the production reaches no way the production offers")
    return faults


_EVERY_PATH_REACHES_A_LEAF_WAY = _Invariant("every-path-reaches-a-leaf-way", _every_path_reaches_a_leaf_way)


class _Crossing:
    """
    Whether a parse may ask a guard in front of an action that came before it. There is an entry per pair of kinds.

    An entry says first whether the table names the pair. Somebody worked out a pair the table names. A pair the table
    leaves out is a pair nobody has worked out. A walk taking an unnamed pair for `False` would look settled while it
    was only ignorant. An unnamed pair therefore refuses the move, and the table records that it came up. The gate
    reports both of these as faults.

    - **a pair consulted with nothing recorded**. The table is then behind what the grammar holds.
    - **a pair recorded that nothing consulted**. The table then claims to know something nobody asked.
      That is a guess about the grammar, exactly like an `ir.Question` handler nothing reaches.

    The pair of faults pins the table to the pairs that occur. The gate reports a missing pair and reports a spare one.
    The table cannot quietly grow a claim.

    A named pair says `True` where asking early is the same question. It says `False` where asking early differs. An
    entry holds a question where the kinds do not settle the pair. The effect of the pair then depends on which guard
    and which action. The table works such an entry out like any other entry. The answer says the instances decide. Such
    an entry is therefore neither of the faults above.
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
    Whether a `(guard, action)` pair may cross.

    This asks a pair behind a refusal too. Stopping at the first refusal would leave those unconsulted. The table's
    faults are for saying which pairs the table is missing.
    """
    answered = [GUARD_CROSSES_ACTION.may_cross(guard, action, grammar) for guard, action in pairs]
    return all(answered)


def _does_name_the_slot(node: object, param: str) -> bool:
    """Whether `node` reads the slot `param` names, as a value the parse holds or as a parameter a call passes."""
    if isinstance(node, (ir.GlobalValue, ir.ParamValue)):
        return node.name == param
    return isinstance(node, ir.Node) and any(
        _does_name_the_slot(getattr(node, field.name), param) for field in dataclasses.fields(node)
    )


def _does_read_nothing_the_write_writes(
    guard: ir.Node, action: ir.SetVarAction | ir.ClearVarAction, _grammar: dict[str, ir.Prod]
) -> bool:
    """
    Whether `guard` reads nothing `action` writes. The parse may then ask the guard in front of the write.

    A comparison names a pair of the values a parse holds, and a write names a slot. The guard naming that slot reads a
    value in front of the write and another value behind it. The write is no business of a guard naming something else.
    The answer is a fact about the pair in hand and not about their kinds. The grammar holds a pair of comparison shapes
    that read the block scalar's floor, and the writes there write that floor.
    """
    return not _does_name_the_slot(guard, action.param)


def _does_read_none_of_the_forbidden(
    guard: ir.Node, action: ir.SetForbiddenAction, grammar: dict[str, ir.Prod]
) -> bool:
    """
    Whether `guard` reads nothing `action` forbids. The parse may then ask the guard in front of the write.

    The parse asks the refusal of a character it takes at a start of line. A lookaround matches its item through that
    same refusal. The lookaround therefore reads the set in force where the parse asks the guard. Moving the guard
    across the write changes that set. The set the guard reads is a fact about the pair of characters the parse
    compares. The guard admits a range of states. The write's forbidden range can start at a different character than
    the guard's range does. The ranges may fail to cross, and the guard then answers alike either side of the write.

    The range of states the guard admits errs wide. A pair that does not cross in it does not cross at all. A write that
    forbids nothing raises. The write displaces the value that was there, and this action names no replacement.
    """
    if action.item is None:
        return False
    accepted, _ways, _entering = _leaf_tables(grammar)
    return not _admits(guard, grammar) & _accepted_part(action.item, grammar, accepted)


# The actions a parse may ask a guard in front of. An entry maps `(guard, action)` to whether asking the guard there is
# the same question. An entry may instead map to the node deciding that for the pair the walk is holding. A pair absent
# is a pair nobody has worked out. Consulting it is a fault rather than a no.
GUARD_CROSSES_ACTION = _Crossing(
    {
        # A window bounds what a committed consume may take. It bounds no more than that. A lookaround reads past its
        # edge.
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
        # Taking a character moves the parse. A lookaround in front of a take asks about a different character, and a
        # comparison of a length asks about a different length.
        ("LookGuard", "ConsumeCharAction"): False,
        ("NegLookGuard", "ConsumeCharAction"): False,
        ("LookGuard", "ConsumeSpanAction"): False,
        ("NegLookGuard", "ConsumeSpanAction"): False,
        # The guard behind a run of a limited span asks what that run took. In front of the run it would read an earlier
        # run.
        ("DidMatchFullSpanGuard", "ConsumeLimitedSpanAction"): False,
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
        # A turn that must take a character records where it began. It writes nothing a guard reads.
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

    A gate cannot test a second take inside a single way. A separate way gives the second take a position where a gate
    can test it.
    """
    return [
        f"{name}: a way takes twice, and its gate can speak for only the first"
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
        if sum(isinstance(item, ir.CONSUMING) for item in _items_of_way(way)) > 1
    ]


_EVERY_WAY_TAKES_AT_MOST_ONCE = _Invariant("every-way-takes-at-most-once", _every_way_takes_at_most_once)


def _mint_consume_states(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Cut a way where it would take a second character on the strength of the first. `A = |g ... take ... CharSet rest|`
    becomes `A = |g ... take ->A'|` with `A' = |- ... CharSet rest|`.

    The way's gate cannot ask about a set behind a take, however the table answers. The way holds a pair of entries and
    has a single gate. A state of its own gives the remaining parts a position where that state's gate can answer.
    `split-consumes-into-gates` puts the question there.

    This runs until nothing moves. A cut leaves what came past the second take in the state it mints, and a longer run
    has a third take there. The state minted for a cut is therefore a way to cut again, a take at a time, until a way
    holds a single take.
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
            if isinstance(action, ir.CONSUMING) and taken:
                break
            taken = taken or isinstance(action, ir.CONSUMING)
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=ir.ChoiceState(alternatives=tuple(told(name, way) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _asked_where_entered(grammar: dict[str, ir.Prod]) -> _Entered:
    """
    `{name: paths}`. A name holds an entry per way that enters the production. An entry is the set of guards asked where
    that way enters it.

    A gate tests the position where the parse enters the way. The way may take a call before it performs anything. Such
    a call sits at that same position. A question the caller asked therefore holds where the callee begins, and it holds
    however many calls deep. A guard can therefore move up to the choice that needs it. The question survives the
    actions the guard protected.

    There is an entry per path rather than the guards the paths share. A pair of questions come here and they differ.
    Whether a path asked about the character at all is a yes-or-no. Escapes reaching a shared tail ask about different
    characters, though they do ask. That character's range is a set, and the answer is the union over the paths. The
    parse takes any character of that union. The paths come back separately. A site then says which it wants.
    `_gated_by` reads a path into a subspace, and a caller unions the subspaces of the paths that share a question.

    A path is a set. A gate holds no order between its guards. A pair of paths asking the same questions is a single
    path.

    A path that has moved contributes nothing. A caller taking a character before the call asks about somewhere else. A
    production entered by name has no caller to ask, and neither has a production nothing calls. Both come back as the
    empty path, and no guard sits where they begin. The walk computes a least fixed point. The knowledge at a call site
    includes the knowledge where the caller itself began.
    """

    def worked_out() -> _Entered:
        entered = _entered_by_name(grammar)
        nothing: frozenset[frozenset[ir.Node]] = frozenset({frozenset()})
        # Who enters what, worked out once, as `{callee: [(caller, the guards asked there, whether the parse has
        # moved)]}`. Read the other way round it is one walk of the grammar per round per name.
        sites: dict[str, list[tuple[str, frozenset[ir.Node], bool]]] = {}
        for holder, production in grammar.items():
            if not isinstance(production.body, ir.ChoiceState):
                continue
            for way in production.body.alternatives:
                call = _first_call_of_way(way)
                if isinstance(call, ir.RefCall) and call.name in grammar:
                    has_moved = any(isinstance(action, ir.CONSUMING) for action in way.actions)
                    sites.setdefault(call.name, []).append((holder, frozenset(way.gate.guards), has_moved))
        known: _Entered = {name: frozenset() for name in grammar}
        for _round in ir.rounds("the guards asked where a parse enters a production"):
            settled: _Entered = {}
            for name in grammar:
                if name in entered:
                    settled[name] = nothing
                    continue
                paths: set[frozenset[ir.Node]] = set()
                for holder, guards, has_moved in sites.get(name, ()):
                    if has_moved:
                        paths.add(frozenset())
                    else:
                        paths |= {above | guards for above in known[holder] or nothing}
                settled[name] = frozenset(paths) or nothing
            if settled == known:
                return settled
            known = settled
        raise AssertionError("`ir.rounds` raises where the rounds run out")

    return ir.kept_for(_ENTERED, grammar, worked_out)


# `_asked_where_entered` keeps an answer per grammar somebody has asked it about. An entry holds the grammar itself
# beside the answer. Keeping the grammar stops a reuse of the key under it.
_ENTERED: ir.Kept = {}


def _no_char_set_is_an_item(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no way holds a character set among what it performs.

    A question and a taking are separate things. A `CharSet` among what the machine performs is both at once. The set
    asks whether the character belongs to it, and takes the character in the same breath. The machine then reaches the
    answer only by entering the way. The answer should decide whether the machine enters the way. After the split, the
    set is the question a gate holds, and `ConsumeCharAction` is the taking.

    The set itself does not go. It stays as the question, inside the `LookGuard` of a gate and as the class a consume
    runs over. The set stops appearing among the actions as a match of its own.
    """
    # A production that *is* a character set is the class itself. Calling it asks and takes at a stroke, which makes the
    # call the fault rather than the production.
    faults = [
        f"{name}: a way calls a character set that asks and takes in a single step"
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
        for item in _items_of_way(way)
        if isinstance(item, ir.RefCall) and item.name in grammar and isinstance(grammar[item.name].body, ir.CharSet)
    ]
    return faults + [
        f"{name}: a way asks a character set and takes the character in a single step"
        for name, production in grammar.items()
        if isinstance(production.body, ir.ChoiceState)
        for way in production.body.alternatives
        for item in _items_of_way(way)
        if isinstance(item, ir.CharSet)
    ]


_NO_CHAR_SET_IS_AN_ITEM = _Invariant("no-char-set-is-an-item", _no_char_set_is_an_item)


def _does_ask_about_a_run(production: ir.Prod) -> bool:
    """
    Whether any way of `production` asks about a run before performing anything. The callers have to have made that run.

    A way asks in the gate, or first among the things the way performs. Those are the same claim on a caller. A state
    minted at a guard holds the question at the head of its own run. Moving the question into the gate says the parse
    enters that state on it.
    """
    body = production.body
    ways = body.alternatives if isinstance(body, ir.ChoiceState) else ()
    return any(
        isinstance(held, ir.DidMatchFullSpanGuard) for way in ways for held in (*way.gate.guards, *way.actions[:1])
    )


def _every_span_question_follows_its_run(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a question about a limited run asks where that run left the parse.

    The guard reads what the action in front of it did and no more. That action has to be the run. The run is the item
    before the question, where the question sits among the things a way performs. The question may instead head a state
    of its own. Minting the guard states puts it there. The run is then what a way calling that state performs last, and
    the call is the first that way makes. A call made past another call starts wherever the earlier call left off. The
    callee has by then taken away what the run did.

    A guard anywhere else reads an earlier run or reads nothing. The interpreter refuses that where it happens. This
    asks the same refusal of the grammar instead. A step that moves the pair apart is then a fault at the step, rather
    than a crash on the first input that reaches it.
    """
    faults = []
    asking = {name for name, production in grammar.items() if _does_ask_about_a_run(production)}
    for name, production in grammar.items():
        body = production.body
        for way in body.alternatives if isinstance(body, ir.ChoiceState) else ():
            # Asked at the head of a way, the run is the caller's to have made, which the walk over call sites below
            # says. Anywhere else the run stands in this way or nowhere.
            for at, item in enumerate(way.actions[1:], start=1):
                if isinstance(item, ir.DidMatchFullSpanGuard) and not isinstance(
                    way.actions[at - 1], ir.ConsumeLimitedSpanAction
                ):
                    faults.append(f"{name}: a way asks about a run where no run comes in front of it")
            calls = _calls_of_way(way)
            if not calls or calls[0].name not in asking:
                continue
            if not way.actions or not isinstance(way.actions[-1], ir.ConsumeLimitedSpanAction):
                faults.append(f"{name}: hands control to a question about a run it did not make")
    return faults


_EVERY_SPAN_QUESTION_FOLLOWS_ITS_RUN = _Invariant(
    "every-span-question-follows-its-run", _every_span_question_follows_its_run
)


def _every_consume_is_protected_by_a_gate(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a gate protects a take. The gate must have found the characters the take takes. Such a gate is a
    `LookGuard`. The `LookGuard` sits in the gate of the way. The parse may instead ask the `LookGuard` on entering the
    way.

    A take no gate tested is a match the parse has to try and give back. The gate is where the parse asks the input, and
    the take may have only the characters the gate found.

    A question that the entering ways asked holds here, at the position where they enter. `_asked_where_entered` says
    so. A guard can therefore move up to the choice that needs one, and the protected take keeps its warrant.
    """
    faults = []
    entering = _asked_where_entered(grammar)
    for name, production in grammar.items():
        body = production.body
        # A body that is not a choice offers no way to read a gate off. It stands as one way with an empty gate.
        ways = (
            body.alternatives
            if isinstance(body, ir.ChoiceState)
            else (ir.AlternativeState(gate=ir.GatePart(), actions=tuple(_held(body))),)
        )
        for way in ways:
            parts = _parts_of_way(way)
            # Each path into the way must have asked, and they need not have asked the same thing. Many escapes reaching
            # a shared tail look at different characters, and all of them look.
            faults += [f"{name}: a way takes what no gate found"] * sum(
                isinstance(item, ir.CONSUMING)
                and not isinstance(item, ir.CharSet)
                and not all(
                    any(isinstance(guard, ir.LookGuard) for guard in _guards_in_force(parts, at, path))
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

    The split creates the gates a caller then tests. A hoist moves a question that exists. Before this step runs, a way
    with no gate decides nothing.

    The question moves in front of the actions that came before it, where the set is not the first thing the way does.
    `GUARD_CROSSES_ACTION` says whether it may. A pass moves a single set per way. A second would have to cross the take
    the first left behind, and a character already taken is a different position.

    A call to a character-set production says the same thing indirectly. `b-break`'s first way is `b-line-feed`. That
    production is `LF` and no more. The set goes where the call was and is then split like any other. The production
    stays put. A consume names the production as what it runs over. An exclusion names the production as what it
    forbids. The production is the class there. It is not a match.
    """

    wrapped: dict[str, str | ir.Prod] = {}

    def gated(name: str, body: ir.Node) -> str:
        """The first call to that class needing a way that asks for `body` and takes it mints that way's name."""
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
        """A single such call moved. That call is the first the way makes."""
        for slot in ("first", "second"):
            held = getattr(way, slot)
            if not isinstance(held, ir.RefCall) or held.name not in grammar or way.recover is not None:
                continue
            body = grammar[held.name].body
            if not isinstance(body, ir.CharSet):
                continue
            # A way takes at most once. A way that already takes aims the call at a way asking for the class. The class
            # stays as the set a consume runs over.
            if any(isinstance(action, ir.CONSUMING) for action in way.actions):
                return _with_call(way, slot, ir.RefCall(name=gated(held.name, body), args=held.args))
            if slot == "first" and way.second is not None:
                return dataclasses.replace(way, actions=(*way.actions, body), first=None)
            return _with_call(dataclasses.replace(way, actions=(*way.actions, body)), slot, None)
        return way

    def told(way: ir.AlternativeState) -> ir.AlternativeState:
        at = next((index for index, action in enumerate(way.actions) if isinstance(action, ir.CharSet)), None)
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=ir.ChoiceState(alternatives=tuple(told(pulled(way)) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **{name: held for name, held in wrapped.items() if isinstance(held, ir.Prod)}}


def _called_first(way: ir.AlternativeState, grammar: dict[str, ir.Prod]) -> str | None:
    """
    The name of the production `way` enters first, where that production offers ways. Anything else is `None`.

    `first` is the call and `second` is where the way continues from it. A way therefore enters `first`, or `second`
    where the way makes no call of its own.
    """
    held = way.first if isinstance(way.first, ir.RefCall) else (way.second if way.first is None else None)
    if not isinstance(held, ir.RefCall) or held.name not in grammar:
        return None
    return held.name if isinstance(grammar[held.name].body, ir.ChoiceState) else None


def _asked_by_every_way(name: str, grammar: dict[str, ir.Prod]) -> set[ir.Node]:
    """
    Answer the guards the ways of `name` share. The parse asks these guards as it enters the production. A caller could
    ask them instead.

    A production offering a single way is the plain case. The gate of that way is the answer. A guard the ways share
    asks before the parse chooses any of them. A caller asking that guard at the call therefore asks the same question
    in the same place. A guard that a way asks and its siblings lack is different. That guard tells the way from its
    siblings.

    A body that is not a choice offers no ways. It asks nothing a caller could ask instead.
    """
    body = grammar[name].body
    if not isinstance(body, ir.ChoiceState):
        return set()
    ways = body.alternatives
    shared = set(ways[0].gate.guards)
    for way in ways[1:]:
        shared &= set(way.gate.guards)
    return shared


def _inlined_called_ways(grammar: dict[str, ir.Prod], namer: _Namer | None = None) -> dict[str, ir.Prod]:
    """
    Put a production offering a single way at the call to it. `A = |gA actA ->B sA|` with `B = |actB fB sB|` becomes `A
    = |gA actA actB fB sA|`.

    A call to a production offering a single way is unconditional. Entering it is running that way. The work of that way
    can therefore go into the site the call had. The gain is that the question `B` would have opened on becomes a
    question this way opens on. A hoist can reach such a question. The call to `B` sat between the pair of questions and
    kept the hoist from reaching both.

    This runs wherever the call appears, and not at a lone site only. A callee a pair of ways reach goes into both. That
    is a copy of the callee's own run rather than a second answer. A callee no site still calls goes away in the sweep.

    This runs only where `B` asks nothing. The parse asks the gate of `B` where it enters `B`. That position is past
    what the caller performs. Moving that gate up therefore falls to the hoist. A gate left in the middle of a way is a
    question the parse asks after entering the way. `every-guard-is-in-a-gate` forbids that. The hoist takes the gate
    first where `B` asks something, and a later round inlines `B` with nothing left to ask.

    This runs only where neither way has a recovery. A recovery rides the push its call makes, and that call changes
    here.

    A way holds a call and a continuation. Inlining brings the callee's call, the callee's continuation and the caller's
    continuation, and the way has a pair of slots. This re-associates the calls and the continuations rather than
    refusing. `(D E) C` is `D (E C)`. The way calls `D` and continues to a state holding `E` and then `C`. That is the
    right-oriented shape, a call and a tail. The state minted for the tail is therefore not the state the inlining
    removed. The call to `B` goes. The sweep removes `B` once no site calls it.

    This leaves such a way untouched where there is no namer to mint with. The sweep runs in places that have none.
    """
    minted = {}

    def inlined(way: ir.AlternativeState) -> ir.AlternativeState:
        # The call is `first`, or `second` where the way makes none of its own. A tail call is a call like any other. A
        # way whose callee is written in has nothing left to continue to, and fits throughout.
        held = way.first if isinstance(way.first, ir.RefCall) else (way.second if way.first is None else None)
        if not isinstance(held, ir.RefCall) or way.recover is not None:
            return way
        body = grammar[held.name].body
        if not isinstance(body, ir.ChoiceState) or len(body.alternatives) != 1:
            return way
        [only] = body.alternatives
        if only.gate.guards or only.recover is not None:
            return way
        # A guard among what the callee performs would land past what the caller performs, which asks a question after
        # the way was entered.
        if any(isinstance(action, ir.GUARDS) for action in only.actions):
            return way
        actions = (*way.actions, *only.actions)
        if not actions and only.first is None and only.second is None:
            return way  # what is left would be a way that performs nothing and calls nothing
        if actions and only.first is not None:
            return way  # a way acts or calls and not both. it is then entered where it calls
        if sum(isinstance(action, ir.CONSUMING) for action in actions) > 1:
            # A way takes once. What it takes is then what its own gate found. `mint-consume-states` cuts a way that
            # would take twice. Writing that cut back in would undo it, and the two would trade the same way forever.
            return way
        # What the way continues to once the callee has run. it is nothing where the callee was the tail call itself
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=ir.ChoiceState(alternatives=tuple(inlined(way) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _hoist_guards_to_callers(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Ask a callee's guards where the parse enters its caller, rather than at the call. `A = |gA actA ->B|` with `B = |gB
    actB ...|` becomes `A = |gA gB actA ->B'|`. `B'` is `B` without the guards that moved.

    A guard takes nothing. A guard's position therefore changes what the parse decides and not what it matches. Anything
    between the call and the entry could change the answer, and `actA` sits between. `GUARD_CROSSES_ACTION` says whether
    a guard may come in front of an action rather than behind it. A guard moves where the actions between admit it. A
    guard the actions refuse stays put.

    `_asked_by_every_way` says what moves. A callee offering a single way gives its gate. A callee offering more gives
    the guards its ways share. The parse asks those before choosing any way. A guard in a way its siblings lack stays.
    That guard tells the way from its siblings.

    The result is `B'` rather than `B`. Callers share a callee, and a guard taken out for the sake of a caller is a
    guard the other callers stop asking. This mints `B'` per callee and per set of guards taken. The sites that took
    those guards call `B'`. So do the sites that could not go on calling `B` unchanged.

    This runs until nothing moves. A caller that has taken a guard is a callee its own callers can take that guard from.
    The questions therefore climb until they reach a way that performs something no guard may cross, or a choice whose
    ways ask different things.
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
    A single pass of `hoist-guards-to-callers`. This takes a guard out of the production a caller enters. That caller
    can take the guard itself.
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
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production, body=ir.ChoiceState(alternatives=tuple(told(way) for way in production.body.alternatives))
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _does_ask_after_an_empty_turn(performed: Sequence[ir.Node], way: ir.AlternativeState) -> bool:
    """
    Whether `way`'s gate asks after a turn that `performed` opened and took nothing in.

    A turn that must take a character is an open, a guard that reads it, and a close. `StartMustConsumeAction` records
    where it began. `DidConsumeSinceOpenGuard` asks whether the parse has taken anything since. `EndMustConsumeAction`
    takes the open off. Together they stop a run over a body matching empty from spinning. Such an open may sit among
    the actions a path performed, and no take falls between the open and its close. The guard then answers no under any
    input. The gate refuses any input, and the path is a path no parse takes.
    """
    for guard in way.gate.guards:
        if not isinstance(guard, ir.DidConsumeSinceOpenGuard):
            continue
        for at, action in enumerate(performed):
            if isinstance(action, ir.StartMustConsumeAction) and action.pair == guard.pair:
                return not any(isinstance(held, ir.CONSUMING) for held in performed[at + 1 :])
    return False


def _flatten_ungated_call_trees(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Say a way that nothing has gated as the paths that it is, a gated way per path. `A = |actA ->B contA| |...|` with `B
    = |gB actB fB contB|` becomes `A = |gB actA actB fB M| |...|`. `M` runs `contB` and then `contA`.

    This step rewrites a way rather than answering a question. It reshapes `ir.ChoiceState` and hands back a body of any
    other kind.

    A way with no gate hands control on. That way hands control to a choice whose ways have gates. The walk leaves the
    way, goes through the calls and the continuations, and reaches a gate. The first question past the way is the
    question that decides entry into the way. The count of calls down to that question does not matter. A path written
    out here is that gate, what the parse performed on the way to it, and where the path goes from there.

    The walk goes through the continuations as well as the calls. A way holding no call is not the end of a path. The
    innermost thing still pending runs next, and the gate is in there. A path ends only where a way has a gate. An
    ending anywhere else is a tree this step cannot flatten, and this step raises rather than passing over it. Such a
    path ends with nothing pending, on a body that is not a choice, or back at a production already entered.

    This step writes the paths in order, and the parse falls where it fell. The paths go into the site the way had,
    depth first and left to right. That is the order the machine would have reached those gates in.

    The path's action goes behind the gate. That is a move and not a copy. `GUARD_CROSSES_ACTION` says whether the parse
    may ask a guard in front of an action rather than behind it. A way holding a path whose gate may not come up does
    not change. This step places the whole set there as well. This step otherwise leaves the way untouched.

    This step drops a path whose gate asks after a turn the path itself opened and took nothing in. The guard asks
    whether the parse took anything since that open. The guard therefore refuses on such a path, and the input does not
    matter. The way holding the path fails there and falls to the way behind it. With the path gone the way reaches its
    refusal sooner. A guard refusing throughout is the turn's whole point. Cancelling the pair would make the path
    succeed, and it would make a run over an empty-matching body spin forever.

    This step runs the remainder in a state of its own. Past the call the leaf makes comes the continuation the leaf
    holds. Then come the pending continuations innermost first, and the last is the continuation the starting way held.
    That is a chain of states. A state holds a call and where to go after it. The paths that end the same way share that
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
            body = grammar[call.name].body
            if not isinstance(body, ir.ChoiceState):
                raise ValueError(f"{owner}: a path reaches {call.name}. That production offers no way for a gate.")
            for opened in body.alternatives:
                walk(opened, entered | {call.name}, done, left)

        walk(way, frozenset({owner}), (), ())
        return found

    def told(owner: str, way: ir.AlternativeState) -> tuple[ir.AlternativeState, ...]:
        if id(way) not in wanted:
            return (way,)
        walked = paths(owner, way)
        # A way each of whose paths is dropped does not change. Nothing at all is a body no choice can offer. A way no
        # input takes is one the pruning is for, rather than the flattening.
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
            # A leaf that only continues has a slot free, and the head of the chain belongs in it. Left in the chain it
            # would be asked a state further on.
            if first is None and len(calls) > 1:
                first, calls = calls[0], calls[1:]
            return dataclasses.replace(
                leaf, actions=(*performed, *leaf.actions), first=first, second=spine(calls, owner)
            )

        return tuple(flattened(*held) for held in walked)

    written = {
        name: (
            production
            if not isinstance(production.body, ir.ChoiceState)
            else dataclasses.replace(
                production,
                body=ir.ChoiceState(
                    alternatives=tuple(one for way in production.body.alternatives for one in told(name, way))
                ),
            )
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
    `spans` with the invalid byte told from the characters again.

    A subspace holds the byte that begins no character as a unit of its own. The codepoints sit beside that byte. A
    subspace coalesces what runs together, and the byte comes out in a single interval with codepoint `0`. A set the
    parser asks keeps the byte and the codepoints apart. A run starting at `0` says the parser accepts a character
    there, where the byte is not a character.
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

    This synthesises the guards and then holds a guard to the form it came out as. `_admits` reads the guards back, and
    the result intersected with what the way already admits must be the atom itself. An atom this cannot say raises
    rather than coming back approximated. A gate wider than the atom would leave the ways it cut overlapping.

    The guards say a pair of things. The axes come first, a guard per axis the atom pins that the way leaves open. This
    asks for no axis the way already fixes, and leaves an axis nothing can say to the check. The characters come second,
    said as the lookahead that holds them. That lookahead becomes a refusal where the atom names a character a set does
    not hold. It becomes the end-of-stream question where the atom names only the end.
    """
    admitted = next(iter({one for one in atom.admitted if one}), None)
    if admitted is None:
        raise ValueError("an atom holding no state at all")
    # An axis at a time, keeping only what narrows. `n <= 0` already says `n <= len(match)`, and asking it again puts a
    # question at run time whose answer is settled.
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
    `way` with its take narrowed to the set `atom` admits, or `way` unchanged where the atom takes nothing away.

    A take of a single character names its own set, and the gate found that character. The set is therefore the atom's
    set, and the way does not change. A run is not narrowed. A run takes its own set past the first character, and a set
    cut down would be a different run. This peels the first character off instead. That gives a single character from
    the atom, then the run again or nothing. Those write the same maximal run in a pair of places. That run takes at
    least a character and stops where its set stops.

    The other take a way of a choice holds is a limited run, and this raises on it. Peeling a character from under a
    limit would leave the limit counting what it does not bound.
    """
    admitted = next(iter({one for one in atom.admitted if one}))
    at = next((index for index, action in enumerate(way.actions) if isinstance(action, ir.CONSUMING)), None)
    if at is None:
        return way
    taken = way.actions[at]
    spans = _peek_spans(_set_of(taken), grammar)
    if spans is None or tuple(map(tuple, spans)) == admitted.spans:
        return way
    if isinstance(taken, ir.ConsumeCharAction):
        narrowed = dataclasses.replace(taken, set=_spans_node(admitted.spans))
        return dataclasses.replace(way, actions=(*way.actions[:at], narrowed, *way.actions[at + 1 :]))
    if not isinstance(taken, ir.ConsumeSpanAction):
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

    The parse enters such a way and fails on any input. The way was there before the cutting, inside a wider way whose
    gate said less. A piece's question travels along the path into what the way calls. A piece can pin an axis the wider
    way left open. Such a piece can then rule out a callee way the wider way left in place. Dropping the way takes
    nothing away, and the parse reaches the refusal of the way behind it sooner. Leaving the way in would let the
    machine make a call it already knows fails.

    This holds where the way takes nothing first. Past a take the parse is somewhere else, and the gate tested the
    earlier position. The gate then has no say over the callee's entry.
    """
    if any(isinstance(action, ir.CONSUMING) for action in way.actions):
        return False
    called = _first_call_of_way(way)
    body = grammar[called.name].body if called is not None and called.name in grammar else None
    if not isinstance(body, ir.ChoiceState):
        return False
    return not any(atom & _gated_by((), one, grammar) for one in body.alternatives)


def _split_overlapping_ways(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Cut the ways of a choice apart where they half overlap. A pair of ways then enters in the same states or in no
    shared state.

    This refines the states admitting the ways into atoms. Those pieces do not overlap, and the pieces cover between
    them what the ways cover. A way becomes a way per atom inside it. Such a way keeps its own gate and gains the guards
    that say the atom. A state admitting the way admits one of its pieces. The cutting loses no state. A pair of pieces
    is apart or the same, and that property makes a piece an atom.

    This runs over the whole choice at once rather than pair by pair. The pieces go into the site the way had, in the
    order the cutting made the atoms. The first way admitting a state is therefore the way that admitted the state
    before, and the parse falls where it fell.

    The cutting skips the choice's else. The else has no gate, and the parse reaches the else wherever the ways in front
    of it do not admit the state. That is not a state a guard says, and not a state an atom can hold.

    A way narrowed to fewer characters than the way takes has its take narrowed too, and `_taking` says how.
    `accepted-and-gated-charsets-are-equal` holds the pair equal. A gate that says less than the take does is a way
    entered on a character it cannot begin with.
    """
    minted: dict[str, ir.Prod] = {}
    _accepted, _splits, entering = _leaf_tables(grammar)

    def told(name: str, production: ir.Prod) -> ir.Prod:
        ways = getattr(production.body, "alternatives")[:-1]
        held = [_entered_in(name, way, grammar, entering) for way in ways]
        # Only the ways something has to be told apart from cut anything. A pair alike but for their gates are a single
        # way reached a pair of ways, with nothing to decide where they overlap.
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
                # A piece already standing here is this piece. A pair of ways alike but for their gates cut to pieces
                # alike in each part, the gate included.
                if piece not in opened:
                    opened.append(piece)
        return dataclasses.replace(
            production, body=ir.ChoiceState(alternatives=(*opened, getattr(production.body, "alternatives")[-1]))
        )

    written = {
        name: (
            told(name, production)
            if isinstance(production.body, ir.ChoiceState) and len(production.body.alternatives) > 1
            else production
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _unheld(node: ir.Node) -> ir.Node:
    """
    `node` with the scopes written around it stripped. A scope writes nothing that changes what the node does.

    A `(token)` around a character is a character taken and a code given to it. A question asked after the first item of
    a way has to see that character. A `(commit)` stays. The question asks about a commit's word on failing.
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
    A way of an outer choice may call an inner choice. Such a way becomes the inner choice's ways. Those ways go into
    the site the call had.

    Take `a | P | c` with `P` written `d | e`. The choice `a | P | c` decides between more ways than it shows. Written
    out, `a | P | c` reads `a | d | e | c`. Those are the same ways, tried in the same order. A way then sits where a
    question can ask the offering choice about it.

    `_flattened_calls` inlines an inner choice's ways into the outer `AltTree`. An `AltTree`'s parts are the ways a
    choice offers.
    """
    return _flattened_calls(grammar, ir.AltTree)


def _flatten_called_sequences(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    An item of a way that calls a run of items becomes those items, at the site the call had.

    `_flattened_calls` flattens the calls of a `SeqTree`. The parts of that node are the items a way performs. A call
    last in the way stays. The continuation past a way's call is a production of its own by design.
    """
    return _flattened_calls(grammar, ir.SeqTree, does_flatten_last=False)


def _reached_within(grammar: dict[str, ir.Prod], kind: type) -> dict[str, set[str]]:
    """
    `{name: {name}}`, the productions a name reaches directly inside a `kind` node, and what those reach in turn.

    This tells a flattening that would end from a flattening that would run forever. A body written out into a body that
    can reach back writes itself out again round after round.
    """
    reaches: dict[str, set[str]] = {name: set() for name in grammar}
    for _round in ir.rounds("the nodes a production reaches inside a kind"):
        settled = {}
        for name, production in grammar.items():
            found = set()
            for node in _held(production.body):
                if isinstance(node, kind):
                    for held in getattr(node, "items"):
                        if isinstance(held, ir.RefCall):
                            found |= {held.name} | reaches[held.name]
            settled[name] = found
        if settled == reaches:
            return reaches
        reaches = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _flattened_once(
    node: ir.Node,
    owner: str,
    grammar: dict[str, ir.Prod],
    kind: type,
    reaches: dict[str, set[str]],
    does_flatten_last: bool,
) -> ir.Node:
    """
    `node` with a call that is the entire `kind` node written out as what the call names.

    This is a rewrite rather than a question. This reshapes `kind` and hands the rest of the node back. The caller names
    `kind`, and a fixed table of kinds cannot say what this does.
    """
    node = ir.rebuilt(node, lambda child: _flattened_once(child, owner, grammar, kind, reaches, does_flatten_last))
    if not isinstance(node, kind):
        return node
    parts = []
    items = getattr(node, "items")
    for at, held in enumerate(items):
        called = grammar[held.name] if isinstance(held, ir.RefCall) and not held.args else None
        # Writing out a callee that can reach itself unrolls its cycle a turn and writes the way back in. The cycle need
        # not run through the body being rewritten.
        does_unroll_a_cycle = called is not None and (
            held.name == owner or owner in reaches[held.name] or held.name in reaches[held.name]
        )
        is_kept = not does_flatten_last and at == len(items) - 1
        if called is None or called.params or not isinstance(called.body, kind) or does_unroll_a_cycle or is_kept:
            parts.append(held)
        else:
            parts += list(getattr(called.body, "items"))
    return dataclasses.replace(node, **{"items": tuple(parts)})


def _flattened_calls(grammar: dict[str, ir.Prod], kind: type, does_flatten_last: bool = True) -> dict[str, ir.Prod]:
    """
    The grammar with a call that is the entire `kind` node written out as what the call names. This runs to a fixpoint.
    A body written out can hold such a call itself. This does not write into a body that can reach back.

    This runs where the call is a whole part. A call inside a longer way would leave a choice among the items. That is a
    distribution rather than a flattening, and it costs a copy of the continuation behind the call. This runs where the
    callee takes no parameter. Writing a parameterised callee out would mean substituting the arguments rather than
    moving the parts.
    """
    for _round in ir.rounds("the flattening of a call that is the entire kind node"):
        reaches = _reached_within(grammar, kind)
        settled = {
            name: dataclasses.replace(
                production, body=_flattened_once(production.body, name, grammar, kind, reaches, does_flatten_last)
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


# `_INNER_WAYS` says which matches a node holds, and this says how to put them back. The pair name the same kinds bar
# `ChoiceState`. `ChoiceState` belongs to the canonical form. The rewrites that read this table run while a body is
# still a tree. The lifts are those rewrites. The minting of continuations is another.
_WITH_INNER_WAYS: ir.Question[ir.Node] = ir.Question(
    "a node rebuilt from the matches it holds",
    {
        ir.AltTree: lambda node, rebuilt: dataclasses.replace(node, items=tuple(rebuilt(way) for way in node.items)),
        ir.SeqTree: lambda node, rebuilt: rebuilt(node),  # a way is a single match, and rebuilds whole.
        # A match that nothing makes holds none, and has no inside to give a production to.
        ir.FailTree: lambda node, rebuilt: node,
        ir.RecoverWrapper: lambda node, rebuilt: dataclasses.replace(
            node, item=rebuilt(node.item), recovery=rebuilt(node.recovery)
        ),
        # A binding puts a value in scope for a match, and the condition is that match.
        ir.BindTree: lambda node, rebuilt: dataclasses.replace(node, cond=rebuilt(node.cond)),
    },
)


def _lifting(kind: type) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    Give a `kind` inside a way a production of its own, and put a call to that production in place of the `kind`.

    A choice is where the parse decides and a run is where it loops. A machine does either in a state. In the middle of
    a way, neither has anywhere to be a state. The state is the production, and the items a way holds run inside that
    production. A minted production is a state a call reaches and comes back from, and the way holds an item like any
    other. Naming a run settles nothing about the run itself. The run stays the possessive consume the grammar wrote.
    Whether it takes another turn is a question the gates ask.

    This mints rather than distributing. Distributing is the other way to take a choice out of a sequence. `a (x | y) b`
    as `a x b | a y b` runs `a` twice wherever `a` takes a character or pushes anything. A copy of `b` per way is a copy
    of the productions `b` calls. The call costs a push and duplicates nothing.
    """

    def transform(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
        minted = {}

        def item(node: ir.Node, owner: str) -> ir.Node:
            if isinstance(node, ir.LEAF_ITEMS):
                return node
            if isinstance(node, kind):
                name = namer.fresh(owner)
                minted[name] = ir.Prod(
                    grammar[owner].number, name, (), _with_inner_ways(node, lambda way: rebuilt(way, owner))
                )
                return ir.RefCall(name=name, args=())
            return _with_inner_ways(node, lambda way: rebuilt(way, owner))

        def rebuilt(node: ir.Node, owner: str) -> ir.Node:
            if isinstance(node, ir.SeqTree):
                return dataclasses.replace(node, items=tuple(item(part, owner) for part in node.items))
            return item(node, owner)

        def body(node: ir.Node, owner: str) -> ir.Node:
            if isinstance(node, ir.BODY_KINDS) and not isinstance(node, ir.SeqTree):
                return _with_inner_ways(node, lambda way: rebuilt(way, owner))
            return rebuilt(node, owner)

        lifted = {
            name: dataclasses.replace(production, body=body(production.body, name))
            for name, production in grammar.items()
        }
        return {**lifted, **minted}

    return transform


def _lower_bind(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
    """
    Write a binding as the match the binding binds for, and the write that follows. `Bind(cond, param, value)` becomes
    `cond SetVar(param, value)`.

    A binding is not a scope over the match. A binding matches `cond`, works the value out where that match ends, and
    writes the value. For the binding the grammar still has, that value is the digit's text, and `(atoi)` reads it off
    the run. That is a match and then an action. The interpreter says so twice over. A binding whose condition has
    matched does exactly what `SetVarAction` does. The binding undoes the write where what follows fails, and the
    condition can then try its next way.
    """

    def lowered(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.BindTree):
            return ir.SeqTree(items=(node.cond, ir.SetVarAction(param=node.param, value=node.value)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _is_actions_alone(node: ir.Node, grammar: dict[str, ir.Prod], seen: frozenset[str] = frozenset()) -> bool:
    """
    Whether `node` is a way made of actions. Such a way takes no character and matches wherever the parse reaches it.

    This is a structural question, and the commit's hoist rests on it. `A (commit m: X)` is `(commit m: A X)` where `A`
    cannot fail. An `A` that could fail would fail under the hoist with the commit's error rather than by not matching.
    A choice counts where some way of it counts, and that way is the way taken. The checker answers no for a recursion
    reached again. Such a recursion has no way of its own to answer with.
    """
    return _IS_ONLY_ACTIONS(node, grammar, seen)


# The commit's lift asks this of a way's leading parts and what those call. The kinds arriving here are therefore the
# kinds a way can begin with there. A wider group would claim more than the corpus bears out.
_IS_ONLY_ACTIONS: ir.Question[bool] = ir.Question(
    "whether a match consists of actions",
    {
        (ir.EmptyTree, ir.SetVarAction): True,
        ir.SeqTree: lambda node, grammar, seen: all(_is_actions_alone(item, grammar, seen) for item in node.items),
        # a choice counts where some way of it counts.
        ir.AltTree: lambda node, grammar, seen: any(_is_actions_alone(item, grammar, seen) for item in node.items),
        ir.TokenWrapper: lambda node, grammar, seen: _is_actions_alone(node.item, grammar, seen),
        # a recursion reached again has no way of its own to answer with.
        ir.RefCall: lambda node, grammar, seen: node.name not in seen
        and _is_actions_alone(grammar[node.name].body, grammar, seen | {node.name}),
        (ir.BindTree, ir.OneCharSet, ir.CharSet, ir.NegLookGuard, ir.RepTree): False,
    },
)


def _is_nullable(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> bool:
    """
    Whether `node` has a way that takes no character. `ways` says this of a production, and this says it of any node.

    This is a question rather than a rewrite, and it answers where `_split` refuses to. A commit may hold an empty match
    and a consuming match together. A split cannot say that as a pair of ways. Asking about it is perfectly ordinary. An
    empty match covered by accident is the whole debt this phase removes. That makes this a `Question`. It raises on a
    kind the table does not name, and the corpus proves the parse reaches the answers held there.
    """
    return _IS_NULLABLE(node, grammar, ways)


def _is_way_nullable(node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> bool:
    """
    Whether a way consumes no character. That holds where the parts of the way consume none.
    """
    return all(_is_nullable(item, grammar, ways) for item in _parts_of_way(node))


_IS_NULLABLE: ir.Question[bool] = ir.Question(
    "whether a match can take no character",
    {
        # A consume is in the first of these. A gate that found the class sits in front of a run of that class, and the
        # run therefore takes a character.
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
    # The provisional actions and the wider consumes fall past this question. The families say what those take.
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
    `reads` holds the ways of `node` that take a character, and `empty` holds the ways that take none. An element of the
    pair is `None` where `node` offers no way of its kind. `ways` says which of the pair a production has. A reference
    therefore splits by name. Splitting a reference does not walk into what it calls.

    The pair are `node` itself, said as an ordered choice of the consuming ways and then the empty ways. That is the
    order `node` already tries them in. A sequence's ways come out in the order the sequence's parts offer them. `a b`
    consuming is `a_reads b` and then `a_empty b_reads`. That enumerates exactly as `a b` does. An alternation's ways
    come out in the order the alternation wrote them. In this grammar a consuming way comes ahead of an empty way.
    """
    return _SPLIT(node, grammar, ways)


def _split_call(node: ir.RefCall, _grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A call's `(reads, empty)`. The pair holds the names of the callee's split where the callee has a split. Otherwise
    the pair says what the callee can do.
    """
    reads, empty, is_both = ways[node.name]
    if is_both:
        return ir.RefCall(f"{node.name}_reads", node.args), ir.RefCall(f"{node.name}_empty", node.args)
    return (node if reads else None), (node if empty else None)


def _split_alt(node: ir.AltTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """An alternation's `(reads, empty)`. It is the consuming ways together, and the empty ones together."""
    parts = [_split(item, grammar, ways) for item in node.items]
    reads = tuple(way for way, _none in parts if way is not None)
    empty = tuple(none for _way, none in parts if none is not None)
    return (ir.AltTree(items=reads) if reads else None), (ir.AltTree(items=empty) if empty else None)


def _split_run(node: ir.StarTree | ir.PlusTree | ir.TrimStarTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    Split a run into a `(reads, empty)` pair. The split asks whether the run must take a turn and whether a turn can
    take nothing.

    The consuming half is a run that must take a turn. `PlusTree` says that. A run keeps one of the pair of forms until
    `lower-runs` says the run as ways. This reads a grammar that still holds both forms.
    """
    reads, empty = _split(getattr(node, "item"), grammar, ways)
    taking = ir.PlusTree(item=reads) if reads is not None else None
    if isinstance(node, ir.StarTree):
        return taking, ir.EmptyTree()  # a run of none or more takes nothing where the first turn does not match
    if empty is None:
        return node, None  # the item reads throughout. a run that must take a turn does too
    # The run ends on a turn that takes nothing, and that turn is the last there is. `lower-runs` says each turn takes a
    # character. A turn taking none is not a turn the run took.
    return taking, empty


def _split_bind(node: ir.BindTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """A binding's `(reads, empty)`. That is the binding around either half of the item it binds."""
    reads, empty = _split(node.cond, grammar, ways)
    return (
        (dataclasses.replace(node, cond=reads) if reads is not None else None),
        (dataclasses.replace(node, cond=empty) if empty is not None else None),
    )


def _split_holder(
    node: ir.TokenWrapper | ir.Wrapper | ir.CommitWrapper | ir.MaxWrapper | ir.RecoverWrapper,
    grammar: dict[str, ir.Prod],
    ways: _Ways,
) -> _Split:
    """A scope's `(reads, empty)`. That is the scope around either half of the item it holds."""
    if node.item is None:
        return None, node  # a `(max)` window with nothing in it. a bound, and no match of its own
    reads, empty = _split(node.item, grammar, ways)
    if isinstance(node, (ir.CommitWrapper, ir.RecoverWrapper)) and reads is not None and empty is not None:
        # Both are the error where the item cannot match. A consuming form of either would be the error where the parse
        # should have gone on to the empty form.
        raise ValueError(f"a {type(node).__name__.lower()} hides both an empty match and a consuming match")
    return (
        (dataclasses.replace(node, item=reads) if reads is not None else None),
        (dataclasses.replace(node, item=empty) if empty is not None else None),
    )


def _ways_of(node: ir.ChoiceState | ir.AlternativeState) -> tuple[ir.AlternativeState, ...]:
    """The ways `node` offers as a tuple. A choice contributes its own ways, and a way contributes itself."""
    return node.alternatives if isinstance(node, ir.ChoiceState) else (node,)


def _way_of_parts(way: ir.AlternativeState, parts: Sequence[ir.Node]) -> ir.AlternativeState:
    """
    `way` with its parts replaced by `parts`. The parts go in the order `_items_of_way` gives them. That order is the
    actions first, then the call, then where the way continues.

    The actions change and the call stays. A call splits to itself. The pair of slots take back exactly what they held.
    """
    return dataclasses.replace(way, actions=tuple(parts[: len(way.actions)]))


def _split_way(node: ir.AlternativeState, grammar: dict[str, ir.Prod], ways: _Ways) -> _WaySplit:
    """
    A way's `(reads, empty)` pair in the canonical form. A way reads where any of its parts does. The consuming ways are
    therefore a way per part that can read. That part reads, and the parts before it take nothing. The empty way is the
    parts taking none. The consuming half comes back as a choice of those ways.

    This reads the parts in order. A lookahead among the parts lets a later consume take a character.
    """
    parts = list(_items_of_way(node))
    reads: list[ir.AlternativeState] = []
    taken: list[ir.Node] = []
    for position, part in enumerate(parts):
        way, none = _split(part, grammar, ways)
        if way is not None:
            reads.append(_way_of_parts(node, taken + [way] + parts[position + 1 :]))
        if none is None:
            # This part consumes throughout. there is no empty way past it
            return (ir.ChoiceState(tuple(reads)) if reads else None), None
        taken.append(none)
    return (ir.ChoiceState(tuple(reads)) if reads else None), _way_of_parts(node, taken)


def _split_canonical(node: ir.AlternativeState | ir.ChoiceState, grammar: dict[str, ir.Prod], ways: _Ways) -> _WaySplit:
    """
    The canonical form's `(reads, empty)` pair for a choice or for a way. Those are the ways of it that take a character
    and the ways that take none. Either is `None` where the node offers neither.

    A choice's ways sit beside one another. A way therefore splits, and this flattens the halves that come back. A way
    may split into a choice of several. A test holds in the way that made it and not in the next way. This therefore
    does not read the ways in order the way it reads a way's parts.
    """
    if not isinstance(node, ir.ChoiceState):
        return _split_way(node, grammar, ways)
    halves = [_split_way(way, grammar, ways) for way in node.alternatives]
    reads = tuple(one for half, _none in halves if half is not None for one in _ways_of(half))
    empty = tuple(one for _half, none in halves if none is not None for one in _ways_of(none))
    return (ir.ChoiceState(reads) if reads else None), (ir.ChoiceState(empty) if empty else None)


def _split_switch(node: ir.CaseTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A switch's `(reads, empty)`. A switch reads where a branch it could take reads. A switch takes nothing where a
    branch it could take takes nothing.
    """
    branches = [branch.item for branch in node.branches] + ([node.default] if node.default is not None else [])
    answers = [_split(item, grammar, ways) for item in branches]
    return (
        node if any(part is not None for part, _empty in answers) else None,
        node if any(part is not None for _reads, part in answers) else None,
    )


def _split_seq(node: ir.SeqTree, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A sequence's `(reads, empty)`. A sequence reads where any part reads. The consuming ways are therefore a way per
    part that can read. That part reads, and the parts before it take nothing. The empty way is the parts taking none.

    The lookaheads before a consume narrow which characters can come next. The consume takes at least a character where
    what those lookaheads admit falls in its set. Such a consume is not among the parts that take nothing. A `PlusTree`
    over a class comes out that way. Reading the pair as if either half could be empty would report an empty way the
    parse has no input for.
    """
    reads: list[ir.Node] = []
    taken: list[ir.Node] = []
    for position, item in enumerate(node.items):
        way, none = _split(item, grammar, ways)
        if way is not None:
            reads.append(ir.SeqTree(items=tuple(taken) + (way,) + node.items[position + 1 :]))
        if none is None:
            # This part consumes throughout. there is no empty way past it
            return (ir.AltTree(items=tuple(reads)) if reads else None), None
        taken.append(none)
    return (ir.AltTree(items=tuple(reads)) if reads else None), ir.SeqTree(items=tuple(taken))


def _split_counted(node: ir.RepTree, taken: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways) -> _Split:
    """
    A counted repetition's `(reads, empty)`. `taken` is the turn it takes, a repetition's item or a counted consume's
    set.

    A non-positive count matches nothing at all. A count the parse works out is therefore a repetition that takes none.
    The indent consume's count is such a count, and it holds the indentation in force. The count tells the ways apart.
    The character does not.
    """
    _reads, empty = _split(taken, grammar, ways)
    if empty is not None:
        raise ValueError(f"a repetition of `{taken}` takes a turn that may take nothing, and cannot be split")
    times = getattr(node.count, "value", None)
    if times is not None:
        return (node, None) if isinstance(times, int) and times > 0 else (None, ir.EmptyTree())
    # The consuming way says the turn it takes rather than leaning on the count that admitted it. What it is then stands
    # in the shape. The count is positive, a turn is taken, and the rest of them follow.
    rest = dataclasses.replace(node, count=ir.SubValue(a=node.count, b=ir.LitValue(value=1)))
    consuming = ir.SeqTree(items=(ir.IsLessThanGuard(a=ir.LitValue(value=0), b=node.count), taken, rest))
    return consuming, ir.IsLessEqualGuard(a=node.count, b=ir.LitValue(value=0))


def _lifted_commit(body: ir.Node, grammar: dict[str, ir.Prod]) -> tuple[str | None, ir.Node]:
    """
    `(message, body)` with a commit over `body` entire lifted off it, and `(None, body)` where `body` has none.

    A commit is the error where its item cannot match. That stops a split. Either form failing would be the error where
    the parse should have gone on to the other form. `A (commit m: X)` and `(commit m: A X)` are the same match where
    what comes before takes no character and cannot fail. The commit therefore comes off, the body splits, and the
    commit goes back over the choice. That gives a single message scope around both ways rather than a scope per way.
    """
    if isinstance(body, ir.CommitWrapper):
        return body.message, body.item
    items = body.items if isinstance(body, ir.SeqTree) else ()
    if not items or not isinstance(items[-1], ir.CommitWrapper):
        return None, body
    if not all(_is_actions_alone(item, grammar) for item in items[:-1]):
        return None, body
    return items[-1].message, ir.SeqTree(items=items[:-1] + (items[-1].item,))


_SPLIT: ir.Question[_Split] = ir.Question(
    "the pair of a match's ways that take a character and its ways that take none. A half is `None` where the match "
    "has no such way.",
    {
        ir.CONSUMING: lambda node, grammar, ways: (node, None),
        ir.CONSUMES_NOTHING: lambda node, grammar, ways: (None, node),
        ir.RefCall: _split_call,
        ir.SeqTree: _split_seq,
        ir.AltTree: _split_alt,
        ir.RUNS: _split_run,
        ir.RepTree: lambda node, grammar, ways: _split_counted(node, node.item, grammar, ways),
        ir.BindTree: _split_bind,
        ir.WRAPPERS: _split_holder,
        (ir.AlternativeState, ir.ChoiceState): _split_canonical,
        # The specialization settles a switch, and the split sees a switch before the specialization runs. The caller
        # decides the branch, and the node covers the branches it can.
        ir.CaseTree: _split_switch,
        ir.VALUE_KINDS: lambda node, grammar, ways: (None, node),  # a value matches nothing and takes no character.
        ir.OptTree: lambda node, grammar, ways: (
            node.item if _split(node.item, grammar, ways)[0] is not None else None,
            ir.EmptyTree(),
        ),
    },
    # The provisional actions belong to phases past the empties. Splitting them would rewrite a shape the grammar lacks.
    untested=(
        ir.CommitProvisionalAction,
        ir.InjectBeforeAction,
        ir.MarkProvisionalAction,
        ir.OpenProvisionalAction,
        ir.RetypeProvisionalAction,
    ),
)


def _production_split(
    production: ir.Prod, grammar: dict[str, ir.Prod], ways: _Ways
) -> tuple[str | None, ir.Node | None, ir.Node | None]:
    """
    `(message, reads, empty)` for a production's body. The production's own commit comes off the split, and `message`
    names it back.
    """
    message, body = _lifted_commit(production.body, grammar)
    reads, empty = _split(body, grammar, ways)
    return message, reads, empty


def _split_ways(grammar: dict[str, ir.Prod]) -> dict[str, tuple[bool, bool, bool]]:
    """
    `{name: (reads, empty, is_both)}`. The entry says whether a production has a way that takes a character, and whether
    it has a way that takes none. The entry also says whether the production does both while a caller chooses to enter
    it. That pairing is the defect.

    This is a least fixed point. A reference can reach back to its own production. A production takes nothing to match
    until some way of it says so. A recursion contributes neither half. A production the parse enters by name stays
    whole. Nobody chooses to enter such a production, and an empty match there decides nothing. The root and the
    recovery reach one another. Once both hold a pair of halves, a choice between them decides on nothing at all.
    """
    entered = _entered_by_name(grammar)
    ways = {name: (False, False, False) for name in grammar}
    for _round in ir.rounds("whether a production reads, matches empty, or does both"):
        settled = {}
        for name, production in grammar.items():
            _message, reads, empty = _production_split(production, grammar, ways)
            is_both = reads is not None and empty is not None and name not in entered
            settled[name] = (reads is not None, empty is not None, is_both)
        if settled == ways:
            return ways
        ways = settled
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _does_take_none_only_at_the_end(
    node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways, seen: frozenset[str] = frozenset()
) -> bool:
    """
    Whether `node` taking no character means the input has ended.

    `l-unparsed` says this outright. Its turns take any character there is, and a run is possessive. The place a turn
    cannot go is therefore past the last character. The rule writes that as a run of at least a turn, or the end. A walk
    for the nodes a production reaches with nothing taken stops where it reaches such a rule. The actions behind that
    rule need an ended input, and no parse continues from there.

    This decides rather than recognises, and a question makes the decision. It therefore raises on a kind the table does
    not name. Falling through to "this does not stop the walk" would make the answer a property of which shapes the
    question happened to name. A step that rewrote such a shape would then move the count while saying nothing about the
    grammar.
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
    A way's answer. Some part of the way says the input has ended. The answer reads that part's gate beside what the
    part performs.
    """
    parts = _parts_of_way(node) if isinstance(node, ir.AlternativeState) else node.items
    return any(_does_take_none_only_at_the_end(part, grammar, ways, seen) for part in parts)


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
        # Anything else says nothing about where the input ends. A match takes characters and an action leaves something
        # behind. The other guards ask about something else. A repetition or a consume speaks about its turns.
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
        return sum(isinstance(held, ir.EndOfStreamGuard) for held in _held(node))

    faults = []
    for name, production in grammar.items():
        if not isinstance(production.body, ir.ChoiceState):
            faults += [f"{name}: an end of stream sits in a body that offers no ways"] * ends(production.body)
            continue
        for way in production.body.alternatives:
            faults += [f"{name}: an end of stream sits among what a way performs"] * sum(
                ends(part) for part in _items_of_way(way)
            )
    return faults


_EVERY_END_OF_STREAM_SITS_IN_A_GATE = _Invariant(
    "every-end-of-stream-sits-in-a-gate", _every_end_of_stream_sits_in_a_gate
)


def _entered_unconsumed(
    node: ir.Node, grammar: dict[str, ir.Prod], ways: _Ways, entering: _Paths = frozenset()
) -> set[str]:
    """
    The productions `node` can enter with nothing taken. That is its left corner, as names.

    The walk follows a guard like anything else. A guard tests where the parse is, and the node a guard reaches sits at
    that same position. A recovery is different. The parse enters a recovery where an abandoned parse stopped, rather
    than where the recovery's rule began. A `(recover)` therefore contributes what its item does and no more.

    The walk takes a way's gate along with its parts, and the questions the gate asks count here too. `entering` holds
    the questions a way entering this production asked where it entered. A question that moved up to a caller still
    holds here, and a consume it tested still reads. A gate admitting just the end of the input stops the walk at its
    `EndOfStreamGuard`. A parse with input left does not enter that way. This walk covers the nodes a parse arrives at
    and stops on.

    A `DidConsumeSinceOpenGuard` stops the walk on the reverse ground. That guard asks for a character taken since its
    own open. A parse reaching the guard therefore took a character, and what sits behind the guard is past where the
    parse began. A run said as a recursion then reaches itself only after it has moved. The guard is the proof, and the
    turn it guards may be anything at all.
    """
    return _ENTERED_UNCONSUMED(node, grammar, ways, entering)


def _entered_by_parts(
    parts: Iterable[ir.Node], grammar: dict[str, ir.Prod], ways: _Ways, _entering: _Paths
) -> set[str]:
    """
    The productions a run of parts enters with nothing taken. The walk takes the parts in turn, up to the first part the
    parse cannot pass without taking a character.
    """
    reached: set[str] = set()
    for item in parts:
        reached |= _entered_unconsumed(item, grammar, ways)
        # A turn that must take a character has taken it wherever this is reached, and the guard asks that and no more.
        # The parse stops there, and the turn itself makes no difference to the run behind the guard.
        if isinstance(item, ir.DidConsumeSinceOpenGuard):
            break
        # This part consumes throughout, or consumes nothing only where the input has ended. Either way nothing behind
        # it is entered where the parse still stands and can go on.
        if not _is_nullable(item, grammar, ways) or _does_take_none_only_at_the_end(item, grammar, ways):
            break
    return reached


# The productions a kind enters at its own position. The lookarounds and the exclusion are named by
# `ir.WALKED_UNCONSUMED` and by the takes-nothing group both. A lookaround takes no character *and* tests a pattern at
# its own position. This binding therefore walks into what a lookaround holds, and the group below leaves the
# lookarounds out. A chain settled that overlap by the order its tests happened to come in. A question refuses to have
# it settled twice.
_ENTERED_UNCONSUMED: ir.Question[set[str]] = ir.Question(
    "the set of production names a match can enter with nothing taken",
    {
        ir.RefCall: lambda node, grammar, ways, entering: {node.name},
        (ir.AltTree, ir.ChoiceState): lambda node, grammar, ways, entering: {
            name for item in _ways_or_items(node) for name in _entered_unconsumed(item, grammar, ways, entering)
        },
        ir.AlternativeState: lambda node, grammar, ways, entering: _entered_by_parts(
            _parts_of_way(node), grammar, ways, entering
        ),
        ir.SeqTree: lambda node, grammar, ways, entering: _entered_by_parts(node.items, grammar, ways, entering),
        ir.BindTree: lambda node, grammar, ways, entering: _entered_unconsumed(node.cond, grammar, ways),
        ir.WALKED_UNCONSUMED: lambda node, grammar, ways, entering: (
            set() if node.item is None else _entered_unconsumed(node.item, grammar, ways)
        ),
        # A character question or a zero-width action reaches no production at its own position.
        tuple(
            kind for kind in (*ir.CONSUMING, *ir.CONSUMES_NOTHING) if kind not in ir.WALKED_UNCONSUMED
        ): lambda node, grammar, ways, entering: set(),
    },
)


def _no_production_reaches_itself_unconsumed(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that no production reaches itself with nothing taken. A parse arriving there cannot go on.

    The generator builds a pushdown that commits to the first gate that fires and does not backtrack. The pushdown
    cannot notice a parse arriving at a position it already reached. A production reaching itself at the same position
    runs for ever. Such a production is therefore a fault rather than a shape to handle.

    The stream and the recovery are mutually recursive by design under a resuming policy. `l-recover` is `l-unparsed`
    and then the stream again. A resumed document can therefore fail again without a second mechanism for it.
    `l-unparsed` keeps that pair from going round for ever. `l-unparsed` is a guard and a possessive run. The run's
    turns accept any character. The run takes nothing where the input has ended, and there `<end-of-stream>` answers
    rather than the stream continuing. `_does_take_none_only_at_the_end` states that behavior as a name. This function's
    walk therefore follows the pair's edge rather than exempting it. A start state is a production like any other once a
    call reaches it.
    """
    ways = _split_ways(grammar)
    entering = _asked_where_entered(grammar)
    edges = {
        name: _entered_unconsumed(production.body, grammar, ways, entering[name])
        for name, production in grammar.items()
    }
    held = _names_that_reach_themselves(edges)
    return [
        f"{name}: reaches itself with nothing taken, and a parse that arrives there cannot go on"
        for name in grammar
        if name in held
    ]


def _names_that_reach_themselves(edges: Mapping[str, Iterable[str]]) -> set[str]:
    """
    The names of `{name: the names it reaches in a step}` that reach themselves along a step or more. Those are the
    names in a circle, plus the names that are their own step.
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


# Registered under `_circles`. `_circles` walks for the loops. The checker above reports what that walk found.
_NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED = _Invariant(
    "no-production-reaches-itself-unconsumed", _no_production_reaches_itself_unconsumed
)


def _hold_established_indents(grammar: dict[str, ir.Prod], namer: _Namer) -> dict[str, ir.Prod]:
    """
    Make an established indentation a level on the parse's stack rather than something a call hands back.

    This is a rewrite rather than a question. It reshapes `ir.SeqTree` and hands back a node of any other kind.

    A block scalar cannot measure its content indentation before the parse reads the first content line. That line
    measures the indentation. The value travels back out through the calls that passed the parameter itself. That is a
    write whose readers sit a production away. A local checker cannot check that write. An edit taking the parameter out
    would silently take the write apart. This step therefore inlines the chain until the write and its reader are in the
    same way. The write is the push. The way's pop later takes that push back.

    Inlining makes the pair local. The callee runs under the indentation of the caller. The argument is the parameter.
    The callee's body, spliced in, says what it said when called. The value the callee left in `n` stays in that same
    `n`.
    """

    # Which productions hand an indentation back is a property of the grammar this step was given, and the step does not
    # change it as it goes. It is worked out once, rather than again for each node it looks at.
    establishing = _establishing(grammar)

    def does_establish(node: ir.Node) -> bool:
        """Whether `node` is a call whose production hands an indentation back to its caller."""
        return isinstance(node, ir.RefCall) and node.name in establishing and _is_by_reference(grammar, node)

    def spliced(items: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
        """`items` with a call handing an indentation back replaced by what that production does."""
        while any(does_establish(item) for item in items):
            held = []
            for item in items:
                if not does_establish(item):
                    held.append(item)
                    continue
                body = grammar[getattr(item, "name")].body
                held += list(body.items) if isinstance(body, ir.SeqTree) else [body]
            items = tuple(held)
        return items

    def bounded(items: tuple[ir.Node, ...]) -> tuple[ir.Node, ...]:
        """`items` with the indentation's write turned into the push that its way takes back."""
        for position, item in enumerate(items):
            if isinstance(item, ir.SetVarAction) and item.param == "n":
                rest, pair = bounded(items[position + 1 :]), namer.pair()
                return (
                    items[:position]
                    + (ir.PushIndentAction(level=item.value, pair=pair),)
                    + rest
                    + (ir.PopIndentAction(level=None, pair=pair),)
                )
        return items

    def held(node: ir.Node) -> ir.Node:
        node = ir.rebuilt(node, held)
        return ir.SeqTree(items=bounded(spliced(node.items))) if isinstance(node, ir.SeqTree) else node

    return {name: dataclasses.replace(production, body=held(production.body)) for name, production in grammar.items()}


def _no_production_writes_the_indentation(grammar: dict[str, ir.Prod]) -> list[str]:
    """
    Check that a production does not write the indentation. The parse then holds the indentation on the stack, rather
    than a call handing the value back.

    The reader of such a write sits a production away from the write. The write itself does not say where the value's
    region runs. The parameter taking the value out holds the region together.
    """
    return [
        f"{name}: writes the indentation for its caller to read back"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.SetVarAction) and node.param == "n"
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

    The parameter stays beside the stack. The run holds the pair to one another, and a read of `n` compares the stack
    against the parameter. The corpus therefore says the pushes go where they should, and no argument says so.
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
    takes the write out. This is a least fixpoint, and a call chain establishes through it.

    The least fixpoint begins at a block scalar's first content line. The whole scalar measures against that line's
    indentation, and the scalar cannot know it before reading that line. The indentation therefore arrives on the call's
    return rather than on the call. The value holds for the region that follows the call.
    """
    names = {
        name
        for name, production in grammar.items()
        if any(isinstance(node, ir.SetVarAction) and node.param == "n" for node in _held(production.body))
    }
    for _round in ir.rounds("the productions that establish n by writing it or by calling a production that does"):
        reached = {
            name
            for name, production in grammar.items()
            for node in _held(production.body)
            if isinstance(node, ir.RefCall) and node.name in names and _is_by_reference(grammar, node)
        }
        if reached <= names:
            return names
        names |= reached
    raise AssertionError("`ir.rounds` raises where the rounds run out")


def _is_by_reference(grammar: dict[str, ir.Prod], node: ir.RefCall) -> bool:
    """Whether the call `node` hands the indentation itself. A callee's write reaches the caller through such a call."""
    callee = grammar.get(node.name)
    if callee is None or "n" not in callee.params:
        return False
    position = callee.params.index("n")
    argument = node.args[position] if position < len(node.args) else None
    return isinstance(argument, ir.ParamValue) and argument.name == "n"


def _is_reading(grammar: dict[str, ir.Prod], production: ir.Prod, param: str) -> bool:
    """
    Whether `production` reads `param`. A write says where a value begins and a pass moves it. Neither is a read.

    A parameter passed as itself is by reference. The pass takes the value to the construct that measures it, or to the
    construct that asks. The `ParamValue` in that argument is therefore the pass, and not a use of the value here. A
    `ParamValue` inside an argument that works something out is a read like any other, and the caller evaluates it.
    """
    passed = set()
    for node in _held(production.body):
        if not isinstance(node, ir.RefCall):
            continue
        callee = grammar.get(node.name)
        declared = () if callee is None else callee.params
        for position in range(min(len(node.args), len(declared))):
            argument = node.args[position]
            if declared[position] == param and isinstance(argument, ir.ParamValue) and argument.name == param:
                passed.add(id(argument))
    return any(
        isinstance(node, ir.ParamValue) and node.name == param and id(node) not in passed
        for node in _held(production.body)
    )


def _is_bounded(production: ir.Prod, param: str) -> bool:
    """Whether `production` says where `param` stops applying. Its way ends on the clear."""
    body = production.body
    items = body.items if isinstance(body, ir.SeqTree) else (body,)
    return bool(items) and isinstance(items[-1], ir.ClearVarAction) and items[-1].param == param


def _clear_reads(param: str) -> Callable[[dict[str, ir.Prod], _Namer], dict[str, ir.Prod]]:
    """
    The transform gives `param` an end. The consuming production clears the value on return, once its calls have
    returned.

    The reader clears the value, and the writer leaves it in place. The construct that opens a value measures the value,
    such as a block scalar's floor deep inside its leading empties. That construct hands the value up to whoever asked.
    Clearing at the write would take the value from the reader that wanted it. The way holding the read takes the value
    and uses it. Once that way returns, the value has no reader left. That makes the value a single region long, and
    lets a single slot hold it.
    """

    def transform(grammar: dict[str, ir.Prod], _namer: _Namer) -> dict[str, ir.Prod]:
        def bounded(production: ir.Prod) -> ir.Prod:
            if not _is_reading(grammar, production, param) or _is_bounded(production, param):
                return production
            body = production.body
            items = body.items if isinstance(body, ir.SeqTree) else (body,)
            return dataclasses.replace(production, body=ir.SeqTree(items=items + (ir.ClearVarAction(param=param),)))

        return {name: bounded(production) for name, production in grammar.items()}

    return transform


def _a_read_is_bounded(param: str) -> _Invariant:
    """
    The invariant is that a production reading `param` says where the value stops.

    A read past the value's end raises. It does not answer from what the last construct left. That holds a value to a
    single region. A single slot holding the parameter needs that too. A stale answer reads exactly like a live answer.
    """

    def test(grammar: dict[str, ir.Prod]) -> list[str]:
        return [
            f"{name}: reads `{param}` and does not say where the value stops"
            for name, production in grammar.items()
            if _is_reading(grammar, production, param) and not _is_bounded(production, param)
        ]

    return _Invariant(f"a-read-of-{param}-is-bounded", test)


# `every-called-alternative-is-unconditional` waits for a phase to claim it. Settling that invariant at the door would
# put the steps from the first under a law the pipeline does not yet need. A question the pipeline is not asking costs a
# lapse on any step that touches a gate. That is noise about the declarations, rather than news about the grammar.
#
# The grammar arrives already holding `_EVERY_CONDITIONAL_WAY_IS_GATED`. A step here does not count as having
# established it. The grammar holds no conditional way until `build-alternatives` says a way by its parts. That step
# leaves a way the parse cannot enter, and the phase's work starts there.
_AT_THE_DOOR = _Phase("at-the-door", establishes=_EVERY_CONDITIONAL_WAY_IS_GATED)

# The specialization takes `_NO_I_T_PARAMETERS` to none. The grammar then declares, passes and reads neither the
# chomping nor the block scalar's indentation mode. Both stay data-dependent until this runs, and the specialization
# cannot reach either. The setters become switches first.
_SPECIALIZE = _Phase(
    "specialize",
    settles=_NO_I_T_PARAMETERS,
    steps=(
        _Step("lift-setters", _lift_setters, reduces=_FINITE_PARAMETERS_ARE_ALWAYS_LITERAL),
        _Step(
            "monomorphize",
            _monomorphize,
            settles=(
                _absent("no-context-case", ir.CaseTree, ir.FlipValue),
                _FINITE_PARAMETERS_ARE_ALWAYS_LITERAL,
                _NO_I_T_PARAMETERS,
                # The count reaches none once the specialization has run, and no earlier. A way cannot answer questions
                # while a context picks between shapes.
                _EVERY_OPTION_IS_REACHABLE,
            ),
        ),
    ),
)

# The pruning takes `no-fails` to none. Once a step runs past the specialization, a question behind that step asks about
# shapes some input reaches.
_NO_FAILS = _absent("no-fails", ir.FailTree)

# The phase takes `_NO_FAILS` to none. The grammar then holds no match that no input makes. A `<fail>` is a branch of a
# live `(case)` until the specialization has run, and what a branch says is not yet what a production does. The phase
# therefore follows the specialization.
_PRUNE_FAILURES = _Phase(
    "prune-failures",
    settles=_NO_FAILS,
    steps=(_Step("prune-failures", _prune_failures, settles=_NO_FAILS),),
)

# The phase takes `_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET` to none. A question about a character is a `CharSet`. A
# set the context picks denotes nothing until the specialization has bound the context. The phase therefore follows the
# specialization. First, the distribution takes a difference into the ways it subtracts from. A subtraction says a set
# where both its sides say one.
_CHARACTER_SETS = _Phase(
    "character-sets",
    settles=_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET,
    steps=(
        _Step("distribute-differences", _distribute_differences, settles=_EVERY_DIFFERENCE_IS_BETWEEN_CHARACTER_SETS),
        _Step(
            "lower-char-sets",
            _lower_char_sets,
            settles=(_EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET, _NO_DIFF_NODES, _EVERY_PEEK_IS_A_CHARACTER_SET),
        ),
    ),
)

# The phase takes `_NO_F_PARAMETER` to none. The block scalar's leading-empty floor is a single value for the parse,
# rather than a value a call passes. The clear gives that value an end first. A single slot holds a parameter where a
# read past the value's region raises. The parse must not answer from what the last construct left.
_DROP_F = _Phase(
    "drop-f",
    settles=_NO_F_PARAMETER,
    steps=(
        _Step("clear-f", _clear_reads("f"), settles=_a_read_is_bounded("f")),
        _Step("read-global-f", _read_off("f", ir.GlobalValue(name="f")), settles=_NO_F_PARAMETER),
    ),
)

# The phase takes `_NO_M_PARAMETER` to none. The detected indent is a single value for the parse. A reader does not take
# it twice over a region something else can write in. The block header measures the indent, and the scalar that asked
# reads it, a construct at a time. A clear and a drop therefore cover the phase.
_DROP_M = _Phase(
    "drop-m",
    settles=_NO_M_PARAMETER,
    steps=(
        _Step("clear-m", _clear_reads("m"), settles=_a_read_is_bounded("m")),
        _Step("read-global-m", _read_off("m", ir.GlobalValue(name="m")), settles=_NO_M_PARAMETER),
    ),
)

# The phase takes `_NO_N_PARAMETER` to none. The indentation is on the parse's stack. A call does not pass it. The
# pushes go in first and the parameter stays beside them. A read comparing the pair over the corpus says the pushes go
# where they should. Dropping the parameter leaves the stack to answer.
_DROP_N = _Phase(
    "drop-n",
    settles=_NO_N_PARAMETER,
    steps=(
        _Step("hold-established-indents", _hold_established_indents, settles=_NO_PRODUCTION_WRITES_THE_INDENTATION),
        _Step("push-indents", _push_indents, settles=_EVERY_INDENTATION_CHANGE_IS_PUSHED),
        _Step("read-indents", _read_off("n", ir.IndentValue()), settles=_NO_N_PARAMETER),
    ),
)

# The phase takes `_NO_OPT_NODES` and `_NO_STAR_OR_PLUS_NODES` to none. A node then hides no match that may take none.
# An empty match is a way beside the way that reads. A repetition is a consume of a class, or the ways `lower-runs`
# makes. The phase does not reach `no-conditional-production-matches-empty`. That invariant says that a conditionally
# entered production matches no empty. The pipeline holds no step that settles it.
_LOWER_REPETITIONS = _Phase(
    "lower-repetitions",
    settles=(_NO_OPT_NODES, _NO_STAR_OR_PLUS_NODES),
    steps=(
        _Step("lower-optionals", _lower_optionals, settles=_NO_OPT_NODES),
        # `no-production-reaches-itself-unconsumed` reaches none from here, and an earlier grammar cannot answer it. The
        # checker takes a cycle off the ways a production offers, and the optionals sit outside those ways.
        _Step("holds-once-optionals-are-ways", establishes=_NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED),
        _Step(
            "span-consumes",
            _span_consumes,
            settles=(_EVERY_CHARACTER_RUN_IS_A_SPAN, _EVERY_EXCLUSION_IS_BOUNDED),
            reduces=_NO_STAR_OR_PLUS_NODES,
            # The step makes the run and its question side by side. A later step may put nothing between the pair. This
            # site states that guarantee.
            establishes=_EVERY_SPAN_QUESTION_FOLLOWS_ITS_RUN,
        ),
        _Step("lower-runs", _lower_runs, settles=_NO_STAR_OR_PLUS_NODES),
    ),
)

# The phase takes `_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS` to none. The parse matches an exclusion's pattern and throws
# it away. The step gives the exclusion a copy holding what matches and asks. The step runs while a `(token)` is still
# the node saying what code characters take. Such a copy has to drop that node.
_FORBIDDEN_PROBES = _Phase(
    "forbidden-probes",
    settles=_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS,
    steps=(_Step("mint-forbidden-probes", _mint_forbidden_probes, settles=_EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS),),
)

# The phase takes the scope nodes to none. `_NO_WRAP_NODES`, `_NO_MAX_NODES` and `_NO_COMMIT_NODES` go.
# `_NO_TOKEN_NODES` and `_NO_EXCLUDE_AT_NODES` go with them. A scope is then the pair of writes that bound it, and no
# node holds what a scope covers. A step takes a single kind. The wrap and the window write both halves. The commit and
# the token write both halves. Both halves name the pair they belong to. The parse then keeps the guarantee a wrapper
# gave by construction. The exclusion names no pair. The set is a single value for the parse. The exclusion's closing
# write names what comes after it. It takes no open back.
_LOWER_SCOPES = _Phase(
    "lower-scopes",
    settles=(_NO_WRAP_NODES, _NO_MAX_NODES, _NO_COMMIT_NODES, _NO_TOKEN_NODES, _NO_EXCLUDE_AT_NODES),
    steps=(
        _Step("lower-wraps", _lower_wraps, settles=_NO_WRAP_NODES),
        _Step("lower-windows", _lower_windows, settles=_NO_MAX_NODES),
        _Step("lower-commits", _lower_commits, settles=_NO_COMMIT_NODES),
        _Step("lower-tokens", _lower_tokens, settles=_NO_TOKEN_NODES),
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
        _Step(
            "lift-choices",
            _lifting(ir.AltTree),
            settles=_EVERY_CHOICE_IS_A_PRODUCTION,
            reduces=_EVERY_SUB_ITEM_IS_ONE_STEP,
        ),
        _Step(
            "lift-recoveries",
            _lifting(ir.RecoverWrapper),
            settles=_EVERY_RECOVERY_IS_A_PRODUCTION,
            reduces=_EVERY_SUB_ITEM_IS_ONE_STEP,
        ),
        _Step("lower-bind", _lower_bind, settles=(_EVERY_SUB_ITEM_IS_ONE_STEP, _NO_BIND_NODES)),
    ),
)

# The phase takes `_NO_CHOICE_OF_CHOICES` to none. A way of a choice is then no call to a choice. A way sits where a
# gate can go on it, rather than a call below. The phase runs before the split into a call and a continuation. A choice
# written out here is a choice the phases behind this see whole. The same phase flattens a run of items nested inside a
# way's own items. That lowers `no-sequence-of-sequences`, and `build-alternatives` takes it to none.
_FLATTEN_CALLS = _Phase(
    "flatten-calls",
    settles=_NO_CHOICE_OF_CHOICES,
    steps=(
        _Step("flatten-called-alternations", _flatten_called_alternations, settles=_NO_CHOICE_OF_CHOICES),
        _Step("flatten-called-sequences", _flatten_called_sequences, reduces=_NO_SEQUENCE_OF_SEQUENCES),
    ),
)

# The phase takes `_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION` to none. A way does its actions, hands control to a
# single production, and says where the path continues. The parts past the call continue.
_MINT_CONTINUATIONS = _Phase(
    "mint-continuations",
    settles=_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION,
    steps=(_Step("mint-continuations", _mint_continuations, settles=_A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION),),
)

# The phase takes `_EVERY_BODY_IS_A_CHOICE_OR_A_SET` to none. A body then speaks the machine's words. A body is a set of
# characters, or the ordered list of alternatives the parse takes one of. The lowering already said a repetition as the
# ways it holds. `no-sequence-of-sequences` reaches none with the bodies built here.
_BUILD_ALTERNATIVES = _Phase(
    "build-alternatives",
    settles=_EVERY_BODY_IS_A_CHOICE_OR_A_SET,
    steps=(
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
            # The step builds a way with a gate. The gate's shape comes into question here. A pair of questions about a
            # single character sit apart, and a hoist brings them together.
            establishes=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE,
        ),
    ),
)

# The phase takes `_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL` to none. A way with no gate is a way still awaiting one. A
# call past the way's actions puts the callee's guards out of reach of where the parse enters the way. A call with a
# state of its own puts the callee's guards and the call's entry at the same position. A step hoisting a guard out of a
# callee rests on that shared position. A step writing a callee's ways into its caller does the same.
_CALL_STATES = _Phase(
    "call-states",
    settles=_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL,
    steps=(_Step("mint-call-states", _mint_call_states, settles=_EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL),),
)

# The phase takes `_NO_GUARD_COMES_PAST_AN_ACTION` and `_EVERY_GUARD_IS_IN_A_GATE` to none. A question a way asks sits
# in the way's gate, and what the way performs comes behind that gate. The grammar says outright whether a way's part is
# a question or an action. The parts run in order. The phase cuts a way that would ask a guard past an action. The
# guards the way holds move into its gate. The questions about the character in front combine into a single set.
#
# The phase does not take those gates out to the ways that call them. The gate stays inside the way and covers what the
# way performs, not what the caller performs before the call. On a path the gate refuses, the way performs none of its
# actions. Such an action is a `PushCodeAction` that did not cut the run, or a `PopMessageAction` that did not leave the
# region. A call site performs something, and the gate cannot move until it sits next to the call. Determinization makes
# that move under `every-called-alternative-is-unconditional`.
_GUARDS_INTO_GATES = _Phase(
    "guards-into-gates",
    settles=(_NO_GUARD_COMES_PAST_AN_ACTION, _EVERY_GUARD_IS_IN_A_GATE),
    steps=(
        _Step("mint-guard-states", _mint_guard_states, settles=_NO_GUARD_COMES_PAST_AN_ACTION),
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
        _Step("merge-gate-peeks", _merge_gate_peeks, settles=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    ),
)

# The phase takes `_NO_CHAR_SET_IS_AN_ITEM` and `_EVERY_CONSUME_IS_PROTECTED_BY_A_GATE` to none. A gate asks the
# question where a caller can see it. The consume then takes the character on the gate's word. The phase is where the
# gates come from. A hoist moves a question that already exists. This phase runs before hoisting could produce such a
# question. A pair of steps does the work. The phase cuts a way that takes a character on the strength of an earlier
# one. The phase repeats the cut until a way takes a single character. A way then holds a single set. The question sits
# where the parse enters the way.
_CONSUMES_BEHIND_GATES = _Phase(
    "consumes-behind-gates",
    settles=_NO_CHAR_SET_IS_AN_ITEM,
    steps=(
        _Step("mint-consume-states", _mint_consume_states, settles=_EVERY_WAY_TAKES_AT_MOST_ONCE),
        _Step(
            "split-consumes-into-gates",
            _split_consumes_into_gates,
            settles=_NO_CHAR_SET_IS_AN_ITEM,
            reduces=_EVERY_CONDITIONAL_WAY_IS_GATED,
        ),
    ),
)

# The phase is for `_EVERY_CONDITIONAL_WAY_IS_GATED`. A question on the input decides whether the parse enters a way.
# The phase moves questions that already exist. The questions move out of a callee offering a single way, and into the
# choice that has to tell its ways apart. The callee still enters under what it gave up. `_asked_where_entered` says so.
# The move keeps a take protected where the moved guard protected that take. The steps here leave the invariant
# unsettled, and `unsettled_invariants` reports what remains at the end.
#
# The phase opens on a pair of claims rather than transforms. The first claim says a leaf way answers without a callee,
# takes its own actions and follows nothing on. The second claim says a leaf way accepts in the space it enters in,
# before any step moves a guard. The grammar arrives holding both claims, and the steps below have to keep them.
#
# A pair of steps does the moving, and they move in opposite directions. A guard climbs to the callers that enter it. A
# way nothing has gated reaches down for the gates of the productions it runs. The step reaching down takes a whole tree
# at once, and no step here writes a callee's ways out a level at a time.
_GATE_THE_WAYS = _Phase(
    "gate-the-ways",
    establishes=(_ACCEPTED_AND_GATED_CHARSETS_ARE_EQUAL, _EVERY_PATH_REACHES_A_LEAF_WAY),
    settles=_EVERY_CONDITIONAL_WAY_IS_GATED,
    steps=(
        _Step("hoist-guards-to-callers", _hoist_guards_to_callers, reduces=_EVERY_CONDITIONAL_WAY_IS_GATED),
        _Step("flatten-ungated-call-trees", _flatten_ungated_call_trees, settles=_EVERY_CONDITIONAL_WAY_IS_GATED),
    ),
)

# The phase takes `_NO_CHOICE_WAYS_PARTIALLY_OVERLAP` to none. The phase decides between a pair of ways. That pair
# enters in the same states, or in no shared state. The phase follows the gating phase. A way with no gate enters
# anywhere. The phase could take nothing apart from such a way. The phase comes before anything determinizing.
# Determinizing has to settle the ways left together.
_SPLIT_OVERLAPS = _Phase(
    "split-overlaps",
    settles=_NO_CHOICE_WAYS_PARTIALLY_OVERLAP,
    steps=(
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
        _Step("merge-gate-peeks-2", _merge_gate_peeks, settles=_EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    ),
)

# The phases appear in the order the pipeline runs them. A phase holds the steps that serve it. A step below keeps its
# place. The step's rules sit beside the step.
_PHASES = (
    _AT_THE_DOOR,
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
    _CALL_STATES,
    _GUARDS_INTO_GATES,
    _CONSUMES_BEHIND_GATES,
    _GATE_THE_WAYS,
    _SPLIT_OVERLAPS,
)

# The phases flattened. A reader of the pipeline takes the steps in this order.
#
# `_lower_continuations_into_conflicts` is in the module and in no phase. It serves `_EVERY_CONFLICT_IS_A_TAIL_CALL`.
# That invariant says the parse enters a conflicting production at the end of a way. A conflict is a production the
# machine cannot steer through. The decision between that production's ways then sits inside it. The transform moves the
# continuations that make it so, and the count does not fall. The copies hold the call sites their originals held. The
# count rises by a conflict per continuation the copying gives a production. A step telling the copies' ways apart would
# take those call sites away, and the pipeline holds no such step. A step here would therefore name no invariant and no
# reason for naming none. `untested_steps` refuses that. `OWED` holds the count it serves.
STEPS = [step for phase in _PHASES for step in phase.flattened()]

# The name of a phase of the pipeline, in the order `_PHASES` gives them. Prose cites a phase by its name, and
# `check_documents` verifies such a citation against this.
PHASE_NAMES = tuple(phase.name for phase in _PHASES)

# The invariants the pipeline owes and no phase has taken on. The run counts them at the end, beside what the steps
# name. A phase pursuing an invariant names that invariant on the steps serving it, and the invariant comes out of here
# then. Claiming the invariants at the start of the pipeline would instead hold the earlier steps to a question the
# pipeline is not asking. That costs a lapse on the steps that touch a gate. It is noise about the declarations, not
# news about the grammar.
OWED = (_EVERY_CHOICE_WAY_IS_DIFFERENT, _EVERY_CONFLICT_IS_A_TAIL_CALL)


def invariants_by_name() -> dict[str, _Invariant]:
    """The invariants the steps name and those still owed, keyed by the name they are cited by."""
    held = {one.name: one for step in STEPS for one in step.invariants}
    held.update({one.name: one for one in OWED})
    return held
