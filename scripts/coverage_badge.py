#!/usr/bin/env python3
# Emit a shields.io endpoint-badge JSON from a gcovr --json-summary file. The published badge
# (img.shields.io/endpoint?url=…/coverage.json) reads this to show the line-coverage percentage; the color steps from
# red (low) to bright green (high).
import json
import sys


def color_for(percent):
    if percent >= 90:
        return "brightgreen"
    if percent >= 75:
        return "green"
    if percent >= 60:
        return "yellowgreen"
    if percent >= 40:
        return "yellow"
    return "red"


def main():
    summary_path, output_path = sys.argv[1], sys.argv[2]
    with open(summary_path) as summary_file:
        percent = round(json.load(summary_file)["line_percent"])
    badge = {"schemaVersion": 1, "label": "coverage", "message": f"{percent}%", "color": color_for(percent)}
    with open(output_path, "w") as output_file:
        json.dump(badge, output_file)


if __name__ == "__main__":
    main()
