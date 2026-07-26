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

import annotated2ir
import chars
import ir

# Nodes that begin no character — a match of one starts no run, so it adds nothing to a first-character set: the
# lookaheads, the epsilon and marker emitters, the guards, and the parameter actions.
_ZERO_WIDTH = ir.ZERO_WIDTH + (  # in alphabetical order
    ir.CloseMatch,
    ir.CloseWindow,
    ir.CommitProvisional,
    ir.Cut,
    ir.Emit,
    ir.Empty,
    ir.EndOfStream,
    ir.Error,
    ir.Increase,
    ir.InjectBefore,
    ir.Le,
    ir.Lt,
    ir.OpenMatch,
    ir.OpenProvisional,
    ir.OpenWindow,
    ir.PopCode,
    ir.PopMessage,
    ir.PushCode,
    ir.PushMessage,
    ir.RetypeProvisional,
    ir.SetVar,
    ir.StartOfLine,
)


class Points:
    """
    The named points of interest, tracked across the pipeline. A point is three things: an identifier that never changes
    — what every consumer speaks — the names currently holding its content, and the point's own logic for picking the
    holders anew. When a step reshapes a current holder, the logic looks at what the holder became — the holder itself
    if it survived, and everything minted off its base — and updates the current names; a point whose holders a step
    loses without successor is a loud fault, not an absorbed drift. The initial holder is a base-grammar name, so a
    point starts anchored to something real and follows its content wherever the steps carry it.
    """

    def __init__(self):
        self.at = {point: (origin,) for point, (origin, _picker) in POINTS.items()}

    def settle(self, label, grammar):
        """
        Re-pick every live point among what its holders became in `grammar`, faulting on a loss at `label`. A point a
        step has retired — its content replaced, the way the determinizer replaces the fold site — is no longer tracked
        and does not fault.
        """
        for point in list(self.at):
            _origin, picker = POINTS[point]
            candidates = {name for name in grammar if any(_descends(name, held) for held in self.at[point])}
            held = tuple(sorted(picker(grammar, candidates)))
            if not held:
                raise AssertionError(f"[{label}] the step lost the point of interest `{point}` ({self.at[point]})")
            self.at[point] = held

    def follow(self, renames):
        """
        Carry every live point through a `{gone: standing}` renaming — what the post-step sweep did to the names. A
        holder the sweep merged or spliced away has its content in the production it names now, whose base the picker
        would not otherwise reach, so the point is moved rather than left to fault on a name that is gone.
        """
        for point, held in self.at.items():
            self.at[point] = tuple(sorted({renames.get(name, name) for name in held}))

    def retire(self, point):
        """Drop a point the pipeline has consumed, so the settle after its consuming step does not fault on its loss."""
        self.at.pop(point, None)

    def current(self, point):
        """
        The names currently holding `point` — the one lookup a consumer makes, by the identifier that never changes.
        """
        held = self.at.get(point)
        if held is None:
            raise AssertionError(f"`{point}` is not a live point of interest")
        return held


def _base(name):
    """`name` without its minted `_<N>` suffixes — the base a helper's name is numbered off."""
    head, _underscore, tail = name.rpartition("_")
    return _base(head) if tail.isdigit() and head else name


def _descends(name, holder):
    """
    Whether `name` is `holder` or something the pipeline made from it — a numbered sibling minted off its base, or a
    `_`-suffixed copy a step split it into, `monomorphize`'s `_t_keep` among them. Bases are compared, so a sibling
    `foo_8` of a holder `foo_7` counts, and a copy's own later helpers count through their shared base.
    """
    base, holder_base = _base(name), _base(holder)
    return base == holder_base or base.startswith(holder_base + "_")


class Namer:
    """
    Fresh helper-production names, `<base>_<N>`, the `<N>` the next unused per base across the whole pipeline.

    One is threaded through every step, so a base's count carries across them: a helper minted for `foo` is `foo_1`, the
    next `foo_2`, and one minted while a later step processes `foo_3` is `foo_4` — never `foo_3_1`, since the base is
    `foo` with any `_<N>` suffix stripped. Two steps minting for the same base do not collide. It carries the pipeline's
    `Points` too — the points of interest tracked across the steps it names.
    """

    def __init__(self):
        self._counts = {}
        self.points = Points()

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
    # the root is entered once per resume policy — the one finite parameter the caller chooses rather than the grammar
    # settles — so each policy's copy is a start state of the machine, made whether or not anything references it.
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


def hoist_repetition_empties(grammar, namer):
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
MATCH_START = "match_start"  # its `(match)`-origin twin: a helper closing a scope its caller opened restores this


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


def _needs_origin(items):
    """
    Whether `items` closes a `(match)` scope it does not open — so a helper holding them must be passed the origin: its
    entry stamps `match_start` with the scope already open, and only the declared parameter restores the caller's own.
    """
    depth = 0
    for item in items:
        if isinstance(item, ir.OpenMatch):
            depth += 1
        elif isinstance(item, ir.CloseMatch):
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


def _bound(node, mapping):
    """
    `node` with each `Param` the `mapping` names replaced by its expression — the substitution a call makes lexical.
    Walks every field itself: the generic walker holds a `Param` as a value and never visits it, where here the
    parameters are exactly what changes.
    """
    if isinstance(node, ir.Param):
        return mapping.get(node.name, node)
    if not dataclasses.is_dataclass(node):
        return node
    changed = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if dataclasses.is_dataclass(value):
            changed[field.name] = _bound(value, mapping)
        elif isinstance(value, tuple) and value and all(dataclasses.is_dataclass(item) for item in value):
            changed[field.name] = tuple(_bound(item, mapping) for item in value)
    return dataclasses.replace(node, **changed) if changed else node


# The declared inlines: callees `{name: reason}` spliced into every way that calls them — the callee's ways standing in
# the caller's place, their parameters bound to the call's arguments, the caller's continuation composed behind the
# callee's own through a minted helper where both stand. A language identity in first-match order — the callee's ways
# keep their order in the caller's — and stream-faithful, nothing moving relative to anything. What it is for: a
# decision hidden one call down is hoisted into the choice that makes it, so the factoring and the certificates see what
# the call wrapper hid — `l-empty`'s two ways are both maximal space scans told apart by `==n` against `<n`, the
# complementary pair the certificate reads, once the prefix wrappers no longer stand between.
DECLARED_INLINES = {
    "block-empty-line": "the empty line's two ways — a full line prefix against a shorter indent — surface into"
    " `l-empty`'s own choice, where the shared scan can factor and the column guards decide",
    "block-line-prefix-in": "the block context's line prefix is one call down to the block prefix; inlined, the chain"
    " shortens toward the indent scan itself",
    "block-line-prefix": "the block prefix is the indent call alone; inlined, the way holds `s-indent` directly, where"
    " the indent refinement's shape applies",
    "shorter-indent": "the shorter-indent way is the scan-and-`Lt` actions alone; inlined, the guard stands beside its"
    " rival's for the complementary pair to certify",
}


def inline_singles(grammar, namer):
    """
    The grammar with each call to a `DECLARED_INLINES` callee spliced in place: the caller's way is replaced by the
    callee's ways — parameters bound to the call's arguments, the caller's gate or the callee way's carried (both
    standing is a loud fault), actions concatenated, and the two continuations composed through a minted helper where
    both stand. Iterated to a fixpoint, so a declared chain collapses whole. A declared name the grammar does not hold,
    a callee that is not a choice, or a way carrying a recovery is a loud fault — the declarations name shapes, and a
    step before this one changing them must be seen.
    """
    result = dict(grammar)
    targets = {name for point in DECLARED_INLINES for name in namer.points.current(point)}
    for name in sorted(targets):
        if name not in result:
            raise AssertionError(f"{name}: declared inlined, but the grammar holds no such production")
        if not isinstance(result[name].body, ir.Choice):
            raise AssertionError(f"{name}: declared inlined, but it is not a choice to splice")

    def spliced(owner, production, way):
        callee = result[way.first.name]
        mapping = dict(zip(callee.params, way.first.args))
        ways = []
        for inner in callee.body.alternatives:
            bound = _bound(inner, mapping)
            if way.recover is not None or bound.recover is not None:
                raise AssertionError(f"{owner}: a recovery rides the {way.first.name} splice, which cannot carry it")
            if way.gate.peek is not None and bound.gate.peek is not None:
                raise AssertionError(f"{owner}: both the way and a {way.first.name} way carry a peek")
            gate = ir.Gate(way.gate.peek or bound.gate.peek, way.gate.guards + bound.gate.guards)
            second = way.second
            if bound.second is not None and second is not None:
                helper = namer.fresh(owner)
                chain = ir.Alternative(gate=ir.Gate(None, ()), actions=(), first=bound.second, second=second)
                result[helper] = ir.Prod(production.number, helper, production.params, ir.Choice((chain,)))
                second = ir.Ref(name=helper, args=tuple(ir.Param(parameter) for parameter in production.params))
            elif bound.second is not None:
                second = bound.second
            ways.append(
                ir.Alternative(
                    gate=gate, actions=way.actions + bound.actions, first=bound.first, second=second, recover=None
                )
            )
        return ways

    changed = True
    while changed:
        changed = False
        for name, production in list(result.items()):
            body = production.body
            if not isinstance(body, ir.Choice):
                continue
            ways = []
            rewritten = False
            for way in body.alternatives:
                if way.first is not None and way.first.name in targets:
                    ways.extend(spliced(name, production, way))
                    rewritten = True
                else:
                    ways.append(way)
            if rewritten:
                result[name] = dataclasses.replace(production, body=ir.Choice(tuple(ways)))
                changed = True
    for point in DECLARED_INLINES:
        namer.points.retire(point)  # spliced into their callers, the callees fall out of reach here
    return result


def subsume_ways(grammar, namer):
    """
    Drop each way a later way makes redundant: identical actions, calls and recovery, under a gate no stricter — the
    later way's peek absent or the same, its guards a subset — so wherever the earlier way succeeds, the later succeeds
    with the very same consumption and stream, and first-found is unchanged by the drop. What it is for: a spliced
    separation leaves a start-of-line way beside a plain fallthrough with the same continuation, and the guarded twin
    says nothing the fallthrough does not.
    """

    def kept(alternatives):
        ways = list(alternatives)
        index = 0
        while index < len(ways):
            earlier = ways[index]
            redundant = any(
                later.actions == earlier.actions
                and later.first == earlier.first
                and later.second == earlier.second
                and later.recover is None
                and earlier.recover is None
                and (later.gate.peek is None or later.gate.peek == earlier.gate.peek)
                and set(later.gate.guards) <= set(earlier.gate.guards)
                for later in ways[index + 1 :]
            )
            if redundant:
                del ways[index]
            else:
                index += 1
        return tuple(ways)

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = ir.Choice(kept(body.alternatives))
        result[name] = dataclasses.replace(production, body=body)
    return result


def refine_indents(grammar, namer):
    """
    Refine each exact-count indentation call into the one maximal scan judged after the fact, in the grammar's own
    spelling — the shape `s-indent-le` already is: an alternative whose call is `s-indent(k)` with a continuation behind
    it takes the scan inline, `PushCode(indent) OpenMatch ConsumeSpan(space) Le(Len(Match), k) Le(k, Len(Match))
    CloseMatch PopCode`, and the continuation is promoted to the call. The measure is the scan's own `(match)` scope, so
    the rewrite reads only this alternative and the FIRST table. One side condition, mechanically checked, and a site
    failing it is left alone: the continuation's first set is pinned, excludes the space, and cannot match empty — so
    the maximal scan steals nothing an exact count would have left, a longer run failing the guard exactly where the
    count came up short, and a leftover space refusing the continuation exactly as it did. Stream-faithful where it
    applies: accepting paths consume the same spaces under the same `indent` code, one token either way. Counted
    consumes of different `k` share no literal prefix; refined, they are the identical scan, and the differing counts
    are residual guards for the prefix factoring to leave behind.
    """
    first = _first_table(grammar)
    space = 0x20

    def refined(alternative):
        reference = alternative.first
        if reference is None or reference.name != "s-indent" or alternative.second is None:
            return alternative
        begins, nullable = first.get(alternative.second.name, (None, True))
        if begins is None or nullable or any(low <= space <= high for low, high in begins):
            return alternative
        [level] = reference.args
        scan = (
            ir.PushCode(code="indent"),
            ir.OpenMatch(),
            ir.ConsumeSpan(set=ir.Ref(name="s-space", args=())),
            ir.Le(a=ir.Len(arg=ir.Match()), b=level),
            ir.Le(a=level, b=ir.Len(arg=ir.Match())),
            ir.CloseMatch(),
            ir.PopCode(),
        )
        return dataclasses.replace(
            alternative, actions=alternative.actions + scan, first=alternative.second, second=None
        )

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = ir.Choice(tuple(refined(alternative) for alternative in body.alternatives))
        result[name] = dataclasses.replace(production, body=body)
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

    first = _first_table(grammar)

    def first_of_spans(reference):
        return first[reference]

    def hoisted(alternative):
        if alternative.gate.peek is not None:
            return alternative
        lead = _gate_lead(alternative.actions)
        if isinstance(lead, ir.ConsumeLiteral):
            return dataclasses.replace(alternative, gate=ir.Gate(ir.Char(lead.text[0]), alternative.gate.guards))
        if any(isinstance(action, (ir.Cut, ir.PushMessage)) for action in alternative.actions):
            return alternative  # committed on entry: a gate refusing first would take that commitment away
        if lead is None and alternative.first is not None:
            peek = production_first(alternative.first.name, frozenset())
            if peek is not None:
                return dataclasses.replace(alternative, gate=ir.Gate(peek, alternative.gate.guards))
        # What the call-first hoisting could not reach — a nullable callee, a run before the call, a chain of both — is
        # peeked as the alternative's whole begin set, actions, call and continuation together, where that is pinned
        # down and cannot match empty: an empty match must stay enterable with no character left to peek, so a nullable
        # alternative keeps its empty gate for the follow-set certificate to decide.
        begins, nullable = _alternative_first(alternative, grammar, first_of_spans)
        if begins and not nullable and begins[0][0] >= 0:  # a begin set holding the invalid unit is not a peek
            return dataclasses.replace(alternative, gate=ir.Gate(_spans_node(begins), alternative.gate.guards))
        return alternative

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


def _spans_node(spans):
    """`spans` as the character-class node a gate peeks — a `Char` per point, a `Range` per run, an `Alt` of several."""
    pieces = tuple(ir.Char(lo) if lo == hi else ir.Range(lo, hi) for lo, hi in spans)
    return pieces[0] if len(pieces) == 1 else ir.Alt(pieces)


def _merged_spans(spans):
    """`spans` as sorted, coalesced `(lo, hi)` codepoint intervals."""
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _subtracted_spans(spans, minus):
    """`spans` with every interval of `minus` removed."""
    for exclude_lo, exclude_hi in minus:
        remaining = []
        for lo, hi in spans:
            if exclude_hi < lo or exclude_lo > hi:
                remaining.append((lo, hi))
                continue
            if lo < exclude_lo:
                remaining.append((lo, exclude_lo - 1))
            if hi > exclude_hi:
                remaining.append((exclude_hi + 1, hi))
        spans = remaining
    return spans


def _denoted_spans(denotation):
    """A `chars.denote` denotation as sorted, disjoint `(lo, hi)` codepoint intervals."""
    kind = denotation[0]
    if kind == "literal":
        return [(denotation[1], denotation[1])]
    if kind == "range":
        return [(denotation[1], denotation[2])]
    if kind == "union":
        return _merged_spans([span for part in denotation[1] for span in _denoted_spans(part)])
    if kind == "difference":
        return _subtracted_spans(
            _denoted_spans(denotation[1]),
            _merged_spans([span for part in denotation[2] for span in _denoted_spans(part)]),
        )
    raise ValueError(f"unknown denotation {denotation!r}")


def _peek_spans(peek, grammar):
    """
    The codepoint intervals `peek` accepts, or `None` where the set is not pinned down. The invalid-byte class is the
    interval `(-1, -1)` — a unit no character class can also hold, so it overlaps only itself — and an alternation
    holding it beside character classes, the recovery's any-byte peek, is the classes' intervals with that unit.
    """
    if isinstance(peek, ir.Invalid):
        return [(-1, -1)]
    if isinstance(peek, ir.LiteralPeek):
        return [(peek.text[0], peek.text[0])]  # the first character is the dispatch; the rest is the gate's own test
    if isinstance(peek, ir.Alt) and any(isinstance(item, ir.Invalid) for item in peek.items):
        rest = tuple(item for item in peek.items if not isinstance(item, ir.Invalid))
        spanned = _peek_spans(ir.Alt(items=rest), grammar) if rest else []
        return None if spanned is None else _merged_spans([(-1, -1)] + spanned)
    denotation = chars.denote(grammar, peek)
    return None if denotation is None else _denoted_spans(denotation)


def _spans_overlap(a, b):
    """Whether the two `_peek_spans` results share a unit."""
    position_a = position_b = 0
    while position_a < len(a) and position_b < len(b):
        if max(a[position_a][0], b[position_b][0]) <= min(a[position_a][1], b[position_b][1]):
            return True
        if a[position_a][1] < b[position_b][1]:
            position_a += 1
        else:
            position_b += 1
    return False


def _node_begins(node, grammar, first_of):
    """
    What a match of `node` can begin with and whether it may match empty — `(spans, nullable)`, the spans `None` where
    not pinned down. A reference answers from the first table; a character class from its denotation; a difference from
    its base, less every exclusion that is a single character class — the interpreter probes exclusions before the base,
    so a match begins with nothing they accept, which makes the subtraction exact — and no narrower where an exclusion
    is not. Anything else is not pinned down, erring wide as every answer here does.
    """
    if isinstance(node, ir.Ref):
        return first_of(node.name)
    if isinstance(node, ir.Diff):
        begins, nullable = _node_begins(node.base, grammar, first_of)
        if begins is None:
            return None, nullable
        for excluded in node.minus:
            if matches_one_char(excluded, grammar):
                cut = _peek_spans(excluded, grammar)
                if cut is not None:
                    begins = _subtracted_spans(begins, cut)
        return begins, nullable
    if matches_one_char(node, grammar):
        return _peek_spans(node, grammar), False
    return None, True


def _alternative_first(alternative, grammar, first_of):
    """
    The spans a match of `alternative` can begin with and whether it can match empty — `(spans, nullable)`, the spans
    `None` where they are not pinned down. Where there is a peek it is the sound first set, the alternative being
    entered only where it holds; nullability is read off the content either way, erring toward "may match empty", as
    every answer here errs wide — a follow set built from these certifies by disjointness, so too wide refuses safely. A
    recovery does not widen the answer: it rides the call's edge and resumes at the frame's return, so it changes what
    may follow the production, never what the alternative begins with entered fresh — the follow computation hands the
    recovery its due on the same edge. A way guarded by the end of the input begins with no character, so its begins pin
    empty — but it does match empty, there where no character is left, and the gate hoisting must keep it enterable with
    nothing to peek; that nothing follows the end is the certificate's own knowledge, applied where the fallthrough's
    begins would otherwise widen by the follow set.
    """
    if any(isinstance(guard, ir.EndOfStream) for guard in alternative.gate.guards):
        return [], True
    spans, consumed, unknown = [], False, False
    for action in alternative.actions:
        if isinstance(action, ir.ConsumeChar):
            consumed = True  # the gated character, which the peek already describes
            break
        if isinstance(action, _ZERO_WIDTH):
            continue
        if isinstance(action, (ir.ConsumeLiteral, ir.ConsumePeeked)):
            spans.append((action.text[0], action.text[0]))
            consumed = True
            break
        if isinstance(action, (ir.ConsumeSpan, ir.ConsumeTrimmedSpan, ir.ConsumeCountedSpan)):
            run_set = action.full if isinstance(action, ir.ConsumeTrimmedSpan) else action.set
            part = _peek_spans(run_set, grammar)
            unknown = unknown or part is None
            spans.extend(part or [])
            if (
                isinstance(action, ir.ConsumeCountedSpan)
                and isinstance(action.count, ir.Lit)
                and action.count.value > 0
            ):
                consumed = True
                break
            continue  # a run that may take nothing: what follows can begin here too
        if isinstance(action, ir.Diff):
            part, part_nullable = _node_begins(action, grammar, first_of)
            unknown = unknown or part is None
            spans.extend(part or [])
            if part_nullable:
                continue  # may take nothing: what follows can begin here too
            consumed = True
            break
        if matches_one_char(action, grammar):
            part = _peek_spans(action, grammar)
            unknown = unknown or part is None
            spans.extend(part or [])
            consumed = True
            break
        return None, True  # an action the analysis does not pin down
    nullable = not consumed
    if not consumed:
        for reference in (alternative.first, alternative.second):
            if reference is None:
                continue
            part, part_nullable = first_of(reference.name)
            unknown = unknown or part is None
            spans.extend(part or [])
            if not part_nullable:
                nullable = False
                break
    if alternative.gate.peek is not None:
        return _peek_spans(alternative.gate.peek, grammar), nullable
    return (None if unknown else _merged_spans(spans)), nullable


def _first_table(grammar):
    """
    The first set and nullability of every production, `{name: (spans, nullable)}` — the spans `None` where not pinned
    down. The least fixpoint of the begin-set equations, so a cycle contributes what its members prove rather than
    poisoning them unknown: spans only grow, toward `None` for not-pinned-down, and nullability only rises, so the
    iteration stops. Every constructor errs wide, so the fixpoint over-approximates what a match can begin with.
    """
    table = {name: ([], False) for name in grammar}

    def first_of(reference):
        return table[reference]

    changed = True
    while changed:
        changed = False
        for name, production in grammar.items():
            body = production.body
            if not isinstance(body, ir.Choice):
                computed = _peek_spans(body, grammar), False  # a terminal character set
            else:
                spans, unknown, nullable = [], False, False
                for alternative in body.alternatives:
                    part, part_nullable = _alternative_first(alternative, grammar, first_of)
                    unknown = unknown or part is None
                    spans.extend(part or [])
                    nullable = nullable or part_nullable
                computed = (None if unknown else _merged_spans(spans)), nullable
            if computed != table[name]:
                table[name] = computed
                changed = True
    return table


def _follow_spans(grammar, first):
    """
    What can follow each production's return, as `{name: spans}` — `None` where it is not pinned down, `first` the
    `_first_table`. A fixpoint over the call edges: a call is followed by its continuation's first set, and by the
    caller's own follow where there is no continuation or it may match empty; a continuation, and a recovery — which
    resumes at the same return — inherit the caller's follow. A production entered from the top is followed by the end
    of the input alone, which no character set collides with, so roots contribute nothing.
    """
    follow = {name: [] for name in grammar}
    top = set()

    def flow(target, spans):
        """Widen `follow[target]` by `spans` (`None` widening it to unknown), reporting whether anything changed."""
        if target in top:
            return False
        if spans is None:
            top.add(target)
            return True
        widened = _merged_spans(follow[target] + spans)
        if widened == follow[target]:
            return False
        follow[target] = widened
        return True

    changed = True
    while changed:
        changed = False
        for name, production in grammar.items():
            if not isinstance(production.body, ir.Choice):
                continue
            inherited = None if name in top else follow[name]
            for alternative in production.body.alternatives:
                call, continuation, recovery = alternative.first, alternative.second, alternative.recover
                if call is not None:
                    if continuation is not None:
                        followed, nullable = first[continuation.name]
                        if followed is None or (nullable and inherited is None):
                            followed = None
                        elif nullable:
                            followed = _merged_spans(followed + inherited)
                    else:
                        followed = inherited
                    changed |= flow(call.name, followed)
                    if recovery is not None:
                        changed |= flow(recovery.name, followed)
                if continuation is not None:
                    changed |= flow(continuation.name, inherited)
    return {name: (None if name in top else follow[name]) for name in grammar}


def _segment_signatures(spans_by_alternative):
    """
    The characters each subset of alternatives shares, as `{signature: spans}`: every alternative's spans are cut at
    every boundary into segments, each accepted by one exact set of alternatives — the signature, member positions in
    order — and a segment's characters join its signature's spans. A singleton signature holds the characters only one
    alternative accepts.
    """
    points = set()
    for spans in spans_by_alternative:
        for lo, hi in spans:
            points.add(lo)
            points.add(hi + 1)
    cuts = sorted(points)
    grouped = {}
    for index in range(len(cuts) - 1):
        lo, hi = cuts[index], cuts[index + 1] - 1
        signature = tuple(
            position
            for position, spans in enumerate(spans_by_alternative)
            if any(span_lo <= lo <= span_hi for span_lo, span_hi in spans)
        )
        if signature:
            grouped.setdefault(signature, []).append((lo, hi))
    return {signature: _merged_spans(spans) for signature, spans in grouped.items()}


def split_conflicts(grammar, namer):
    """
    Split every choice whose gates overlap into one the first character decides alone. The characters only one
    alternative accepts stay its own, the peek narrowed to them; the characters a set of alternatives share go to a
    minted `_<N>` production holding those alternatives in their order, peeks narrowed to the shared characters, called
    behind a gate on exactly them — so the original's gates become disjoint and the overlap is confined to the helper,
    whose alternatives all peek the same characters, ready for their common prefix to be factored. Each conflict call
    stands where its first member stood, which preserves the order the members and a trailing fallthrough are tried in;
    an alternative two signatures share is copied into both. A trailing ungated fallthrough stays where it was; a choice
    holding a guard, an ungated alternative elsewhere, or a peek that is not a pinned character set is left alone, as is
    one whose whole overlap is all of it — splitting that would mint a copy of itself.
    """
    minted = {}

    def split(name, production):
        body = production.body
        alternatives = list(body.alternatives)
        fallthrough = None
        if alternatives and alternatives[-1].gate.peek is None and not alternatives[-1].gate.guards:
            fallthrough = alternatives[-1]
            alternatives = alternatives[:-1]
        if len(alternatives) < 2:
            return body
        spans_by_alternative = []
        for alternative in alternatives:
            if alternative.gate.peek is None or alternative.gate.guards:
                return body
            spanned = _peek_spans(alternative.gate.peek, grammar)
            if spanned is None:
                return body
            spans_by_alternative.append(spanned)
        grouped = _segment_signatures(spans_by_alternative)
        conflicts = {signature: spans for signature, spans in grouped.items() if len(signature) > 1}
        if not conflicts:
            return body
        exclusive = {}
        for signature, spans in grouped.items():
            if len(signature) == 1:
                exclusive.setdefault(signature[0], []).extend(spans)
        if not exclusive and len(conflicts) == 1 and fallthrough is None:
            return body  # the whole choice is one shared set
        arguments = tuple(ir.Param(parameter) for parameter in production.params)
        rewritten = []
        for position, alternative in enumerate(alternatives):
            for signature in sorted(signature for signature in conflicts if signature[0] == position):
                shared = _spans_node(conflicts[signature])
                members = tuple(
                    dataclasses.replace(alternatives[member], gate=ir.Gate(shared, alternatives[member].gate.guards))
                    for member in signature
                )
                helper = namer.fresh(name)
                minted[helper] = ir.Prod(production.number, helper, production.params, ir.Choice(members))
                rewritten.append(ir.Alternative(ir.Gate(shared, ()), (), ir.Ref(helper, arguments), None))
            spans = _merged_spans(exclusive.get(position, []))
            if spans:
                narrowed = ir.Gate(_spans_node(spans), alternative.gate.guards)
                rewritten.append(dataclasses.replace(alternative, gate=narrowed))
        if fallthrough is not None:
            rewritten.append(fallthrough)
        return ir.Choice(tuple(rewritten))

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = split(name, production)
        result[name] = dataclasses.replace(production, body=body)
    result.update(minted)
    return result


# Where a common prefix must stop: a frame-scoped pair's half, which a helper may not hold alone, and a length-ambiguous
# run, whose backtracking order a factoring must not reshuffle.
_PREFIX_STOP = (ir.OpenWindow, ir.CloseMatch, ir.CloseWindow, ir.ConsumeTrimmedSpan)
# The fixed-width consumes a prefix may hold: each takes exactly what it takes or fails, identically in every
# alternative that shares it, so factoring it out reorders nothing.
_PREFIX_CONSUMES = (ir.ConsumeChar, ir.ConsumeLiteral, ir.ConsumeCountedSpan)


def factor_prefixes(grammar, namer):
    """
    Factor the common prefix out of every choice whose alternatives all peek the same characters — the shape
    `split-conflicts` confines an overlap to. The prefix is the longest run of identical leading actions that are
    zero-width or fixed-width consumes; a length-ambiguous run stops it, backtracking over it being an order a factoring
    must not reshuffle, as does a `(max)` window's edge. Two admissions reach further, each locally checked. An
    identical maximal scan joins where every leftover's first set is pinned, cannot match empty, and excludes the
    scanned set: a run cut short leaves a set character at the head, which no leftover then admits, so the maximal run
    is the only one that proceeds and the factoring reorders nothing — the refined indents satisfy this by construction,
    their own side condition being the same check. And a `(match)` scope's opening joins, its origin passed where the
    split cuts the pair: a leftover closing a scope the prefix opened declares `match_start` and is handed the caller's
    own, the `code` parameter's exact twin, so its close restores what the unfactored close restored. It must consume at
    least the peeked character, or nothing would move past the shared gate. What remains of each alternative — leftover
    actions, calls and recovery — moves to a minted `_<N>` decision production the prefix calls, alternatives in their
    order; the gate hoisting then gives each leftover the characters it can go on, one character deeper than the gate
    the alternatives shared.
    """
    minted = {}
    first = _first_table(grammar)

    def first_of(reference):
        return first[reference]

    def one_outcome(action, alternatives, depth):
        # The scan's admission: every leftover past it must begin off the scanned set, pinned and never empty — a
        # shorter run leaves a set character at the head, which no leftover then admits.
        scanned = _peek_spans(action.set, grammar)
        if scanned is None:
            return False
        for alternative in alternatives:
            remainder = dataclasses.replace(
                alternative, gate=ir.Gate(None, ()), actions=alternative.actions[depth + 1 :]
            )
            begins, nullable = _alternative_first(remainder, grammar, first_of)
            if begins is None or nullable or _spans_overlap(begins, scanned):
                return False
        return True

    def factor(name, production):
        body = production.body
        alternatives = body.alternatives
        if len(alternatives) < 2:
            return body
        peek = alternatives[0].gate.peek
        if peek is None or any(a.gate.peek != peek or a.gate.guards for a in alternatives):
            return body
        prefix = []
        for elements in zip(*(a.actions for a in alternatives)):
            action = elements[0]
            if any(element != action for element in elements[1:]) or isinstance(action, _PREFIX_STOP):
                break
            if isinstance(action, ir.ConsumeSpan):
                if not one_outcome(action, alternatives, len(prefix)):
                    break
            elif not isinstance(action, _ZERO_WIDTH) and not isinstance(action, _PREFIX_CONSUMES):
                break
            prefix.append(action)
        if not any(isinstance(action, (ir.ConsumeSpan,) + _PREFIX_CONSUMES) for action in prefix):
            return body

        def peeled(alternative):
            # A leftover's leading assertions become its gate's guards: both are judged at this same position — after
            # the prefix, before anything of the leftover's own consumes — so the move changes nothing but where the
            # certificates can see them.
            actions = alternative.actions[len(prefix) :]
            index = 0
            while index < len(actions) and isinstance(actions[index], (ir.Lt, ir.Le)):
                index += 1
            return dataclasses.replace(alternative, gate=ir.Gate(None, tuple(actions[:index])), actions=actions[index:])

        leftovers = tuple(peeled(a) for a in alternatives)
        inner = production.params
        if any(_needs_code(leftover.actions) for leftover in leftovers) and CODE not in inner:
            inner = inner + (CODE,)
        if any(_needs_origin(leftover.actions) for leftover in leftovers) and MATCH_START not in inner:
            inner = inner + (MATCH_START,)
        helper = namer.fresh(name)
        minted[helper] = ir.Prod(production.number, helper, inner, ir.Choice(leftovers))
        arguments = tuple(ir.Param(parameter) for parameter in inner)
        return ir.Choice((ir.Alternative(ir.Gate(peek, ()), tuple(prefix), ir.Ref(helper, arguments), None),))

    result = {}
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.Choice):
            body = factor(name, production)
        result[name] = dataclasses.replace(production, body=body)
    result.update(minted)
    return result


# The assurance ledger: points of interest entered committed on a declaration rather than a proof, each with the reason
# its gate order is the grammar's meaning. An entry is held two ways — the hybrid corpus parses with it committed, so a
# wrong declaration diverges from backtracking on the spot, and `declared_faults` refuses one the analysis has since
# proved, so the ledger never quietly outgrows its reasons. The entries name points, so no minted number is declared and
# the tracked content is what stays committed as the steps renumber the helpers around it.
DECLARED_COMMITS = {
    "block-header-keep-chomp": "chomp-first subsumes: on '+' it takes every header indicator-first takes, and '+1'"
    " besides — the declared reorder is what stood it first",
    "block-header-strip-chomp": "chomp-first subsumes: on '-' it takes every header indicator-first takes, and"
    " '-1' besides — the declared reorder is what stood it first",
}


def committed_productions(points):
    """The productions entered committed on declaration — the current holders of every `DECLARED_COMMITS` point."""
    return {name for point in DECLARED_COMMITS for name in points.current(point)}


def _holds(node, want):
    """Whether `want` holds for `node` or anything in its own shape — references named, not entered."""
    if want(node):
        return True
    found = []
    ir.rebuilt(node, lambda child: (found.append(_holds(child, want)), child)[1])
    return any(found)


def _two_ways(body):
    """`body`'s two ways where it is a two-way alternation — spelled as an `Alt` or a `Choice` — else `None`."""
    if isinstance(body, ir.Choice) and len(body.alternatives) == 2:
        return body.alternatives
    if isinstance(body, ir.Alt) and len(body.items) == 2:
        return body.items
    return None


def _chomp_choice_picker(monomorphized, literal=None):
    """
    The picker for a block header's chomp choice: the header-ordering alternation, whose one way calls the chomping
    indicator's `monomorphized` copy first and whose other reaches it only behind the indentation indicator — `|+2`
    against `|2+`, the order the chomp-first reorder swaps. Which way goes on the chomping first is the whole of the
    choice, so the indentation side is read as "not that one" rather than by a name of its own: the elimination puts a
    minted helper where the indentation indicator's call stood, the call's nullable-or-not choice, and the ordering is
    the same ordering. Before the alternation binarizes to that two-call shape, and after a later step reshapes it, the
    point stays anchored to the holders that carry the chomping side at all — a reference to that indicator, or the bare
    `literal` character it inlines to where it has one.
    """

    def is_chomp(name):
        return name.startswith("c-chomping-indicator") and ("_t_" not in name or monomorphized in name)

    def first_call(way):
        return way.first.name if isinstance(getattr(way, "first", None), ir.Ref) else None

    def is_ordering(ways):
        return sum(is_chomp(first_call(way) or "") for way in ways) == 1

    def chomp_side(node):
        def wants(held):
            if literal is not None and held == ir.Char(cp=literal):
                return True
            return isinstance(held, ir.Ref) and is_chomp(held.name)

        return _holds(node, wants)

    def pick(grammar, candidates):
        ordering = [
            name for name in candidates if (ways := _two_ways(grammar[name].body)) is not None and is_ordering(ways)
        ]
        holders = ordering or [name for name in candidates if chomp_side(grammar[name].body)]
        # Clip's chomping indicator matches only empty, so `eliminate-empties` dissolves it into a residue that is
        # nothing at all, and there is no chomping side left to hold on to — the point stays with the header copies its
        # own family carries, which `reorder-declared` holds to the two-way shape it speaks about. A chomping side that
        # matches a character is there or the point is genuinely lost, so the fallback is clip's alone.
        return holders or (sorted(candidates) if literal is None else [])

    return pick


# The flow fold's conflict, the break `b-l-folded` decides two ways — before monomorphize fixes its flow-in context into
# the name, and after. The fold site is whatever production currently calls it, tracked by that content.
_FLOW_FOLD_CONFLICT = ("b-l-folded", "b-l-folded_c_flow-in")


def _flow_fold_site_picker(grammar, candidates):
    """The fold site: the `s-flow-folded` production whose way calls the flow fold's conflict."""
    return [name for name in candidates if any(ref in _FLOW_FOLD_CONFLICT for ref in grammar[name].references())]


def _refs_picker(*required):
    """
    A production of its base identified by the references that name its role. A reference counts where it descends from
    the name required — a step that mints a copy of the callee and calls that instead leaves the role intact, the
    consuming copies `eliminate-empties` calls among them. Before monomorphize fixes a context into those names, the
    base has no copy yet to tell apart, so the whole family holds the point.
    """

    def pick(grammar, candidates):
        specific = [
            name
            for name in candidates
            if all(any(_descends(ref, want) for ref in grammar[name].references()) for want in required)
        ]
        return specific or sorted(candidates)

    return pick


def _loop_seam_picker(grammar, candidates):
    """
    The block sequence loop's exit seam: the single-way production whose call and continuation are both helpers of its
    own base — the loop and the frame it returns through. Before that shape takes form the whole family holds it.
    """
    seam = [
        name
        for name in candidates
        if isinstance(grammar[name].body, ir.Choice)
        and len(grammar[name].body.alternatives) == 1
        and (way := grammar[name].body.alternatives[0]).first is not None
        and way.second is not None
        and _base(way.first.name) == _base(name)
        and _base(way.second.name) == _base(name)
    ]
    return seam or sorted(candidates)


# The points of interest: each an identifier that never changes, mapped to its origin — the base-grammar name whose
# content it follows — and its picker. The declared tables speak in these identifiers, so no minted number is ever
# declared and no declaration goes stale when a step renumbers the helpers around a point.
POINTS = {
    "block-header-keep-chomp": ("c-b-block-header", _chomp_choice_picker("_t_keep", 0x2B)),
    "block-header-strip-chomp": ("c-b-block-header", _chomp_choice_picker("_t_strip", 0x2D)),
    "flow-fold-site": ("s-flow-folded", _flow_fold_site_picker),
    "block-empty-line": ("l-empty", _refs_picker("s-indent-lt", "s-line-prefix_c_block-in")),
    "block-line-prefix-in": ("s-line-prefix", _refs_picker("s-block-line-prefix")),
    "block-line-prefix": ("s-block-line-prefix", _refs_picker("s-indent")),
    "shorter-indent": ("s-indent-lt", _refs_picker("s-space")),
    "block-seq-loop-exit": ("l+block-sequence", _loop_seam_picker),
}

# The declared reorders: points of interest whose two alternatives the `reorder-declared` step swaps, each with the
# reason the swap keeps the first-found parse. Alternative order is semantics under backtracking-with-commits — the
# first match binds — so a reorder is sound only where a per-point argument shows every input finds the same parse
# either way, and that argument is position-dependent: these hold only AFTER monomorphize, never in the base grammar.
# The header is the proof. In the base, `c-chomping-indicator` is one data-dependent production whose clip branch
# matches empty, so a chomp-first ordering enters through that empty match on `|2-`, completes the header's `(any)`, and
# its `(commit)` turns the valid trailing `-` into an error backtracking cannot undo. Monomorphize distributes the
# data-dependence away: the strip and keep copies gate their chomping way on the literal `-`/`+` — no empty match left
# to enter through, so on `|2-` that way fails before anything commits and falls through — and the clip copy's chomping
# indicator matches only empty, so its two orderings are empty-commutations of one language, either order the same
# parse. The reasons below are those per-copy arguments; a point that resolves to anything but the two-way choice the
# swap speaks about faults loudly, so the declarations cannot outlive the shapes they speak about.
DECLARED_REORDERS = {
    "block-header-keep-chomp": "chomp-first: its way is gated on the literal '+', which the input has or has not"
    " — no empty match to enter through — and standing first it also takes '+1', which indicator-first misparses",
    "block-header-strip-chomp": "chomp-first: its way is gated on the literal '-', which the input has or has not"
    " — no empty match to enter through — and standing first it also takes '-1', which indicator-first misparses",
}

# The declared return-extensions: sites `{caller: reason}` whose one way is a call and a continuation, folded so the
# continuation's actions run inside the call's own family — `A then B` becoming `A^`, where `A^` is `A` with `B`'s
# actions appended to every return path, copies minted along the tail and continuation chains so every other caller of
# `A` stands untouched and a tail recursion folds to its own copy. A language identity, and stream-faithful: the
# appended actions run exactly where the continuation ran. What it is for: a block loop's exit way returns through a
# chain of end-marker frames to the parent's next scan, and each extension absorbs one frame into the loop's own choice,
# until the scan the exit shares with the continue way is local to the conflict and the held factoring can take both —
# the seam decomposed into corpus-held identities rather than one atomic flip.
DECLARED_EXTENSIONS = {
    "block-seq-loop-exit": "the sequence loop's exit returns through the end-marker frame; absorbing it stands"
    " `end-sequence` inside the loop's own exit way, one frame nearer the parent's scan — and its two resume-policy"
    " copies with it, the point holding all three seams the family spells",
}


def declared_faults(grammar, committed):
    """
    The assurance-ledger holders that no longer hold their ground: one the analysis proves on its own — a declaration
    gone stale — refused so the ledger's reasons stay true. The `committed` holders come resolved from the points.
    """
    proved = _proved_productions(grammar)
    return [
        f"{name}: declared committed, but the analysis proves it — the declaration is stale"
        for name in sorted(committed)
        if name in proved
    ]


def deterministic_productions(grammar, committed):
    """
    The productions entered committed: every one the analysis proves one-gate-decidable, and the assurance ledger's
    declared few beside them — the `committed` holders resolved from the `DECLARED_COMMITS` points, each carrying the
    reason its order is the grammar's meaning, held to backtracking by the hybrid corpus and to freshness by
    `declared_faults`.
    """
    return _proved_productions(grammar) | {name for name in committed if name in grammar}


def _proved_productions(grammar):
    """
    The productions whose every decision is statically proved one-gate-decidable. A character-set terminal and a
    single-alternative choice decide nothing. A choice whose alternatives peek pairwise-disjoint character sets can hold
    at most one gate at any position, so committing to the first that holds is the same parse backtracking finds: the
    alternatives all start at the same position, and where one gate holds no other's `Look` could. A guard on a gate is
    allowed — it only narrows the one candidate its peek admits, so it decides nothing between alternatives, and its
    refusal falls through exactly as backtracking does. Two alternatives may even share a peek where their guards are
    complementary — one `Lt(x, y)`, the other `Le(y, x)` — since exactly one of the pair holds and the shared character
    decides nothing the guards do not. The same stands with an ungated last alternative — the empty way out of a loop,
    or a call whose first set no gate could pin — where everything it can begin with, its own first set widened by the
    production's follow set where it may match empty, is pinned down and disjoint from every peek: where a gate holds,
    the last way cannot succeed, entered fresh or backtracked into, and for a last alternative entered-and-failed is the
    same as not entered. The count of productions neither proved here nor declared in the ledger is what the determinize
    work drives to none.
    """
    first = _first_table(grammar)
    follow = _follow_spans(grammar, first)

    def first_of(reference):
        return first[reference]

    deterministic = set()
    for name, production in grammar.items():
        body = production.body
        if not isinstance(body, ir.Choice) or len(body.alternatives) <= 1:
            deterministic.add(name)  # a terminal, a single way through, or no way at all: nothing to decide
            continue
        if _decides(name, production, grammar, first_of, follow[name]):
            deterministic.add(name)
    return deterministic


def _literal_decided(earlier, grammar):
    """
    Whether `earlier` — the first of two alternatives sharing a first character — is decided by its own literal gate:
    its peek is a `LiteralPeek` whose text its way in spells exactly, by its own leading consume or down the single-way
    productions it opens on. Where the gate refuses, the alternative is never entered and the later one is tried, which
    is where backtracking's failed literal lands; where it holds, the consume is the literal the gate found, committed
    the way the grammar means a literal — whole, and past any follow test the gate carries. The corpus's hybrid run is
    what holds the greedy side of that reading to the reference, the marker fixtures with it.
    """
    peek = earlier.gate.peek
    return isinstance(peek, ir.LiteralPeek) and _spells_peek(earlier, peek.text, grammar, frozenset())


def _spells_peek(alternative, text, grammar, seen):
    """
    Whether `alternative`'s way in consumes exactly `text` — its own leading consume, or the one found down its
    single-way calls whose actions are all zero-width. A spine level's own gate does not matter here: a gate is a
    necessary condition on entry, and what the way in consumes is the same behind any of them.
    """
    lead = next((action for action in alternative.actions if not isinstance(action, _ZERO_WIDTH)), None)
    if lead is not None:
        return isinstance(lead, ir.ConsumePeeked) and lead.text == text
    reference = alternative.first
    if reference is None or reference.name in seen or reference.name not in grammar:
        return False
    body = grammar[reference.name].body
    if not isinstance(body, ir.Choice) or len(body.alternatives) != 1:
        return False
    [entry] = body.alternatives
    return _spells_peek(entry, text, grammar, seen | {reference.name})


# A production's context classes are capped: past this many distinct follows, the set collapses to the unpinned class,
# which fails every follow-widened judgment — erring toward counting a conflict, never hiding one.
_CLASS_CAP = 24


def _follow_classes(grammar, first):
    """
    For each production, the distinct follows a root parse can reach it under — each class a tuple of spans, `None` the
    unpinned class, and the empty tuple the end of the input. A call with a continuation contributes the continuation's
    first set, widened by the caller's own classes where the continuation may match empty; a tail call and a
    continuation position inherit the caller's classes whole; a recovery entry is reached from wherever a cut fired,
    which is the unpinned class. A least fixpoint from the roots, whose own follow is the input's end.
    """
    classes = {name: set() for name in grammar}
    for resume in annotated2ir.RESUMES:
        root = ir.entry(grammar, ir.ROOT, {"r": resume})[0]
        if root in classes:
            classes[root].add(())
    changed = True
    while changed:
        changed = False

        def contribute(target, contributions):
            nonlocal changed
            held = classes.get(target)
            if held is None or None in held:
                return
            for contribution in contributions:
                if contribution not in held:
                    held.add(contribution)
                    changed = True
            if len(held) > _CLASS_CAP:
                held.clear()
                held.add(None)
                changed = True

        for name, production in grammar.items():
            body = production.body
            if not isinstance(body, ir.Choice):
                continue
            inherited = classes[name]
            for alternative in body.alternatives:
                if alternative.recover is not None:
                    contribute(alternative.recover.name, {None})
                if alternative.first is not None and alternative.second is not None:
                    spans, nullable = first.get(alternative.second.name, (None, True))
                    if spans is None:
                        contribute(alternative.first.name, {None})
                    elif nullable:
                        contribute(
                            alternative.first.name,
                            {None if k is None else tuple(_merged_spans(list(spans) + list(k))) for k in inherited},
                        )
                    else:
                        contribute(alternative.first.name, {tuple(spans)})
                    contribute(alternative.second.name, inherited)
                elif alternative.first is not None:
                    contribute(alternative.first.name, inherited)
                elif alternative.second is not None:
                    contribute(alternative.second.name, inherited)
    return classes


def context_conflicts(grammar, committed):
    """
    The correct meter: the root-reachable decision points no gate decides — each a `(production, context)` pair where
    the production's choice, judged with that context's follow (the one-level-inline judgment), is not
    one-gate-decidable. A production undecidable in isolation may be decidable at every context a root parse reaches it
    under, and then it is no conflict at all; one undecidable at some reachable context is counted once per such
    context, that being the number of specialized copies a context split would leave conflicted. The `committed`
    holders, resolved from the points, are skipped — they decide by declaration. Returns the failing pairs as `{name:
    failing-class-count}`.
    """
    first = _first_table(grammar)
    classes = _follow_classes(grammar, first)
    merged = _follow_spans(grammar, first)

    def first_of(reference):
        return first[reference]

    failing = {}
    for name, production in grammar.items():
        body = production.body
        if not isinstance(body, ir.Choice) or len(body.alternatives) <= 1:
            continue
        if name in committed:
            continue
        if _decides(name, production, grammar, first_of, merged[name]):
            continue  # decidable under the merged follow is decidable under every context's
        reached = classes[name] or {None}
        count = sum(
            1 for k in reached if not _decides(name, production, grammar, first_of, None if k is None else list(k))
        )
        if count:
            failing[name] = count
    return failing


def _decides(name, production, grammar, first_of, follow_spans):
    """
    Whether `production`'s choice is one-gate-decidable under `follow_spans` — what may follow it in the context being
    judged. The certificates are context-free but for one point: a nullable last way's begins widen by the follow, so
    the same choice can be decidable in one context and not another — which is why the meter judges root-reachable
    contexts, this function taking each context's follow in turn, and the isolation view passing the merged one.
    """
    body = production.body
    if all(alternative.gate.peek is None and alternative.gate.guards for alternative in body.alternatives) and all(
        _complementary_guards(one, other)
        for index, one in enumerate(body.alternatives)
        for other in body.alternatives[index + 1 :]
    ):
        # A choice of guard-led ways, pairwise complementary: at most one is enterable at any position, so committing to
        # the first whose guards hold is the way backtracking finds — the factored indent's `==n` against `<n` residue.
        return True
    if (
        len(body.alternatives) == 2
        and _sure_way(body.alternatives[0], grammar)
        and body.alternatives[1].gate.peek is None
    ):
        # A sure way against a fallthrough: the first way, once its peek admits the character, cannot fail — its actions
        # are refusal-free and it calls nothing — so where the gate holds it is the parse backtracking finds first, and
        # where the gate refuses, the fallthrough is backtracking's own next try. The optional separations are the
        # shape: a gated whites scan against taking none.
        return True
    ways = list(body.alternatives)
    spans = []
    for alternative in ways:
        if alternative.gate.peek is not None:
            # a guard is allowed: it only narrows the one candidate its peek admits, and with pairwise-disjoint peeks
            # there is never a second, so a guard refusing falls through exactly as backtracking does
            spanned = _peek_spans(alternative.gate.peek, grammar)
        else:
            # An ungated way is enterable always, so its effective gate is what it can begin with — and an empty match
            # is followed by whatever comes next, so a nullable way's begins widen by the context's follow, except
            # behind an end-of-input guard, where the empty match exists only where nothing follows at all. This is the
            # one-level-inline judgment: the way decides here exactly as it would inlined into the caller.
            spanned, nullable = _alternative_first(alternative, grammar, first_of)
            ends = any(isinstance(guard, ir.EndOfStream) for guard in alternative.gate.guards)
            if nullable and not ends and spanned is not None:
                spanned = None if follow_spans is None else _merged_spans(spanned + list(follow_spans))
        if spanned is None:
            return False
        spans.append(spanned)
    return not any(
        _spans_overlap(spans[one], spans[other])
        and not _complementary_guards(ways[one], ways[other])
        and not _literal_decided(ways[one], grammar)
        for one in range(len(spans))
        for other in range(one + 1, len(spans))
    )


# The actions that cannot refuse wherever the shaped grammar puts them: the zero-width kinds save the guards — a
# `Lt`/`Le` or a lookahead may say no — and the scans that may take nothing, plus the gate-backed single consumes.
_SURE_ACTS = (
    ir.CloseMatch,
    ir.CloseWindow,
    ir.CommitProvisional,
    ir.ConsumeChar,
    ir.ConsumePeeked,
    ir.ConsumeSpan,
    ir.ConsumeTrimmedSpan,
    ir.Emit,
    ir.Empty,
    ir.Error,
    ir.Increase,
    ir.InjectBefore,
    ir.MarkProvisional,
    ir.OpenMatch,
    ir.OpenProvisional,
    ir.OpenWindow,
    ir.PopCode,
    ir.PopMessage,
    ir.PushCode,
    ir.PushMessage,
    ir.RetypeProvisional,
    ir.SetVar,
)


def _sure_way(alternative, grammar):
    """
    Whether `alternative` cannot fail once its peek admits the character: it is peek-gated with no guards, calls nothing
    and recovers nothing, and every action is refusal-free — the gate's own consume, a maximal scan, or a zero-width act
    that is not itself a test. Entered, it is the parse; refused at the gate, backtracking's next try is the choice's
    next way — so committing on the gate is the order backtracking finds.
    """
    return (
        alternative.gate.peek is not None
        and not alternative.gate.guards
        and alternative.first is None
        and alternative.second is None
        and alternative.recover is None
        and all(isinstance(action, _SURE_ACTS) for action in alternative.actions)
    )


def _complementary_guards(one, other):
    """
    Whether the two alternatives can never both be entered, whatever the position: one's gate carries `Lt(x, y)` and the
    other's `Le(y, x)` over the same two expressions, and over integers exactly one of those holds — so a peek the two
    share still decides, the guard pair separating what the character cannot. The indentation loop is the case that
    needs this: eat-another-space and the column-reached stop both gate on a space, told apart only by the column
    against `n`.
    """
    for forward, reverse in ((one, other), (other, one)):
        for guard in forward.gate.guards:
            if isinstance(guard, ir.Lt) and any(
                isinstance(candidate, ir.Le) and candidate.a == guard.b and candidate.b == guard.a
                for candidate in reverse.gate.guards
            ):
                return True
    return False


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


# The follow test a hoisted literal gate carries, declared by literal as `(then, barrier)`, each in its natural
# polarity. A document marker cuts a document only where white, a break, or the end of the input follows —
# `c-forbidden`'s trailing class, the end of the input passing a `then` on its own. A directive keyword is the whole
# name only where no name character follows — the reserved directive's own greedy `ns-char+` is what claims a longer one
# — and anything past a boundary is the grammar's to judge, the seen keyword being its commit.
_MARKER_BOUNDARY = ir.Alt(items=(ir.Ref(name="b-char", args=()), ir.Ref(name="s-white", args=())))
_NAME_BARRIER = ir.Ref(name="ns-char", args=())
_BOUNDARIES = {
    tuple(b"---"): (_MARKER_BOUNDARY, None),
    tuple(b"..."): (_MARKER_BOUNDARY, None),
    tuple(b"YAML"): (None, _NAME_BARRIER),
    tuple(b"TAG"): (None, _NAME_BARRIER),
}


def gate_literals(grammar, namer):
    """
    The grammar with every literal decision gated on the literal whole, in two motions. First, in place: an alternative
    that consumes a literal its gate already pins — the leading consume past any zero-width actions — takes a
    `LiteralPeek` on the full text, the consume behind it a `ConsumePeeked` that cannot fail. Then, hoisted: an
    alternative whose whole way in is a call opening on a literal-gated production takes that production's own
    `LiteralPeek` as its peek — verbatim where the callee's carries a follow test, and from `_BOUNDARIES` where it does
    not. The follow test is declared, never derived: a first set cannot see the line structure that decides these — a
    bare document's first characters include the block sequence's own dash, so a class derived from the continuation
    admits `----` to the marker reading the spec gives a plain scalar, which the marker fixtures catch. The hoist only
    narrows: a callee's entry gate is the alternative's own necessary condition, so what it refuses could never have
    matched — and where the literal and its follow test hold but the way still dies deeper, the grammar's own forbidden
    rule is what denies the rival reading. Runs to a fixpoint, so a gate climbs as many call levels as spell it.
    """
    result = dict(grammar)

    def literal_entry(reference, seen=frozenset()):
        # the literal peek a call's spine opens on — the callee's own, or one found down its single-way calls whose
        # actions are all zero-width — else None
        if reference is None or reference.name in seen or reference.name not in result:
            return None
        body = result[reference.name].body
        if not isinstance(body, ir.Choice) or len(body.alternatives) != 1:
            return None
        [way] = body.alternatives
        peek = way.gate.peek
        if isinstance(peek, ir.LiteralPeek):
            return peek
        if any(not isinstance(action, _ZERO_WIDTH) for action in way.actions):
            return None
        return literal_entry(way.first, seen | {reference.name})

    changed = True
    while changed:
        changed = False
        for name, production in list(result.items()):
            body = production.body
            if not isinstance(body, ir.Choice):
                continue
            ways = []
            for index, way in enumerate(body.alternatives):
                lead = next((action for action in way.actions if not isinstance(action, _ZERO_WIDTH)), None)
                if (
                    way.gate.peek is not None
                    and not isinstance(way.gate.peek, ir.LiteralPeek)
                    and isinstance(lead, ir.ConsumeLiteral)
                    and len(lead.text) > 1
                    and _peek_spans(way.gate.peek, grammar) == [(lead.text[0], lead.text[0])]
                ):
                    gate = dataclasses.replace(way.gate, peek=ir.LiteralPeek(text=lead.text, then=None, barrier=None))
                    swapped = tuple(
                        ir.ConsumePeeked(text=action.text) if action is lead else action for action in way.actions
                    )
                    way = dataclasses.replace(way, gate=gate, actions=swapped)
                    changed = True
                entry = literal_entry(way.first)
                if (
                    entry is not None
                    and way.gate.peek is not None
                    and not isinstance(way.gate.peek, ir.LiteralPeek)
                    and all(isinstance(action, _ZERO_WIDTH) for action in way.actions)
                    and _peek_spans(way.gate.peek, grammar) == [(entry.text[0], entry.text[0])]
                ):
                    hoisted = entry
                    if entry.then is None and entry.barrier is None:
                        # A declared follow test refuses entries the way in would have taken to a committed error, so it
                        # may narrow a gate only where a later alternative catches what it refuses — the fall through
                        # backtracking's failed rival lands on. A way that stands alone keeps its own gate and its own
                        # failure surface, the suffix's seen-marker commit being the fixture-pinned case.
                        declared = _BOUNDARIES.get(entry.text) if index + 1 < len(body.alternatives) else None
                        if declared is None:
                            hoisted = None
                        else:
                            then, barrier = declared
                            hoisted = dataclasses.replace(entry, then=then, barrier=barrier)
                    if hoisted is not None:
                        way = dataclasses.replace(way, gate=dataclasses.replace(way.gate, peek=hoisted))
                        changed = True
                ways.append(way)
            alternatives = tuple(ways)
            if alternatives != body.alternatives:
                result[name] = dataclasses.replace(production, body=ir.Choice(alternatives=alternatives))
    return result


def reorder_declared(grammar, namer):
    """
    The grammar with each `DECLARED_REORDERS` point's two alternatives swapped — one generic move, its targets and their
    reasons data rather than logic, so no transformation recognizes a production by name in code. Each entry names a
    point of interest; the swap lands on every production currently holding it, and a point held by anything but the
    two-way choice the swap speaks about is a loud fault: a step before this one changing the shape must be seen, not
    absorbed. The corpus holds each swap to the stream as it holds every step.
    """
    result = dict(grammar)
    for point in sorted(DECLARED_REORDERS):
        for name in namer.points.current(point):
            production = grammar.get(name)
            if production is None:
                raise AssertionError(f"{point}: declared reordered, but the grammar holds no `{name}`")
            if not isinstance(production.body, ir.Choice) or len(production.body.alternatives) != 2:
                raise AssertionError(f"{point}: `{name}` is not the two-way choice the swap speaks about")
            one, other = production.body.alternatives
            result[name] = dataclasses.replace(production, body=ir.Choice(alternatives=(other, one)))
    return result


def extend_returns(grammar, namer):
    """
    The grammar with each `DECLARED_EXTENSIONS` site folded: the site's one way must be a call and a continuation whose
    own single way is actions alone, and the call is replaced by a minted copy of its production with those actions
    appended to every return path — a way with no calls takes them onto its actions, a tail call retargets to the copy
    of its target, a way with a continuation retargets that continuation's copy, and a recursion meets its own copy in
    the memo and folds. The copies stay within the extension's own walk, so every other caller of the original stands
    untouched and the purge sweeps what dies. A declared name the grammar does not hold, or a site or continuation that
    is not the shape the fold speaks about, is a loud fault. Appending actions that close a `(token)` or `(match)` scope
    they do not open would read the copy's own frame where the original read the site's; such an extension is refused
    rather than mis-scoped.
    """
    result = dict(grammar)

    def extended(name, extension, copies, owner):
        if name in copies:
            return copies[name]
        production = result[name]
        copy = namer.fresh(name)
        copies[name] = copy
        if not isinstance(production.body, ir.Choice):
            # A terminal cannot take the appended actions inside; its copy is the wrapper `terminal then extension`, the
            # extension standing in a minted actions-only continuation shared across the walk.
            if None not in copies:
                helper = namer.fresh(owner)
                copies[None] = helper
                way = ir.Alternative(gate=ir.Gate(None, ()), actions=tuple(extension), first=None, second=None)
                result[helper] = ir.Prod(production.number, helper, (), ir.Choice((way,)))
            wrapper = ir.Alternative(
                gate=ir.Gate(None, ()),
                actions=(),
                first=ir.Ref(name=name, args=()),
                second=ir.Ref(name=copies[None], args=()),
            )
            result[copy] = ir.Prod(production.number, copy, production.params, ir.Choice((wrapper,)))
            return copy
        ways = []
        for alternative in production.body.alternatives:
            if alternative.second is not None:
                ways.append(
                    dataclasses.replace(
                        alternative,
                        second=ir.Ref(
                            name=extended(alternative.second.name, extension, copies, owner),
                            args=alternative.second.args,
                        ),
                    )
                )
            elif alternative.first is not None:
                ways.append(
                    dataclasses.replace(
                        alternative,
                        first=ir.Ref(
                            name=extended(alternative.first.name, extension, copies, owner),
                            args=alternative.first.args,
                        ),
                    )
                )
            else:
                ways.append(dataclasses.replace(alternative, actions=alternative.actions + extension))
        result[copy] = ir.Prod(production.number, copy, production.params, ir.Choice(tuple(ways)))
        return copy

    for point in sorted(DECLARED_EXTENSIONS):
        for name in namer.points.current(point):
            production = grammar.get(name)
            if production is None:
                raise AssertionError(f"{point}: declared extended, but the grammar holds no `{name}`")
            if not isinstance(production.body, ir.Choice) or len(production.body.alternatives) != 1:
                raise AssertionError(f"{point}: `{name}` is not the single way the fold speaks about")
            [way] = production.body.alternatives
            if way.first is None or way.second is None:
                raise AssertionError(f"{point}: `{name}`'s way is not a call and a continuation")
            follower = grammar.get(way.second.name)
            if follower is None or not isinstance(follower.body, ir.Choice) or len(follower.body.alternatives) != 1:
                raise AssertionError(
                    f"{point}: the continuation is not the single-way production the fold speaks about"
                )
            [tail] = follower.body.alternatives
            if tail.first is not None or tail.second is not None or tail.gate.peek is not None or tail.gate.guards:
                raise AssertionError(f"{point}: the continuation is not actions alone, so the fold cannot absorb it")
            if _needs_code(tail.actions) or _needs_origin(tail.actions):
                raise AssertionError(f"{point}: the continuation closes a scope it does not open — the fold refuses it")
            copy = extended(way.first.name, tail.actions, {}, name)
            result[name] = dataclasses.replace(
                production,
                body=ir.Choice((dataclasses.replace(way, first=ir.Ref(name=copy, args=way.first.args), second=None),)),
            )
    return result


def speculate_folds(grammar, namer):
    """
    The grammar with the flow fold determinized — the determinizer's cycle run as a pipeline step: detect the conflict,
    generate its provisional productions from the decision it reads off the grammar, replace the site. Held to the
    corpus here like every other step. Imported at call time, so the two modules pair without a load-time cycle.
    """
    import determinize

    return determinize.determinize(grammar, namer)


# The provisional-run actions, and the states the balance walk tracks them through: no run open, a run open, and a run
# open that has taken its mark, which cuts it in two so a retype or an injection may name a side.
_PROVISIONAL = (ir.OpenProvisional, ir.MarkProvisional, ir.RetypeProvisional, ir.InjectBefore, ir.CommitProvisional)
_RUN_CLOSED, _RUN_OPEN, _RUN_MARKED = "closed", "open", "marked"


def _run_state_after(action, state, name, faults):
    """The run state after `action` meets `state`, recording a fault where they do not fit."""
    if isinstance(action, ir.OpenProvisional):
        if state != _RUN_CLOSED:
            faults.add(f"{name}: an OpenProvisional inside an open run")
            return state
        return _RUN_OPEN
    if isinstance(action, ir.MarkProvisional):
        if state == _RUN_CLOSED:
            faults.add(f"{name}: a MarkProvisional outside a run")
            return state
        return _RUN_MARKED  # re-taken in a marked run, the mark moves — the last taken wins
    if isinstance(action, ir.RetypeProvisional):
        if state == _RUN_CLOSED:
            faults.add(f"{name}: a RetypeProvisional outside a run")
        elif action.region != "all" and state != _RUN_MARKED:
            faults.add(f"{name}: a RetypeProvisional of a marked region with no mark")
        return state
    if isinstance(action, ir.InjectBefore):
        if state == _RUN_CLOSED:
            faults.add(f"{name}: an InjectBefore outside a run")
        elif action.at == "mark" and state != _RUN_MARKED:
            faults.add(f"{name}: an InjectBefore at the mark with no mark")
        return state
    if isinstance(action, ir.CommitProvisional):
        if state == _RUN_CLOSED:
            faults.add(f"{name}: a CommitProvisional with no run open")
            return state
        return _RUN_CLOSED
    return state


def provisional_faults(grammar):
    """
    The provisional-run actions that do not balance: an open inside an open run, a mark outside one, a retype or inject
    outside one, a marked-region retype or a mark injection where no mark was taken, or a production carrying the
    actions that no root ever reaches. The run is the queue's, one for the whole parse, so its state flows through calls
    rather than frames: each production maps the states it is entered under to the states it can return in, the fixpoint
    seeded at the roots — the productions no body references — with no run open. The walk errs wide: a state joins
    wherever any path could carry it, a recovery is walked from every state its call spans, and a fault here is a step's
    to avoid, never an input's to trigger.
    """
    faults = set()
    referenced = set()

    def note(node):
        if isinstance(node, ir.Ref):
            referenced.add(node.name)
        ir.rebuilt(node, lambda child: note(child) or child)

    for production in grammar.values():
        note(production.body)
    exits = {name: {} for name in grammar}  # per production: the states it is entered under -> the states it returns in
    for name in grammar:
        if name not in referenced:
            exits[name][_RUN_CLOSED] = set()
    grew = [True]  # a new entry or a grown exit set both demand another round, from wherever they are noticed

    def call_exits(reference, state):
        # what the callee can return in when entered under `state`, registering the entry where it is new
        if state not in exits[reference.name]:
            exits[reference.name][state] = set()
            grew[0] = True
        return exits[reference.name][state]

    while grew[0]:
        grew[0] = False
        for name, entry in [(name, entry) for name, entries in exits.items() for entry in entries]:
            body = grammar[name].body
            if not isinstance(body, ir.Choice):
                outs = {entry}  # a terminal char-set: the run state passes through untouched
            else:
                outs = set()
                for alternative in body.alternatives:
                    state = entry
                    for action in alternative.actions:
                        state = _run_state_after(action, state, name, faults)
                    states = {state}
                    if alternative.first is not None:
                        states = set(call_exits(alternative.first, state))
                        if alternative.recover is not None:  # a recovery can pick up from any state the call spans
                            for spanned in {state} | states:
                                states |= call_exits(alternative.recover, spanned)
                    if alternative.second is not None:
                        states = {out for spanned in states for out in call_exits(alternative.second, spanned)}
                    outs |= states
            if not outs <= exits[name][entry]:
                exits[name][entry] |= outs
                grew[0] = True

    def holds_provisional(node):
        if isinstance(node, _PROVISIONAL):
            return True
        found = []
        ir.rebuilt(node, lambda child: found.append(holds_provisional(child)) or child)
        return any(found)

    for name, production in grammar.items():
        if holds_provisional(production.body) and not exits[name]:
            faults.add(f"{name}: provisional actions no root reaches")
    return sorted(faults)


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


def _is_nullable(node, nullable):
    """Whether `node` can match the empty string, given the set of `nullable` production names."""
    if isinstance(node, _ZERO_WIDTH):
        return True
    if isinstance(node, ir.Seq):
        return all(_is_nullable(item, nullable) for item in node.items)
    if isinstance(node, ir.Alt):
        return any(_is_nullable(item, nullable) for item in node.items)
    if isinstance(node, ir.Ref):
        return node.name in nullable
    return False


def _nullable_set(grammar, opaque=frozenset()):
    """
    The productions whose body can match the empty string, a boolean least fixpoint. A production in `opaque` reads as
    consuming whatever its shape — the roots and recovery targets that keep their empty ways, so nothing distributes
    them. A recovery reads the input, a scan that may take zero characters is a value the run decides not a way it
    chooses, so neither adds a way to match empty.
    """
    nullable = set()
    changed = True
    while changed:
        changed = False
        for name, production in grammar.items():
            if name not in nullable and name not in opaque and _is_nullable(production.body, nullable):
                nullable.add(name)
                changed = True
    return nullable


def eliminate_empties(grammar, namer):
    """
    Make the grammar proper: no production, a root or recovery target aside, matches the empty string by shape — the
    classic ε-elimination, done at the call site so it is scope-aware. A nullable production keeps its own body and
    gains a minted copy of it that must read; each `Ref(P)` becomes `P_consuming | residue`, the copy that reads tried
    first and the zero-width residue — the guards, markers and emitters the empty match is made of — its fallback. The
    choice stands exactly where the call stood, so a `Push`/`Pop` or `Open`/`Close` pair around it is never split or
    duplicated. A purely empty production, the `e-node` among them, has no consuming copy and dissolves into the residue
    at each site. What was decided blind inside `P` becomes a choice sitting next to what follows it, whose first
    characters are what decide it. The original is left as it stands, and with every call to it rewritten the purge is
    what takes it — so a fixture that enters it directly pins to the stage before this one.

    A copy is made to consume by keeping only the ways that read: an alternation drops its empty way, a sequence that
    already reads distributes its calls in place, and a sequence every part of which may be empty splits into an ordered
    choice over which part is the first to read — the parts before it held to their empty match, in the order the parse
    already tried them. A root keeps its empty ways — an empty stream is YAML — and a recovery target keeps its own, the
    parse re-entering it at an error. The empty a value forces — a span scan of zero characters, an `s-indent(0)` — is a
    run the scan decides, not a way a parse chooses, and stays.
    """

    def named_inside(node, found):
        if isinstance(node, ir.Ref):
            found.add(node.name)
        ir.rebuilt(node, lambda child: (named_inside(child, found), child)[1])

    def recovery_names(node, found):
        if isinstance(node, ir.Recover):
            named_inside(node.recovery, found)
        ir.rebuilt(node, lambda child: (recovery_names(child, found), child)[1])

    # The productions a parse enters by name rather than through a call, and the ones a `(recover)` names. Each keeps
    # its empty ways: they are entered where there is no call site to hold the choice this distributes into one.
    exempt = entered_by_name(grammar)
    for production in grammar.values():
        recovery_names(production.body, exempt)

    nullable_all = _nullable_set(grammar)  # every production that may match empty, exempt included
    strip = _nullable_set(grammar, frozenset(exempt))  # the ones this makes consuming — exempt kept nullable

    def consumes(node, seen=frozenset()):
        """Whether `node` can match a non-empty string — has a consuming form to keep."""
        if isinstance(node, _ZERO_WIDTH):
            return False
        if isinstance(node, (ir.Seq, ir.Alt)):
            return any(consumes(item, seen) for item in node.items)
        if isinstance(node, ir.Ref):
            return node.name not in seen and consumes(grammar[node.name].body, seen | {node.name})
        return True  # a terminal or a scan reads the input

    # The consuming copy of every nullable production that has ways that read; one name per production, so a recursion
    # meets its own copy rather than minting another.
    minted = {name: namer.fresh(name) for name in sorted(strip) if consumes(grammar[name].body)}

    def residue(node, seen=frozenset()):
        """
        `node`'s empty match — the zero-width chain it matches empty by — or `None` where it cannot match one. A call
        cycle is cut: an empty match only reachable through itself is an infinite parse, not a way. An exempt reference
        keeps its empty ways, so it stands for its own empty match. A binding the empty match carries keeps its target,
        so the call must pass that parameter by its own name — an argument that renames it would have the inlined
        binding write somewhere else, and is a loud fault rather than a silent one.
        """
        if isinstance(node, _ZERO_WIDTH):
            return node
        if isinstance(node, ir.Seq):
            parts = tuple(residue(item, seen) for item in node.items)
            return None if any(part is None for part in parts) else _flat_seq(parts)
        if isinstance(node, ir.Alt):
            held = tuple(part for part in (residue(item, seen) for item in node.items) if part is not None)
            return held[0] if len(held) == 1 else (ir.Alt(held) if held else None)
        if isinstance(node, ir.Ref):
            if node.name in strip:
                if node.name in seen:
                    return None
                held = residue(grammar[node.name].body, seen | {node.name})
                if held is None:
                    return None
                mapping = dict(zip(grammar[node.name].params, node.args))
                for parameter in _bound_params(held, set()):
                    argument = mapping.get(parameter)
                    if argument is not None and not (isinstance(argument, ir.Param) and argument.name == parameter):
                        raise AssertionError(f"{node.name}: its empty match binds `{parameter}`, passed as an argument")
                return _bound(held, mapping)
            return node if node.name in nullable_all else None  # exempt-and-nullable stands for its own empty match
        return None

    def distribute(node):
        """`node` with each nullable reference replaced in place by its consuming-copy-or-empty choice."""
        node = ir.rebuilt(node, distribute)
        if isinstance(node, ir.Ref) and node.name in strip:
            held = residue(node)
            if node.name not in minted:
                return held
            return ir.Alt((dataclasses.replace(node, name=minted[node.name]), held))
        return node

    def consuming(node):
        """`node` made to match at least one character, its references distributed."""
        if isinstance(node, ir.Alt):
            kept = tuple(consuming(item) for item in node.items if consumes(item))
            return kept[0] if len(kept) == 1 else ir.Alt(kept)
        if isinstance(node, ir.Seq):
            if any(not _is_nullable(item, nullable_all) for item in node.items):
                return distribute(node)  # a part must read, so the sequence already consumes
            ways = []
            for index, item in enumerate(node.items):
                if consumes(item):
                    before = tuple(residue(part) for part in node.items[:index])
                    after = tuple(distribute(part) for part in node.items[index + 1 :])
                    ways.append(_flat_seq(before + (consuming(item),) + after))
                if not _is_nullable(item, nullable_all):
                    break  # this part must read, so nothing after it is the first that does
            return ways[0] if len(ways) == 1 else ir.Alt(tuple(ways))
        if isinstance(node, ir.Ref):
            return dataclasses.replace(node, name=minted[node.name]) if node.name in minted else node
        return distribute(node)

    result = {}
    for name, production in grammar.items():
        # A nullable production stands as it is — unreachable once its calls are rewritten, and the purge's to take —
        # beside the copy that carries its consuming ways.
        result[name] = (
            production if name in strip else dataclasses.replace(production, body=distribute(production.body))
        )
        if name in minted:
            copy = minted[name]
            result[copy] = ir.Prod(production.number, copy, production.params, consuming(production.body))

    lingering = sorted(_nullable_set(purged(result), frozenset(exempt)))
    assert not lingering, f"production(s) still matching empty by shape: {lingering[:8]}"
    return result


def _bound_params(node, found):
    """The parameters `node`'s own shape binds — the targets of the `(set)` and `(increase)` actions it holds."""
    if isinstance(node, (ir.SetVar, ir.Increase)):
        found.add(node.param)
    ir.rebuilt(node, lambda child: (_bound_params(child, found), child)[1])
    return found


def declare_bindings(grammar, namer):
    """
    Give each production the parameters its own body binds and does not declare. A value leaves a production only
    through a declared parameter passed by reference, so a binding a production does not declare is written into its own
    frame and dropped on return; it survives only where the write stands above every frame that reads it, which nothing
    holds it to — and a step that mints a helper out of the middle of such a body puts a frame exactly there. Declared,
    every helper a later step mints carries it, a minted call passing each declared parameter as itself, so the write
    reaches the reader wherever the split lands. No call site changes: an argument a caller does not give leaves the
    parameter unbound, which is the ambient value the production read before. What a production binds for itself is what
    it detects — the block header's auto-detected indent `m` and the leading empties' floor `f` — and the new parameter
    is appended, so every positional argument still lands where it did.
    """
    result = {}
    for name, production in grammar.items():
        missing = tuple(sorted(_bound_params(production.body, set()) - set(production.params)))
        result[name] = dataclasses.replace(production, params=production.params + missing) if missing else production
    return result


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
    ("hoist-repetition-empties", hoist_repetition_empties),
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
    ("eliminate-empties", eliminate_empties),
    ("declare-bindings", declare_bindings),
    ("lift-choices", lift_choices),
    ("single-consumes", single_consumes),
    ("binarize", binarize),
    ("alternative-shape", alternative_shape),
    ("lower-recovers", lower_recovers),
    ("inline-singles", inline_singles),
    ("subsume-ways", subsume_ways),
    ("refine-indents", refine_indents),
    ("gate-hoist", gate_hoist),
    ("split-conflicts", split_conflicts),
    ("factor-prefixes", factor_prefixes),
    ("gate-hoist-leftovers", gate_hoist),
    ("speculate-folds", speculate_folds),
    ("gate-literals", gate_literals),
    ("reorder-declared", reorder_declared),
    ("extend-returns", extend_returns),
]


def entered_by_name(grammar):
    """
    The productions a parse enters without a call, each a start state of its own: the root's copy under every resume
    policy — the one parameter the caller chooses — and the recovery a failed cut lands on. A caller cannot be
    redirected to what no caller names, so these keep their own names through every cleanup.
    """
    return {
        ir.entry(grammar, name, {"n": -1, "r": resume})[0]
        for name in (ir.ROOT, ir.RECOVER)
        for resume in annotated2ir.RESUMES
    }


def reachable(grammar):
    """
    The productions the parse can enter, transitively: the ones it enters by name and everything those reference, read
    off each node's own `references`.
    """
    seen = set()
    worklist = list(entered_by_name(grammar))
    while worklist:
        name = worklist.pop()
        if name in seen or name not in grammar:
            continue
        seen.add(name)
        worklist.extend(grammar[name].references())
    return seen


def purged(grammar):
    """`grammar` without the productions no parse can enter — dead-production elimination, from the root down."""
    keep = reachable(grammar)
    return {name: production for name, production in grammar.items() if name in keep}


def _spliced(grammar, keep):
    """
    `grammar` with every do-nothing frame gone: a production whose whole body is one ungated, action-free call, with no
    continuation and no recovery of its own, is what it calls, so every reference to it becomes a reference to that
    callee — its parameters bound to the call's arguments. A chain of them collapses in one pass, and a frame that
    reaches only itself is left where it stands, a call that never returns being no simpler spelled inline.
    """
    frames = {}
    for name, production in grammar.items():
        body = production.body
        if name in keep or not isinstance(body, ir.Choice) or len(body.alternatives) != 1:
            continue
        [way] = body.alternatives
        if way.gate.peek is not None or way.gate.guards or way.actions:
            continue
        if way.first is None or way.second is not None or way.recover is not None:
            continue
        frames[name] = way.first
    if not frames:
        return grammar, {}

    def resolved(reference):
        seen = set()
        while isinstance(reference, ir.Ref) and reference.name in frames and reference.name not in seen:
            seen.add(reference.name)
            inner = frames[reference.name]
            reference = _bound(inner, dict(zip(grammar[reference.name].params, reference.args)))
        return reference

    def rewrite(node):
        node = ir.rebuilt(node, rewrite)
        return resolved(node) if isinstance(node, ir.Ref) else node

    swept = {name: dataclasses.replace(p, body=rewrite(p.body)) for name, p in grammar.items()}
    return swept, {name: resolved(reference).name for name, reference in frames.items()}


def _grouped(grammar):
    """
    `{name: group}`, productions that behave alike sharing a group: same parameters, and the same body once every
    reference in it is read as the group of what it names rather than by the name itself — so two loops that differ only
    in what their helpers are called come out alike, which comparing the bodies as written cannot see. The groups are
    the coarsest partition that stays stable under that reading: everything starts alike, and a difference splits it,
    until a round splits nothing.
    """

    def keyed(body, block):
        def rewrite(node):
            node = ir.rebuilt(node, rewrite)
            if isinstance(node, ir.Ref):
                return dataclasses.replace(node, name=f"#{block.get(node.name, -1)}")
            return node

        return rewrite(body)

    block = dict.fromkeys(grammar, 0)
    while True:
        signatures, refined = {}, {}
        for name in sorted(grammar):
            signature = (grammar[name].params, keyed(grammar[name].body, block))
            refined[name] = signatures.setdefault(signature, len(signatures))
        if refined == block:
            return block
        block = refined


def _merged(grammar, keep):
    """
    `grammar` with productions that behave alike spelled once: a call to one is a call to any other of its group, so
    every reference to a duplicate becomes a reference to the one kept. A production the parse enters by name is always
    kept, having a name a caller cannot be redirected away from — and where a group holds two of those, both stand.
    """
    block = _grouped(grammar)
    standing = {}
    for name in sorted(grammar, key=lambda name: (name not in keep, name)):
        standing.setdefault(block[name], name)
    canonical = {name: standing[block[name]] for name in grammar if name not in keep and standing[block[name]] != name}
    if not canonical:
        return grammar, {}

    def rewrite(node):
        node = ir.rebuilt(node, rewrite)
        if isinstance(node, ir.Ref) and node.name in canonical:
            return dataclasses.replace(node, name=canonical[node.name])
        return node

    return {name: dataclasses.replace(p, body=rewrite(p.body)) for name, p in grammar.items()}, canonical


def cleaned(grammar):
    """
    `grammar` with what a transformation leaves behind swept up, and the renames the sweep made — `{gone: standing}`, so
    what tracks a production by name follows its content to where it went. The frames that only call something else are
    spliced out, the productions that spell the very same thing are merged into one, and — last, so it sees what the
    other two strand — every production no parse can enter is purged. Splicing and merging feed each other, a merge
    making two frames the same call and a splice making two callers identical, so they run to a fixpoint. None of it
    changes what the grammar matches or emits: a spliced frame ran no action and made no decision, and a merged
    production is the one kept, character for character.
    """
    keep = entered_by_name(grammar)
    renames = {}

    def landed(name):
        seen = set()
        while name in renames and name not in seen:
            seen.add(name)
            name = renames[name]
        return name

    while True:
        spliced, splices = _spliced(grammar, keep)
        swept, merges = _merged(spliced, keep)
        if swept == grammar:
            return purged(grammar), {gone: landed(gone) for gone in renames}
        renames.update(splices)
        renames.update(merges)
        grammar = swept


def stages(grammar):
    """
    The grammar after each step, as `(label, grammar)` pairs, opening with `("base", grammar)` — what `check_normalize`
    diffs the interpreter's token stream across, so a step that changes it is named. Returns the `Points` beside the
    pairs, so the analysis of the final grammar reads its committed holders from the same tracking the steps used. One
    `Namer` is threaded through the steps, so the helper productions they mint number `<base>_<N>` off a count shared
    across them. Each step's grammar is cleaned of what the step leaves behind — the do-nothing frames, the duplicate
    productions, and the ones the root no longer reaches — since a transformation that replaces a call site strands the
    callee, and a frame or a duplicate the sweep leaves standing would hold the determinize meter above its honest
    floor. The base grammar is kept whole: it is the grammar as frozen at the completeness gate, cleaned by no step.
    """
    namer = Namer()
    namer.points.settle("base", grammar)
    result = [("base", grammar)]
    for name, transform in STEPS:
        grammar, renames = cleaned(transform(grammar, namer))
        namer.points.follow(renames)
        namer.points.settle(name, grammar)
        result.append((name, grammar))
    return result, namer.points


def normalize(grammar):
    """The grammar with every step applied in order, cleaned after each of what the step leaves behind."""
    return stages(grammar)[0][-1][1]
