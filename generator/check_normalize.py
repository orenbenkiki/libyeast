# SPDX-License-Identifier: MIT
"""
Check that the normalization pipeline preserves the grammar's meaning.

A structural transformation may land where it changes no token the interpreter emits and no event the fold produces. So
this runs the nets the base grammar already passes, on the grammar the pipeline produces.

The first net is the fixtures, a `tests/spec` case reproduced token for token by `check_interpreter`. The other is the
YAML Test Suite, folded to events green-or-declared by `check_star`. Coverage runs on the suite too, and it pools across
the stages.

The proof rests on token and event identity. The vendored-spec check means nothing on a transformed grammar, and this
does not run it.

A stage's grammar drops what the root has stopped reaching. A transformation that replaces a call site therefore strands
the fixtures of the callee it dead-ends. A callee stranded this way is a fixture-only monomorphic copy at
`monomorphize`.

A stranded fixture stays. Such a fixture pins to the last stage whose grammar can still run it. It keeps guarding that
grammar token for token, and credits coverage from there. A fixture no stage at all can run is an error.

A pinned group runs against its own stage. A group passes exactly where the steps preserve the corpus over the stages
its fixtures survive. A later step would otherwise have to restore exactly a stream an earlier step broke. The default
run therefore answers the whole question. A search for the faulty step pays off only after a failure.

This check has a pair of modes. By default it runs the whole pipeline once and judges at the end. `make pc` and CI want
that single pass and a failure reported where it appears.

Under `--bisect` this goes on to name the step. A step breaks what the steps before it kept. The stages run green then
red, and a binary search finds the seam in a logarithmic number of corpus runs.

`--bisect <step>` names the step to suspect first. That is the step just written. The stages either side of it go first.
A right guess costs a pair of runs, and a wrong guess falls through to the search.

An empty pipeline makes the base grammar the whole pipeline. This then passes exactly where the base grammar's gates do.
That is how the net proves itself wired before a transformation rides it.

A corpus run reaches a question's handlers. This reports a handler no corpus run reached. `a-kind-dispatch-raises` wants
that report beside the raise `ir` performs.
"""

import argparse
import os
from collections.abc import Iterable, Sequence

import annotated2ir
import check_grammar_coverage
import check_interpreter
import check_star
import gate
import interpreter
import ir
import normalize
import spaces
import spec_tests


def _corpus_errors(
    label: str, grammar: dict[str, ir.Prod], fixtures: Sequence[spec_tests.Fixture], suite: Sequence[str]
) -> list[str]:
    """
    The cases `grammar` does not reproduce. An error names the step that produced `grammar`. This keeps the fixtures
    `grammar` can still run. A stranded fixture names a production this grammar has stopped holding.
    """
    runnable = [fixture for fixture in fixtures if spec_tests.runnable_fault(fixture, grammar) is None]
    errors = [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, runnable)]
    return errors + [f"[{label}] star {error}" for error in check_star.disagreements(grammar, suite)]


def _spaces_held(grammar: dict[str, ir.Prod], fixtures: Sequence[spec_tests.Fixture]) -> list[str]:
    """
    The places a parse reaches that the spaces say it cannot, as error strings.

    The other checks read the grammar to work out which places a parse may reach. This holds those checkers to a parse
    that really happened. A space that refuses a place the parse is actually in came out too narrow. A checker reading
    only the grammar catches none of that. The entry and exit spaces reach this gate and stop there. The gate passes a
    space that admits more than a parse reaches. The fault shows once a step prunes by that space.

    The gate checks both ends of a way, and the far end of an action. An entry check holds what a gate admits. An entry
    check says nothing about where performing something leaves the parse. The action transitions compute that. The entry
    checks pass an action that goes wrong where no gate asks.

    The gate skips an action's near end. The caller supplies that end rather than a checker.
    """
    # Read for a parse from the root alone, which is the machine that ships. A fixture starting outside the states the
    # root brings it to simulates a parse no input makes.
    held = normalize.spaces_of_ways(grammar)
    faults: list[str] = []

    def checking(
        node: ir.Node,
        before: Iterable[spaces.GuardAnswers],
        after: Iterable[spaces.GuardAnswers],
        emitter: interpreter.Emitter,
    ) -> None:
        found = held.get(id(node))
        if found is None:  # an action, compared with where performing it says the parse is left
            said = normalize.after_action(node, spaces.guard_answers_in(before), grammar)
            if after and not any(said.under(one) for one in after):
                faults.append(
                    f"performing {type(node).__name__} at position {emitter.position} leaves the parse where the "
                    f"space says it cannot"
                )
            return
        name, at, entered, leaves = found
        for what, said, answers in (("entering", entered, before), ("leaving", leaves, after)):
            if answers and not any(said.under(one) for one in answers):
                faults.append(
                    f"{name}: way {at} at position {emitter.position} sits on {what} where the space says the way "
                    f"cannot"
                )

    for fixture in fixtures:
        try:
            interpreter.run(
                grammar,
                fixture.production,
                fixture.input,
                spec_tests.arguments(fixture, grammar),
                checking=checking,
            )
        except Exception:  # noqa: BLE001  failure-is-reported: by `_corpus_errors`  # pylint: disable=W0718
            continue
    return sorted(set(faults))


def _pinned(
    stages: Sequence[tuple[str, dict[str, ir.Prod]]], fixtures: Sequence[spec_tests.Fixture]
) -> tuple[list[list[spec_tests.Fixture]], list[str]]:
    """
    The fixtures a stage must pass, as a list of groups parallel to `stages`. A fixture pins to the last stage whose
    grammar can run it. The purge strands that fixture at any later stage.

    The answer also holds the fixtures no stage can run, as error strings.
    """
    groups: list[list[spec_tests.Fixture]] = [[] for _stage in stages]
    errors = []
    for fixture in fixtures:
        last = None
        for index, (_label, grammar) in enumerate(stages):
            if spec_tests.runnable_fault(fixture, grammar) is None:
                last = index
        if last is None:
            errors.append(f"{os.path.basename(fixture.input_path)}: no stage's grammar can run it")
        else:
            groups[last].append(fixture)
    return groups, errors


def _narrowed(
    corpus: Iterable[str], fixtures: Sequence[spec_tests.Fixture], suite: Sequence[str]
) -> tuple[Sequence[spec_tests.Fixture], Sequence[str]]:
    """
    The fixtures and suite cases named in `corpus`, as the pair to search with. The search asks which step first broke
    *these*. The pipeline already answers any other case. The search then runs over the named cases rather than over the
    whole corpus.
    """
    named = {line.split("]", 1)[1].split(":", 1)[0].split(None, 1)[1] for line in corpus if "]" in line}
    # A declared divergence stays in whatever suite is asked about. Its declaration is checked against the cases given,
    # and dropping it would report the declaration stale at each probe.
    cases = [case for case in suite if case in named or case in check_star.DIVERGENCES]
    wanted = [fixture for fixture in fixtures if os.path.basename(fixture.input_path) in named]
    return (wanted, cases) if (wanted or cases) else (fixtures, suite)


def _reporting_stage(corpus: Iterable[str], stages: Sequence[tuple[str, dict[str, ir.Prod]]]) -> int:
    """
    The earliest stage that reported a divergence, as an index. A fixture faces judgement at the last stage whose
    grammar can run it. A fixture failing there puts the break at that stage or below it, and no stage above can be the
    seam.
    """
    labels = [label for label, _grammar in stages]
    reported = [labels.index(line.split("]")[0].lstrip("[")) for line in corpus if line.startswith("[")]
    return min(reported) if reported else len(stages) - 1


def _first_broken(
    stages: Sequence[tuple[str, dict[str, ir.Prod]]],
    fixtures: Sequence[spec_tests.Fixture],
    suite: Sequence[str],
    hint: str | None = None,
    bound: int | None = None,
) -> tuple[str, list[str]] | None:
    """
    The earliest stage whose grammar does not reproduce the corpus, as `(label, errors)`, or `None` where the whole
    pipeline reproduces it. A step breaks what the steps before it kept. The stages run green then red, and a binary
    search finds the seam. The search costs a probe per doubling of the pipeline's length rather than a probe per stage.

    `bound` is the highest stage worth asking about, the earliest stage that already reported a divergence.

    `hint` names a step to suspect first, and the probes around that step bound the search however they fall. A stage
    before the hint that already breaks puts the seam below. A hinted stage that breaks behind a clean stage is the
    seam. A clean hinted stage puts the seam above.

    A hint costs no more than it saves. The search starts from the range the hint left rather than from the whole
    pipeline.
    """
    labels = [label for label, _grammar in stages]
    low = 0  # the stage at `low` is taken to hold; the one at `high` is known not to
    high = len(stages) - 1 if bound is None else bound

    def probed(index: int) -> list[str]:
        """A running search reports a stage before it checks the corpus there, and reports again after the check."""
        ir.say(f"    probing [{labels[index]}] ({index} of {len(stages) - 1})")
        errors = _corpus_errors(*stages[index], fixtures, suite)
        ir.say(f"    [{labels[index]}] {'breaks' if errors else 'holds'} over {high - low} stage(s) still in range")
        return errors

    if hint is not None and hint in labels:
        index = labels.index(hint)
        if 0 < index <= high:
            ir.say(f"suspecting [{hint}] first")
            if probed(index - 1):
                high = index - 1
            else:
                errors = probed(index)
                if errors:
                    return stages[index][0], errors
                low = index
    if not probed(high):
        return None
    first = probed(low)
    if first:
        return stages[low][0], first
    while high - low > 1:
        middle = (low + high) // 2
        if probed(middle):
            high = middle
        else:
            low = middle
    return stages[high][0], probed(high)


def _check(does_bisect: bool = False, hint: str | None = None) -> None:
    """
    Report the steps of the pipeline that change what the fixtures and the suite parse to.

    `does_bisect` makes this narrow a failing step to the production that holds the fault. `hint` names where to look
    first.
    """
    ir.say("loading the fixtures and the suite")
    fixtures = spec_tests.load()
    suite = check_star.cases()
    ir.say(f"{len(fixtures)} fixture(s) and {len(suite)} suite case(s). running {len(normalize.STEPS)} step(s)")
    stages = normalize.stages(annotated2ir.load())
    final_label, final = stages[-1]
    ir.say(
        f"{len(stages) - 1} stage(s) built from {len(final)} production(s) at [{final_label}]. pinning the fixtures."
    )
    groups, errors = _pinned(stages, fixtures)

    corpus = []
    for (label, grammar), pinned in zip(stages, groups):
        if pinned:
            ir.say(f"[{label}] {len(pinned)} pinned fixture(s)")
            corpus += [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, pinned)]
    ir.say(f"[{final_label}] {len(suite)} suite case(s)")
    corpus += [f"[{final_label}] star {error}" for error in check_star.disagreements(final, suite)]
    ir.say(f"[{final_label}] holding the spaces to the states the fixtures really reach")
    errors += [f"[{final_label}] space {error}" for error in _spaces_held(final, groups[-1])]
    if corpus:  # something broke the stream; say so at once, whether or not the step behind it is asked for
        print(f"FAILING: {len(corpus)} corpus divergence(s)", flush=True)
        for divergence in corpus[:5]:
            print(f"    {divergence}", flush=True)
        if does_bisect:
            print("    searching for the step that broke them", flush=True)
            wanted, cases = _narrowed(corpus, fixtures, suite)
            found = _first_broken(stages, wanted, cases, hint, _reporting_stage(corpus, stages))
            if found is not None:
                label, corpus = found
                print(f"    [{label}] is the first stage that does not hold", flush=True)
        else:
            print("    re-run with `--bisect [step]` to name the step behind it", flush=True)
    errors += corpus
    exercisers = [(grammar, pinned) for (_label, grammar), pinned in zip(stages, groups) if pinned]
    ir.say(f"coverage of [{final_label}] against {sum(len(pinned) for _grammar, pinned in exercisers)} exerciser(s)")
    for error in check_grammar_coverage.gaps(final, exercisers):
        errors.append(f"[final] coverage {error}")
    ir.say("holding the pipeline to its own law")
    # The pipeline's own law. Each step's invariant is a count that does not rise, is none where the step settles it,
    # and stays none after.
    for fault in normalize.invariant_faults(stages):
        errors.append(f"[invariant] {fault}")
    # A step naming neither an invariant nor a reason for having none promises what nothing checks. A step left with
    # nothing goes rather than being found a new job.
    for name in normalize.untested_steps():
        errors.append(f"[step] `{name}` names neither an invariant nor a reason for having none")
    # A phase names the invariants it settles. Some step of the phase settles or establishes those.
    for fault in normalize.phase_faults():
        errors.append(f"[phase] {fault}")
    # What the steps asked of `GUARD_CROSSES_ACTION` that it does not answer, and what it answers that nothing asked. An
    # unnamed pair refuses the move, which looks exactly like a worked-out no unless it is said here.
    for guard, action in normalize.GUARD_CROSSES_ACTION.unnamed():
        errors.append(f"[crossing] a walk asked about `{guard}` in front of `{action}`, and the table does not say")
    for guard, action in normalize.GUARD_CROSSES_ACTION.unconsulted():
        errors.append(f"[crossing] the table says `{guard}` in front of `{action}`. nothing asked about that pair.")
    # What the runs asked of a global that one value for the parse could not have answered. A global holds a value that
    # does not nest, said of the grammar that has just run rather than of the argument that made it one.
    asked = interpreter.ASKED["flattened"]
    if asked:
        errors.append(f"[global] {asked} read(s) answered from a stack a single slot could not have held")

    # Each question that answered a kind it had called untested, and which kind. Said before the gate reports, which
    # exits and would take the list with it.
    owed = ir.owed()
    pairs = [f"{what} - {kind}" for what, kinds in owed.items() for kind in kinds]
    ir.say(
        f"{len(pairs)} question answer(s) taken on a family's word and untested, against a corpus that "
        f"{'did not hold' if corpus else 'held'}: " + ("; ".join(pairs) or "none")
    )
    for what, kinds in owed.items():
        for kind in kinds:
            errors.append(
                f"[question] the question of {what} calls {kind} untested and that call has arrived. say whether the "
                f"family naming the question covers that call. then take it off that list, or name a family that "
                f"tells the question from that call."
            )

    # The rounds each fixpoint took, deepest first. Said before the gate reports, which exits where anything failed and
    # would take this with it. `ir.ROUNDS` is a backstop, and this line says how far out of reach it is.
    deepest = ir.deepest_rounds()
    ir.say(
        f"deepest fixpoint {max(deepest.values(), default=0)} round(s) against a cap of {ir.ROUNDS}: "
        + ("; ".join(f"{what} {took}" for what, took in deepest.items()) or "none")
    )

    ir.say("done, results:")
    gate.report(
        errors,
        "normalization fault(s) - a step that changes the grammar's meaning, or an invariant broken with no reason "
        "given",
        f"normalization pipeline: {len(normalize.STEPS)} step(s) preserve {len(fixtures)} fixtures and {len(suite)} "
        f"suite cases",
    )
    print("    " + " -> ".join(step.name for step in normalize.STEPS))
    # The stranded fixtures. Each guards the last stage whose grammar can still run it, the purge having taken its
    # production out of the later ones.
    stranded = len(fixtures) - len(groups[-1])
    print(f"    {stranded} fixture(s) pinned to an earlier stage's grammar, the last to run them")
    # The steps with no invariant that say why, which is the one way a step may name none. A fault above counts the
    # rest.
    exempt = [step.name for step in normalize.STEPS if step.untestable]
    print(f"    {len(exempt)} of {len(normalize.STEPS)} step(s) have no invariant and say why: " f"{', '.join(exempt)}")
    # A question's handler nothing reached. A kind is exercised by the inputs that reach it, and a run over the whole
    # corpus says a handler is dead.
    unused = ir.unexercised()
    print(
        f"    {sum(len(kinds) for kinds in unused.values())} question handler(s) nothing reached: "
        + ("; ".join(f"{what} - {', '.join(kinds)}" for what, kinds in unused.items()) or "none")
    )
    # What the final grammar still breaks, whatever the steps settle between them. Each is a step not yet written.
    unsettled = normalize.unsettled_invariants(final)
    print(
        f"    {len(unsettled)} invariant(s) the final grammar still breaks: "
        + ", ".join(f"{name} {count}" for name, count in unsettled)
    )
    print(f"    {len(final)} production(s) in the grammar the phase hands on")
    # The globals the grammar has come to hold, and what the runs asked of them that one slot could not have answered.
    # At none the slot is the stack, and the value does not nest.
    held = [name for name in ir.GLOBAL_PARAMS if not any(name in final[production].params for production in final)]
    print(f"    {len(held)} global(s) - {', '.join(held) or 'none'} - asked {asked} read(s) a single slot could not")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check that the normalization pipeline preserves the grammar's meaning"
    )
    parser.add_argument(
        "--bisect",
        nargs="?",
        const=True,
        metavar="STEP",
        help="on a corpus failure, name the step behind it. suspect STEP first.",
    )
    arguments = parser.parse_args()
    hint = arguments.bisect if isinstance(arguments.bisect, str) else None
    gate.run_deep(lambda: _check(does_bisect=arguments.bisect is not None, hint=hint))


if __name__ == "__main__":
    main()
