# SPDX-License-Identifier: MIT
"""
How a gate says what it found.

Every gate reports the same way, so that a failure reads the same wherever it came from — and so that reporting one
cannot be got subtly wrong in one place and right in the other eleven.
"""

import multiprocessing
import sys

import ir

# What the workers of `spread` read. Set before they are forked, so whatever they answer about — a grammar, a corpus —
# costs nothing to hand over: a forked child is a copy of this process and none of it changes while they run.
_SPREAD = None


def _one(item):
    """One item's answer, in a worker, and what answering it reached — which the parent folds into its own."""
    work, held, named = _SPREAD
    ir.working(named(item) if named else "")
    try:
        return work(held, item), ir.what_was_reached()
    finally:
        ir.working("")


def spread(work, held, items, named=None):
    """
    `work(held, item)` for every item, shared out over the cores, in the order the items stand.

    Every gate here asks the same shape of question — one that is a pure function of something already built and one
    item of a list, over hundreds of items. The workers are forked rather than started afresh, so `held` is not sent
    anywhere; only each answer comes back. And forked from whichever thread calls this, so that thread's stack goes with
    them — which is what keeps a check that recurses deeply from running out of stack in a worker.

    What a worker reached comes back with its answer. A reading marks the kinds it answered for in the process that ran
    it, so a kind only a worker ever met would be reported as one nothing reached; folded in here, what was reached is
    reached whoever did it.

    `named` says what an item is, for the lines a worker prints while it is on that one. Without it they say only the
    time, and a dozen workers writing to one stream is a dozen unattributable lines.
    """
    global _SPREAD  # noqa: PLW0603 — what the forked workers read, and there is one of them per process
    _SPREAD = (work, held, named)
    answers = []
    try:
        with multiprocessing.get_context("fork").Pool() as pool:
            for answer, reached in pool.imap(_one, items, chunksize=8):
                ir.also_reached(reached)
                answers.append(answer)
    finally:
        _SPREAD = None
    return answers


def report(errors, noun, summary):
    """
    Print `errors` to standard error and exit non-zero, or print `summary` and return.

    `noun` names what an error is, for the count that follows the list of them.
    """
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} {noun}", file=sys.stderr)
        sys.exit(1)
    print(summary)
