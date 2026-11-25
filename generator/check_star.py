# SPDX-License-Identifier: MIT
"""
Fold libyeast's token stream up to the YAML Test Suite's events. The check compares them case by case.

The community suite (`third_party/yaml-test-suite/`) states an `in.yaml` per case. The suite also states either the
`test.event` a conformant parser produces or an `error` marker where a parser must reject. libyeast is a token parser.
The check is the deterministic fold `star` defines. A valid case must fold to its events. An error case must come back a
rejection. The match reaches as far as the token layer settles it. `star` says what the token layer settles.

A case libyeast does not agree with is either a bug or a difference we chose. A difference we chose goes into
DIVERGENCES with its reason, and this fails otherwise. The check compares a declared case to the events `tests/star/`
holds for that case. A declaration naming no suite case fails. Events in `tests/star/` that no declaration names fail as
well. libyeast follows the suite except where the spec says otherwise. The spec is libyeast's source of truth.
"""

import os
from collections.abc import Sequence

import annotated2ir
import gate
import ir
import star

# The suite cases libyeast folds differently from the suite, by `<ID>`, and why the difference is the spec's rather than
# a bug. A case not listed must agree. A listed case must fold to the events `_DECLARED` holds for it.
DIVERGENCES = {
    "JEF9/02": (
        "an empty kept block scalar whose input ends in no line break. the spec reads end-of-input as a line break "
        "only in b-chomped-last. an empty scalar does not reach that rule. l-literal-content skips the group where "
        "no content line appears. l-keep-empty's l-empty needs a real b-break, and the input holds none. the spec "
        "therefore folds the scalar to the empty scalar. the suite's line break comes from YAMLStar appending a "
        "trailing break to the input, and the grammar appends none."
    ),
}

# The events the spec gives for a case that `DIVERGENCES` declares. The binding lays them out as the suite lays out a
# case.
_DECLARED = os.path.join(gate.TREE, "tests", "star")


def _disagreement(  # pylint: disable=broad-exception-caught
    grammar: dict[str, ir.Prod], directory: str, expected: str
) -> str | None:
    """
    The way libyeast's fold of `<directory>/in.yaml` disagrees with `expected`, or `None` if it agrees. `expected` is a
    directory holding a `test.event` or an `error` marker. A valid case must fold to that `test.event`. An error case
    must come back a rejection.
    """
    with open(os.path.join(directory, "in.yaml"), "rb") as handle:
        data = handle.read()
    is_error = os.path.exists(os.path.join(expected, "error"))
    try:
        events = star.run_case(grammar, data)
    except star.Incompatible:  # failure-is-reported: The rejection is the verdict on the case.
        return None if is_error else f"libyeast rejects a case {expected} holds events for"
    except Exception as error:  # failure-is-reported: The function returns the crash.
        return f"crash: {type(error).__name__}: {error}"
    if is_error:
        return f"libyeast accepts a case {expected} marks as an error"
    with open(os.path.join(expected, "test.event"), encoding="utf-8") as handle:
        held = star.parse_events(handle.read())
    folded = "\n".join(str(event) for event in events)
    wanted = "\n".join(str(event) for event in held)
    return None if folded == wanted else f"folds to events {expected} does not hold"


def _one_case(held: tuple[dict[str, ir.Prod]], case: str) -> str | None:
    """
    Fold a case and compare the fold with what the case expects. `None` comes back where the fold agrees. A case
    `DIVERGENCES` declares expects what `_DECLARED` holds for it. Another case expects what the suite says of it.

    `held` holds the grammar to fold the case with. A worker that `spread` forks inherits `held` once.
    """
    (grammar,) = held
    expected = os.path.join(_DECLARED if case in DIVERGENCES else star.SUITE, case)
    return _disagreement(grammar, os.path.join(star.SUITE, case), expected)


def cases() -> list[str]:
    """The suite's case ids in sorted order. An id is the path from `star.SUITE` to a directory holding `in.yaml`."""
    return gate.cases_under(star.SUITE, "in.yaml")


def disagreements(grammar: dict[str, ir.Prod], suite: Sequence[str] | None = None) -> list[str]:
    """
    Answer an error string for a suite case whose fold differs from the events the case expects. `_DECLARED` holds the
    expected events of a case `DIVERGENCES` declares. An undeclared case expects the suite's events. The answer also
    holds an error for a declared case the suite lacks. It holds an error for events of `_DECLARED` no divergence
    declares. `check_normalize._corpus_errors` holds a structurally-transformed grammar to these same expected events.
    """
    if suite is None:
        suite = cases()
    ir.say(f"        {len(suite)} suite case(s) spread over the cores")
    found = gate.spread(_one_case, (grammar,), suite)
    errors = []
    for case, disagreement in zip(suite, found):
        if disagreement is not None:
            errors.append(f"{case}: {disagreement}")
    for case in sorted(set(DIVERGENCES) - set(suite)):
        errors.append(f"{case}: declared as a divergence, but the suite has no such case")
    for case in sorted(set(gate.cases_under(_DECLARED, "test.event")) - set(DIVERGENCES)):
        errors.append(f"{case}: no divergence declares the events of `tests/star/` for this case")
    return errors


def main() -> None:
    suite = cases()
    errors = disagreements(annotated2ir.load(), suite)
    gate.report(
        errors,
        "case(s) that disagree with the suite and hold no declaration",
        f"YAML Test Suite folded: {len(suite)} cases, {len(DIVERGENCES)} declared divergence(s)",
    )
    for case in sorted(DIVERGENCES):
        print(f"    {case}: {DIVERGENCES[case]}")


if __name__ == "__main__":
    main()
