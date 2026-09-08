# SPDX-License-Identifier: MIT
"""
Check that `annotated2ir` then `ir2annotated` reproduce libyeast's grammar exactly.

Loads `grammar/yeast-spec-1.2.yaml` and translates it to the IR and back. The regenerated data equals the source,
compared as parsed data rather than text. A translation that loses a production fails here. So does a translation that
quietly rewrites a production. Neither waits for what the IR generates afterwards.
"""

import annotated2ir
import gate
import ir2annotated

import yaml


def main() -> None:
    with open(annotated2ir.DEFAULT_GRAMMAR, encoding="utf-8") as handle:
        original = yaml.safe_load(handle)
    regenerated = ir2annotated.regenerate(annotated2ir.translate(original))

    # A production the round-trip lost reads as one that differs, its regenerated side being nothing at all.
    errors = []
    for key in original:
        if original[key] != regenerated.get(key):
            errors.append(f"{key}:\n  source:      {original[key]!r}\n  regenerated: {regenerated.get(key)!r}")
    for key in sorted(set(regenerated) - set(original)):
        errors.append(f"{key}: regenerated, but the source has no such production")

    rules = sum(1 for key in original if not key.startswith(":"))
    gate.report(errors, "production(s) that do not round-trip", f"grammar round-trip OK: {rules} productions")


if __name__ == "__main__":
    main()
