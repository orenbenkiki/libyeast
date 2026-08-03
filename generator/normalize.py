# SPDX-License-Identifier: MIT
"""
Normalize the grammar, one goal at a time.

The pipeline is a sequence of phases, each owning one invariant: a phase adds steps until its count is none, and from
its end the invariant is enforced — the law's "none stays none" makes every later step keep it. A phase finished with a
green corpus is a checkpoint worth landing on its own.

Phase 0 is the chomping. `t` is a parameter the base grammar sets by matching an indicator and reads two productions
later through the environment, so a read of it means nothing until a caller is known. `lift-chomping` inverts the setter
into a switch and `monomorphize` specializes that switch into the names, after which nothing declares, passes or reads
it — `no-t-parameter` at none.

Phase 1 is the character questions. A set of characters is written many ways and asked in several — a union, a
difference, a reference, the item of a lookaround — and the parser tests one key for one bit. `lower-char-sets` says
every one of them as the intervals it denotes, after which each question about a character is a `CharSet` or a literal —
`every-character-question-is-a-set-or-a-literal` at none. It follows the specialization because a set a context
parameter picks denotes nothing until a caller is known.
"""

import dataclasses
import inspect

import annotated2ir
import chars
import ir


@dataclasses.dataclass(frozen=True)
class Invariant:
    """
    Something the grammar is held to, and how to count where it is broken.

    The name is what a fault reads as and what the invariant is known by, so a step naming one already named is reducing
    that same count rather than one of its own. `test` takes a grammar and gives back the places it is broken, as error
    strings — a list, so its length is the count and its contents say where.

    A test wanting the points of interest as well takes `(grammar, points)`, which is what a declared site's property
    needs: the site is a point, tracked beside the grammar rather than in it, so what a declared step makes true cannot
    be read off the productions alone.
    """

    name: str
    test: object

    def __call__(self, grammar, points=None):
        wants_points = len(inspect.signature(self.test).parameters) > 1
        return self.test(grammar, points) if wants_points else self.test(grammar)


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
            candidates = {name for name in grammar if any(_is_descendant(name, held) for held in self.at[point])}
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


def _is_descendant(name, holder):
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


def _is_using(node, param):
    """
    Whether `Param(param)` appears anywhere in `node`. Walks every field itself: the generic walker holds a `Param` as a
    value and never visits one a field holds directly, where here the parameters are exactly what is being looked for.
    """
    if isinstance(node, ir.Param):
        return node.name == param
    if not dataclasses.is_dataclass(node):
        return False
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if dataclasses.is_dataclass(value):
            if _is_using(value, param):
                return True
        elif isinstance(value, tuple):
            if any(dataclasses.is_dataclass(item) and _is_using(item, param) for item in value):
                return True
    return False


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
    first = next(index for index, item in enumerate(items) if _is_using(item, param))
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
                if param in values and param not in production.params and _is_using(body, param):
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


@dataclasses.dataclass(frozen=True)
class Step:
    """
    One step of the pipeline: what it is called, what it does, what it makes true, and whether it finishes making it.

    A step's job is to establish an invariant, not to move a meter. The meter moves when the last step of a sequence
    lands; what every step before it buys is a property the rest may lean on, which is how the grammar comes to be
    simple enough for common-prefix factoring and gate disjointness to decide it.

    An invariant is a count and not a yes-or-no. `invariants` says which ones this step is about and how to count where
    each is broken — a step often makes more than one thing true, and `lower-star` leaves both no complex `Star` and
    every repetition a character-set run. A step naming an invariant is taken to **finish** it, that being what a step
    is for; `reduces` names the ones among them it only lowers the count of, for a count several steps share — the gate
    hoists do, and no one of them leaves none. An `Invariant` and a `transform` are both shared where two steps do the
    same work on different grounds.

    `lapses` is what this step is allowed to break: `{invariant: reason}`, empty for nearly every step, holding a
    written reason where a step undoes something an earlier one settled. `invariant_faults` holds the pipeline to the
    law — a count never rises, a settling step leaves none, and none stays none — and reads a lapse as the one licence
    to break it.

    An invariant goes by its name, so two steps naming the same one are reducing a single count and the set of them is
    collected by name rather than by how many steps mention it.

    **An empty `invariants` is temporary, and `untestable` says why one is empty for good.** A step with neither
    transforms the grammar and promises something nothing checks, which is the shape every hard day here has started
    from, and `untested_steps` counts those. Some steps genuinely have nothing standing to test — what they make true is
    momentary, or is a property of a run rather than of a shape — and each says so in a sentence rather than sitting in
    a count that can never reach none. Naming both is a fault: a step either has an invariant or a reason.
    """

    name: str
    transform: object
    invariants: object = ()
    reduces: tuple = ()
    lapses: dict = dataclasses.field(default_factory=dict)
    untestable: str = ""

    def __post_init__(self):
        held = (self.invariants,) if isinstance(self.invariants, Invariant) else tuple(self.invariants)
        object.__setattr__(self, "invariants", held)

    def does_settle(self, invariant):
        """Whether this step is the one that takes `invariant`'s count to none."""
        return invariant.name in {held.name for held in self.invariants} and invariant.name not in self.reduces


def _absent(name, *kinds):
    """
    The invariant that a node kind is gone — what a lowering step makes true, that the shape it rewrites is nowhere in
    the grammar and nothing later spells it again.
    """

    def test(grammar):
        faults = []

        def walk(owner, node):
            if isinstance(node, kinds):
                faults.append(f"{owner}: a {type(node).__name__} survives the step that lowers it")
            ir.rebuilt(node, lambda child: (walk(owner, child), child)[1])

        for owner, production in grammar.items():
            walk(owner, production.body)
        return faults

    return Invariant(name, test)


def _computed_chomping(grammar):
    """
    Calls handing `t` a value worked out rather than written — a chomping the specialization cannot pick a copy for.

    A finite parameter specializes away only where every call names one of its values outright: a copy per value is
    made, and a call carrying an expression has no copy to go to. Made lexical, `t` is one of the two the context is.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.Ref):
                callee = grammar.get(node.name)
                if callee is not None and "t" in callee.params:
                    held = node.args[callee.params.index("t")]
                    if not isinstance(held, ir.Lit):
                        faults.append(f"{owner}: hands `{node.name}` a chomping it works out")
            ir.rebuilt(node, lambda child: (walk(child, owner), child)[1])

        walk(production.body)
    return faults


CHOMPING_LEXICAL = Invariant("chomping-is-lexical", _computed_chomping)


def untested_steps():
    """
    The steps naming neither an invariant nor a reason for having none — each promising what nothing checks.

    Driven to none: at none, `Step.invariants` loses its default and a step must name one or say why it cannot. Until
    then this is the honest measure of how much of the pipeline rests on nothing but the corpus. A step with a written
    `untestable` is not one of these; what it claims is on the record and answerable.
    """
    return [step.name for step in STEPS if not step.invariants and not step.untestable]


def invariant_faults(stages, points=None):
    """
    Where the pipeline breaks its own law, as error strings — empty where it holds.

    Each step's `test` counts the places its invariant is broken, and the law over the stages is: the count never rises,
    the step that settles it leaves none, and past that it stays none. A step whose `lapses` names the invariant is
    licensed to break it and says why; a step that breaks it without one is a fault named where it stands.

    The invariant is named by its test, so several steps reducing one count are read as one law. A count is measured
    from the first stage whose step names it, the stages before it being no business of the invariant's.
    """
    faults, taken = [], set()
    by_name = {held.name: held for step in STEPS for held in step.invariants}
    for step in STEPS:
        if step.invariants and step.untestable:
            faults.append(f"[{step.name}] names an invariant and says it has none — one or the other")
        for named in step.lapses:
            if named not in by_name:
                faults.append(f"[{step.name}] declares a lapse of `{named}`, which no step carries")
    for named in sorted(by_name):
        test = by_name[named]
        first = min(index for index, step in enumerate(STEPS) if named in {h.name for h in step.invariants})
        settled, standing = False, None
        for index in range(first, len(STEPS)):
            step, (label, grammar) = STEPS[index], stages[index + 1]
            count = len(test(grammar, points))
            licensed = named in step.lapses
            broken = (
                (standing is not None and count > standing) or (settled and count) or (step.does_settle(test) and count)
            )
            if broken and licensed:
                taken.add((step.name, named))
            if standing is not None and count > standing and not licensed:
                faults.append(f"[{label}] `{named}` rises from {standing} to {count}, and the step declares no lapse")
            if settled and count and not licensed:
                faults.append(f"[{label}] `{named}` is settled and stands at {count}, and the step declares no lapse")
            if step.does_settle(test) and count and not licensed:
                faults.append(f"[{label}] settles `{named}` and leaves {count} standing")
            settled = (settled or step.does_settle(test)) and not count
            standing = count
    # A lapse is a reason for something that happens. One nothing happens under is a claim the grammar has outgrown, and
    # it goes rather than standing as a licence nobody needs — the same net the declared tables answer to.
    for step in STEPS:
        for named in step.lapses:
            if named in by_name and (step.name, named) not in taken:
                faults.append(f"[{step.name}] declares a lapse of `{named}` and does not break it")
    # And the same the other way about: a step naming an invariant it neither lowers nor leaves at none is claiming work
    # it does not do. The law reads both directions — no step without an invariant, no invariant without a step doing
    # something about it.
    for index, step in enumerate(STEPS):
        for held in step.invariants:
            before, after = len(held(stages[index][1], points)), len(held(stages[index + 1][1], points))
            if after and after >= before:
                faults.append(
                    f"[{step.name}] names `{held.name}` and neither lowers it ({before} to {after}) nor leaves none"
                )
    return faults


def standing_invariants(grammar, points=None):
    """
    Every invariant the pipeline names that `grammar` still breaks, as `[(name, count)]` worst first.

    What the steps settle between them is not the same question as what is true at the end: an invariant settled early
    and broken later under a declared lapse stands here all the same, and a lapse is a reason rather than an excuse.
    Each one standing is work still owed — a step that has not been written — so this is the list Phase 03 finishes by
    emptying, and it says so mechanically instead of leaving it to be noticed.
    """
    named = {held.name: held for step in STEPS for held in step.invariants}
    standing = [(name, len(test(grammar, points))) for name, test in sorted(named.items())]
    return sorted(((name, count) for name, count in standing if count), key=lambda held: -held[1])


def unsettled_invariants():
    """
    The invariants some step reduces and no step settles — a count driven down, with nothing yet claiming to finish it.

    Not a fault: a step that only reduces a count is doing its job, and naming a settler before one exists would be a
    claim rather than a check. Counted so the gap is read rather than assumed away.
    """
    return sorted(
        {held.name for step in STEPS for held in step.invariants if not any(other.does_settle(held) for other in STEPS)}
    )


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
    `grammar` with every do-nothing production gone: one whose whole body is a single ungated, action-free call, with no
    continuation and no recovery of its own, is what it calls, so every reference to it becomes a reference to that
    callee — its parameters bound to the call's arguments. A chain of them collapses in one pass, and one that reaches
    only itself is left where it stands, a call that never returns being no simpler spelled inline.
    """
    passthroughs = {}
    for name, production in grammar.items():
        body = production.body
        if name in keep or not isinstance(body, ir.Choice) or len(body.alternatives) != 1:
            continue
        [way] = body.alternatives
        if way.gate.peek is not None or way.gate.guards or way.actions:
            continue
        if way.first is None or way.second is not None or way.recover is not None:
            continue
        passthroughs[name] = way.first
    if not passthroughs:
        return grammar, {}

    def resolved(reference):
        seen = set()
        while isinstance(reference, ir.Ref) and reference.name in passthroughs and reference.name not in seen:
            seen.add(reference.name)
            inner = passthroughs[reference.name]
            reference = _bound(inner, dict(zip(grammar[reference.name].params, reference.args)))
        return reference

    def rewrite(node):
        node = ir.rebuilt(node, rewrite)
        return resolved(node) if isinstance(node, ir.Ref) else node

    swept = {name: dataclasses.replace(p, body=rewrite(p.body)) for name, p in grammar.items()}
    return swept, {name: resolved(reference).name for name, reference in passthroughs.items()}


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
    what tracks a production by name follows its content to where it went. The productions that only call something else
    are spliced out, the ones that spell the very same thing are merged into one, and — last, so it sees what the other
    two strand — every production no parse can enter is purged. Splicing and merging feed each other, a merge making two
    productions the same call and a splice making two callers identical, so they run to a fixpoint. None of it changes
    what the grammar matches or emits: a spliced production ran no action and made no decision, and a merged production
    is the one kept, character for character.

    Only a merge is a rename. Two productions that behave alike are one thing under two names, so what tracked either
    tracks the one kept; a spliced production is not renamed but *consumed*, its callee one that already stood for
    itself and holds none of the spliced one's role. Following a splice would slide a point of interest off the wrapper
    it names and onto the callee — which is how a declaration meant for a prefix wrapper came to name the indent scan
    underneath it and dissolve it.
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
        spliced, _splices = _spliced(grammar, keep)
        swept, merges = _merged(spliced, keep)
        if swept == grammar:
            return purged(grammar), {gone: landed(gone) for gone in renames}
        renames.update(merges)
        grammar = swept


def stages(grammar):
    """
    The grammar after each step, as `(label, grammar)` pairs, opening with `("base", grammar)` — what `check_normalize`
    diffs the interpreter's token stream across, so a step that changes it is named. Returns the `Points` beside the
    pairs, so the analysis of the final grammar reads its committed holders from the same tracking the steps used. One
    `Namer` is threaded through the steps, so the helper productions they mint number `<base>_<N>` off a count shared
    across them. Each step's grammar is cleaned of what the step leaves behind — the do-nothing productions, the
    duplicates, and the ones the root no longer reaches — since a transformation that replaces a call site strands the
    callee, and a do-nothing or a duplicate the sweep leaves standing would hold the determinize meter above its honest
    floor. The base grammar is kept whole: it is the grammar as frozen at the completeness gate, cleaned by no step.

    Every step must change the grammar, and one that does not is a fault. A step earns its place by doing something: it
    goes idle when what it looks for has stopped reaching it — a shape an earlier step now spells differently, a
    declared site whose content moved — and that is a regression in the step before it, not a step to leave standing.
    The check reads the transform's own output, before the sweep, so a step is judged on what it did rather than on what
    the sweep did after it.
    """
    namer = Namer()
    namer.points.settle("base", grammar)
    result = [("base", grammar)]
    for step in STEPS:
        produced = step.transform(grammar, namer)
        if produced == grammar:
            raise AssertionError(f"the `{step.name}` step changed nothing — what it looks for no longer reaches it")
        grammar, renames = cleaned(produced)
        namer.points.follow(renames)
        namer.points.settle(step.name, grammar)
        result.append((step.name, grammar))
    return result, namer.points


def lower_char_sets(grammar, namer):
    """
    Rewrite every character set as one `CharSet`, the shape the parser asks its one question in.

    A set of characters is written many ways — a character, a range, a union of them, a base with exclusions — and all
    of them come to the same thing: the parser tests a key for one bit. Said once here, as the sorted disjoint intervals
    it denotes, there is one shape to convert and the codegen has no algebra left to walk.

    It also makes the spelling canonical, which is what lets the sweep do its own work: two productions denoting the
    same characters differently — a union written in either order — are structurally unequal and do not merge, the merge
    reading shape rather than extension. Said as intervals they are the same node and merge like anything else.

    A maximal one is taken, not every one inside it: the intervals of a union are its own, and nothing asks about them
    apart. A reference is left standing where a match takes it — a character set with a production of its own keeps it,
    and the reference is what the callers hold — so nothing is purged and no fixture is stranded. Inside a lookaround it
    is read through instead: what a peek holds is the question "is the character one of these", and a name is not a
    question the machine can put to the input.
    """

    def lowered(node):
        if isinstance(node, (ir.Look, ir.NegLook, ir.LookBehind)):
            asked = as_char_set(node.item, grammar)
            if isinstance(asked, ir.CharSet):
                return dataclasses.replace(node, item=asked)
        if isinstance(node, ir.Ref):
            return node  # its production is where the set is said, and this is the caller's hold on it
        said = as_char_set(node, grammar)
        return said if isinstance(said, ir.CharSet) else ir.rebuilt(node, lowered)

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _other_character_questions(grammar):
    """
    Every question about a character said as something other than a `CharSet` — the one shape the parser can be given.

    A question is about a character when what it matches is exactly one: a union, a difference, a raw character node,
    and the item a lookaround peeks. Whether the spans can be worked out is no part of the question — a node matching
    one character is a character set, and one this cannot reduce is worse than one it can, not excused, the reduction
    failing being how a set stops being sayable at all.

    A reference is a hold on the production where the set is said, so a match taking one is no fault; inside a
    lookaround it is a fault, what a peek holds being the question rather than the hold. A guard over several characters
    — an `(exclude)`, a difference of two multi-character productions, a lookahead for a comment — asks nothing about a
    character and is no business of this count.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.CharSet):
                return  # lowered
            if isinstance(node, (ir.Look, ir.NegLook, ir.LookBehind)) and is_one_char(node.item, grammar):
                if not isinstance(node.item, ir.CharSet):
                    kinds = (type(node).__name__, type(node.item).__name__)
                    faults.append(f"{owner}: a {kinds[0]} asks about a character as a {kinds[1]}")
                return
            if isinstance(node, ir.Ref):
                return  # a hold on the production where the set is
            if is_one_char(node, grammar):
                faults.append(f"{owner}: a {type(node).__name__} is a character set and is not a `CharSet`")
                return
            ir.rebuilt(node, lambda child: (walk(child, owner), child)[1])

        walk(production.body)
    return faults


ONLY_SETS_AND_LITERALS = Invariant("every-character-question-is-a-set-or-a-literal", _other_character_questions)


def is_one_char(node, grammar, seen=frozenset()):
    """
    Whether `node` matches exactly one character — a terminal char class. A `+`/`*` over one stays a single repeated
    char-set match (one SIMD call); over anything else it breaks into a sequence or a recursion. A `Char`, `Range` or
    `Invalid` is one; a `Diff` is one when its base is (the exclusions only narrow it); an `Alt` is one when every
    branch is (a union of char sets), so a lowered optional `x | <empty>` is not one; a `Ref` is one when its production
    is.
    """
    if isinstance(node, (ir.Char, ir.Range, ir.Invalid, ir.CharSet)):
        return True
    if isinstance(node, ir.Diff):
        return is_one_char(node.base, grammar, seen)
    if isinstance(node, ir.Alt):
        return bool(node.items) and all(is_one_char(item, grammar, seen) for item in node.items)  # empty: no match
    if isinstance(node, ir.Case):
        return all(is_one_char(branch.item, grammar, seen) for branch in node.branches)  # a context-picked class
    if isinstance(node, ir.Ref):
        return node.name in seen or is_one_char(grammar[node.name].body, grammar, seen | {node.name})
    return False


def as_char_set(node, grammar):
    """
    `node` said as a `CharSet` where it is a character set, and `node` itself where it is not.

    The one place that decides it, so the lowering step and everything that builds a gate afterwards agree. A reference
    is read through here rather than left standing: in a peek it is the question "is the character one of these", not
    the hold on a production a match needs, and an alternation of such references is one set like any other.
    """
    if isinstance(node, ir.LiteralPeek):
        return node  # several characters, of which only the first is the dispatch — no set says that
    if not is_one_char(node, grammar):
        return node
    spans = _peek_spans(node, grammar)
    return node if spans is None else _spans_node(spans)


def _spans_node(spans):
    """
    `spans` as the character set a gate peeks — the one shape a character question is asked in.

    The invalid byte's `(-1, -1)` is kept apart from the characters: it is a unit no character class also holds, so it
    overlaps only itself, and coalescing it with a run starting at zero would say the parser accepts a character there.
    """
    invalid = [span for span in spans if span[0] < 0]
    return ir.CharSet(
        tuple([(-1, -1)] * bool(invalid) + [tuple(span) for span in _merged_spans([s for s in spans if s[0] >= 0])])
    )


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
    if isinstance(peek, ir.CharSet):
        return [tuple(span) for span in peek.spans]
    if isinstance(peek, ir.Invalid):
        return [(-1, -1)]
    if isinstance(peek, ir.LiteralPeek):
        return [(peek.text[0], peek.text[0])]  # the first character is the dispatch; the rest is the gate's own test
    if isinstance(peek, ir.Alt):
        # Unioned here rather than denoted, since the invalid byte has no denotation: it is a unit no character holds,
        # and an alternation carrying one — as the `Invalid` node or as the interval a `CharSet` says it with — denotes
        # nothing while admitting perfectly well.
        gathered = []
        for item in peek.items:
            admitted = _peek_spans(item, grammar)
            if admitted is None:
                return None
            gathered += admitted
        invalid = [span for span in gathered if span[0] < 0]
        return [(-1, -1)] * bool(invalid) + _merged_spans([span for span in gathered if span[0] >= 0])
    denotation = chars.denote(grammar, peek)
    return None if denotation is None else _denoted_spans(denotation)


# No point of interest is tracked yet: a point is a name a later phase's declared step speaks about, and none of those
# has arrived.
POINTS = {}


def _held(node):
    """
    `node` and everything it holds, however it holds it — the fields walked themselves rather than through the generic
    walker, which carries a `Param` a field holds directly as a value and never visits it. A comparison's operands are
    where that hides: `s-indent-floor`'s `Le(f, n)` reads two parameters the walker sees neither of.
    """
    yield node
    if dataclasses.is_dataclass(node):
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            for item in value if isinstance(value, tuple) else (value,):
                yield from _held(item)


def _parameter_uses(grammar, wanted):
    """
    Every place a parameter in `wanted` is still carried: a production declaring one, a call passing one at the position
    the callee declares it, an expression reading one.

    A parameter is the grammar's one implicit thing — a read of `t` means nothing until a caller is known — so what a
    step past it may lean on is that nothing has to be resolved before the grammar can be read.
    """
    faults = []
    for name, production in grammar.items():
        faults += [f"{name}: declares `{param}`" for param in production.params if param in wanted]
        for node in _held(production.body):
            if isinstance(node, ir.Ref):
                callee = grammar.get(node.name)
                declared = () if callee is None else callee.params
                for position in range(min(len(node.args), len(declared))):
                    if declared[position] in wanted:
                        faults.append(f"{name}: passes `{declared[position]}` to `{node.name}`")
            elif isinstance(node, ir.Param) and node.name in wanted:
                faults.append(f"{name}: reads `{node.name}`")
    return faults


# Phase 0's invariant, and what the phase is finished by: the chomping is nowhere carried.
NO_T_PARAMETER = Invariant("no-t-parameter", lambda grammar: _parameter_uses(grammar, {"t"}))

# Phase 2's invariant: the block scalar's leading-empty floor is nowhere declared, passed or read as a parameter.
NO_F_PARAMETER = Invariant("no-f-parameter", lambda grammar: _parameter_uses(grammar, {"f"}))

# Phase 3's invariant: the detected indent is nowhere declared, passed or read as a parameter.
NO_M_PARAMETER = Invariant("no-m-parameter", lambda grammar: _parameter_uses(grammar, {"m"}))


def _replaced(node, swap):
    """
    `node` with `swap` applied to it and to everything it holds, the fields walked themselves.

    The generic walker carries a `Param` as a value and never visits one a field holds directly, which is where a read
    hides — so a rewrite of the reads is written against this rather than against it.
    """
    node = swap(node)
    if not dataclasses.is_dataclass(node):
        return node
    changed = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, tuple):
            items = tuple(_replaced(item, swap) for item in value)
            if items != value:
                changed[field.name] = items
        elif dataclasses.is_dataclass(value):
            held = _replaced(value, swap)
            if held is not value:
                changed[field.name] = held
    return dataclasses.replace(node, **changed) if changed else node


def _read_global(param):
    """
    A transform making `param` the parse's one value: the declaration off every production, the argument off every call,
    and every read of it a `Global`.

    What licenses it is that the value does not nest — the floor is measured by the leading empty lines of one block
    scalar and read by its first content line, one construct at a time — and the clear is what holds it to that, a read
    past the region refused rather than answered. The writes are left as they stand: a `(set)` and a `(clear)` name the
    parameter as a string, and reach the single slot once no production declares it.
    """

    def transform(grammar, namer):
        positions = {
            name: production.params.index(param) for name, production in grammar.items() if param in production.params
        }

        def swap(node):
            if isinstance(node, ir.Param) and node.name == param:
                return ir.Global(name=param)
            if isinstance(node, ir.Ref) and positions.get(node.name, len(node.args)) < len(node.args):
                position = positions[node.name]
                return dataclasses.replace(node, args=node.args[:position] + node.args[position + 1 :])
            return node

        return {
            name: dataclasses.replace(
                production,
                params=tuple(held for held in production.params if held != param),
                body=_replaced(production.body, swap),
            )
            for name, production in grammar.items()
        }

    return transform


# What runs its item more than once, so a read inside one is a read on every turn.
_REPETITIONS = (ir.Star, ir.Plus, ir.Rep, ir.TrimStar)


def pass_detected_indent(grammar, namer):
    """
    Give a loop the indentation it measured rather than working it out again on every turn.

    A block collection detects what its entries are indented by, once, and then measures every entry against `n+m` — so
    the detected value has to stay live for as long as the loop runs, and every collection or block scalar the loop
    enters detects one of its own in the meantime. The loop moves into a production entered at `n+m` and reads the
    indentation it was entered at, which leaves the detection read once, in the argument beside the write, with nothing
    run in between.

    Only where the reading is that simple: every read of the detection inside the moved body stands in one and the same
    expression, and nothing there reads the indentation any other way, so entering at that expression says exactly what
    the body said. A body reading it two ways — the compact collections' `m` beside their `n+1+m` — is left alone,
    having no loop to carry the value across either.
    """
    minted = {}

    def measured(production):
        """The `(set)` of the detection leading the body and what follows it, or `None` where the body is not that."""
        body = production.body
        if not isinstance(body, ir.Seq) or len(body.items) < 2:
            return None
        setter, rest = body.items[0], body.items[1:]
        if not isinstance(setter, ir.SetVar) or setter.param != "m":
            return None
        return setter, rest[0] if len(rest) == 1 else ir.Seq(items=rest)

    def entered_at(rest):
        """
        The one expression `rest` reads the detection in, with `rest` reading it as the indentation it is entered at —
        and `None` where `rest` reads either of them any other way.
        """
        held = {
            argument
            for node in _held(rest)
            if isinstance(node, ir.Ref)
            for argument in node.args
            if _is_using(argument, "m")
        }
        if len(held) != 1:
            return None
        level = next(iter(held))

        def carried(node):
            """How many reads of the indentation and of the detection `node` holds."""
            return len([held for held in _held(node) if isinstance(held, ir.Param) and held.name in ("m", "n")])

        standing = len([node for node in _held(rest) if node == level])
        if carried(rest) != standing * carried(level):  # a read outside the expression the body is entered at
            return None
        return level, _replaced(rest, lambda node: ir.Param(name="n") if node == level else node)

    result = {}
    for name, production in grammar.items():
        found = measured(production)
        entered = entered_at(found[1]) if found is not None else None
        if entered is None:
            result[name] = production
            continue
        level, remains = entered
        helper = namer.fresh(name)
        minted[helper] = ir.Prod(number=production.number, name=helper, params=("n",), body=remains)
        result[name] = dataclasses.replace(
            production, body=ir.Seq(items=(found[0], ir.Ref(name=helper, args=(level,))))
        )
    result.update(minted)
    return result


def _looped_detections(grammar):
    """
    Reads of the detected indent standing under a repetition — the value asked for again on every turn of a loop.

    A read under one is what makes the detection live for as long as the loop runs, and everything the loop enters
    detects its own in the meantime. Read where it is measured it is a value one place can hold; read per turn it is
    not.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name, repeated=False):
            if isinstance(node, ir.Param):
                if repeated and node.name == "m":
                    faults.append(f"{owner}: reads the detected indent on every turn of a loop")
                return
            if not dataclasses.is_dataclass(node):
                return
            inside = repeated or isinstance(node, _REPETITIONS)
            for field in dataclasses.fields(node):
                value = getattr(node, field.name)
                for item in value if isinstance(value, tuple) else (value,):
                    walk(item, owner, inside)

        walk(production.body)
    return faults


DETECTION_READ_WHERE_MEASURED = Invariant("no-detected-indent-read-per-turn", _looped_detections)


def _is_reading(grammar, production, param):
    """
    Whether `production` reads `param`: a write says where a value begins and a pass carries it, and neither is a read.

    A parameter passed as itself is by reference — the value on its way to the construct that measures it, or to the one
    that asks about it — so the `Param` standing in that argument is the pass and not a use of the value here. One
    inside an argument that works something out is a read like any other, the caller being where it is evaluated.
    """
    passed = set()
    for node in _held(production.body):
        if not isinstance(node, ir.Ref):
            continue
        callee = grammar.get(node.name)
        declared = () if callee is None else callee.params
        for position in range(min(len(node.args), len(declared))):
            argument = node.args[position]
            if declared[position] == param and isinstance(argument, ir.Param) and argument.name == param:
                passed.add(id(argument))
    return any(
        isinstance(node, ir.Param) and node.name == param and id(node) not in passed for node in _held(production.body)
    )


def _is_bounded(production, param):
    """Whether `production` says where `param` stops applying: its way ends on the clear."""
    body = production.body
    items = body.items if isinstance(body, ir.Seq) else (body,)
    return bool(items) and isinstance(items[-1], ir.ClearVar) and items[-1].param == param


def _clear_reads(param):
    """
    A transform giving `param` an end: the production that reads it clears it where it returns, behind its calls.

    The reader and not the writer. A value is measured by the construct that opens — a block scalar's floor deep inside
    its leading empties — and handed up to the one that asked for it, so clearing where it was written takes it from the
    only thing that wanted it. The way holding the read takes the value, uses it, and by the time it comes back nothing
    else wants it, which is what makes the value one region long and lets a single slot answer for it.
    """

    def transform(grammar, namer):
        def bounded(production):
            if not _is_reading(grammar, production, param) or _is_bounded(production, param):
                return production
            body = production.body
            items = body.items if isinstance(body, ir.Seq) else (body,)
            return dataclasses.replace(production, body=ir.Seq(items=items + (ir.ClearVar(param=param),)))

        return {name: bounded(production) for name, production in grammar.items()}

    return transform


def _unbounded_reads(param):
    """
    The invariant that `param` has an end: a production reading it and not saying where the value stops.

    Past its end a read is refused rather than answered from what the last construct left, which is what holds a value
    to one region — and what a single slot standing for the parameter needs, a stale answer being indistinguishable from
    a live one.
    """

    def test(grammar):
        return [
            f"{name}: reads `{param}` and does not say where the value stops"
            for name, production in grammar.items()
            if _is_reading(grammar, production, param) and not _is_bounded(production, param)
        ]

    return Invariant(f"every-read-of-{param}-is-bounded", test)


STEPS = [
    # Phase 0 establishes `NO_T_PARAMETER`: nothing declares, passes or reads the chomping. The chomping is
    # data-dependent until this runs, so it cannot be specialized: the setter becomes a switch first.
    Step("lift-chomping", lift_chomping, CHOMPING_LEXICAL, reduces=("chomping-is-lexical",)),
    Step(
        "monomorphize", monomorphize, (_absent("no-context-case", ir.Case, ir.Flip), CHOMPING_LEXICAL, NO_T_PARAMETER)
    ),
    # Phase 1 establishes `ONLY_SETS_AND_LITERALS`: a question about a character is a `CharSet`. A set the context picks
    # denotes nothing until the specialization has bound the context, so this follows Phase 0.
    Step("lower-char-sets", lower_char_sets, ONLY_SETS_AND_LITERALS),
    # Phase 2 establishes `NO_F_PARAMETER`: the block scalar's leading-empty floor is the parse's one value rather than
    # one a call carries. The value is given an end first, a single slot answering for a parameter only where a read
    # past the region it was measured in is refused rather than answered from what the last construct left.
    Step("clear-f", _clear_reads("f"), _unbounded_reads("f")),
    Step("read-global-f", _read_global("f"), NO_F_PARAMETER),
    # Phase 3 establishes `NO_M_PARAMETER`: the detected indent is the parse's one value. A value one place can hold is
    # one nothing else writes while it is live, so the loop that read it per turn is entered at what it measured first.
    Step("pass-detected-indent", pass_detected_indent, DETECTION_READ_WHERE_MEASURED),
    Step("clear-m", _clear_reads("m"), _unbounded_reads("m")),
    Step("read-global-m", _read_global("m"), NO_M_PARAMETER),
]
