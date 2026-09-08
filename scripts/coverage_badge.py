#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Emit a shields.io endpoint-badge JSON from a gcovr `--json-summary` file.

The published badge at `img.shields.io/endpoint?url=.../coverage.json` reads this and shows the line-coverage
percentage. Its colour steps from red at low coverage to bright green at high.
"""

import json
import sys


def _color_for(percent: int) -> str:
    """The shields.io colour for a coverage `percent`."""
    if percent >= 90:
        return "brightgreen"
    if percent >= 75:
        return "green"
    if percent >= 60:
        return "yellowgreen"
    if percent >= 40:
        return "yellow"
    return "red"


def main() -> None:
    summary_path, output_path = sys.argv[1], sys.argv[2]
    with open(summary_path, encoding="utf-8") as summary_file:
        percent = round(json.load(summary_file)["line_percent"])
    badge = {"schemaVersion": 1, "label": "coverage", "message": f"{percent}%", "color": _color_for(percent)}
    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(badge, output_file)


if __name__ == "__main__":
    main()
