# SPDX-License-Identifier: MIT
"""Check that libyeast's grammar is still the official grammar.

Erases libyeast's token annotations and indicator productions from `grammar/yeast-spec-1.2.yaml`, and compares what remains,
production by production, against the vendored `yaml-spec-1.2.yaml`. A production that differs is either a mistake or a
departure we chose; a chosen one must be declared in DEVIATIONS, with its reason, or this fails. What libyeast adds
therefore cannot quietly become what libyeast changes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir2spec  # noqa: E402
import annotated2ir  # noqa: E402

VENDORED = "third_party/yaml-grammar/yaml-spec-1.2.yaml"

# Where libyeast departs from the official grammar, and why. A deviation is a decision, not an escape: nothing belongs
# here that could be fixed by correcting the grammar instead.
DEVIATIONS = {
    "c-indentation-indicator": (
        'the official grammar sets m to the string "auto-detect", and then computes n + m — an integer plus a string, '
        "which nothing in it ever redeems. libyeast sets m to <auto-detect-indent>, the marker the official grammar "
        "already uses for the same thing in l+block-sequence and l+block-mapping, so that m is always an indentation"
    ),
    "s-l+block-indented": (
        "the official grammar reads m here and sets it nowhere — its own notes concede that it 'assumes that m is "
        "stored as a state/stack variable and has been set somewhere else'. libyeast sets it, from "
        "<auto-detect-in-line-indent>: the spaces that follow on this line, which is what a compact collection is "
        "indented by"
    ),
}


def main():
    vendored = ir2spec.normalized(annotated2ir.load(VENDORED))
    recovered = ir2spec.official(annotated2ir.load())

    errors = []
    for name in (key for key in vendored if not key.startswith(":")):
        if recovered.get(name) != vendored[name] and name not in DEVIATIONS:
            errors.append(f"{name}: differs from the official grammar, and is not a declared deviation")
            errors.append(f"    official: {vendored[name]!r}")
            errors.append(f"    libyeast: {recovered.get(name)!r}")
    for name in sorted(set(recovered) - set(vendored)):
        if not name.startswith(":"):
            errors.append(f"{name}: libyeast has a production the official grammar does not")
    for name in sorted(DEVIATIONS):
        if name not in vendored:
            errors.append(f"{name}: declared as a deviation, but the official grammar has no such production")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        sys.exit(1)
    productions = sum(1 for key in vendored if not key.startswith(":"))
    print(f"official grammar recovered: {productions} productions, {len(DEVIATIONS)} declared deviation(s)")
    for name in sorted(DEVIATIONS):
        print(f"    {name}: {DEVIATIONS[name]}")


if __name__ == "__main__":
    main()
