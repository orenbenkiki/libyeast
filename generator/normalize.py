# SPDX-License-Identifier: MIT
"""
Normalize the grammar, one goal at a time.

The pipeline is a sequence of phases, each owning one invariant: a phase adds steps until its count is none, and from
its end the invariant is enforced — the law's "none stays none" makes every later step keep it. A phase finished with a
green corpus is a checkpoint worth landing on its own.

Phase 0 is the two parameters the grammar sets by matching: the chomping `t` and the block scalar's indentation mode
`i`. Each is set by an indicator and read productions later through the environment, so a read of either means nothing
until a caller is known. `lift-setters` inverts both setters into switches and `monomorphize` specializes those into the
names, after which nothing declares, passes or reads either — `no-i-t-parameters` at none.

Phase 1 is the character questions. A set of characters is written many ways and asked in several — a union, a
difference, a reference, the item of a lookaround — and the parser tests one key for one bit. `lower-char-sets` says
every one of them as the intervals it denotes, after which each question about a character is a `CharSet` or a literal —
`every-character-question-is-a-set-or-a-literal` at none. It follows the specialization because a set a context
parameter picks denotes nothing until a caller is known.

Phases 2 to 4 are the values a call carries. The block scalar's leading-empty floor `f` and the detected indent `m` are
one value for the parse rather than one per call, so each is given an end and then read off a single slot; the
indentation `n` is one value per region, so it goes on the parse's own stack — `hold-established-indents` makes the one
indentation a call hands back into a push, `push-indents` says where every other change is, and `read-indents` takes the
parameter away, leaving the stack the one place it is. Nothing declares, passes or reads any of the three.

Phase 5 is the empties. A caller choosing whether to enter a production that may match nothing is choosing blind, and
the choice cannot be put on a character while both answers live under one name. `lower-optionals` brings the empty match
an optional hides out beside the way that reads, `span-consumes` takes the character runs out of the question by writing
each as the scan it is, `lower-runs` says the two repetitions as the one `LongestRun` they are,
`mint-consuming-and-residue` gives every production that may match empty a name for each of the two things it is,
`distribute-residues` writes the choice between the two where the caller stands rather than behind the one name, and
`dissolve-residues` writes what is left taking no character into the call sites that enter it. Nothing a caller chooses
to enter can match empty — `only-root-empties` at none, the root and the recovery keeping their empty ways, having no
call site to hold the choice.

Phase 6 is the wrappers. A scope that holds what it covers has nowhere to stand in an alternative, which has a place for
an action and none for a node enclosing a call, so each becomes the pair that brackets it: `lower-wraps` writes a
`(wrap)` as its two markers, `lower-windows` a `(max)` as the window pair, `lower-commits` a `(commit)` as the message
pair, and `lower-tokens` a `(token)` as the code pair. What a wrapper guaranteed by construction the pairs are held to
instead: `every-scope-closes-on-its-own-way` says a scope opened on a way is closed on it, the ways of a choice agree on
what they leave open, and a run's turn leaves none. A `(recover)` is a handler rather than a scope and stays, its home
being the edge an alternative rides.
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
        does_want_points = len(inspect.signature(self.test).parameters) > 1
        return self.test(grammar, points) if does_want_points else self.test(grammar)


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


def lift_setters(grammar, namer):
    """
    Make every data-dependent finite parameter lexical, so each monomorphizes like the context.

    Two are: the chomping `t`, which `c-chomping-indicator` sets by matching an indicator and the block scalar reads two
    productions later through the env, and the indentation mode `i`, which `c-indentation-indicator` sets by whether
    there was a digit and the scalar's first content line reads. A set is not a switch, so neither can be specialized.
    This inverts each setter into a `(case)` on its parameter that matches the condition for a given value, and turns
    each production holding one as a local out-parameter into an ordered choice over its values — every branch fixing
    the parameter to a literal it hands the setter and the reader alike. The parse tries the values in the setter's
    order, so exactly the one whose condition holds matches, and the value flows as a value rather than stashed state.
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
    did_change = True
    while did_change:
        did_change = False
        for name in grammar:
            for callee, passed in calls[name]:
                inherited = relevant[callee] - passed
                if inherited - relevant[name]:
                    relevant[name] |= inherited
                    did_change = True
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
    collected by name rather than by how many steps mention it. `invariants` and `reduces` are both given the invariant
    itself for that reason: the name is what they are compared by, and a name written out here instead would be a second
    spelling of it that nothing resolves — a misspelt one would read as a step that reduces nothing, which is to say as
    one that settles what it does not.

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
        for field in ("invariants", "reduces"):
            named = getattr(self, field)
            held = (named,) if isinstance(named, Invariant) else tuple(named)
            object.__setattr__(self, field, held)

    def does_settle(self, invariant):
        """Whether this step is the one that takes `invariant`'s count to none."""
        return invariant.name in self.carries and invariant.name not in {held.name for held in self.reduces}

    @property
    def carries(self):
        """The names of the invariants this step is about — what it settles and what it only lowers alike."""
        return {held.name for held in self.invariants}


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


def _computed_finite(grammar):
    """
    Calls handing a set-by-matching finite parameter a value worked out rather than written — one the specialization
    cannot pick a copy for.

    A finite parameter specializes away only where every call names one of its values outright: a copy per value is
    made, and a call carrying an expression has no copy to go to. The chomping and the indentation mode are the two the
    grammar sets by matching; made lexical, each is what the context already is.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.Ref):
                callee = grammar.get(node.name)
                for param in ("t", "i"):
                    if callee is None or param not in callee.params:
                        continue
                    position = callee.params.index(param)
                    if position < len(node.args) and not isinstance(node.args[position], ir.Lit):
                        faults.append(f"{owner}: hands `{node.name}` a `{param}` it works out")
            ir.rebuilt(node, lambda child: (walk(child, owner), child)[1])

        walk(production.body)
    return faults


FINITE_LEXICAL = Invariant("every-set-finite-parameter-is-lexical", _computed_finite)


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
        for held in step.reduces:
            if held.name not in step.carries:
                faults.append(f"[{step.name}] says it only lowers `{held.name}`, which it does not carry at all")
    for named in sorted(by_name):
        test = by_name[named]
        first = min(index for index, step in enumerate(STEPS) if named in step.carries)
        is_settled, standing = False, None
        for index in range(first, len(STEPS)):
            step, (label, grammar) = STEPS[index], stages[index + 1]
            count = len(test(grammar, points))
            is_licensed = named in step.lapses
            is_broken = (
                (standing is not None and count > standing)
                or (is_settled and count)
                or (step.does_settle(test) and count)
            )
            if is_broken and is_licensed:
                taken.add((step.name, named))
            if standing is not None and count > standing and not is_licensed:
                faults.append(f"[{label}] `{named}` rises from {standing} to {count}, and the step declares no lapse")
            if is_settled and count and not is_licensed:
                faults.append(f"[{label}] `{named}` is settled and stands at {count}, and the step declares no lapse")
            if step.does_settle(test) and count and not is_licensed:
                faults.append(f"[{label}] settles `{named}` and leaves {count} standing")
            is_settled = (is_settled or step.does_settle(test)) and not count
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


def _flattened(node):
    """
    `node` with what a transformation leaves in its shape taken out: a sequence or a choice of one item is that item, a
    nested one of the same kind is its items in place, and an `<empty>` in a sequence goes, matching where it stood and
    moving nothing.

    None of it changes what the grammar matches or emits — `<empty>` is the continuation itself, and concatenation and
    ordered choice are each associative — which is what makes it the sweep's business rather than a step's. A step is
    not asked to tidy the shape it built, any more than it is asked not to leave a duplicate production behind.

    A choice of *nothing* is not litter and stays: it is the path that never matches, which is what a specialization
    leaves where a value has no branch.
    """
    node = ir.rebuilt(node, _flattened)
    if isinstance(node, ir.Seq):
        items = tuple(
            held
            for item in node.items
            for held in (item.items if isinstance(item, ir.Seq) else (item,))
            if not isinstance(held, ir.Empty)
        )
        if not items:
            return ir.Empty()
        return items[0] if len(items) == 1 else ir.Seq(items=items)
    if isinstance(node, ir.Alt) and node.items:
        items = tuple(held for item in node.items for held in (item.items if isinstance(item, ir.Alt) else (item,)))
        return items[0] if len(items) == 1 else ir.Alt(items=items)
    return node


def cleaned(grammar):
    """
    `grammar` with what a transformation leaves behind swept up, and the renames the sweep made — `{gone: standing}`, so
    what tracks a production by name follows its content to where it went. Every body is flattened to the shape it
    denotes, the productions that only call something else are spliced out, the ones that spell the very same thing are
    merged into one, and — last, so it sees what the other two strand — every production no parse can enter is purged.
    Splicing and merging feed each other, a merge making two productions the same call and a splice making two callers
    identical, so they run to a fixpoint. None of it changes what the grammar matches or emits: a flattened body denotes
    what it denoted, a spliced production ran no action and made no decision, and a merged production is the one kept,
    character for character.

    The flattening comes first because the merge reads shape rather than meaning: two productions that say the same
    thing with a singleton alternation in different places are structurally unequal and do not merge, where said flat
    they are the same node. So it is where the sweep's own work is decided, and a merge it makes possible is a duplicate
    the sweep existed to find rather than one it invented.

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
        flat = {name: dataclasses.replace(p, body=_flattened(p.body)) for name, p in grammar.items()}
        spliced, _splices = _spliced(flat, keep)
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


def _wide_differences(grammar):
    """
    Differences taking characters from something that is not a character set — the shape a set subtraction cannot say.

    A `(---)` denotes a set only where both sides are sets. Where its base is a choice holding a match of several
    characters — an escape, which is what the double-quoted, single-quoted and tag characters subtract from — the
    subtraction is a filter over a language instead, and nothing downstream can intersect it with a gate or hand it to
    the parser as one bit.

    The two sides are asked different questions, each the one its half of the lowering answers: a base is a set where it
    matches one character, that being what the reduction folds, and a subtracted side is one where its characters are
    pinned, that being what the reduction reads. An annotation around a subtracted character changes neither — the
    difference reads the text a match takes and not the code it carries — which is `nb-char` taking the byte-order mark
    out of the printable characters.
    """
    return [
        f"{name}: a `(---)` takes characters from something that is not a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Diff)
        and not (
            ir.is_one_char(node.base, grammar) and all(_peek_spans(taken, grammar) is not None for taken in node.minus)
        )
    ]


DIFFERENCES_BETWEEN_SETS = Invariant("every-difference-is-between-character-sets", _wide_differences)

# What a shortest match is counted up to. A difference takes a set of single characters, so it can remove only a match
# of one character: past one, how much more a way takes makes no difference to what the subtraction reaches.
_SHORTEST_CAP = 2


def _shortest_match(node, grammar, seen=frozenset()):
    """
    The fewest characters `node` can match, counted no further than `_SHORTEST_CAP`.

    A lower bound is what the count is for, so a difference contributes its base's — the exclusions only remove matches
    — and a production reached again contributes the cap, a match that bottoms out never being the recursive way. Every
    kind is named and one named nowhere raises: a kind answered for by accident would say a way takes two characters
    where it can take one, and the subtraction would come off a way it reaches.
    """
    if isinstance(node, (ir.Char, ir.CharSet, ir.Invalid, ir.Range)):
        return 1
    if isinstance(node, ir.Diff):
        return _shortest_match(node.base, grammar, seen)
    if isinstance(node, (*_ACTIONS, *_GUARDS, ir.Empty, ir.Star, ir.Opt)):
        return 0  # an action or a guard takes nothing, and a repetition of none or more takes no turn
    if isinstance(node, ir.Seq):
        return min(_SHORTEST_CAP, sum(_shortest_match(item, grammar, seen) for item in node.items))
    if isinstance(node, (ir.Alt, ir.Case)):
        ways = (
            node.items
            if isinstance(node, ir.Alt)
            else tuple(branch.item for branch in node.branches) + ((node.default,) if node.default is not None else ())
        )
        return min((_shortest_match(way, grammar, seen) for way in ways), default=_SHORTEST_CAP)
    if isinstance(node, ir.Plus):
        return _shortest_match(node.item, grammar, seen)
    if isinstance(node, ir.LongestRun):
        return _shortest_match(node.item, grammar, seen) if node.least else 0
    if isinstance(node, (ir.Rep, ir.ConsumeCountedSpan)):
        # A count the parse works out may be none at all, and then the repetition takes nothing.
        taken = node.item if isinstance(node, ir.Rep) else node.set
        if not isinstance(node.count, ir.Lit) or node.count.value <= 0:
            return 0
        return min(_SHORTEST_CAP, node.count.value * _shortest_match(taken, grammar, seen))
    if isinstance(node, ir.Bind):
        return _shortest_match(node.cond, grammar, seen)
    if isinstance(node, (ir.Token, ir.Wrap, ir.Max, ir.Commit, ir.Recover)):
        return 0 if node.item is None else _shortest_match(node.item, grammar, seen)
    if isinstance(node, ir.ConsumeSpan):
        return 0  # a span of none or more, its least turn taking nothing
    if isinstance(node, ir.Ref):
        if node.name in seen:
            return _SHORTEST_CAP
        return _shortest_match(grammar[node.name].body, grammar, seen | {node.name})
    raise TypeError(f"cannot tell how few characters {type(node).__name__} can match")


def _difference_ways(base, grammar):
    """
    The ways `base` offers, in order — the items of a choice, read through the reference that names it.

    A difference's base is a name at every site, so the ways are the callee's; the production it names stays for its own
    callers. A call passing arguments is refused rather than spliced without them.
    """
    if isinstance(base, ir.Ref):
        if base.args:
            raise ValueError(f"a `(---)` takes characters from `{base.name}`, which is passed arguments")
        return _difference_ways(grammar[base.name].body, grammar)
    return base.items if isinstance(base, ir.Alt) else (base,)


def _taken(ways, minus):
    """
    `ways` — a run of one-character ways — as the one difference their union stands in, and nothing where it is empty.
    """
    if not ways:
        return ()
    return (ir.Diff(base=ways[0] if len(ways) == 1 else ir.Alt(items=tuple(ways)), minus=minus),)


def distribute_differences(grammar, namer):
    """
    Take a difference into the ways of what it subtracts from, so that what is left of it stands between two sets.

    A difference over a choice is the choice of the differences, and the ways keep their order: `(A | B) - m` is `(A -
    m) | (B - m)`, and a run of ways that are each one character takes the subtraction once, as the union they already
    are. What that leaves is a difference of two character sets wherever the subtraction reaches anything.

    A way that takes two characters or more keeps its whole language, since a subtracted set takes one character and can
    remove only a match of one. That is the whole of the step: `nb-double-char` is an escape or a character, and
    `ns-double-char` subtracts the whitespace only the second of them can be.

    A way that may take one character without being a set is refused rather than guessed at — the subtraction reaches it
    and no set says how — as is a subtraction of anything but single characters.
    """

    def distributed(node):
        node = ir.rebuilt(node, distributed)
        if not isinstance(node, ir.Diff) or ir.is_one_char(node.base, grammar):
            return node
        if any(_peek_spans(taken, grammar) is None for taken in node.minus):
            raise ValueError("a `(---)` subtracts something that is not a character set")
        ways, run = [], []
        for way in _difference_ways(node.base, grammar):
            if ir.is_one_char(way, grammar):
                run.append(way)
                continue
            if _shortest_match(way, grammar) < _SHORTEST_CAP:
                raise ValueError("a `(---)` reaches a way that may take one character and is not a set")
            ways.extend(_taken(run, node.minus))
            ways.append(way)
            run = []
        ways.extend(_taken(run, node.minus))
        return ir.Alt(items=tuple(ways)) if len(ways) != 1 else ways[0]

    return {
        name: dataclasses.replace(production, body=distributed(production.body)) for name, production in grammar.items()
    }


# A subtraction between two sets is a set, so once every difference stands between two the lowering says them all as one
# `CharSet` and the notation is gone: what the parser is given is a bit to test, never an algebra to walk.
NO_DIFF_NODES = _absent("no-diff-nodes", ir.Diff)

# What a peek holds that shapes the output rather than the question: the run's code and the markers. A lookaround is
# probed and given back, so none of it reaches the stream and none of it is part of what the peek asks.
_PEEK_OUTPUT = (ir.Emit, ir.PopCode, ir.PushCode, ir.Token, ir.Wrap)


def _wide_peeks(grammar):
    """
    Lookarounds holding something other than a character set — a question the machine cannot put to one character.

    The twin of `no-diff-nodes` for the peeks, and it answers for what the character count cannot: that one judges a
    peek whose question is a character and says nothing about one whose question is wider, where this refuses every
    shape but the set. It is what holds the steps that mint peeks — a possessive scan's empty way is the negative peek
    of its own set — to minting them as sets, 39 of the 64 in the final grammar being theirs rather than the grammar's.

    An `(exclude)` is no peek of this kind and is no business of this count: it asks about a line rather than about a
    character, and where it goes is the phase that makes a line start a decision.
    """
    return [
        f"{name}: a {type(node).__name__} holds a {type(node.item).__name__} rather than a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, (ir.Look, ir.NegLook, ir.LookBehind)) and not isinstance(node.item, ir.CharSet)
    ]


PEEKS_ARE_SETS = Invariant("every-peek-is-a-character-set", _wide_peeks)


def _peeked_question(node, grammar):
    """
    What a peek of `node` asks, with what only shapes the output read off it — the question the machine puts to the
    input.

    A probe emits nothing and gives back what it read, so an annotation inside one is dead: `c-comment` is a `#` under
    the code its character carries, and peeking it asks whether the character is a `#`. A name is read through for the
    same reason a match's is not — the caller's hold on a production is what a match wants and a peek has no use for.
    """
    if isinstance(node, ir.Ref) and not node.args:
        return _peeked_question(grammar[node.name].body, grammar)
    if isinstance(node, (ir.Token, ir.Wrap)):
        return _peeked_question(node.item, grammar)
    if isinstance(node, ir.Seq):
        asked = [item for item in node.items if not isinstance(item, _PEEK_OUTPUT)]
        if len(asked) == 1:
            return _peeked_question(asked[0], grammar)
    return node


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
    is read through instead, along with any annotation on what it names: what a peek holds is the question "is the
    character one of these", and neither a name nor a code is a question the machine can put to the input.
    """

    def lowered(node):
        if isinstance(node, (ir.Look, ir.NegLook, ir.LookBehind)):
            asked = as_char_set(_peeked_question(node.item, grammar), grammar)
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
    lookaround it is a fault, what a peek holds being the question rather than the hold. A peek is judged on the
    question it asks rather than on the shape it names it with — an annotation inside one is dead, a probe emitting
    nothing — so peeking a `#` under the code its character carries is a character question like any other. A guard over
    several characters — an `(exclude)`, a difference of two multi-character productions — asks nothing about a
    character and is no business of this count.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.CharSet):
                return  # lowered
            if isinstance(node, (ir.Look, ir.NegLook, ir.LookBehind)) and ir.is_one_char(
                _peeked_question(node.item, grammar), grammar
            ):
                if not isinstance(node.item, ir.CharSet):
                    kinds = (type(node).__name__, type(node.item).__name__)
                    faults.append(f"{owner}: a {kinds[0]} asks about a character as a {kinds[1]}")
                return
            if isinstance(node, ir.Ref):
                return  # a hold on the production where the set is
            if ir.is_one_char(node, grammar):
                faults.append(f"{owner}: a {type(node).__name__} is a character set and is not a `CharSet`")
                return
            ir.rebuilt(node, lambda child: (walk(child, owner), child)[1])

        walk(production.body)
    return faults


ONLY_SETS_AND_LITERALS = Invariant("every-character-question-is-a-set-or-a-literal", _other_character_questions)


def as_char_set(node, grammar):
    """
    `node` said as a `CharSet` where it is a character set, and `node` itself where it is not.

    The one place that decides it, so the lowering step and everything that builds a gate afterwards agree. A reference
    is read through here rather than left standing: in a peek it is the question "is the character one of these", not
    the hold on a production a match needs, and an alternation of such references is one set like any other.
    """
    if isinstance(node, ir.LiteralPeek):
        return node  # several characters, of which only the first is the dispatch — no set says that
    if not ir.is_one_char(node, grammar):
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


# Phase 0's invariant, and what the phase is finished by: neither parameter the grammar sets by matching is carried —
# the chomping `t` and the block scalar's indentation mode `i`. Both are read productions away from where they are set,
# so a read of either means nothing until a caller is known, and both are settled by the same specialization.
NO_I_T_PARAMETERS = Invariant("no-i-t-parameters", lambda grammar: _parameter_uses(grammar, {"i", "t"}))

# Phase 2's invariant: the block scalar's leading-empty floor is nowhere declared, passed or read as a parameter.
NO_F_PARAMETER = Invariant("no-f-parameter", lambda grammar: _parameter_uses(grammar, {"f"}))

# Phase 3's invariant: the detected indent is nowhere declared, passed or read as a parameter.
NO_M_PARAMETER = Invariant("no-m-parameter", lambda grammar: _parameter_uses(grammar, {"m"}))

# Phase 4's invariant: the indentation is nowhere declared, passed or read as a parameter, the parse's stack holding it.
NO_N_PARAMETER = Invariant("no-n-parameter", lambda grammar: _parameter_uses(grammar, {"n"}))


def _unpushed_indents(grammar):
    """
    Indentations a parse changes without saying so: a call measured against one the parse does not stand under, and a
    call that establishes one whose region nothing bounds.

    A push and its pop are what put a level where every read reaches it without a call carrying it, and they are looked
    for immediately around the call — the one place the level is known and nothing of the caller's runs between.
    """
    faults = []
    establishing = _establishing(grammar)
    for name, production in grammar.items():
        guarded = set()
        for node in _held(production.body):
            if not isinstance(node, ir.Seq):
                continue
            for position, item in enumerate(node.items):
                before = node.items[position - 1] if position else None
                after = node.items[position + 1] if position + 1 < len(node.items) else None
                if isinstance(before, ir.PushIndent) and isinstance(after, ir.PopIndent):
                    guarded.add(id(item))  # entered under a level this way pushes and takes back
                if isinstance(after, ir.PushIndent) and isinstance(node.items[-1], ir.PopIndent):
                    guarded.add(id(item))  # establishes a level, pushed where it returns and held to the way's end
        for node in _held(production.body):
            if id(node) in guarded or not isinstance(node, ir.Ref):
                continue
            if _pushed_level(grammar, node) is not None:
                faults.append(f"{name}: calls `{node.name}` against an indentation nothing pushes")
        for node in _held(production.body):
            if not isinstance(node, ir.Seq):
                continue
            for position, item in enumerate(node.items):
                if id(item) in guarded or not isinstance(item, ir.Ref):
                    continue
                if item.name not in establishing or not _is_by_reference(grammar, item):
                    continue
                if any(_is_using(later, "n") for later in node.items[position + 1 :]):
                    faults.append(f"{name}: reads the indentation `{item.name}` establishes, and nothing holds it")
    return faults


INDENTS_PUSHED = Invariant("every-indentation-change-is-pushed", _unpushed_indents)


# The scopes a pair opens and closes: an action, and the one that takes it back. In alphabetical order by the opening
# action.
_SCOPES = (
    (ir.OpenWindow, ir.CloseWindow),
    (ir.PushCode, ir.PopCode),
    (ir.PushIndent, ir.PopIndent),
    (ir.PushMessage, ir.PopMessage),
)


def _scope_walk(items, owner, signature, faults):
    """
    What one way does to the stack, as `(taken, left)` — the scopes it takes off that it did not open, and the ones it
    opens and leaves standing for whatever the parse does next.

    A way's calls are not alike. The one it carries on at — the last, a way returning exactly where it does — is the
    rest of the same path, so what it takes off is what this way left open and what it leaves is left for the caller.
    Any call before it is one the way comes back from, so it must come back level: what it opened it closed, since the
    continuation waiting behind it is this way's and not the callee's.
    """
    taken, left = [], []
    calls = [item for item in items if isinstance(item, ir.Ref)]
    carries_on = calls[-1] if calls else None

    def close(kind):
        if not left:
            taken.append(kind)  # what the parse that reached here left open, which this takes off
        elif left[-1] is kind:
            left.pop()
        else:
            faults.append(f"{owner}: closes {kind.__name__} where {left[-1].__name__} is what stands open")
            left.pop()

    for item in items:
        for opening, closing in _SCOPES:
            if isinstance(item, opening):
                left.append(opening)
            elif isinstance(item, closing):
                close(opening)
        if isinstance(item, ir.Ref):
            called_taken, called_left = signature[item.name]
            if item is not carries_on:
                if (called_taken, called_left) != ((), ()):
                    faults.append(f"{owner}: comes back from {item.name}, which does not come back level")
                continue
            for kind in called_taken:
                close(kind)
            left.extend(called_left)
    return tuple(taken), tuple(left)


def _scope_signature(grammar):
    """
    What each production does to the stack, as `{name: (taken, left)}`.

    A least fixpoint, since what a way leaves includes what it carries on at: every production starts at "nothing either
    way" and the walk runs until the answers stop moving, so a continuation that reaches itself settles at nothing. It
    moves along the carrying-on calls alone — a call a way comes back from is held to level rather than followed — which
    is what makes it settle at all: read through both, a pair of productions calling each other has no least answer and
    the rounds swap two of them for ever.
    """
    signature = {name: ((), ()) for name in grammar}
    for _round in range(len(grammar) + 1):
        did_move = False
        for name, production in grammar.items():
            answers = _scope_answers(grammar, name, production, signature, [])
            answer = answers[0] if answers else ((), ())
            if signature[name] != answer:
                signature[name], did_move = answer, True
        if not did_move:
            return signature
    raise AssertionError("what the productions leave on the stack never settled")


def _scope_answers(grammar, name, production, signature, faults):
    """
    What each way of `production` does to the stack. A recovery answers for none of it: it runs where the abandoned
    parse stopped, with what that parse left open already put back.
    """
    body = production.body
    opened = _way_items(body.item if isinstance(body, ir.Recover) else body)
    return [_scope_walk(items, name, signature, faults) for items in opened]


def _unclosed_scopes(grammar):
    """
    Scopes no path closes, and closes with nothing standing open to take.

    What a wrapper guarantees by holding what it covers, a pair has to be held to instead: `ir.Wrap` is a node rather
    than the two markers it stands for precisely so a `begin` cannot lose its `end`. The pair is held to closing on the
    *path* rather than on the way, a way that hands control on being half of one — a `PushCode` before the call and its
    `PopCode` in the continuation are the same pair, meeting on the parse's own stack, which is what phase 6 moved them
    there for.

    What is refused: a call a way comes back from that does not come back level, a run whose turn leaves a scope open
    that another turn would open again, ways of one choice that leave different scopes open where a caller cannot tell
    which was taken, a close of a scope other than the one standing open, and a production a parse enters by name that
    leaves one open or takes one off that nothing opened.
    """
    signature = _scope_signature(grammar)
    faults = []
    for name, production in grammar.items():
        answers = _scope_answers(grammar, name, production, signature, faults)
        if len(set(answers)) > 1:
            faults.append(f"{name}: ways that leave different scopes open, and a caller cannot tell which")
        if isinstance(production.body, ir.LongestRun) and answers and answers[0] != ((), ()):
            faults.append(f"{name}: a run whose turn leaves a scope open, which another turn would open again")
    for name in entered_by_name(grammar):
        taken, left = signature[name]
        faults += [f"{name}: opens {kind.__name__} and no path closes it" for kind in left]
        faults += [f"{name}: closes {kind.__name__} where nothing opened one" for kind in taken]
    return faults


SCOPES_CLOSED = Invariant("every-scope-closes-on-the-path-that-opens-it", _unclosed_scopes)


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


def _read_off(param, held):
    """
    A transform taking `param` off the calls: the declaration off every production, the argument off every call, and
    every read of it `held` — the one place the value is now, a global's single slot or the parse's own stack.

    What licenses a global is that its value does not nest — the floor is measured by the leading empty lines of one
    block scalar and read by its first content line, one construct at a time — and the clear is what holds it to that, a
    read past the region refused rather than answered. What licenses the stack is that every read of the parameter has
    been compared against it, over the whole corpus, while the two stood side by side. The writes are left as they
    stand: a `(set)` and a `(clear)` name the parameter as a string, and reach the slot once no production declares it.
    """

    def transform(grammar, namer):
        positions = {
            name: production.params.index(param) for name, production in grammar.items() if param in production.params
        }

        def swap(node):
            if isinstance(node, ir.Param) and node.name == param:
                return held
            if isinstance(node, ir.Ref) and positions.get(node.name, len(node.args)) < len(node.args):
                position = positions[node.name]
                return dataclasses.replace(node, args=node.args[:position] + node.args[position + 1 :])
            return node

        return {
            name: dataclasses.replace(
                production,
                params=tuple(carried for carried in production.params if carried != param),
                body=_replaced(production.body, swap),
            )
            for name, production in grammar.items()
        }

    return transform


def _pushed_level(grammar, node):
    """
    The indentation `node` is measured against where that is not the one in force, and `None` where it is.

    A call handing the parameter itself passes the indentation already standing, so nothing changes and nothing is
    pushed; a call handing anything else — a sum, a column, a literal — is entered under one of its own.
    """
    if not isinstance(node, ir.Ref):
        return None
    callee = grammar.get(node.name)
    if callee is None or "n" not in callee.params:
        return None
    position = callee.params.index("n")
    if position >= len(node.args):
        return None
    level = node.args[position]
    return None if isinstance(level, ir.Param) and level.name == "n" else level


def span_consumes(grammar, namer):
    """
    Write a run over a character class as the one scan it is: `x*` becomes a `ConsumeSpan` and `x+` the character and
    the span behind it.

    What such a run takes is a value the input decides rather than a way the parse chooses, and saying it as a scan is
    what lets the codegen make one repeated-char-set call of it. Both are the same match said differently: a
    `ConsumeSpan` is the maximal run a `Star` already takes, and a `Plus` is its item once and then that same run.

    A counted repetition of one is the same value said with a count rather than an end: `x{n}` is the `n` characters of
    the set, all of them or none, which is what a `ConsumeCountedSpan` takes — a count the parse works out included,
    since a non-positive one matches nothing there as it does here.

    A run over anything else is left standing. It is a way, and what to do about the empty match it hides is a question
    about the parse rather than about a scan.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Rep) and ir.is_one_char(node.item, grammar):
            return ir.ConsumeCountedSpan(count=node.count, set=node.item)
        if isinstance(node, ir.Star) and ir.is_one_char(node.item, grammar):
            return ir.ConsumeSpan(set=node.item)
        if isinstance(node, ir.Plus) and ir.is_one_char(node.item, grammar):
            return ir.Seq(items=(node.item, ir.ConsumeSpan(set=node.item)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _repeated_character_classes(grammar):
    """
    Runs over a character class still written as a repetition — a value the scan decides, said as a way the parse
    repeats.

    What repeats is asked of `ir.repeated` rather than of a list of kinds kept here: a list goes stale the moment a
    repetition is spelled a new way, and this count would then read none because the kinds it named are gone rather than
    because no character run is left.
    """
    return [
        f"{name}: repeats a character class instead of scanning it"
        for name, production in grammar.items()
        for node in _held(production.body)
        if dataclasses.is_dataclass(node) and (item := ir.repeated(node)) is not None and ir.is_one_char(item, grammar)
    ]


CHARACTER_RUNS_SCANNED = Invariant("every-character-run-is-a-span", _repeated_character_classes)

# The two repetitions the vendored notation writes are gone, one `LongestRun` standing for both. What is left after this
# is one operation for repeating a way, which is what every reading past here is written against. The counted `Rep` is
# not one of these: it takes the number of turns it names rather than as many as it can, and is a later step's.
NO_STAR_OR_PLUS_NODES = _absent("no-star-or-plus-nodes", ir.Star, ir.Plus)


# Phase 6's first: a scope that holds what it covers is the pair that brackets it instead. A `(wrap)` is the one that
# says so outright — a node rather than the two markers so that a `begin` cannot lose its `end`, which is a guarantee
# `every-scope-closes-on-its-own-way` takes over for the pairs and `check_markers` still owes for the markers.
NO_WRAP_NODES = _absent("no-wrap-nodes", ir.Wrap)


def lower_wraps(grammar, namer):
    """
    Write each `(wrap)` as the two markers it stands for: `Wrap(begin, end, x)` becomes `Emit(begin) x Emit(end)`.

    A scope that holds what it covers has nowhere to stand in an alternative, which has a place for an action and none
    for a node enclosing a call. The node is sugar and says so: it exists so the two markers are paired by construction,
    and what it stands for is exactly the sequence written here — the interpreter emits the one, matches the item, and
    emits the other.

    The two come out adjacent in one way of one production and nothing until the calls are split can separate them.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Wrap):
            return ir.Seq(items=(ir.Emit(code=node.begin), node.item, ir.Emit(code=node.end)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# Phase 6's second: the `(max)` window is the pair that opens and closes it.
NO_MAX_NODES = _absent("no-max-nodes", ir.Max)


def lower_windows(grammar, namer):
    """
    Write each `(max)` as the window pair: `Max(limit, message, x)` becomes `OpenWindow(limit, message) x
    CloseWindow()`.

    Windows do not nest — only the outermost applies, an inner one being inside the budget the outer already bounds —
    and the pair counts the opens standing where the wrapper asked whether a ceiling was already set. The two agree only
    where every window is spelled one way, an open counting nothing having no ceiling to answer for, so the step leaves
    no `Max` at all rather than lowering the sites that want it.

    A `(max)` with nothing in it is the vendored grammar's bare length note, which libyeast writes around a production
    rather than before one; nothing in the pipeline places one, so meeting one here is an error rather than a window to
    open.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Max):
            if node.item is None:
                raise ValueError("a `(max)` with nothing in it is a length note, and the pipeline places none")
            return ir.Seq(items=(ir.OpenWindow(limit=node.limit, message=node.message), node.item, ir.CloseWindow()))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# Phase 6's third: the region a failed cut answers for is the pair that opens and closes it.
NO_COMMIT_NODES = _absent("no-commit-nodes", ir.Commit)


def lower_commits(grammar, namer):
    """
    Write each `(commit)` as the message pair: `Commit(message, x)` becomes `PushMessage(message) x PopMessage()`.

    A commit is the error where its item never reaches its own end, and nothing more: a continuation that fails past a
    matched item backtracks like any other match, the commitment not reaching past it. The pair says exactly that — the
    push records a region, the pop marks it reached, and an unwind that gets back to a push through a region never
    closed is the error. The interpreter says so where it implements the push: a `(commit)` scope's terms, the close
    standing where the scope's end stood.

    What changes is where the record lives: the wrapper keeps it in a Python local, the pair on the emitter's own list
    of open commitments, which is what survives the pair being split across a call.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Commit):
            return ir.Seq(items=(ir.PushMessage(message=node.message), node.item, ir.PopMessage()))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# Phase 6's last: the code the characters of a run carry is the pair that sets it and takes it back.
NO_TOKEN_NODES = _absent("no-token-nodes", ir.Token)


def lower_tokens(grammar, namer):
    """
    Write each `(token)` as the code pair: `Token(code, x)` becomes `PushCode(code) x PopCode()`.

    An annotation does not make a token: it says what code the characters consumed within it carry, and cuts the run at
    each edge so what came before and after falls into tokens of its own. Both halves do that — each cuts, the push sets
    the code and the pop takes back what it displaced — so the characters between them carry the same code either way.

    What changes is where the displaced code waits. The wrapper keeps it in a Python local, which is to say in the frame
    of the match that is running; the pair puts it on the parse's own stack, which is what lets the two halves end up in
    different productions once a way is split into a call and a continuation. That is the whole reason for the step, and
    the reason `every-scope-closes-on-its-own-way` has to hold while it happens: a pop takes back whatever is on top, so
    a pair cut apart carelessly would take back what another way had put there.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Token):
            return ir.Seq(items=(ir.PushCode(code=node.code), node.item, ir.PopCode()))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def lower_runs(grammar, namer):
    """
    Say the two repetitions as the one operation they are: `x*` becomes `LongestRun(x, 0)` and `x+` `LongestRun(x, 1)`.

    A run takes `x` again and again while it matches and stops where it does not, and what it took is the longest run —
    there is no shorter one, a continuation that fails failing the run rather than sending it back for fewer turns.
    `least` is the whole of the difference between the two spellings: a run of none or more falls through where nothing
    matched, one that must take a turn refuses there. Neither is the other with something around it, and the interpreter
    says so — its two arms were the same code but for that last line, and are one arm now.

    A run over a character class is the same operation said as the scan a parser makes of it, which `span-consumes` has
    already written as a `ConsumeSpan`; what is left here repeats a way.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Star):
            return ir.LongestRun(item=node.item, least=0)
        if isinstance(node, ir.Plus):
            return ir.LongestRun(item=node.item, least=1)
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def lower_optionals(grammar, namer):
    """
    Write each optional as the alternation it already is: `x?` becomes `x | <empty>`, the empty way standing beside the
    one that reads rather than hidden inside a node.

    The interpreter says it is the same match: an `Opt` tries its item with the continuation behind it and, where that
    fails, rewinds and takes the continuation alone — which is an alternation of the item and `<empty>`, tried in that
    order. Nothing about how much the item takes changes, so nothing has to be known about what follows.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        return ir.Alt(items=(node.item, ir.Empty())) if isinstance(node, ir.Opt) else node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


# What this step is for, on the way to the empties phase's own count: no optional hides an empty match. A `Star` hides
# the same thing and is not this — its lowering is no identity, the run being possessive where an alternation's empty
# way is a fallback the continuation can reach.
NO_OPT_NODES = _absent("no-opt-nodes", ir.Opt)


# What a match takes. A kind that always takes at least one character, and the kinds that never take any — the latter
# told apart by what they do with the position they do not move: an action leaves something behind and matches wherever
# it is reached, a guard leaves nothing and may decline. `<empty>` is both, doing nothing and always matching, so it is
# named where each of them needs it. A kind that reads on one way and not on another — a run, a repetition, a choice —
# is asked about its parts instead, and one named nowhere raises: an empty match answered for by accident is the whole
# debt this phase is here to remove. Each in alphabetical order.
_ALWAYS_READS = (
    ir.Char,
    ir.CharSet,
    ir.ConsumeChar,
    ir.ConsumeLiteral,
    ir.ConsumePeeked,
    ir.Diff,
    ir.Invalid,
    ir.LiteralPeek,
    ir.Range,
)
_ACTIONS = (
    ir.ClearVar,
    ir.CloseWindow,
    ir.CommitProvisional,
    ir.Emit,
    ir.Error,
    ir.ExcludeAt,
    ir.Increase,
    ir.InjectBefore,
    ir.MarkProvisional,
    ir.OpenProvisional,
    ir.OpenWindow,
    ir.PopCode,
    ir.PopIndent,
    ir.PopMessage,
    ir.PushCode,
    ir.PushIndent,
    ir.PushMessage,
    ir.RetypeProvisional,
    ir.SetVar,
)
_GUARDS = (ir.Cut, ir.EndOfStream, ir.Le, ir.Look, ir.LookBehind, ir.Lt, ir.NegLook, ir.StartOfLine)


# What an item may be: something the machine does where it stands — a call, an action, a guard, a character taken, or
# nothing at all. A guard's question is the guard's own business and no item of the way, which is what lets a peek hold
# a set and an exclusion a bounded literal; `every-peek-is-a-character-set` and `every-exclusion-is-bounded` are what
# answer for those. In alphabetical order after the families.
_LEAF_ITEMS = (
    *_ACTIONS,
    *_GUARDS,
    ir.Char,
    ir.CharSet,
    ir.ConsumeChar,
    ir.ConsumeCountedSpan,
    ir.ConsumeSpan,
    ir.Empty,
    ir.Invalid,
    ir.Range,
    ir.Ref,
)

# What holds a match, and so cannot stand where an item does: the tree the phase takes apart. A choice and a run become
# productions of their own, a recovery moves to the edge an alternative rides, and a binding becomes an action.
_HOLDS_A_MATCH = (ir.Alt, ir.Bind, ir.LongestRun, ir.Recover, ir.Seq)

# What a production's body may be, each a state the machine has: a choice of ways, a run of one, a way under a handler,
# or a way. A binding is not among them — a body that is one hides a write behind a match, which `no-bind-nodes` counts
# wherever it stands.
_BODY_KINDS = (ir.Alt, ir.Choice, ir.LongestRun, ir.Recover, ir.Seq)


def _inner_ways(node):
    """The matches `node` holds, each a way in its own right — what the walk goes on into once it has counted one."""
    if isinstance(node, ir.Choice):
        return node.alternatives
    if isinstance(node, ir.Alt):
        return node.items
    if isinstance(node, ir.Seq):
        return (node,)
    if isinstance(node, ir.LongestRun):
        return (node.item,)
    if isinstance(node, ir.Recover):
        return (node.item, node.recovery)
    return (node.cond,)  # a binding, whose condition is the match it puts a value in scope for


def _nested_matches(grammar, reported):
    """
    Items standing in a way that hold a match — every shape the machine has no single step for.

    The phase's own count, which its steps reduce between them: a body is a choice of ways or a run of one, a way is a
    run of items, and an item is a call, an action, a guard, a character taken or nothing. Anything else holds a match
    inside it and has to become a production of its own before a state machine can be read off it. `reported` narrows
    what is counted to one kind, which is how a step's own invariant reads its share of the phase's through the same
    walk — the walk goes into every holder either way, so a nested one is found wherever it stands.

    A nested one is counted where it stands rather than only at the outermost, so lifting one takes exactly one off the
    count and never uncovers a fault that was not already read. A kind named nowhere raises: an item answered for by
    accident is a shape the machine would meet with no state to be in.
    """
    faults = []

    def item(node, owner):
        if isinstance(node, _LEAF_ITEMS):
            return
        if isinstance(node, _HOLDS_A_MATCH):
            if isinstance(node, reported):
                faults.append(f"{owner}: a {type(node).__name__} stands where an item does and holds a match")
            for inner in _inner_ways(node):
                way(inner, owner)
            return
        raise TypeError(f"cannot tell whether {type(node).__name__} is an item the machine runs where it stands")

    def way(node, owner):
        for part in _items_of_way(node):
            item(part, owner)

    for name, production in grammar.items():
        body = production.body
        for opened in _inner_ways(body) if isinstance(body, _BODY_KINDS) else (body,):
            way(opened, name)
    return faults


ITEMS_ARE_LEAVES = Invariant("no-item-holds-a-match", lambda grammar: _nested_matches(grammar, _HOLDS_A_MATCH))

# The phase's first share: a choice is where the machine has a state, and standing inside a way it has nowhere to be
# one. Its own production is that state, and the way holds the call.
CHOICES_ARE_BODIES = Invariant("every-choice-is-a-body", lambda grammar: _nested_matches(grammar, ir.Alt))

# The second: a run is the machine's loop state, which is a production it jumps back to the top of. Naming it decides
# nothing about the run itself — it stays the possessive scan it was, and whether a turn is taken is the gate's
# question, asked where the gates arrive.
RUNS_ARE_BODIES = Invariant("every-run-is-a-body", lambda grammar: _nested_matches(grammar, ir.LongestRun))

# The third: a recovery is a handler over a match, which an alternative carries on its own edge. It has no edge to ride
# until the alternatives are made, so it gets a production of its own meanwhile — a body the re-encode reads as the way
# it is, its item the call and its recovery what rides the push.
RECOVERIES_ARE_BODIES = Invariant("every-recovery-is-a-body", lambda grammar: _nested_matches(grammar, ir.Recover))

# The last: a binding is a match and the write that follows it, which the vocabulary already spells as two things.
NO_BIND_NODES = _absent("no-bind-nodes", ir.Bind)

# What a bounded question is made of: a character class, a bounded run of characters the input must begin, the
# zero-width conditions, and the choices and sequences holding them. A name is not among them — what a call asks costs
# whatever the callee reads, which is the whole difference between a guard the machine can test where it stands and one
# it would have to run a parse to answer.
_BOUNDED_LEAVES = (ir.CharSet, ir.EndOfStream, ir.LiteralPeek, ir.StartOfLine)


def _is_bounded_question(node):
    """Whether `node` asks what a bounded run of characters answers — a leaf that is one, or a choice or run of them."""
    if isinstance(node, _BOUNDED_LEAVES):
        return True  # a literal peek is a leaf here: what it holds is its own run and its follow test, not a match
    if isinstance(node, (ir.Alt, ir.Seq)):
        return all(_is_bounded_question(item) for item in node.items)
    return False


def _wide_exclusions(grammar):
    """
    Exclusions asking a question the machine cannot test in a bounded run of characters.

    An `(exclude)` is a guard the parse carries, tested at every start of line while it stands, so what it asks has to
    be answerable where it is asked: `c-forbidden` is a line beginning `---` or `...` and then a break, a space or the
    end, which is four characters and no more. The line-at-this-indentation test is not — an indentation is a run of
    spaces with no bound — and it is a condition on a line start rather than a question about what follows, so it lands
    where the block-structure work makes a line start a decision the grammar spells.
    """
    return [
        f"{name}: an `(exclude)` asks what no bounded run of characters answers"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.ExcludeAt) and not _is_bounded_question(node.item)
    ]


EXCLUSIONS_ARE_BOUNDED = Invariant("every-exclusion-is-bounded", _wide_exclusions)


def _items_of_way(way):
    """
    The items a way is made of, whichever way it is spelt.

    An alternative says its parts by name — what it does, what it calls, where it carries on — where a sequence says
    them in a row; a reading that walks a way wants them in the order the parse performs them either way. What a
    recovery rides is not among them: it answers for a cut rather than standing in the way's own run.
    """
    if isinstance(way, ir.Alternative):
        return (*way.actions, *(held for held in (way.first, way.second) if held is not None))
    return way.items if isinstance(way, ir.Seq) else (way,)


def _ways_or_items(node):
    """
    The ways a choice offers or the items an alternative performs — what a walk goes into, in either spelling.

    A choice says its ways as `alternatives` where it is the machine's and as `items` where it is the tree's, and an
    alternative says its parts by name where a sequence says them in a row.
    """
    if isinstance(node, ir.Choice):
        return node.alternatives
    return node.items if isinstance(node, ir.Alt) else _items_of_way(node)


def _way_items(body):
    """The ways `body` opens, each as the run of items it is — what phase 7 leaves every body made of."""
    return tuple(_items_of_way(way) for way in (_inner_ways(body) if isinstance(body, _BODY_KINDS) else (body,)))


def _crowded_ways(grammar):
    """
    Ways holding more than the machine performs in one: actions, a call, and where to carry on when it returns.

    An edge of the machine is one push — the continuation it will come back to — and a jump. So a way is what it does
    before it hands control on, the call it hands it to, and the one production that carries on: what stands past the
    first call is that continuation's, and a second call is where to carry on rather than a third thing to do.

    Counted by the way rather than by the item standing in the wrong place, since a way is what the minting rewrites and
    what is left of one after the split is a way of its own with the same question asked of it.
    """
    faults = []
    for name, production in grammar.items():
        for items in _way_items(production.body):
            calls = 0
            for item in items:
                if isinstance(item, ir.Ref):
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


WAYS_ARE_CALL_AND_CONTINUATION = Invariant("a-way-is-actions-a-call-and-a-continuation", _crowded_ways)


def _untold_bodies(grammar):
    """
    Bodies that are not one of the three things the machine has a state for.

    A terminal is a set of characters and nothing else. A loop is a run over a call — the state it jumps back to the top
    of, which says nothing about when it stops, that being a character's to decide. Everything else is an ordered list
    of alternatives, each what one way of the machine does: a gate to enter on, the actions it performs, the call it
    hands control to, where to carry on when that returns, and the recovery riding the push.

    The shape is checked through rather than at the top, since what makes a body canonical is that nothing inside it is
    the tree again: an alternative's actions hold no call, and what it calls is a name.
    """
    faults = []
    for name, production in grammar.items():
        body = production.body
        if isinstance(body, ir.CharSet):
            continue
        if isinstance(body, ir.LongestRun):
            if not isinstance(body.item, ir.Ref):
                faults.append(f"{name}: a run whose turn is not a call, where the loop has no state to jump to")
            continue
        if not isinstance(body, ir.Choice):
            faults.append(f"{name}: a body that is neither a set, a run of a call, nor a choice of alternatives")
            continue
        for alternative in body.alternatives:
            if not isinstance(alternative, ir.Alternative):
                faults.append(f"{name}: a choice holding what is not an alternative")
            elif any(isinstance(action, ir.Ref) for action in alternative.actions):
                faults.append(f"{name}: an alternative doing a call among its actions")
            elif any(
                held is not None and not isinstance(held, ir.Ref) for held in (alternative.first, alternative.second)
            ):
                faults.append(f"{name}: an alternative handing control to what is not a name")
    return faults


BODIES_ARE_STATES = Invariant("every-body-is-a-choice-a-run-or-a-set", _untold_bodies)


def _ungated_ways(grammar):
    """
    Ways a parse enters on nothing, where another way stands behind them.

    A machine takes a way by looking at what stands in front of it, so a way its gate says nothing about is one it would
    have to try and give back — which is the backtracking the whole shape is for getting rid of. A peek is the usual
    answer and not the only one: a guard is a question the machine can put where it stands too, and a way entered at the
    end of the input has no character to be asked about at all, which is the input deciding rather than the decision
    being left open.

    The last way of a choice is exempt: an empty gate is the unconditional fallthrough, and something has to be what
    happens where nothing else fires. A body with one way is no decision at all and is asked nothing.
    """
    return [
        f"{name}: a way its gate says nothing about, where another way stands behind it"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice) and len(production.body.alternatives) > 1
        for way in production.body.alternatives[:-1]
        if way.gate.peek is None and not way.gate.guards
    ]


WAYS_ARE_GATED = Invariant("every-way-gated", _ungated_ways)


def _do_spans_overlap(one, other):
    """Whether two runs of codepoint intervals admit a character in common."""
    return any(not (left[1] < right[0] or right[1] < left[0]) for left in one for right in other)


def _undecided_choices(grammar):
    """
    Choices no character tells apart — the meter, and what determinizing exists to drive to none.

    A machine that never backtracks takes the way whose gate the character in front of it fires, so a choice is decided
    when at most one gate can fire on any character: the ways it offers are entered on sets that do not meet. Two ways
    admitting the same character are decided by order and nothing else, which is a guess the machine has no way to take
    back, and a way with no gate at all is worse — it would have to be tried.

    The last way is the exception and not a fault: an empty gate there is the unconditional fallthrough, taken where no
    gate fired, which is a decision a character made by firing nothing. A guard is no part of this, a guard being a
    condition on the parse rather than a question about the character; a choice its ways' peeks do not separate counts
    here even where a guard would have separated them, which errs toward work rather than away from it.

    Counted per choice rather than per way: the choice is what the machine decides at, and one way of it left ungated
    leaves the whole decision undecided.
    """
    return [f"{name}: {reason}" for name, reason in _undecided_reasons(grammar).items()]


def _undecided_reasons(grammar):
    """
    `{name: reason}` per choice no character tells apart — the meter's own reading, said once for both its readers.
    """
    reasons = {}
    for name, production in grammar.items():
        body = production.body
        if not isinstance(body, ir.Choice) or len(body.alternatives) < 2:
            continue
        peeks = [
            _peek_spans(way.gate.peek, grammar) if way.gate.peek is not None else None for way in body.alternatives
        ]
        if any(spans is None for spans in peeks[:-1]):
            reasons[name] = "a choice offering a way entered on no character"
            continue
        admitted = [spans for spans in peeks if spans is not None]
        if any(
            _do_spans_overlap(admitted[before], admitted[after])
            for before in range(len(admitted))
            for after in range(before + 1, len(admitted))
        ):
            reasons[name] = "a choice whose ways admit the same character, and order is what tells them apart"
    return reasons


DECISIONS_GO_ON_A_CHARACTER = Invariant("every-decision-goes-on-a-character", _undecided_choices)


def _follows_of(grammar):
    """
    `{name: {follow}}` — what each production is reached with, a follow being where the parse goes when it returns.

    A follow is the position *and* what stands at it: called and come back from, with something to carry on at or with
    nothing, is not the same place as called as the tail of a way. The walk that roots a conflict reads it that way, so
    this does too — read without the position, a production called both ways looks reached with one follow, and the
    count then says a conflict can be asked about where the walk refuses it.
    """
    follows = {}
    for name, production in grammar.items():
        if not isinstance(production.body, ir.Choice):
            continue
        for way in production.body.alternatives:
            if way.first is not None:
                carried = None if way.second is None else way.second.name
                follows.setdefault(way.first.name, set()).add(("comes back", carried))
            if way.second is not None:
                follows.setdefault(way.second.name, set()).add(("carries on", None))
    return follows


def _conflicts_with_several_follows(grammar):
    """
    Choices no character decides that are reached with more than one follow.

    A conflict resolves on what comes *after* it as often as on what it holds — the way that stops where a content line
    follows is told from the one that goes on by the line, which is the caller's and not the conflict's. So a walk over
    the live ways has to stand somewhere, and where the production is called from places that carry on differently there
    is no single somewhere to stand: the question "which way does the input take" has as many answers as callers.

    Not a fault of the conflict's own — it is what makes the conflict unaskable, and every such site is a verdict the
    meter is claiming without having one. So this is the count that says how much of the meter can even be worked on:
    the walk refuses exactly these, and what it says of the rest is a verdict to act on.
    """
    follows = _follows_of(grammar)
    return [
        f"{name}: a choice no character decides, reached from {len(follows[name])} places and so with nowhere to ask it"
        for name in _undecided_reasons(grammar)
        if len(follows.get(name, ())) > 1
    ]


CONFLICTS_CAN_BE_ASKED = Invariant("every-conflict-can-be-asked", _conflicts_with_several_follows)


def deterministic_productions(grammar):
    """
    The productions a parse may enter committed: the ones whose ways a character tells apart.

    What the interpreter's committed mode takes. Entering one, the first way whose gate holds is the parse and no other
    is tried, which is the machine's own behaviour — so running the corpus with these committed and everything else
    backtracking asks the question no static count can. The meter says whether a character *picks* a way; this says
    whether the way it picks goes on to match, and a gate can be perfectly disjoint and still be wrong, the way it
    admits failing three characters later where a backtracking parse would have taken the next one.

    A body with one way is in it: there is nothing to choose, so committing to it is what backtracking does anyway.
    """
    undecided = _undecided_reasons(grammar)
    return frozenset(
        name for name, production in grammar.items() if isinstance(production.body, ir.Choice) and name not in undecided
    )


# What a loop repeats is a state it jumps to, so a run's turn is a call. Its own count rather than a share of the
# phase's: naming the turn mints a production for it, which is a body of the tree's own shape until the re-encode
# reaches it, so what the phase counts does not move.
RUN_TURNS_ARE_CALLS = Invariant(
    "every-run-turns-on-a-call",
    lambda grammar: [
        f"{name}: a run whose turn is not a call, where the loop has no state to jump to"
        for name, production in grammar.items()
        if isinstance(production.body, ir.LongestRun) and not isinstance(production.body.item, ir.Ref)
    ],
)


def _asked_parts(node, grammar):
    """
    What a question asks, in order — names read through, annotations read off, sequences flattened into one run.

    The same reading `_peeked_question` makes of a peek, said of a question with more than one part in it: a probe emits
    nothing and gives back what it read, so a code around a character is no part of what is asked and a name is the
    caller's hold rather than a question the machine can put to the input.
    """
    if isinstance(node, ir.Ref) and not node.args:
        return _asked_parts(grammar[node.name].body, grammar)
    if isinstance(node, (ir.Token, ir.Wrap)):
        return _asked_parts(node.item, grammar)
    if isinstance(node, ir.Seq):
        return tuple(part for item in node.items for part in _asked_parts(item, grammar))
    return () if isinstance(node, _PEEK_OUTPUT) else (node,)


def _literal_text(node, grammar):
    """The fixed run of codepoints `node` asks the input to begin with, or `None` where what it asks is not one."""
    text = []
    for part in _asked_parts(node, grammar):
        spans = _peek_spans(part, grammar)
        if spans is None or len(spans) != 1 or spans[0][0] != spans[0][1] or spans[0][0] < 0:
            return None
        text.append(spans[0][0])
    return tuple(text) or None


def _follow_class(node, grammar):
    """
    `node` as the one class a literal peek's follow test names, or `None` where it is not a class.

    An end-of-stream way is dropped rather than denoted: a follow test is what the character after the run must be *if
    there is one*, the end of the input passing it either way, so a question that admits the end says nothing this has
    to carry.
    """
    spans = []
    for way in node.items if isinstance(node, ir.Alt) else (node,):
        if isinstance(way, ir.EndOfStream):
            continue
        admitted = _peek_spans(way, grammar)
        if admitted is None:
            return None
        spans += admitted
    return _spans_node(_merged_spans(spans)) if spans else None


def _as_literal_question(node, grammar):
    """
    `node` as the literal peeks it denotes — guards, then a choice of fixed runs each with its follow test — or `None`
    where it is not of that shape.

    What makes the rewrite an identity is that a question is probed and given back: the follow test is one class for
    every run, so distributing it over them duplicates a test rather than a match, and reading a name through costs
    nothing that was ever emitted.
    """
    parts = _asked_parts(node, grammar)
    if len(parts) < 2:
        return None
    guards, runs, follow = parts[:-2], parts[-2], parts[-1]
    if not all(isinstance(guard, ir.StartOfLine) for guard in guards):
        return None
    texts = [_literal_text(way, grammar) for way in (runs.items if isinstance(runs, ir.Alt) else (runs,))]
    then = _follow_class(follow, grammar)
    if then is None or any(text is None for text in texts):
        return None
    peeks = tuple(ir.LiteralPeek(text=text, then=then, barrier=None) for text in texts)
    return ir.Seq(items=(*guards, peeks[0] if len(peeks) == 1 else ir.Alt(items=peeks)))


def mint_continuations(grammar, namer):
    """
    Give what a way does past its call a production of its own, so that a way is actions, a call, and where to carry on.

    An edge of the machine is one push and one jump: the push says where to come back to and the jump goes. So what
    stands past the call belongs to that continuation rather than to the way — spelled out, `a P1 b P2 c` is `a`, the
    call `P1`, and a production holding `b P2 c`, which splits the same way until nothing is left standing past a call.
    A way that ends in two calls is already that shape, the second being where to carry on rather than a third thing to
    do, so nothing is minted for it.

    A scope opened before the call and closed after it comes apart here, and that is what phase 6 was for: the pairs
    hold on the parse's own stack rather than in the frame of the match that is running, so a `PushCode` left in the way
    and its `PopCode` carried into the continuation still meet.
    """
    minted = {}

    def split(items, owner):
        for index, item in enumerate(items):
            if not isinstance(item, ir.Ref):
                continue
            rest = items[index + 1 :]
            if not rest or (len(rest) == 1 and isinstance(rest[0], ir.Ref)):
                return items
            name = namer.fresh(owner)
            carried = split(rest, owner)
            minted[name] = ir.Prod(
                grammar[owner].number, name, (), carried[0] if len(carried) == 1 else ir.Seq(items=carried)
            )
            return (*items[: index + 1], ir.Ref(name=name, args=()))
        return items

    def way(node, owner):
        items = split(node.items if isinstance(node, ir.Seq) else (node,), owner)
        return items[0] if len(items) == 1 else ir.Seq(items=items)

    def body(node, owner):
        if isinstance(node, _BODY_KINDS) and not isinstance(node, ir.Seq):
            return _with_inner_ways(node, lambda opened: way(opened, owner))
        return way(node, owner)

    split_ways = {
        name: dataclasses.replace(production, body=body(production.body, name)) for name, production in grammar.items()
    }
    return {**split_ways, **minted}


def call_run_turns(grammar, namer):
    """
    Give a run's turn a production of its own where it is not already a call, so that a loop is a state it jumps to.

    A run repeats one thing, and what the machine repeats is a state: `LongestRun(Ref(P))` says go to `P`, come back, go
    again. A turn spelled out in place is the same match with nowhere to jump to.
    """
    minted = {}

    def turned(name, production):
        body = production.body
        if not isinstance(body, ir.LongestRun) or isinstance(body.item, ir.Ref):
            return production
        held = namer.fresh(name)
        minted[held] = ir.Prod(production.number, held, (), body.item)
        return dataclasses.replace(production, body=dataclasses.replace(body, item=ir.Ref(name=held, args=())))

    turns = {name: turned(name, production) for name, production in grammar.items()}
    return {**turns, **minted}


def _as_alternative(way, recovery=None):
    """
    One way of a body as the alternative it is: the actions it performs, the call it hands control to, and where it
    carries on.

    A way with one call is a tail-goto and that call is where it carries on; with two, the first is the call it comes
    back from and the second where it goes then. A recovery rides the push, so the call it protects is the one the way
    comes back from — which is what the interpreter reads it as, the handler standing over that call and its
    continuation being the caller's.

    The gate is empty here. What a way is entered on is a question about the character in front of it, which is the
    hoist's to answer; until then the alternatives are tried in order, which is what the tree said too.
    """
    items = way.items if isinstance(way, ir.Seq) else (way,)
    calls = tuple(item for item in items if isinstance(item, ir.Ref))
    actions = tuple(item for item in items if not isinstance(item, ir.Ref))
    if recovery is not None:
        first, second = calls[0], calls[1] if len(calls) > 1 else None
    else:
        first, second = (calls[0], calls[1]) if len(calls) > 1 else (None, calls[0] if calls else None)
    return ir.Alternative(gate=ir.Gate(), actions=actions, first=first, second=second, recover=recovery)


def build_alternatives(grammar, namer):
    """
    Say every body in the machine's own words: a set of characters, a run over the state it repeats, or the ordered list
    of alternatives one of which the parse takes.

    A change of spelling and not of meaning — by the time it runs a body already *is* a choice of ways that are actions,
    a call and a continuation, and the interpreter reads an alternative as exactly the sequence the tree spelt: the
    gate's peek as a lookahead, then its guards, then the actions, then the call and where it carries on. What is gained
    is that the shape says what the machine does rather than leaving it to be worked out, which is what every reading
    from here on stands on.
    """

    def told(production):
        body = production.body
        if isinstance(body, (ir.CharSet, ir.LongestRun)):
            return production
        if isinstance(body, ir.Recover):
            alternatives = (_as_alternative(body.item, recovery=body.recovery),)
        elif isinstance(body, ir.Alt):
            alternatives = tuple(_as_alternative(way) for way in body.items)
        else:
            alternatives = (_as_alternative(body),)
        return dataclasses.replace(production, body=ir.Choice(alternatives=alternatives))

    return {name: told(production) for name, production in grammar.items()}


def _way_entry(way, grammar, ways, entry):
    """
    The characters a way can be entered on, as spans — what a machine would have to see in front of it to take this way.

    A gated way is entered on its gate and nothing else, that being what a gate says. Otherwise the walk goes along the
    items until something must take a character: a set contributes itself and ends it, a call contributes what its
    production can start on and ends it unless that production can take nothing, and a scan of none or more contributes
    its set and goes on, since it may take none. An action or a guard contributes nothing and does not end the walk —
    neither touches the input — so what stands behind them is what the way is entered on. A kind named nowhere raises: a
    way entered on characters this cannot see is a gate too narrow, which loses a parse rather than costing one.
    """
    if way.gate.peek is not None:
        return _peek_spans(way.gate.peek, grammar) or []
    got = []
    for item in _items_of_way(way):
        if isinstance(item, (ir.Char, ir.CharSet, ir.Invalid, ir.Range)):
            return got + (_peek_spans(item, grammar) or [])
        if isinstance(item, (ir.ConsumeSpan, ir.ConsumeCountedSpan)):
            got += _peek_spans(item.set, grammar) or []
            continue  # a scan of none or more may take none, so what follows can be what enters the way
        if isinstance(item, ir.Ref):
            got += list(entry[item.name])
            if not ways[item.name][1]:
                return got  # the callee always takes a character, so nothing past it is entered here
            continue
        if isinstance(item, (*_ACTIONS, *_GUARDS, ir.Empty, ir.ConsumeChar)):
            continue  # an action or a guard takes no character, and a gated consume is the gate's own business
        raise TypeError(f"cannot tell what characters {type(item).__name__} lets a way be entered on")
    return got


def _entry_spans(grammar):
    """
    `{name: spans}` — the characters a parse of each production can start on.

    A least fixed point, since a production can reach itself: nothing is taken to be an entry until some way says so,
    which is what makes the answer the smallest one consistent with the grammar rather than everything. It errs wide
    where it errs at all — a scan that may take none contributes its set *and* what follows — because a gate too wide
    costs a parse that fails where it could have been refused, and a gate too narrow loses one that should have matched.
    """
    ways = _split_ways(grammar)
    entry = {name: () for name in grammar}
    for _round in range(len(grammar) + 1):
        did_move = False
        for name, production in grammar.items():
            body = production.body
            if isinstance(body, ir.CharSet):
                got = _peek_spans(body, grammar) or []
            elif isinstance(body, ir.LongestRun):
                got = list(entry[body.item.name])
            else:
                got = [span for way in body.alternatives for span in _way_entry(way, grammar, ways, entry)]
            merged = tuple(_merged_spans([span for span in got if span[0] >= 0]))
            merged += ((-1, -1),) * any(span[0] < 0 for span in got)
            if entry[name] != merged:
                entry[name], did_move = merged, True
        if not did_move:
            return entry
    raise AssertionError("the characters a production can be entered on never settled")


def gate_hoist(grammar, namer):
    """
    A way whose first action takes a character is entered on that character: the set rises into the gate and the action
    becomes taking what the gate found.

    A gate is what the machine looks at to choose a way, and it looks without consuming — so the set moves and a
    `ConsumeChar` stands where it did, taking the one character the gate has already found there. The two say the same
    match in the same order, which is what the interpreter reads them as: the peek as a lookahead, then the actions.

    Every alternative, not only the ones a choice needs to tell apart. A way whose first action is a set fails there
    where the set is not, gate or no gate, so saying it in the gate says what the machine is entered on and changes
    nothing about when the way matches.
    """

    def hoisted(way):
        if way.gate.peek is not None or not way.actions or not isinstance(way.actions[0], ir.CharSet):
            return way
        return dataclasses.replace(
            way,
            gate=dataclasses.replace(way.gate, peek=way.actions[0]),
            actions=(ir.ConsumeChar(), *way.actions[1:]),
        )

    def told(production):
        body = production.body
        if not isinstance(body, ir.Choice):
            return production
        return dataclasses.replace(
            production, body=ir.Choice(alternatives=tuple(hoisted(way) for way in body.alternatives))
        )

    return {name: told(production) for name, production in grammar.items()}


def _does_refuse_softly(name, grammar, ways, seen=frozenset()):
    """
    Whether entering `name` on a character it cannot start with fails rather than raising.

    A gate on a call refuses the way where the callee could not have started, which is the answer the callee would have
    given one call deeper — unless the callee answers with an error rather than a refusal. A `(commit)` opened before
    anything has to take a character makes the failure the error that region names, and a `(cut)` or an `(error)`
    standing there says so outright. Then a gate that never enters it turns an error into a way not taken, and the
    choice goes on to a way that matches: a parse that accepted nothing before accepts something now, which the corpus
    reads as libyeast taking a document the suite rejects.

    A production reached again says nothing new, the way in having been judged where it stood; a set or a run refuses by
    not matching, having nothing to raise with.
    """
    if name in seen:
        return True
    body = grammar[name].body
    if not isinstance(body, ir.Choice):
        return True
    for way in body.alternatives:
        for item in _items_of_way(way):
            if isinstance(item, (ir.Cut, ir.Error, ir.PushMessage)):
                return False
            if isinstance(item, (ir.Char, ir.CharSet, ir.ConsumeChar, ir.Invalid, ir.Range)):
                break
            if isinstance(item, ir.Ref):
                if not _does_refuse_softly(item.name, grammar, ways, seen | {name}):
                    return False
                if not ways[item.name][1]:
                    break
    return True


def gate_hoist_call(grammar, namer):
    """
    A way that begins by handing control to a production is entered on what that production can start on.

    The way cannot match unless the callee does, and the callee cannot start on a character outside its entry set — so
    the set refuses exactly what the way would have failed on anyway, one call deeper. The entry set errs wide where it
    errs, which is the safe direction here: a gate too wide costs a parse that fails where it could have been refused,
    and a gate too narrow loses one that should have matched.

    Not where the callee can take nothing. Such a way passes through the call to whatever stands behind it, so what
    enters it is the callee's entry set *and* the rest of the way's, and this hoist has only the first half. Nor where
    the callee answers a character it cannot start on with an error rather than a refusal: refusing at the gate would
    let the choice go on to a way that matches where the parse used to stop, which is a different language and not a
    narrower one.
    """
    entry = _entry_spans(grammar)
    ways = _split_ways(grammar)

    def hoisted(way):
        if way.gate.peek is not None:
            return way
        items = _items_of_way(way)
        if not items or not isinstance(items[0], ir.Ref):
            return way
        called = items[0].name
        if ways[called][1] or not entry[called] or not _does_refuse_softly(called, grammar, ways):
            return way
        return dataclasses.replace(way, gate=dataclasses.replace(way.gate, peek=_spans_node(entry[called])))

    def told(production):
        body = production.body
        if not isinstance(body, ir.Choice):
            return production
        return dataclasses.replace(
            production, body=ir.Choice(alternatives=tuple(hoisted(way) for way in body.alternatives))
        )

    return {name: told(production) for name, production in grammar.items()}


def hoist_past_actions(grammar, namer):
    """
    A way is entered on the character its first question asks, whatever actions stand in front of that question.

    A gate is tested before the way is entered and an action touches no input, so what the machine looks at to choose
    this way is the same character either way. The peek moves and the actions stay where they are: the interpreter runs
    the peek as a lookahead and then the actions, and a way that fails at the character test rewinds whatever its
    actions did, so testing before performing them is the same parse token for token.

    A call the way can pass through — one whose production can take nothing — does not end the walk either: what enters
    the way is then what that call can start on *and* what stands behind it, which is the union the walk accumulates. A
    way that passes through everything it holds is left alone, no character having to be in front of it at all.

    The walk stops at a commit, and this is the same refusal `gate-hoist-call` makes one call deeper: a `(cut)`, an
    `(error)` or a committed region opened before the question means failing there is an error rather than a refusal,
    and a gate that keeps the way from being entered at all turns that error into a way not taken. It stops at a scan of
    none or more too, which forces no character to be there.

    Only the peek moves. A guard stays among the actions: it may read what an action before it wrote, and hoisting one
    over that action would have it read what the parse had not yet done.
    """
    entry = _entry_spans(grammar)
    ways = _split_ways(grammar)

    def asked(items):
        """The spans a way is entered on and the index of the consume to take on the gate's word, or `None`."""
        got = []
        for at, item in enumerate(items):
            if isinstance(item, (ir.Cut, ir.Error, ir.PushMessage)):
                return None
            if isinstance(item, ir.CharSet):
                return got + (_peek_spans(item, grammar) or []), at
            if isinstance(item, ir.Ref):
                if not entry[item.name] or not _does_refuse_softly(item.name, grammar, ways):
                    return None
                got += list(entry[item.name])
                if not ways[item.name][1]:
                    return got, None
                continue  # the callee may take nothing, so what stands behind it enters the way as well
            if isinstance(item, (ir.ConsumeSpan, ir.ConsumeCountedSpan)):
                return None  # a scan of none or more forces no character to be there
            if isinstance(item, (*_ACTIONS, *_GUARDS, ir.Empty)):
                continue
            return None
        return None  # the way passes through everything it holds: no character has to be in front of it

    def hoisted(way):
        if way.gate.peek is not None:
            return way
        found = asked(_items_of_way(way))
        if found is None:
            return way
        spans, at = found
        actions = way.actions if at is None else (*way.actions[:at], ir.ConsumeChar(), *way.actions[at + 1 :])
        return dataclasses.replace(way, gate=dataclasses.replace(way.gate, peek=_spans_node(spans)), actions=actions)

    def told(production):
        body = production.body
        if not isinstance(body, ir.Choice):
            return production
        return dataclasses.replace(
            production, body=ir.Choice(alternatives=tuple(hoisted(way) for way in body.alternatives))
        )

    return {name: told(production) for name, production in grammar.items()}


def _spans_meeting(one, other):
    """The codepoints both span lists admit — what a way entered on both is entered on."""
    return [
        (max(left[0], right[0]), min(left[1], right[1]))
        for left in one
        for right in other
        if not (left[1] < right[0] or right[1] < left[0])
    ]


def splice_conflicts(grammar, namer):
    """
    A call to a choice no character decides, made where the way has taken nothing and handed control to no one else, is
    spliced: the callee's ways stand where the call did, each carrying on where the way would have.

    What a conflict is asked is which way the input takes, and the answer is often behind the return — so the walk has
    to stand where the caller stands. Called from several places that carry on differently there is no such place, and
    spliced there is one per site: each copy is the conflict in the context that reaches it, whose follow is the rest of
    the way it now sits in. The copies differ by where they are rather than by what they hold, which is what keeps the
    sweep from folding them back into one.

    Sound where the way has taken no character and called nobody before the splice: the callee is then entered exactly
    where the way is, so what the callee is entered on is what the way is entered on, and the two gates meet at one
    position. Where the way is gated too, the gates are met — a spliced way is entered on what both admit, and one
    admitting nothing is a way the parse could never have taken and is dropped.

    A spliced way holding three calls has nowhere to put the third, an alternative having two: what the callee carried
    on at and what the way carried on at become a state of their own, which is the same minting `mint-continuations`
    does and for the same reason.

    Run until it stops taking sites, because splicing makes sites: a conflict spliced into its callers puts copies where
    those callers are called from, and those are asked the same question. One pass moves the count up as often as down —
    what is left where it started is a caller that has become the conflict — so the pass is not the step; the fixpoint
    is.
    """
    while True:
        spliced = _spliced_once(grammar, namer)
        if spliced == grammar:
            return grammar
        grammar = cleaned(spliced)[0]


def _spliced_once(grammar, namer):
    """One pass of `splice_conflicts`, which runs it to a fixpoint."""
    follows = _follows_of(grammar)
    conflicts = {name for name in _undecided_reasons(grammar) if len(follows.get(name, ())) > 1}
    signature = _scope_signature(grammar)
    minted = {}

    def spliced(owner, way):
        target = way.first if way.first is not None and way.first.name in conflicts else None
        if target is None and way.first is None and way.second is not None and way.second.name in conflicts:
            target, carried = way.second, None
        elif target is not None:
            carried = way.second
        else:
            return [way]
        if any(isinstance(action, ir.CONSUMING) for action in way.actions):
            return [way]  # the way has taken a character, so the callee is not entered where the way is
        if any(isinstance(action, (ir.Cut, ir.Error, ir.PushMessage)) for action in way.actions):
            # The way has committed before the call, and the callee's ways backtrack *inside* that region: spliced out,
            # each would open a region of its own, and the first one's failure would be the error rather than the next
            # way's turn. The flow collections' unterminated-bracket commits are every one of these.
            return [way]
        if carried is not None and any(
            inner.second is not None and signature[inner.second.name] != ((), ())
            for inner in grammar[target.name].body.alternatives
        ):
            # What the callee carries on at would become a call the way comes back from, with the caller's own
            # continuation pushed behind it — so a scope that call leaves open would meet the push rather than its own
            # pop. Level, it meets nothing; otherwise only folding the follow into the callee can say it, which is not
            # this step.
            return [way]
        ways = []
        for inner in grammar[target.name].body.alternatives:
            peek = inner.gate.peek if way.gate.peek is None else way.gate.peek if inner.gate.peek is None else None
            if peek is None and way.gate.peek is not None and inner.gate.peek is not None:
                met = _spans_meeting(
                    _peek_spans(way.gate.peek, grammar) or [], _peek_spans(inner.gate.peek, grammar) or []
                )
                if not met:
                    continue  # entered on nothing: a way the parse could never have taken
                peek = _spans_node(_merged_spans([span for span in met if span[0] >= 0]) + [s for s in met if s[0] < 0])
            second = inner.second
            if second is not None and carried is not None:
                held = namer.fresh(owner)
                minted[held] = ir.Prod(
                    grammar[owner].number,
                    held,
                    (),
                    ir.Choice(alternatives=(ir.Alternative(gate=ir.Gate(), actions=(), first=second, second=carried),)),
                )
                second = ir.Ref(name=held, args=())
            elif second is None:
                second = carried
            ways.append(
                ir.Alternative(
                    gate=ir.Gate(peek=peek, guards=(*way.gate.guards, *inner.gate.guards)),
                    actions=(*way.actions, *inner.actions),
                    first=inner.first,
                    second=second,
                    recover=inner.recover,
                )
            )
        return ways

    def told(name, production):
        body = production.body
        if not isinstance(body, ir.Choice):
            return production
        ways = tuple(one for way in body.alternatives for one in spliced(name, way))
        return dataclasses.replace(production, body=ir.Choice(alternatives=ways))

    return {**{name: told(name, production) for name, production in grammar.items()}, **minted}


def hoist_guards(grammar, namer):
    """
    A way that begins with a guard carries it in its gate, where the machine asks it.

    A gate is a peek and the zero-width conditions that must hold with it, and a guard leading a way is exactly one of
    those: `EndOfStream` says the way is entered where no character is left, and a look-behind asks about the character
    already taken. Both are questions the machine can put where it stands, which is what a gate is for.

    Only a leading run of them moves. A guard further in may read what an action before it wrote — an indentation
    comparison after a push — and asking it at the gate would have it read what the parse has not done yet.
    """

    def hoisted(way):
        leading = 0
        while leading < len(way.actions) and isinstance(way.actions[leading], (ir.EndOfStream, ir.LookBehind)):
            leading += 1
        if not leading:
            return way
        return dataclasses.replace(
            way,
            gate=dataclasses.replace(way.gate, guards=(*way.gate.guards, *way.actions[:leading])),
            actions=way.actions[leading:],
        )

    def told(production):
        body = production.body
        if not isinstance(body, ir.Choice):
            return production
        return dataclasses.replace(
            production, body=ir.Choice(alternatives=tuple(hoisted(way) for way in body.alternatives))
        )

    return {name: told(production) for name, production in grammar.items()}


def bound_exclusions(grammar, namer):
    """
    Write what an exclusion asks as the bounded run of characters it is: `c-forbidden` becomes the two literal peeks a
    line beginning `---` or `...` and then a break, a space or the end denotes.

    An `(exclude)` is a guard the parse carries and tests at every start of line while it stands, so what it asks has to
    be answerable where it is asked — four characters here, and the machine's own fill already guarantees them. Named
    instead, it is a call the guard would have to run a parse to answer.

    What it does not reach is the line at this indentation, which is a run of spaces with no bound and a condition on a
    line start rather than a question about what follows it. That one waits for the line start to be a decision the
    grammar spells.
    """

    def bounded(node):
        node = ir.rebuilt(node, bounded)
        if not isinstance(node, ir.ExcludeAt):
            return node
        ways = node.item.items if isinstance(node.item, ir.Alt) else (node.item,)
        asked = tuple(_as_literal_question(way, grammar) or way for way in ways)
        return dataclasses.replace(node, item=asked[0] if len(asked) == 1 else ir.Alt(items=asked))

    return {
        name: dataclasses.replace(production, body=bounded(production.body)) for name, production in grammar.items()
    }


def _with_inner_ways(node, rebuilt):
    """`node` with each match it holds replaced by `rebuilt` of it — the transform's mirror of `_inner_ways`."""
    if isinstance(node, ir.Alt):
        return dataclasses.replace(node, items=tuple(rebuilt(way) for way in node.items))
    if isinstance(node, ir.Seq):
        return rebuilt(node)
    if isinstance(node, ir.LongestRun):
        return dataclasses.replace(node, item=rebuilt(node.item))
    if isinstance(node, ir.Recover):
        return dataclasses.replace(node, item=rebuilt(node.item), recovery=rebuilt(node.recovery))
    if isinstance(node, ir.Bind):
        return dataclasses.replace(node, cond=rebuilt(node.cond))  # the condition is the match it binds a value for
    raise TypeError(f"cannot tell which matches {type(node).__name__} holds")


def _lifting(kind):
    """
    The transform giving every `kind` standing inside a way a production of its own, and leaving the call where it
    stood.

    A choice is where the parse decides and a run is where it loops, and a machine does either in a state: standing in
    the middle of a way neither has anywhere to be one, since the state is the production and what a way holds is what
    runs inside it. Minted out, each is a state a call reaches and comes back from, and the way holds an item like any
    other. Naming a run settles nothing about the run itself — it stays the possessive scan it was, and whether it takes
    another turn is a question the gates ask.

    Minting rather than distributing, which is the other way to take a choice out of a sequence: `a (x | y) b` as `a x b
    | a y b` runs `a` twice wherever it takes a character or pushes anything, and a copy of `b` per way is a copy of
    whatever `b` calls. The call costs a push and duplicates nothing.
    """

    def transform(grammar, namer):
        minted = {}

        def item(node, owner):
            if isinstance(node, _LEAF_ITEMS):
                return node
            if isinstance(node, kind):
                name = namer.fresh(owner)
                minted[name] = ir.Prod(
                    grammar[owner].number, name, (), _with_inner_ways(node, lambda way: rebuilt(way, owner))
                )
                return ir.Ref(name=name, args=())
            return _with_inner_ways(node, lambda way: rebuilt(way, owner))

        def rebuilt(node, owner):
            if isinstance(node, ir.Seq):
                return dataclasses.replace(node, items=tuple(item(part, owner) for part in node.items))
            return item(node, owner)

        def body(node, owner):
            if isinstance(node, _BODY_KINDS) and not isinstance(node, ir.Seq):
                return _with_inner_ways(node, lambda way: rebuilt(way, owner))
            return rebuilt(node, owner)

        lifted = {
            name: dataclasses.replace(production, body=body(production.body, name))
            for name, production in grammar.items()
        }
        return {**lifted, **minted}

    return transform


def lower_bind(grammar, namer):
    """
    Write each binding as the match it binds for and the write that follows it: `Bind(cond, param, value)` becomes `cond
    SetVar(param, value)`.

    A binding is not a scope over the match: it matches `cond`, works the value out where that match ends — the digit's
    own text, for the one binding left, which `(atoi)` reads off the run — and writes it. That is a match and then an
    action, and the interpreter says so twice over: what a binding does once its condition has matched is exactly what
    `SetVar` does, down to undoing the write where what follows fails so the condition can try its next way.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Bind):
            return ir.Seq(items=(node.cond, ir.SetVar(param=node.param, value=node.value)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _is_actions_alone(node, grammar, seen=frozenset()):
    """
    Whether `node` is built of actions alone — a way that takes no character and matches wherever it is reached.

    A structural question, and what the commit's hoist rests on: `A (commit m: X)` is `(commit m: A X)` only where `A`
    cannot fail, an `A` that could failing under the hoist with the commit's error rather than by not matching. A choice
    is one where some way is, since that way is the one taken; a recursion reached again is not, having no way of its
    own to answer with.
    """
    if isinstance(node, (*_ACTIONS, ir.Empty)):
        return True
    if isinstance(node, ir.Seq):
        return all(_is_actions_alone(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Alt):
        return any(_is_actions_alone(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Choice):
        return any(_is_actions_alone(way, grammar, seen) for way in node.alternatives)
    if isinstance(node, ir.Alternative):
        return all(_is_actions_alone(item, grammar, seen) for item in _items_of_way(node))
    if isinstance(node, (ir.Token, ir.Wrap)):
        return _is_actions_alone(node.item, grammar, seen)
    if isinstance(node, ir.Ref):
        return node.name not in seen and _is_actions_alone(grammar[node.name].body, grammar, seen | {node.name})
    if isinstance(node, ir.KINDS):
        return False
    raise TypeError(f"cannot tell whether {type(node).__name__} is built of actions alone")


def _does_empty_leave_nothing(node, grammar, ways, seen=frozenset()):
    """
    Whether every way of `node` that takes no character leaves nothing behind — it reads the input and answers, and a
    rewind past it undoes all of it.

    What lets a run over an item that may take nothing drop the zero-width turn `_repeat` keeps: the turn is taken
    either way, and where it leaves nothing the two runs are the same match. Something with no empty way has none to
    answer for; a `(token)` over one that takes no character cuts a run of no characters, which is no token, and a
    `(wrap)`'s markers are tokens whether or not anything is between them.
    """
    if _split(node, grammar, ways)[1] is None:
        return True
    if isinstance(node, (*_GUARDS, ir.ConsumeSpan, ir.ConsumeCountedSpan, ir.Empty)):
        return True
    if isinstance(node, (*_ACTIONS, ir.Bind, ir.Wrap)):
        return False
    if isinstance(node, (ir.Seq, ir.Alt)):
        return all(_does_empty_leave_nothing(item, grammar, ways, seen) for item in node.items)
    if isinstance(node, (ir.Choice, ir.Alternative)):
        return all(_does_empty_leave_nothing(item, grammar, ways, seen) for item in _ways_or_items(node))
    if isinstance(node, (ir.Star, ir.Plus, ir.LongestRun, ir.Rep, ir.Token, ir.Max, ir.Commit, ir.Recover)):
        return node.item is None or _does_empty_leave_nothing(node.item, grammar, ways, seen)
    if isinstance(node, ir.Ref):
        return node.name in seen or _does_empty_leave_nothing(
            grammar[node.name].body, grammar, ways, seen | {node.name}
        )
    raise TypeError(f"cannot tell whether {type(node).__name__} leaves anything behind")


def _is_nullable(node, grammar, ways):
    """
    Whether `node` has a way that takes no character — what `ways` says of a production, said of any node.

    A reading rather than a rewrite: it answers where `_split` refuses to, a commit holding both an empty match and a
    reading one being a shape no split can say as two ways but a perfectly ordinary thing to ask about. Every kind is
    named and one named nowhere raises, an empty match answered for by accident being the whole debt this phase removes.
    """
    if isinstance(node, _ALWAYS_READS):
        return False
    if isinstance(node, (*_ACTIONS, *_GUARDS, ir.ConsumeSpan, ir.Empty)):
        return True
    if isinstance(node, ir.Ref):
        return ways[node.name][1]
    if isinstance(node, ir.Seq):
        return all(_is_nullable(item, grammar, ways) for item in node.items)
    if isinstance(node, ir.Alt):
        return any(_is_nullable(item, grammar, ways) for item in node.items)
    if isinstance(node, ir.Choice):
        return any(_is_nullable(way, grammar, ways) for way in node.alternatives)
    if isinstance(node, ir.Alternative):
        return all(_is_nullable(item, grammar, ways) for item in _items_of_way(node))
    if isinstance(node, ir.Star):
        return True
    if isinstance(node, ir.LongestRun):
        return node.least == 0 or _is_nullable(node.item, grammar, ways)
    if isinstance(node, ir.Plus):
        return _is_nullable(node.item, grammar, ways)
    if isinstance(node, (ir.Rep, ir.ConsumeCountedSpan)):
        # A count the parse works out may be none at all, and then the repetition takes nothing.
        taken = node.item if isinstance(node, ir.Rep) else node.set
        return not isinstance(node.count, ir.Lit) or node.count.value <= 0 or _is_nullable(taken, grammar, ways)
    if isinstance(node, ir.Bind):
        return _is_nullable(node.cond, grammar, ways)
    if isinstance(node, (ir.Token, ir.Wrap, ir.Max, ir.Commit, ir.Recover)):
        return node.item is None or _is_nullable(node.item, grammar, ways)
    raise TypeError(f"cannot tell whether {type(node).__name__} can take nothing")


def _unsplittable_runs(grammar, ways):
    """
    Runs this cannot say as two ways: one over an item that may take nothing, whose zero-width turn leaves something
    behind. The reading way drops that turn, which is the same match only where the turn left nothing.
    """
    return [
        f"{name}: a run takes a turn that takes nothing and leaves something behind"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, (ir.Star, ir.Plus, ir.LongestRun))
        and not _does_empty_leave_nothing(node.item, grammar, ways)
    ]


def _split(node, grammar, ways):
    """
    `(reads, empty)` — `node`'s ways that take a character and its ways that take none, either `None` where it has none
    of them. `ways` says which of the two each production has, so a reference splits by name rather than by walking into
    what it calls.

    The two are `node` itself, said as an ordered choice of the reading ways and then the empty ones, and that is the
    order `node` already tries them in: a sequence's ways come out in the order its parts offer them — `a b` reading is
    `a_reads b` and then `a_empty b_reads`, which enumerates exactly as `a b` does — and an alternation's come out in
    the order it wrote them, no way of the grammar having an empty way ahead of a reading one.
    """
    if isinstance(node, _ALWAYS_READS):
        return node, None
    if isinstance(node, (*_ACTIONS, *_GUARDS, ir.Empty)):
        return None, node
    if isinstance(node, ir.ConsumeSpan):
        # The scan is possessive, so it takes none exactly where the set is not there — which is the question the empty
        # way asks, and the reading way is the character and the run behind it, as a `Plus` over the set is written.
        return ir.Seq(items=(node.set, node)), ir.NegLook(item=as_char_set(node.set, grammar))
    if isinstance(node, ir.Ref):
        reads, empty, apart = ways[node.name]
        if apart:
            return ir.Ref(f"{node.name}_reads", node.args), ir.Ref(f"{node.name}_empty", node.args)
        return (node if reads else None), (node if empty else None)
    if isinstance(node, ir.Seq):
        return _split_seq(node, grammar, ways)
    if isinstance(node, ir.Alt):
        parts = [_split(item, grammar, ways) for item in node.items]
        reads = tuple(way for way, _none in parts if way is not None)
        empty = tuple(none for _way, none in parts if none is not None)
        return (ir.Alt(items=reads) if reads else None), (ir.Alt(items=empty) if empty else None)
    if isinstance(node, (ir.Star, ir.Plus, ir.LongestRun)):
        least = 0 if isinstance(node, ir.Star) else 1 if isinstance(node, ir.Plus) else node.least
        reads, empty = _split(node.item, grammar, ways)
        taking = ir.LongestRun(item=reads, least=1) if reads is not None else None
        if least == 0:
            return taking, ir.Empty()  # a run of none or more takes nothing where the first turn cannot match
        if empty is None:
            return node, None  # the item always reads, so a run that must take a turn does
        # The run ends on a turn that takes nothing, which is kept once — and `_unsplittable_runs` holds that turn to
        # leaving nothing, so the reading way is the reading turns and the empty way is the one that took none.
        return taking, empty
    if isinstance(node, ir.Rep):
        return _split_counted(node, node.item, grammar, ways)
    if isinstance(node, ir.ConsumeCountedSpan):
        return _split_counted(node, node.set, grammar, ways)
    if isinstance(node, ir.Bind):
        reads, empty = _split(node.cond, grammar, ways)
        return (
            (dataclasses.replace(node, cond=reads) if reads is not None else None),
            (dataclasses.replace(node, cond=empty) if empty is not None else None),
        )
    if isinstance(node, (ir.Token, ir.Wrap, ir.Max, ir.Commit, ir.Recover)):
        if node.item is None:
            return None, node  # a `(max)` window with nothing in it: a bound, and no match of its own
        reads, empty = _split(node.item, grammar, ways)
        if isinstance(node, (ir.Commit, ir.Recover)) and reads is not None and empty is not None:
            # Both are the error where the item cannot match, so a reading form of one would raise where the parse
            # should have gone on to the empty form. The body's own commit is lifted off before this; a deeper one has
            # nowhere to be lifted to.
            raise ValueError(f"a {type(node).__name__.lower()} hides both an empty match and a reading one")
        return (
            (dataclasses.replace(node, item=reads) if reads is not None else None),
            (dataclasses.replace(node, item=empty) if empty is not None else None),
        )
    if isinstance(node, (ir.Choice, ir.Alternative)):
        # The canonical form is not split into a reading copy and an empty one — the steps that rewrote by such copies
        # ran before it existed. What is asked of it here is only which of the two it can do, which is what
        # `_split_ways` keeps, so the node stands for whichever it can and nothing rewrites by the answer.
        parts = _ways_or_items(node)
        answers = [_split(part, grammar, ways) for part in parts]
        does_read = any(part is not None for part, _empty in answers)
        does_take_none = (
            any(part is not None for _reads, part in answers)
            if isinstance(node, ir.Choice)
            else all(part is not None for _reads, part in answers)
        )
        return (node if does_read else None, node if does_take_none else None)
    raise TypeError(f"cannot tell what {type(node).__name__} takes")


def _split_seq(node, grammar, ways):
    """
    A sequence's `(reads, empty)`. It reads where any one part does, so the reading ways are one per part that can —
    that part reading and everything before it taking nothing — and the empty way is every part taking none.
    """
    reads, taken = [], []
    for position, item in enumerate(node.items):
        way, none = _split(item, grammar, ways)
        if way is not None:
            reads.append(ir.Seq(items=tuple(taken) + (way,) + node.items[position + 1 :]))
        if none is None:
            return (ir.Alt(items=tuple(reads)) if reads else None), None  # this part always reads: no empty way past it
        taken.append(none)
    return (ir.Alt(items=tuple(reads)) if reads else None), ir.Seq(items=tuple(taken))


def _split_counted(node, taken, grammar, ways):
    """
    A counted repetition's `(reads, empty)` — `taken` being the one turn it takes, a repetition's item or a counted
    scan's set.

    A non-positive count matches nothing at all, so a count the parse works out — the indent scan's, which is the
    indentation in force — is a repetition that takes none, and the two ways are told apart by the count rather than by
    the character.
    """
    _reads, empty = _split(taken, grammar, ways)
    if empty is not None:
        raise ValueError(f"a repetition of `{taken}` takes a turn that may take nothing, and cannot be split")
    if isinstance(node.count, ir.Lit):
        return (node, None) if node.count.value > 0 else (None, ir.Empty())
    # The reading way says the turn it takes rather than leaning on the count that admitted it, so what it is stands in
    # the shape: the count is positive, one turn is taken, and the rest of them follow.
    rest = dataclasses.replace(node, count=ir.Sub(a=node.count, b=ir.Lit(value=1)))
    reading = ir.Seq(items=(ir.Lt(a=ir.Lit(value=0), b=node.count), taken, rest))
    return reading, ir.Le(a=node.count, b=ir.Lit(value=0))


def _lifted_commit(body, grammar):
    """
    `(message, body)` with a commit over the whole of `body` lifted off it, and `(None, body)` where there is none.

    A commit is the error where its item cannot match, which stops a split: one form of it failing would raise where the
    parse should have gone on to the other. Where everything before it takes no character and always matches, `A (commit
    m: X)` and `(commit m: A X)` are the same match — so it comes off, the body splits, and it goes back over the
    choice, one message scope around both ways rather than one around each.
    """
    if isinstance(body, ir.Commit):
        return body.message, body.item
    items = body.items if isinstance(body, ir.Seq) else ()
    if not items or not isinstance(items[-1], ir.Commit):
        return None, body
    if not all(_is_actions_alone(item, grammar) for item in items[:-1]):
        return None, body
    return items[-1].message, ir.Seq(items=items[:-1] + (items[-1].item,))


def _production_split(production, grammar, ways):
    """`(message, reads, empty)` for a production's body, its own commit lifted off the split and named back."""
    message, body = _lifted_commit(production.body, grammar)
    reads, empty = _split(body, grammar, ways)
    return message, reads, empty


def _split_ways(grammar):
    """
    `{name: (reads, empty, apart)}` — whether each production has a way that takes a character, whether it has one that
    takes none, and whether the two are told apart under names of their own.

    A least fixed point, since a reference can reach back to its own production: nothing is taken to match until some
    way of it says so, so a recursion on its own contributes neither. A production the parse enters by name is left
    whole — nobody chooses to enter one, so an empty match there decides nothing, and the root and the recovery reach
    each other, which is a choice on nothing at all once each is two things.
    """
    entered = entered_by_name(grammar)
    ways = {name: (False, False, False) for name in grammar}
    while True:
        settled = {}
        for name, production in grammar.items():
            _message, reads, empty = _production_split(production, grammar, ways)
            is_told_apart = reads is not None and empty is not None and name not in entered
            settled[name] = (reads is not None, empty is not None, is_told_apart)
        if settled == ways:
            return ways
        ways = settled


def mint_consuming_and_residue(grammar, namer):
    """
    Give every production that may match empty a name for each of the two things it is: `<name>_reads`, the ways that
    take a character, and `<name>_empty`, the ways that take none.

    A caller choosing whether to enter such a production is choosing blind — entering it may take nothing at all — and
    the choice cannot be put on a character while both answers live under one name. Each form is a local rewrite of the
    production's own body, and the residue gets a name rather than being spelled inline, so nothing has to be worked out
    bottom-up: a body's parts are split by what their own names already say.

    The production keeps its name and becomes the choice of the two, which is the same match said in the same order. One
    whose ways all take nothing is left whole — there is no choice in it to name — and it is what the phase dissolves
    into its call sites rather than what this step splits.
    """
    ways = _split_ways(grammar)
    unsplittable = _unsplittable_runs(grammar, ways)
    if unsplittable:
        raise AssertionError(f"the empties cannot be named: {'; '.join(unsplittable)}")
    result = {}
    for name, production in grammar.items():
        message, reads, empty = _production_split(production, grammar, ways)
        if not ways[name][2]:
            result[name] = production
            continue
        choice = []
        for form, body in (("reads", reads), ("empty", empty)):
            held = f"{name}_{form}"
            result[held] = ir.Prod(production.number, held, production.params, body)
            choice.append(ir.Ref(held, tuple(ir.Param(carried) for carried in production.params)))
        body = ir.Alt(items=tuple(choice))
        result[name] = dataclasses.replace(
            production, body=body if message is None else ir.Commit(message=message, item=body)
        )
    return result


def _unnamed_empties(grammar):
    """
    Productions whose empty match is not a way of its own — a caller reaching one is choosing whether to enter something
    that may take nothing, with nothing to go on.

    A way that takes no character is allowable where that is all it can do: such a production has no choice in it to
    name, and what becomes of it is the phase's next question rather than this step's. The ways are read through the
    production's own commit, that being one message scope over the choice rather than a way of it.
    """
    ways = _split_ways(grammar)
    faults = []
    for name, production in grammar.items():
        if not ways[name][2]:
            continue
        _message, body = _lifted_commit(production.body, grammar)
        for way in _offered(body):
            reads, empty = _split(way, grammar, ways)
            if reads is not None and empty is not None:
                faults.append(f"{name}: offers a way that takes a character and a way that takes none, as one")
    return faults


def _offered(body):
    """
    The ways `body` offers at its top — an alternation's, and its own where it is not one.

    A `Choice` is the canonical form's alternation and has no business here, so it raises rather than reading as one
    way; so does a kind named nowhere, a new way of offering ways being exactly what would go unread.
    """
    if isinstance(body, ir.Alt):
        return [offered for item in body.items for offered in _offered(item)]
    if isinstance(body, ir.Choice):
        return [offered for way in body.alternatives for offered in _offered(way)]
    if isinstance(body, (ir.Case, ir.Flip)):
        raise TypeError(f"a {type(body).__name__} offers ways, and this reads only an alternation's")
    if isinstance(body, ir.KINDS):
        return [body]
    raise TypeError(f"cannot tell what ways {type(body).__name__} offers")


EMPTIES_NAMED = Invariant("every-empty-match-is-a-way", _unnamed_empties)


def distribute_residues(grammar, namer):
    """
    Put the choice between a production's two ways where its caller stands, so a character can decide it.

    A production that says its ways under names of their own still holds the choice behind one name, and a caller
    reaching it enters without knowing whether anything will be taken. Written at the call site — `A ::= F (X_reads |
    X_empty)` — the choice stands where the parse is, which is where a gate can go on it. The way around it is not split
    to do that: `A ::= F X_reads | F` would run `F` twice, where one alternation inside the sequence duplicates nothing.

    One pass and no iteration. What such a production's body holds is the two calls and nothing else, so a choice
    written into a caller carries no further call of one in with it.
    """
    ways = _split_ways(grammar)
    held = {name: production for name, production in grammar.items() if ways[name][2]}

    def distributed(node):
        node = ir.rebuilt(node, distributed)
        if isinstance(node, ir.Ref) and node.name in held:
            return _bound(held[node.name].body, dict(zip(held[node.name].params, node.args)))
        return node

    return {
        name: production if name in held else dataclasses.replace(production, body=distributed(production.body))
        for name, production in grammar.items()
    }


def _blind_calls(grammar):
    """
    Calls that enter a production which may take a character and may take none — a choice made with nothing to go on,
    what is entered deciding for itself whether anything is consumed.

    The root and the recovery reach each other and a parse enters both by name, so neither is told apart and the calls
    between them are no part of this count: nobody chooses to enter one, and each being two things would make the other
    a choice on nothing at all.
    """
    ways = _split_ways(grammar)
    return [
        f"{name}: calls `{node.name}`, which may take a character and may take none"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Ref) and ways[node.name][2]
    ]


CALLS_DECIDED = Invariant("no-call-enters-both-ways", _blind_calls)


def dissolve_residues(grammar, namer):
    """
    Write every production that takes no character into the call sites that enter it, so nothing is reached by a name
    that stands for a match of nothing.

    What is left matching empty once the two ways are told apart is what only ever took none: the residue a split named,
    and the productions that were actions alone — `e-node`, a pair of markers around an empty scalar, at twenty-six call
    sites. A name is worth having where it stands for a decision, and there is none in a way that consumes nothing and
    always ends where it began; written where it is entered, the caller's own way says what it does and no call is made
    on the chance that it takes nothing.

    Each is written out before it is written in, a residue holding calls of others. One reaching itself would be a match
    of nothing at all rather than a match of nothing, and the grammar has none.
    """
    ways = _split_ways(grammar)
    entered = entered_by_name(grammar)
    dissolved = {name: grammar[name].body for name in grammar if name not in entered and ways[name][1]}

    def written(node, into):
        node = ir.rebuilt(node, lambda child: written(child, into))
        if isinstance(node, ir.Ref) and node.name in into:
            return _bound(into[node.name], dict(zip(grammar[node.name].params, node.args)))
        return node

    for _round in range(len(dissolved) + 1):
        settled = {name: written(body, dissolved) for name, body in dissolved.items()}
        if settled == dissolved:
            break
        dissolved = settled
    else:
        raise AssertionError("a production that takes no character reaches itself, and cannot be written out")

    return {
        name: (
            production
            if name in dissolved
            else dataclasses.replace(production, body=written(production.body, dissolved))
        )
        for name, production in grammar.items()
    }


def _blind_empties(grammar):
    """
    Productions that match empty and that a caller chooses whether to enter — each one a decision made with no character
    to go on, since what is entered may take nothing at all.

    A parse enters the root and the recovery by name rather than by a call, so an empty match there decides nothing and
    neither is one of these. Everything else that could match empty is now a way of the caller's own, where a gate can
    be put on it.
    """
    ways = _split_ways(grammar)
    entered = entered_by_name(grammar)
    return [
        f"{name}: matches empty, and a caller chooses whether to enter it"
        for name in grammar
        if name not in entered and ways[name][1]
    ]


ONLY_ROOT_EMPTIES = Invariant("only-root-empties", _blind_empties)


# What the runs become, and what they must not become. A repetition is the last thing in the grammar that is neither a
# production nor a choice, and every reading past this one — the calls a way holds, the characters it can begin with,
# the gate over them — is written against those two; a run said as a production of its own is what makes them uniform.
def _entered_unconsumed(node, grammar, ways):
    """
    The productions `node` can enter with nothing taken — its left corner, as names.

    A guard is followed like anything else: it is tested where the parse stands, so what it reaches is reached at that
    same position. A recovery is not: it is entered where an abandoned parse stopped rather than where its rule began,
    so a `(recover)` contributes what its item does and nothing more.
    """
    if isinstance(node, ir.Ref):
        return {node.name}
    if isinstance(node, (ir.Alt, ir.Choice)):
        return {name for item in _ways_or_items(node) for name in _entered_unconsumed(item, grammar, ways)}
    if isinstance(node, ir.Alternative):
        reached = set()
        for item in _items_of_way(node):
            reached |= _entered_unconsumed(item, grammar, ways)
            if not _is_nullable(item, grammar, ways):
                break
        return reached
    if isinstance(node, ir.Seq):
        reached = set()
        for item in node.items:
            reached |= _entered_unconsumed(item, grammar, ways)
            if not _is_nullable(item, grammar, ways):
                break  # this part always reads, so nothing past it is entered where the parse still stands
        return reached
    if isinstance(node, ir.Bind):
        return _entered_unconsumed(node.cond, grammar, ways)
    if isinstance(node, _WALKED_UNCONSUMED):
        return set() if node.item is None else _entered_unconsumed(node.item, grammar, ways)
    if isinstance(node, (*_ALWAYS_READS, *_ACTIONS, *_GUARDS, ir.ConsumeSpan, ir.ConsumeCountedSpan, ir.Empty)):
        return set()  # a character question or a zero-width action reaches no production where it stands
    raise TypeError(f"cannot tell what {type(node).__name__} enters where it stands")


# The kinds whose item is entered where they are: a run and a repetition take their first turn there, a scope and a
# commit their content, and a lookaround tests at the position it stands at. In alphabetical order.
_WALKED_UNCONSUMED = (
    ir.Commit,
    ir.ExcludeAt,
    ir.LongestRun,
    ir.Look,
    ir.LookBehind,
    ir.Max,
    ir.NegLook,
    ir.Plus,
    ir.Recover,
    ir.Rep,
    ir.Star,
    ir.Token,
    ir.Wrap,
)


def _unconsumed_cycles(grammar):
    """
    Productions that reach themselves with nothing taken — a parse that arrives there cannot go on.

    The machine being built is a pushdown that commits to the first gate that fires and never backtracks, so it has no
    way to notice it is where it already was: a production reaching itself at the same position runs for ever. Nothing
    absorbs it the way an LR construction would, which is why this is a fault and not a shape to handle.

    One cycle is the grammar's own and is exempt: the stream and the recovery are mutually recursive by design under a
    resuming policy — `l-recover` is `l-unparsed` and then the stream again, which is what lets a resumed document fail
    again without a second mechanism for it. It terminates because the only way round it is through an `l-unparsed` that
    takes nothing, and that happens where the next line is a document boundary or the input has ended: at a boundary the
    stream consumes the `---` or `...` itself, and at the end `<end-of-stream>` answers. So a second recovery costs a
    character, and the pair cannot go round without one.

    The exemption is the cycle's rather than a name's, since anything minted out of a body on it stands on it too: a
    path through what a parse enters by name is cut, and what still reaches itself is a cycle of the grammar's own
    making and a fault. Under `r=n` there is no such cycle at all, the recovery bringing back the unparsed text and
    stopping.
    """
    ways = _split_ways(grammar)
    entered = entered_by_name(grammar)
    edges = {
        name: {reached for reached in _entered_unconsumed(production.body, grammar, ways) if reached not in entered}
        for name, production in grammar.items()
    }
    reach = dict(edges)
    while True:
        grown = {name: held | {far for near in held for far in edges.get(near, ())} for name, held in reach.items()}
        if grown == reach:
            break
        reach = grown
    return [
        f"{name}: reaches itself with nothing taken, and a parse that arrives there cannot go on"
        for name in grammar
        if name in reach[name]
    ]


NO_UNCONSUMED_CYCLE = Invariant("no-production-reaches-itself-unconsumed", _unconsumed_cycles)


def hold_established_indents(grammar, namer):
    """
    Make an established indentation something the parse stands under rather than something a call hands back.

    A block scalar cannot know what its content is indented by until its first content line is read, so that line
    measures it and the value travels back out through every call that passed the parameter itself. That is a write
    whose readers are a production away, which nothing local can check and the parameter's removal would silently take
    apart. So the chain is inlined until the write and what reads it are one way, and the write is then the push that
    way ends by taking back.

    Inlining is what makes the pair local: the callee is entered under the caller's own indentation — the argument is
    the parameter — so its body says the same thing spliced in as it did called, and the value it left in `n` is left in
    the same `n`.
    """

    def does_establish(node):
        """Whether `node` is a call whose production hands an indentation back to this one."""
        return isinstance(node, ir.Ref) and node.name in _establishing(grammar) and _is_by_reference(grammar, node)

    def spliced(items):
        """
        `items` with each call that hands an indentation back replaced by what that production does — and again on what
        that brings in, since the one that establishes may be a call further down the chain.
        """
        while any(does_establish(item) for item in items):
            held = []
            for item in items:
                if not does_establish(item):
                    held.append(item)
                    continue
                body = grammar[item.name].body
                held += list(body.items) if isinstance(body, ir.Seq) else [body]
            items = tuple(held)
        return items

    def bounded(items):
        """`items` with a write of the indentation made the push its way takes back."""
        for position, item in enumerate(items):
            if isinstance(item, ir.SetVar) and item.param == "n":
                rest = bounded(items[position + 1 :])
                return items[:position] + (ir.PushIndent(level=item.value),) + rest + (ir.PopIndent(level=None),)
        return items

    def held(node):
        node = ir.rebuilt(node, held)
        return ir.Seq(items=bounded(spliced(node.items))) if isinstance(node, ir.Seq) else node

    return {name: dataclasses.replace(production, body=held(production.body)) for name, production in grammar.items()}


def _written_indents(grammar):
    """
    Writes of the indentation — a value a call hands back to whatever asked, rather than one the parse stands under.

    What reads such a write is a production away from where it happens, so nothing local says where the value's region
    is, and the parameter carrying it out is the only thing holding it together.
    """
    return [
        f"{name}: writes the indentation, which its caller must read back"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.SetVar) and node.param == "n"
    ]


INDENTS_HELD = Invariant("no-indentation-write", _written_indents)


def push_indents(grammar, namer):
    """
    Say where the indentation changes, so the parse stands under it rather than a call carrying it.

    A call measured against an indentation other than the one in force is entered under it and gives it back where it
    returns, so the push goes before the call and the pop behind it — the pair inside one way of one production, the
    level being known nowhere else. A call handing the parameter itself is entered under what already stands and pushes
    nothing; an indentation a call would have established is already a push, `hold-established-indents` having made it
    one.

    The parameter stays beside the stack: the run holds the two to each other, every read of `n` comparing the stack
    against it, so what says the pushes stand where they should is the corpus rather than an argument.
    """

    def pushed(node):
        node = ir.rebuilt(node, pushed)
        level = _pushed_level(grammar, node)
        return node if level is None else ir.Seq(items=(ir.PushIndent(level=level), node, ir.PopIndent(level=None)))

    return {name: dataclasses.replace(production, body=pushed(production.body)) for name, production in grammar.items()}


def _establishing(grammar):
    """
    The productions that hand an indentation back to their caller: the ones writing it, and the ones calling those with
    the parameter itself, which is what carries the write out. A least fixpoint, a call chain establishing through it.

    A block scalar's first content line is where this begins — its indentation is what the whole scalar is measured
    against, and the scalar cannot know it before that line is read. So the value arrives by the call returning rather
    than by the call being made, and the region it holds for is what follows the call.
    """
    names = {
        name
        for name, production in grammar.items()
        if any(isinstance(node, ir.SetVar) and node.param == "n" for node in _held(production.body))
    }
    while True:
        carried = {
            name
            for name, production in grammar.items()
            for node in _held(production.body)
            if isinstance(node, ir.Ref) and node.name in names and _is_by_reference(grammar, node)
        }
        if carried <= names:
            return names
        names |= carried


def _is_by_reference(grammar, node):
    """Whether the call `node` hands the indentation itself, which is what lets a callee's write reach the caller."""
    callee = grammar.get(node.name)
    if callee is None or "n" not in callee.params:
        return False
    position = callee.params.index("n")
    argument = node.args[position] if position < len(node.args) else None
    return isinstance(argument, ir.Param) and argument.name == "n"


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
    # Phase 0 establishes `NO_I_T_PARAMETERS`: nothing declares, passes or reads the chomping or the block scalar's
    # indentation mode. Each is data-dependent until this runs, so neither can be specialized: the setters become
    # switches first.
    Step("lift-setters", lift_setters, FINITE_LEXICAL, reduces=FINITE_LEXICAL),
    Step(
        "monomorphize", monomorphize, (_absent("no-context-case", ir.Case, ir.Flip), FINITE_LEXICAL, NO_I_T_PARAMETERS)
    ),
    # Phase 1 establishes `ONLY_SETS_AND_LITERALS`: a question about a character is a `CharSet`. A set the context picks
    # denotes nothing until the specialization has bound the context, so this follows Phase 0. The difference is taken
    # into the ways it subtracts from first, since a subtraction says a set only where both of its sides do.
    Step("distribute-differences", distribute_differences, DIFFERENCES_BETWEEN_SETS),
    Step("lower-char-sets", lower_char_sets, (ONLY_SETS_AND_LITERALS, NO_DIFF_NODES, PEEKS_ARE_SETS)),
    # Phase 2 establishes `NO_F_PARAMETER`: the block scalar's leading-empty floor is the parse's one value rather than
    # one a call carries. The value is given an end first, a single slot answering for a parameter only where a read
    # past the region it was measured in is refused rather than answered from what the last construct left.
    Step("clear-f", _clear_reads("f"), _unbounded_reads("f")),
    Step("read-global-f", _read_off("f", ir.Global(name="f")), NO_F_PARAMETER),
    # Phase 3 establishes `NO_M_PARAMETER`: the detected indent is the parse's one value. Nothing reads it twice over a
    # region something else can write in — the block header measures it and the scalar that asked reads it, one
    # construct at a time — so a clear and a drop are the whole of it.
    Step("clear-m", _clear_reads("m"), _unbounded_reads("m")),
    Step("read-global-m", _read_off("m", ir.Global(name="m")), NO_M_PARAMETER),
    # Phase 4 establishes `NO_N_PARAMETER`: the indentation is on the parse's own stack rather than carried by a call.
    # The pushes go in first and the parameter stays beside them, so what says they stand where they should is every
    # read comparing the two over the corpus; dropping the parameter is what leaves the stack the one place it is.
    Step("hold-established-indents", hold_established_indents, INDENTS_HELD),
    Step("push-indents", push_indents, INDENTS_PUSHED),
    Step("read-indents", _read_off("n", ir.Indent()), NO_N_PARAMETER),
    # Phase 5 is the empties, and what it is finished by is no production matching empty but the ones a parse enters by
    # name. This step is the first of it: an empty match is a way beside the one that reads, not a node hiding one.
    Step("lower-optionals", lower_optionals, NO_OPT_NODES),
    Step(
        "span-consumes",
        span_consumes,
        (CHARACTER_RUNS_SCANNED, NO_STAR_OR_PLUS_NODES),
        reduces=NO_STAR_OR_PLUS_NODES,
    ),
    Step("lower-runs", lower_runs, (NO_STAR_OR_PLUS_NODES, NO_UNCONSUMED_CYCLE)),
    Step("mint-consuming-and-residue", mint_consuming_and_residue, EMPTIES_NAMED),
    Step("distribute-residues", distribute_residues, CALLS_DECIDED),
    Step("dissolve-residues", dissolve_residues, ONLY_ROOT_EMPTIES),
    # Phase 6 takes the scopes off what they cover, each step one kind, and every one of them is held to the pairs it
    # leaves closing where they open — the guarantee a wrapper gave by construction, now a count.
    Step("lower-wraps", lower_wraps, (NO_WRAP_NODES, SCOPES_CLOSED)),
    Step("lower-windows", lower_windows, (NO_MAX_NODES, SCOPES_CLOSED)),
    Step("lower-commits", lower_commits, (NO_COMMIT_NODES, SCOPES_CLOSED)),
    Step("lower-tokens", lower_tokens, (NO_TOKEN_NODES, SCOPES_CLOSED)),
    # Phase 7 takes the tree apart: an item standing in a way is something the machine does where it stands, and every
    # shape holding a match inside it becomes a production of its own. The steps reduce `no-item-holds-a-match` between
    # them, each settling its own share of it.
    Step(
        "lift-choices",
        _lifting(ir.Alt),
        (ITEMS_ARE_LEAVES, CHOICES_ARE_BODIES),
        reduces=ITEMS_ARE_LEAVES,
        lapses=dict.fromkeys(
            ("every-empty-match-is-a-way", "no-call-enters-both-ways", "only-root-empties"),
            "a choice between reading and taking nothing becomes a production where it is a state, and a call reaches "
            "it: what phase 5 wrote at the call site because nothing could gate it there, the gates answer for where "
            "it now stands",
        ),
    ),
    Step(
        "lift-runs",
        _lifting(ir.LongestRun),
        (ITEMS_ARE_LEAVES, RUNS_ARE_BODIES),
        reduces=ITEMS_ARE_LEAVES,
        lapses=dict.fromkeys(
            ("every-empty-match-is-a-way", "no-call-enters-both-ways", "only-root-empties"),
            "a run of none or more is the same choice under another name — take a turn or take none — and naming it "
            "puts that choice behind a call, where the loop state is; the turn is a character's to decide and the "
            "gates are what decide it",
        ),
    ),
    Step(
        "lift-recoveries",
        _lifting(ir.Recover),
        (ITEMS_ARE_LEAVES, RECOVERIES_ARE_BODIES),
        reduces=ITEMS_ARE_LEAVES,
    ),
    Step("lower-bind", lower_bind, (ITEMS_ARE_LEAVES, NO_BIND_NODES)),
    Step("bound-exclusions", bound_exclusions, EXCLUSIONS_ARE_BOUNDED, reduces=EXCLUSIONS_ARE_BOUNDED),
    # Phase 8 is the call: a way does its actions, hands control to one production, and says where to carry on when it
    # comes back. What stood past the call is what carries on.
    Step(
        "mint-continuations",
        mint_continuations,
        WAYS_ARE_CALL_AND_CONTINUATION,
        lapses=dict.fromkeys(
            ("every-empty-match-is-a-way", "no-call-enters-both-ways", "only-root-empties"),
            "what a way does past its call is a production of its own, and one carrying actions alone takes no "
            "character: the canonical form mints those deliberately, a continuation being where the parse carries on "
            "rather than a choice anything makes",
        ),
    ),
    # Phase 9 says every body in the machine's own words: a set of characters, a run over the state it repeats, or the
    # ordered list of alternatives one of which the parse takes.
    Step("call-run-turns", call_run_turns, RUN_TURNS_ARE_CALLS),
    Step("build-alternatives", build_alternatives, BODIES_ARE_STATES),
    # Phase 10 is the gate: a way is entered on the character in front of it, which is what a machine that never
    # backtracks chooses by. The hoists reduce `every-way-gated` between them.
    Step(
        "gate-hoist",
        gate_hoist,
        (WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
        reduces=(WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
    ),
    Step(
        "gate-hoist-call",
        gate_hoist_call,
        (WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
        reduces=(WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
    ),
    Step(
        "hoist-past-actions",
        hoist_past_actions,
        (WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
        reduces=(WAYS_ARE_GATED, DECISIONS_GO_ON_A_CHARACTER),
    ),
    Step("hoist-guards", hoist_guards, WAYS_ARE_GATED, reduces=WAYS_ARE_GATED),
    Step(
        "splice-conflicts",
        splice_conflicts,
        CONFLICTS_CAN_BE_ASKED,
        reduces=CONFLICTS_CAN_BE_ASKED,
        lapses=dict.fromkeys(
            (
                "every-decision-goes-on-a-character",
                "every-empty-match-is-a-way",
                "every-way-gated",
                "no-call-enters-both-ways",
                "only-root-empties",
            ),
            "a conflict spliced where it was called is that conflict once per site, each in the context that reaches "
            "it: the copies are what the walk can finally be asked about, and what it says of them is that a character "
            "decides all but a handful, so the counts follow the copies rather than the work",
        ),
    ),
    Step(
        "hoist-past-actions-3",
        hoist_past_actions,
        WAYS_ARE_GATED,
        reduces=WAYS_ARE_GATED,
    ),
]
