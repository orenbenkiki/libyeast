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
so the fast answer is the whole answer, and the slow walk over every stage is worth its cost only when there is a step
to name. A failure is announced the moment it is seen, and the walk then runs backward — new steps land at the
pipeline's end, so the last stage still holding is usually near it, and the step after that stage is the culprit.

An empty pipeline makes the one stage the base grammar itself, so this passes exactly when the base's own gates do —
which is how the net is proved wired before a transformation rides it.
"""

import os
import sys
import threading

import annotated2ir
import check_grammar_coverage
import check_interpreter
import check_star
import gate
import interpreter
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


def _check():
    fixtures = spec_tests.load()
    suite = check_star.cases()
    stages, points = normalize.stages(annotated2ir.load())
    committed = normalize.committed_productions(points)
    groups, errors = _pinned(stages, fixtures)

    corpus = []
    for (label, grammar), pinned in zip(stages, groups):
        if pinned:
            corpus += [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, pinned)]
    corpus += [f"[{stages[-1][0]}] star {error}" for error in check_star.disagreements(stages[-1][1], suite)]
    if corpus:  # something broke the stream; say so at once, then walk backward to name the step that did
        print(f"FAILING: {len(corpus)} corpus divergence(s) — walking back for the step that broke them", flush=True)
        for divergence in corpus[:5]:
            print(f"    {divergence}", flush=True)
        # New steps land at the pipeline's end, so the break is usually late: walk backward for the last stage that
        # still holds, and the step after it is the culprit — one extra pass when the last step broke, where the forward
        # walk would pay one per step.
        culprit = corpus
        for index in range(len(stages) - 2, -1, -1):
            label, grammar = stages[index]
            named = _corpus_errors(label, grammar, fixtures, suite)
            if not named:
                culprit = _corpus_errors(*stages[index + 1], fixtures, suite)
                print(f"    last stage still holding: [{label}] — the step after it broke", flush=True)
                break
        corpus = culprit
    errors += corpus
    final = stages[-1][1]
    deterministic = normalize.deterministic_productions(final, committed)
    if not errors:  # the hybrid run is judged only where the backtracking one stands, so a fault names its mode
        errors += [
            f"[deterministic] fixture {error}"
            for error in check_interpreter.reproduced(final, groups[-1], deterministic=deterministic)
        ]
        errors += [
            f"[deterministic] star {error}"
            for error in check_star.disagreements(final, suite, deterministic=deterministic)
        ]
    exercisers = [(grammar, pinned) for (_label, grammar), pinned in zip(stages, groups) if pinned]
    for error in check_grammar_coverage.gaps(final, exercisers):
        errors.append(f"[final] coverage {error}")
    # The content-run gate reads the `(token)` scopes lower-tokens dissolves, so it runs on the last grammar that still
    # holds them; lower-tokens leaves the character runs it checks untouched, so the two grammars agree on the answer.
    before_lower_tokens = dict(stages)["lower-star"]
    for offender in normalize.content_run_offenders(before_lower_tokens):
        errors.append(f"[content-runs] {offender}: a long text token is collected one character at a time")
    for fault in normalize.non_char_set_runs(stages[-1][1]):
        errors.append(f"[char-set-runs] {fault}")
    for fault in normalize.provisional_faults(stages[-1][1]):
        errors.append(f"[provisional] {fault}")
    for fault in normalize.declared_faults(stages[-1][1], committed):
        errors.append(f"[ledger] {fault}")
    residue = normalize.unshaped_actions(stages[-1][1])

    gate.report(
        errors,
        "normalization fault(s) — a step that changes the grammar's meaning, a content run not matched in bulk, a "
        "repetition that is not a character-set run, or a provisional run that does not balance",
        f"normalization pipeline: {len(normalize.STEPS)} step(s) preserve {len(fixtures)} fixtures and {len(suite)} "
        f"suite cases — backtracking, and hybrid with {len(deterministic)} production(s) entered committed — every "
        f"long text token matched in bulk by a character-set run",
    )
    print("    " + " -> ".join(name for name, _transform in normalize.STEPS))
    # The stranded fixtures: each guards the last stage whose grammar can still run it, the purge having taken its
    # production out of every later one.
    stranded = len(fixtures) - len(groups[-1])
    print(f"    {stranded} fixture(s) pinned to an earlier stage's grammar, the last to run them")
    # Not a fault: what the canonical form does not spell yet, printed so the number is watched down to none rather than
    # discovered later. The determinize phase is what resolves each of them.
    print(f"    {len(residue)} action(s) the canonical form does not spell: a leftover scope or a nullable repetition")
    print(f"    {len(normalize.ungated_alternatives(stages[-1][1]))} alternative(s) with no character to go on")
    # The determinize meter: the corpus is parsed with every proved production entered committed, so this is the count
    # of productions still backtracking — driven to none, at which point it becomes a gate.
    print(f"    {len(final) - len(deterministic)} production(s) not yet deterministic in isolation")
    # The correct meter: the goal is the grammar deterministic as invoked from the root, not every production at every
    # hypothetical entry — so this counts root-reachable decision points, each a production judged under one context's
    # follow, the one-level-inline judgment. This is the number driven to none.
    failing = normalize.context_conflicts(final, committed)
    print(f"    {sum(failing.values())} root-context decision point(s) undecided, across {len(failing)} production(s)")
    # The assurance ledger: committed on a declared reason rather than a proof, each entry held to backtracking by the
    # hybrid run above and to freshness by its own net — watched here so the declared few never grow quietly.
    print(f"    {len(committed)} production(s) committed by declaration — the assurance ledger")
    # Properness, over the tail rather than at the one step that makes it: from the elimination on, no production but
    # the ones a parse enters by name may match empty, since one that does holds a decision its call sites were to have
    # taken. `eliminate_empties` asserts this on its own output; what this counts is the four later steps that hand
    # nullability back — driven to none, at which point it becomes a gate.
    labels = [label for label, _grammar in stages]
    tail = stages[labels.index("eliminate-empties") :]
    improper = [
        len(normalize.improper_faults(grammar, normalize.keeps_empty_ways(grammar))) for _label, grammar in tail
    ]
    print(
        f"    {improper[-1]} production(s) match empty where no call site can hold the choice, "
        f"{max(improper)} at the worst of the {len(tail)} stages from the elimination on"
    )
    # What the corpus asked of a global that one value for the parse could not have answered. Every run above has been
    # answered from a stack beside the slot, so this is what stands between the two and not a count of anything that
    # went wrong: at none, the machine can hold the slot alone.
    print(f"    {interpreter.ASKED['flattened']} read(s) one value for a global could not answer")


def main():
    sys.setrecursionlimit(RECURSION_LIMIT)
    threading.stack_size(STACK_BYTES)
    status = {}

    def worker():
        try:
            _check()
        except SystemExit as exit:  # gate.report exits on failure; carry its code back to the main thread
            status["code"] = exit.code

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    sys.exit(status.get("code", 0))


if __name__ == "__main__":
    main()
