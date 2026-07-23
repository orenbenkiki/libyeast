# SPDX-License-Identifier: MIT
"""
Check that the provisional-run balance net refuses every misuse of a run.

`normalize.provisional_faults` walks the run's state — closed, open, marked — through the call graph and faults where an
action meets a state it cannot: an open inside a run, a mark outside one or a second mark, a retype or an injection
outside a run, a marked-region retype or a mark injection where no mark was taken, and a commit with no run. The net is
the one thing that keeps a speculation's actions balanced before a corpus ever runs, since each is zero-width to every
other analysis. `check_normalize` runs it on the real grammar and expects none; this runs it on the misuses themselves,
so a rail that stops guarding fails here rather than passing everything.
"""

import gate
import ir
import normalize


def _root(actions):
    """A one-production grammar whose only alternative performs `actions` — a root, since nothing references it."""
    alternative = ir.Alternative(
        gate=ir.Gate(peek=None, guards=()), actions=tuple(actions), first=None, second=None, recover=None
    )
    production = ir.Prod(number=0, name="p", params=(), body=ir.Choice(alternatives=(alternative,)))
    return {"p": production}


def _open():
    return ir.OpenProvisional()


def _mark():
    return ir.MarkProvisional()


def _retype(region):
    return ir.RetypeProvisional(rest=None, breaks="line-feed", region=region)


def _inject(at):
    return ir.InjectBefore(codes=("begin-pair",), at=at)


def _commit():
    return ir.CommitProvisional()


# Each case: a name, the run's actions, and the fault substring expected — or None where the run balances.
CASES = (
    ("balanced with a mark", (_open(), _mark(), _inject("mark"), _retype("before_mark"), _commit()), None),
    ("balanced without a mark", (_open(), _retype("all"), _inject("start"), _commit()), None),
    ("open inside a run", (_open(), _open(), _commit()), "an OpenProvisional inside"),
    ("mark outside a run", (_mark(),), "a MarkProvisional outside"),
    ("a second mark", (_open(), _mark(), _mark(), _commit()), "a second MarkProvisional"),
    ("retype outside a run", (_retype("all"),), "a RetypeProvisional outside"),
    ("marked-region retype with no mark", (_open(), _retype("before_mark"), _commit()), "marked region with no mark"),
    ("inject outside a run", (_inject("start"),), "an InjectBefore outside"),
    ("mark injection with no mark", (_open(), _inject("mark"), _commit()), "at the mark with no mark"),
    ("commit with no run", (_commit(),), "a CommitProvisional with no run"),
)


def _case_is_judged(errors, label, actions, expected):
    """The net faults on `actions` with a message holding `expected`, or passes them clean where `expected` is None."""
    faults = normalize.provisional_faults(_root(actions))
    if expected is None:
        if faults:
            errors.append(f"{label}: a balanced run drew faults {faults}")
    elif not any(expected in fault for fault in faults):
        errors.append(f"{label}: no fault held {expected!r} — got {faults}")


def main():
    errors = []
    for label, actions, expected in CASES:
        _case_is_judged(errors, label, actions, expected)
    gate.report(
        errors,
        "provisional balance net gap(s) — a run misuse the net did not refuse",
        f"provisional balance net: {len(CASES)} run(s) judged, every misuse refused",
    )


if __name__ == "__main__":
    main()
