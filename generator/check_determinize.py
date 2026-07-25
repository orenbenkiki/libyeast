# SPDX-License-Identifier: MIT
"""
Check the determinizer's analysis on the flow fold — the piece under the transform, held to a known decision.

`determinize.determinize` is the whole cycle, and `check_normalize` holds it to the corpus. This is the unit beneath:
that the analysis, run on the fold conflict in the grammar just before the step, reads the decision the fold is known to
need — hold the break, retype it to `line-fold`. It fixes the expected value rather than comparing to the transform's
own output, which the transform now derives from this same analysis; the corpus is what proves the two agree.
"""

import sys
import threading

import annotated2ir
import determinize
import gate
import normalize

STACK_BYTES = 256 * 1024 * 1024
RECURSION_LIMIT = 200000

# The fold's conflict: the folding break, taken one way as a trimmed empty line and the other as a folded space, on one
# gate. It stands non-deterministic until `determinize` resolves it.
FOLD_CONFLICT = "b-l-folded_c_flow-in"
FOLD_STEP = "speculate-folds"

# What the fold is known to decide: hold the break, and retype it to `line-fold` where content follows, over the whole
# run with no mark. `rest` is `None` — the fold holds no non-break token.
EXPECTED_RETYPE = (None, "line-fold", "all")


def _grammar_before(step_name):
    """The grammar as it stands just before `step_name` runs — the conflict the determinizer is handed."""
    names = [name for name, _transform in normalize.STEPS]
    if step_name not in names:
        raise AssertionError(f"{step_name}: no such pipeline step")
    # stages() opens with the base, so the grammar at a step's index is the one the step before it produced — purged of
    # what the root no longer reaches, exactly as the pipeline hands it on.
    return normalize.stages(annotated2ir.load())[0][names.index(step_name)][1]


def _check():
    before = _grammar_before(FOLD_STEP)
    errors = []

    if not determinize.divergent_holds(before, FOLD_CONFLICT):
        errors.append(f"{FOLD_CONFLICT}: the analysis found no held token where the fold holds the break")

    derived = determinize.derive_retype(before, FOLD_CONFLICT)
    if derived != EXPECTED_RETYPE:
        errors.append(f"{FOLD_CONFLICT}: derived retype {derived} is not the fold's known {EXPECTED_RETYPE}")

    gate.report(
        errors,
        "determinizer analysis gap(s) — a decision the engine does not read where the fold is known to need one",
        f"determinizer: the fold's decision — hold the break, retype it {EXPECTED_RETYPE} — is read off the conflict",
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
