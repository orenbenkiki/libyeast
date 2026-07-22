# SPDX-License-Identifier: MIT
"""
Fold libyeast's token stream up to the YAML Test Suite's events and check the two agree, case for case.

The community suite (`third_party/yaml-test-suite/`) states, per case, an `in.yaml` and either the `test.event` a
conformant parser produces or an `error` marker where it must reject. libyeast is a token parser, so the check is the
deterministic fold `star` defines: a valid case must fold to its events, an error case must come back a rejection, each
matched as far as the token layer settles them.

A case libyeast does not agree with is either a bug or a difference we chose; a chosen one must be declared in
DIVERGENCES, with its reason, or this fails. A declared case that agrees again is a stale declaration and fails too, the
same way `check_vendor_spec` guards its deviations — libyeast follows the suite except where the spec, its source of
truth, says otherwise.
"""

import os

import annotated2ir
import gate
import star

# The suite cases libyeast folds differently from the suite, each by its `<ID>` and the reason the difference is the
# spec's rather than a bug. A case not listed must agree; a listed one must not.
DIVERGENCES = {
    "JEF9/02": (
        "an empty kept block scalar whose input ends in no line break. The spec reads end-of-input as a line "
        "break only in b-chomped-last, which an all-empty scalar never reaches: with no content line "
        "l-literal-content skips that group, and l-keep-empty's l-empty needs a real b-break the input does not "
        "have. So the spec folds it to the empty scalar; the suite's one line break is YAMLStar appending a "
        "trailing break to the input, which the grammar does not"
    ),
}


def _disagreement(grammar, directory, deterministic=frozenset()):
    """
    How libyeast's fold of `<directory>/in.yaml` disagrees with the case, or `None` if it agrees. A valid case must fold
    to its `test.event`; an error case must come back a rejection.
    """
    with open(os.path.join(directory, "in.yaml"), "rb") as handle:
        data = handle.read()
    is_error = os.path.exists(os.path.join(directory, "error"))
    try:
        events = star.run_case(grammar, data, deterministic=deterministic)
    except star.Incompatible:
        return None if is_error else "libyeast rejects a case the suite accepts"
    except Exception as error:  # noqa: BLE001 — a crash is a disagreement to report, not to abort the gate on
        return f"crash: {type(error).__name__}: {error}"
    if is_error:
        return "libyeast accepts a case the suite rejects"
    with open(os.path.join(directory, "test.event")) as handle:
        expected = star.parse_events(handle.read())
    folded = "\n".join(str(event) for event in events)
    wanted = "\n".join(str(event) for event in expected)
    return None if folded == wanted else "folds to events the suite does not expect"


def cases():
    """The suite's case ids, `<ID>` or `<ID>/<part>`, sorted."""
    return sorted(
        os.path.relpath(root, star.SUITE) for root, _directories, files in os.walk(star.SUITE) if "in.yaml" in files
    )


def disagreements(grammar, suite=None, deterministic=frozenset()):
    """
    The suite cases `grammar` folds differently from the suite and does not declare, as error strings — empty when it
    agrees green-or-declared. Takes the grammar as an argument, so a structurally-transformed grammar folds the whole
    corpus to the same events the base one does. `deterministic` passes through to the interpreter, so a hybrid run is
    held to the same events too.
    """
    if suite is None:
        suite = cases()
    errors = []
    for case in suite:
        disagreement = _disagreement(grammar, os.path.join(star.SUITE, case), deterministic=deterministic)
        if case in DIVERGENCES:
            if disagreement is None:
                errors.append(f"{case}: declared as a divergence, but now agrees with the suite")
        elif disagreement is not None:
            errors.append(f"{case}: {disagreement}")
    for case in sorted(set(DIVERGENCES) - set(suite)):
        errors.append(f"{case}: declared as a divergence, but the suite has no such case")
    return errors


def main():
    suite = cases()
    errors = disagreements(annotated2ir.load(), suite)
    gate.report(
        errors,
        "case(s) that disagree with the suite and are not declared",
        f"YAML Test Suite folded: {len(suite)} cases, {len(DIVERGENCES)} declared divergence(s)",
    )
    for case in sorted(DIVERGENCES):
        print(f"    {case}: {DIVERGENCES[case]}")


if __name__ == "__main__":
    main()
