# SPDX-License-Identifier: MIT
"""Round-trip check: `spec2grammar` then `grammar2spec` must reproduce the vendored grammar exactly.

Loads the vendored yaml-grammar, translates it to the IR and back, and asserts the regenerated data equals the source
(compared as parsed data, not text). Reports the first differing production and exits non-zero on any mismatch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grammar2spec  # noqa: E402
import spec2grammar  # noqa: E402

import yaml  # noqa: E402


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else spec2grammar.DEFAULT_SPEC
    with open(source) as handle:
        original = yaml.safe_load(handle)
    regenerated = grammar2spec.regenerate(spec2grammar.translate(original))

    if regenerated == original:
        rules = sum(1 for key in original if not key.startswith(":"))
        print(f"grammar round-trip OK: {rules} productions")
        return

    for key in original:
        if original[key] != regenerated.get(key):
            print(f"round-trip mismatch at {key!r}:", file=sys.stderr)
            print(f"  source:      {original[key]!r}", file=sys.stderr)
            print(f"  regenerated: {regenerated.get(key)!r}", file=sys.stderr)
            break
    missing = sorted(set(original) - set(regenerated))
    extra = sorted(set(regenerated) - set(original))
    if missing:
        print(f"  missing keys: {missing[:5]}", file=sys.stderr)
    if extra:
        print(f"  extra keys: {extra[:5]}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
