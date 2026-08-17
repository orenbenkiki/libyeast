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
every one of them as the intervals it denotes, after which each question about a character is a `CharSet` —
`every-character-question-is-a-character-set` at none. It follows the specialization because a set a context parameter
picks denotes nothing until a caller is known.

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
`dissolve-residues` writes what is left taking no character into the call sites that enter it. Nothing anything decides
to enter can match empty — `no-conditional-production-matches-empty` at none, the root and the recovery keeping their
empty ways, having no call site to hold the choice, and a continuation being where a way carries on rather than
something chosen.

Phase 6 is the wrappers. A scope that holds what it covers has nowhere to stand in an alternative, which has a place for
an action and none for a node enclosing a call, so each becomes the pair that brackets it: `lower-wraps` writes a
`(wrap)` as its two markers, `lower-windows` a `(max)` as the window pair, `lower-commits` a `(commit)` as the message
pair, and `lower-tokens` a `(token)` as the code pair. What a wrapper guaranteed by construction the pairs are held to
instead: each half carries the pair it belongs to, and the parse is what holds them to it — the interpreter refuses a
close whose pair does not meet the open standing on its stack. A `(recover)` is a handler rather than a scope and stays,
its home being the edge an alternative rides.

**A reading that dispatches on node kinds names every kind it accepts and raises on the rest.** Never a trailing default
— no `return None`, `return True`, `continue` or `break` catching a kind nobody thought about. A default answer for a
spelling the reading does not recognise is not caution: it reports the reading's own blindness as a fact about the
grammar, and every count taken from it is wrong quietly and plausibly. `_entry_of`, `_is_nullable` and `_split` each end
in a raise for that reason, and so must anything written beside them.

**And one reading per question.** What characters can be in front of a match is `_entry_of`; whether a match can take
nothing is `_is_nullable`; what its reading and empty halves are is `_split`. A step or a check that needs one of these
calls it rather than walking the grammar again — two readings of one question drift, and the drift shows up as a count
that moves where nothing about the grammar did.
"""

import dataclasses
import inspect
import os

import annotated2ir
import chars
import gate
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

    A test is a question about a grammar and never a change to one, which is what lets the counting pass hand the same
    grammars to every invariant at once, in whatever order the cores take them. The grammar is read before and after to
    hold it to that: a production is frozen, so the only way one can differ is by having been put there, and a test that
    puts one there is answering about a grammar nothing else was asked about.
    """

    name: str
    test: object

    def __call__(self, grammar, points=None):
        does_want_points = len(inspect.signature(self.test).parameters) > 1
        held = {name: id(production) for name, production in grammar.items()}
        try:
            return self.test(grammar, points) if does_want_points else self.test(grammar)
        finally:
            # Held even where the test raises: a grammar a reading could not answer about is one the next invariant is
            # handed all the same, so a change left behind on the way out is the one nothing would otherwise see.
            if {name: id(production) for name, production in grammar.items()} != held:
                raise AssertionError(f"`{self.name}` changed the grammar it was asked about")


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
    `foo` with any `_<N>` suffix stripped. Two steps minting for the same base do not collide. It mints the pairs the
    scope actions carry as well, on one count for the whole pipeline rather than per base, and carries the pipeline's
    `Points` too — the points of interest tracked across the steps it names.

    A name is never handed out twice, and never one a grammar it has been shown already holds. The count alone was not
    enough for that: a namer that had not been told about a grammar would hand back `foo_1` over the `foo_1` standing in
    it, and the minting would replace a production rather than add one — silently, since a step writes what it mints
    into the same dict. Every grammar the namer is shown is taken into `_taken`, so the count skips what is there.
    """

    def __init__(self):
        self._counts = {}
        self._taken = set()
        self._pairs = 0
        self.points = Points()

    def sees(self, grammar):
        """Take `grammar`'s names as ones never to hand out — what keeps a fresh name from replacing a production."""
        self._taken.update(grammar)

    def pair(self):
        """
        A fresh pair, as the single-pair set both its halves carry.

        One count for every pair there is: not one per kind, not one per step, and not one per base as the names are — a
        name has only to be unlike the names of its own base, where a pair has to be unlike every other pair. So no two
        pairs anywhere share an identifier, and a `PushCode`'s can never be a `PushIndent`'s even by accident, which
        leaves the kinds as a second thing to disagree on rather than the only one.
        """
        self._pairs += 1
        return frozenset({self._pairs})

    def fresh(self, owner):
        """A fresh `<base>_<N>` name for a helper of `owner`, the base being `owner` without its `_<N>` suffix."""
        head, _underscore, tail = owner.rpartition("_")
        base = head if tail.isdigit() and head else owner
        while True:
            self._counts[base] = self._counts.get(base, 0) + 1
            minted = f"{base}_{self._counts[base]}"
            if minted not in self._taken:
                self._taken.add(minted)
                return minted


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

    An invariant is a count and not a yes-or-no, and a step says outright which of the three it does to each. `settles`
    names the invariants it takes to none, having been handed them broken — a step often makes more than one thing true,
    and `span-consumes` leaves both every character run a span and every exclusion bounded. `reduces` names the ones it
    only lowers the count of, for a count several steps share: the gate hoists do, and no one of them leaves none.
    `establishes` names the ones that read none of what it hands on and were no question before it: the shape they are
    about is what this step builds, so what stood in front of it is not a grammar they could have been asked of. None of
    the three is read off the others, so a declaration says exactly what the step does rather than what is left over. An
    `Invariant` and a `transform` are both shared where two steps do the same work on different grounds.

    `lapses` is what this step is allowed to break: `{invariant: reason}`, empty for nearly every step, holding a
    written reason where a step undoes something an earlier one settled. `invariant_faults` holds the pipeline to the
    law — a count never rises, a settling step leaves none, and none stays none — and reads a lapse as the one licence
    to break it. It also holds every one of those three declarations to what the counts say happened, so a step claiming
    to settle what it only lowers, to lower what it settles, or to break what it leaves alone is a fault where it
    stands.

    An invariant goes by its name, so two steps naming the same one are working a single count and the set of them is
    collected by name rather than by how many steps mention it. `settles` and `reduces` are both given the invariant
    itself for that reason: the name is what they are compared by, and a name written out here instead would be a second
    spelling of it that nothing resolves — a misspelt one would read as a step that reduces nothing, which is to say as
    one that settles what it does not.

    **Naming neither is temporary, and `untestable` says why one names neither for good.** A step with neither
    transforms the grammar and promises something nothing checks, which is the shape every hard day here has started
    from, and `untested_steps` counts those. Some steps genuinely have nothing standing to test — what they make true is
    momentary, or is a property of a run rather than of a shape — and each says so in a sentence rather than sitting in
    a count that can never reach none. Naming both is a fault: a step either has an invariant or a reason.

    A `transform` of `None` makes the step a **claim**: it does nothing to the grammar and only says that where it
    stands, what it names reads none. That is how a property the pipeline is handed rather than makes is written down —
    tying it to whichever step happens to run next would read as that step establishing it, and the first step to break
    it would then be blamed on the wrong side of the line. A claim `establishes` what it names, being a step that
    changes nothing and so can only be saying where a question starts being asked.
    """

    name: str
    transform: object = None
    settles: object = ()
    reduces: object = ()
    establishes: object = ()
    lapses: dict = dataclasses.field(default_factory=dict)
    untestable: str = ""

    def __post_init__(self):
        for field in ("settles", "reduces", "establishes"):
            named = getattr(self, field)
            held = (named,) if isinstance(named, Invariant) else tuple(named)
            object.__setattr__(self, field, held)

    def does_settle(self, invariant):
        """Whether this step says it takes `invariant`'s count to none."""
        return invariant.name in {held.name for held in self.settles}

    def does_establish(self, invariant):
        """Whether this step says `invariant` holds of what it hands on, and was no question before it."""
        return invariant.name in {held.name for held in self.establishes}

    def does_finish(self, invariant):
        """Whether this step says `invariant` reads none of what it hands on, however it came to."""
        return self.does_settle(invariant) or self.does_establish(invariant)

    def does_reduce(self, invariant):
        """Whether this step says it lowers `invariant`'s count without finishing it."""
        return invariant.name in {held.name for held in self.reduces}

    @property
    def is_a_claim(self):
        """Whether the step only says what it names holds where it stands, leaving the grammar as it found it."""
        return self.transform is None

    @property
    def invariants(self):
        """Every invariant this step names — settled, established and only lowered alike."""
        return (*self.settles, *self.reduces, *self.establishes)

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


def _finite_parameters_are_always_literal(grammar):
    """Check that every call hands each finite parameter a literal value."""
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.Ref):
                callee = grammar.get(node.name)
                for param in ir.FINITE_PARAMS:
                    if callee is None or param not in callee.params:
                        continue
                    position = callee.params.index(param)
                    if position < len(node.args) and not isinstance(node.args[position], ir.Lit):
                        faults.append(f"{owner}: hands `{node.name}` a `{param}` that is not a literal")
            ir.rebuilt(node, lambda child: (walk(child, owner), child)[1])

        walk(production.body)
    return faults


FINITE_PARAMETERS_ARE_ALWAYS_LITERAL = Invariant(
    "finite-parameters-are-always-literal", _finite_parameters_are_always_literal
)


def untested_steps():
    """
    The steps naming neither an invariant nor a reason for having none — each promising what nothing checks.

    Driven to none: at none, `Step.invariants` loses its default and a step must name one or say why it cannot. Until
    then this is the honest measure of how much of the pipeline rests on nothing but the corpus. A step with a written
    `untestable` is not one of these; what it claims is on the record and answerable.
    """
    return [step.name for step in STEPS if not step.invariants and not step.untestable]


def _one_count(held, asked):
    """The count for one `(stage, invariant)` pair, run in a worker."""
    stages, by_name, points = held
    at, named = asked
    return _counted(by_name[named], stages[at][1], points)


def _counted_over(stages, by_name, points, asked=None):
    """
    `{(stage, invariant): count}` — each invariant asked of the stages it is a question of, shared out over the cores.

    Each ask is a pure question about a grammar already built, so they are independent and there are some eighteen
    hundred of them, which is half of what this check spends. `asked` names the `(stage, invariant)` pairs to put; the
    whole cross-product where nothing says otherwise, which only a caller that knows every invariant answers of every
    stage may want.
    """
    if asked is None:
        asked = [(at, named) for at in range(len(stages)) for named in by_name]
    ir.say(f"    counting {len(by_name)} invariant(s) over {len(stages)} stage(s), {os.cpu_count()} at a time")
    counts = gate.spread(
        _one_count, (stages, by_name, points), asked, named=lambda pair: f"[{stages[pair[0]][0]}] {pair[1]}"
    )
    return dict(zip(asked, counts))


def _counted(invariant, grammar, points):
    """
    How many places `grammar` breaks `invariant`.

    Asked only of the stages the invariant is a question of, which is from the step that carries it onward. A reading
    raises on a shape it was never told about, and a grammar the step behind has not yet reshaped holds plenty — so an
    invariant asked in front of its own step raises here rather than answering, and what says where to start asking is
    the step's own `establishes`.
    """
    return len(invariant(grammar, points))


def invariant_faults(stages, points=None):
    """
    Where the pipeline breaks its own law, as error strings — empty where it holds.

    Each step's `test` counts the places its invariant is broken, and the law over the stages is: the count never rises,
    the step that settles it leaves none, and past that it stays none. A step whose `lapses` names the invariant is
    licensed to break it and says why; a step that breaks it without one is a fault named where it stands.

    The invariant is named by its test, so several steps reducing one count are read as one law. A count is measured
    from the first stage whose step names it, the stages before it being no business of the invariant's.

    A step names an invariant only where it settles it, only lowers it, or claims it. One naming an invariant that was
    already none when it was handed the grammar is doing none of the three: it establishes nothing, and the law would
    read the first step behind it to break the invariant as the one at fault. Such a property is written as a claim,
    which says where it holds without pinning it on a step that did not make it hold.

    Every count is taken once, into `standing`, and the laws below are comparisons over it. Each is a reading over a
    whole grammar, and the laws want the same ones — a count never rising, a step taking one to none, a step lowering
    one it does not name — so taking them where they are wanted read each grammar five times over.
    """
    faults, taken = [], set()
    by_name = {held.name: held for step in STEPS for held in step.invariants}
    # Where each invariant starts being a question: the stage handed to the first step that carries it, or — where that
    # step establishes it — the stage it hands on, the shape it is about being what that step builds. Asked in front of
    # that, a reading meets what the pipeline has not reshaped yet and raises, which is a fault and not a count.
    asking = {}
    for named in by_name:
        first = min(index for index, step in enumerate(STEPS) if named in step.carries)
        asking[named] = first + 1 if STEPS[first].does_establish(by_name[named]) else first
    standing = _counted_over(
        stages, by_name, points, [(at, named) for named, first in asking.items() for at in range(first, len(stages))]
    )
    for index, step in enumerate(STEPS):
        if step.invariants and step.untestable:
            faults.append(f"[{step.name}] names an invariant and says it has none — one or the other")
        if step.is_a_claim and not step.invariants:
            faults.append(f"[{step.name}] transforms nothing and names nothing, so it says nothing at all")
        if step.is_a_claim and step.lapses:
            faults.append(f"[{step.name}] transforms nothing and declares a lapse, which is a licence to break")
        for named in step.lapses:
            if named not in by_name:
                faults.append(f"[{step.name}] declares a lapse of `{named}`, which no step carries")
        for held in step.reduces:
            if held.name not in step.carries:
                faults.append(f"[{step.name}] says it only lowers `{held.name}`, which it does not carry at all")
        for held in step.invariants:
            # `establishes` is the one that says nothing about what stood before it, so an already-none count is what it
            # expects rather than a sign it is claiming work it did not do.
            if step.is_a_claim or held in step.reduces or step.does_establish(held):
                continue
            if standing.get((index, held.name)) == 0:
                faults.append(
                    f"[{step.name}] settles `{held.name}`, which was already none when it was handed the grammar — a "
                    f"claim rather than a step, and a step behind it is where the break would be blamed"
                )
    for named in sorted(by_name):
        test = by_name[named]
        first = min(index for index, step in enumerate(STEPS) if named in step.carries)
        is_settled, held = False, None
        for index in range(first, len(STEPS)):
            step, label = STEPS[index], stages[index + 1][0]
            count = standing[index + 1, named]
            is_licensed = named in step.lapses
            is_broken = (
                (held is not None and count > held) or (is_settled and count) or (step.does_finish(test) and count)
            )
            if is_broken and is_licensed:
                taken.add((step.name, named))
            if held is not None and count > held and not is_licensed:
                faults.append(f"[{label}] `{named}` rises from {held} to {count}, and the step declares no lapse")
            if is_settled and count and not is_licensed:
                faults.append(f"[{label}] `{named}` is settled and stands at {count}, and the step declares no lapse")
            if step.does_finish(test) and count and not is_licensed:
                faults.append(f"[{label}] settles `{named}` and leaves {count} standing")
            # `reduces` says a step lowers a count without finishing it. A stage it leaves at none is a step that
            # settled what it said it would not, and the declaration is then a second thing to read beside the code
            # rather than the same thing said once.
            if step.does_reduce(test) and not count:
                faults.append(f"[{label}] says it only lowers `{named}` and leaves none standing")
            is_settled = (is_settled or step.does_finish(test)) and not count
            held = count
    # Taking a count to none is a claim about the grammar and is said outright, wherever it happens. Read over every
    # step rather than from the first that names the invariant, since a step settling one it never mentions is exactly
    # what no other reading here can see — `build-alternatives` took `no-sequence-of-sequences` to none in silence while
    # the step that named it was left claiming a settle it does not make. Lowering a count without finishing it is not
    # this: an invariant is measured from the first step that names it, so what a step does to a shape the pipeline has
    # not built yet is construction rather than work on the property.
    for index, step in enumerate(STEPS):
        for named, test in by_name.items():
            before, after = standing.get((index, named)), standing.get((index + 1, named))
            if before and after == 0 and not step.does_finish(test):
                faults.append(f"[{step.name}] takes `{named}` to none and does not say it settles it")
    # Once an invariant is a question the pipeline asks, every step that moves its count says so. Read from the first
    # step that names it, that being where the count starts being measured: what a step does to a shape the pipeline has
    # not built yet is construction, and what it does to one already under the law is work on the property.
    for named, test in by_name.items():
        first = min(index for index, step in enumerate(STEPS) if named in step.carries)
        for index in range(first, len(STEPS)):
            step = STEPS[index]
            before, after = standing.get((index, named)), standing.get((index + 1, named))
            if before is not None and after is not None and after < before and named not in step.carries:
                faults.append(f"[{step.name}] lowers `{named}` from {before} to {after} and does not name it")
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


def unsettled_invariants(grammar, points=None):
    """
    Every invariant the pipeline names that `grammar` still breaks, as `[(name, count)]` worst first.

    What the steps settle between them is not the same question as what is true at the end: an invariant settled early
    and broken later under a declared lapse is unsettled all the same, and a lapse is a reason rather than an excuse.
    Each one standing is work still owed — a step that has not been written — so this is the list Phase 03 finishes by
    emptying, and it says so mechanically instead of leaving it to be noticed.
    """
    named = {held.name: held for step in STEPS for held in step.invariants}
    standing = [(name, len(test(grammar, points))) for name, test in sorted(named.items())]
    return sorted(((name, count) for name, count in standing if count), key=lambda held: -held[1])


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
        if way.gate.guards or way.actions:
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


def _pairs_blanked(body):
    """
    `(body with every pair blanked, the pairs it held)`, in the order the walk meets them.

    Which pair a half belongs to is not what a production does, so two that differ only there behave alike and are one.
    Read off whether the node has a `pair` at all rather than off a list of the kinds that do, so a pair added to the IR
    is alike-compared from the day it exists.
    """
    held = []

    def rewrite(node):
        node = ir.rebuilt(node, rewrite)
        if dataclasses.is_dataclass(node) and any(field.name == "pair" for field in dataclasses.fields(node)):
            held.append(node.pair)
            return dataclasses.replace(node, pair=frozenset())
        return node

    return rewrite(body), tuple(held)


def _pairs_written(body, pairs):
    """`body` with `pairs` put back into its halves, in the order `_pairs_blanked` took them out."""
    held = iter(pairs)

    def rewrite(node):
        node = ir.rebuilt(node, rewrite)
        if dataclasses.is_dataclass(node) and any(field.name == "pair" for field in dataclasses.fields(node)):
            return dataclasses.replace(node, pair=next(held))
        return node

    return rewrite(body)


def _grouped(grammar):
    """
    `{name: group}`, productions that behave alike sharing a group: same parameters, and the same body once every
    reference in it is read as the group of what it names rather than by the name itself, and once the pairs its scope
    actions carry are blanked — so two loops that differ only in what their helpers are called, or in which pair they
    hold, come out alike, which comparing the bodies as written cannot see. The groups are the coarsest partition that
    stays stable under that reading: everything starts alike, and a difference splits it, until a round splits nothing.
    """

    def blanked(body):
        """`body` with every reference's name blanked, and the names they held, in the order the walk meets them."""
        held = []

        def rewrite(node):
            node = ir.rebuilt(node, rewrite)
            if isinstance(node, ir.Ref):
                held.append(node.name)
                return dataclasses.replace(node, name="#")
            return node

        return rewrite(body), tuple(held)

    # What a body is, said once: the shape it has with the names taken out, and the names in the order they stood. A
    # round only ever changes what group a name is in, never where it stands, so the shape is built once and each round
    # is a tuple of group numbers beside it rather than the whole body written out again.
    shapes = {name: blanked(_pairs_blanked(production.body)[0]) for name, production in grammar.items()}
    ordered = sorted(grammar)
    block = dict.fromkeys(grammar, 0)
    for _round in ir.rounds("the sweep's refinement of duplicate bodies"):
        signatures, refined = {}, {}
        for name in ordered:
            shape, held = shapes[name]
            signature = (grammar[name].params, shape, tuple(block.get(one, -1) for one in held))
            refined[name] = signatures.setdefault(signature, len(signatures))
        if refined == block:
            return block
        block = refined


def _merged(grammar, keep):
    """
    `grammar` with productions that behave alike spelled once: a call to one is a call to any other of its group, so
    every reference to a duplicate becomes a reference to the one kept. A production the parse enters by name is always
    kept, having a name a caller cannot be redirected away from — and where a group holds two of those, both stand.

    The one kept stands for the ones that go, so its scope actions come to stand for their pairs as well: each half of
    it holds every pair the halves it replaced held. That is what a close asking whether it shares a pair with the open
    on the stack reads, and it is where a merge's cost is paid — two pairs the grammar itself stopped telling apart are
    two a close can no longer be held to one of.
    """
    block = _grouped(grammar)
    standing = {}
    for name in sorted(grammar, key=lambda name: (name not in keep, name)):
        standing.setdefault(block[name], name)
    canonical = {name: standing[block[name]] for name in grammar if name not in keep and standing[block[name]] != name}
    if not canonical:
        return grammar, {}
    gone = {}
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
    # Each node renames what it holds, which is the same declaration reachability reads: a name is followed because the
    # class says it holds one, not because a walk recognised the node it is spelled in.
    return {name: p.renamed(canonical) for name, p in swept.items()}, canonical


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

    An empty match among a way's actions is litter of the same kind as one in a sequence, and goes for the same reason:
    it takes no character and does nothing, so the way matches exactly what it did without it.
    """
    node = ir.rebuilt(node, _flattened)
    if isinstance(node, ir.Alternative) and any(isinstance(action, ir.Empty) for action in node.actions):
        return dataclasses.replace(
            node, actions=tuple(action for action in node.actions if not isinstance(action, ir.Empty))
        )
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


def cleaned(grammar, namer=None):
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

    for _round in ir.rounds("the splice of do-nothing productions"):
        flat = {name: dataclasses.replace(p, body=_flattened(p.body)) for name, p in grammar.items()}
        spliced, _splices = _spliced(flat, keep)
        swept, merges = _merged(_inlined_called_ways(spliced, namer), keep)
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

    A claim is not a step in this sense — it transforms nothing and stands in the list only to say where a property
    already holds, so it repeats the stage it was handed rather than making one.

    Every step must change the grammar, and one that does not is a fault. A step earns its place by doing something: it
    goes idle when what it looks for has stopped reaching it — a shape an earlier step now spells differently, a
    declared site whose content moved — and that is a regression in the step before it, not a step to leave standing.
    The check reads the transform's own output, before the sweep, so a step is judged on what it did rather than on what
    the sweep did after it.
    """
    namer = Namer()
    namer.sees(grammar)
    namer.points.settle("base", grammar)
    result = [("base", grammar)]
    for step in STEPS:
        ir.say(f"    [{step.name}] over {len(grammar)} production(s)")
        if step.is_a_claim:
            result.append(
                (step.name, grammar)
            )  # a claim leaves the grammar it was handed, so the stage is the same one
            continue
        produced = step.transform(grammar, namer)
        if produced == grammar:
            raise AssertionError(f"the `{step.name}` step changed nothing — what it looks for no longer reaches it")
        grammar, renames = cleaned(produced, namer)
        namer.sees(grammar)
        namer.points.follow(renames)
        namer.points.settle(step.name, grammar)
        result.append((step.name, grammar))
    return result, namer.points


def _every_difference_is_between_character_sets(grammar):
    """
    Check that both sides of every `(---)` denote a character set.

    The base must match exactly one character, and each subtracted side must have codepoints that can be worked out
    here. Annotations around a subtracted character do not count against it: a difference reads the text a match takes,
    not the code it carries.
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


EVERY_DIFFERENCE_IS_BETWEEN_CHARACTER_SETS = Invariant(
    "every-difference-is-between-character-sets", _every_difference_is_between_character_sets
)

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
    if isinstance(node, (*_TAKES_NOTHING, ir.Star, ir.Opt)):
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
    if isinstance(node, _HOLDERS):
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


def _every_peek_is_a_character_set(grammar):
    """
    Check that every lookaround holds a character set, so its question is one the machine can put to one character.

    An `(exclude)` is no peek of this kind and is no business of this count: it asks about a line rather than about a
    character.
    """
    return [
        f"{name}: a {type(node).__name__} holds a {type(node.item).__name__} rather than a character set"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, _PEEKS) and not isinstance(node.item, ir.CharSet)
    ]


EVERY_PEEK_IS_A_CHARACTER_SET = Invariant("every-peek-is-a-character-set", _every_peek_is_a_character_set)


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
        if isinstance(node, _PEEKS):
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


def _every_character_question_is_a_character_set(grammar):
    """
    Check that every question about a character is written as a `CharSet` — the one shape the parser can be given.

    A question is about a character when what it matches is exactly one: a union, a difference, a raw character node,
    and the item a lookaround peeks. A reference is a hold on the production where the set is said, so a match taking
    one is no fault; inside a lookaround it is a fault, what a peek holds being the question rather than the hold. A
    guard over several characters — an `(exclude)`, a difference of two multi-character productions — asks nothing about
    a character and is no business of this count.
    """
    faults = []
    for name, production in grammar.items():

        def walk(node, owner=name):
            if isinstance(node, ir.CharSet):
                return  # lowered
            if isinstance(node, _PEEKS) and ir.is_one_char(_peeked_question(node.item, grammar), grammar):
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


EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET = Invariant(
    "every-character-question-is-a-character-set", _every_character_question_is_a_character_set
)


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
    return _PEEK_SPANS(peek, grammar)


def _united_peek_spans(peek, grammar):
    """
    An alternation's intervals, unioned here rather than denoted: the invalid byte has no denotation — it is a unit no
    character holds — and an alternation carrying one, as the `Invalid` node or as the interval a `CharSet` says it
    with, denotes nothing while admitting perfectly well.
    """
    gathered = []
    for item in peek.items:
        admitted = _peek_spans(item, grammar)
        if admitted is None:
            return None
        gathered += admitted
    invalid = [span for span in gathered if span[0] < 0]
    return [(-1, -1)] * bool(invalid) + _merged_spans([span for span in gathered if span[0] >= 0])


def _denoted_peek_spans(peek, grammar):
    """A question's intervals by what it denotes, which is `None` where the set is not pinned down."""
    denotation = chars.denote(grammar, peek)
    return None if denotation is None else _denoted_spans(denotation)


_PEEK_SPANS = ir.Reading(
    "the codepoint intervals a peek admits, or none where its set is not pinned down",
    {
        ir.CharSet: lambda peek, grammar: [tuple(span) for span in peek.spans],
        ir.Invalid: [(-1, -1)],
        ir.Alt: _united_peek_spans,
        (ir.Char, ir.Diff, ir.Range, ir.Ref): _denoted_peek_spans,
    },
)


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


def _every_indentation_change_is_pushed(grammar):
    """
    Check that a push and its pop stand around every call that changes the indentation — one measured against a level,
    and one that establishes a level a later item reads.

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


EVERY_INDENTATION_CHANGE_IS_PUSHED = Invariant(
    "every-indentation-change-is-pushed", _every_indentation_change_is_pushed
)


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
    Write a run over a character class as the one scan it is, entered on the class: `x+` is the guard that the class is
    in front and the span behind it, and `x*` is that same way beside the way the class is not in front of.

    What such a run takes is a value the input decides rather than a way the parse chooses, and saying it as a scan is
    what lets the codegen make one repeated-char-set call of it. A span the class is known to stand in front of takes
    what a `Plus` does, at least one character of it.

    A `Star`'s second way asks that the class is *not* there rather than matching empty beside it. The scan it replaces
    is possessive: where the class is in front it takes the run and there is no shorter match to be given back for. An
    empty way would be exactly that shorter match — `s-indent-le` is a run of spaces judged against the indentation
    afterwards, and one too long would fail the judgement, fall back to no spaces at all and pass it. Told apart on the
    character, neither way is reachable where the other was taken, and the alternation is the scan.

    The guard rather than the class itself, though either admits the same text. A match of the class is a call, and a
    call is where `mint-continuations` ends a way — so the span would land in a continuation entered after it returned,
    and what says the run takes a character would be a production away from the run. A guard is no call: it stays among
    the actions beside the span, which is both where a hoist can lift it into the gate and where `_split_seq` can read
    it as the reason the scan is not empty.

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
        if isinstance(node, (ir.Star, ir.Plus)) and ir.is_one_char(node.item, grammar):
            asked = as_char_set(_peeked_question(node.item, grammar), grammar)
            taken = ir.Seq(items=(ir.Look(item=asked), ir.ConsumeSpan(set=node.item)))
            return taken if isinstance(node, ir.Plus) else ir.Alt(items=(taken, ir.NegLook(item=asked)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _every_character_run_is_a_span(grammar):
    """
    Check that no repetition is over a character class — a run of characters is a value the scan decides rather than a
    way the parse repeats.

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


EVERY_CHARACTER_RUN_IS_A_SPAN = Invariant("every-character-run-is-a-span", _every_character_run_is_a_span)

# The two repetitions the vendored notation writes are gone, one `LongestRun` standing for both. What is left after this
# is one operation for repeating a way, which is what every reading past here is written against. The counted `Rep` is
# not one of these: it takes the number of turns it names rather than as many as it can, and is a later step's.
NO_STAR_OR_PLUS_NODES = _absent("no-star-or-plus-nodes", ir.Star, ir.Plus)


# Phase 6's first: a scope that holds what it covers is the pair that brackets it instead. A `(wrap)` is the one that
# says so outright — a node rather than the two markers so that a `begin` cannot lose its `end`, which is a guarantee
# the pair carries instead, the parse holding a close to the open it shares a pair with, and `check_markers` still owes
# for the markers.
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
            pair = namer.pair()
            return ir.Seq(
                items=(
                    ir.OpenWindow(limit=node.limit, message=node.message, pair=pair),
                    node.item,
                    ir.CloseWindow(pair=pair),
                )
            )
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
            pair = namer.pair()
            return ir.Seq(
                items=(
                    ir.PushMessage(message=node.message, pair=pair),
                    node.item,
                    ir.PopMessage(pair=pair),
                )
            )
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def mint_forbidden_probes(grammar, namer):
    """
    Give what an exclusion forbids a copy of its own, holding only what matches and asks.

    The pattern answers whether what must not be there is, and the parse is put back where it stood whatever it says —
    so a `(token)` inside it says what code characters carry that nothing keeps. The copy drops it and matches the same
    text, the annotation being the whole of the difference.

    A copy rather than a rewrite of what stands, since the same production is reached both ways: `c-directives-end` is
    the `---` a document really opens with and the `---` a plain scalar must not run into, and only the second is
    matched to be thrown away. The whole reach gets one, the callers inside it naming the copies, and the sweep behind
    this collapses every copy that came out the same as what it was made from.
    """
    copies = {name: namer.fresh(name) for name in sorted(_forbidden_productions(grammar))}

    def tested(node):
        node = ir.rebuilt(node, tested)
        return node.item if isinstance(node, ir.Token) else node

    def forbids(node):
        node = ir.rebuilt(node, forbids)
        if isinstance(node, (ir.ExcludeAt, ir.SetForbidden)) and node.item is not None:
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
    the reason each half carries its pair: a pop takes back whatever is on top, so a pair cut apart carelessly would
    take back what another way had put there, and the pair is what says so where it happens.
    """

    def lowered(node):
        node = ir.rebuilt(node, lowered)
        if isinstance(node, ir.Token):
            pair = namer.pair()
            return ir.Seq(items=(ir.PushCode(code=node.code, pair=pair), node.item, ir.PopCode(pair=pair)))
        return node

    return {
        name: dataclasses.replace(production, body=lowered(production.body)) for name, production in grammar.items()
    }


def _reached_forbidden(node, standing, reached):
    """
    What is forbidden where `node` ends, recording against `reached` what each call it makes is entered with.

    A sequence carries the set along its parts, an `(exclude)` sets it for everything past where it stands, and every
    other shape holds its parts where it stands itself — so a branch of a choice neither takes an exclusion from its
    sibling nor hands one on.
    """
    if isinstance(node, ir.Seq):
        for item in node.items:
            standing = _reached_forbidden(item, standing, reached)
        return standing
    if isinstance(node, ir.ExcludeAt):
        return node.item
    if isinstance(node, ir.Ref):
        reached[node.name].add(standing)
        return standing
    ir.rebuilt(node, lambda child: (_reached_forbidden(child, standing, reached), child)[1])
    return standing


def _forbidden_entries(grammar):
    """
    `{name: sets}` — what may not match at a start of line where each production is entered, `None` among them where
    nothing may.

    A least fixpoint over the calls: a parse enters by name with nothing forbidden, and a call carries in whatever
    stands where it is made. Most productions are entered one way; `l-unparsed` under a line-bounded policy is entered
    two, the stream's recovery reaching it with nothing forbidden and a block entry's reaching it inside a document.
    """
    entries = {name: set() for name in grammar}
    for name in entered_by_name(grammar):
        entries[name].add(None)
    for _round in range(len(grammar) + 1):
        reached = {name: set(held) for name, held in entries.items()}
        for name, production in grammar.items():
            for standing in entries[name]:
                _reached_forbidden(production.body, standing, reached)
        if reached == entries:
            return entries
        entries = reached
    raise AssertionError("what is forbidden where each production is entered never settled")


def lower_exclusions(grammar, namer):
    """
    Write each `(exclude)` as the two writes that bound it: `ExcludeAt(x)` over the rest of a way becomes
    `SetForbidden(x)` where it stands and `SetForbidden(<what stands after>)` where the way ends.

    An exclusion is in force until the production holding it returns, which is a frame's worth of scope and the last one
    phase 6 has to take off. What the frame kept, the writes say outright — so a step that moves a way's parts into
    another production carries the end with them rather than widening what the exclusion covers.

    The closing write names what stands after rather than taking back what the opening write displaced, the set being
    one value for the parse. A production holding an exclusion that two callers reach in different states cannot name
    one, so it is copied per state and each caller enters the copy for its own — the same specialization `monomorphize`
    makes of a parameter, of the one thing that is not one.
    """
    entries = _forbidden_entries(grammar)
    held = {
        name for name, production in grammar.items() if any(isinstance(n, ir.ExcludeAt) for n in _held(production.body))
    }
    copies = {}  # {(name, standing): the name to call in that state}
    for name in sorted(held):
        for standing in sorted(entries[name], key=str)[1:]:
            copies[(name, standing)] = namer.fresh(name)

    def told(name, standing):
        return copies.get((name, standing), name)

    def lowered(node, standing):
        if isinstance(node, ir.Ref):
            return dataclasses.replace(node, name=told(node.name, standing))
        if isinstance(node, ir.Seq):
            items = []
            for index, item in enumerate(node.items):
                if isinstance(item, ir.ExcludeAt):
                    rest = lowered(ir.Seq(items=node.items[index + 1 :]), item.item)
                    return ir.Seq(
                        items=(*items, ir.SetForbidden(item=item.item), *rest.items, ir.SetForbidden(item=standing))
                    )
                items.append(lowered(item, standing))
            return ir.Seq(items=tuple(items))
        return ir.rebuilt(node, lambda child: lowered(child, standing))

    written = {}
    for name, production in grammar.items():
        for standing in sorted(entries[name] or {None}, key=str):
            under = told(name, standing)
            written[under] = dataclasses.replace(production, name=under, body=lowered(production.body, standing))
    return written


NO_EXCLUDE_AT_NODES = _absent("no-exclude-at-nodes", ir.ExcludeAt)


def lower_runs(grammar, namer):
    """
    Say the two repetitions as the ways they are: a turn, a recursion taking the rest, and a settled region around the
    turns after the first. `x*` offers the turn never taken beside them and `x+` does not, which is the whole of the
    difference between the two spellings.

    Every turn takes a character — a turn taking none would repeat for ever, and a run ends where a turn is not taken.
    What the region settles is the turns it holds: a failure past its close gives the whole run up rather than taking
    fewer turns, or taking one of them another way, which is what makes the run the longest one. So the two backtrack
    points a run has stand where the grammar says them, the first turn outside the region and every turn after it in.

    The turn is a production of its own because both places take it, and the recursion one because a loop is a state the
    machine jumps to. What stands here after that is `Alt` and `Seq` like anything else, and the phases behind this
    shape it: the guard past the turn is what cuts the way, and the gates arrive where every gate does.

    A run over a character class is the same operation said as the scan a parser makes of it, which `span-consumes` has
    already written as a `ConsumeSpan`; what is left here repeats a way, and a way repeated is said as ways.
    """
    minted = {}

    def said(name, number, node):
        """`node`'s turn and the recursion that takes the rest of them, as the productions they are."""
        # A loop is a state the machine jumps to, so the recursion is a production; the turn is one too where it is not
        # already a call, both places taking it having to name the same state rather than spell the match out twice.
        loop = namer.fresh(name)
        if isinstance(node.item, ir.Ref):
            turn = node.item
        else:
            held = namer.fresh(name)
            minted[held] = ir.Prod(number, held, (), node.item)
            turn = ir.Ref(name=held, args=())
        taking, pair = namer.pair(), namer.pair()
        takes = (ir.StartMustConsume(pair=taking), turn, ir.EndMustConsume(pair=taking))
        minted[loop] = ir.Prod(
            number, loop, (), ir.Alt(items=(ir.Seq(items=(*takes, ir.Ref(name=loop, args=()))), ir.Empty()))
        )
        rest = (ir.PushBackTrack(pair=pair), ir.Ref(name=loop, args=()), ir.PopBackTrack(pair=pair))
        taken = ir.Seq(items=(*takes, *rest))
        return taken if isinstance(node, ir.Plus) else ir.Alt(items=(taken, ir.Empty()))

    def lowered(name, number, node):
        node = ir.rebuilt(node, lambda held: lowered(name, number, held))
        return said(name, number, node) if isinstance(node, (ir.Star, ir.Plus)) else node

    runs = {
        name: dataclasses.replace(production, body=lowered(name, production.number, production.body))
        for name, production in grammar.items()
    }
    return {**runs, **minted}


def lower_optionals(grammar, namer):
    """
    Write each optional as the alternation it already is: `x?` becomes `x | <empty>`, the empty way standing beside the
    one that reads rather than hidden inside a node.

    The interpreter says it is the same match: an `Opt` tries its item with the continuation behind it and, where that
    fails, rewinds and takes the continuation alone — which is an alternation of the item and `<empty>`, tried in that
    order. Nothing about how much the item takes changes, so nothing has to be known about what follows.

    The `<empty>` stands even where the item can already take nothing, and the corpus is what says so: the optional
    offers all of the item's ways and *then* an empty match, where the item alone offers only its own — whose empty way
    may sit ahead of its reading ones. Dropping the second empty match there changes which parse is preferred, and 439
    fixtures read differently for it. Two ways that both take nothing is what the empties phase splits apart later.
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
    ir.Range,
)
_ACTIONS = (
    ir.ClearVar,
    ir.CloseWindow,
    ir.CommitProvisional,
    ir.Cut,
    ir.Emit,
    ir.Error,
    ir.ExcludeAt,
    ir.Increase,
    ir.InjectBefore,
    ir.MarkProvisional,
    ir.OpenProvisional,
    ir.OpenWindow,
    ir.PopBackTrack,
    ir.PopCode,
    ir.PopIndent,
    ir.PopMessage,
    ir.PopRecovery,
    ir.PushBackTrack,
    ir.PushCode,
    ir.PushIndent,
    ir.PushMessage,
    ir.PushRecovery,
    ir.RetypeProvisional,
    ir.SetForbidden,
    ir.SetVar,
    ir.StartMustConsume,
)
# A question the parse answers where it stands, taking nothing: what the input holds around it, and how the count it
# carries compares. A `(cut)` is not one of these — it takes nothing either, but it commits the parse rather than asking
# it anything, and it stands with the actions and the other commits.
_GUARDS = (
    ir.EndMustConsume,
    ir.EndOfStream,
    ir.Le,
    ir.LiteralPeek,
    ir.Look,
    ir.LookBehind,
    ir.Lt,
    ir.NegLook,
    ir.StartOfLine,
)

# What takes no character at all, the counterpart of `_ALWAYS_READS`: an action leaves something behind, a guard asks a
# question, an empty match does neither. What stands behind one of these is what a match begins on.
_TAKES_NOTHING = (*_ACTIONS, *_GUARDS, ir.Empty)


# A peek: a guard that holds its question about the input as an `item` and takes nothing, whether it asks about what
# stands in front or what stands behind. `every-peek-is-a-character-set` is what these are held to. Not every guard that
# reads the input is one — `EndOfStream` asks whether a character is there at all and holds no question, and a
# `LiteralPeek` holds a run of characters rather than a set.
_PEEKS = (ir.Look, ir.LookBehind, ir.NegLook)

# The guards that read what stands in front of the parse: whether a character is there at all, whether it begins a
# literal, whether it falls in a set, whether it falls outside one. What stands behind is not one of these, and neither
# is where the parse is in the line or how the indentation compares.
_LOOKS_AHEAD = (ir.EndOfStream, ir.LiteralPeek, ir.Look, ir.NegLook)

# A node that holds what it covers rather than bracketing it with a pair — what the wrappers phase takes apart.
_HOLDERS = (ir.Commit, ir.Max, ir.Recover, ir.Token, ir.Wrap)

# A repetition: the turns it takes are the same state entered again.
_RUNS = (ir.LongestRun, ir.Plus, ir.Star)

# A scan of a character class, which may be asked for none at all and so forces no character to be there.
_SCANS = (ir.ConsumeCountedSpan, ir.ConsumeSpan)

# What a production may hold instead of a matcher: a value the parse works out — an indentation, a measured length, a
# parameter, a switch over one. It matches nothing, so it takes no character and reads nowhere. In alphabetical order.
_VALUE_KINDS = (
    ir.Add,
    ir.Atoi,
    ir.AutoDetectIndent,
    ir.Column,
    ir.Flip,
    ir.Global,
    ir.Indent,
    ir.Len,
    ir.Lit,
    ir.Match,
    ir.Param,
    ir.Sub,
)


# What an item may be: something the machine does where it stands — a call, an action, a guard, a character taken, or
# nothing at all. A guard's question is the guard's own business and no item of the way, which is what lets a peek hold
# a set and an exclusion a question a bounded run of steps answers; `every-peek-is-a-character-set` and
# `every-exclusion-is-bounded` are what answer for those. In alphabetical order after the families.
_LEAF_ITEMS = (
    *_ACTIONS,
    *_GUARDS,
    ir.Char,
    ir.CharSet,
    ir.ConsumeChar,
    ir.ConsumeCountedSpan,
    ir.ConsumePeeked,
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
    return _INNER_WAYS(node)


_INNER_WAYS = ir.Reading(
    "the ways a body offers, each a match in its own right",
    {
        ir.Choice: lambda node: node.alternatives,
        ir.Alt: lambda node: node.items,
        ir.Seq: lambda node: (node,),
        ir.LongestRun: lambda node: (node.item,),
        ir.Recover: lambda node: (node.item, node.recovery),
        ir.Bind: lambda node: (node.cond,),  # a binding: the match it puts a value in scope for
    },
)


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


EVERY_SUB_ITEM_IS_ONE_STEP = Invariant(
    "every-sub-item-is-one-step", lambda grammar: _nested_matches(grammar, _HOLDS_A_MATCH)
)

# The phase's first share: a choice is where the machine has a state, and standing inside a way it has nowhere to be
# one. Its own production is that state, and the way holds the call.
EVERY_CHOICE_IS_A_PRODUCTION = Invariant(
    "every-choice-is-a-production", lambda grammar: _nested_matches(grammar, ir.Alt)
)

# The second: a recovery is a handler over a match, which an alternative carries on its own edge. It has no edge to ride
# until the alternatives are made, so it gets a production of its own meanwhile — a body the re-encode reads as the way
# it is, its item the call and its recovery what rides the push.
EVERY_RECOVERY_IS_A_PRODUCTION = Invariant(
    "every-recovery-is-a-production", lambda grammar: _nested_matches(grammar, ir.Recover)
)

# The last: a binding is a match and the write that follows it, which the vocabulary already spells as two things.
NO_BIND_NODES = _absent("no-bind-nodes", ir.Bind)

# What costs the machine a turn per character rather than one step: a way it repeats until the input stops it. A span is
# not among them — a run taken whole is a value the input decides, judged once, so what it costs does not grow with what
# it takes. A counted repetition is here too, its turns being as many as the count says and the count a value the parse
# works out.
_REPETITIONS = (ir.LongestRun, ir.Plus, ir.Rep, ir.Star)


def _is_bounded_question(node, grammar, entered=frozenset()):
    """
    Whether `node` asks what a bounded number of the machine's steps answers.

    Repetition and recursion are the whole of what makes a question unbounded. A call costs what its callee costs, and
    one that reaches itself costs without bound; everything else is a fixed run of steps, a span among them — a run of
    spaces measured against the indentation is one scan and a comparison, where a loop over the same characters is a
    turn each.
    """
    if isinstance(node, _REPETITIONS):
        return False
    if isinstance(node, ir.Ref):
        if node.name in entered or node.name not in grammar:
            return False
        return _is_bounded_question(grammar[node.name].body, grammar, entered | {node.name})
    held = []
    ir.rebuilt(node, lambda child: (held.append(child), child)[1])
    return all(_is_bounded_question(child, grammar, entered) for child in held)


def _every_exclusion_is_bounded(grammar):
    """
    Check that every `(exclude)` asks what a bounded number of the machine's steps answers.

    An `(exclude)` is a guard the parse carries, tested at every start of line while it stands, so what it asks has to
    be answerable where it is asked: `c-forbidden` is a line beginning `---` or `...` and then a break, a space or the
    end, which is a handful of steps. The line-at-this-indentation question is another — the run of spaces is one scan,
    judged against the indentation once — where a loop over those spaces would cost a turn each and never be answerable
    where it stands.
    """
    return [
        f"{name}: an `(exclude)` asks what no bounded run of steps answers"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.ExcludeAt) and not _is_bounded_question(node.item, grammar)
    ]


EVERY_EXCLUSION_IS_BOUNDED = Invariant("every-exclusion-is-bounded", _every_exclusion_is_bounded)


def _parts_only_match_and_ask(node):
    """Whether every part `node` holds only matches and asks."""
    held = []
    ir.rebuilt(node, lambda child: (held.append(child), child)[1])
    return all(_ONLY_MATCHES_AND_ASKS(child) for child in held)


# What an exclusion's pattern may be made of, asked of one node and not of what it calls: a call is answered for by the
# production it names, which `_forbidden_productions` reaches in its own right.
_ONLY_MATCHES_AND_ASKS = ir.Reading(
    "whether what an exclusion forbids only matches and asks",
    {
        # A match takes characters and says whether they were there, which is the whole of what the probe wants. A
        # literal taken on the gate's word is one of them, its characters found before it ran.
        (ir.Char, ir.CharSet, ir.ConsumeChar, ir.ConsumePeeked, ir.ConsumeSpan, ir.Range): True,
        # A guard reads where the parse stands and leaves it there, and an empty match does neither. A literal peek is
        # one of them: it asks whether the input begins with its text and takes nothing either way.
        (ir.Empty, ir.EndOfStream, ir.Le, ir.LiteralPeek, ir.StartOfLine): True,
        ir.Ref: True,
        # A shape that holds parts is what its parts are — a difference and a lookaround among them, each asking about a
        # match of its own.
        (ir.Alt, ir.Alternative, ir.Choice, ir.Diff, ir.Gate, ir.Look, ir.NegLook, ir.Seq): (
            lambda node: _parts_only_match_and_ask(node)
        ),
        # A `(token)` says what code the characters under it carry, which is a mark on a stream the probe emits nothing
        # to.
        ir.Token: False,
    },
)


def _forbidden_productions(grammar):
    """
    The productions an exclusion's pattern reaches, transitively — what the parse runs at a start of line to answer
    whether what it must not find is there.
    """
    seen = set()
    worklist = [
        name
        for production in grammar.values()
        for node in _held(production.body)
        if isinstance(node, (ir.ExcludeAt, ir.SetForbidden))
        for name in node.references()
    ]
    while worklist:
        name = worklist.pop()
        if name in seen or name not in grammar:
            continue
        seen.add(name)
        worklist.extend(grammar[name].references())
    return seen


def _every_forbidden_only_matches_and_asks(grammar):
    """
    Check that what an exclusion forbids only matches characters and asks questions.

    The pattern is run at every start of line to answer whether what must not be there is, and the parse is put back
    where it stood whatever the answer. So it may take characters and it may ask, and it may do nothing else: an action
    leaves a mark, and a match nothing keeps has nothing left to take one back with.

    Asked of the productions the pattern reaches rather than of the sites naming it, since one pattern reached from
    several sites is one thing to answer for and a step that copies a site copies no fault.
    """
    return [
        f"{name}: what an exclusion forbids does more than match and ask"
        for name in sorted(_forbidden_productions(grammar))
        if not _ONLY_MATCHES_AND_ASKS(grammar[name].body)
    ]


EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS = Invariant(
    "every-forbidden-only-matches-and-asks", _every_forbidden_only_matches_and_asks
)


def _no_choice_of_choices(grammar):
    """
    Check that no way of a choice is a call to a choice.

    `a | P | c` where `P` is `d | e` offers three ways and makes four decisions, the fourth behind a call nothing about
    the outer choice can see. Written out, `a | d | e | c` is the same four ways with every one of them standing where
    the choice can be asked about it — which is what lets a gate be put on each.

    Read of the tree's spelling, an `Alt` of ways one of which names an `Alt`. Past the re-encode there are no `Alt`
    nodes at all, so what settles this stays settled by the shape rather than by a promise.
    """
    return [
        f"{name}: a way of a choice that is a call to a choice"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Alt)
        for way in node.items
        if isinstance(way, ir.Ref) and isinstance(grammar[way.name].body, ir.Alt)
    ]


NO_CHOICE_OF_CHOICES = Invariant("no-choice-of-choices", _no_choice_of_choices)


def _no_sequence_of_sequences(grammar):
    """
    Check that no item of a way, bar the last, is a call to a run of items.

    `a P c` where `P` is `d e` is four things done in a row and shows three, so a reading that walks a way to find what
    it does first stops at the call rather than at `d`. Written out, `a d e c` is the same run with every part of it
    standing where the way holds it.

    A call standing last is not one of them: what a way does past its call is a production of its own by design, which
    is what the phase behind this mints. It is a call in the middle that hides a run, and only that.

    Read of the tree's spelling, a `Seq` of items one of which names a `Seq`. Past the re-encode there are no `Seq`
    nodes at all, so what settles this stays settled by the shape rather than by a promise.
    """
    return [
        f"{name}: an item of a way that is a call to a run of items"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.Seq)
        for item in node.items[:-1]
        if isinstance(item, ir.Ref) and isinstance(grammar[item.name].body, ir.Seq)
    ]


NO_SEQUENCE_OF_SEQUENCES = Invariant("no-sequence-of-sequences", _no_sequence_of_sequences)


def _items_of_way(way):
    """
    The items a way *performs*, whichever way it is spelt — and **not** its gate.

    An alternative says its parts by name — what it does, what it calls, where it carries on — where a sequence says
    them in a row; a reading that walks a way wants them in the order the parse performs them either way. What a
    recovery rides is not among them: it answers for a cut rather than standing in the way's own run.

    Leaving the gate out is right for a rewrite, which keeps it untouched, and wrong for nearly every question. Three
    readings have answered about a way through this and been wrong for the same reason — the gate is what decides
    whether the way is entered at all, so a walk that cannot see it reports what a way would do if it were always taken.
    `_parts_of_way` is the one to ask instead; this is for callers that mean the performing parts alone.
    """
    if isinstance(way, ir.Alternative):
        return (*way.actions, *(held for held in (way.first, way.second) if held is not None))
    return way.items if isinstance(way, ir.Seq) else (way,)


def _parts_of_way(way):
    """
    What a question about a way should walk: the guards its gate asks, then what it performs, in the order the parse
    meets them.

    A gate is asked before the way is entered, so a reading that leaves it out answers about a way the parse may never
    take — which is how the same defect reached `_split_way`, `_takes_none_only_at_the_end` and `_entered_unconsumed` in
    turn. Its guards are zero-width and stand among a way's parts as readily as one of its actions would, so a walk over
    these asks each of them the question it already asks of the rest.

    The peek is not among them. It is the question about the character in front rather than something the way does, and
    a walk that took it for a part would read a peeked way as one that must take a character — which is what a peek says
    about the input, not about the way. `_ahead_of_gate` is what reads it.
    """
    return (*way.gate.guards, *_items_of_way(way)) if isinstance(way, ir.Alternative) else _items_of_way(way)


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


def _a_way_is_actions_a_call_and_a_continuation(grammar):
    """
    Check that every way is actions, the call it hands control to, and the one production that carries on.

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


A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION = Invariant(
    "a-way-is-actions-a-call-and-a-continuation", _a_way_is_actions_a_call_and_a_continuation
)


def _every_body_is_a_choice_a_run_or_a_set(grammar):
    """
    Check that every body is one of the three things the machine has a state for.

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


EVERY_BODY_IS_A_CHOICE_A_RUN_OR_A_SET = Invariant(
    "every-body-is-a-choice-a-run-or-a-set", _every_body_is_a_choice_a_run_or_a_set
)


def _guard_past_an_action_at(actions):
    """Where a guard first stands past an action among `actions`, or `None` where none does."""
    acted = False
    for index, action in enumerate(actions):
        if isinstance(action, _GUARDS):
            if acted:
                return index
        else:
            acted = True
    return None


def _no_guard_stands_past_an_action(grammar):
    """
    Check that no guard stands past an action, so a way is the guards it asks, then what it performs, then the call it
    makes and where it carries on.

    A guard is what decides, and one reached past an action decides nothing that can be acted on: the action has already
    happened, so failing there is the parse dying rather than another way being tried. Standing in front, every guard a
    way holds is one its gate could carry, and what is behind them is a straight run of things that always happen. A way
    holding no guard at all is the shape this is working towards, and holds trivially.

    It is what lets a way be split into what it takes and what it does not. Such a split walks the parts in order and
    hands each half the parts that belong to it; a guard sitting behind an action belongs to neither half without the
    action being performed twice or not at all.
    """
    return [
        f"{name}: a guard stands past an action the way already performed"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        if _guard_past_an_action_at(way.actions) is not None
    ]


NO_GUARD_STANDS_PAST_AN_ACTION = Invariant("no-guard-stands-past-an-action", _no_guard_stands_past_an_action)


# The end of the stream as a value the character in front of the parse takes: a unit no character class holds, the way
# the invalid byte's `(-1, -1)` is one, so a gate wanting a character and a gate wanting the end are told apart by the
# same span algebra as any two classes.
_END_OF_STREAM = (-2, -2)

# Everything that character can be: the end of the stream, the invalid byte, and the codepoints.
_EVERY_CHARACTER = ((_END_OF_STREAM[0], 0x10FFFF),)


def _guards_in_force(parts, at, entering=()):
    """
    The guards that hold where `parts[at]` stands — the one accessor for "what has been asked about this position".

    Three things say it and they say the same thing. What every way that enters this production asked where it entered
    it, which `_asked_where_entered` works out and a caller passes in; what the way's own gate asks, which stands among
    the parts `_parts_of_way` gives; and what a guard already passed over asked. A question that moved from any of those
    places to any other is the same question, which is why one walk reads all three.

    A part that takes characters clears them. Past a take the parse is somewhere else, and every question asked before
    it was about where it no longer is.
    """
    held = list(entering)
    for item in parts[:at]:
        if isinstance(item, _TAKES_CHARACTERS):
            held = []
        elif isinstance(item, _GUARDS):
            held.append(item)
    return tuple(held)


def _ahead_of(guards, grammar):
    """What the character in front can be, given `guards` hold — everything, narrowed by each of them in turn."""
    ahead = _EVERY_CHARACTER
    for guard in guards:
        ahead = _narrowed_ahead(ahead, guard, grammar)
    return ahead


def _ahead_of_any(parts, at, paths, grammar):
    """
    What the character in front of `parts[at]` can be, over every path that enters the way — their union, since any of
    them may be the one the parse took and the answer has to hold for all of them.
    """
    spans = [span for path in paths or ({},) for span in _ahead_of(_guards_in_force(parts, at, path), grammar)]
    return tuple(_merged_spans(spans))


def _entering_guards(grammar):
    """
    `{id(way): guards}` — what holds where each way is entered, worked out once for a grammar and kept.

    Every walk that asks what the character in front can be needs it, and none of them knows which production the way it
    holds belongs to. Rather than thread the answer through each of them — `_is_nullable`, `_split`, the walk for where
    the input ends, all of them readings other readings call — it is looked up here by the way itself.

    Kept against the grammar it was worked out for, and the grammar is held with it: the pipeline builds a new one per
    step and each is asked about many times over.
    """
    held = _ENTERING.get(id(grammar))
    if held is None or held[0] is not grammar:
        asked = _asked_where_entered(grammar)
        by_way = {
            id(way): asked[name]
            for name, production in grammar.items()
            if isinstance(production.body, ir.Choice)
            for way in production.body.alternatives
        }
        _ENTERING[id(grammar)] = held = (grammar, by_way)
    return held[1]


# What `_entering_guards` keeps, per grammar it has been asked about: the grammar itself, so the key cannot be reused
# under it, and the answer.
_ENTERING = {}


def _ahead_of_gate(node, grammar, entering=None):
    """
    What the character in front of a way can be once its gate has admitted it. Anything that is not a way has no gate
    and narrows nothing.

    A guard hoisted into a gate says what a guard among the actions said, so a walk over what a way does has to read the
    gate to see it: `hoist-guards-to-gates` moves the very lookaheads that say a scan behind them takes a character, and
    a walk that read only the actions would call the scan empty again. The same holds one call up — a question hoisted
    out to the ways that enter this one still holds where they enter it — which is what `_entering_guards` says and what
    a caller may override.
    """
    if not isinstance(node, ir.Alternative):
        return _EVERY_CHARACTER
    if entering is None:
        entering = _entering_guards(grammar).get(id(node), ())
    return _ahead_of_any(_parts_of_way(node), len(node.gate.guards), entering, grammar)


def _narrowed_ahead(ahead, item, grammar):
    """
    What the character in front of a way can still be, once `item` has been passed over — the spans it admitted, met
    with what a lookahead among the parts before said of it. Anything else narrows nothing.
    """
    if not isinstance(item, ir.Look):
        return ahead
    return tuple(_spans_meeting(ahead, _peek_spans(item.item, grammar) or []))


def _does_scan_read(item, ahead, grammar):
    """
    Whether `item` is a scan whose own class is known to stand in front of it, so it takes at least one character.

    A `ConsumeSpan` is a run of none or more and takes none exactly where its set is not there. Where the lookaheads
    before it leave nothing the set does not hold, it is there — which is what a `Plus` over a class is written as, and
    a walk reading the pair as if the scan could be empty would report an empty way the input never offers.
    """
    if not isinstance(item, ir.ConsumeSpan):
        return False
    spans = _peek_spans(item.set, grammar)
    if not ahead or spans is None:
        return False
    return all(any(low >= at and high <= to for at, to in spans) for low, high in ahead)


def _can_be_refused(node, grammar, seen=frozenset()):
    """
    Whether some input makes `node` fail and be handed back, rather than matching or raising.

    This is what says whether the ways behind one can be reached: a choice goes on to the next way exactly where this
    one is handed back, so a way no input refuses is the last way the machine ever takes. Matching and raising both stop
    the choice, and neither leaves anything for the way behind to be entered on.

    `seen` holds the productions the walk is already inside, a recursion reached again saying nothing new.
    """
    return _CAN_BE_REFUSED(node, grammar, seen)


def _can_a_way_be_refused(way, grammar, seen):
    """
    Whether some input makes a way fail and be handed back — its items in turn, up to what commits.

    A gate stands in front of the way, so a way carrying one is refused wherever the gate declines. Past a `(cut)` or
    inside a committed region a failure is the message rather than a way handed back, which is why the walk stops
    counting there: `interpreter.match` raises through a region that has not closed and hands back through one that has.
    """
    if _does_a_gate_decide(way):
        return True
    open_regions = 0
    for item in map(_unheld, _items_of_way(way)):
        if isinstance(item, ir.PushMessage):
            open_regions += 1
        elif isinstance(item, ir.PopMessage):
            open_regions -= 1
        elif isinstance(item, ir.Cut):
            return False  # past a cut every failure is the error it names, so nothing behind it is handed back
        elif isinstance(item, ir.Commit):
            continue  # what it holds raises where it fails, the region being the same thing the pair spells
        elif not open_regions and _can_be_refused(item, grammar, seen):
            return True
    return False


def _does_a_gate_decide(way):
    """
    Whether a gate stands in front of a way, so that the way is entered only where the gate holds.

    A character question or a guard, either being a question the machine asks before the way runs: a wrong input turns
    the way away and the choice goes on to the next, whatever the way itself would have done had it been entered.
    """
    return isinstance(way, ir.Alternative) and (bool(way.gate.guards))


_CAN_BE_REFUSED = ir.Reading(
    "whether some input makes a match fail and be handed back, rather than matching or raising",
    {
        # An action leaves something behind and an empty match is the thing itself: neither has an input to fail on. A
        # cut is one of the actions here, matching and turning what fails behind it into the error it names.
        (*_ACTIONS, ir.Empty, *_VALUE_KINDS): False,
        # A character that is not there and a guard that declines are both handed back where they stand.
        (*_ALWAYS_READS, *_GUARDS): True,
        # A run of none or more takes no turn where its set is not there, and a trimmed scan is one of those.
        (ir.ConsumeSpan, ir.ConsumeTrimmedSpan, ir.Opt, ir.Star): False,
        # A counted scan is all or nothing: asked for more characters than are there, it takes none and is handed back.
        ir.ConsumeCountedSpan: lambda node, grammar, seen: not (
            isinstance(node.count, ir.Lit) and node.count.value <= 0
        ),
        ir.LongestRun: lambda node, grammar, seen: node.least > 0 and _can_be_refused(node.item, grammar, seen),
        ir.Plus: lambda node, grammar, seen: _can_be_refused(node.item, grammar, seen),
        # A count of none takes nothing whatever happens; any other turns on what it repeats.
        ir.Rep: lambda node, grammar, seen: not (isinstance(node.count, ir.Lit) and node.count.value <= 0)
        and _can_be_refused(node.item, grammar, seen),
        # A recursion reached again says nothing new, the way in having been judged where it stood.
        ir.Ref: lambda node, grammar, seen: node.name not in seen
        and _can_be_refused(grammar[node.name].body, grammar, seen | {node.name}),
        (ir.Alternative, ir.Seq): _can_a_way_be_refused,
        # A choice is handed back only where every way it offers is: one that matches is the choice matching.
        (ir.Alt, ir.Choice): lambda node, grammar, seen: all(
            _can_be_refused(way, grammar, seen) for way in _ways_or_items(node)
        ),
        # A committed region raises where what it holds fails, so it is never the thing handed back.
        ir.Commit: False,
        (ir.Max, ir.Recover, ir.Token, ir.Wrap): lambda node, grammar, seen: (
            node.item is not None and _can_be_refused(node.item, grammar, seen)
        ),
        ir.Bind: lambda node, grammar, seen: _can_be_refused(node.cond, grammar, seen),
        # A switch the specialization settles: it is handed back only where every branch it could take is.
        ir.Case: lambda node, grammar, seen: all(
            _can_be_refused(item, grammar, seen)
            for item in [branch.item for branch in node.branches] + ([node.default] if node.default else [])
        ),
    },
)


def _every_option_is_reachable(grammar):
    """
    Check that some input reaches every way a choice offers, the ways in front of it being ones an input can refuse.

    A choice goes on to its next way exactly where the one before it fails and is handed back. So a way that no input
    refuses — one that always matches, or whose failure is the error a commit names — is the last way the machine takes,
    and every way behind it is one nothing can enter. Backtracking hides the first half: the way matches, the
    continuation fails, the parse returns and tries the next. A machine that never returns simply loses them.

    So a choice may hold one such way and it must stand last, where it is the fallthrough every choice ends in. Two of
    them is worse than undecidable: the second is unreachable, and nothing about the grammar says which of the two was
    meant.

    What the gates *leave* undecided is a different question, and `every-way-has-simple-gate` asks it of `_entry_of`.
    """
    faults = []
    for name, production in grammar.items():
        for node in _held(production.body):
            offered = (
                node.alternatives if isinstance(node, ir.Choice) else node.items if isinstance(node, ir.Alt) else ()
            )
            for way in offered[:-1]:
                if not _can_be_refused(way, grammar):
                    faults.append(f"{name}: a way no input refuses, with a way behind it")
    return faults


EVERY_OPTION_IS_REACHABLE = Invariant("every-option-is-reachable", _every_option_is_reachable)


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


def _every_ungated_way_has_actions_or_a_call(grammar):
    """
    Check that a way carrying no gate does its actions or makes its call and never both, so a gate can still reach it.

    A way with no gate is one still to be given one, and the two ways to give it one — hoisting a guard up out of its
    callee, writing the callee's ways into it — both need the callee's guards to reach where the way is entered. What
    stands between them is what the way does: a guard crosses an action or provably does not, which
    `GUARD_CROSSES_ACTION` is what says, and a guard behind an action that refuses it is one nothing can bring up. So
    such a way is a way nothing can ever gate.

    Of a way that carries a gate this asks nothing: it is entered on its own questions, does what it does, and calls,
    which the machine runs as one edge. So this empties itself — where `every-conditional-way-is-gated` stands at none
    there are no ways left for it to be about.

    A continuation is not a call. It is entered where the callee left off rather than where the way was, so a way that
    acts and then hands control on is one thing done and then handed on, which is what a way is for.
    """
    return [
        f"{name}: a way nothing has gated acts before it calls, so no guard of its callee can reach it"
        for name, way in _ungated_ways(grammar)
        if way.first is not None and way.actions
    ]


EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL = Invariant(
    "every-ungated-way-has-actions-or-a-call", _every_ungated_way_has_actions_or_a_call
)


def mint_call_states(grammar, namer):
    """
    Give the call a way makes past its own actions a state of its own: what the way does keeps its gate and hands
    control on, and the call and where it comes back to are the minted state's one way.

    A way carries one gate and is asked it where it is entered, so a call made past the way's own actions is a call
    whose callee is entered somewhere else. Minted, the call is the first thing its way does, and the gate that admits
    the way and the gate that admits what it calls stand at one position.

    What the way keeps is its gate, its actions and a tail call, which is no push — so the scopes meet where they met.
    The recovery goes with the call, riding the push the minted state now makes.
    """
    minted = {}

    def told(name, way):
        if way.first is None or not way.actions:
            return way
        held = namer.fresh(name)
        minted[held] = ir.Prod(
            grammar[name].number,
            held,
            (),
            ir.Choice(
                alternatives=(ir.Alternative(gate=ir.Gate(), first=way.first, second=way.second, recover=way.recover),)
            ),
        )
        return ir.Alternative(gate=way.gate, actions=way.actions, second=ir.Ref(name=held, args=()))

    cut = {}
    for name, production in grammar.items():
        body = production.body
        if not isinstance(body, ir.Choice):
            cut[name] = production
            continue
        ways = tuple(told(name, way) for way in body.alternatives)
        cut[name] = dataclasses.replace(production, body=ir.Choice(alternatives=ways))
    return {**cut, **minted}


def mint_guard_states(grammar, namer):
    """
    Give what a way does from its first late guard onward a state of its own: the way keeps its gate and what it did up
    to there and hands control on, and the guard and everything behind it are the minted state's one way.

    A cut and not a reordering, which is the whole of why it is safe. A way's parts run in order, so making the tail a
    production entered where the way left off is the same run, and the guard is asked exactly where it was asked before.
    Moved instead, a guard standing past a consume would be asked of another character — so nothing here has to know
    what an action takes or what it writes, and there are no conditions on which guards may go.

    What the way keeps is its gate, what it did, and a tail call, which is no push, so the scopes meet where they met.
    The call it made and the recovery riding it go with the tail.

    Run until nothing moves: a minted state holds the guard at its head and whatever followed, which may ask late again.
    """

    def told(name, way):
        at = _guard_past_an_action_at(way.actions)
        if at is None:
            return way, {}
        held = namer.fresh(name)
        minted = {
            held: ir.Prod(
                grammar[name].number,
                held,
                (),
                ir.Choice(
                    alternatives=(
                        ir.Alternative(
                            gate=ir.Gate(),
                            actions=way.actions[at:],
                            first=way.first,
                            second=way.second,
                            recover=way.recover,
                        ),
                    )
                ),
            )
        }
        return ir.Alternative(gate=way.gate, actions=way.actions[:at], second=ir.Ref(name=held, args=())), minted

    for _round in ir.rounds("mint-guard-states"):
        cut, minted = {}, {}
        for name, production in grammar.items():
            body = production.body
            if not isinstance(body, ir.Choice):
                cut[name] = production
                continue
            ways = []
            for way in body.alternatives:
                held, made = told(name, way)
                ways.append(held)
                minted.update(made)
            cut[name] = dataclasses.replace(production, body=ir.Choice(alternatives=tuple(ways)))
        settled = {**cut, **minted}
        if settled == grammar:
            return grammar
        grammar = settled


def _every_guard_is_in_a_gate(grammar):
    """
    Check that every guard a way asks stands in its gate rather than among its actions.

    A gate is asked where the way is entered, and `no-guard-stands-past-an-action` puts every guard a way holds in the
    run before it performs anything — so the two stand at one position, and the gate is the one of them a caller can
    see. Left among the actions, the question is reached only by entering the way, which is the entering the gate exists
    to decide.

    One fault per guard rather than per way, a way holding two of them owing two moves.
    """
    return [
        f"{name}: a way asks a guard among its actions, where its gate is asked at the same position"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        for action in way.actions
        if isinstance(action, _GUARDS)
    ]


EVERY_GUARD_IS_IN_A_GATE = Invariant("every-guard-is-in-a-gate", _every_guard_is_in_a_gate)


def hoist_guards_to_gates(grammar, namer):
    """
    Move the guards a way asks out of its actions and into its gate.

    A guard is zero-width, so asking it at the gate and asking it where it stands are the same question — provided
    nothing between the two changes the answer. Nothing can: `no-guard-stands-past-an-action` leaves every guard in the
    run before the way performs anything, so there is nothing between at all. That is the whole of the argument, and it
    is why this needs no account of which action writes what a comparison reads, nor of what commits where.

    What it buys is that the question is in the field a caller can see. A guard among the actions is reached only by
    entering the way; in the gate it is what the entering is decided by, and what a lift can carry to the ways that call
    it.
    """

    def hoisted(way):
        moving = []
        for action in way.actions:
            if not isinstance(action, _GUARDS):
                break
            moving.append(action)
        if not moving:
            return way
        return dataclasses.replace(
            way,
            gate=ir.Gate(guards=(*way.gate.guards, *moving)),
            actions=way.actions[len(moving) :],
        )

    return {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production, body=ir.Choice(alternatives=tuple(hoisted(way) for way in production.body.alternatives))
            )
        )
        for name, production in grammar.items()
    }


def merge_gate_peeks(grammar, _namer):
    """
    Say a gate's questions about the character in front of it as one.

    Two peeks in a gate ask about the same character at the same position, so the sets say between them what one set
    says: what every `Look` admits is what they all admit, what any `NegLook` refuses is refused, and a gate holding
    both admits the first less the second. The gate keeps whatever else it holds — what stands behind, where the parse
    is in the line, how the indentation compares — each of those being about something other than this character.

    A gate reading ahead twice in a way this cannot say as one set is a fault and not a shape to leave alone: an
    `EndOfStream` holds no set and a `LiteralPeek` holds a run rather than one character, so neither can be folded into
    a `Look`, and neither can stand beside one — the parse would be asked twice about a character it reads once, with no
    saying which answer the machine acts on.
    """

    def merged(way):
        ahead = [guard for guard in way.gate.guards if isinstance(guard, _LOOKS_AHEAD)]
        looks = [guard for guard in ahead if isinstance(guard, ir.Look)]
        nots = [guard for guard in ahead if isinstance(guard, ir.NegLook)]
        if len(ahead) < 2:
            return way
        if len(looks) + len(nots) != len(ahead):
            kinds = ", ".join(sorted(type(guard).__name__ for guard in ahead))
            raise AssertionError(f"a gate reads ahead as {kinds}, which is two questions about one character")
        rest = [guard for guard in way.gate.guards if not isinstance(guard, (ir.Look, ir.NegLook))]
        refused = _merged_spans([span for guard in nots for span in guard.item.spans])
        if looks:
            admitted = list(looks[0].item.spans)
            for guard in looks[1:]:
                admitted = _subtracted_spans(admitted, _subtracted_spans(admitted, list(guard.item.spans)))
            asked = ir.Look(item=_spans_node(_subtracted_spans(admitted, refused)))
        else:
            asked = ir.NegLook(item=_spans_node(refused))
        return dataclasses.replace(way, gate=ir.Gate(guards=(*rest, asked)))

    return {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=dataclasses.replace(
                    production.body, alternatives=tuple(merged(way) for way in production.body.alternatives)
                ),
            )
        )
        for name, production in grammar.items()
    }


def _every_gate_looks_ahead_at_most_once(grammar):
    """
    Check that a gate reads what stands in front of the parse at most once.

    Every one of these is a question about the same characters at the same position, so two of them in one gate are one
    question said twice: two `Look`s are the set they both admit, a `Look` beside a `NegLook` is the set the first
    admits and the second does not, and two `NegLook`s are the set neither admits. A gate that keeps them apart makes
    the machine ask twice what it can answer once, and makes every reading of what a way is entered on take the guards
    together before it can say anything about them.

    Only what reads ahead. What stands behind, where the parse is in the line, and how the indentation compares are each
    about something else, and a gate may hold one of those beside its one lookahead.
    """
    return [
        f"{name}: a gate reads what is in front of it {held} times, where once would say the same"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        for held in (sum(isinstance(guard, _LOOKS_AHEAD) for guard in way.gate.guards),)
        if held > 1
    ]


EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE = Invariant(
    "every-gate-looks-ahead-at-most-once", _every_gate_looks_ahead_at_most_once
)


def _ungated_ways(grammar):
    """
    The ways something decides to enter that carry no gate, as `(name, way)` pairs.

    What "ungated" is, asked once and read by everything that asks it: a way of a choice that offers more than one, not
    the last of them, whose own gate holds no guard and where not every path into the production asked one — a guard
    asked where the production is entered being asked where the way is.
    """
    entering = _asked_where_entered(grammar)
    return [
        (name, way)
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice) and len(production.body.alternatives) > 1
        for way in production.body.alternatives[:-1]
        if not way.gate.guards and not all(entering[name])
    ]


def _every_conditional_way_is_gated(grammar):
    """
    Check that every way something decides to enter carries a gate.

    A machine takes a way by asking its gate before entering it, so a way its gate says nothing about is one it would
    have to try and give back — which is the backtracking the whole shape is for getting rid of. What the gate holds
    comes second: each guard is a question the machine can put where it stands — the character in front, the one behind,
    whether any is left, where the parse is in the line, how the indentation compares. That the question is one it can
    answer at all is `every-peek-is-a-character-set`, which holds of every lookaround wherever it stands, a gate
    included.

    Where the question was asked is not the question. A guard the ways that enter this production all asked is asked at
    the position this way is entered at — `_asked_where_entered` is what says so, and a hoist that takes a guard up to
    the callers has moved it, not lost it. So a way stands gated where its own gate holds a guard *or* every path into
    it asked one, the same way `every-consume-is-protected-by-a-gate` accepts a question one call up. Whether the gates
    of a choice tell its ways apart is a later question and emphatically not this one.

    Two kinds of way are not a decision and are not asked. One nothing chooses to enter: a body offering a single way,
    which a caller reaches by calling it and not by picking it — the continuation that pops what a consuming production
    opened, the root, the recovery. And the last way of a choice, which is that choice's else: an empty gate there is
    the fallthrough, and something has to be what happens where nothing else fired.

    Whether the gates of a choice tell its ways apart is a later question and not this one; this asks only that each way
    something decides between has one.
    """
    return [
        f"{name}: a way nothing has asked anything about, where another way stands behind it"
        for name, _way in _ungated_ways(grammar)
    ]


EVERY_CONDITIONAL_WAY_IS_GATED = Invariant("every-conditional-way-is-gated", _every_conditional_way_is_gated)


class Crossing:
    """
    Whether a guard may be asked before an action that stood in front of it, one answer per pair of kinds.

    Three values and not two. A pair the table names is `True` where asking early is the same question and `False` where
    it is not; a pair it does not name is one nobody has worked out, and the difference between "no" and "not yet" is
    the whole point — a walk that took an unnamed pair for `False` would look settled while it was only ignorant. So an
    unnamed pair refuses the move *and* is recorded, and both of these are faults the gate reports:

    - **a pair consulted with nothing recorded**, which says the table is behind what the grammar holds;
    - **a pair recorded that nothing consulted**, which says the table claims to know something it was never asked —
      a guess about the grammar, exactly as an `ir.Reading` handler nothing reaches is.

    Between them the table is pinned to the pairs that occur: what is missing is reported and what is spare is reported,
    so it never quietly grows a claim.
    """

    def __init__(self, told):
        self._told = dict(told)
        self._asked = set()
        self._unnamed = set()

    def may_cross(self, guard, action):
        """Whether `guard` may be asked before `action`, recording the pair either way."""
        pair = (type(guard).__name__, type(action).__name__)
        if pair not in self._told:
            self._unnamed.add(pair)
            return False
        self._asked.add(pair)
        return self._told[pair]

    def unnamed(self):
        """The pairs something consulted and the table does not name."""
        return sorted(self._unnamed)

    def unconsulted(self):
        """The pairs the table names and nothing consulted."""
        return sorted(set(self._told) - self._asked)


# What a guard may be asked in front of: each entry is `(guard, action)` to whether asking the guard there is the same
# question. A pair not here is one nobody has worked out, and consulting it is a fault rather than a no.
GUARD_CROSSES_ACTION = Crossing(
    {
        # A window bounds what a committed consume may take and nothing else: a lookaround reads past its edge freely,
        # which the interpreter says outright where it counts a probe, so neither end of one changes an answer.
        ("Look", "CloseWindow"): True,
        ("NegLook", "CloseWindow"): True,
        ("Look", "OpenWindow"): True,
        # A variable is the parse's own working. A lookaround reads the input and not a variable, so the question is the
        # same either side. A comparison does read one, and no way asks a comparison in front of a write.
        ("Look", "ClearVar"): True,
        # Taking a character moves the parse: a lookaround asked in front of one asks about a different character, and a
        # comparison of a length about a different length.
        ("Le", "ConsumeCountedSpan"): False,
        ("Look", "ConsumeChar"): False,
        ("NegLook", "ConsumeChar"): False,
        ("Look", "ConsumeCountedSpan"): False,
        ("Le", "ConsumeSpan"): False,
        ("Look", "ConsumeSpan"): False,
        ("Lt", "ConsumeSpan"): False,
        ("NegLook", "ConsumeSpan"): False,
        # A committed region is a scope and not a point: inside it, failing is the error it names rather than a refusal
        # handed back. `PushMessage` and `PopMessage` are its two ends and a `Cut` says the same at a point, so a guard
        # crossing any of them is asked on the other side of that line — before the region opened, or while it is still
        # open — and its refusal changes from the one to the other.
        ("Look", "Cut"): False,
        ("Le", "PopMessage"): False,
        ("Lt", "PopMessage"): False,
        # What the parse hands back, which nothing asked of the input or of a count reads.
        ("EndOfStream", "Emit"): True,
        ("LiteralPeek", "Emit"): True,
        ("Look", "Emit"): True,
        ("Lt", "Emit"): True,
        ("NegLook", "Emit"): True,
        ("StartOfLine", "Emit"): True,
        # A code is the token being built. It is neither the input nor a count, so every guard passes it.
        ("Le", "PopCode"): True,
        ("Look", "PopCode"): True,
        ("Lt", "PopCode"): True,
        ("NegLook", "PopCode"): True,
        # The indentation the parse carries: a lookaround reads the input and not that, so it passes either end of a
        # push; a comparison reads exactly what these write, so it does not.
        ("Look", "PopIndent"): True,
        ("Look", "PopMessage"): False,
        ("Le", "PushCode"): True,
        ("LiteralPeek", "PushCode"): True,
        ("Look", "PushCode"): True,
        ("Lt", "PushCode"): True,
        ("NegLook", "PushCode"): True,
        ("StartOfLine", "PushCode"): True,
        ("Look", "PushIndent"): True,
        # A comparison reads the indentation, which this writes: asked in front of it, it reads the one before.
        ("Lt", "PushIndent"): False,
        ("NegLook", "PushIndent"): True,
        ("Le", "PushMessage"): False,
        ("Look", "PushMessage"): False,
        ("Look", "SetVar"): True,
        ("NegLook", "SetVar"): True,
        # A turn that must take a character records where it began. Nothing about the input, a count, the indentation or
        # the token is written, and the region decides nothing until its close — so a guard asked either side of the
        # open asks the same question of the same character, whatever it asks about.
        ("Le", "StartMustConsume"): True,
        ("LiteralPeek", "StartMustConsume"): True,
        ("Look", "StartMustConsume"): True,
        ("Lt", "StartMustConsume"): True,
        ("NegLook", "StartMustConsume"): True,
        ("StartOfLine", "StartMustConsume"): True,
        # A settled region's close says where a later failure goes and nothing about what any guard reads.
        ("LiteralPeek", "PopBackTrack"): True,
        ("Look", "PopBackTrack"): True,
        # A turn that must take a character asks where the parse stands against where its own open stood. Whatever takes
        # a character moves it, so the question is a different one on the other side; a marker or a code moves nothing,
        # and it is the same one. An inner turn's open standing between the two is not passed either: asked in front of
        # one, the question is answered before that turn has run.
        ("EndMustConsume", "ConsumeChar"): False,
        # Nor a committed region's close, which is the line a refusal changes meaning across, as it is for every guard.
        ("EndMustConsume", "PopMessage"): False,
        ("EndMustConsume", "ConsumePeeked"): False,
        ("EndMustConsume", "ConsumeSpan"): False,
        ("EndMustConsume", "Emit"): True,
        ("EndMustConsume", "PopCode"): True,
        ("EndMustConsume", "PushCode"): True,
        ("EndMustConsume", "StartMustConsume"): False,
        # An error token is what the parse hands back, as a marker is: nothing asked of the input or of a count reads
        # one.
        ("EndOfStream", "Error"): True,
        ("LiteralPeek", "Error"): True,
        ("Look", "Error"): True,
        # The indentation the parse carries is not the input, which is what these two read.
        ("EndOfStream", "PushIndent"): True,
        ("LiteralPeek", "PushIndent"): True,
        # What may not match at a start of line is read by whatever matches there, and a `LiteralPeek` matches its
        # characters through the same refusal a `Look` matches its item through — so each reads the set standing where
        # it is asked. Whether a character is there at all matches nothing and reads none of it.
        ("EndOfStream", "SetForbidden"): True,
        ("LiteralPeek", "SetForbidden"): False,
        ("Look", "SetForbidden"): False,
    }
)


def _run_of_one_character_sets(actions):
    """
    Where a run of two or more sets each holding one codepoint begins among `actions`, and how long it is — or `None`.

    Adjacent and nothing between them: characters taken one after another with no action in the middle are one literal
    the input either begins with or does not. Where an action stands between two of them they belong to different
    tokens, and folding them would make one token of two.
    """
    at = None
    for index, action in enumerate(actions):
        is_one = isinstance(action, ir.CharSet) and len(action.spans) == 1 and action.spans[0][0] == action.spans[0][1]
        if not is_one:
            if at is not None and index - at > 1:
                return at, index - at
            at = None
        elif at is None:
            at = index
    return (at, len(actions) - at) if at is not None and len(actions) - at > 1 else None


def _one_character_called(node, grammar):
    """The set a call names, where what it calls is one character and nothing else — else `None`."""
    if not isinstance(node, ir.Ref) or node.name not in grammar:
        return None
    body = grammar[node.name].body
    return (
        body if isinstance(body, ir.CharSet) and len(body.spans) == 1 and body.spans[0][0] == body.spans[0][1] else None
    )


def _characters_pulled_in(way, grammar):
    """
    `way` with a call to a one-character production standing where the character does, or `way` unchanged.

    The base grammar mirrors the official one, which gives some characters a rule of their own: `b-break` is `CR LF`
    said as two calls, where `c-directives-end` is `---` said as three characters. The two spell the same kind of thing,
    and a literal is what the machine wants of either — so the calls are pulled in first and the run is looked for once.

    Only where the way performs nothing of its own. What a way does comes before what it calls, so a call pulled in
    ahead of an action would be taken before the action rather than after it.
    """
    if way.actions or way.recover is not None:
        return way
    held = [one for one in (way.first, way.second) if one is not None]
    taken = [_one_character_called(one, grammar) for one in held]
    if len(held) < 2 or any(one is None for one in taken):
        return way
    return dataclasses.replace(way, actions=tuple(taken), first=None, second=None)


def fold_literals_into_gates(grammar, namer):
    """
    Say a run of single characters as the literal it is: `A = |g '-' '-' '-' rest|` becomes `A = |g <"---">
    ConsumePeeked("---") rest|`.

    Three sets taken one after another are three decisions where the input offers one: either it begins with `---` or it
    does not. Asked as a literal the gate holds the whole of it, and the generated parser answers with a single
    comparison where a per-character split would spend a state on each — which is what `LiteralPeek` was written for.

    Only where the sets are adjacent. An action between two of them means they are taken into different tokens, and one
    literal in their place would make one token of two.

    Where the run is not the first thing the way does, the question moves in front of whatever stood before it, and
    `GUARD_CROSSES_ACTION` says whether it may.
    """

    def told(way):
        way = _characters_pulled_in(way, grammar)
        found = _run_of_one_character_sets(way.actions)
        if found is None:
            return way
        at, length = found
        text = tuple(action.spans[0][0] for action in way.actions[at : at + length])
        asked = ir.LiteralPeek(text=text, then=None, barrier=None)
        if not all([GUARD_CROSSES_ACTION.may_cross(asked, action) for action in way.actions[:at]]):
            return way
        return dataclasses.replace(
            way,
            gate=ir.Gate(guards=(*way.gate.guards, asked)),
            actions=(*way.actions[:at], ir.ConsumePeeked(text=text), *way.actions[at + length :]),
        )

    return {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production, body=ir.Choice(alternatives=tuple(told(way) for way in production.body.alternatives))
            )
        )
        for name, production in grammar.items()
    }


def split_counted_spans_on_the_count(grammar, namer):
    """
    Give a run of a set counted by the parse the two ways it is: `A = |g … span(n, S) …|` becomes `A = |g 0<n <S> …
    span(n, S) …| |g n<=0 … …|`.

    A counted run takes `n` characters, and `n` is what the parse worked out rather than what the grammar wrote — so
    where it is none the run takes nothing and the way matches empty, and where it is more the way is entered on a
    character of `S`. Both are questions the machine answers where it stands: the comparison against the count it
    carries, and the character in front of it. Said as two ways, each is one thing and each says which.

    The order is the run first and the empty way behind it, which is the order a counted run already tries them in.

    Both gates ask in front of whatever the way performs before the run, so `GUARD_CROSSES_ACTION` says whether they may
    — and where any of them may not, the way is left whole.
    """

    def told(way):
        at = next(
            (index for index, action in enumerate(way.actions) if isinstance(action, ir.ConsumeCountedSpan)),
            None,
        )
        if at is None:
            return (way,)
        span = way.actions[at]
        asked = ir.Look(item=as_char_set(span.set, grammar))
        if isinstance(span.count, ir.Lit):
            # The count is the grammar's own and positive, so the run always takes: there is no second way to say, and
            # what the gate owes is only that a character of the set is there.
            if not all([GUARD_CROSSES_ACTION.may_cross(asked, action) for action in way.actions[:at]]):
                return (way,)
            return (dataclasses.replace(way, gate=ir.Gate(guards=(*way.gate.guards, asked))),)
        taking = ir.Lt(a=ir.Lit(value=0), b=span.count)
        none = ir.Le(a=span.count, b=ir.Lit(value=0))
        if not all(
            [
                GUARD_CROSSES_ACTION.may_cross(guard, action)
                for guard in (taking, asked, none)
                for action in way.actions[:at]
            ]
        ):
            return (way,)
        return (
            dataclasses.replace(way, gate=ir.Gate(guards=(*way.gate.guards, taking, asked))),
            dataclasses.replace(
                way,
                gate=ir.Gate(guards=(*way.gate.guards, none)),
                actions=(*way.actions[:at], *way.actions[at + 1 :]),
            ),
        )

    return {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=ir.Choice(alternatives=tuple(one for way in production.body.alternatives for one in told(way))),
            )
        )
        for name, production in grammar.items()
    }


def _every_way_takes_at_most_once(grammar):
    """
    Check that a way takes characters at most once, so that what it takes is what its own gate found.

    A gate speaks for the position the way is entered at. Past a take the parse stands somewhere else, so a second take
    in the same way is one no gate of that way can vouch for, however the questions are moved about. Given a way of its
    own it is entered where its own gate can be asked.
    """
    return [
        f"{name}: a way takes twice, and its gate can speak for only the first"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        if sum(isinstance(item, _TAKES_CHARACTERS) for item in _items_of_way(way)) > 1
    ]


EVERY_WAY_TAKES_AT_MOST_ONCE = Invariant("every-way-takes-at-most-once", _every_way_takes_at_most_once)


def mint_consume_states(grammar, namer):
    """
    Cut a way where it would take a second character on the strength of the first: `A = |g … take … CharSet rest|`
    becomes `A = |g … take →A′|` with `A′ = |— … CharSet rest|`.

    A gate speaks for the position the way is entered at. Past a take the parse stands somewhere else, so a set behind
    one is a question the way's gate cannot ask however the table answers — the way holds two entries and has one gate.
    Given the rest of it a state of its own, the second take is entered where its own gate can be asked, and
    `split-consumes-into-gates` puts the question there.
    """
    minted = {}

    def told(name, way):
        taken = False
        for at, action in enumerate(way.actions):
            if isinstance(action, _TAKES_CHARACTERS) and taken:
                break
            taken = taken or isinstance(action, _TAKES_CHARACTERS)
        else:
            return way
        held = namer.fresh(name)
        minted[held] = ir.Prod(
            grammar[name].number,
            held,
            (),
            ir.Choice(
                alternatives=(
                    ir.Alternative(
                        gate=ir.Gate(),
                        actions=way.actions[at:],
                        first=way.first,
                        second=way.second,
                        recover=way.recover,
                    ),
                )
            ),
        )
        return dataclasses.replace(
            way, actions=way.actions[:at], first=None, second=ir.Ref(name=held, args=()), recover=None
        )

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=ir.Choice(alternatives=tuple(told(name, way) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


# What moves the parse on, so a question asked in front of it no longer speaks for where the parse now stands. The
# counterpart of the actions, which leave the position where they found it.
_TAKES_CHARACTERS = (
    ir.CharSet,
    ir.ConsumeChar,
    ir.ConsumeCountedSpan,
    ir.ConsumeLiteral,
    ir.ConsumePeeked,
    ir.ConsumeSpan,
    ir.ConsumeTrimmedSpan,
)


def _asked_where_entered(grammar):
    """
    `{name: paths}` — a set holding, per way that enters the production, the set of guards asked where it enters it.

    A gate speaks for the position the way is entered at, and a call made before the way performs anything is made at
    that same position. So a question the caller asked holds where the callee begins, and it holds however many calls
    deep — which is what lets a guard move up to the choice that needs it without the question being lost to whatever it
    protected.

    One entry per path rather than the guards they share, because two questions are asked of this and they differ.
    Whether every path asked about the character at all is a yes-or-no, and seventeen escapes reaching one shared tail
    each ask about a different character while every one of them asks. What that character can be is a set, and the
    answer is the union over the paths, any of them being the one taken. Handing the paths back lets each site say which
    it wants — `_ahead_of_any` unions them, `_guards_in_force` reads one.

    Each path is a set: a gate has no order between its guards, so two paths asking the same questions are one path.

    A path that has moved contributes nothing, a caller taking a character before the call being one that asks about
    somewhere else. A production entered by name has no caller to ask and neither has one nothing calls; both come back
    as the single empty path, nothing being known where they begin. A least fixed point, since what is known at a call
    site includes what was known where the caller itself began.
    """
    held = _ENTERED.get(id(grammar))
    if held is not None and held[0] is grammar:
        return held[1]
    entered = entered_by_name(grammar)
    nothing = frozenset({frozenset()})
    # Who enters what, worked out once: `{callee: [(caller, the guards asked there, whether the parse has moved)]}`.
    # Read the other way round it is one scan of the grammar per round per name, which on a grammar of any size is the
    # whole cost of this.
    sites = {}
    for holder, production in grammar.items():
        if not isinstance(production.body, ir.Choice):
            continue
        for way in production.body.alternatives:
            call = way.first if way.first is not None else way.second
            if isinstance(call, ir.Ref) and call.name in grammar:
                has_moved = any(isinstance(action, _TAKES_CHARACTERS) for action in way.actions)
                sites.setdefault(call.name, []).append((holder, frozenset(way.gate.guards), has_moved))
    known = {name: frozenset() for name in grammar}
    for _round in ir.rounds("what is asked where each production is entered"):
        settled = {}
        for name in grammar:
            if name in entered:
                settled[name] = nothing
                continue
            paths = set()
            for holder, guards, has_moved in sites.get(name, ()):
                if has_moved:
                    paths.add(frozenset())
                else:
                    paths |= {above | guards for above in known[holder] or nothing}
            settled[name] = frozenset(paths) or nothing
        if settled == known:
            _ENTERED[id(grammar)] = (grammar, settled)
            return settled
        known = settled


# What `_asked_where_entered` keeps, per grammar it has been asked about: the grammar itself, so the key cannot be
# reused under it, and the answer.
_ENTERED = {}


def _no_char_set_is_an_item(grammar):
    """
    Check that no way holds a character set among what it performs.

    A question and a taking are two things. A `CharSet` standing where the machine performs things is both at once: it
    asks whether the character belongs to the set and takes it in the same breath, so the answer is reached only by
    entering the way — when the answer is what entering the way should have been decided by. Split, the set is the
    question a gate holds and `ConsumeChar` is the taking.

    The set itself does not go. It stays as the question, inside the `Look` of a gate and as the class a scan runs over;
    what goes is its standing among the actions as a match of its own.
    """
    # A production that *is* a character set is the class itself — a scan names it as what it runs over, an exclusion as
    # what it forbids, and `every-character-question-is-a-character-set` holds it to being one. Calling it is what asks
    # and takes in one step, so the call is the fault and the production is not.
    faults = [
        f"{name}: a way calls a character set, asking and taking in one step"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        for item in _items_of_way(way)
        if isinstance(item, ir.Ref) and item.name in grammar and isinstance(grammar[item.name].body, ir.CharSet)
    ]
    return faults + [
        f"{name}: a way asks a character set and takes the character in one step"
        for name, production in grammar.items()
        if isinstance(production.body, ir.Choice)
        for way in production.body.alternatives
        for item in _items_of_way(way)
        if isinstance(item, ir.CharSet)
    ]


NO_CHAR_SET_IS_AN_ITEM = Invariant("no-char-set-is-an-item", _no_char_set_is_an_item)


def _every_consume_is_protected_by_a_gate(grammar):
    """
    Check that every take is protected by a gate that found what it takes — a `Look` or a `LiteralPeek`, in the way's
    own gate or in one asked wherever the way is entered.

    A take that nothing vouched for is a match the parse has to try and give back, which is the backtracking the shape
    is for removing. The gate is where the input is asked, and what it found is what the take is entitled to.

    Not only the way's own gate. A question asked by every way that enters this one, at the position it enters it, holds
    here too — `_asked_where_entered` is what says so, and it is what lets a guard move up to the choice that needs it
    without the take it protected losing its warrant.
    """
    faults = []
    entering = _asked_where_entered(grammar)
    for name, production in grammar.items():
        body = production.body
        # A body that is not a choice offers no way to read a gate off, so what holds where it begins is only what the
        # ways that enter it asked. A take in there is a take like any other: `b-carriage-return` is one character and
        # nothing else, and the character it takes wants finding before it is taken as much as any.
        ways = (
            body.alternatives
            if isinstance(body, ir.Choice)
            else (ir.Alternative(gate=ir.Gate(), actions=tuple(_held(body))),)
        )
        for way in ways:
            parts = _parts_of_way(way)
            # Every path into the way must have asked, and they need not have asked the same thing: seventeen escapes
            # reaching one shared tail each look at a different character, and every one of them looks.
            faults += [f"{name}: a way takes what no gate found"] * sum(
                isinstance(item, _TAKES_CHARACTERS)
                and not isinstance(item, ir.CharSet)
                and not all(
                    any(isinstance(guard, (ir.Look, ir.LiteralPeek)) for guard in _guards_in_force(parts, at, path))
                    for path in entering[name] or ({},)
                )
                for at, item in enumerate(parts)
            )
    return faults


EVERY_CONSUME_IS_PROTECTED_BY_A_GATE = Invariant(
    "every-consume-is-protected-by-a-gate", _every_consume_is_protected_by_a_gate
)


def split_consumes_into_gates(grammar, namer):
    """
    Say the asking and the taking as two things: `A = |g CharSet(S) rest|` becomes `A = |g Look(S) ConsumeChar rest|`.

    A character set standing among what a way performs asks whether the character belongs to it and takes it in the same
    breath, so the answer is reached only by entering the way — when the answer is what entering the way should have
    been decided by. Split, the question is in the gate where a caller can see it, and `ConsumeChar` takes the character
    the gate has already found.

    This is where the gates come from. A hoist moves a question that exists; nothing before this makes one, which is why
    the ways that decide nothing outnumber the ones that do until it has run.

    Where the set is not the first thing the way does, the question moves in front of whatever stood before it, and
    `GUARD_CROSSES_ACTION` says whether it may. One set per way per pass: a second would have to cross the take the
    first left behind, and a character already taken is a different position.

    A call to a production that is a character set is the same thing said one call away — `b-break`'s second way is
    `b-carriage-return`, which is `CR` and nothing else. The set stands where the call did and is then split like any
    other. The production stays as it is: a scan names it as what it runs over and an exclusion as what it forbids, and
    there it is the class rather than a match.
    """

    wrapped = {}

    def gated(name, body):
        """The name of a way that asks for `body` and takes it, minted where a call to that class first needs one."""
        if name not in wrapped:
            held = namer.fresh(name)
            wrapped[name] = held
            wrapped[held] = ir.Prod(
                grammar[name].number,
                held,
                (),
                ir.Choice(
                    alternatives=(
                        ir.Alternative(gate=ir.Gate(guards=(ir.Look(item=body),)), actions=(ir.ConsumeChar(),)),
                    )
                ),
            )
        return wrapped[name]

    def pulled(way):
        """`way` with every call to a character set standing as that set, or aimed at a way that asks for it."""
        for _round in ir.rounds("the classes a way calls"):
            settled = _pulled_once(way)
            if settled == way:
                return way
            way = settled

    def _pulled_once(way):
        """One such call moved — the first the way makes, since moving it changes what the next one may do."""
        for slot in ("first", "second"):
            held = getattr(way, slot)
            if not isinstance(held, ir.Ref) or held.name not in grammar or way.recover is not None:
                continue
            body = grammar[held.name].body
            if not isinstance(body, ir.CharSet):
                continue
            # A way takes at most once. Where it already takes, the class cannot come in beside it and the call is aimed
            # at a way that asks for it instead — the class itself stays, a scan naming it as what it runs over.
            if any(isinstance(action, _TAKES_CHARACTERS) for action in way.actions):
                return dataclasses.replace(way, **{slot: ir.Ref(name=gated(held.name, body), args=held.args)})
            if slot == "first" and way.second is not None:
                return dataclasses.replace(way, actions=(*way.actions, body), first=None)
            return dataclasses.replace(way, actions=(*way.actions, body), **{slot: None})
        return way

    def told(way):
        at = next((index for index, action in enumerate(way.actions) if isinstance(action, ir.CharSet)), None)
        if at is None:
            return way
        asked = ir.Look(item=way.actions[at])
        if not all([GUARD_CROSSES_ACTION.may_cross(asked, action) for action in way.actions[:at]]):
            return way
        return dataclasses.replace(
            way,
            gate=ir.Gate(guards=(*way.gate.guards, asked)),
            actions=(*way.actions[:at], ir.ConsumeChar(), *way.actions[at + 1 :]),
        )

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=ir.Choice(alternatives=tuple(told(pulled(way)) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **{name: held for name, held in wrapped.items() if isinstance(held, ir.Prod)}}


def _called_first(way, grammar):
    """
    The name of what `way` enters first, where that is a production offering ways — else `None`.

    `first` is the call and `second` where the way carries on from it, so what a way enters first is its `first`, or its
    `second` where it makes no call of its own.
    """
    held = way.first if isinstance(way.first, ir.Ref) else (way.second if way.first is None else None)
    if not isinstance(held, ir.Ref) or held.name not in grammar:
        return None
    return held.name if isinstance(grammar[held.name].body, ir.Choice) else None


def _asked_by_every_way(name, grammar):
    """
    The guards every way of `name` asks — what entering it asks whichever way it goes, and so what a caller could ask
    instead.

    A production offering one way is the plain case, its only way's gate being all of them. Where it offers several, a
    guard in all of them is asked before any of them is chosen, so taking it out of each and asking it at the call is
    the same question in the same place. A guard in some of them is not: it is what tells those ways from the rest.

    A body that is not a choice offers no ways, and so asks nothing a caller could ask instead.
    """
    body = grammar[name].body
    if not isinstance(body, ir.Choice):
        return set()
    ways = body.alternatives
    shared = set(ways[0].gate.guards)
    for way in ways[1:]:
        shared &= set(way.gate.guards)
    return shared


def _carrying_on_to(way, tail, owner, grammar, namer, minted, named=None):
    """
    `way` with `tail` behind everything it already does — the one place the slots are dealt with.

    A way holds a call and a continuation and no more, so where to put `tail` depends on what is already there. Nothing
    where it carries on: `tail` becomes that. A continuation and no call: what it carried on to becomes the call and
    `tail` what follows it, the same two matches in the same order. Both: there is no slot left, so what it carried on
    to and then `tail` are given a state of their own and the way carries on to that.

    The middle case is left alone where the way holds a recovery. A recovery rides the push its call makes, and making a
    call of what was a continuation would have it ride a push that was not there before.
    """
    if way.second is None:
        return dataclasses.replace(way, second=tail)
    if way.first is None and way.recover is None:
        return dataclasses.replace(way, first=way.second, second=tail)
    body = ir.Choice(alternatives=(ir.Alternative(gate=ir.Gate(), first=way.second, second=tail),))
    named = {} if named is None else named
    held = named.get((owner, body))
    if held is None or held not in grammar:
        held = held or namer.fresh(owner)
        named[owner, body] = held
        minted[held] = ir.Prod(grammar[owner].number, held, (), body)
    return dataclasses.replace(way, second=ir.Ref(name=held, args=()))


def _has_an_ungated_decision(name, grammar):
    """Whether `name` offers ways something decides between and one of them, bar the last, carries no gate."""
    body = grammar[name].body
    ways = body.alternatives if isinstance(body, ir.Choice) else ()
    return len(ways) > 1 and any(not way.gate.guards for way in ways[:-1])


def lower_gated_continuations(grammar, namer):
    """
    Put what a call carries on to inside the callee, where the callee has a way nothing can be entered on: `D = |gD actD
    →A →cont|` becomes `D = |gD actD →A′|`, with `A′` the ways of `A` each carrying on to `cont`.

    The same matches in the same order — `A` was tried a way at a time with `cont` behind whichever matched, and `A′` is
    that written out. What it buys is where `cont` stands: a way of `A` that asks nothing now *enters* `cont`, and
    `hoist-guards-to-callers` takes a question from what a way enters. Lowering asks nothing of the input and gates
    nothing by itself; the hoist reaches nothing without it.

    Only where `cont` has something to give — a guard every one of its ways asks, which is what the hoist can take.
    Lowering one that has none moves a call for nothing.

    `cont`'s own guards go nowhere. They are asked where `cont` is entered, which is where the parse stands once `A` has
    returned, and that is the same position in `A′` as it was in `D`: what `D`'s gate asked spoke for where `D` was
    entered, and `A` may have taken characters since.
    """
    minted, named = {}, {}

    def lowered(name, tail):
        """The name of `name`'s ways each carrying on to `tail`, minted where that pair is first wanted."""
        key = (name, tail.name, tail.args)
        if key not in named:
            held = namer.fresh(name)
            named[key] = held
            minted[held] = dataclasses.replace(
                grammar[name],
                name=held,
                body=ir.Choice(
                    alternatives=tuple(
                        _carrying_on_to(way, tail, name, grammar, namer, minted)
                        for way in grammar[name].body.alternatives
                    )
                ),
            )
        return named[key]

    def told(way):
        if not isinstance(way.first, ir.Ref) or not isinstance(way.second, ir.Ref):
            return way
        if not _has_an_ungated_decision(way.first.name, grammar):
            return way
        if not _asked_by_every_way(way.second.name, grammar):
            return way
        held = lowered(way.first.name, way.second)
        return dataclasses.replace(way, first=ir.Ref(name=held, args=way.first.args), second=None)

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production, body=ir.Choice(alternatives=tuple(told(way) for way in production.body.alternatives))
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _inlined_called_ways(grammar, namer=None):
    """
    Put a production offering one way where the call to it stands: `A = |gA actA →B sA|` with `B = |actB fB sB|` becomes
    `A = |gA actA actB fB sA|`.

    A call to a production offering one way is unconditional — entering it is running that way — so what it does can
    stand where the call did. What it buys is that the question `B` would have been entered on is now a question this
    way enters on, which is what a hoist can reach; a call in between is what has been keeping them apart.

    Every site, not only a lone one. A callee reached from two ways is written into both, which is a copy of what it
    does rather than a second answer, and a callee every site inlines is one nothing calls and the sweep takes away.

    Only where `B` asks nothing. Its gate is asked where `B` is entered, which is past what the caller performs, so
    moving it up is the hoist's business and subject to the table; a gate left in the middle of a way is a question
    asked after the way was entered, which `every-guard-is-in-a-gate` is there to forbid. Where `B` asks something the
    hoist takes it first, and `B` is inlined on a later round with nothing left to ask.

    Only where neither way carries a recovery. A recovery rides the push its call makes, and the call it rides changes
    here.

    A way holds a call and a continuation, and inlining leaves the callee's call, the callee's continuation and the
    caller's own to place in the two. Where all three are there they are re-associated rather than refused: `(D E) C` is
    `D (E C)`, so the way calls `D` and carries on to a state holding `E` and then `C`. That is the right-oriented shape
    — a call and a tail — and it is why the state minted for the tail is not the state the inlining removed: the call to
    `B` goes, `B` goes with it where nothing else calls it, and what is left is one call fewer at every site.

    Where there is no namer to mint with, such a way is left alone. The sweep runs in places that have none.
    """
    minted = {}

    def inlined(way):
        # The call is `first`, or `second` where the way makes none of its own — a tail call is a call like any other,
        # and one whose callee is written in has nothing left to carry on to, so it always fits.
        held = way.first if isinstance(way.first, ir.Ref) else (way.second if way.first is None else None)
        if not isinstance(held, ir.Ref) or way.recover is not None:
            return way
        body = grammar[held.name].body
        if not isinstance(body, ir.Choice) or len(body.alternatives) != 1:
            return way
        [only] = body.alternatives
        if only.gate.guards or only.recover is not None:
            return way
        # A guard among what the callee performs would land past what the caller performs, which is a question asked
        # after the way was entered. Until `no-guard-stands-past-an-action` has run there are such guards, and this
        # sweep runs at every stage.
        if any(isinstance(action, _GUARDS) for action in only.actions):
            return way
        actions = (*way.actions, *only.actions)
        if not actions and only.first is None and only.second is None:
            return way  # what is left would be a way that performs nothing and calls nothing
        if actions and only.first is not None:
            return way  # a way acts or calls and never both, so that it is entered where it calls
        if sum(isinstance(action, _TAKES_CHARACTERS) for action in actions) > 1:
            # A way takes once, so that what it takes is what its own gate found. `mint-consume-states` cuts a way that
            # would take twice, and writing that cut back in would undo it — the two would trade the same way forever.
            return way
        # What the way carries on to once the callee has run — nothing, where the callee was the tail call itself.
        carries = None if way.first is None else way.second
        if carries is None:
            return dataclasses.replace(way, actions=actions, first=only.first, second=only.second)
        if only.second is None:
            return dataclasses.replace(way, actions=actions, first=only.first, second=carries)
        if namer is None:
            return way
        tail = namer.fresh(held.name)
        minted[tail] = ir.Prod(
            grammar[held.name].number,
            tail,
            (),
            ir.Choice(alternatives=(ir.Alternative(gate=ir.Gate(), first=only.second, second=carries),)),
        )
        return dataclasses.replace(way, actions=actions, first=only.first, second=ir.Ref(name=tail, args=()))

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=ir.Choice(alternatives=tuple(inlined(way) for way in production.body.alternatives)),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def expand_called_ways(grammar, namer):
    """
    Offer a callee's ways where the call stood: `A = |gA →B cont| |…|` with `B = |g0 …| |g1 …|` becomes `A = |gA g0 …
    cont| |gA g1 … cont| |…|`.

    A way whose gate says nothing about it and whose callee's ways each say something of their own cannot take those
    questions by hoisting — they differ, which is what tells the callee's ways apart, and there is no one guard the
    callee is entered on. Written out where the call stood, each way keeps its own question and the caller has as many
    ways as the callee offered, each of them gated.

    In place and in order. The callee's ways stand where the call did, in the order it offered them, so the parse falls
    between them exactly where it fell before.

    Not where the way holds a committed region open at the call. `PushMessage` runs its continuation and raises where
    that fails with the region unclosed, so the callee's choice must stand *inside* that continuation; copied out, each
    way is its own continuation, the first to fail raises, and the ways behind it are never reached.

    Not where a recovery rides the call, which would then ride each copy. And what a way carries on to is placed as
    `_carrying_on_to` places it — behind the callee's own continuation, in a state of its own where both are there.

    Run until nothing moves. A way written out here is one its own callers can write out in turn, so the questions come
    up a level at a time until they reach a way that performs something no guard may cross. What ends it is that every
    site taken leaves one fewer way nothing has gated: a site is judged by what it would leave, and one that leaves a
    way still ungated is not taken at all. So the count falls with every site and the walk has a floor to reach.
    """
    named = {}  # kept across the rounds, so a site taken twice names what it needs the same both times
    for _round in ir.rounds("expand-called-ways"):
        settled = cleaned(_expanded_once(grammar, namer, named), namer)[0]
        namer.sees(settled)
        if settled == grammar:
            return grammar
        grammar = settled


def _is_wholly_gated(grammar, held):
    """Whether `held` calls a production that offers ways and every one of them carries a gate."""
    if not isinstance(held, ir.Ref) or held.name not in grammar:
        return False
    body = grammar[held.name].body
    return isinstance(body, ir.Choice) and bool(body.alternatives) and all(one.gate.guards for one in body.alternatives)


def _expanded_once(grammar, namer, named=None):
    """One pass of `expand-called-ways`: every call whose callee's ways may stand where it does, written out."""
    minted = {}
    named = {} if named is None else named

    def told(owner, way):
        held = way.first if isinstance(way.first, ir.Ref) else (way.second if way.first is None else None)
        if way.gate.guards or not isinstance(held, ir.Ref) or way.recover is not None:
            return (way,)
        body = grammar[held.name].body
        if not isinstance(body, ir.Choice) or len(body.alternatives) < 2:
            return (way,)
        # A way asks its gate before it performs anything, so a callee's guard written in here is asked in front of what
        # the caller performs rather than behind it. That is the crossing the table answers, and every guard of every
        # way has to be admitted — one that is not leaves the whole call where it stands.
        if not all(
            [
                GUARD_CROSSES_ACTION.may_cross(guard, action)
                for one in body.alternatives
                for guard in one.gate.guards
                for action in way.actions
            ]
        ):
            return (way,)
        carries = way.second if way.first is not None else None
        opened = []
        for one in body.alternatives:
            standing = dataclasses.replace(one, actions=(*way.actions, *one.actions))
            if carries is not None:
                standing = _carrying_on_to(standing, carries, owner, grammar, namer, minted, named)
            # A gated way that acts and then calls is one edge the machine runs. An ungated one is a way still to be
            # given a gate, and no guard of its callee can reach it past what it performs.
            if not standing.gate.guards and standing.actions and standing.first is not None:
                return (way,)
            opened.append(standing)
        # What this site is worth, read off what it would leave rather than off what it was handed: one way nothing has
        # gated goes, and whatever comes out ungated stands in its place. Where they all come out gated the site pays
        # and the ways stand here, which is what lets every paying site be taken at once.
        if not all(one.gate.guards for one in opened):
            return (way,)
        return tuple(opened)
        # ruff: noqa — the lowering below is held out of the way while the reading is settled

        # Where one does not, the same ways are worth having somewhere else. Standing in a production of their own, the
        # one that asks nothing carries on at what this way carried on at, and a guard of *that* is what admits it — a
        # question this way could not ask, since what follows the call is not what follows the callee's own way. So the
        # tail goes down into the callee and this way hands control on to it: nothing is decided and nothing is copied
        # out, and there is no coming back from it, what followed the call now being the end of every way it offers.
        if carries is None or way.actions:
            return (way,)
        standing = named.get((held.name, carries))
        if standing is None or standing not in grammar:
            standing = standing or namer.fresh(held.name)
            named[held.name, carries] = standing
            minted[standing] = ir.Prod(grammar[held.name].number, standing, (), ir.Choice(alternatives=tuple(opened)))
        return (dataclasses.replace(way, first=None, second=ir.Ref(name=standing, args=())),)

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production,
                body=ir.Choice(
                    alternatives=tuple(one for way in production.body.alternatives for one in told(name, way))
                ),
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def hoist_guards_to_callers(grammar, namer):
    """
    Ask a callee's guards where its caller is entered rather than where the call is made: `A = |gA actA →B|` with `B =
    |gB actB …|` becomes `A = |gA gB actA →B′|`, `B′` being `B` with the guards it no longer asks.

    A guard takes nothing, so where it is asked changes what is decided and not what is matched — provided nothing
    between the two positions changes the answer. What stands between is `actA`, and `GUARD_CROSSES_ACTION` is what says
    whether a guard may be asked in front of an action rather than behind it. A guard moves only where every action it
    would cross admits it; one that cannot stays where it is, and the rest still move.

    What moves is what `_asked_by_every_way` says: a callee offering one way gives its gate, and one offering several
    gives the guards all of them ask, which are asked before any of them is chosen. A guard only some ways ask stays —
    that is what tells those ways from the rest.

    `B′` rather than `B`. The callee is shared, and a guard taken out of it for one caller's sake is a guard the other
    callers no longer ask. Minted per callee and set of guards taken, it is called only from the sites that took them,
    and the sites that could not go on calling `B` as it stands.

    Run until nothing moves: a caller that has taken a guard is a callee its own callers can take it from, so the
    questions climb until they reach a way that performs something no guard may cross, or a choice whose ways ask
    different things.
    """
    for _round in ir.rounds("hoist-guards-to-callers"):
        settled = cleaned(_hoisted_once(grammar, namer))[0]
        namer.sees(settled)
        if settled == grammar:
            return grammar
        grammar = settled


def _hoisted_once(grammar, namer):
    """One pass of `hoist-guards-to-callers`: every guard a caller may take from what it enters, taken."""
    minted, named = {}, {}

    def without(name, taken):
        """The name of `name` with `taken` no longer asked by any of its ways, minted where first wanted."""
        key = (name, taken)
        if key not in named:
            held = namer.fresh(name)
            named[key] = held
            minted[held] = dataclasses.replace(
                grammar[name],
                name=held,
                body=ir.Choice(
                    alternatives=tuple(
                        dataclasses.replace(way, gate=ir.Gate(guards=tuple(set(way.gate.guards) - set(taken))))
                        for way in grammar[name].body.alternatives
                    )
                ),
            )
        return named[key]

    def told(way):
        called = _called_first(way, grammar)
        if called is None:
            return way
        # Every action is asked about, and the answers taken together afterwards: stopping at the first refusal would
        # leave the pairs behind it unconsulted, and what the table is missing is what its faults are for saying.
        taken = frozenset(
            guard
            for guard in _asked_by_every_way(called, grammar)
            if all([GUARD_CROSSES_ACTION.may_cross(guard, action) for action in way.actions])
        )
        if not taken:
            return way
        held = ir.Ref(name=without(called, taken), args=getattr(way.first or way.second, "args", ()))
        moved = dataclasses.replace(way, gate=ir.Gate(guards=(*way.gate.guards, *taken)))
        return (
            dataclasses.replace(moved, first=held) if way.first is not None else dataclasses.replace(moved, second=held)
        )

    written = {
        name: (
            production
            if not isinstance(production.body, ir.Choice)
            else dataclasses.replace(
                production, body=ir.Choice(alternatives=tuple(told(way) for way in production.body.alternatives))
            )
        )
        for name, production in grammar.items()
    }
    return {**written, **minted}


def _unheld(node):
    """
    `node` with the scopes written around it stripped — what it does, whatever is spelled about it.

    A `(token)` around a character is a character taken and a code given to it, and a reading after what a way does
    first has to see the character. A `(commit)` is not stripped: what it says about failing is the thing being asked.
    """
    while (inner := _HELD_MATCH(node)) is not node:
        node = inner
    return node


_HELD_MATCH = ir.Reading(
    "the match a node holds inside the scope written around it, or the node itself where it holds none",
    {
        # A window with nothing in it is a bound rather than a scope around a match, so it is what it is.
        (ir.Max, ir.Recover, ir.Token, ir.Wrap): lambda node: node.item if node.item is not None else node,
        (
            *_ALWAYS_READS,
            *_TAKES_NOTHING,
            *_SCANS,
            *_RUNS,
            ir.Alt,
            ir.Alternative,
            ir.Bind,
            ir.Choice,
            ir.Commit,
            ir.Opt,
            ir.Ref,
            ir.Rep,
            ir.Seq,
        ): lambda node: node,
    },
)


def _spans_meeting(one, other):
    """The codepoints both span lists admit — what a way entered on both is entered on."""
    return [
        (max(left[0], right[0]), min(left[1], right[1]))
        for left in one
        for right in other
        if not (left[1] < right[0] or right[1] < left[0])
    ]


def flatten_called_alternations(grammar, namer):
    """
    A way of a choice that is a call to a choice becomes that choice's ways, standing where the call did.

    `a | P | c` where `P` is `d | e` makes four decisions and shows three, the fourth hidden behind a call. Written out
    it is `a | d | e | c` — the same four ways, tried in the same order, each now standing where the choice that offers
    it can be asked about it.

    What `_flattened_calls` does of an `Alt`, whose parts are the ways a choice offers.
    """
    return _flattened_calls(grammar, ir.Alt)


def flatten_called_sequences(grammar, namer):
    """
    An item of a way that is a call to a run of items becomes those items, standing where the call did.

    What `_flattened_calls` does of a `Seq`, whose parts are the items a way performs — leaving a call that stands last,
    since what a way does past its call is a production of its own by design.
    """
    return _flattened_calls(grammar, ir.Seq, does_flatten_last=False)


def _reached_within(grammar, kind):
    """
    `{name: {name}}` — the productions each one names directly inside a `kind` node, and everything those reach in turn.

    What tells a flattening that would end from one that would not: a body written out into one that can reach back
    writes itself out again every round.
    """
    reaches = {name: set() for name in grammar}
    for _round in ir.rounds("what each production reaches inside a kind"):
        settled = {}
        for name, production in grammar.items():
            found = set()
            for node in _held(production.body):
                if isinstance(node, kind):
                    for held in node.items:
                        if isinstance(held, ir.Ref):
                            found |= {held.name} | reaches[held.name]
            settled[name] = found
        if settled == reaches:
            return reaches
        reaches = settled


def _flattened_calls(grammar, kind, does_flatten_last=True):
    """
    The grammar with every call standing alone inside a `kind` node written out as what it names, run until nothing
    moves — a body written out may itself hold such a call — and never into a body that can reach back.

    Only where the call stands as a whole part: a call inside a longer way would leave a choice among the items, which
    is a distribution rather than a flattening and costs a copy of everything behind it. And only where the callee takes
    no parameter, since writing one out would mean substituting its arguments rather than moving its parts.
    """
    for _round in ir.rounds("the flattening of calls standing alone"):
        reaches = _reached_within(grammar, kind)

        def flattened(node, owner, reaches=reaches, grammar=grammar):
            node = ir.rebuilt(node, lambda held: flattened(held, owner))
            if not isinstance(node, kind):
                return node
            parts = []
            for at, held in enumerate(node.items):
                called = grammar[held.name] if isinstance(held, ir.Ref) and not held.args else None
                # Writing out a callee that can reach itself unrolls its cycle one turn and spells the way back in, so
                # the next pass has the same call to write out again. The cycle need not run through the body being
                # rewritten: the stream and the recovery reach each other, and a production minted between them is on
                # neither's path while standing squarely in the middle of it.
                does_unroll_a_cycle = called is not None and (
                    held.name == owner or owner in reaches[held.name] or held.name in reaches[held.name]
                )
                is_kept = not does_flatten_last and at == len(node.items) - 1
                if (
                    called is None
                    or called.params
                    or not isinstance(called.body, kind)
                    or does_unroll_a_cycle
                    or is_kept
                ):
                    parts.append(held)
                else:
                    parts += list(called.body.items)
            return dataclasses.replace(node, items=tuple(parts))

        settled = {
            name: dataclasses.replace(production, body=flattened(production.body, name))
            for name, production in grammar.items()
        }
        if settled == grammar:
            return settled
        grammar = settled


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
    return _IS_ONLY_ACTIONS(node, grammar, seen)


# Asked where a commit is lifted, which is of a way's leading parts and what they call — so the kinds it meets are the
# few a way can begin with there, and a wider group would be claiming more than the corpus bears out.
_IS_ONLY_ACTIONS = ir.Reading(
    "whether a match is built of actions alone",
    {
        (ir.Empty, ir.SetVar): True,
        ir.Seq: lambda node, grammar, seen: all(_is_actions_alone(item, grammar, seen) for item in node.items),
        # A choice is one where some way is, since that way is the one taken.
        ir.Alt: lambda node, grammar, seen: any(_is_actions_alone(item, grammar, seen) for item in node.items),
        ir.Token: lambda node, grammar, seen: _is_actions_alone(node.item, grammar, seen),
        # A recursion reached again is not one, having no way of its own to answer with.
        ir.Ref: lambda node, grammar, seen: node.name not in seen
        and _is_actions_alone(grammar[node.name].body, grammar, seen | {node.name}),
        (ir.Bind, ir.Char, ir.CharSet, ir.NegLook, ir.Rep): False,
    },
)


def _is_nullable(node, grammar, ways):
    """
    Whether `node` has a way that takes no character — what `ways` says of a production, said of any node.

    A reading rather than a rewrite: it answers where `_split` refuses to, a commit holding both an empty match and a
    reading one being a shape no split can say as two ways but a perfectly ordinary thing to ask about. An empty match
    answered for by accident is the whole debt this phase removes, so it is a `Reading` — a kind it was not told about
    raises, and the corpus proves every answer it holds is reached.
    """
    return _IS_NULLABLE(node, grammar, ways)


def _counted_span_is_nullable(node, grammar, ways):
    """Whether a counted scan can take nothing — a count the parse works out may be none at all."""
    return not isinstance(node.count, ir.Lit) or node.count.value <= 0 or _is_nullable(node.set, grammar, ways)


def _is_way_nullable(node, grammar, ways):
    """
    Whether a way takes no character — every part of it taking none, the parts read in order so that a scan its own
    class is known to stand in front of is one that reads.
    """
    ahead = _ahead_of_gate(node, grammar)
    for item in _parts_of_way(node):
        if _does_scan_read(item, ahead, grammar):
            return False
        if not _is_nullable(item, grammar, ways):
            return False
        ahead = _narrowed_ahead(ahead, item, grammar)
    return True


_IS_NULLABLE = ir.Reading(
    "whether a match can take no character",
    {
        _ALWAYS_READS: False,
        (*_TAKES_NOTHING, ir.ConsumeSpan): True,
        ir.Ref: lambda node, grammar, ways: ways[node.name][1],
        (ir.Alternative, ir.Seq): lambda node, grammar, ways: _is_way_nullable(node, grammar, ways),
        ir.Alt: lambda node, grammar, ways: any(_is_nullable(way, grammar, ways) for way in node.items),
        # A run of none or more takes nothing by taking no turn; one of at least a turn takes nothing only where the
        # turn does.
        (ir.Opt, ir.Star): True,
        ir.LongestRun: lambda node, grammar, ways: node.least == 0 or _is_nullable(node.item, grammar, ways),
        ir.Plus: lambda node, grammar, ways: _is_nullable(node.item, grammar, ways),
        ir.ConsumeCountedSpan: _counted_span_is_nullable,
        ir.Bind: lambda node, grammar, ways: _is_nullable(node.cond, grammar, ways),
        _HOLDERS: lambda node, grammar, ways: node.item is None or _is_nullable(node.item, grammar, ways),
        # What the groups above name and nothing ever asks this. A choice is asked as the `Alt` it is before the
        # re-encode, the canonical form being read a way at a time; the provisional actions and the wider consumes stand
        # where their own phases put them, past this question.
        (
            ir.CommitProvisional,
            ir.ConsumeLiteral,
            ir.Choice,
            ir.InjectBefore,
            ir.MarkProvisional,
            ir.Max,
            ir.OpenProvisional,
            ir.RetypeProvisional,
        ): ir.NEVER,
    },
)


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
    return _SPLIT(node, grammar, ways)


def _split_scan(node, grammar, ways):
    """
    A scan's `(reads, empty)`. It is possessive, so it takes none exactly where the set is not there — which is the
    question the empty way asks — and the reading way is the character and the run behind it, as a `Plus` over the set
    is written.
    """
    return ir.Seq(items=(node.set, node)), ir.NegLook(item=as_char_set(node.set, grammar))


def _split_call(node, grammar, ways):
    """A call's `(reads, empty)`: the two names where the callee is told apart under them, else what it can do."""
    reads, empty, is_both = ways[node.name]
    if is_both:
        return ir.Ref(f"{node.name}_reads", node.args), ir.Ref(f"{node.name}_empty", node.args)
    return (node if reads else None), (node if empty else None)


def _split_alt(node, grammar, ways):
    """An alternation's `(reads, empty)`: its reading ways together, and its empty ones together."""
    parts = [_split(item, grammar, ways) for item in node.items]
    reads = tuple(way for way, _none in parts if way is not None)
    empty = tuple(none for _way, none in parts if none is not None)
    return (ir.Alt(items=reads) if reads else None), (ir.Alt(items=empty) if empty else None)


def _split_run(node, grammar, ways):
    """
    A run's `(reads, empty)`, by whether it must take a turn and whether a turn can take nothing.

    The reading half is a run that must take one, which is what `Plus` says — the two spellings are what a run is until
    `lower-runs` says it as ways, and this reads a grammar that still holds them.
    """
    reads, empty = _split(node.item, grammar, ways)
    taking = ir.Plus(item=reads) if reads is not None else None
    if isinstance(node, ir.Star):
        return taking, ir.Empty()  # a run of none or more takes nothing where the first turn cannot match
    if empty is None:
        return node, None  # the item always reads, so a run that must take a turn does
    # The run ends on a turn that takes nothing, which is kept once — and `_unsplittable_runs` holds that turn to
    # leaving nothing, so the reading way is the reading turns and the empty way is the one that took none.
    return taking, empty


def _split_bind(node, grammar, ways):
    """A binding's `(reads, empty)`: the binding around each half of what it binds."""
    reads, empty = _split(node.cond, grammar, ways)
    return (
        (dataclasses.replace(node, cond=reads) if reads is not None else None),
        (dataclasses.replace(node, cond=empty) if empty is not None else None),
    )


def _split_holder(node, grammar, ways):
    """A scope's `(reads, empty)`: the scope around each half of what it holds."""
    if node.item is None:
        return None, node  # a `(max)` window with nothing in it: a bound, and no match of its own
    reads, empty = _split(node.item, grammar, ways)
    if isinstance(node, (ir.Commit, ir.Recover)) and reads is not None and empty is not None:
        # Both are the error where the item cannot match, so a reading form of one would raise where the parse should
        # have gone on to the empty form. The body's own commit is lifted off before this; a deeper one has nowhere to
        # be lifted to.
        raise ValueError(f"a {type(node).__name__.lower()} hides both an empty match and a reading one")
    return (
        (dataclasses.replace(node, item=reads) if reads is not None else None),
        (dataclasses.replace(node, item=empty) if empty is not None else None),
    )


def _ways_of(node):
    """The ways `node` offers as a tuple, a choice contributing its own and a way standing for itself."""
    return node.alternatives if isinstance(node, ir.Choice) else (node,)


def _way_of_parts(way, parts):
    """
    `way` with its parts replaced by `parts`, which stand in the order `_items_of_way` gives them — the actions first,
    then the call, then where it carries on.
    """
    actions, rest = tuple(parts[: len(way.actions)]), list(parts[len(way.actions) :])
    first = rest.pop(0) if way.first is not None else None
    second = rest.pop(0) if way.second is not None else None
    return dataclasses.replace(way, actions=actions, first=first, second=second)


def _split_way(node, grammar, ways):
    """
    A way's `(reads, empty)` in the canonical form. It reads where any one of its parts does, so the reading ways are
    one per part that can — that part reading and everything before it taking nothing — and the empty way is every part
    taking none. A way splits into several ways that way, so what comes back reading is a choice of them.

    The parts are read in order, since what the lookaheads among them admit is what says a scan behind one takes a
    character.
    """
    parts = list(_items_of_way(node))
    reads, taken, ahead = [], [], _ahead_of_gate(node, grammar)
    for position, part in enumerate(parts):
        way, none = (part, None) if _does_scan_read(part, ahead, grammar) else _split(part, grammar, ways)
        if way is not None:
            reads.append(_way_of_parts(node, taken + [way] + parts[position + 1 :]))
        if none is None:
            return (ir.Choice(tuple(reads)) if reads else None), None  # this part always reads: no empty way past it
        taken.append(none)
        ahead = _narrowed_ahead(ahead, part, grammar)
    return (ir.Choice(tuple(reads)) if reads else None), _way_of_parts(node, taken)


def _split_canonical(node, grammar, ways):
    """
    A choice's or a way's `(reads, empty)` in the canonical form: the ways of it that take a character and the ways that
    take none, either `None` where it has none of them.

    A choice's ways stand beside one another, so each splits on its own and the two halves are the ways that came back
    from each — flattened, a way splitting into a choice of several. Nothing a way tests holds in the next, which is why
    they are not read in order the way a way's own parts are.
    """
    if not isinstance(node, ir.Choice):
        return _split_way(node, grammar, ways)
    halves = [_split(way, grammar, ways) for way in node.alternatives]
    reads = tuple(one for half, _none in halves if half is not None for one in _ways_of(half))
    empty = tuple(one for _half, none in halves if none is not None for one in _ways_of(none))
    return (ir.Choice(reads) if reads else None), (ir.Choice(empty) if empty else None)


def _split_switch(node, grammar, ways):
    """A switch's `(reads, empty)`: it reads where any branch it could take does, and takes nothing where any does."""
    branches = [branch.item for branch in node.branches] + ([node.default] if getattr(node, "default", None) else [])
    answers = [_split(item, grammar, ways) for item in branches]
    return (
        node if any(part is not None for part, _empty in answers) else None,
        node if any(part is not None for _reads, part in answers) else None,
    )


def _split_seq(node, grammar, ways):
    """
    A sequence's `(reads, empty)`. It reads where any one part does, so the reading ways are one per part that can —
    that part reading and everything before it taking nothing — and the empty way is every part taking none.

    A scan reached with its own class known to stand in front of it is not one of the parts that can take none: the
    lookaheads before it narrow what the character can be, and where nothing they admit is off the scan's set the scan
    takes at least one. That is what a `Plus` over a class is written as, and reading the pair as if either half could
    be empty would report an empty way the parse has no input for.
    """
    reads, taken, ahead = [], [], _EVERY_CHARACTER
    for position, item in enumerate(node.items):
        way, none = (item, None) if _does_scan_read(item, ahead, grammar) else _split(item, grammar, ways)
        if way is not None:
            reads.append(ir.Seq(items=tuple(taken) + (way,) + node.items[position + 1 :]))
        if none is None:
            return (ir.Alt(items=tuple(reads)) if reads else None), None  # this part always reads: no empty way past it
        taken.append(none)
        ahead = _narrowed_ahead(ahead, item, grammar)
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


_SPLIT = ir.Reading(
    "the pair of a match's ways that take a character and its ways that take none, each `None` where it has none",
    {
        _ALWAYS_READS: lambda node, grammar, ways: (node, None),
        _TAKES_NOTHING: lambda node, grammar, ways: (None, node),
        ir.ConsumeSpan: _split_scan,
        ir.Ref: _split_call,
        ir.Seq: _split_seq,
        ir.Alt: _split_alt,
        _RUNS: _split_run,
        ir.Rep: lambda node, grammar, ways: _split_counted(node, node.item, grammar, ways),
        ir.ConsumeCountedSpan: lambda node, grammar, ways: _split_counted(node, node.set, grammar, ways),
        ir.Bind: _split_bind,
        _HOLDERS: _split_holder,
        (ir.Alternative, ir.Choice): _split_canonical,
        # A switch the specialization settles, met only before it runs: which branch is taken is decided by the caller
        # and not by the input, so the honest answer is that any of them may be, and the node stands for whichever it
        # can. Nothing rewrites by these halves — the steps that did run after the specialization.
        ir.Case: _split_switch,
        _VALUE_KINDS: lambda node, grammar, ways: (None, node),  # a value matches nothing, so it takes no character
        ir.Opt: lambda node, grammar, ways: (
            node.item if _split(node.item, grammar, ways)[0] is not None else None,
            ir.Empty(),
        ),
        # What the two wide groups name and this never meets: the provisional actions and the literal questions belong
        # to phases past the empties, and a split of them would be a rewrite of a shape that is not there yet.
        (
            ir.CommitProvisional,
            ir.ConsumeLiteral,
            ir.InjectBefore,
            ir.MarkProvisional,
            ir.OpenProvisional,
            ir.RetypeProvisional,
        ): ir.NEVER,
    },
)


def _production_split(production, grammar, ways):
    """`(message, reads, empty)` for a production's body, its own commit lifted off the split and named back."""
    message, body = _lifted_commit(production.body, grammar)
    reads, empty = _split(body, grammar, ways)
    return message, reads, empty


def _split_ways(grammar):
    """
    `{name: (reads, empty, is_both)}` — whether each production has a way that takes a character, whether it has one
    that takes none, and whether it does both while being one a caller chooses to enter, which is the defect.

    A least fixed point, since a reference can reach back to its own production: nothing is taken to match until some
    way of it says so, so a recursion on its own contributes neither. A production the parse enters by name is left
    whole — nobody chooses to enter one, so an empty match there decides nothing, and the root and the recovery reach
    each other, which is a choice on nothing at all once each is two things.
    """
    entered = entered_by_name(grammar)
    ways = {name: (False, False, False) for name in grammar}
    for _round in ir.rounds("which of the two each production can do"):
        settled = {}
        for name, production in grammar.items():
            _message, reads, empty = _production_split(production, grammar, ways)
            is_both = reads is not None and empty is not None and name not in entered
            settled[name] = (reads is not None, empty is not None, is_both)
        if settled == ways:
            return ways
        ways = settled


# What the runs become, and what they must not become. A repetition is the last thing in the grammar that is neither a
# production nor a choice, and every reading past this one — the calls a way holds, the characters it can begin with,
# the gate over them — is written against those two; a run said as a production of its own is what makes them uniform.
def _takes_none_only_at_the_end(node, grammar, ways, seen=frozenset()):
    """
    Whether `node` taking no character means the input has ended.

    What `l-unparsed` says outright: its turns take every character there is and a run is possessive, so the one place
    none of them can be taken is past the last one, and the rule spells that as a run of at least one turn or the end. A
    walk for what a production reaches with nothing taken stops where it meets one of these — what stands behind it is
    reached where the input has ended, and no parse carries on from there.

    Decided and not recognised: it is asked through a reading, so a kind it was never told about raises rather than
    falling through to "this does not stop the walk". It fell through for years, and every step that rewrote a shape
    silently changed the answer — the `l-recover` circuit read none, then twenty, then none again, each time saying
    something about the reading rather than about the grammar.
    """
    return _TAKES_NONE_ONLY_AT_THE_END(node, grammar, ways, seen)


def _empty_ways_end_the_input(node, grammar, ways, seen):
    """A choice's answer: it has an empty way, and every one of them means the input has ended."""
    empty = [way for way in _ways_or_items(node) if _is_nullable(way, grammar, ways)]
    return bool(empty) and all(_takes_none_only_at_the_end(way, grammar, ways, seen) for way in empty)


def _a_part_ends_the_input(node, grammar, ways, seen):
    """A way's answer: some part of it says the input has ended, its gate read with what it performs."""
    parts = _parts_of_way(node) if isinstance(node, ir.Alternative) else node.items
    return any(_takes_none_only_at_the_end(part, grammar, ways, seen) for part in parts)


_TAKES_NONE_ONLY_AT_THE_END = ir.Reading(
    "whether a match that takes no character means the input has ended",
    {
        # The one question that says so, wherever it is asked — in a gate since `hoist-guards-to-gates`, among the
        # actions before it.
        ir.EndOfStream: True,
        ir.Ref: lambda node, grammar, ways, seen: node.name not in seen
        and _takes_none_only_at_the_end(grammar[node.name].body, grammar, ways, seen | {node.name}),
        (ir.Alt, ir.Choice): _empty_ways_end_the_input,
        (ir.Alternative, ir.Seq): _a_part_ends_the_input,
        # Everything else says nothing about where the input ends: a match takes characters, an action leaves something
        # behind, every other guard asks about something else, and a repetition or a scan answers for its own turns.
        (
            *_ACTIONS,
            *(guard for guard in _GUARDS if guard is not ir.EndOfStream),
            *_ALWAYS_READS,
            *_SCANS,
            *_RUNS,
            *_HOLDERS,
            *_VALUE_KINDS,
            ir.Bind,
            ir.Case,
            ir.Empty,
            ir.Opt,
            ir.Rep,
        ): False,
    },
)


def _every_end_of_stream_stands_in_a_gate(grammar):
    """
    Check that every end of stream stands in a gate — an `EndOfStream` is asked where a way is entered and nowhere else.

    Whether a character is there is a question, so it belongs where a way's questions are asked. The grammar says where
    the input may end and says it outright, and the machine reaches the end as a way it was offered rather than as a
    match it fell into. One asked among the actions would be asked where the way has already been entered, and one in a
    body that offers no ways is asked where nothing chooses on the answer.

    What follows the question is not this invariant's business: a way that reaches the end still has the wrapping up to
    do, and may hand that on to a continuation the way any other does. A way that takes no character and is not gated on
    the end is no end of stream at all — it is an empty match, which matches past the last character as it matches
    anywhere, and what tells it from its neighbours is what follows rather than the end.
    """

    def ends(node):
        return sum(isinstance(held, ir.EndOfStream) for held in _held(node))

    faults = []
    for name, production in grammar.items():
        if not isinstance(production.body, ir.Choice):
            faults += [f"{name}: an end of stream stands in a body that offers no ways"] * ends(production.body)
            continue
        for way in production.body.alternatives:
            faults += [f"{name}: an end of stream stands among what a way performs"] * sum(
                ends(part) for part in _items_of_way(way)
            )
    return faults


EVERY_END_OF_STREAM_STANDS_IN_A_GATE = Invariant(
    "every-end-of-stream-stands-in-a-gate", _every_end_of_stream_stands_in_a_gate
)


def _entered_unconsumed(node, grammar, ways, entering=()):
    """
    The productions `node` can enter with nothing taken — its left corner, as names.

    A guard is followed like anything else: it is tested where the parse stands, so what it reaches is reached at that
    same position. A recovery is not: it is entered where an abandoned parse stopped rather than where its rule began,
    so a `(recover)` contributes what its item does and nothing more.

    A way's gate is walked with its parts, so what the gate asks is asked here too, and `entering` carries what every
    way that enters this production asked where it entered it — a question that moved up to a caller still holds here,
    and a scan it vouched for still reads. One gate admitting only the end of the input stops the walk where its
    `EndOfStream` stands: a parse with input left never enters that way, and this walk is for what a parse can arrive at
    and not go on from.

    An `EndMustConsume` stops it for the reverse reason: it asks that a character was taken since its own open, so a
    parse reaching it took one and nothing behind it stands where the parse still does. That is what lets a run said as
    a recursion be read as reaching itself only ever having moved — the guard is the proof, where the turn it guards may
    be anything at all.
    """
    if isinstance(node, ir.Ref):
        return {node.name}
    if isinstance(node, (ir.Alt, ir.Choice)):
        return {name for item in _ways_or_items(node) for name in _entered_unconsumed(item, grammar, ways, entering)}
    if isinstance(node, (ir.Alternative, ir.Seq)):
        reached = set()
        parts = _parts_of_way(node) if isinstance(node, ir.Alternative) else node.items
        for at, item in enumerate(parts):
            reached |= _entered_unconsumed(item, grammar, ways)
            # A turn that must take a character has taken one wherever this is reached, that being the whole of what the
            # guard asks — so nothing behind it is entered where the parse still stands, whatever the turn itself was.
            if isinstance(item, ir.EndMustConsume):
                break
            # This part always reads, or takes nothing only where the input has ended: either way nothing behind it is
            # entered where the parse still stands and can go on. A scan its own class stands in front of always reads,
            # which the guards in force where it stands are what say.
            if _does_scan_read(item, _ahead_of_any(parts, at, entering, grammar), grammar):
                break
            if not _is_nullable(item, grammar, ways) or _takes_none_only_at_the_end(item, grammar, ways):
                break
        return reached
    if isinstance(node, ir.Bind):
        return _entered_unconsumed(node.cond, grammar, ways)
    if isinstance(node, _WALKED_UNCONSUMED):
        return set() if node.item is None else _entered_unconsumed(node.item, grammar, ways)
    if isinstance(node, (*_ALWAYS_READS, *_TAKES_NOTHING, *_SCANS)):
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


def _no_production_reaches_itself_unconsumed(grammar):
    """
    Check that no production reaches itself with nothing taken, a parse that arrives there being unable to go on.

    The machine being built is a pushdown that commits to the first gate that fires and never backtracks, so it has no
    way to notice it is where it already was: a production reaching itself at the same position runs for ever. Nothing
    absorbs it the way an LR construction would, which is why this is a fault and not a shape to handle.

    The stream and the recovery are mutually recursive by design under a resuming policy — `l-recover` is `l-unparsed`
    and then the stream again, which is what lets a resumed document fail again without a second mechanism for it. What
    keeps that pair from going round for ever is `l-unparsed`: it is a guard and a possessive run whose turns accept
    every character, so it takes nothing only where the input has ended, and there `<end-of-stream>` answers rather than
    the stream carrying on. `_takes_none_only_at_the_end` is that reason said as a clip, so the pair is read rather than
    exempted — every edge is followed, a start state being a production like any other once a call reaches it.
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


def _names_that_reach_themselves(edges):
    """
    The names of `{name: the names it reaches in one step}` that reach themselves along one or more steps — the ones
    standing in a circle of more than one name, plus the ones that are their own step.
    """
    return {name for circle in _circles(edges) for name in circle if len(circle) > 1 or name in edges.get(name, ())}


def _circles(edges):
    """
    The circles of `{name: the names it reaches in one step}` — its strongly connected components, each as a tuple,
    ordered so a circle stands after every circle it reaches.

    Tarjan's algorithm, one walk of the edges, which is what answers both who reaches themselves and what may be read
    before what. Growing each name's whole reach instead would answer the same question by building a relation whose
    size is the square of the grammar's.
    """
    circles = []
    index, low, reached = {}, {}, 0
    standing, is_standing = [], set()
    for root in edges:
        if root in index:
            continue
        index[root] = low[root] = reached
        reached += 1
        standing.append(root)
        is_standing.add(root)
        walk = [(root, iter(sorted(edges[root])))]
        while walk:
            name, steps = walk[-1]
            for far in steps:
                if far not in index:
                    index[far] = low[far] = reached
                    reached += 1
                    standing.append(far)
                    is_standing.add(far)
                    walk.append((far, iter(sorted(edges.get(far, ())))))
                    break
                if far in is_standing:
                    low[name] = min(low[name], index[far])
            else:
                walk.pop()
                if walk:
                    above = walk[-1][0]
                    low[above] = min(low[above], low[name])
                if low[name] == index[name]:
                    circle = []
                    while True:
                        far = standing.pop()
                        is_standing.discard(far)
                        circle.append(far)
                        if far == name:
                            break
                    circles.append(tuple(circle))
    return tuple(circles)


NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED = Invariant(
    "no-production-reaches-itself-unconsumed", _no_production_reaches_itself_unconsumed
)


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

    # Which productions hand an indentation back is a property of the grammar this step was given, and the step does not
    # change it as it goes: worked out once, rather than again for every node it looks at.
    establishing = _establishing(grammar)

    def does_establish(node):
        """Whether `node` is a call whose production hands an indentation back to this one."""
        return isinstance(node, ir.Ref) and node.name in establishing and _is_by_reference(grammar, node)

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
                rest, pair = bounded(items[position + 1 :]), namer.pair()
                return (
                    items[:position]
                    + (ir.PushIndent(level=item.value, pair=pair),)
                    + rest
                    + (ir.PopIndent(level=None, pair=pair),)
                )
        return items

    def held(node):
        node = ir.rebuilt(node, held)
        return ir.Seq(items=bounded(spliced(node.items))) if isinstance(node, ir.Seq) else node

    return {name: dataclasses.replace(production, body=held(production.body)) for name, production in grammar.items()}


def _no_production_writes_the_indentation(grammar):
    """
    Check that nothing writes the indentation, so the parse stands under it rather than a call handing it back.

    What reads such a write is a production away from where it happens, so nothing local says where the value's region
    is, and the parameter carrying it out is the only thing holding it together.
    """
    return [
        f"{name}: writes the indentation, which its caller must read back"
        for name, production in grammar.items()
        for node in _held(production.body)
        if isinstance(node, ir.SetVar) and node.param == "n"
    ]


NO_PRODUCTION_WRITES_THE_INDENTATION = Invariant(
    "no-production-writes-the-indentation", _no_production_writes_the_indentation
)


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
        if level is None:
            return node
        pair = namer.pair()
        return ir.Seq(items=(ir.PushIndent(level=level, pair=pair), node, ir.PopIndent(level=None, pair=pair)))

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
    for _round in ir.rounds("what each declared site establishes"):
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


def _every_read_is_bounded(param):
    """
    The invariant that every production reading `param` says where the value stops.

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
    # `every-called-alternative-is-unconditional` is claimed by no step. Settled at the door it would put every step
    # from the first under its law, and no phase is pursuing it — a question the pipeline is not asking yet costs a
    # lapse on every step that touches a gate, which is noise about the declarations rather than news about the grammar.
    # It comes back when a phase takes it on.
    #
    # `EVERY_CONDITIONAL_WAY_IS_GATED` is what the grammar arrives already holding, so that no step is read as having
    # established it: there are no ways for anything to decide between until `build-alternatives` says a way by its
    # parts, and the count is none the whole way here. Whichever step first raises it is the one leaving a way nothing
    # can be entered on, which is `build-alternatives` itself and is where the phase's work starts.
    Step("holds-at-the-door", establishes=EVERY_CONDITIONAL_WAY_IS_GATED),
    # Phase 0 establishes `NO_I_T_PARAMETERS`: nothing declares, passes or reads the chomping or the block scalar's
    # indentation mode. Each is data-dependent until this runs, so neither can be specialized: the setters become
    # switches first.
    Step("lift-setters", lift_setters, reduces=FINITE_PARAMETERS_ARE_ALWAYS_LITERAL),
    Step(
        "monomorphize",
        monomorphize,
        settles=(
            _absent("no-context-case", ir.Case, ir.Flip),
            FINITE_PARAMETERS_ARE_ALWAYS_LITERAL,
            NO_I_T_PARAMETERS,
            # None once the specialization has run and no earlier: a context that picks between shapes is not a grammar
            # the readings of a way can be asked about, and the copies are where a way a caller cannot reach goes.
            EVERY_OPTION_IS_REACHABLE,
        ),
    ),
    # Phase 1 establishes `EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET`: a question about a character is a `CharSet`. A
    # set the context picks denotes nothing until the specialization has bound the context, so this follows Phase 0. The
    # difference is taken into the ways it subtracts from first, since a subtraction says a set only where both of its
    # sides do.
    Step("distribute-differences", distribute_differences, settles=EVERY_DIFFERENCE_IS_BETWEEN_CHARACTER_SETS),
    Step(
        "lower-char-sets",
        lower_char_sets,
        settles=(EVERY_CHARACTER_QUESTION_IS_A_CHARACTER_SET, NO_DIFF_NODES, EVERY_PEEK_IS_A_CHARACTER_SET),
    ),
    # Phase 2 establishes `NO_F_PARAMETER`: the block scalar's leading-empty floor is the parse's one value rather than
    # one a call carries. The value is given an end first, a single slot answering for a parameter only where a read
    # past the region it was measured in is refused rather than answered from what the last construct left.
    Step("clear-f", _clear_reads("f"), settles=_every_read_is_bounded("f")),
    Step("read-global-f", _read_off("f", ir.Global(name="f")), settles=NO_F_PARAMETER),
    # Phase 3 establishes `NO_M_PARAMETER`: the detected indent is the parse's one value. Nothing reads it twice over a
    # region something else can write in — the block header measures it and the scalar that asked reads it, one
    # construct at a time — so a clear and a drop are the whole of it.
    Step("clear-m", _clear_reads("m"), settles=_every_read_is_bounded("m")),
    Step("read-global-m", _read_off("m", ir.Global(name="m")), settles=NO_M_PARAMETER),
    # Phase 4 establishes `NO_N_PARAMETER`: the indentation is on the parse's own stack rather than carried by a call.
    # The pushes go in first and the parameter stays beside them, so what says they stand where they should is every
    # read comparing the two over the corpus; dropping the parameter is what leaves the stack the one place it is.
    Step("hold-established-indents", hold_established_indents, settles=NO_PRODUCTION_WRITES_THE_INDENTATION),
    Step("push-indents", push_indents, settles=EVERY_INDENTATION_CHANGE_IS_PUSHED),
    Step("read-indents", _read_off("n", ir.Indent()), settles=NO_N_PARAMETER),
    # Phase 5 establishes `NO_OPT_NODES` and `NO_STAR_OR_PLUS_NODES`: no node hides a match that may take none. An empty
    # match is a way beside the one that reads, and a repetition is a scan of a class or a run over the state it
    # repeats. What the phase does not reach is `no-conditional-production-matches-empty` — that no production something
    # decides to enter matches empty — which no step in the pipeline settles.
    Step("lower-optionals", lower_optionals, settles=NO_OPT_NODES),
    # `no-production-reaches-itself-unconsumed` is none from here, and is not a question an earlier grammar answers: a
    # cycle is read off the ways a production offers, which the optionals are the last thing to be spelled outside of.
    Step("holds-once-optionals-are-ways", establishes=NO_PRODUCTION_REACHES_ITSELF_UNCONSUMED),
    Step(
        "span-consumes",
        span_consumes,
        settles=(EVERY_CHARACTER_RUN_IS_A_SPAN, EVERY_EXCLUSION_IS_BOUNDED),
        reduces=NO_STAR_OR_PLUS_NODES,
    ),
    Step("lower-runs", lower_runs, settles=NO_STAR_OR_PLUS_NODES),
    # `EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS`: what an exclusion forbids is matched to be thrown away, so it is given a
    # copy holding only what matches and asks. It runs while a `(token)` is still the one node saying what code
    # characters carry, which is the whole of what such a copy has to drop.
    Step("mint-forbidden-probes", mint_forbidden_probes, settles=EVERY_FORBIDDEN_ONLY_MATCHES_AND_ASKS),
    # Phase 6 establishes `NO_WRAP_NODES`, `NO_MAX_NODES`, `NO_COMMIT_NODES`, `NO_TOKEN_NODES` and
    # `NO_EXCLUDE_AT_NODES`: nothing holds what it covers, a scope being the pair of writes that bound it. Each step
    # takes one kind, and each of the five writes both halves carrying the pair they belong to — the guarantee a wrapper
    # gave by construction, now something the parse is held to.
    Step("lower-wraps", lower_wraps, settles=NO_WRAP_NODES),
    Step("lower-windows", lower_windows, settles=NO_MAX_NODES),
    Step("lower-commits", lower_commits, settles=NO_COMMIT_NODES),
    Step("lower-tokens", lower_tokens, settles=NO_TOKEN_NODES),
    Step("lower-exclusions", lower_exclusions, settles=NO_EXCLUDE_AT_NODES),
    # Phase 7 establishes `EVERY_SUB_ITEM_IS_ONE_STEP`: an item standing in a way is one thing the machine does where it
    # stands, so every shape holding a match inside it becomes a production of its own. The three lifts each settle the
    # kind they name and lower this count between them, and `lower-bind` takes it to none.
    Step(
        "lift-choices",
        _lifting(ir.Alt),
        settles=EVERY_CHOICE_IS_A_PRODUCTION,
        reduces=EVERY_SUB_ITEM_IS_ONE_STEP,
    ),
    Step(
        "lift-recoveries",
        _lifting(ir.Recover),
        settles=EVERY_RECOVERY_IS_A_PRODUCTION,
        reduces=EVERY_SUB_ITEM_IS_ONE_STEP,
    ),
    Step("lower-bind", lower_bind, settles=(EVERY_SUB_ITEM_IS_ONE_STEP, NO_BIND_NODES)),
    # Phase 8 establishes `NO_CHOICE_OF_CHOICES`: no way of a choice is a call to a choice, so every way stands where a
    # gate can be put on it rather than one call below. It runs before the way is split into a call and a continuation,
    # since a choice written out here is one every phase behind this sees whole. The run of items among the items of a
    # way is flattened beside it, and `no-sequence-of-sequences` is only lowered here — Phase 10 takes it to none.
    Step("flatten-called-alternations", flatten_called_alternations, settles=NO_CHOICE_OF_CHOICES),
    Step("flatten-called-sequences", flatten_called_sequences, reduces=NO_SEQUENCE_OF_SEQUENCES),
    # Phase 9 establishes `A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION`: a way does its actions, hands control to one
    # production, and says where to carry on when it comes back. What stood past the call is what carries on.
    Step(
        "mint-continuations",
        mint_continuations,
        settles=A_WAY_IS_ACTIONS_A_CALL_AND_A_CONTINUATION,
    ),
    # Phase 10 establishes `EVERY_BODY_IS_A_CHOICE_A_RUN_OR_A_SET`: every body is said in the machine's own words — a
    # set of characters or the ordered list of alternatives one of which the parse takes, a repetition having been said
    # as the ways it is where it was lowered. `no-sequence-of-sequences` reaches none with the bodies built here.
    Step(
        "build-alternatives",
        build_alternatives,
        settles=(EVERY_BODY_IS_A_CHOICE_A_RUN_OR_A_SET, NO_SEQUENCE_OF_SEQUENCES),
        lapses={
            "every-conditional-way-is-gated": "this is where there are ways to gate at all — a body said as the "
            "ordered list of alternatives one of which the parse takes is the first thing anything has to be told "
            "apart by, and none of them carries a question yet. The debt is made here and paid down by every step "
            "that moves a question into a gate"
        },
        # A way with a gate is what this builds, so this is where the gate's own shape starts being asked about at all.
        # Nothing has yet brought two questions about one character together, and a hoist is the only thing that will.
        establishes=EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE,
    ),
    # `EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL`: a way with no gate is one still to be given one, and a call made past
    # the way's own actions is a call whose callee's guards nothing can bring up to where the way is entered. Given the
    # call a state of its own, the two stand at one position — which is what every step that hoists a guard out of a
    # callee, or writes a callee's ways into the way calling it, rests on.
    Step("mint-call-states", mint_call_states, settles=EVERY_UNGATED_WAY_HAS_ACTIONS_OR_A_CALL),
    # Phase 11 establishes `NO_GUARD_STANDS_PAST_AN_ACTION` and `EVERY_GUARD_IS_IN_A_GATE`: every question a way asks
    # stands in its gate, and nothing it performs stands in front of one. The grammar says outright which of the two a
    # way's parts are. Two steps: a way is cut where it would ask a guard past an action, and the guards it holds move
    # into its gate.
    #
    # Taking those gates out to the ways that call them is not here. It would carry a guard over what the caller
    # performs before the call, and on the path the guard refuses those actions never happened — a `PushCode` that did
    # not cut the run, a `PopMessage` that did not leave the region. Every one of the call sites performs something, so
    # nothing can move until the gate and the call are made adjacent. `every-called-alternative-is-unconditional` is
    # where that lands, and it belongs with determinization rather than here.
    Step(
        "mint-guard-states",
        mint_guard_states,
        settles=NO_GUARD_STANDS_PAST_AN_ACTION,
    ),
    Step(
        "hoist-guards-to-gates",
        hoist_guards_to_gates,
        settles=(EVERY_GUARD_IS_IN_A_GATE, EVERY_END_OF_STREAM_STANDS_IN_A_GATE),
        reduces=EVERY_CONDITIONAL_WAY_IS_GATED,
        lapses={
            "every-gate-looks-ahead-at-most-once": "a guard moved into a gate stands beside whatever that gate already "
            "asked, and two questions about the character in front are one question until they are said as one set"
        },
    ),
    Step("merge-gate-peeks", merge_gate_peeks, settles=EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    # Phase 12 establishes `NO_CHAR_SET_IS_AN_ITEM` and `EVERY_CONSUME_IS_PROTECTED_BY_A_GATE`: the asking and the
    # taking are two things, the question in the gate where a caller can see it and the taking on the gate's word. This
    # is where the gates come from — a hoist moves a question that exists, and until this has run there are barely any
    # to move. Four steps: a run of single characters is the literal it is, a counted run says the two ways its count
    # makes it, a way that would take a second character on the strength of the first is cut, and what is left is one
    # set each.
    Step(
        "fold-literals-into-gates",
        fold_literals_into_gates,
        reduces=(NO_CHAR_SET_IS_AN_ITEM, EVERY_CONDITIONAL_WAY_IS_GATED),
    ),
    Step("mint-consume-states", mint_consume_states, settles=EVERY_WAY_TAKES_AT_MOST_ONCE),
    Step(
        "split-consumes-into-gates",
        split_consumes_into_gates,
        settles=NO_CHAR_SET_IS_AN_ITEM,
        reduces=EVERY_CONDITIONAL_WAY_IS_GATED,
    ),
    # Last of the four, because it says one way as two: a set still standing among the actions would be copied into
    # both, and `no-char-set-is-an-item` would rise where nothing had gone wrong.
    Step(
        "split-counted-spans-on-the-count",
        split_counted_spans_on_the_count,
        settles=EVERY_CONSUME_IS_PROTECTED_BY_A_GATE,
        reduces=EVERY_CONDITIONAL_WAY_IS_GATED,
    ),
    # Phase 13 establishes `EVERY_CONDITIONAL_WAY_IS_GATED`: every way something decides to enter is entered on what the
    # input says. The questions exist now, and this is where they move — out of a callee that offers one way and into
    # the choice that has to tell its ways apart. What the callee gave up it is still entered under, which is what
    # `_asked_where_entered` says and what keeps the take it protected still protected.
    Step("expand-called-ways", expand_called_ways, reduces=EVERY_CONDITIONAL_WAY_IS_GATED),
    Step(
        "hoist-guards-to-callers",
        hoist_guards_to_callers,
        reduces=EVERY_CONDITIONAL_WAY_IS_GATED,
        lapses={
            "every-gate-looks-ahead-at-most-once": "a guard taken up to a caller stands beside whatever that caller's "
            "gate already asked, the same two questions about one character as when it moved into a gate below"
        },
    ),
    Step("merge-gate-peeks-2", merge_gate_peeks, settles=EVERY_GATE_LOOKS_AHEAD_AT_MOST_ONCE),
    Step("expand-called-ways-2", expand_called_ways, reduces=EVERY_CONDITIONAL_WAY_IS_GATED),
]
