# SPDX-License-Identifier: MIT
"""
Check that the determinizer derives the flow fold's decision, and that it is the one the pipeline already commits.

`speculate-folds` is the fold hand-built: a step that opens a provisional run over the break and retypes it to
`line-fold` where content follows. It is held to the whole corpus by `check_normalize`, in both interpreter modes. So it
is the oracle. This runs the determinizer on the same conflict in the grammar as it stands just before that step, and
asserts the decision it derives — hold the break, retype it to the content path's own code — equals the
`RetypeProvisional` the step encodes. The engine reads the decision off the alternatives; the oracle's corpus test is
then the engine's, for this case.
"""

import threading
import sys

import annotated2ir
import determinize
import gate
import ir
import normalize

STACK_BYTES = 256 * 1024 * 1024
RECURSION_LIMIT = 200000

# The fold's conflict: the folding break, taken one way as a trimmed empty line and the other as a folded space, on one
# gate. `normalize.deterministic_productions` leaves it out, and `speculate-folds` is what resolves it.
FOLD_CONFLICT = "b-l-folded_c_flow-in"
FOLD_STEP = "speculate-folds"


def _before_and_after(step_name):
    """The grammar just before `step_name` runs, and just after — the conflict as it stands, and the oracle's output."""
    namer = normalize.Namer()
    grammar = annotated2ir.load()
    before = None
    for name, transform in normalize.STEPS:
        if name == step_name:
            before = grammar
        grammar = transform(grammar, namer)
        if name == step_name:
            return before, grammar
    raise AssertionError(f"{step_name}: no such pipeline step")


def _oracle_retype(after):
    """The `RetypeProvisional` `speculate-folds` emits, as `(rest, breaks, region)` — the decision it commits."""
    for production in after.values():
        if not isinstance(production.body, ir.Choice):
            continue
        for alternative in production.body.alternatives:
            for action in alternative.actions:
                if isinstance(action, ir.RetypeProvisional):
                    return (action.rest, action.breaks, action.region)
    return None


def _check():
    before, after = _before_and_after(FOLD_STEP)
    errors = []

    if not determinize.divergent_holds(before, FOLD_CONFLICT):
        errors.append(f"{FOLD_CONFLICT}: the determinizer found no held token where the fold holds the break")

    # The whole decision, derived: the held break, and the code the content path — the folded way, which accepts where a
    # content line follows — gives it. Nothing here is told to the engine; it walks the conflict and reads it.
    derived = determinize.derive_retype(before, FOLD_CONFLICT)
    oracle = _oracle_retype(after)
    if oracle is None:
        errors.append(f"{FOLD_STEP}: emits no RetypeProvisional to check the derivation against")
    elif derived != oracle:
        errors.append(f"{FOLD_CONFLICT}: derived retype {derived} is not the oracle's {oracle}")

    gate.report(
        errors,
        "determinizer derivation gap(s) — a decision the engine does not read where the pipeline commits one",
        f"determinizer: the fold's decision — hold the break, retype it {oracle} — is the one {FOLD_STEP} commits",
    )


def main():
    sys.setrecursionlimit(RECURSION_LIMIT)
    threading.stack_size(STACK_BYTES)
    status = {}

    def worker():
        try:
            _check()
        except SystemExit as exit:
            status["code"] = exit.code

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    sys.exit(status.get("code", 0))


if __name__ == "__main__":
    main()
