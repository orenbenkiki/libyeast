# SPDX-License-Identifier: MIT
"""
How a gate says what it found.

Every gate reports the same way, so that a failure reads the same wherever it came from — and so that reporting one
cannot be got subtly wrong in one place and right in the other eleven.
"""

import faulthandler
import multiprocessing
import os
import sys
import threading

import ir

# How long one item may go without finishing before it is taken to be stuck. Every item here is a pure question about
# something already built — one invariant over one grammar, one fixture through one interpreter — and the slowest of
# them answers in well under a second. A walk that reaches this prints where every thread stands and stops, which is
# what a silent spin does not.
PATIENCE = 10


def impatient(what, seconds=PATIENCE):
    """
    Arm the watchdog over `what`: once `seconds` have gone by, say what it was on, print where every thread stands, and
    take the whole run down.

    Naming the work is the point — a stack says which walk is spinning and this says which question it was answering,
    and a walk that is instant over one grammar and stuck over another is only told apart by the second.
    """
    patient()

    def fire():
        print(f"stuck on {what} for {seconds}s, and nothing here takes that long:", file=sys.stderr, flush=True)
        faulthandler.dump_traceback()
        os._exit(3)  # noqa: SLF001 — the run is wedged, and a raise here reaches no thread that would act on it

    global _WATCHDOG  # noqa: PLW0603 — one per process, the way the work it watches is one at a time
    _WATCHDOG = threading.Timer(seconds, fire)
    _WATCHDOG.daemon = True
    _WATCHDOG.start()


def patient():
    """Take the watchdog off, whatever it was watching having finished."""
    global _WATCHDOG  # noqa: PLW0603 — one per process, the way the work it watches is one at a time
    if _WATCHDOG is not None:
        _WATCHDOG.cancel()
        _WATCHDOG = None


# The watchdog standing over the work in hand, or none where nothing is being watched.
_WATCHDOG = None


def _one(item):
    """One item's answer, in a worker, and what answering it reached — which the parent folds into its own."""
    work, held, named = _SPREAD
    what = named(item) if named else str(item)
    impatient(what)  # a fork keeps no threads, so a worker arms its own, over each item it takes
    ir.working(what)
    try:
        return work(held, item), ir.what_was_reached()
    finally:
        patient()
        ir.working("")


# What the workers of `spread` read. Set before they are forked, so whatever they answer about — a grammar, a corpus —
# costs nothing to hand over: a forked child is a copy of this process and none of it changes while they run.
_SPREAD = None


def spread(work, held, items, named=None):
    """
    `work(held, item)` for every item, shared out over the cores, in the order the items stand.

    Every gate here asks the same shape of question — one that is a pure function of something already built and one
    item of a list, over hundreds of items. The workers are forked rather than started afresh, so `held` is not sent
    anywhere; only each answer comes back. And forked from whichever thread calls this, so that thread's stack goes with
    them — which is what keeps a check that recurses deeply from running out of stack in a worker.

    What a worker reached comes back with its answer. A question marks the kinds it answered for in the process that ran
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
            # A worker that stops answering — stuck, or gone where its own watchdog took it — leaves the parent waiting
            # on a result that will never come, so the parent watches too, and every answer that arrives puts it back.
            impatient(f"an answer from one of the workers over {len(items)} item(s)")
            for answer, reached in pool.imap(_one, items, chunksize=8):
                impatient(f"an answer from one of the workers over {len(items)} item(s)")
                ir.also_reached(reached)
                answers.append(answer)
    finally:
        patient()
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
