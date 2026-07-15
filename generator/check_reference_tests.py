# SPDX-License-Identifier: MIT
"""Check that the vendored reference tests are intact and that libyeast's grammar can run the ones that align.

Every `.input` must pair with an `.output` and every `.output` with an `.input`; every filename must decode to a
production; every parameter value must be well-formed. Then each fixture is sorted against the grammar: one whose
production and parameters the grammar declares is runnable, the rest exercise the reference parser's own internal
productions and are skipped — reported here, by production, so the set of what libyeast does not yet cover is visible
rather than silent.
"""

import os

import annotated2ir
import gate
import reference_tests


def main():
    grammar = annotated2ir.load()
    fixtures = reference_tests.load()

    errors = []

    # Fixture integrity: inputs and outputs pair up exactly, and every name decodes to a production.
    inputs = {os.path.basename(f.input_path)[: -len(".input")] for f in fixtures}
    outputs = {name[: -len(".output")] for name in os.listdir(reference_tests.TESTS_DIR) if name.endswith(".output")}
    for stem in sorted(inputs - outputs):
        errors.append(f"{stem}.input: has no matching .output")
    for stem in sorted(outputs - inputs):
        errors.append(f"{stem}.output: has no matching .input")
    for fixture in fixtures:
        if not fixture.production:
            errors.append(f"{os.path.basename(fixture.input_path)}: no production in the filename")
        reason = reference_tests.bad_value(fixture)
        if reason:
            errors.append(f"{os.path.basename(fixture.input_path)}: {reason}")

    # Grammar alignment: what runs, and what is the reference parser's own and gets skipped.
    runnable = 0
    skipped = {}
    for fixture in fixtures:
        reason = reference_tests.is_runnable(fixture, grammar)
        if reason is None:
            runnable += 1
        else:
            skipped.setdefault(fixture.production, reason)

    gate.report(
        errors,
        "malformed reference fixture(s)",
        f"reference tests: {len(fixtures)} fixtures, {runnable} runnable against the grammar, "
        f"{len(fixtures) - runnable} skipped across {len(skipped)} reference-internal production(s)",
    )
    for production in sorted(skipped):
        print(f"    skip {production}: {skipped[production]}")


if __name__ == "__main__":
    main()
