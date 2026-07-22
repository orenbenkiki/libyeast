# SPDX-License-Identifier: MIT
"""
Check that the normalization pipeline preserves the grammar's meaning.

A structural transformation is only allowed if it changes no token the interpreter emits and no event the fold produces.
So this runs the two nets the base grammar already passes, on the grammar the pipeline produces: the fixtures — every
`tests/spec` case reproduced token for token (`check_interpreter`) — and the YAML Test Suite, folded to events
green-or-declared (`check_star`). Coverage runs on it too, catching a production a step left unreachable. Token and
event identity is the whole proof; the vendored-spec check is meaningless on a transformed grammar and is not run here.

The corpus is run over the final grammar first, and over each step's only where that fails. Every step preserving the
corpus and the last one doing so come to the same thing — a step would have to break the stream and a later one restore
it exactly — so the fast answer is the whole answer, and the slow walk is worth its cost only when there is a step to
name. That walk stops at the first step that diverges, which is the one that broke it.

An empty pipeline makes the one stage the base grammar itself, so this passes exactly when the base's own gates do —
which is how the net is proved wired before a transformation rides it.
"""

import sys
import threading

import annotated2ir
import check_grammar_coverage
import check_interpreter
import check_star
import gate
import normalize

# The recursive helpers a transformed grammar carries recurse as deep as their input is long, past both Python's limit
# and a default stack. So the check runs on a thread given a large one, with the limit raised to match — deep enough for
# any real input, where `interpreter.DEPTH_LIMIT` is the cap that refuses a runaway with a trace before either is hit.
STACK_BYTES = 256 * 1024 * 1024
RECURSION_LIMIT = 200000


def _corpus_errors(label, grammar, fixtures, suite):
    """The cases `grammar` does not reproduce, named for the step that produced it."""
    errors = [f"[{label}] fixture {error}" for error in check_interpreter.reproduced(grammar, fixtures)]
    return errors + [f"[{label}] star {error}" for error in check_star.disagreements(grammar, suite)]


def _check():
    fixtures = check_interpreter.spec_tests.load()
    suite = check_star.cases()
    stages = normalize.stages(annotated2ir.load())

    errors = _corpus_errors(*stages[-1], fixtures, suite)
    if errors:  # something broke the stream; walk the steps to name the first one that did
        for label, grammar in stages:
            named = _corpus_errors(label, grammar, fixtures, suite)
            if named:
                errors = named
                break
    final = stages[-1][1]
    deterministic = normalize.deterministic_productions(final)
    if not errors:  # the hybrid run is judged only where the backtracking one stands, so a fault names its mode
        errors += [
            f"[deterministic] fixture {error}"
            for error in check_interpreter.reproduced(final, fixtures, deterministic=deterministic)
        ]
        errors += [
            f"[deterministic] star {error}"
            for error in check_star.disagreements(final, suite, deterministic=deterministic)
        ]
    for error in check_grammar_coverage.gaps(stages[-1][1]):
        errors.append(f"[final] coverage {error}")
    # The content-run gate reads the `(token)` scopes lower-tokens dissolves, so it runs on the last grammar that still
    # holds them; lower-tokens leaves the character runs it checks untouched, so the two grammars agree on the answer.
    before_lower_tokens = dict(stages)["lower-star"]
    for offender in normalize.content_run_offenders(before_lower_tokens):
        errors.append(f"[content-runs] {offender}: a long text token is collected one character at a time")
    for fault in normalize.non_char_set_runs(stages[-1][1]):
        errors.append(f"[char-set-runs] {fault}")
    residue = normalize.unshaped_actions(stages[-1][1])

    gate.report(
        errors,
        "normalization fault(s) — a step that changes the grammar's meaning, a content run not matched in bulk, or a "
        "repetition that is not a character-set run",
        f"normalization pipeline: {len(normalize.STEPS)} step(s) preserve {len(fixtures)} fixtures and {len(suite)} "
        f"suite cases — backtracking, and hybrid with {len(deterministic)} production(s) entered committed — every "
        f"long text token matched in bulk by a character-set run",
    )
    print("    " + " -> ".join(name for name, _transform in normalize.STEPS))
    # Not a fault: what the canonical form does not spell yet, printed so the number is watched down to none rather than
    # discovered later. The determinize phase is what resolves each of them.
    print(f"    {len(residue)} action(s) the canonical form does not spell: a leftover scope or a nullable repetition")
    print(f"    {len(normalize.ungated_alternatives(stages[-1][1]))} alternative(s) with no character to go on")
    # The determinize meter: the corpus is parsed with every proved production entered committed, so this is the count
    # of productions still backtracking — driven to none, at which point it becomes a gate.
    print(f"    {len(final) - len(deterministic)} production(s) not yet deterministic")


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
