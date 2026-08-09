# SPDX-License-Identifier: MIT
"""
Check that the normalization pipeline preserves the grammar's meaning.

A structural transformation is only allowed if it changes no token the interpreter emits and no event the fold produces.
So this runs the two nets the base grammar already passes, on the grammar the pipeline produces: the fixtures — every
`tests/spec` case reproduced token for token (`check_interpreter`) — and the YAML Test Suite, folded to events
green-or-declared (`check_star`). Coverage runs on it too, pooled across the stages. Token and event identity is the
whole proof; the vendored-spec check is meaningless on a transformed grammar and is not run here.

Each stage's grammar is purged of what the root no longer reaches, so a transformation that replaces a call site strands
the fixtures of the callee it dead-ends — the fold family at `speculate-folds`, the fixture-only monomorphic copies at
`monomorphize`. A stranded fixture is not dropped: it is pinned to the last stage whose grammar can still run it, keeps
guarding that grammar token for token, and credits coverage from there. Only a fixture no stage at all can run is an
error.

Each pinned group is run against its own stage. Every group passing and every step preserving the corpus over the stages
its fixtures survive come to the same thing — a step would have to break the stream and a later one restore it exactly —
so the fast answer is the whole answer, and naming the step that broke it is worth its cost only once something has.

Hence two modes. By default it runs everything and judges at the end, which is what `make pc` and CI want: one pass, and
a failure reported where it stands. Given `--bisect` it goes on to name the step, by binary search — a step only breaks
what the steps before it kept, so the stages are green then red and the seam is found in a logarithmic number of corpus
runs. `--bisect <step>` names the step to suspect first, the one just written: its two neighbouring stages are tried
before the search, so a right guess costs two runs and a wrong one falls through to it.

An empty pipeline makes the one stage the base grammar itself, so this passes exactly when the base's own gates do —
which is how the net is proved wired before a transformation rides it.
"""

import argparse
import os
import sys
import threading
import traceback

import annotated2ir
import check_grammar_coverage
import check_interpreter
import check_star
import gate
import interpreter
import ir
import normalize
import spec_tests

# The recursive helpers a transformed grammar carries recurse as deep as their input is long, past both Python's limit
# and a default stack. So the check runs on a thread given a large one, with the limit raised to match — deep enough for
# any real input, where `interpreter.DEPTH_LIMIT` is the cap that refuses a runaway with a trace before either is hit.
STACK_BYTES = 256 * 1024 * 1024
RECURSION_LIMIT = 200000


def _corpus_errors(label, grammar, fixtures, suite):
    """
    The cases `grammar` does not reproduce, named for the step that produced it — the fixtures filtered to the ones
    `grammar` can still run, a stranded fixture's production being no longer this grammar's to ask about.
    """
    runnable = [fixture for fixture in fixtures if spec_tests.runnable_fault(fixture, grammar) is None]
    errors = [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, runnable)]
    return errors + [f"[{label}] star {error}" for error in check_star.disagreements(grammar, suite)]


def _pinned(stages, fixtures):
    """
    The fixtures each stage is held to, as a list of groups parallel to `stages` — every fixture pinned to the last
    stage whose grammar can run it, the purge having stranded it everywhere later — and the fixtures no stage at all can
    run, as error strings.
    """
    groups = [[] for _stage in stages]
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


def _narrowed(corpus, fixtures, suite):
    """
    The fixtures and suite cases named in `corpus`, as the pair to search with. The question the search asks is which
    step first broke *these*, so every other case is work whose answer is already known — a probe over fourteen cases
    where the whole corpus is eleven hundred.
    """
    named = {line.split("]", 1)[1].split(":", 1)[0].split(None, 1)[1] for line in corpus if "]" in line}
    # A declared divergence stays in whatever suite is asked about: its declaration is checked against the cases given,
    # so dropping it would report the declaration stale at every probe.
    cases = [case for case in suite if case in named or case in check_star.DIVERGENCES]
    wanted = [fixture for fixture in fixtures if os.path.basename(fixture.input_path) in named]
    return (wanted, cases) if (wanted or cases) else (fixtures, suite)


def _reporting_stage(corpus, stages):
    """
    The earliest stage a divergence was reported at, as an index. A fixture is judged at the last stage whose grammar
    can run it, so where one fails there the break is at that stage or below it and no stage above can be the seam.
    """
    labels = [label for label, _grammar in stages]
    reported = [labels.index(line.split("]")[0].lstrip("[")) for line in corpus if line.startswith("[")]
    return min(reported) if reported else len(stages) - 1


def _first_broken(stages, fixtures, suite, hint=None, bound=None):
    """
    The earliest stage whose grammar does not reproduce the corpus, as `(label, errors)`, or `None` where every one
    does. A step only breaks what the steps before it kept, so the stages run green then red and the seam is a binary
    search — six probes over fifty stages rather than fifty.

    `bound` is the highest stage worth asking about, the earliest one a divergence was already reported at. `hint` names
    a step to suspect first, and its two probes bound the search whichever way they fall: a stage before it that already
    breaks puts the seam below, its own stage breaking after a clean one before it *is* the seam, and both holding puts
    the seam above. So a hint never costs more than it saves, and the search that follows one starts from the range it
    left rather than from the whole pipeline.
    """
    labels = [label for label, _grammar in stages]
    low = 0  # the stage at `low` is taken to hold; the one at `high` is known not to
    high = len(stages) - 1 if bound is None else bound
    if hint is not None and hint in labels:
        index = labels.index(hint)
        if 0 < index <= high:
            if _corpus_errors(*stages[index - 1], fixtures, suite):
                high = index - 1
            else:
                errors = _corpus_errors(*stages[index], fixtures, suite)
                if errors:
                    return stages[index][0], errors
                low = index
    if not _corpus_errors(*stages[high], fixtures, suite):
        return None
    first = _corpus_errors(*stages[low], fixtures, suite)
    if first:
        return stages[low][0], first
    while high - low > 1:
        middle = (low + high) // 2
        if _corpus_errors(*stages[middle], fixtures, suite):
            high = middle
        else:
            low = middle
    return stages[high][0], _corpus_errors(*stages[high], fixtures, suite)


def _check(does_bisect=False, hint=None):
    fixtures = spec_tests.load()
    suite = check_star.cases()
    stages, points = normalize.stages(annotated2ir.load())
    final_label, final = stages[-1]
    groups, errors = _pinned(stages, fixtures)

    corpus = []
    for (label, grammar), pinned in zip(stages, groups):
        if pinned:
            corpus += [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, pinned)]
    corpus += [f"[{final_label}] star {error}" for error in check_star.disagreements(final, suite)]
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
    for error in check_grammar_coverage.gaps(final, exercisers):
        errors.append(f"[final] coverage {error}")
    # The pipeline's own law: each step's invariant is a count that never rises, is none where the step settles it, and
    # stays none after — a step breaking one saying so in its `lapses` and why, and a step naming one doing something
    # about it. Every structural property the pipeline claims is judged here, so nothing else below repeats one.
    for fault in normalize.invariant_faults(stages, points):
        errors.append(f"[invariant] {fault}")
    # A step naming neither an invariant nor a reason for having none promises what nothing checks. A step outliving the
    # invariant it was written for is the way one arrives here: the invariant is what the pipeline is for and the step
    # only a means to it, so what is left carrying nothing goes rather than being found a new thing to carry.
    for name in normalize.untested_steps():
        errors.append(f"[step] `{name}` names neither an invariant nor a reason for having none")
    # What the runs asked of a global that one value for the parse could not have answered: the reads where the stack
    # beside it held something the slot did not. A global is what does not nest, and this is what says so of the grammar
    # that has just run rather than of the argument that made it one.
    asked = interpreter.ASKED["flattened"]
    if asked:
        errors.append(f"[global] {asked} read(s) answered from a stack a single slot could not have stood for")

    gate.report(
        errors,
        "normalization fault(s) — a step that changes the grammar's meaning, or an invariant broken with no reason "
        "given",
        f"normalization pipeline: {len(normalize.STEPS)} step(s) preserve {len(fixtures)} fixtures and {len(suite)} "
        f"suite cases",
    )
    print("    " + " -> ".join(step.name for step in normalize.STEPS))
    # The stranded fixtures: each guards the last stage whose grammar can still run it, the purge having taken its
    # production out of every later one.
    stranded = len(fixtures) - len(groups[-1])
    print(f"    {stranded} fixture(s) pinned to an earlier stage's grammar, the last to run them")
    # The steps that carry no invariant and say why, which is the one way a step may name none — a fault above counts
    # the rest.
    exempt = [step.name for step in normalize.STEPS if step.untestable]
    print(
        f"    {len(exempt)} of {len(normalize.STEPS)} step(s) have no invariant to carry and say why: "
        f"{', '.join(exempt)}"
    )
    # A reading's handler nothing reached. Read here and nowhere else: a kind is exercised by the inputs that reach it,
    # so only a run over the whole corpus can say a handler is dead — and a dead one is a guess about the grammar that
    # held, either a kind that cannot occur where the reading is asked or a shape the corpus does not reach.
    unused = ir.unexercised()
    print(
        f"    {sum(len(kinds) for kinds in unused.values())} reading handler(s) nothing reached: "
        + ("; ".join(f"{what} — {', '.join(kinds)}" for what, kinds in unused.items()) or "none")
    )
    # What the final grammar still breaks, whatever the steps settle between them — each one a step not yet written, and
    # the list Phase 03 finishes by emptying. Every structural count the phase watches is in here, the meter among them,
    # so what follows says only what the list cannot: where those counts fall and what they are made of.
    unsettled = normalize.unsettled_invariants(final, points)
    print(
        f"    {len(unsettled)} invariant(s) the final grammar still breaks: "
        + ", ".join(f"{name} {count}" for name, count in unsettled)
    )
    print(f"    {len(final)} production(s) in the grammar the phase hands on")
    # The committed net, and the other half of what determinizing owes. The productions a character decides are entered
    # committed — the first way whose gate holds is the parse, no other tried — and everything else backtracks; a case
    # the two modes read differently is a gate that is disjoint and still wrong, its way failing further on where
    # backtracking would have taken the next. No static count sees that, and every step that moves a gate or moves where
    # an error fires risks it. It is a count and not yet a gate: none of it is refused until it reads none, at which
    # point the two modes agreeing becomes the law the way the corpus already is.
    committed = normalize.deterministic_productions(final)
    unsafe = check_interpreter.reproduced(final, groups[-1], deterministic=committed)
    unsafe += check_star.disagreements(final, suite, deterministic=committed)
    choices = sum(1 for production in final.values() if isinstance(production.body, ir.Choice))
    print(
        f"    {len(committed)} of {choices} choice(s) a character decides, entered committed — "
        f"{len(unsafe)} case(s) the two modes read differently"
    )
    # The globals the grammar has come to hold, and what the runs asked of them that one slot could not have answered.
    # At none the slot is the stack, which is what says the value does not nest.
    held = [name for name in ir.GLOBAL_PARAMS if not any(name in final[production].params for production in final)]
    print(f"    {len(held)} global(s) — {', '.join(held) or 'none'} — asked {asked} read(s) a single slot could not")


def main():
    parser = argparse.ArgumentParser(
        description="Check that the normalization pipeline preserves the grammar's meaning"
    )
    parser.add_argument(
        "--bisect",
        nargs="?",
        const=True,
        metavar="STEP",
        help="on a corpus failure, name the step behind it; STEP is the one to suspect first",
    )
    arguments = parser.parse_args()
    hint = arguments.bisect if isinstance(arguments.bisect, str) else None

    sys.setrecursionlimit(RECURSION_LIMIT)
    threading.stack_size(STACK_BYTES)
    status = {}

    def worker():
        try:
            _check(does_bisect=arguments.bisect is not None, hint=hint)
        except SystemExit as exit:  # gate.report exits on failure; carry its code back to the main thread
            status["code"] = exit.code
        except BaseException:  # noqa: BLE001 — a thread's exception reaches no exit code of its own
            # A crash is a failure, and one raised here would otherwise be printed by the thread's excepthook while the
            # main thread exits zero — a green gate over a check that never finished.
            traceback.print_exc()
            status["code"] = 1

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    sys.exit(status.get("code", 0))


if __name__ == "__main__":
    main()
