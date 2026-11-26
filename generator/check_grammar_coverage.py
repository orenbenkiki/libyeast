# SPDX-License-Identifier: MIT
"""
Check that the conformance fixtures exercise the productions of the grammar.

Coverage is dynamic rather than by name. A production counts when running a fixture actually reaches it.

A production nothing reaches is a gap the suite must fill.

The gate proves coverage by running the fixtures through the interpreter. It does not compare the output. A crash of the
interpreter on a fixture stops the gate.

Reaching a production is half of exercising it. A rule is a decision. A fixture that sees the rule say yes leaves the
other answer untested. So the corpus must also show a rule **reject** an input. A `(cut)` inside a rule is a way for the
rule to reject. `interpreter._try_cut` fails the match when the parse past the cut fails. `interpreter._try_call`
records a rule whose match failed as rejected.

A rule that *cannot* say no is exempt. `l-yaml-stream` matches at any position, and its parts are optional. A fixture
where `l-yaml-stream` fails cannot exist.

`_is_total` proves a rule total from its body rather than from a list. A total rule can match nothing at all. The rule
enclosing a total rule says whether the input ended. `l-yeast-stream` does that for the root.

A `(cut)` is a decision too, and the same argument applies to it. A cut that does not fire is a commit point nothing has
shown reachable.

The gate reads a cut firing off the fixtures' own expected output rather than off a watch on the interpreter. The
expected output is stricter than such a watch. It proves the cut's error survived the way back to the caller. A cut that
fired inside a lookahead is speculative. Such a firing proves that the cut can fire and no more.
`interpreter._try_probe` drops the failure of such a cut.

`_exercised` takes the grammar as an argument, the way the interpreter does. So this gate answers about the base grammar
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

# `_ALWAYS` lists the nodes that match wherever the parse asks. A `(cut)` counts as matching. `interpreter._try_cut`
# takes nothing and refuses nothing, and the parse commits there. A failure past a `(cut)` reports the `message` of that
# `(cut)`, unless a failure that came first already set `failing`. A later `(cut)` or the edge of a `(max)` window can
# set `failing` first. The list is alphabetical. `_IS_TOTAL` is a `Question`. A `Question` raises on a kind its table
# leaves out.
_ALWAYS = (
    ir.ClearVarAction,
    ir.CloseWindowAction,
    ir.CommitProvisionalAction,
    # A gate found the character a `ConsumeCharAction` takes. `interpreter._try_gated_char` takes it. The take fails
    # only past the edge of a `(max)` window, where `Emitter.try_consume` declines the character.
    ir.ConsumeCharAction,
    ir.CutAction,
    ir.EmitAction,
    ir.EmptyTree,
    # `interpreter._try_close_turn` takes the open off and asks nothing about whether the turn took a character.
    # `interpreter._try_did_consume_since_open` asks that. This close matches wherever the parse reaches it.
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
    # A turn that took no character says no. `interpreter._try_did_consume_since_open` answers so.
    ir.DidConsumeSinceOpenGuard,
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

    Proving it takes nothing beyond the shape. A star or an optional can take nothing. A sequence is total when the
    items are total. An alternation is total when any item is total. A `(case)` is total when the branches under it are
    total. A set that tests a character may say no. A zero-width guard may say no as well.

    Recursion assumes total and lets the rest of the body decide. A rule is total where the parts of its body beside the
    recursion are total.

    A `(case)` names a branch per value or has a default. `validate_grammar._check_total_cases` holds it to that.
    """
    return _IS_TOTAL(node, grammar, seen)


def _a_switch_is_total(node: ir.CaseTree, grammar: Mapping[str, ir.Prod], seen: frozenset[str]) -> bool:
    """
    A `(case)`'s answer. The branches under the case are total, and the `default` is total where the case has it.
    """
    if node.default is not None:  # The `else` covers a value with no branch.
        return _is_total(node.default, grammar, seen) and all(_is_total(b.item, grammar, seen) for b in node.branches)
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
        # A wrapping `(max)` says no where its production does. The vendored grammar's bare `(max)` is a length note,
        # and it counts as not total.
        ir.MaxWrapper: lambda node, grammar, seen: (
            _is_total(node.item, grammar, seen) if node.item is not None else False
        ),
        # A recovery says no where the item inside it does.
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
        # A recursion the walk reaches again counts as total, and the rest of the body decides. A rule is total where
        # the parts of its body beside the recursion are total.
        ir.RefCall: lambda node, grammar, seen: node.name in seen
        or _is_total(grammar[node.name].body, grammar, seen | {node.name}),
    },
)


def _exercised(
    grammar: dict[str, ir.Prod], fixtures: Sequence[spec_tests.Fixture] | None = None
) -> tuple[set[str], set[str]]:
    """
    The productions the fixtures reach, and the productions they see reject an input. The fixtures are the whole suite,
    or `fixtures` where the caller gives them.

    Returns `(reached, rejected)`. `interpreter._try_call` reaches a production whose body offers a solution.
    `interpreter._evaluated_call` reaches a production a value expression evaluates. `interpreter._try_call` rejects a
    production that fails to match. `interpreter._try_way` rejects the productions a way calls where the way's gate
    refuses.

    A failure past a `(cut)` counts as a rejection too. `interpreter._try_cut` hands back the failure, and records its
    message where no earlier failure set one.
    """
    coverage = interpreter.Coverage()
    exercisers = spec_tests.load() if fixtures is None else fixtures
    for at, fixture in enumerate(exercisers):
        if at and not at % 100:  # The loop reports its progress.
            ir.say(f"            {at} of {len(exercisers)} fixture(s) exercised")
        # The run raises on a crashing fixture.
        arguments = spec_tests.arguments(fixture, grammar)
        interpreter.run(grammar, fixture.production, fixture.input, arguments, coverage=coverage)
    return coverage.reached, coverage.rejected


def _fired() -> set[str]:
    r"""
    The error texts the fixtures' own expected output holds. The fixtures show that a `(cut)` or `(error)` holding such
    a text reaches the output. `interpreter._try_recover` emits the message of a `(cut)`, and `interpreter._try_error`
    emits the message of an `(error)`.

    These are the texts as the wire holds them. `error` escapes them with `wire.escape`. A comparison against them wants
    the message escaped too. A message naming a backslash is `\x5c` here and a backslash in `messages.yaml`.
    """
    texts = set()
    for fixture in spec_tests.load():
        if not os.path.exists(fixture.output_path):
            continue  # `check_spec_tests` reports an input file that has no output file.
        for token in wire.parse(fixture.expected):
            if token.code == wire.ERROR:
                texts.add(token.text)
    return texts


# The pattern matches the suffix that `ir.specialized` appends to a name. That suffix is a finite parameter and the
# value it took. The pattern is `None` where `ir.FINITE_PARAMS` is empty.
_MONOMORPHIC_SUFFIX = (
    re.compile(r"_(?:" + "|".join(ir.FINITE_PARAMS) + r")_[a-z]+(?:-[a-z]+)*") if ir.FINITE_PARAMS else None
)
_HELPER_SUFFIX = re.compile(r"(?:_\d+)+$")  # the suffix a transformation's minted helper takes.


def _base(name: str) -> str:
    """
    `name` with its monomorphic-copy and minted-helper suffixes stripped. `FINITE_PARAMS` holds `c`. `foo_c_flow-in_1`
    comes back as `foo`.

    A copy or a helper is a piece of its base, and the fixtures test the base.
    """
    return _HELPER_SUFFIX.sub("", _MONOMORPHIC_SUFFIX.sub("", name) if _MONOMORPHIC_SUFFIX else name)


def _ungated_in_a_way(way: ir.AlternativeState) -> list[str]:
    """The productions `way` calls with no guard in front. A way whose gate asks something gates its calls."""
    return [] if way.gate.guards else [held.name for held in (way.first, way.second) if held is not None]


# The productions a body calls with no gate in front. A choice and a way read their gates. A body of another kind holds
# no gate. The calls of such a body go ungated.
_UNGATED_CALLS: ir.Question[list[str]] = ir.Question(
    "the productions a body calls with no gate in front",
    {
        ir.ChoiceState: lambda body: [name for way in body.alternatives for name in _ungated_in_a_way(way)],
        ir.AlternativeState: _ungated_in_a_way,
        (
            *ir.TREES,
            *ir.WRAPPERS,
            *ir.CALLS,
            *ir.VALUE_KINDS,
            *ir.CONSUMING,
            *ir.ACTIONS,
            *ir.GUARDS,
            *ir.PARTS,
        ): lambda body: body.references(),
    },
)


def _decided_by_callers(grammar: Mapping[str, ir.Prod], wanted: Iterable[str]) -> set[str]:
    """
    The bases of the names in `wanted` that some production calls and that no body calls ungated. A refusal by such a
    base then goes unseen.

    `interpreter._try_way` tests a way's gate before the call behind it. A refused gate counts as a refusal by the
    productions the way calls. `accepted-and-gated-charsets-are-equal` holds a way's gate to the characters that way
    consumes itself.

    A production whose callers have taken up its refusals counts the same way as a refused gate. A gate in a caller's
    caller does not count.

    The parse can still walk into a production that some body calls ungated and find a refusal there.
    """
    gated = {wanted_name: True for wanted_name in wanted}
    for production in grammar.values():
        for name in _UNGATED_CALLS(production.body):
            if name in gated:
                gated[name] = False
    called = {called_name for calling_production in grammar.values() for called_name in calling_production.references()}
    return {_base(gated_name) for gated_name, is_gated in gated.items() if is_gated and gated_name in called}


def gaps(
    grammar: dict[str, ir.Prod],
    exercisers: Sequence[tuple[dict[str, ir.Prod], Sequence[spec_tests.Fixture] | None]] | None = None,
) -> list[str]:
    """
    The productions `grammar` leaves unexercised and the messages nothing fires, as error strings. The list is empty
    where the fixtures reach and reject the productions and say the messages.

    A caller may pass a structurally-transformed grammar. The same fixtures must still exercise the productions that
    transformation reshapes. A monomorphic copy counts as covered where its base counts as covered.

    `exercisers` is `(grammar, fixtures)` pairs, and this runs them in turn. That is how a pipeline's grammars pool
    their coverage. A fixture stranded by a transformation runs against the last grammar that can run it. A base name
    exercised at any stage credits its copies here. With `exercisers` left out, `grammar` itself runs against the whole
    suite.

    A base that is total where the fixtures run it goes without the rejection. A production that matches at any position
    shows no refusal to see.
    """
    reached_bases: set[str] = set()
    rejected_bases: set[str] = set()
    pairs = [(grammar, None)] if exercisers is None else exercisers
    for at, (stage, fixtures) in enumerate(pairs):
        ir.say(f"        exerciser {at + 1} of {len(pairs)} over {len(fixtures or ())} fixture(s)")
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
        for stage_grammar, _fixtures in ([] if exercisers is None else exercisers)
        if base in stage_grammar and _is_total(stage_grammar[base].body, stage_grammar, frozenset({base}))
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
