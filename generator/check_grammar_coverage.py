# SPDX-License-Identifier: MIT
"""
Check that the conformance fixtures exercise every production of the grammar, both ways.

Coverage is dynamic, not by name: a production counts when running a fixture actually reaches it — so a production with
no fixture of its own (a `seq-spaces`, an `in-flow`) is covered by the fixtures that reach it, and one that nothing
reaches is a gap the suite must fill. What the gate proves is coverage by the suite the interpreter reproduces, since a
fixture it crashes on leaves its productions unexercised and is reported as the gap it is.

Reaching a production is half of exercising it. A rule is a decision, and a fixture that only ever watches it say yes
leaves the other answer untested — so each must also be seen to **reject** an input, a `(cut)` inside it being one of
the ways it can. The exception is a rule that *cannot* say no: `l-yaml-stream` matches at every position because every
part of it is optional, and asking for a fixture where it fails would be asking for the impossible. Those are computed,
not listed — `is_total` proves it from the body — and a rule that can never say no is worth knowing about anyway: a
total rule swallows whatever it is given, so what says the input ended is whatever encloses it, `l-yeast-stream` for the
root.

A `(cut)` is a decision too, and the same argument applies to it: one that never fires is a commit point nothing has
shown is reachable, and a message nothing has shown is right. That is checked against the fixtures' own expected output
rather than by watching the interpreter, which is stricter — it proves the error survived to be handed back, where a cut
that fired inside a lookahead is speculative and would prove only that it can fire.

`exercised` takes the grammar as an argument, the way the interpreter does, so this gate answers about the base grammar
and about every stage the pipeline hands on — each transformation reshapes the productions, and the fixtures must still
exercise all of them.
"""

import os
import re

import annotated2ir
import check_messages
import gate
import interpreter
import ir
import spec_tests
import wire
import yaml

# The parameters every rule is reached under every value of, and how many values that is. The resume policy is the only
# one: the caller chooses it once and it is threaded down unchanged, where a context is chosen by the rule that descends
# into one and can leave a rule out of reach of a value entirely.
AMBIENT = {"r": len(annotated2ir.RESUMES)}

# The nodes that match wherever they are asked to, and the ones that may always refuse. A `(cut)` counts as matching: it
# takes nothing and refuses nothing where it stands, committing the parse instead, so what fails is whatever comes after
# it. Both lists in alphabetical order, and a kind in neither raises rather than being read as either.
ALWAYS = (
    ir.ClearVarAction,
    ir.CloseWindowAction,
    ir.CommitProvisionalAction,
    ir.ConsumeCharAction,  # the gate found the character, so taking it cannot fail
    ir.ConsumePeekedAction,  # the gate found the literal, so taking it cannot fail
    ir.CutAction,
    ir.EmitAction,
    ir.EmptyTree,
    ir.ErrorAction,
    ir.ExcludeAtAction,
    ir.FlipValue,
    ir.IncreaseAction,
    ir.InjectBeforeAction,
    ir.MarkProvisionalAction,
    ir.OpenProvisionalAction,
    ir.OpenWindowAction,
    ir.PopBackTrackAction,
    ir.PopCodeAction,
    ir.PopIndentAction,
    ir.PopMessageAction,
    ir.PopRecoveryAction,
    ir.PushBackTrackAction,
    ir.PushCodeAction,
    ir.PushIndentAction,
    ir.PushMessageAction,
    ir.PushRecoveryAction,
    ir.RetypeProvisionalAction,
    ir.SetForbiddenAction,
    ir.SetVarAction,
    ir.StartMustConsumeAction,
)
NEVER_SURE = (
    ir.OneCharSet,
    ir.CharSet,  # a set says no where the character is not one of its own
    ir.ConsumeLiteralAction,  # a fixed sequence says no where the input does not spell it
    ir.ColumnLeGuard,
    ir.ColumnLtGuard,
    ir.DiffSet,
    ir.EndMustConsumeGuard,  # a turn that took no character says no, which is what ends the run holding it
    ir.EndOfStreamGuard,
    ir.InvalidSet,
    ir.LiteralPeekGuard,  # a gate's literal form says no where the input does not begin it
    ir.LookGuard,
    ir.LookBehindGuard,
    ir.NegLookGuard,
    ir.RangeSet,
    ir.StartOfLineGuard,
)


def is_total(node, grammar, seen=frozenset()):
    """
    Whether `node` matches at every position and for every parameter value, provably — or not at all.

    Proving it takes only the shape: a repetition or an optional can take nothing, a sequence is total when every item
    is, an alternation when any item is, a `(case)` when every branch it has is total. Anything that reads the input,
    and every zero-width guard, may say no. Recursion assumes total and lets the rest of the body decide, so a rule is
    total only when some path through it does not depend on the recursion.

    A `(case)` on `c` or `t` is read the way `check_markers` reads one: a rule reached only in some contexts lists only
    those, so a branch that is not there is a path that cannot be taken rather than one that says no. Counting a missing
    branch as a refusal would ask for a fixture running `nb-single-text` at `block-in`, which nothing reaches it with.

    `r` is not like them and `AMBIENT` says so. A context is chosen by the rule that descends into one, so a rule can be
    out of reach of a value; the resume policy is chosen once by the caller and threaded to everything unchanged, so
    every rule is reached under every value of it. A `(case)` on `r` with a value missing is therefore a path that is
    taken and says no — which is exactly how `l-recover-entry` declines to answer for a policy that recovers elsewhere.
    """
    return _IS_TOTAL(node, grammar, seen)


def _a_switch_is_total(node, grammar, seen):
    """A `(case)`'s answer: every branch it has is total, and no value it is reached under is left without one."""
    if node.default is not None:  # the else covers every value with no branch, so no value is left to say no
        return is_total(node.default, grammar, seen) and all(is_total(b.item, grammar, seen) for b in node.branches)
    if len(node.branches) < AMBIENT.get(node.var, 0):
        return False  # a value of an ambient parameter with no branch is a path that is taken, and says no
    return all(is_total(branch.item, grammar, seen) for branch in node.branches)


def _a_way_is_total(node, grammar, seen):
    """
    An alternative's answer: it is entered on its gate, so one holding a guard may say no; one holding none is entered
    always, and then what may say no is its actions and the productions it hands control to.
    """
    if node.gate.guards:
        return False
    parts = node.actions + tuple(item for item in (node.first, node.second) if item is not None)
    return all(is_total(part, grammar, seen) for part in parts)


_IS_TOTAL = ir.Reading(
    "whether a match is total — matching at every position and for every parameter value",
    {
        ALWAYS: True,
        NEVER_SURE: False,
        # A repetition of none or more, an optional and a scan all take nothing where nothing is there.
        (ir.ConsumeSpanAction, ir.ConsumeTrimmedSpanAction, ir.OptTree, ir.StarTree, ir.TrimStarTree): True,
        # A wrapping `(max)` says no where its production does; the vendored grammar's bare `(max)` is a length note.
        ir.MaxWrapper: lambda node, grammar, seen: (
            is_total(node.item, grammar, seen) if node.item is not None else False
        ),
        # A recovery answers a cut and nothing else, so what says no is the item saying it.
        (
            ir.CommitWrapper,
            ir.PlusTree,
            ir.RecoverWrapper,
            ir.RepTree,
            ir.TokenWrapper,
            ir.Wrapper,
        ): lambda node, grammar, seen: is_total(node.item, grammar, seen),
        ir.BindTree: lambda node, grammar, seen: is_total(node.cond, grammar, seen),
        ir.SeqTree: lambda node, grammar, seen: all(is_total(item, grammar, seen) for item in node.items),
        ir.AltTree: lambda node, grammar, seen: any(is_total(item, grammar, seen) for item in node.items),
        ir.CaseTree: _a_switch_is_total,
        ir.ChoiceState: lambda node, grammar, seen: any(is_total(way, grammar, seen) for way in node.alternatives),
        ir.AlternativeState: _a_way_is_total,
        # A recursion reached again is taken as total, the rest of the body deciding: a rule is total only where some
        # path through it does not depend on the recursion.
        ir.RefCall: lambda node, grammar, seen: node.name in seen
        or is_total(grammar[node.name].body, grammar, seen | {node.name}),
    },
)


def exercised(grammar, fixtures=None):
    """
    The productions the reproducible fixtures reach, and the ones they see reject an input — the whole suite's, or
    `fixtures`' where given.

    Returns `(reached, rejected)`. A production is reached when its body offers a solution or a value expression
    evaluates it; it is rejected when it fails to match, a `(cut)` inside it being one of the ways it can — a cut hands
    back a failure like any other, carrying the message it names beside it.

    The run itself records this, where it enters and hands back productions. Nothing here watches the interpreter from
    outside: a watcher sees only the calls that go through the name it replaced, so a handler reaching a production any
    other way is missed, and the report reads exactly like a covered one.
    """
    coverage = interpreter.Coverage()
    exercisers = spec_tests.load() if fixtures is None else fixtures
    for at, fixture in enumerate(exercisers):
        if at and not at % 100:  # the run is instrumented and slower than a plain one, so it says where it is
            ir.say(f"            {at} of {len(exercisers)} fixture(s) exercised")
        # A fixture that crashes here is a fault of its own and is raised: swallowing it would leave the productions it
        # covers unexercised and report that instead, which says the grammar has a gap where what happened is that the
        # parse died. Every fixture runs clean today, so nothing is being made stricter.
        arguments = spec_tests.arguments(fixture, grammar)
        interpreter.run(grammar, fixture.production, fixture.input, arguments, coverage=coverage)
    return coverage.reached, coverage.rejected


def fired():
    r"""
    The error texts the fixtures' own expected output carries — the `(cut)`s and `(error)`s shown to reach it.

    These are the texts as the wire holds them, escaped, which is what a message must be escaped to be compared against:
    a message naming a backslash is `\x5c` here and a backslash in `messages.yaml`.
    """
    texts = set()
    for fixture in spec_tests.load():
        if not os.path.exists(fixture.output_path):
            continue  # unpaired, which the fixture gate reports
        for token in wire.parse(fixture.expected):
            if token.code == wire.ERROR:
                texts.add(token.text)
    return texts


_MONOMORPHIC_SUFFIX = (
    re.compile(r"_(?:" + "|".join(ir.FINITE_PARAMS) + r")_[a-z]+(?:-[a-z]+)*") if ir.FINITE_PARAMS else None
)
_HELPER_SUFFIX = re.compile(r"(?:_\d+)+$")  # what a transformation's minted helper carries


def _base(name):
    """
    `name` with its monomorphic-copy and minted-helper suffixes stripped — `foo_c_flow-in_1` to `foo`. A copy or a
    helper is covered when its base is: the fixtures test a base production, and each is a token-faithful piece of it,
    proved to change no token and reachable, that adds no logic of its own to leave untested. A monomorphic copy differs
    only by a static parameter substitution; a helper is a piece of the base's own body, moved — the tail of a sequence
    too long to hold two calls, the turn of a run — all of which the base is seen to do, so requiring more of one than
    of the body it came from would ask the corpus for what the untransformed grammar never needed. Covering every one
    directly would take a fixture per production and context it appears in — combinatorial, where `[ 'x' ]` reaching a
    copy `key: 'x'` does not is a hole in the corpus, not dead code.

    The credit is the floor and not the ceiling: a base's coverage records that an input was taken, not which of its
    pieces took it. What the credit answers for is a base like `l-recover-entry`, a resume policy that declines and so
    matches nowhere, which no fixture could reach whatever it was cut into.
    """
    return _HELPER_SUFFIX.sub("", _MONOMORPHIC_SUFFIX.sub("", name) if _MONOMORPHIC_SUFFIX else name)


def _decided_by_callers(grammar, wanted):
    """
    The bases among `wanted` that every caller gates before entering — so nothing can be seen to refuse them.

    A gate is tested before the call it stands in front of, so where every way that names a production carries one, the
    parse never enters that production on a character it cannot start with. It has no way left to refuse, and asking the
    corpus for an input that makes it is asking for one that cannot exist.

    This is the rule the one-hop case already follows — a gate saying no counts as the production it guards saying no,
    "otherwise gating a rule correctly would make it look untested" — said of a production whose refusals have all been
    taken up by its callers rather than by the one gate immediately in front of it. A production some way calls ungated
    is not among them: there the parse can still walk in and be turned away.
    """
    gated = {name: True for name in wanted}
    for production in grammar.values():
        body = production.body
        if not isinstance(body, ir.ChoiceState):
            continue
        for way in body.alternatives:
            for held in (way.first, way.second):
                if isinstance(held, ir.RefCall) and held.name in gated and not way.gate.guards:
                    gated[held.name] = False
    called = {name for production in grammar.values() for name in production.references()}
    return {_base(name) for name, is_gated in gated.items() if is_gated and name in called}


def gaps(grammar, exercisers=None):
    """
    The productions `grammar` leaves unexercised and the messages nothing fires, as error strings — empty when the
    fixtures reach and reject every production and carry every message. Takes the grammar as an argument, so it re-runs
    on a structurally-transformed grammar whose reshaped productions the same fixtures must still exercise; a
    monomorphic copy is held covered when its base is. `exercisers` — `(grammar, fixtures)` pairs, each run in its own
    right — is how a pipeline's grammars pool their coverage: a fixture stranded by a transformation runs against the
    last grammar its production is reachable in, and a base name exercised at any stage credits its copies here. Left
    out, `grammar` itself is exercised with the whole suite.

    A base that is total where the fixtures run it is excused the rejection: nothing can be seen to reject what matches
    at every position, and a copy that says no where the base matched empty would be asking the corpus for a refusal the
    untransformed grammar had nowhere to show.
    """
    reached_bases, rejected_bases = set(), set()
    pairs = [(grammar, None)] if exercisers is None else exercisers
    for at, (stage, fixtures) in enumerate(pairs):
        ir.say(f"        exerciser {at + 1} of {len(pairs)}, {len(fixtures or ())} fixture(s)")
        reached, rejected = exercised(stage, fixtures)
        reached_bases |= {_base(name) for name in reached}
        rejected_bases |= {_base(name) for name in rejected}
    with open(check_messages.MESSAGES) as handle:
        messages = yaml.safe_load(handle)
    texts = fired()

    errors = [f"{name}: no reproducible fixture exercises it" for name in grammar if _base(name) not in reached_bases]
    wanting = [
        name
        for name in grammar
        if _base(name) not in rejected_bases and not is_total(grammar[name].body, grammar, frozenset({name}))
    ]
    excused = {
        base
        for base in {_base(name) for name in wanting}
        for stage, _fixtures in ([] if exercisers is None else exercisers)
        if base in stage and is_total(stage[base].body, stage, frozenset({base}))
    }
    excused |= _decided_by_callers(grammar, {name for name in wanting})
    errors += [
        f"{name}: no fixture makes it reject an input, and it is not total"
        for name in wanting
        if _base(name) not in excused
    ]
    errors += [
        f"{code}: no fixture's output carries its message, so nothing shows the cut fires"
        for code, text in sorted(messages.items())
        if wire.escape(text.encode("utf-8")) not in texts
    ]
    return errors


def main():
    grammar = annotated2ir.load()
    with open(check_messages.MESSAGES) as handle:
        message_count = len(yaml.safe_load(handle))
    gate.report(
        gaps(grammar),
        "gap(s) in what the fixtures exercise",
        f"grammar coverage: {len(grammar)} productions matched and rejected, {message_count} messages fired",
    )


if __name__ == "__main__":
    main()
