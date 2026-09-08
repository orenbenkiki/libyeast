# SPDX-License-Identifier: MIT
"""
Write new prose back into the files a fragment came from.

`collect_fragments` records a site per comment block, with the file and the lines that block occupies. This replaces
those lines. A rewrite the critic answers with lands this way, and so does a sweep over a word the project turned down.

A part answers a site, in the order the sites read. `converge_fragments` hands back a part per site already.

The shape of the replacement comes off the lines it replaces. A line opening on `//` or `#` keeps that marker and its
indent. A line holding code and a comment keeps the code. A Python docstring keeps its quotes. A markdown paragraph
takes no marker.

New prose goes in as the caller wrote it. `make reformat` wraps the comments and the docstrings afterwards.

A part goes through the write-time hooks before it lands. A hook reads a tool call, and this writes the file outright.
`checkers.refusals_for` gives a caller at the keyboard and a caller here the same answer. `make pc` remains the gate.

**Usage:** `python3 generator/apply_prose.py <rewritten.json>`, where the file holds `[{"key": ..., "parts": [...]}]`.
"""

import importlib
import json
import os
import re
import sys

from typing import Any

import collect_fragments
import gate

_USAGE = "usage: apply_prose.py <rewritten.json>"  # a bad call gets it.

# The quotes a Python docstring opens and closes with.
_QUOTES = ('"""', "'''")

# The marker a comment opens with, by the language of the file. A `#` in a C file opens a preprocessor directive. A
# reader taking that `#` for a marker would eat the directive.
_MARKERS = {"C": "//", "JS": "//", "Python": "#", "Shell": "#", "YAML": "#", "Make": "#", "CMake": "#"}

# The column the formatters wrap a source line at. `Makefile` and `.clang-format` set the same limit.
_WIDEST = 120

# The letters a Python string may open with in front of its quotes.
_A_PREFIX = re.compile(r"^[rbfuRBFU]*")


def _indent_of(line: str) -> str:
    """The whitespace `line` opens with."""
    return line[: len(line) - len(line.lstrip())]


def _wrapped(opening: str, said: str, lines: int) -> list[str]:
    """
    A comment sharing a line with code, wrapped the way `make reformat` wraps it.

    The first line keeps the code and the marker. A line below it opens at the marker's column. A comment the file wrote
    on a single line goes back on a single line.
    """
    if lines == 1:
        return [f"{opening}{said}".rstrip()]
    marker = opening.strip().rsplit(" ", 1)[-1] if " " in opening.strip() else opening.strip()
    under = " " * (len(opening) - len(marker) - 1) + marker + " "
    held, room = [], _WIDEST - len(opening)
    taken: list[str] = []
    for word in said.split():
        if taken and len(" ".join(taken + [word])) > room:
            held.append(taken)
            taken, room = [word], _WIDEST - len(under)
        else:
            taken.append(word)
    held.append(taken)
    return [f"{opening if at == 0 else under}{' '.join(run)}".rstrip() for at, run in enumerate(held)]


def _shaped(original: list[str], said: str, path: str) -> list[str]:
    """
    The lines that replace `original`. They hold `said` in the shape the old lines had.

    A comment keeps the marker and the indent it had. A comment sharing a line with code keeps the code in front of it.
    A Python docstring keeps the quotes and any letter in front of them. Any other line takes the indent and no marker.

    A JSON note raises here. Writing prose back over those lines drops the quotes and the comma around it.
    """
    if collect_fragments.language_of(path) == "JSON":
        raise ValueError(f"{path}: a JSON note is rewritten by hand")
    indent, opens = _indent_of(original[0]), original[0].strip()
    body = said.strip("\n").split("\n")
    marker = _MARKERS.get(collect_fragments.language_of(path) or "")
    if marker:
        run = re.match(rf"^(\s*{re.escape(marker)}+[!<]?\s?)", original[0])
        if run:
            return [f"{run.group(1)}{one}".rstrip() for one in body]
        shared = re.match(rf"^(.*\S\s+{re.escape(marker)}+[!<]?\s?)", original[0])
        if shared:
            return _wrapped(shared.group(1), " ".join(body), len(original))
    found = _A_PREFIX.match(opens)
    letters = found.group(0) if found else ""
    quoted = opens[len(letters) :]
    if quoted.startswith(_QUOTES):
        quotes = _QUOTES[0] if quoted.startswith(_QUOTES[0]) else _QUOTES[1]
        if len(original) == 1 and len(body) == 1 and quoted.endswith(quotes) and quoted != quotes:
            return [f"{indent}{letters}{quotes}{body[0]}{quotes}"]
        return [f"{indent}{letters}{quotes}", *[f"{indent}{one}".rstrip() for one in body], f"{indent}{quotes}"]
    return body


def _does_reproduce(site: Any) -> bool:
    """
    Whether shaping a site's own prose gives back the lines it came from.

    A docstring holding a bullet indents the lines under that bullet deeper. `collect_fragments` strips a docstring
    line, and the deeper indent goes with it. Shaping such a site would flatten the bullet. A caller writes only to a
    site this answers true for.
    """
    with open(os.path.join(gate.TREE, site.path), encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    original = lines[site.lines[0] - 1 : site.lines[-1]]
    return _shaped(original, site.prose, site.path) == original


def _refused(site: Any, said: str) -> list[str]:
    """
    The refusals the write-time hooks give for `said` in the place `site` names.

    `checkers` wants the prose and the lines the file would hold. `_shaped` builds those lines. `short_comments` and
    `altitude` read the marker and the indent, and dedented prose hides both from them.
    """
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    checkers = importlib.import_module("checkers")
    with open(os.path.join(gate.TREE, site.path), encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    original = lines[site.lines[0] - 1 : site.lines[-1]]
    return checkers.refusals_for(said, site.path, "\n".join(_shaped(original, said, site.path)))


def _written(path: str, parts: list[tuple[tuple[int, ...], str]]) -> None:
    """Replace the lines of `path` that `parts` names. A later line goes first. The earlier numbers then hold."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    for numbers, said in sorted(parts, key=lambda one: -one[0][0]):
        first, last = numbers[0], numbers[-1]
        lines[first - 1 : last] = _shaped(lines[first - 1 : last], said, path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    """Write the prose the file on the command line names, and print the fragments it landed in."""
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    held = {one.key: one for one in collect_fragments.fragments()}
    by_path: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    landed = []
    for asked in _loaded(sys.argv[1]):
        key, parts = asked["key"], asked["parts"]
        if key not in held:
            print(f"no fragment is keyed {key}", file=sys.stderr)
            sys.exit(1)
        sites = held[key].sites
        if len(parts) != len(sites):
            print(f"{key} answers with {len(parts)} part(s) for {len(sites)} site(s)", file=sys.stderr)
            sys.exit(1)
        for site in sites:
            if not _does_reproduce(site):
                where = f"{site.path}:{site.lines[0]}"
                print(f"the site of {key} at {where} takes a hand edit. this cannot shape it back.", file=sys.stderr)
                sys.exit(1)
        for site, said in zip(sites, parts):
            found = _refused(site, said)
            if found:
                where = f"{site.path}:{site.lines[0]}"
                print(f"the part for {key} at {where} breaks a rule this project holds prose to", file=sys.stderr)
                print("\n\n".join(found), file=sys.stderr)
                sys.exit(1)
        for site, said in zip(sites, parts):
            by_path.setdefault(os.path.join(gate.TREE, site.path), []).append((site.lines, said))
        landed.append(key)
    for path, parts in by_path.items():
        _written(path, parts)
    print(json.dumps({"landed": landed, "files": sorted(by_path)}, indent=2))


if __name__ == "__main__":
    main()
