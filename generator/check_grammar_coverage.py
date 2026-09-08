# SPDX-License-Identifier: MIT
"""
Check that the conformance fixtures exercise the productions of the grammar. Both directions count.

Coverage is dynamic rather than by name. A production counts when running a fixture actually reaches it.

So a production lacking a fixture, such as a `seq-spaces` or an `in-flow`, takes cover from the fixtures that reach it.
A production nothing reaches is a gap the suite must fill.

The gate proves coverage by the suite the interpreter reproduces. A fixture the interpreter crashes on leaves its
productions unexercised, and this reports that as a gap.

Reaching a production is half of exercising it. A rule is a decision, and a fixture that sees it say yes and stops there
leaves the other answer untested. So the corpus must also show a rule **reject** an input. A `(cut)` inside a rule is a
way that can happen.

The exception is a rule that *cannot* say no. `l-yaml-stream` matches at any position, and its parts are optional.
Asking for a fixture where it fails would ask for the impossible.

`_is_total` proves that from the body, rather than a list stating it. A rule that cannot say no is worth knowing about
anyway. A total rule swallows whatever arrives, and whatever encloses it says the input ended. `l-yeast-stream` does
that for the root.

A `(cut)` is a decision too, and the same argument applies to it. A cut that does not fire is a commit point nothing has
shown reachable. It is a message nothing has shown right.

The fixtures' own expected output settles that, rather than a watch on the interpreter. That checker is stricter. It
proves the error survived the way back to the caller. A cut that fired inside a lookahead is speculative, and would
prove that it can fire and no more.

`exercised` takes the grammar as an argument, the way the interpreter does. So this gate answers about the base grammar
and about any stage the pipeline hands on. A transformation reshapes the productions, and the fixtures must still
exercise them.
"""

import os
import re
from collections.abc import Iterable, Mapping, Sequence

import annotated2ir
import check_messages
import gate
import interpreter
import ir
import spec_tests
import wire
import yaml

# The parameters whose values a rule arrives at across the board, and the count of those values. The resume policy is
# such a parameter. A context is not. The caller chooses the policy once, and the grammar threads it down unchanged. A
# context comes from the rule that descends into it, and a rule may sit out of reach of a context value.
_AMBIENT = {"r": len(annotated2ir.RESUMES)}

# The nodes that match wherever the parse asks, and the nodes that may refuse wherever they appear. A `(cut)` counts as
# matching. A `(cut)` takes nothing and refuses nothing wherever it appears, and the parse commits there. Whatever comes
# after it is what fails. Both lists run in alphabetical order, and a kind in neither raises rather than falling to
# either.
_ALWAYS = (
    ir.ClearVarAction,
    ir.CloseWindowAction,
    ir.CommitProvisionalAction,
    ir.ConsumeCharAction,  # the gate found the character. Taking it cannot fail.
    ir.CutAction,
    ir.EmitAction,
    ir.EmptyTree,
    # The close takes the open off and asks nothing about whether the turn matched. `DidConsumeSinceOpenGuard` asks
    # that. This close matches wherever the parse reaches it.
    ir.EndMustConsumeAction,
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
_NEVER_SURE = (
    ir.OneCharSet,
    ir.CharSet,  # a set says no where the character falls outside it.
    ir.IsLessEqualGuard,
    ir.IsLessThanGuard,
    ir.DiffSet,
    ir.DidConsumeSinceOpenGuard,  # a turn that took no character says no. That ends the run holding it.
    ir.EndOfStreamGuard,
    ir.InvalidSet,
    ir.LookGuard,
    ir.LookBehindGuard,
    ir.NegLookGuard,
    ir.RangeSet,
    ir.StartOfLineGuard,
)


def _is_total(node: ir.Node, grammar: Mapping[str, ir.Prod], seen: frozenset[str] = frozenset()) -> bool:
    """
    Whether `node` matches at any position and under any parameter value. False where the shape gives no proof of that.

    Proving it takes nothing beyond the shape. A repetition or an optional can take nothing. A sequence is total when
    the items are total, an alternation when any item is total, and a `(case)` when the branches under it are total.
    Anything that reads the input may say no, and so may a zero-width guard.

    Recursion assumes total and lets the rest of the body decide. A rule is total where a path through it does not
    depend on the recursion.

    A `(case)` on `c` or `t` reads the way `check_markers` reads such a case. A rule reached in a context lists that
    context. A branch that is not there is a path nobody can take, rather than a path that says no. Counting a missing
    branch as a refusal would ask for a fixture running `nb-single-text` at `block-in`. A parse reaches it under other
    contexts.

    `r` is not like them and `AMBIENT` says so. A context comes from the rule that descends into it, and a rule may sit
    out of reach of a value. The caller chooses the resume policy once, and the grammar threads it down unchanged. A
    rule then arrives at the policy's values, and the set is the same throughout.

    A `(case)` on `r` with a value missing is therefore a path the parse takes, and it says no. That is exactly how
    `l-recover-entry` declines to answer for a policy that recovers elsewhere.
    """
    return _IS_TOTAL(node, grammar, seen)


def _a_switch_is_total(node: ir.CaseTree, grammar: Mapping[str, ir.Prod], seen: frozenset[str]) -> bool:
    """
    A `(case)`'s answer. The branches under the case are total, and no value the case arrives at goes without a branch.
    """
    if node.default is not None:  # the else covers a value with no branch. No value is left to say no
        return _is_total(node.default, grammar, seen) and all(_is_total(b.item, grammar, seen) for b in node.branches)
    if len(node.branches) < _AMBIENT.get(node.var, 0):
        return False  # a value of an ambient parameter with no branch is a path that is taken, and says no
    return all(_is_total(branch.item, grammar, seen) for branch in node.branches)


def _a_way_is_total(node: ir.AlternativeState, grammar: Mapping[str, ir.Prod], seen: frozenset[str]) -> bool:
    """
    An alternative's answer. The gate decides entry. A gate holding a guard may say no. A way holding no guard takes the
    parse in wherever it arrives. The actions of the way and the productions it hands control to may then say no.
    """
    if node.gate.guards:
        return False
    parts = node.actions + tuple(item for item in (node.first, node.second) if item is not None)
    return all(_is_total(part, grammar, seen) for part in parts)


_IS_TOTAL: ir.Question[bool] = ir.Question(
    "whether a match is total. A total match holds at any position and for any parameter value.",
    {
        _ALWAYS: True,
        _NEVER_SURE: False,
        # A star or an optional matches by taking nothing, and a span consume sits behind a gate that already answered.
        # Neither kind tells its caller no.
        (ir.ConsumeSpanAction, ir.ConsumeTrimmedSpanAction, ir.OptTree, ir.StarTree, ir.TrimStarTree): True,
        # A wrapping `(max)` says no where its production does. The vendored grammar's bare `(max)` is a length note.
        ir.MaxWrapper: lambda node, grammar, seen: (
            _is_total(node.item, grammar, seen) if node.item is not None else False
        ),
        # A recovery answers a cut and stops there. The item inside it is what says no.
        (
            ir.CommitWrapper,
            ir.PlusTree,
            ir.RecoverWrapper,
            ir.RepTree,
            ir.TokenWrapper,
            ir.Wrapper,
        ): lambda node, grammar, seen: _is_total(node.item, grammar, seen),
        ir.BindTree: lambda node, grammar, seen: _is_total(node.cond, grammar, seen),
        ir.SeqTree: lambda node, grammar, seen: all(_is_total(item, grammar, seen) for item in node.items),
        ir.AltTree: lambda node, grammar, seen: any(_is_total(item, grammar, seen) for item in node.items),
        ir.CaseTree: _a_switch_is_total,
        ir.ChoiceState: lambda node, grammar, seen: any(_is_total(way, grammar, seen) for way in node.alternatives),
        ir.AlternativeState: _a_way_is_total,
        # A recursion the walk reaches again counts as total, and the rest of the body decides. A rule is total where a
        # path through it does not depend on the recursion.
        ir.RefCall: lambda node, grammar, seen: node.name in seen
        or _is_total(grammar[node.name].body, grammar, seen | {node.name}),
    },
)


def _exercised(
    grammar: dict[str, ir.Prod], fixtures: Sequence[spec_tests.Fixture] | None = None
) -> tuple[set[str], set[str]]:
    """
    The productions the reproducible fixtures reach, and the productions they see reject an input. The fixtures are the
    whole suite, or `fixtures` where the caller gives them.

    Returns `(reached, rejected)`. A production counts as reached when the body offers a solution, or when a value
    expression evaluates the production. A production counts as rejected when it fails to match. A `(cut)` inside a
    production is a way that can happen. A cut hands back a failure like any other, and holds the message it names.

    The run itself records this, where it enters and hands back productions. This watches the interpreter from inside
    rather than outside. A watcher outside sees the calls that go through the name it replaced, and misses a handler
    reaching a production another way. The report would then read like a covered production.
    """
    coverage = interpreter.Coverage()
    exercisers = spec_tests.load() if fixtures is None else fixtures
    for at, fixture in enumerate(exercisers):
        if at and not at % 100:  # the run is instrumented and slower than a plain one. It says where it is
            ir.say(f"            {at} of {len(exercisers)} fixture(s) exercised")
        # A fixture that crashes here is a fault of its own and is raised. Swallowing it would leave the productions it
        # covers unexercised and report a gap in the grammar.
        arguments = spec_tests.arguments(fixture, grammar)
        interpreter.run(grammar, fixture.production, fixture.input, arguments, coverage=coverage)
    return coverage.reached, coverage.rejected


def _fired() -> set[str]:
    r"""
    The error texts the fixtures' own expected output holds. The `(cut)`s and `(error)`s shown to reach it.

    These are the texts as the wire holds them. The wire escapes them. A comparison against them wants the message
    escaped too. A message naming a backslash is `\x5c` here and a backslash in `messages.yaml`.
    """
    texts = set()
    for fixture in spec_tests.load():
        if not os.path.exists(fixture.output_path):
            continue  # unpaired, which the fixture gate reports
        for token in wire.parse(fixture.expected):
            if token.code == wire.ERROR:
                texts.add(token.text)
    return texts


# The suffix monomorphizing appends to a name. That suffix is a finite parameter and the value it took. Empty where the
# grammar declares no finite parameter.
_MONOMORPHIC_SUFFIX = (
    re.compile(r"_(?:" + "|".join(ir.FINITE_PARAMS) + r")_[a-z]+(?:-[a-z]+)*") if ir.FINITE_PARAMS else None
)
_HELPER_SUFFIX = re.compile(r"(?:_\d+)+$")  # the suffix a transformation's minted helper takes.


def _base(name: str) -> str:
    """
    `name` with its monomorphic-copy and minted-helper suffixes stripped. `foo_c_flow-in_1` comes back as `foo`.

    A base covers the copies and the helpers under it. The fixtures test a base production. A copy or a helper is a
    token-faithful piece of that base, proved to change no token and proved reachable. Neither adds logic of its own to
    leave untested.

    A monomorphic copy differs in a static parameter substitution. A helper is a piece of the body of the base, taken
    out and given a name. That is the tail of a sequence too long to hold a pair of calls, or the turn of a run. The
    corpus already shows the base doing both of those. Requiring more of a helper than of the body it came from would
    ask the corpus for what the untransformed grammar did not need.

    Covering a copy directly would take a fixture per production and context it appears in. That count is combinatorial.
    `[ 'x' ]` reaching a copy where `key: 'x'` does not is a hole in the corpus rather than dead code.

    The credit is the floor and not the ceiling. A base's coverage records that the parse took an input, and not which
    piece took it. The credit answers for a base like `l-recover-entry`. That is a resume policy that declines, and it
    matches at no position. A fixture could reach it under no cutting whatever.
    """
    return _HELPER_SUFFIX.sub("", _MONOMORPHIC_SUFFIX.sub("", name) if _MONOMORPHIC_SUFFIX else name)


def _decided_by_callers(grammar: Mapping[str, ir.Prod], wanted: Iterable[str]) -> set[str]:
    """
    The bases among `wanted` that a caller gates before entering. A refusal by such a base then goes unseen.

    The parse tests a gate before the call behind it. A gate in front of a call turns the parse away from a character
    the production cannot start with. Such a production has no way left to refuse. Asking the corpus for an input that
    makes it refuse asks for an input that cannot exist.

    This is the rule the near case already follows. A gate saying no counts as a refusal by the production behind that
    gate, "otherwise gating a rule correctly would make it look untested". The same holds here of a production whose
    callers have taken up its refusals. Callers further out count too.

    A production some way calls ungated is not among them. There the parse can still walk in and find a refusal.
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


def gaps(
    grammar: dict[str, ir.Prod],
    exercisers: Sequence[tuple[dict[str, ir.Prod], Sequence[spec_tests.Fixture] | None]] | None = None,
) -> list[str]:
    """
    The productions `grammar` leaves unexercised and the messages nothing fires, as error strings. Empty where the
    fixtures reach and reject the productions and say the messages.

    Takes the grammar as an argument, and re-runs on a structurally-transformed grammar whose reshaped productions the
    same fixtures must still exercise. A monomorphic copy counts as covered where its base counts as covered.

    `exercisers` is `(grammar, fixtures)` pairs, and this runs them in turn. That is how a pipeline's grammars pool
    their coverage. A fixture stranded by a transformation runs against the last grammar its production is reachable in.
    A base name exercised at any stage credits its copies here. With `exercisers` left out, `grammar` itself runs
    against the whole suite.

    A base that is total where the fixtures run it goes without the rejection. A production that matches at any position
    shows no refusal to see. A copy that says no where the base matched empty would ask the corpus for a refusal the
    untransformed grammar had no place to show.
    """
    reached_bases: set[str] = set()
    rejected_bases: set[str] = set()
    pairs = [(grammar, None)] if exercisers is None else exercisers
    for at, (stage, fixtures) in enumerate(pairs):
        ir.say(f"        exerciser {at + 1} of {len(pairs)}, {len(fixtures or ())} fixture(s)")
        reached, rejected = _exercised(stage, fixtures)
        reached_bases |= {_base(name) for name in reached}
        rejected_bases |= {_base(name) for name in rejected}
    with open(check_messages.MESSAGES, encoding="utf-8") as handle:
        messages = yaml.safe_load(handle)
    texts = _fired()

    errors = [f"{name}: no reproducible fixture exercises it" for name in grammar if _base(name) not in reached_bases]
    wanting = [
        name
        for name in grammar
        if _base(name) not in rejected_bases and not _is_total(grammar[name].body, grammar, frozenset({name}))
    ]
    excused = {
        base
        for base in {_base(name) for name in wanting}
        for stage, _fixtures in ([] if exercisers is None else exercisers)
        if base in stage and _is_total(stage[base].body, stage, frozenset({base}))
    }
    excused |= _decided_by_callers(grammar, set(wanting))
    errors += [
        f"{name}: no fixture makes the rule reject an input, and the rule is not total"
        for name in wanting
        if _base(name) not in excused
    ]
    errors += [
        f"{code}: no fixture's output holds the message. nothing shows the cut fires."
        for code, text in sorted(messages.items())
        if wire.escape(text.encode("utf-8")) not in texts
    ]
    return errors


def main() -> None:
    grammar = annotated2ir.load()
    with open(check_messages.MESSAGES, encoding="utf-8") as handle:
        message_count = len(yaml.safe_load(handle))
    gate.report(
        gaps(grammar),
        "gap(s) in what the fixtures exercise",
        f"grammar coverage: {len(grammar)} productions matched and rejected, and {message_count} messages fired",
    )


if __name__ == "__main__":
    main()
