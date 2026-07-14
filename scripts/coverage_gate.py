#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Enforce the coverage-annotation contract from a gcovr JSON report.

Every executable line that tests do NOT cover must carry a `// UNTESTED` comment.
Conversely, a `// UNTESTED` on a line that IS covered is stale. Either is an
error, reported as `<path>:<line>: message` for editor go-to-line.

Usage: coverage_gate.py <gcovr-json>
"""

import json
import os
import sys

MARKER = "// UNTESTED"


def main():
    if len(sys.argv) != 2:
        print("usage: coverage_gate.py <gcovr-json>", file=sys.stderr)
        return 2

    with open(sys.argv[1], encoding="utf-8") as handle:
        report = json.load(handle)

    # A report covering nothing has nothing to complain about, and would pass — so the contract would evaporate in
    # silence the day gcovr's filters stopped matching. There is always something to cover.
    if not report.get("files"):
        print("coverage gate: the report covers no files at all", file=sys.stderr)
        return 2

    violations = []
    for entry in report["files"]:
        path = entry["file"]
        if not os.path.exists(path):
            print(f"{path}: source file in report not found on disk", file=sys.stderr)
            return 2
        with open(path, encoding="utf-8") as source_handle:
            source = source_handle.readlines()

        # A physical line can appear multiple times (macros/inlining); it counts
        # as covered if any instance ran.
        counts = {}
        for line in entry.get("lines", []):
            number = line.get("line_number")
            if number is None:
                continue
            counts[number] = max(counts.get(number, 0), line.get("count", 0))

        for number, count in sorted(counts.items()):
            if number < 1 or number > len(source):
                continue
            text = source[number - 1]
            is_annotated = MARKER in text
            if count == 0 and not is_annotated:
                violations.append((path, number, f"uncovered line, needs a {MARKER} comment", text))
            elif count > 0 and is_annotated:
                violations.append((path, number, f"stale {MARKER} on a covered line", text))

    # Editor-friendly `<path>:<line>: message` at column 0 (gcc/clang style).
    for path, number, reason, text in violations:
        print(f"{path}:{number}: {reason}: {text.strip()}")

    if violations:
        print(f"coverage gate FAILED: {len(violations)} violation(s)", file=sys.stderr)
        return 1
    print("coverage gate passed: every uncovered line annotated, no stale annotations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
