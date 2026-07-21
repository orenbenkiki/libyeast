# SPDX-License-Identifier: MIT
"""
Check that every step of the normalization pipeline preserves the grammar's meaning, step by step.

A structural transformation is only allowed if it changes no token the interpreter emits and no event the fold produces.
So after each step this runs the two nets the base grammar already passes, on that step's grammar: the fixtures — every
`tests/spec` case reproduced token for token (`check_interpreter`) — and the YAML Test Suite, folded to events
green-or-declared (`check_star`). A single divergence fails the gate, naming the step and the case. Coverage runs once
on the final grammar, catching a production a step left unreachable. Token and event identity is the whole proof; the
vendored-spec check is meaningless on a transformed grammar and is not run here.

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


def _check():
    fixtures = check_interpreter.spec_tests.load()
    suite = check_star.cases()
    stages = normalize.stages(annotated2ir.load())

    errors = []
    for label, grammar in stages:
        for error in check_interpreter.reproduced(grammar, fixtures):
            errors.append(f"[{label}] fixture {error}")
        for error in check_star.disagreements(grammar, suite):
            errors.append(f"[{label}] star {error}")
    for error in check_grammar_coverage.gaps(stages[-1][1]):
        errors.append(f"[final] coverage {error}")
    # The content-run gate reads the `(token)` scopes lower-tokens dissolves, so it runs on the last grammar that still
    # holds them; lower-tokens leaves the character runs it checks untouched, so the two grammars agree on the answer.
    before_lower_tokens = dict(stages)["lower-star"]
    for offender in normalize.content_run_offenders(before_lower_tokens):
        errors.append(f"[content-runs] {offender}: a long text token is collected one character at a time")
    for fault in normalize.non_char_set_runs(stages[-1][1]):
        errors.append(f"[char-set-runs] {fault}")

    gate.report(
        errors,
        "normalization fault(s) — a step that changes the grammar's meaning, a content run not matched in bulk, or a "
        "repetition that is not a character-set run",
        f"normalization pipeline: {len(normalize.STEPS)} step(s), {len(fixtures)} fixtures and {len(suite)} suite "
        f"cases preserved across each, every long text token matched in bulk by a character-set run",
    )
    for name, _transform in normalize.STEPS:
        print(f"    {name}: corpus preserved")


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
