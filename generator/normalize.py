# SPDX-License-Identifier: MIT
"""
The normalization pipeline: the ordered, semantics-preserving transformations that carry the hand-authored grammar
toward the canonical form a state machine falls out of.

A transformation is a function from a grammar to a grammar — a grammar being the `{name: ir.Prod}` mapping
`annotated2ir.load()` returns, its nodes the frozen dataclasses of `ir`. Each is small enough to prove by eye and is
held to preserving the interpreter's token stream over the whole corpus, step by step, by `check_normalize`. `STEPS`
lists them in order as `(name, transform)` pairs, so a step is named wherever it passes or fails; the pipeline is the
one seam every transformation slots into.
"""

import dataclasses

import ir
import spec_tests

# Nodes that begin no character — a match of one starts no run, so it adds nothing to a first-character set: the
# lookaheads, the epsilon and marker emitters, the guards, and the parameter actions.
_ZERO_WIDTH = ir.ZERO_WIDTH + (  # in alphabetical order
    ir.CloseMatch,
    ir.CloseWindow,
    ir.Cut,
    ir.Emit,
    ir.Empty,
    ir.EndOfStream,
    ir.Error,
    ir.Increase,
    ir.Le,
    ir.Lt,
    ir.OpenMatch,
    ir.OpenWindow,
    ir.PopCode,
    ir.PopMessage,
    ir.PushCode,
    ir.PushMessage,
    ir.SetVar,
    ir.StartOfLine,
)


class Namer:
    """
    Fresh helper-production names, `<base>_<N>`, the `<N>` the next unused per base across the whole pipeline.

    One is threaded through every step, so a base's count carries across them: a helper minted for `foo` is `foo_1`, the
    next `foo_2`, and one minted while a later step processes `foo_3` is `foo_4` — never `foo_3_1`, since the base is
    `foo` with any `_<N>` suffix stripped. Two steps minting for the same base do not collide.
    """

    def __init__(self):
        self._counts = {}

    def fresh(self, owner):
        """A fresh `<base>_<N>` name for a helper of `owner`, the base being `owner` without its `_<N>` suffix."""
        head, _underscore, tail = owner.rpartition("_")
        base = head if tail.isdigit() and head else owner
        self._counts[base] = self._counts.get(base, 0) + 1
        return f"{base}_{self._counts[base]}"


def _branch(node, value):
    """The item of the `(case)`/`(flip)` `node`'s branch for `value` — its `else` default where no branch names it."""
    for branch in node.branches:
        if branch.value == value:
            return branch.item
    default = getattr(node, "default", None)
    if default is not None:
        return default
    raise ValueError(f"{node.var} has no branch for {value!r}")


def _uses(node, param):
    """Whether `Param(param)` appears anywhere in `node`."""
    if isinstance(node, ir.Param):
        return node.name == param
    found = []
    ir.rebuilt(node, lambda child: found.append(_uses(child, param)) or child)
    return any(found)


def _substitute(node, param, value):
    """`node` with each `Param(param)` replaced by `Lit(value)`."""
    if isinstance(node, ir.Param) and node.name == param:
        return ir.Lit(value)
    return ir.rebuilt(node, lambda child: _substitute(child, param, value))


def _finite_setter(node):
    """
    `(param, [value, ...], {value: condition})` where `node` is an alternation of `Bind`s that each match a condition
    and set one finite parameter to a literal — a data-dependent setter of a finite parameter — else `None`. The values
    are in the alternation's order, which is the order the choice lifting it must try them in.
    """
    if not isinstance(node, ir.Alt) or not node.items or not all(isinstance(item, ir.Bind) for item in node.items):
        return None
    params = {item.param for item in node.items}
    if len(params) != 1 or not all(isinstance(item.value, ir.Lit) for item in node.items):
        return None
    (param,) = params
    if param not in ir.FINITE_PARAMS:
        return None
    return param, [item.value.value for item in node.items], {item.value.value: item.cond for item in node.items}


def _dispatch(body, param, values):
    """
    `body` with the tail that uses `param` — its first use to the end of the top-level sequence — replaced by an ordered
    choice over `values`, each branch substituting the literal for `Param(param)`.
    """
    items = body.items if isinstance(body, ir.Seq) else (body,)
    first = next(index for index, item in enumerate(items) if _uses(item, param))
    prefix, tail = items[:first], items[first:]
    choice = ir.Alt(tuple(ir.Seq(tuple(_substitute(item, param, value) for item in tail)) for value in values))
    return ir.Seq(prefix + (choice,)) if prefix else choice


def lift_chomping(grammar, namer):
    """
    Make a data-dependent finite parameter lexical, so it monomorphizes like the context. The chomping `t` is the one:
    `c-chomping-indicator` matches an indicator and sets `t` — strip, keep, or clip — which the block scalar reads two
    productions later, through the env. That set is not a switch, so `t` cannot be specialized. This inverts the setter
    into a `(case) t` that matches the indicator for a given `t`, and turns each production that holds `t` as a local
    out-parameter into an ordered choice over its values — each branch fixing `t` to a literal it hands the setter and
    the reader alike. The parse tries the values in the setter's order, so exactly the one whose indicator is present
    matches, and `t` flows as a value rather than stashed state.
    """
    setters = {name: setter for name in grammar if (setter := _finite_setter(grammar[name].body))}
    values = {param: ordered for param, ordered, _conditions in setters.values()}
    result = {}
    for name, production in grammar.items():
        if name in setters:
            param, ordered, conditions = setters[name]
            body = ir.Case(param, tuple(ir.Branch(value, conditions[value]) for value in ordered))
        else:
            body = production.body
            for param in ir.FINITE_PARAMS:
                if param in values and param not in production.params and _uses(body, param):
                    body = _dispatch(body, param, values[param])
        result[name] = dataclasses.replace(production, body=body)
    return result


def _relevant_finite(grammar):
    """
    For each production, the finite parameters its specialized subtree depends on — the ones a monomorphic copy must fix
    in its name. A production's own body reads some directly (a `Case`/`Flip` on one, or one passed as itself); the rest
    it inherits and hands down, so a callee's relevant parameters are relevant to the caller too, save the ones the
    caller passes an argument for. A least fixed point, since a reference can reach back to its own production.
    """
    reads, calls = {}, {}
    for name, production in grammar.items():
        direct, references = set(), []

        def gather(node):
            if isinstance(node, (ir.Case, ir.Flip)) and node.var in ir.FINITE_PARAMS:
                direct.add(node.var)
            if isinstance(node, ir.Param) and node.name in ir.FINITE_PARAMS:
                direct.add(node.name)
            if isinstance(node, ir.Ref):
                passed = {p for p, _argument in zip(grammar[node.name].params, node.args) if p in ir.FINITE_PARAMS}
                references.append((node.name, passed))
            ir.rebuilt(node, lambda child: gather(child) or child)

        gather(production.body)
        reads[name], calls[name] = direct, references
    relevant = {name: set(direct) for name, direct in reads.items()}
    changed = True
    while changed:
        changed = False
        for name in grammar:
            for callee, passed in calls[name]:
                inherited = relevant[callee] - passed
                if inherited - relevant[name]:
                    relevant[name] |= inherited
                    changed = True
    return relevant


def monomorphize(grammar, namer):
    """
    Specialize the lexical finite parameters — the context c — away. Each production is copied once per combination of
    their values it is reached with — from the root, following references, so only combinations that occur are made —
    its `Case` and `Flip` on them evaluated to the copy's values, its name `ir.specialized`, and those parameters
    dropped from its signature. `n`, `m`, `f`, and the runtime state `t` and `r` stay parameters.
    """
    result, done, pending = {}, set(), []

    def finite_value(expression, env):
        """
        A finite (c/t/r) value expression as its concrete value under `env`, inlining a value function — `in-flow` maps
        one context to another — the way a call to it would.
        """
        if isinstance(expression, ir.Param):
            return env[expression.name]
        if isinstance(expression, ir.Lit):
            return expression.value
        if isinstance(expression, ir.Flip):
            return finite_value(_branch(expression, env[expression.var]), env)
        if isinstance(expression, ir.Ref):
            callee = grammar[expression.name]
            inner = {p: finite_value(a, env) for p, a in zip(callee.params, expression.args)}
            return finite_value(callee.body, inner)
        raise ValueError(f"not a finite value: {expression!r}")

    def runtime_value(expression, env):
        """
        A runtime (n/m/f) argument with its `Flip`s on a finite parameter and value functions — `seq-spaces` is `n` or
        `n-1` by context — reduced under `env`, its arithmetic and runtime parameters left.
        """
        if isinstance(expression, ir.Ref):
            callee = grammar[expression.name]
            inner = {
                parameter: (
                    finite_value(argument, env) if parameter in ir.FINITE_PARAMS else runtime_value(argument, env)
                )
                for parameter, argument in zip(callee.params, expression.args)
            }
            return runtime_value(callee.body, inner)
        if isinstance(expression, ir.Flip):
            return runtime_value(_branch(expression, env[expression.var]), env)
        if isinstance(expression, ir.Param):
            return env.get(expression.name, expression)
        return ir.rebuilt(expression, lambda inner: runtime_value(inner, env))

    def specialize(node, env):
        if isinstance(node, ir.Case) and node.var in ir.FINITE_PARAMS:
            value = env.get(node.var)
            for branch in node.branches:
                if branch.value == value:
                    return specialize(branch.item, env)
            if node.default is not None:
                return specialize(node.default, env)
            return ir.Alt(())  # no branch for this value: the case declines, as the interpreter does — never matches
        if isinstance(node, ir.Ref):
            passed, args = {}, []
            for parameter, argument in zip(grammar[node.name].params, node.args):
                if parameter in ir.FINITE_PARAMS:
                    passed[parameter] = finite_value(argument, env)
                else:
                    args.append(runtime_value(argument, env))
            # the callee inherits the ambient finite values and overrides the ones this reference passes; its copy is
            # named by the finite parameters its subtree depends on, at those values, an unset one carried as `None`.
            ambient = {x: passed[x] if x in passed else env.get(x) for x in relevant[node.name]}
            pending.append((node.name, ambient))
            return ir.Ref(ir.specialized(node.name, ambient), tuple(args))
        return ir.rebuilt(node, lambda child: specialize(child, env))

    relevant = _relevant_finite(grammar)
    pending.append((ir.ROOT, {x: ir.FINITE_DEFAULTS.get(x) for x in relevant[ir.ROOT]}))
    # the fixtures are entry points too: each runs a production in isolation at the finite values its filename names,
    # reaching combinations the root does not, so seed each so its monomorphic copy is there for the driver to enter.
    for fixture in spec_tests.load():
        if fixture.production in grammar:
            ambient = {x: fixture.parameters.get(x, ir.FINITE_DEFAULTS.get(x)) for x in relevant[fixture.production]}
            pending.append((fixture.production, ambient))
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


def _lower_optionals(node):
    """
    `node` with each optional `x?` rewritten as the alternation `x | <empty>`. Bottom-up, so a parent sees its
    already-lowered children.
    """
    node = ir.rebuilt(node, _lower_optionals)
    if isinstance(node, ir.Opt):
        return ir.Alt((node.item, ir.Empty()))
    return node


def lower_optionals(grammar, namer):
    """
    Rewrite each optional `x?` as the alternation `x | <empty>` — the same match, x greedily then nothing, with the
    empty as the last, unconditional alternative the canonical form allows. Removes the `Opt` node kind.
    """
    return {
        name: dataclasses.replace(production, body=_lower_optionals(production.body))
        for name, production in grammar.items()
    }


def matches_one_char(node, grammar, seen=frozenset()):
    """
    Whether `node` matches exactly one character — a terminal char class. A `+`/`*` over one stays a single repeated
    char-set match (one SIMD call); over anything else it breaks into a sequence or a recursion. A `Char`, `Range` or
    `Invalid` is one; a `Diff` is one when its base is (the exclusions only narrow it); an `Alt` is one when every
    branch is (a union of char sets), so a lowered optional `x | <empty>` is not one; a `Ref` is one when its production
    is.
    """
    if isinstance(node, (ir.Char, ir.Range, ir.Invalid)):
        return True
    if isinstance(node, ir.Diff):
        return matches_one_char(node.base, grammar, seen)
    if isinstance(node, ir.Alt):
        return bool(node.items) and all(matches_one_char(item, grammar, seen) for item in node.items)  # empty: no match
    if isinstance(node, ir.Case):
        return all(matches_one_char(branch.item, grammar, seen) for branch in node.branches)  # a context-picked class
    if isinstance(node, ir.Ref):
        return node.name in seen or matches_one_char(grammar[node.name].body, grammar, seen | {node.name})
    return False


def matches_empty(node, grammar, seen=frozenset()):
    """
    Whether `node` can match the empty string. Conservative: a node it cannot prove non-empty is reported as matching
    empty, so a caller under-acts. A `Star` over a node that matches empty cannot become a right-recursive helper — it
    would spin on that empty match where the interpreter's own repetition stops.
    """
    if isinstance(node, (ir.Char, ir.Range, ir.Invalid, ir.Diff)):
        return False
    if isinstance(node, ir.Ref):
        return node.name in seen or matches_empty(grammar[node.name].body, grammar, seen | {node.name})
    if isinstance(node, ir.Seq):
        return all(matches_empty(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Alt):
        return any(matches_empty(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Plus):
        return matches_empty(node.item, grammar, seen)
    if isinstance(node, (ir.Token, ir.Wrap, ir.Bound, ir.Commit, ir.Recover)):
        return matches_empty(node.item, grammar, seen)
    if isinstance(node, ir.Max):
        return node.item is None or matches_empty(node.item, grammar, seen)
    if isinstance(node, ir.Case):
        return any(matches_empty(branch.item, grammar, seen) for branch in node.branches)
    if isinstance(node, ir.Bind):
        return matches_empty(node.cond, grammar, seen)
    return True


_NEVER_CONSUMES = (
    ir.StartOfLine,
    ir.EndOfStream,
    ir.Look,
    ir.NegLook,
    ir.LookBehind,
    ir.ExcludeAt,
    ir.Lt,
    ir.Le,
    ir.SetVar,
    ir.Increase,
    ir.Emit,
    ir.Cut,
    ir.Error,
)


def hoist_empty(grammar, namer):
    """
    Take the empty match out of what a repetition repeats, so nothing repeats what may consume nothing. A `x*` or `x+`
    over a nullable `x` cannot become a recursive helper — the recursion would spin where `x` takes nothing — so `x` is
    split into the matches that consume and the matches that do not, and the repetition keeps only the first. The empty
    is not lost: a repetition already means "as many as there are, including none", so it absorbs it.

    Splitting a sequence takes an ordered choice over which of its parts is the first to consume, the parts before it
    held to their empty match — which is where a `<start-of-line>` or an `<end-of-stream>` comes up, those being what
    `s-separate-in-line` and `b-comment` match empty *by*. The order is the order the parse already tried them in, so a
    greedy match still finds the same one first.
    """
    minted, lookup = {}, dict(grammar)

    def nullable(node):
        return matches_empty(node, lookup)

    def consuming_name(name):
        """
        The production matching what `name` matches and consumes; minted from its body the first time it is asked for,
        so a recursion through it resolves to the same one. While its body is being built it stands in as something that
        reads a character, which is what it is — a consuming production matches no empty, whatever its body turns out to
        be.
        """
        fresh = f"{name}_consuming"
        if fresh in minted:
            return fresh
        original = grammar[name]
        minted[fresh] = None
        lookup[fresh] = ir.Prod(original.number, fresh, original.params, ir.Invalid())
        body = consuming(original.body)
        if body is None:
            del minted[fresh], lookup[fresh]
            return None
        minted[fresh] = lookup[fresh] = ir.Prod(original.number, fresh, original.params, body)
        return fresh

    def empty(node):
        """`node` held to its empty match — the guards it matches empty by — or `None` where it cannot match one."""
        if isinstance(node, (ir.Empty, ir.Star, ir.Opt)):
            return ir.Empty()
        if isinstance(node, _NEVER_CONSUMES):
            return node
        if isinstance(node, (ir.Plus, ir.Token, ir.Wrap, ir.Bound, ir.Commit)):
            held = empty(node.item)
            return None if held is None else dataclasses.replace(node, item=held)
        if isinstance(node, ir.Seq):
            parts = [empty(item) for item in node.items]
            return None if any(part is None for part in parts) else _flat_seq(tuple(parts))
        if isinstance(node, ir.Alt):
            return next((held for held in (empty(item) for item in node.items) if held is not None), None)
        if isinstance(node, ir.Ref):
            return ir.Ref(node.name, node.args) if nullable(node) else None
        return None  # anything that reads the input matches no empty

    def consuming(node):
        """`node` held to the matches that consume a character, or `None` where it has none."""
        if isinstance(node, (ir.Char, ir.Range, ir.Diff, ir.Invalid, ir.Rep, ir.TrimStar)):
            return node if not nullable(node) else None
        if isinstance(node, (ir.Empty, ir.Star, ir.Opt) + _NEVER_CONSUMES):
            return None if not isinstance(node, (ir.Star, ir.Opt)) else consuming(node.item)
        if isinstance(node, (ir.Token, ir.Wrap, ir.Bound, ir.Commit)):
            inner = consuming(node.item)
            return None if inner is None else dataclasses.replace(node, item=inner)
        if isinstance(node, ir.Plus):
            inner = consuming(node.item)
            return None if inner is None else ir.Plus(inner)
        if isinstance(node, ir.Alt):
            kept = tuple(item for item in (consuming(item) for item in node.items) if item is not None)
            return None if not kept else (kept[0] if len(kept) == 1 else ir.Alt(kept))
        if isinstance(node, ir.Seq):
            branches = []
            for index, item in enumerate(node.items):
                inner = consuming(item)
                if inner is not None:
                    held = [empty(before) for before in node.items[:index]]
                    if any(part is None for part in held):
                        break  # a part before this one must consume, so it is the first that can
                    branches.append(_flat_seq(tuple(held) + (inner,) + node.items[index + 1 :]))
                if not nullable(item):
                    break  # this part must consume, so nothing after it can be the first that does
            return None if not branches else (branches[0] if len(branches) == 1 else ir.Alt(tuple(branches)))
        if isinstance(node, ir.Ref):
            if not nullable(node):
                return node
            fresh = consuming_name(node.name)
            return None if fresh is None else ir.Ref(fresh, node.args)
        return None

    def lift(node):
        node = ir.rebuilt(node, lift)
        if isinstance(node, (ir.Star, ir.Plus)) and nullable(node.item):
            inner = consuming(node.item)
            return ir.Star(inner) if inner is not None else ir.Empty()  # it consumed nothing, so it repeats nothing
        return node

    result = {name: dataclasses.replace(production, body=lift(production.body)) for name, production in grammar.items()}
    lifted = set()  # a minted body holds repetitions of its own, and lifting them may mint again
    while True:
        pending = [name for name, production in minted.items() if production is not None and name not in lifted]
        if not pending:
            break
        for name in pending:
            lifted.add(name)
            minted[name] = dataclasses.replace(minted[name], body=lift(minted[name].body))
    result.update({name: production for name, production in minted.items() if production is not None})
    return result


def _lower_plus(node, grammar):
    """
    `node` with each `x+` over a complex `x` rewritten as the sequence `x x*`, and each `x+` over a char class left
    alone to stay one SIMD match. Bottom-up, so a parent sees its already-lowered children.
    """
    node = ir.rebuilt(node, lambda child: _lower_plus(child, grammar))
    if isinstance(node, ir.Plus) and not matches_one_char(node.item, grammar):
        return ir.Seq((node.item, ir.Star(node.item)))
    return node


def lower_plus(grammar, namer):
    """
    Rewrite each `x+` over a complex production as the sequence `x x*` — one match then zero or more, the same
    one-or-more. A `x+` over a character class stays as it is, for the alternative shape to spell as a gate on `[x]` and
    a single span scan. Every complex `x+` in the grammar is over a production that consumes, so the sequence never
    matches `x` a second time where `x+` would not.
    """
    return {
        name: dataclasses.replace(production, body=_lower_plus(production.body, grammar))
        for name, production in grammar.items()
    }


def lower_star(grammar, namer):
    """
    Rewrite each `x*` over a complex, always-consuming production as a fresh right-recursive helper `_N ::= x _N |
    <empty>`, the star replaced by a reference to it. A `x*` over a character class stays as it is, to map later to a
    single repeated-char-set SIMD call; one over a node that can match empty stays too, since the recursion would spin
    on that empty match — it waits for the zero-width guard a later step brings. The helper carries the owner
    production's parameters, threaded unchanged through the recursion, and anything else `x` reads flows through the
    interpreter's env inheritance, as it did in place.
    """
    minted = {}

    def lower(owner, params, node):
        node = ir.rebuilt(node, lambda child: lower(owner, params, child))
        item = node.item if isinstance(node, ir.Star) else None
        if item is not None and not matches_empty(item, grammar) and not matches_one_char(item, grammar):
            name = namer.fresh(owner)
            reference = ir.Ref(name, tuple(ir.Param(parameter) for parameter in params))
            body = ir.Alt((ir.Seq((item, reference)), ir.Empty()))
            minted[name] = ir.Prod(grammar[owner].number, name, params, body)
            return reference
        return node

    result = {
        name: dataclasses.replace(production, body=lower(name, production.params, production.body))
        for name, production in grammar.items()
    }
    result.update(minted)
    return result


def _lower_tokens(node):
    """
    `node` with each `(token)` and `(wrap)` rewritten as the sequence of actions it stands for. Bottom-up, so a parent
    sees its already-lowered children.
    """
    node = ir.rebuilt(node, _lower_tokens)
    if isinstance(node, ir.Token):
        return ir.Seq((ir.PushCode(node.code), node.item, ir.PopCode()))
    if isinstance(node, ir.Wrap):
        return ir.Seq((ir.Emit(node.begin), node.item, ir.Emit(node.end)))
    return node


def lower_tokens(grammar, namer):
    """
    Rewrite each `(token)` as `PushCode(code), item, PopCode` and each `(wrap)` as `Emit(begin), item, Emit(end)`: the
    run-code changes a token stands for become explicit actions over the run code its production carries on its frame,
    and the markers a wrap stands for become the plain `(emit)`s they always were. Removes the `Token` and `Wrap` node
    kinds — after it the run code is a runtime value, no longer a scope the tree shape implies.
    """
    return {
        name: dataclasses.replace(production, body=_lower_tokens(production.body))
        for name, production in grammar.items()
    }


def _lower_bounds(node):
    """
    `node` with each `(<<<)` rewritten as the pair of actions that mark and restore the `(match)` origin around its run.
    Bottom-up, so a parent sees its already-lowered children.
    """
    node = ir.rebuilt(node, _lower_bounds)
    if isinstance(node, ir.Bound):
        return ir.Seq((ir.OpenMatch(), node.item, ir.CloseMatch()))
    return node


def lower_bounds(grammar, namer):
    """
    Rewrite each `(<<<)` as `OpenMatch, item, CloseMatch`: the `(match)` origin a bound marks for its run becomes an
    explicit action over the origin its production carries on its frame, restored at the run's trailing edge. Removes
    the `Bound` node kind — after it the measuring origin is a runtime value, no longer a scope the tree shape implies.
    """
    return {
        name: dataclasses.replace(production, body=_lower_bounds(production.body))
        for name, production in grammar.items()
    }


def _lower_windows(node):
    """
    `node` with each `(max)` rewritten as the pair of actions that open and restore its character window around the run
    it bounds, or dropped where it is a bare length note. Bottom-up, so a parent sees its already-lowered children.
    """
    node = ir.rebuilt(node, _lower_windows)
    if isinstance(node, ir.Max):
        if node.item is None:
            return ir.Empty()  # a bare `(max)` is a length note libyeast never runs — only recovers to
        return ir.Seq((ir.OpenWindow(node.limit, node.message), node.item, ir.CloseWindow()))
    return node


def lower_windows(grammar, namer):
    """
    Rewrite each `(max)` as `OpenWindow(limit, message), item, CloseWindow`: the character window a `(max)` bounds its
    run with becomes an explicit action over the window its production carries on its frame, restored at the run's
    trailing edge — the overflow past the edge failing the window's cut in `consume`, no longer a wrapper catching it.
    Removes the `Max` node kind — after it the window is a runtime value, no longer a scope the tree shape implies.
    """
    return {
        name: dataclasses.replace(production, body=_lower_windows(production.body))
        for name, production in grammar.items()
    }


def _lower_binds(node):
    """
    `node` with each `(if)(set)` rewritten as its `(match)`-measured condition and the assignment that reads it.
    Bottom-up, so a parent sees its already-lowered children.
    """
    node = ir.rebuilt(node, _lower_binds)
    if isinstance(node, ir.Bind):
        return ir.Seq((ir.OpenMatch(), node.cond, ir.SetVar(node.param, node.value), ir.CloseMatch()))
    return node


def lower_binds(grammar, namer):
    """
    Rewrite each `(if)(set)` as `OpenMatch, cond, SetVar(param, value), CloseMatch`: the parameter a bind sets from what
    its condition matched becomes a plain `(set)` over the `(match)` origin the condition runs under, marked and
    restored around it the way `(<<<)` is. Removes the `Bind` node kind — its condition and its assignment, one node
    holding a match scope, become the ordinary run and action they always were.
    """
    return {
        name: dataclasses.replace(production, body=_lower_binds(production.body))
        for name, production in grammar.items()
    }


def _lower_commits(node):
    """
    `node` with each `(commit)` rewritten as the pair of actions it stands for. Bottom-up, so a parent sees its
    already-lowered children.
    """
    node = ir.rebuilt(node, _lower_commits)
    if isinstance(node, ir.Commit):
        return ir.Seq((ir.PushMessage(node.message), node.item, ir.PopMessage()))
    return node


def lower_commits(grammar, namer):
    """
    Rewrite each `(commit)` as `PushMessage(message), item, PopMessage`: the committed region a commit scope stands for
    becomes explicit actions bracketing exactly `item`, so a failure inside the unclosed region raises `message` and one
    past the close backtracks softly, as the scope did. The extent survives every later split by being written in the
    grammar rather than implied by the tree shape; a helper may hold one half of the pair, since the pop pairs with its
    push dynamically and reads no frame value back. Removes the `Commit` node kind.
    """
    return {
        name: dataclasses.replace(production, body=_lower_commits(production.body))
        for name, production in grammar.items()
    }


def _flat_seq(items):
    """
    A `Seq` of `items`, flattened: a nested `Seq` spliced in, an `Empty` dropped as the no-op it is in a sequence, a
    single survivor unwrapped, and an empty result an `Empty`.
    """
    flat = []
    for item in items:
        if isinstance(item, ir.Empty):
            continue
        if isinstance(item, ir.Seq):
            flat.extend(item.items)
        else:
            flat.append(item)
    if not flat:
        return ir.Empty()
    return flat[0] if len(flat) == 1 else ir.Seq(tuple(flat))


def _flat_alt(items):
    """
    An `Alt` of `items`, flattened: a nested `Alt` spliced in (an ordered choice is associative), a single survivor
    unwrapped. An empty `Alt` — the never-match — is kept, since it is not an `Empty`.
    """
    flat = []
    for item in items:
        if isinstance(item, ir.Alt):
            flat.extend(item.items)
        else:
            flat.append(item)
    return flat[0] if len(flat) == 1 else ir.Alt(tuple(flat))


def _flatten(node):
    """
    `node` flattened: nested `Seq`/`Alt` spliced, `Empty` dropped from sequences, and singletons unwrapped. Bottom-up,
    so a parent sees its already-flattened children — which is also what leaves a repetition's element bare, for the
    step that turns a run into a consume to recognize the character class under it.
    """
    node = ir.rebuilt(node, _flatten)
    if isinstance(node, ir.Seq):
        return _flat_seq(node.items)
    if isinstance(node, ir.Alt):
        return _flat_alt(node.items)
    return node


def flatten(grammar, namer):
    """
    Flatten and simplify every production toward the canonical shape: splice nested `Seq`/`Alt`, drop the `Empty` no-ops
    a sequence carries, unwrap singleton `Seq`/`Alt`, and expand a fixed `(k)` repetition into its `k` copies. Removes
    the `Rep` node kind for a literal count; a `(n)` over a runtime count stays for the determinize phase, where
    counting a run is a gate. Introduces no canonical node yet — it hands the peek/consume seam a flat tree to reshape.
    """
    return {
        name: dataclasses.replace(production, body=_flatten(production.body)) for name, production in grammar.items()
    }


# The scopes a production's frame holds the outer value of, whose close reads that value back off it. A `(token)`'s is
# passable: a helper split out of the middle of one takes the outer code as a parameter, so a cut may fall inside it.
# The `(match)` origin and the `(max)` window are not passed, so a segment moved out must open and close them together.
_CODE_OPEN, _CODE_CLOSE = ir.PushCode, ir.PopCode
_OPENS = (ir.OpenMatch, ir.OpenWindow)
_CLOSES = (ir.CloseMatch, ir.CloseWindow)
CODE = "code"  # the parameter a helper declares to be handed the run code its caller was entered under


def _needs_code(items):
    """Whether `items` closes a `(token)` scope it does not open — so a helper holding them must be passed the code."""
    depth = 0
    for item in items:
        if isinstance(item, _CODE_OPEN):
            depth += 1
        elif isinstance(item, _CODE_CLOSE):
            depth -= 1
            if depth < 0:
                return True
    return False


def _scope_start(items, index):
    """
    Where the segment holding `items[index]` begins — `index` itself, or the open of the innermost frame-held scope
    around it, so that what is moved out to a helper carries that scope's open and close together.
    """
    depth, start = 0, 0
    for position in range(index + 1):
        if depth == 0:
            start = position
        if isinstance(items[position], _OPENS):
            depth += 1
        elif isinstance(items[position], _CLOSES):
            depth -= 1
    return start


def _balanced_end(items, start):
    """
    The end of the longest run of `items` from `start` that opens no frame-held scope it does not also close, or `None`
    where the very first item closes one opened before it.

    A `(token)`, a `(<<<)` and a `(max)` lower to a pair that opens a scope and closes it by reading the production's
    own value back off its frame. A helper holding one half of a pair would be entered under the opened value and read
    that back instead of the outer one — a `(token)`'s characters would keep its code past its end. So what is moved out
    to a helper is a whole segment between them, and the pair stays where it was, closing in the production that opened
    it.
    """
    end, depth = None, 0
    for index in range(start, len(items)):
        if isinstance(items[index], _OPENS):
            depth += 1
        elif isinstance(items[index], _CLOSES):
            depth -= 1
        if depth < 0:
            break  # this closes a scope opened before `start`, so the segment cannot reach it
        if depth == 0:
            end = index + 1
    return end


def lift_choices(grammar, namer):
    """
    Give every nested choice a production of its own, so a choice is only ever a whole production's body — the canonical
    form's shape, where a production is either a terminal character set or an ordered list of alternatives. An `Alt`
    anywhere but at the root of a body becomes a fresh `_<N>` helper, referenced in its place; the helper's own branches
    are lifted the same way, so a choice nested three deep unfolds into three productions.

    Only a choice in a sequence position is lifted — the branches of a control-flow alternation. An `Alt` inside a
    character class, a difference or a lookahead is a set of characters or a pattern, not a decision, and is left where
    it is. The helper carries the owner's parameters, threaded unchanged, the way `lower_star`'s does.
    """
    minted = {}

    def lift(owner, number, params, node, is_root):
        if isinstance(node, ir.Alt) and not is_root:
            name = namer.fresh(owner)
            minted[name] = ir.Prod(number, name, params, lift(name, number, params, node, True))
            return ir.Ref(name, tuple(ir.Param(parameter) for parameter in params))
        if isinstance(node, (ir.Alt, ir.Seq)):
            return dataclasses.replace(node, items=tuple(lift(owner, number, params, i, False) for i in node.items))
        if isinstance(node, (ir.Commit, ir.Star, ir.Plus)):
            return dataclasses.replace(node, item=lift(owner, number, params, node.item, False))
        if isinstance(node, ir.Recover):
            return ir.Recover(
                lift(owner, number, params, node.recovery, False), lift(owner, number, params, node.item, False)
            )
        return node

    result = {
        name: dataclasses.replace(
            production, body=lift(name, production.number, production.params, production.body, True)
        )
        for name, production in grammar.items()
    }
    result.update(minted)
    return result


def single_consumes(grammar, namer):
    """
    Split every alternative down to at most one gate-needing terminal — the one its gate peeks. That is a
    single-character terminal, or a char-set `x+`, whose at-least-one is exactly what a gate on `[x]` proves. An
    alternative with two or more is cut after its first: what follows becomes a fresh `_<N>` helper called in its place,
    cut the same way until none is left over, so `b-break` — a carriage return and a line feed — becomes two states, and
    an escape's eight hex digits become eight.

    A character run is not one of these. A `ConsumeSpan` or a `ConsumeTrimmedSpan` is a bulk scan the state performs,
    not a character the gate had to peek to decide, so an alternative may hold any number of them; what a state has one
    of is the gate, and so the gated character. The helper carries the owner's parameters, threaded unchanged.
    """
    minted, lookup = {}, dict(grammar)  # a helper is looked up too, since the split reclassifies what it put in place

    def needs_gate(item):
        # a char-set `x+` needs the gate as much as a single character: its gate is what proves it takes at least one
        target = item.item if isinstance(item, ir.Plus) else item
        return matches_one_char(target, lookup)

    def split(owner, number, params, alternative):
        items = alternative.items if isinstance(alternative, ir.Seq) else (alternative,)
        while True:
            positions = [index for index, item in enumerate(items) if needs_gate(item)]
            if len(positions) < 2:
                break
            start = _scope_start(items, positions[1])  # the first terminal is this state's gated character; a
            # `(token)` around the rest is cut through, its code passed on
            end = _balanced_end(items, start)
            if end is None or (start == 0 and end == len(items)):
                break  # the whole alternative is one scope, so there is nothing to move out of it
            if matches_one_char(_flat_seq(items[start:end]), lookup):
                break  # a segment that is itself a character class moves to a helper that is one, which is no progress
            segment = items[start:end]
            # A segment that closes a `(token)` its caller opened is handed that caller's own code, so its close
            # restores the outer one rather than the pushed one it was entered under.
            inner = params + (CODE,) if _needs_code(segment) and CODE not in params else params
            arguments = tuple(ir.Param(parameter) for parameter in inner)
            name = namer.fresh(owner)
            reference = ir.Ref(name, arguments)
            moved = split(name, number, inner, _flat_seq(segment))
            minted[name] = lookup[name] = ir.Prod(number, name, inner, moved)
            items = items[:start] + (reference,) + items[end:]
        return _flat_seq(items)

    result = {}
    for name, production in grammar.items():
        body, number, params = production.body, production.number, production.params
        if isinstance(body, ir.Alt):
            body = ir.Alt(tuple(split(name, number, params, item) for item in body.items))
        else:
            body = split(name, number, params, body)
        result[name] = dataclasses.replace(production, body=body)
    result.update(minted)
    return result


def binarize(grammar, namer):
    """
    Split every alternative down to at most two production calls, the canonical form's limit — two meaning "call the
    first, resume at the second", which is one stack push per edge. An alternative with three or more calls is cut after
    its first: what follows becomes a fresh `_<N>` helper the alternative calls in its place, and the helper is cut the
    same way until none is left over, so `A -> B C D` becomes `A -> B A_1` and `A_1 -> C D`.

    A call is a reference to a production that is not a character class — a character-class reference is a terminal the
    gate tests, not a call. Only the top level of an alternative is counted; a nested choice holds alternatives of its
    own, which the alternative-shape rewrite lifts out. The helper carries the owner's parameters, threaded unchanged,
    the way `lower_star`'s does.
    """
    minted, lookup = {}, dict(grammar)  # a helper is looked up too, since the split reclassifies what it put in place

    def is_call(node):
        return isinstance(node, ir.Ref) and not matches_one_char(node, lookup)

    def split(owner, number, params, alternative):
        items = alternative.items if isinstance(alternative, ir.Seq) else (alternative,)
        while True:
            positions = [index for index, item in enumerate(items) if is_call(item)]
            if len(positions) < 3:
                break
            start = _scope_start(items, positions[1])  # the first call stays; from the second on is the helper's
            end = _balanced_end(items, start)
            if end is None or (start == 0 and end == len(items)):
                break  # the whole alternative is one scope, so there is nothing to move out of it
            if sum(1 for item in items[start:end] if is_call(item)) < 2:
                break  # nothing left to move that holds more calls than the reference replacing it
            segment = items[start:end]
            # A segment that closes a `(token)` its caller opened is handed that caller's own code, so its close
            # restores the outer one rather than the pushed one it was entered under.
            inner = params + (CODE,) if _needs_code(segment) and CODE not in params else params
            arguments = tuple(ir.Param(parameter) for parameter in inner)
            name = namer.fresh(owner)
            reference = ir.Ref(name, arguments)
            moved = split(name, number, inner, _flat_seq(segment))
            minted[name] = lookup[name] = ir.Prod(number, name, inner, moved)
            items = items[:start] + (reference,) + items[end:]
        return _flat_seq(items)

    result = {}
    for name, production in grammar.items():
        body, number, params = production.body, production.number, production.params
        if isinstance(body, ir.Alt):
            body = ir.Alt(tuple(split(name, number, params, item) for item in body.items))
        else:
            body = split(name, number, params, body)
        result[name] = dataclasses.replace(production, body=body)
    result.update(minted)
    return result


_GUARDS = (ir.StartOfLine, ir.EndOfStream, ir.Look, ir.NegLook, ir.LookBehind, ir.ExcludeAt, ir.Lt, ir.Le)


def _gate_lead(actions):
    """
    The first action a gate may go on — the first one that consumes, or a `PushMessage` standing before it: a committed
    region's first character is not a gate's to refuse, since entering and failing must raise the region's message.
    """
    return next(
        (action for action in actions if isinstance(action, ir.PushMessage) or not isinstance(action, _ZERO_WIDTH)),
        None,
    )


def alternative_shape(grammar, namer):
    """
    Shape every production into the form the state machine reads: a terminal character class, or a `Choice` of
    `Alternative`s, each a `Gate` to enter on, the actions it performs, and up to two productions — the call and the
    continuation to resume at when it returns.

    Nothing may follow the continuation, since a production returns exactly when it does, so an alternative is cut at
    its first call and everything after becomes a fresh `_<N>` helper the continuation names — a sequence's trailing
    actions included, which is how the `end` marker after a scalar's last call gets a state to sit in. The gate takes
    the zero-width conditions an alternative opens with, and peeks the character it goes on where that is a single
    character class nothing consumes before — a char-set `x+` included, its peek `[x]` proving the `ConsumeSpan` it
    becomes takes at least one; a run or a call left un-peeked is for the gate hoisting to reach.

    A `(recover)` and a repetition over a nullable production are none of these, and stay as actions where they stand —
    the recover for `lower_recovers` to move onto its edge, the rest counted by `unshaped_actions`.
    """
    minted = {}

    def is_call(node):
        return isinstance(node, ir.Ref) and not matches_one_char(node, lookup)

    lookup = dict(grammar)

    def shape(owner, number, params, items):
        guards, index = [], 0
        while index < len(items) and isinstance(items[index], _GUARDS):
            guards.append(items[index])
            index += 1
        rest = items[index:]
        call = next((position for position, item in enumerate(rest) if is_call(item)), None)
        actions = list(rest if call is None else rest[:call])
        first = None if call is None else rest[call]
        tail = () if call is None else rest[call + 1 :]

        peek = None
        for position, action in enumerate(actions):  # the gate goes on the first character consumed, where it is one
            if isinstance(action, ir.PushMessage):
                break  # a committed region: a gate refusing its first character would soften the error it must raise
            if isinstance(action, _ZERO_WIDTH):
                continue
            if matches_one_char(action, lookup):
                peek, actions[position] = action, ir.ConsumeChar()
            elif isinstance(action, ir.Plus) and matches_one_char(action.item, lookup):
                # a char-set `x+`: the gate peeks `[x]`, which proves the span takes at least one character
                peek, actions[position] = action.item, ir.ConsumeSpan(action.item)
            break

        second = None
        if tail:
            name = namer.fresh(owner)
            inner = params + (CODE,) if _needs_code(tail) and CODE not in params else params
            minted[name] = lookup[name] = ir.Prod(number, name, inner, shape(name, number, inner, tail))
            second = ir.Ref(name, tuple(ir.Param(parameter) for parameter in inner))
        return ir.Choice((ir.Alternative(ir.Gate(peek, tuple(guards)), tuple(actions), first, second),))

    result = {}
    for name, production in grammar.items():
        body, number, params = production.body, production.number, production.params
        if matches_one_char(body, grammar):
            result[name] = production  # a terminal production is a character class, and stays one
            continue
        alternatives = body.items if isinstance(body, ir.Alt) else (body,)
        shaped = []
        for alternative in alternatives:
            items = alternative.items if isinstance(alternative, ir.Seq) else (alternative,)
            shaped.extend(shape(name, number, params, tuple(items)).alternatives)
        result[name] = dataclasses.replace(production, body=ir.Choice(tuple(shaped)))
    result.update(minted)
    return result


def lower_recovers(grammar, namer):
    """
    Move each `(recover)` from the action it stands in onto the edge it protects: the alternative calls the guarded
    production as its `first` and names the recovery in `recover`, so the frame pushed for the call is the one a cut
    unwinds to — the handler is the frame, and the resume point its own return. The calls the alternative already had
    move behind it: one becomes the continuation as it is, two move into a minted `_<N>` helper the edge resumes at.
    Only the canonical shape moves — a `Recover` standing as the sole action, its item and recovery plain references;
    anything else stays, for `unshaped_actions` to count.
    """
    minted = {}

    def lowered(owner, production, alternative):
        if len(alternative.actions) != 1 or not isinstance(alternative.actions[0], ir.Recover):
            return alternative
        scope = alternative.actions[0]
        if not isinstance(scope.item, ir.Ref) or not isinstance(scope.recovery, ir.Ref):
            return alternative
        second = alternative.first
        if alternative.second is not None:  # two calls to resume at move to a helper the edge resumes at as one
            name = namer.fresh(owner)
            body = ir.Choice((ir.Alternative(ir.Gate(None, ()), (), alternative.first, alternative.second),))
            minted[name] = ir.Prod(production.number, name, production.params, body)
            second = ir.Ref(name, tuple(ir.Param(parameter) for parameter in production.params))
        return dataclasses.replace(alternative, actions=(), first=scope.item, second=second, recover=scope.recovery)

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = ir.Choice(tuple(lowered(name, production, alternative) for alternative in body.alternatives))
        result[name] = dataclasses.replace(production, body=body)
    result.update(minted)
    return result


def gate_hoist(grammar, namer):
    """
    Give an alternative that goes on a call the characters that call can begin with, so the decision is made where it is
    taken rather than one production down. A production's first set falls straight out of the shaped form — it is the
    union of its alternatives' peeks — so a call's is read off the production it names, and an alternative that consumes
    nothing before it takes that union as its own peek.

    The peek only has to hold wherever the call could match, so a union that is too wide is safe and a first set that
    cannot be pinned down leaves the gate as it was: a nullable production, a run that may take nothing, or a repetition
    still awaiting determinize. Hoisting moves the test in front of the actions, which is why an alternative that opens
    a committed region — a `PushMessage`, or a `(cut)` before the call — is left alone: the commitment must be entered,
    and a gate that refuses first would soften the error it names into a skip.
    """
    first_of = {}

    def production_first(name, seen):
        if name in seen or name not in grammar:
            return None  # a recursion says nothing about what it starts with
        if name in first_of:
            return first_of[name]
        body = grammar[name].body
        if matches_one_char(body, grammar):
            return body  # a terminal production is its own first set
        if not isinstance(body, ir.Choice):
            return None
        peeks = []
        for alternative in body.alternatives:
            peek = alternative_first(alternative, seen | {name})
            if peek is None:
                return None  # one alternative it may start anywhere with makes the whole union unknown
            if peek not in peeks:
                peeks.append(peek)
        found = peeks[0] if len(peeks) == 1 else ir.Alt(tuple(peeks))
        if not seen:
            first_of[name] = found
        return found

    def alternative_first(alternative, seen):
        if alternative.gate.peek is not None:
            return alternative.gate.peek
        lead = _gate_lead(alternative.actions)
        if lead is None:
            for reference in (alternative.first, alternative.second):
                if reference is not None:
                    return production_first(reference.name, seen)
            return None  # it consumes nothing, so what follows it decides — a first set does not say
        if isinstance(lead, ir.ConsumeLiteral):
            return ir.Char(lead.text[0])
        return lead if matches_one_char(lead, grammar) else None

    def hoisted(alternative):
        if alternative.gate.peek is not None:
            return alternative
        lead = _gate_lead(alternative.actions)
        if isinstance(lead, ir.ConsumeLiteral):
            return dataclasses.replace(alternative, gate=ir.Gate(ir.Char(lead.text[0]), alternative.gate.guards))
        if lead is not None or alternative.first is None:
            return alternative
        if any(isinstance(action, ir.Cut) for action in alternative.actions):
            return alternative  # the cut has committed by the time the call runs; a gate must not undo that
        peek = production_first(alternative.first.name, frozenset())
        if peek is None:
            return alternative
        return dataclasses.replace(alternative, gate=ir.Gate(peek, alternative.gate.guards))

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = ir.Choice(tuple(hoisted(alternative) for alternative in body.alternatives))
        result[name] = dataclasses.replace(production, body=body)
    return result


def ungated_alternatives(grammar):
    """
    The alternatives no gate decides — those entered without a character to go on, which the determinize phase must
    settle by other means. An alternative that consumes nothing is one on purpose, the unconditional fallthrough a
    choice ends with, and is not counted.
    """
    ungated = []
    for name, production in grammar.items():
        if not isinstance(production.body, ir.Choice):
            continue
        for alternative in production.body.alternatives:
            if alternative.gate.peek is not None:
                continue
            consuming = [action for action in alternative.actions if not isinstance(action, _ZERO_WIDTH)]
            if consuming or alternative.first is not None:
                ungated.append(f"{name}: an alternative with no character to go on")
    return ungated


def unshaped_actions(grammar):
    """
    The actions that are not what the canonical form spells — a `(commit)` or `(recover)` scope a lowering left
    standing, or a repetition over a nullable production. The count is zero, and stays counted as the net that puts a
    leftover back on the board rather than letting a step assume it away.
    """
    residue = []
    for name, production in grammar.items():

        def walk(node, name=name):
            if isinstance(node, ir.Alternative):
                for action in node.actions:
                    if isinstance(action, (ir.Commit, ir.Recover, ir.Star, ir.Plus)):
                        residue.append(f"{name}: a {type(action).__name__} action")
            ir.rebuilt(node, lambda child: walk(child) or child)

        walk(production.body)
    return residue


def _trim_runs(node, grammar):
    """
    `node` with each `(w* p)*` — a run of content whose inner whitespace is kept and trailing whitespace given back —
    rewritten as a `TrimStar` over `w | p` that trims `w`. Bottom-up, so a parent sees its already-rewritten children.
    """
    node = ir.rebuilt(node, lambda child: _trim_runs(child, grammar))
    if (
        isinstance(node, ir.Star)
        and isinstance(node.item, ir.Seq)
        and len(node.item.items) == 2
        and isinstance(node.item.items[0], ir.Star)
        and matches_one_char(node.item.items[0].item, grammar)
    ):
        whitespace = node.item.items[0].item
        content = node.item.items[1]
        return ir.TrimStar(ir.Alt((whitespace, content)), whitespace)
    return node


def trim_runs(grammar, namer):
    """
    Rewrite each `(s-white* content)*` — a run of content, its inner whitespace kept and its trailing whitespace given
    back — as a `TrimStar` over `s-white | content` that trims `s-white`. This is the in-line run of a plain and of a
    single- or double-quoted one, a long text token today matched one character per outer iteration; the `TrimStar` is
    the maximal run a single scan takes, its trailing whitespace trimmed. `content` may be complex — a quoted escape, a
    plain `#`/`:` behind its guard — which does not stop the run being one scan: the whitespace and the plain content
    are its char-set bulk, the exceptions its slow path.
    """
    return {
        name: dataclasses.replace(production, body=_trim_runs(production.body, grammar))
        for name, production in grammar.items()
    }


class NotAlmostCharSet(Exception):
    """
    A `*` over an alternation of character classes and complex alternatives whose first character a class also accepts,
    or cannot be pinned down — factoring it to `common* (uncommon common*)*` would change the match, so it is refused
    rather than guessed.
    """


def _first_chars(node, grammar, seen=frozenset()):
    """
    The codepoints a match of `node` can begin with, or `None` where that set is not a concrete few — a range of first
    characters, or a shape not analysed — which the caller treats as unsure rather than guessing through.
    """
    if isinstance(node, ir.Char):
        return frozenset({node.cp})
    if isinstance(node, ir.Ref):
        return frozenset() if node.name in seen else _first_chars(grammar[node.name].body, grammar, seen | {node.name})
    if isinstance(node, ir.Alt):
        first = frozenset()
        for item in node.items:
            got = _first_chars(item, grammar, seen)
            if got is None:
                return None
            first |= got
        return first
    if isinstance(node, ir.Seq):
        first = frozenset()
        for item in node.items:
            got = _first_chars(item, grammar, seen)
            if got is None:
                return None
            first |= got
            if not matches_empty(item, grammar):
                break
        return first
    if isinstance(node, (ir.Token, ir.Wrap, ir.Bound, ir.Commit, ir.Recover, ir.Star, ir.Plus, ir.Opt)):
        return _first_chars(node.item, grammar, seen)
    if isinstance(node, ir.Max):
        return frozenset() if node.item is None else _first_chars(node.item, grammar, seen)
    if isinstance(node, ir.Case):
        first = frozenset()
        for branch in node.branches:
            got = _first_chars(branch.item, grammar, seen)
            if got is None:
                return None
            first |= got
        return first
    if isinstance(node, ir.Bind):
        return _first_chars(node.cond, grammar, seen)
    if isinstance(node, ir.Diff):
        return _first_chars(node.base, grammar, seen)  # the exclusions only narrow, so the base's set over-approximates
    if isinstance(node, _ZERO_WIDTH):
        return frozenset()  # begins no character
    return None  # a Range, Invalid or Rep as a first-character source is not a concrete few — unsure


def _accepts(node, codepoint, grammar, seen=frozenset()):
    """Whether the character class `node` matches `codepoint`."""
    if isinstance(node, ir.Char):
        return node.cp == codepoint
    if isinstance(node, ir.Range):
        return node.lo <= codepoint <= node.hi
    if isinstance(node, ir.Diff):
        return _accepts(node.base, codepoint, grammar, seen) and not any(
            _accepts(excluded, codepoint, grammar, seen) for excluded in node.minus
        )
    if isinstance(node, ir.Alt):
        return any(_accepts(item, codepoint, grammar, seen) for item in node.items)
    if isinstance(node, ir.Case):
        return any(_accepts(branch.item, codepoint, grammar, seen) for branch in node.branches)
    if isinstance(node, ir.Ref):
        return node.name not in seen and _accepts(grammar[node.name].body, codepoint, grammar, seen | {node.name})
    return False


def hoist_char_runs(grammar, namer):
    """
    Factor a `*` over an almost-character-set — an alternation of character classes and a few complex exceptions, the
    escapes of a quoted scalar, a URI, a tag — into `common* (uncommon common*)*`: the common characters matched in bulk
    runs (each a repeated-char-set match, later one SIMD call), dropping to the slow path only for an exception. A
    `TrimStar` factors the same way, keeping its trim a character set of its own: `trim-run (trim* uncommon trim-run)*`,
    so its common runs stay the two-set trimming scan a plain or quoted scalar's line compiles to. The equivalence holds
    only where an exception cannot begin with a character the common set also accepts, so that a greedy common run never
    takes one an ordered choice would have handed the exception; where that cannot be shown the factoring is refused
    with `NotAlmostCharSet` rather than guessed.
    """

    def resolved(node):
        while isinstance(node, ir.Ref):
            node = grammar[node.name].body
        if isinstance(node, ir.Diff):
            # A difference over an alternation distributes into the alternation: the exclusions are a start-position
            # guard the interpreter probes before matching the base, so guarding the whole alternation and guarding each
            # branch accept the same characters. Pushing them in lets the escape of a tag or URI — `ns-tag-char` is
            # `ns-uri-char` less a few indicators — surface as the one complex branch a run factors around.
            base = resolved(node.base)
            if isinstance(base, ir.Alt):
                return ir.Alt(tuple(ir.Diff(item, node.minus) for item in base.items))
        return node

    def flat_alternatives(node):
        """
        `node`'s alternatives, flat: a char class kept whole, an alternation resolved and its own alternatives flattened
        in, so a run's fast char set separates from its complex exceptions however the grammar nests them.
        """
        if matches_one_char(node, grammar):
            return (node,)
        expanded = resolved(node)
        if isinstance(expanded, ir.Alt):
            return tuple(alternative for item in expanded.items for alternative in flat_alternatives(item))
        return (node,)

    def split(alternatives, owner):
        """
        `alternatives` split into `(common, uncommon)` — a character set of the char-class alternatives, or `None` where
        there is none, and one alternative of the complex ones, or `None` where there is none. Raises where a complex
        alternative is not provably start-disjoint from the common set, so a greedy common run could take a character an
        ordered choice would have handed the exception.
        """
        common_alts = tuple(item for item in alternatives if matches_one_char(item, grammar))
        uncommon_alts = tuple(item for item in alternatives if not matches_one_char(item, grammar))
        common = None if not common_alts else common_alts[0] if len(common_alts) == 1 else ir.Alt(common_alts)
        uncommon = None if not uncommon_alts else uncommon_alts[0] if len(uncommon_alts) == 1 else ir.Alt(uncommon_alts)
        if common is not None and uncommon is not None:
            first = _first_chars(uncommon, grammar)
            if first is None or any(_accepts(common, codepoint, grammar) for codepoint in first):
                raise NotAlmostCharSet(
                    f"{owner}: a repeated alternation's complex alternative is not provably start-disjoint from its "
                    f"character classes, so its character runs cannot be factored out"
                )
        return common, uncommon

    def hoist(owner, node):
        node = ir.rebuilt(node, lambda child: hoist(owner, child))
        if isinstance(node, ir.TrimStar):
            # A trimmed run factors as a plain run does, but each common run gives back its trailing trim, so the trim
            # stays a char set of its own: `trim-run (trim* uncommon trim-run)*`, the leading `trim*` re-taking what the
            # run before it gave back — which is what keeps the whitespace before a mid-scalar `:` while trailing
            # whitespace is still trimmed off the end.
            common, uncommon = split(flat_alternatives(node.full), owner)
            run = ir.TrimStar(common, node.trim)
            if uncommon is None:
                return run  # a pure character-set run — one trimming scan, no exceptions
            return ir.Seq((run, ir.Star(ir.Seq((ir.Star(node.trim), uncommon, run)))))
        if not isinstance(node, ir.Star):
            return node
        alternation = resolved(node.item)
        if not isinstance(alternation, ir.Alt):
            return node
        common, uncommon = split(alternation.items, owner)
        if common is None or uncommon is None:
            return node  # a pure character set (lower-star keeps it) or all complex (lower-star recurses it)
        run = ir.Star(common)
        return ir.Seq((run, ir.Star(ir.Seq((uncommon, run)))))

    return {
        name: dataclasses.replace(production, body=hoist(name, production.body)) for name, production in grammar.items()
    }


# The token codes that carry a long text token — a run of content: a scalar's text, a name's meta, the recovery's
# unparsed text and invalid bytes. A run under one of these should be matched by a char-set `+`/`*` (one SIMD call), not
# one character per iteration; `content_run_offenders` finds where the normalized grammar still matches it per char.
CONTENT_CODES = frozenset({"text", "meta", "unparsed-text", "unparsed-invalid"})
_ROOT_CODE = "unparsed-text"  # the code the emitter starts with, before any `(token)` sets one


def _child_nodes(node):
    """The grammar nodes `node` holds directly, as a list."""
    children = []
    ir.rebuilt(node, lambda child: children.append(child) or child)
    return children


def _ref_codes(node, active):
    """Yield `(ref-target, active-code)` for each `Ref` in `node`, under the `(token)` scopes that change the code."""
    if isinstance(node, ir.Ref):
        yield (node.name, active)
        return
    inner = node.code if isinstance(node, ir.Token) else active
    for child in _child_nodes(node):
        yield from _ref_codes(child, inner)


def codes_at(grammar):
    """
    For each production, the token codes active at its entry — the run code in force when it is called, gathered over
    every call site by propagation from the root down through the `(token)` scopes.
    """
    entry = {name: set() for name in grammar}
    entry[ir.ROOT] = {_ROOT_CODE}
    worklist = [ir.ROOT]
    while worklist:
        name = worklist.pop()
        for active in list(entry[name]):
            for target, code in _ref_codes(grammar[name].body, active):
                if target in entry and code not in entry[target]:
                    entry[target].add(code)
                    worklist.append(target)
    return entry


def _is_lowered_star(name, grammar):
    """Whether `name` is a helper `lower_star` minted, `_N ::= x _N | <empty>`."""
    body = grammar[name].body
    return (
        isinstance(body, ir.Alt)
        and len(body.items) == 2
        and isinstance(body.items[1], ir.Empty)
        and isinstance(body.items[0], ir.Seq)
        and len(body.items[0].items) == 2
        and isinstance(body.items[0].items[1], ir.Ref)
        and body.items[0].items[1].name == name
    )


def _content_tail(node, active, grammar, seen=frozenset()):
    """
    How `node` ends its content matching under the `active` token code, at its surface — `"run"` where the last content
    consumed is a char-set `*`/`+` (a bulk run, so consecutive content folds into it), `"bare"` where it is a single
    character (so each one costs a loop iteration), or `None` where it consumes no surface content (behind a `(wrap)`,
    through a helper's own loop, or under a non-content code). This tells an efficient content loop from one that
    collects its run one character at a time.
    """
    if isinstance(node, (ir.Star, ir.Plus)):
        return "run" if active in CONTENT_CODES and matches_one_char(node.item, grammar) else None
    if isinstance(node, ir.TrimStar):
        return "run" if active in CONTENT_CODES else None  # a maximal run, matched in bulk by a single trimming scan
    if isinstance(node, (ir.Char, ir.Range, ir.Diff, ir.Invalid)):
        return "bare" if active in CONTENT_CODES else None
    if isinstance(node, ir.Wrap):
        return None  # a nested span or node — its content is not this loop's surface run
    if isinstance(node, ir.Token):
        return _content_tail(node.item, node.code, grammar, seen)
    if isinstance(node, ir.Ref):
        if node.name in seen or _is_lowered_star(node.name, grammar):
            return None
        return _content_tail(grammar[node.name].body, active, grammar, seen | {node.name})
    if isinstance(node, ir.Seq):
        for item in reversed(node.items):  # the tail is the last item that consumes content
            tail = _content_tail(item, active, grammar, seen)
            if tail is not None:
                return tail
        return None
    if isinstance(node, (ir.Alt, ir.Case)):
        items = node.items if isinstance(node, ir.Alt) else [branch.item for branch in node.branches]
        tails = {_content_tail(item, active, grammar, seen) for item in items}
        return "bare" if "bare" in tails else "run" if "run" in tails else None  # a bare branch makes it per-character
    if isinstance(node, (ir.Bound, ir.Commit, ir.Recover)):
        return _content_tail(node.item, active, grammar, seen)
    if isinstance(node, ir.Max):
        return _content_tail(node.item, active, grammar, seen) if node.item is not None else None
    if isinstance(node, ir.Bind):
        return _content_tail(node.cond, active, grammar, seen)
    return None


def content_run_offenders(grammar):
    """
    The productions collecting a content run one character at a time — a `lower_star` helper whose iteration ends its
    content on a bare character rather than a char-set `*`/`+`. Each is a place a long text token is not bulk-matched;
    the check that reports them shrinks to empty as the char-run factoring reaches every one.
    """
    entry = codes_at(grammar)
    return [
        name
        for name in grammar
        if _is_lowered_star(name, grammar)
        and any(_content_tail(grammar[name].body.items[0].items[0], code, grammar) == "bare" for code in entry[name])
    ]


def _literal_codepoint(node, grammar, seen=frozenset()):
    """
    The one codepoint `node` spells, or `None` where it is not a single literal character. A production named for a
    character — `b-carriage-return`, `b-line-feed` — spells the one its body does, so a break's two stand together as
    the fixed sequence they are.
    """
    if isinstance(node, ir.Char):
        return node.cp
    if isinstance(node, ir.Ref) and not node.args and node.name in grammar and node.name not in seen:
        return _literal_codepoint(grammar[node.name].body, grammar, seen | {node.name})
    return None


def _span_consumes(node, grammar):
    """
    `node` with each character-set `Star` rewritten as `ConsumeSpan` and each `TrimStar` as `ConsumeTrimmedSpan`.
    Bottom-up, so a parent sees its already-rewritten children.
    """
    node = ir.rebuilt(node, lambda child: _span_consumes(child, grammar))
    if isinstance(node, ir.Seq):
        items, collapsed, index = node.items, [], 0
        while index < len(items):
            # Literal characters standing in a row are one fixed sequence to match, not a state each: `---`, `...`, a
            # directive's `YAML` or `TAG`, and the carriage return and line feed of a break.
            run = index
            while run < len(items) and _literal_codepoint(items[run], grammar) is not None:
                run += 1
            if run - index > 1:
                text = tuple(_literal_codepoint(item, grammar) for item in items[index:run])
                collapsed.append(ir.ConsumeLiteral(text))
                index = run
                continue
            # The same character class standing in a row is a counted run written out — a URI escape's two hex digits.
            run = index
            while run < len(items) and items[run] == items[index]:
                run += 1
            if run - index > 1 and matches_one_char(items[index], grammar):
                collapsed.append(ir.ConsumeCountedSpan(items[index], ir.Lit(run - index)))
                index = run
                continue
            collapsed.append(items[index])
            index += 1
        node = _flat_seq(tuple(collapsed))
    if isinstance(node, ir.TrimStar):
        return ir.ConsumeTrimmedSpan(node.full, node.trim)
    if isinstance(node, ir.Star) and matches_one_char(node.item, grammar):
        return ir.ConsumeSpan(node.item)
    if isinstance(node, ir.Rep) and matches_one_char(node.item, grammar):
        return ir.ConsumeCountedSpan(node.item, node.count)
    return node


def span_consumes(grammar, namer):
    """
    Rewrite each run over a character class as the consume action the canonical form spells, each a single scan: a
    `Star` becomes a `ConsumeSpan`, a `TrimStar` a `ConsumeTrimmedSpan`, and a `({N})` repetition a `ConsumeCountedSpan`
    — a maximal run, a maximal run that gives its trailing trim back, and a run of exactly so many. The counted one is
    what keeps an escape's eight hex digits and an indent's `n` spaces each a single scan rather than a state per
    character. A `Star` over a nullable production is left for the determinize phase's zero-width guard. Removes the
    `TrimStar` and `Rep` node kinds and every character-set `Star`.
    """
    return {
        name: dataclasses.replace(production, body=_span_consumes(production.body, grammar))
        for name, production in grammar.items()
    }


def non_char_set_runs(grammar):
    """
    The runs whose element is not the character set a bulk scan needs. A `ConsumeTrimmedSpan` runs and trims two
    character sets — it is the two-set trimming scan a scalar's line compiles to — so a `full` or `trim` that is not one
    means the fast/slow split did not complete. A `ConsumeSpan` runs a character set too. A `Star` may still run one
    that is nullable, which `lower_star` cannot make a recursive helper of and which waits for the zero-width guard
    determinize will bring; a `Star` over anything else non-character-set is a `lower_star` that should have fired.
    """
    faults = []

    def walk(name, node):
        if isinstance(node, ir.ConsumeTrimmedSpan):
            if not matches_one_char(node.full, grammar):
                faults.append(f"{name}: a ConsumeTrimmedSpan runs a non-character-set `full`")
            if not matches_one_char(node.trim, grammar):
                faults.append(f"{name}: a ConsumeTrimmedSpan trims a non-character-set `trim`")
        if isinstance(node, ir.ConsumeSpan) and not matches_one_char(node.set, grammar):
            faults.append(f"{name}: a ConsumeSpan runs a non-character-set `set`")
        if (
            isinstance(node, ir.Star)
            and not matches_one_char(node.item, grammar)
            and not matches_empty(node.item, grammar)
        ):
            faults.append(f"{name}: a Star runs a non-character-set, non-nullable production")
        ir.rebuilt(node, lambda child: walk(name, child) or child)

    for name in grammar:
        walk(name, grammar[name].body)
    return faults


# The pipeline, in order, as `(name, transform)` pairs.
STEPS = [
    ("lift-chomping", lift_chomping),
    ("monomorphize", monomorphize),
    ("lower-optionals", lower_optionals),
    ("hoist-empty", hoist_empty),
    ("lower-plus", lower_plus),
    ("trim-runs", trim_runs),
    ("hoist-char-runs", hoist_char_runs),
    ("lower-star", lower_star),
    ("lower-tokens", lower_tokens),
    ("lower-bounds", lower_bounds),
    ("lower-windows", lower_windows),
    ("lower-binds", lower_binds),
    ("lower-commits", lower_commits),
    ("flatten", flatten),
    ("span-consumes", span_consumes),
    ("lift-choices", lift_choices),
    ("single-consumes", single_consumes),
    ("binarize", binarize),
    ("alternative-shape", alternative_shape),
    ("lower-recovers", lower_recovers),
    ("gate-hoist", gate_hoist),
]


def stages(grammar):
    """
    The grammar after each step, as `(label, grammar)` pairs, opening with `("base", grammar)` — what `check_normalize`
    diffs the interpreter's token stream across, so a step that changes it is named. One `Namer` is threaded through the
    steps, so the helper productions they mint number `<base>_<N>` off a count shared across them.
    """
    namer = Namer()
    result = [("base", grammar)]
    for name, transform in STEPS:
        grammar = transform(grammar, namer)
        result.append((name, grammar))
    return result


def normalize(grammar):
    """The grammar with every step applied in order."""
    return stages(grammar)[-1][1]
