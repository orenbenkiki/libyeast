# SPDX-License-Identifier: MIT
"""
The determinizer: derive a conflict's provisional-run decisions from the grammar by subset construction.

A conflict is a production whose alternatives overlap — one no gate can decide, which
`normalize.deterministic_productions` leaves out. Its live alternatives are walked in lockstep over the input, and the
divergence between them read: a character all paths consume over the same span but under different codes is *held*, to
be retyped at resolution to the surviving path's own code; a marker some paths emit and others do not is *injected*; the
character on which the paths' gates first differ is the *discriminator*, where the run commits. The walk parks each path
at its next consume, branching at a choice and following a single way, with revisit detection so a nullable loop
terminates rather than spins.

`determinize` is the whole cycle for the fold — detect the conflict, generate the provisional productions from the
decision, replace the site — a grammar-to-grammar transform the pipeline runs and `check_normalize` holds to the corpus,
the same harness every other step answers to. The emission is the fold's shape for now; deriving it for an arbitrary
conflict is the pass's sequel. The one obligation the analysis rests on is the mechanism's: the readings a run decides
between agree token for token, so every difference between them is a held code or a zero-width marker, never a dropped
or grown token.
"""

import collections

import ir
import normalize

# The actions that consume input — each parks a live path at a lookahead frontier, since the next character decides
# whether the path continues. A `ConsumeCountedSpan` and a `ConsumeSpan` take a run of one set; the rest take one
# character, the gate having found it.
_CONSUMES = (ir.ConsumeChar, ir.ConsumeSpan, ir.ConsumeCountedSpan, ir.ConsumeLiteral, ir.ConsumePeeked)

# A cap on the return stack: a walk that grows past it is genuinely non-convergent — an unbounded run, which the
# emission resolves as a runtime loop rather than a straight-line region. It sits far above any bounded conflict's
# depth.
_DEPTH_CAP = 120

# A configuration is the parse's state at a frontier: `frames` the return stack (each a `(name, alternative, cursor)`
# triple), `code` the run code a `PushCode` last set, and `origin` the conflict alternative the path descends from. The
# cursor is `0` (apply this alternative's actions), `1`/`2` (call its first/second production), `3` (return), or
# `("act", index)` (resume actions after a consumed one).
Config = collections.namedtuple("Config", ("frames", "code", "origin"))

# A parked configuration and what it will consume next: `spans` the character set of a consume, or `None` for an
# accepting path that consumes nothing more.
Parked = collections.namedtuple("Parked", ("config", "spans"))

# The line-break characters: a held token whose span lies within them retypes by the `breaks` slot, any other by `rest`.
_BREAKS = (0x0A, 0x0D)


def _is_terminal(grammar, name):
    """Whether `name` is a terminal character set rather than a choice of alternatives."""
    return name in grammar and not isinstance(grammar[name].body, ir.Choice)


def _consume_spans(grammar, action, gate):
    """The character set an action consumes: a span action's own set, a literal's first character, else the gate's."""
    if isinstance(action, (ir.ConsumeSpan, ir.ConsumeCountedSpan)):
        return normalize._peek_spans(action.set, grammar)
    if isinstance(action, ir.ConsumeLiteral):
        return [(action.text[0], action.text[0])]
    return None if gate.peek is None else normalize._peek_spans(gate.peek, grammar)


def _closure(grammar, configs):
    """
    Every configuration advanced by zero-width steps to its next consume frontier.

    A path applies its alternative's actions — a `PushCode` sets the run code, an `Emit` is a marker, a consume parks it
    — then calls its first and second productions, entering a choice by branching over every alternative and a terminal
    by parking, and returns when a production is spent. Revisit detection drops a configuration already seen this pass,
    so a nullable loop, which returns to a state it has held, terminates.
    """
    parked, work, seen = [], list(configs), set()
    while work:
        config = work.pop()
        if len(config.frames) > _DEPTH_CAP:
            raise RuntimeError("the determinizer walk did not converge — an unbounded run the emission must loop")
        if config in seen:
            continue
        seen.add(config)
        if not config.frames:
            parked.append(Parked(config, None))  # an accepting path: nothing more to consume
            continue
        name, alternative, cursor = config.frames[-1]
        chosen = grammar[name].body.alternatives[alternative]
        if cursor == 0 or (isinstance(cursor, tuple) and cursor[0] == "act"):
            start = 0 if cursor == 0 else cursor[1]
            code, parked_here = config.code, False
            for index in range(start, len(chosen.actions)):
                action = chosen.actions[index]
                if isinstance(action, ir.PushCode):
                    code = action.code
                elif isinstance(action, _CONSUMES):
                    resumed = config.frames[:-1] + ((name, alternative, ("act", index + 1)),)
                    parked.append(
                        Parked(config._replace(frames=resumed, code=code), _consume_spans(grammar, action, chosen.gate))
                    )
                    parked_here = True
                    break
            if not parked_here:
                work.append(config._replace(frames=config.frames[:-1] + ((name, alternative, 1),), code=code))
            continue
        if cursor in (1, 2):
            reference = chosen.first if cursor == 1 else chosen.second
            resume = config.frames[:-1] + ((name, alternative, cursor + 1),)
            if reference is None:
                work.append(config._replace(frames=resume))
            elif _is_terminal(grammar, reference.name):
                spans = normalize._peek_spans(ir.Ref(name=reference.name, args=()), grammar)
                parked.append(Parked(config._replace(frames=resume), spans))
            else:
                for index in range(len(grammar[reference.name].body.alternatives)):
                    work.append(config._replace(frames=resume + ((reference.name, index, 0),)))
            continue
        work.append(config._replace(frames=config.frames[:-1]))  # cursor 3: return to the caller
    return parked


def _does_admit(spans, codepoint):
    """Whether `spans` — a list of inclusive `(low, high)` ranges — contains `codepoint`."""
    return spans is not None and any(low <= codepoint <= high for low, high in spans)


def _step(grammar, parked, codepoint):
    """The parked set after consuming `codepoint`: every path whose frontier admits it, advanced and re-closed."""
    advanced = [entry.config for entry in parked if _does_admit(entry.spans, codepoint)]
    return _closure(grammar, advanced)


def _caller_frame(grammar, root):
    """
    The caller's continuation to root a conflict beneath, so a path that returns reaches its follow rather than
    stopping.

    A conflict resolves on what comes *after* it — the folded fold line accepts where a content line follows, and that
    line is the caller's, not the conflict's. So the walk is rooted at the conflict with the frame of the production
    that calls it sitting under it, its cursor past the call. Returns the single such frame, or raises where the
    conflict is not called from exactly one place — the follow would then be several, which this does not yet handle.
    """
    callers = []
    for name, production in grammar.items():
        if not isinstance(production.body, ir.Choice):
            continue
        for index, alternative in enumerate(production.body.alternatives):
            if alternative.first is not None and alternative.first.name == root:
                callers.append((name, index, 2))
            if alternative.second is not None and alternative.second.name == root:
                callers.append((name, index, 3))
    if len(callers) != 1:
        raise ValueError(f"{root}: called from {len(callers)} places — the follow is not yet unique")
    return callers[0]


def _rooted(grammar, root):
    """The conflict's live alternatives, each parked at its first consume, rooted beneath the caller's continuation."""
    frame = _caller_frame(grammar, root)
    starts = [Config((frame, (root, index, 0)), None, index) for index in range(len(grammar[root].body.alternatives))]
    return _closure(grammar, starts)


def divergent_holds(grammar, root):
    """
    The tokens a conflict must hold, each mapped to the code its origins give it.

    At the conflict's first frontier the live paths consume the same character; where they give it different codes, the
    token cannot be handed back until the run resolves — it is held, and retyped at resolution to the code of whichever
    path survives. The result maps each held character span to `{origin: code}`, and is empty where the paths agree on
    every code, needing no held token. This is the retype's source: the surviving path's entry is the code to retype to.
    """
    by_span = collections.defaultdict(dict)
    for parked in _rooted(grammar, root):
        if parked.spans is not None:
            for span in parked.spans:
                by_span[span][parked.config.origin] = parked.config.code
    return {span: codes for span, codes in by_span.items() if len(set(codes.values())) > 1}


def _shared_codepoint(parked):
    """A codepoint every still-consuming origin admits — the shared prefix the walk advances through, or `None`."""
    per_origin = collections.defaultdict(list)
    for entry in parked:
        if entry.spans is not None:
            per_origin[entry.config.origin].extend(entry.spans)
    if not per_origin:
        return None
    candidates = {low for spans in per_origin.values() for low, _high in spans}
    for codepoint in sorted(candidates):
        if all(any(_does_admit(spans, codepoint) for spans in [origin_spans]) for origin_spans in per_origin.values()):
            return codepoint
    return None


def content_origin(grammar, root):
    """
    The origin that survives where content follows — the conflict's resolution when the run is not the chomped-away way.

    The walk advances through the shared prefix the origins agree on; where one origin accepts — completes its way with
    nothing more to consume — that is the content reading, since the alternatives that keep consuming are the empty or
    trailing ways. Returns that origin, its held codes the retype targets, or `None` where the walk diverges with no
    accept within the bound (a run the emission must resolve as a loop rather than a straight commit).
    """
    parked = _rooted(grammar, root)
    for _bound in range(64):
        accepting = sorted(entry.config.origin for entry in parked if entry.spans is None)
        if accepting:
            return accepting[0]
        codepoint = _shared_codepoint(parked)
        if codepoint is None:
            return None
        parked = _step(grammar, parked, codepoint)
    return None


def derive_retype(grammar, root):
    """
    The `RetypeProvisional` a conflict commits, as `(rest, breaks, region)`, derived from its held tokens and survivor.

    Each held token is retyped to the surviving content path's own code for it, slotted by kind — a break-consumed token
    into `breaks`, any other into `rest`. The region is `all` where no mark divides the run. `None` where the conflict
    holds nothing, or its content path is a loop no straight commit resolves.
    """
    holds = divergent_holds(grammar, root)
    if not holds:
        return None
    survivor = content_origin(grammar, root)
    if survivor is None:
        return None
    rest = breaks = None
    for span, codes in holds.items():
        target = codes.get(survivor)
        if span[0] in _BREAKS and span[1] in _BREAKS:
            breaks = target
        else:
            rest = target
    return (rest, breaks, "all")


def determinize(grammar, namer):
    """
    The grammar with the flow fold determinized — detected, generated, and replaced by the engine rather than hand-cut.

    The fold's site sequences `b-l-folded` with the line prefix that follows it; the conflict is `b-l-folded`, whose
    break is taken one way as a trimmed empty line and the other as a folded space, on one gate. The run opens over the
    break and holds it, the next line is read through — its indent and whites carry the same codes whatever the outcome
    — and the one character past them decides: a break is an empty line, committing the trimmed way with the held break
    kept a `break`; anything else, the end of the stream included, retypes it and commits, the follower's prefix already
    consumed. The retype is not named here — `derive_retype` reads it off the conflict. Past the commitment the
    empty-line loop decides every further break at its own gate. The productions the site called stay in the grammar,
    reached by their own fixtures; what falls out of the stream's reach is a later sweep's.
    """
    conflict = "b-l-folded_c_flow-in"
    # The site is the point of interest the pipeline tracks by content — whatever production now calls the conflict —
    # not a minted number a step could renumber out from under this.
    held = [name for name in namer.points.current("flow-fold-site") if len(grammar[name].body.alternatives) == 1]
    sites = [
        name
        for name in held
        if isinstance(grammar[name].body.alternatives[0].first, ir.Ref)
        and grammar[name].body.alternatives[0].first.name == conflict
    ]
    if len(sites) != 1:
        raise AssertionError(f"the flow-fold site is {len(sites)} single-way callers of {conflict}, not the one")
    [site] = sites
    old = grammar[site]
    [way] = old.body.alternatives
    if way.second is None:
        raise AssertionError(f"{site}: the fold site no longer sequences {conflict} with a follower")

    code_param = normalize.CODE
    n, code, origin = ir.Param(name="n"), ir.Param(name=code_param), ir.Param(name="match_start")
    breaks = way.gate.peek  # the site's own break class, kept as it is
    space, tab, white = ir.Char(cp=0x20), ir.Char(cp=0x09), ir.Ref(name="s-white", args=())
    below_n = ir.Lt(a=ir.Len(arg=ir.Match()), b=n)  # the column, spaces alone consumed since the line began
    at_n = ir.Le(a=n, b=ir.Len(arg=ir.Match()))

    def alternative(peek=None, guards=(), actions=(), first=None, second=None):
        gate = ir.Gate(peek=peek, guards=tuple(guards))
        return ir.Alternative(gate=gate, actions=tuple(actions), first=first, second=second, recover=None)

    def production(name, params, *alternatives):
        return ir.Prod(number=old.number, name=name, params=tuple(params), body=ir.Choice(alternatives=alternatives))

    def ref(name, *args):
        return ir.Ref(name=name, args=tuple(args))

    names = [namer.fresh(site) for _index in range(8)]
    scan_enter, scan, whites, decide, empties, empties_scan, empties_whites, empties_decide = names

    def line_scan(name, on_empty, on_content, after_whites):
        # One fresh line, its column measured from the `(<<<)` origin the enter production set at its start: spaces
        # below `n` are the indent; at `n` the rest are whites; a break at any column is an empty line; and past the
        # gates, content or the stream's end at exactly `n` ends the scan with the prefix consumed. Under `n` with
        # anything but a break there is no way, exactly where the empty line's short indent and the follower's full
        # prefix refuse.
        edge = (ir.CloseMatch(), ir.PopCode())
        return production(
            name,
            ("n", code_param, "match_start"),
            alternative(peek=space, guards=(below_n,), actions=(ir.ConsumeChar(),), first=ref(name, n, code, origin)),
            alternative(peek=space, guards=(at_n,), actions=edge, first=ref(after_whites, n)),
            alternative(peek=tab, guards=(at_n,), actions=edge, first=ref(after_whites, n)),
            alternative(peek=breaks, actions=edge + on_empty, first=ref("b-as-line-feed"), second=ref(empties, n)),
            alternative(guards=(at_n,), actions=edge + on_content),
        )

    def line_whites(name, then):
        rest = (ir.PushCode(code="white"), ir.ConsumeSpan(set=white), ir.PopCode())
        return production(name, ("n",), alternative(peek=white, actions=rest, first=ref(then, n)))

    def line_enter(name, then):
        opened = (ir.PushCode(code="indent"), ir.OpenMatch())
        return production(name, ("n",), alternative(actions=opened, first=ref(then, n, code, origin)))

    rest_code, breaks_code, region = derive_retype(grammar, conflict)
    retype = (ir.RetypeProvisional(rest=rest_code, breaks=breaks_code, region=region), ir.CommitProvisional())
    result = dict(grammar)
    result[site] = production(
        site,
        old.params,
        alternative(
            peek=breaks, actions=(ir.OpenProvisional(),), first=ref("b-non-content"), second=ref(scan_enter, n)
        ),
    )
    result[scan_enter] = line_enter(scan_enter, scan)
    result[scan] = line_scan(scan, on_empty=(ir.CommitProvisional(),), on_content=retype, after_whites=whites)
    result[whites] = line_whites(whites, decide)
    result[decide] = production(
        decide,
        ("n",),
        alternative(
            peek=breaks, actions=(ir.CommitProvisional(),), first=ref("b-as-line-feed"), second=ref(empties, n)
        ),
        alternative(actions=retype),
    )
    result[empties] = line_enter(empties, empties_scan)
    result[empties_scan] = line_scan(empties_scan, on_empty=(), on_content=(), after_whites=empties_whites)
    result[empties_whites] = line_whites(empties_whites, empties_decide)
    result[empties_decide] = production(
        empties_decide,
        ("n",),
        alternative(peek=breaks, first=ref("b-as-line-feed"), second=ref(empties, n)),
        alternative(actions=(ir.Empty(),)),
    )
    namer.points.retire("flow-fold-site")  # the site is replaced here, so the point has done its work
    return result
