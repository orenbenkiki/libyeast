# SPDX-License-Identifier: MIT
"""
Fold libyeast's token stream up to the YAML Test Suite's events. The check compares them case by case.

The community suite (`third_party/yaml-test-suite/`) states an `in.yaml` per case. The suite also states either the
`test.event` a conformant parser produces or an `error` marker where a parser must reject. libyeast is a token parser.
The check is the deterministic fold `star` defines. A valid case must fold to its events. An error case must come back a
rejection. The match reaches as far as the token layer settles it.

A case libyeast does not agree with is either a bug or a difference we chose. A difference we chose goes into
DIVERGENCES with its reason, and this fails otherwise. A declared case that agrees again is a stale declaration and
fails too. That is the same way `check_vendor_spec` guards its deviations. libyeast follows the suite except where the
spec says otherwise. The spec is libyeast's source of truth.
"""

import os
from collections.abc import Sequence

import annotated2ir
import gate
import ir
import star

# The suite cases libyeast folds differently from the suite, by `<ID>`, and why the difference is the spec's rather than
# a bug. A case not listed must agree. A listed case must differ.
DIVERGENCES = {
    "JEF9/02": (
        "an empty kept block scalar whose input ends in no line break. the spec reads end-of-input as a line break "
        "only in b-chomped-last. an empty scalar does not reach that rule. l-literal-content skips the group where "
        "no content line appears. l-keep-empty's l-empty needs a real b-break, and the input holds none. the spec "
        "therefore folds the scalar to the empty scalar. the suite's one line break comes from YAMLStar appending a "
        "trailing break to the input, and the grammar appends none."
    ),
}


def _disagreement(grammar: dict[str, ir.Prod], directory: str) -> str | None:
    """
    The way libyeast's fold of `<directory>/in.yaml` disagrees with the case, or `None` if it agrees. A valid case must
    fold to its `test.event`. An error case must come back a rejection.
    """
    with open(os.path.join(directory, "in.yaml"), "rb") as handle:
        data = handle.read()
    is_error = os.path.exists(os.path.join(directory, "error"))
    try:
        events = star.run_case(grammar, data)
    except star.Incompatible:  # failure-is-reported: as this case's verdict, a rejection being right for an error case
        return None if is_error else "libyeast rejects a case the suite accepts"
    except Exception as error:  # noqa: BLE001  failure-is-reported: as this function's reason  # pylint: disable=W0718
        return f"crash: {type(error).__name__}: {error}"
    if is_error:
        return "libyeast accepts a case the suite rejects"
    with open(os.path.join(directory, "test.event"), encoding="utf-8") as handle:
        expected = star.parse_events(handle.read())
    folded = "\n".join(str(event) for event in events)
    wanted = "\n".join(str(event) for event in expected)
    return None if folded == wanted else "folds to events the suite does not expect"


def _one_case(held: tuple[dict[str, ir.Prod]], case: str) -> str | None:
    """
    A case folded and held to what the suite says of it. `None` comes back where the fold and the suite agree.

    `held` holds what a run judges a case under, handed to a worker once. It is the grammar to fold the case with.
    """
    (grammar,) = held
    return _disagreement(grammar, os.path.join(star.SUITE, case))


def cases() -> list[str]:
    """The suite's case ids in sorted order. A case id is `<ID>` or `<ID>/<part>`."""
    return gate.cases_under(star.SUITE, "in.yaml")


def disagreements(grammar: dict[str, ir.Prod], suite: Sequence[str] | None = None) -> list[str]:
    """
    The suite cases `grammar` folds differently from the suite and does not declare, as error strings. Empty where the
    grammar agrees green-or-declared. Takes the grammar as an argument. A structurally-transformed grammar folds the
    whole corpus to the same events the base grammar does.
    """
    if suite is None:
        suite = cases()
    ir.say(f"        {len(suite)} suite case(s) spread over the cores")
    found = gate.spread(_one_case, (grammar,), suite)
    errors = []
    for case, disagreement in zip(suite, found):
        if case in DIVERGENCES:
            if disagreement is None:
                errors.append(f"{case}: declared as a divergence, and it agrees with the suite")
        elif disagreement is not None:
            errors.append(f"{case}: {disagreement}")
    for case in sorted(set(DIVERGENCES) - set(suite)):
        errors.append(f"{case}: declared as a divergence, but the suite has no such case")
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
