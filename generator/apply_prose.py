# SPDX-License-Identifier: MIT
"""
Write new prose back into the files a fragment came from.

`collect_fragments` records the sites of a fragment, with the file and the lines a site occupies. This module replaces
those lines. A rewrite the critic answers with lands this way. A sweep over a word the project turned down lands the
same way.

A rewrite gives a prose per site, in the order the sites read. A site whose new prose matches the tree writes nothing.
That site takes no shaping and no hook.

The shape of the replacement comes off the lines it replaces. A line opening on `//` or `#` keeps that marker and its
indent. A line holding code and a comment keeps the code. A Python docstring keeps its quotes. A markdown paragraph
takes no marker.

New prose goes in as the caller wrote it. `make reformat` wraps the comments and the docstrings afterwards.

The new text of a file goes through the checkers before it lands. `checkers.refusals_for_edit` reads it as the
write-time hook reads a Write call, and a caller at the keyboard gets the same answer. `make pc` remains the gate.

**Usage:** `python3 generator/apply_prose.py <rewritten.json>`. The file holds `[{"key": ..., "prose_by_site": [...]}]`.
An entry may name `changes` in place of `prose_by_site`, and `applied` turns those into the prose per site.
"""

import importlib
import json
import os
import re
import shutil
import sys
import tempfile

from typing import Any

import collect_fragments
import gate

_USAGE = "usage: apply_prose.py <rewritten.json>"  # The script prints this text when a caller gets the arguments wrong.

# The quotes a Python docstring opens and closes with.
_QUOTES = ('"""', "'''")

# The marker a comment opens with, by the language of the file. A `#` in a C file opens a preprocessor directive. A
# reader taking that `#` for a marker would eat the directive.
_MARKERS = {
    "C": "//",
    "JS": "//",
    "Python": "#",
    "Shell": "#",
    "YAML": "#",
    "Make": "#",
    "CMake": "#",
    "Conf": "#",
}

# The column the formatters wrap a source line at. `Makefile` and `.clang-format` set the same limit.
_WIDEST = 120

# The languages whose formatter rewraps a comment sharing a line with code. clang-format does that to a C file.
_REFLOWED = ("C",)

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


def _shared_marker(original: str, path: str) -> str:
    """
    The text in front of a comment that opens after code on its line. That text holds the marker. An empty string where
    the line opens on the marker, or holds no marker at all.
    """
    marker = _MARKERS.get(collect_fragments.language_of(path) or "")
    if not marker or re.match(rf"^(\s*{re.escape(marker)}+[!<]?\s?)", original):
        return ""
    found = re.match(rf"^(.*\S\s+{re.escape(marker)}+[!<]?\s?)", original)
    return found.group(1) if found else ""


def _json_strings(said: str, room: int) -> list[str]:
    """
    The strings a paragraph of a JSON note takes. A string holds `room` characters at most.

    A word wider than `room` takes its own line. That line runs past the limit.
    """
    held: list[str] = []
    taken: list[str] = []
    for word in said.split():
        if taken and len(" ".join(taken + [word])) > room:
            held.append(" ".join(taken))
            taken = [word]
        else:
            taken.append(word)
    return held + [" ".join(taken)]


def _json_note(original: list[str], said: str) -> list[str]:
    """
    The lines of a JSON note holding `said`.

    A note whose value is a string keeps that shape. The prose goes back as a single string. A note whose value is an
    array keeps the array. A string there takes a line. An empty string sits between a pair of paragraphs. The comma of
    the note goes back where the note had one.

    A note of another shape gets the array form. `does_hold` then answers false for it, and the caller leaves that note
    untouched.
    """
    indent = _indent_of(original[0])
    ends = "," if original[-1].rstrip().endswith(",") else ""
    if len(original) == 1 and "[" not in original[0]:
        return [f'{indent}"_": {json.dumps(said)}{ends}']
    body: list[str] = []
    for at, paragraph in enumerate(said.split("\n\n")):
        body += [""] if at else []
        body += _json_strings(paragraph, _WIDEST - len(indent) - len('  "",'))
    held = [f'{indent}"_": [']
    held += [f"{indent}  {json.dumps(one)}," for one in body[:-1]]
    return [*held, f"{indent}  {json.dumps(body[-1])}", f"{indent}]{ends}"]


def _shaped(original: list[str], said: str, path: str) -> list[str]:
    """
    The lines that replace `original`. They hold `said` in the shape the old lines had.

    A comment keeps the marker and the indent it had. A comment sharing a line with code keeps the code in front of it.
    A Python docstring keeps the quotes and any letter in front of them. `_json_note` shapes a JSON note. Any other line
    takes the indent and no marker.
    """
    if collect_fragments.language_of(path) == "JSON":
        return _json_note(original, said)
    indent, opens = _indent_of(original[0]), original[0].strip()
    body = said.strip("\n").split("\n")
    marker = _MARKERS.get(collect_fragments.language_of(path) or "")
    if marker:
        run = re.match(rf"^(\s*{re.escape(marker)}+[!<]?\s?)", original[0])
        if run:
            return [f"{run.group(1)}{one}".rstrip() for one in body]
        shared = _shared_marker(original[0], path)
        if shared:
            return _wrapped(shared, " ".join(body), len(original))
    found = _A_PREFIX.match(opens)
    letters = found.group(0) if found else ""
    quoted = opens[len(letters) :]
    if quoted.startswith(_QUOTES):
        quotes = _QUOTES[0] if quoted.startswith(_QUOTES[0]) else _QUOTES[1]
        if len(original) == 1 and len(body) == 1 and quoted.endswith(quotes) and quoted != quotes:
            return [f"{indent}{letters}{quotes}{body[0]}{quotes}"]
        return [f"{indent}{letters}{quotes}", *[f"{indent}{one}".rstrip() for one in body], f"{indent}{quotes}"]
    return body


def _original(fragment: Any, site: Any) -> list[str]:
    """The lines `site` of `fragment` covers, as the file writes them."""
    with open(os.path.join(gate.TREE, site.path), encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    numbers = collect_fragments.site_lines(fragment, site)
    return lines[numbers[0] - 1 : numbers[-1]]


def does_hold(fragment: Any, site: Any) -> bool:
    """
    Whether shaping a site's own prose gives back the lines it came from.

    Public. `converge_prose` asks this before a critic reads a fragment, and again before it writes the fragment.

    A docstring holding a bullet indents the lines under that bullet deeper. `collect_fragments` strips a docstring
    line, and the deeper indent goes with it. Shaping such a site would flatten the bullet. A caller writes only to a
    site this answers true for.

    A comment sharing a line with code in a language of `_REFLOWED` takes no such test. The formatter rewraps that
    comment. `_wrapped` rewraps the comment by a rule of its own. The formatter and `_wrapped` disagree about where a
    word lands, and the words hold.

    A JSON note goes through the same round trip. `_json_note` shapes the note, and this answers true where the shape
    comes back the way the file wrote it.
    """
    original = _original(fragment, site)
    if collect_fragments.language_of(site.path) in _REFLOWED and _shared_marker(original[0], site.path):
        return True
    return _shaped(original, site.prose, site.path) == original


def _checkers() -> Any:
    """The `checkers` module of the hooks. `checkers.refusals_for_edit` runs the checkers prose obeys."""
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    return importlib.import_module("checkers")


def text_refusals(path: str, text: str) -> list[str]:
    """
    The refusals the checkers give for writing `text` into `path`, a path relative to the tree. The checkers read the
    write as the write-time hook reads a Write call.

    Public. `record_run.proposal_refusals` asks this of the pending proposals file with a proposal in it.
    """
    edit = collect_fragments.written({"file_path": os.path.join(gate.TREE, path), "content": text})
    held: list[str] = _checkers().refusals_for_edit(edit, path)
    return held


def draft_refusals(fragment: Any, draft: list[str]) -> list[str]:
    """
    The refusals the checkers give for `draft`, the new prose per site of `fragment`. The checkers read a file the draft
    moves in, with the draft written into it.

    Public. `converge_prose` asks this of a critic's answer and again before a tree write. `prose_answer` asks this of a
    critic's rewrite.
    """
    return [found for path, text in _drafted(fragment, draft).items() for found in text_refusals(path, text)]


def applied(prose_by_site: list[str], changes: list[dict[str, str]]) -> list[str]:
    """
    The prose per site a rewrite's changes leave.

    A change names the text it replaces. This rewrites the earliest run of that text. The rewrite lands in the site
    holding the text. A change quoting text no site holds rewrites nothing.

    Public. `prose_answer` asks the write-time hooks of the prose a critic's changes leave.
    """
    held = list(prose_by_site)
    for one in changes:
        old, new = str(one.get("old", "")), str(one.get("new", ""))
        for at, said in enumerate(held):
            if old and old in said:
                held[at] = said.replace(old, new, 1)
                break
    return held


def _replaced(lines: list[str], sites: list[tuple[tuple[int, ...], str]], path: str) -> list[tuple[int, int, int]]:
    """
    Put the new prose of `sites` into `lines` of `path`. A site reads as `(the line numbers, the new prose)`.

    The answer holds a run per site, as `(the first line, the lines the site held, the lines it holds now)`. A later
    site goes in first. The line numbers of an earlier site then still hold. The runs come back in that order too.
    """
    runs = []
    for numbers, said in sorted(sites, key=lambda one: -one[0][0]):
        first, last = numbers[0], numbers[-1]
        shaped = _shaped(lines[first - 1 : last], said, path)
        lines[first - 1 : last] = shaped
        runs.append((first, last - first + 1, len(shaped)))
    return runs


def _placed(fragment: Any, prose_by_site: list[str]) -> dict[str, tuple[list[str], list[tuple[int, int, int]]]]:
    """
    `{a file: (its new lines, the runs of _replaced)}` for the files the sites of `fragment` move in. A site whose prose
    holds still stays out.
    """
    by_path: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    for site, said in zip(fragment.sites, prose_by_site):
        if said != site.prose:
            by_path.setdefault(site.path, []).append((collect_fragments.site_lines(fragment, site), said))
    held = {}
    for path, sites in by_path.items():
        with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        runs = _replaced(lines, sites, path)
        held[path] = (lines, runs)
    return held


def _drafted(fragment: Any, prose_by_site: list[str]) -> dict[str, str]:
    """`{a file: its new text}` for the files the sites of `fragment` move in."""
    return {path: "\n".join(lines) for path, (lines, _runs) in _placed(fragment, prose_by_site).items()}


def _swapped_in(path: str, text: str) -> None:
    """
    Put `text` at `path`.

    A reader sees the text before this write or the text after it. Truncating a file and writing it back leaves a
    window. A hook reading a source file in that window cannot parse it.
    """
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    shutil.copymode(path, temporary)
    os.replace(temporary, path)


def fragment_written(fragment: Any, prose_by_site: list[str]) -> dict[str, list[tuple[int, int, int]]]:
    """
    Write the new prose of `fragment` into the tree. A site whose prose holds still takes no write.

    The answer maps a file to the runs `_replaced` gives for it.

    Public. `converge_prose` writes a fragment the comparator changed, and moves the sites below the runs.
    """
    held = {}
    for path, (lines, runs) in _placed(fragment, prose_by_site).items():
        _swapped_in(os.path.join(gate.TREE, path), "\n".join(lines))
        held[path] = runs
    return held


def _written(path: str, sites: list[tuple[tuple[int, ...], str]]) -> None:
    """Write the new prose of `sites` into `path`. `_replaced` says how a site reads."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    _replaced(lines, sites, path)
    _swapped_in(path, "\n".join(lines))


def _loaded(path: str) -> Any:
    """The JSON `path` holds."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    """Write the prose the file on the command line names, and print the keys that landed."""
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    held = {one.key: one for one in collect_fragments.fragments()}
    by_path: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    landed, held_back = [], []
    for asked in _loaded(sys.argv[1]):
        key = asked["key"]
        if key not in held:
            held_back.append(f"{key}: no fragment is keyed that")
            continue
        fragment = held[key]
        sites = fragment.sites
        prose_by_site = (
            asked["prose_by_site"]
            if "prose_by_site" in asked
            else applied([site.prose for site in sites], asked["changes"])
        )
        if len(prose_by_site) != len(sites):
            held_back.append(f"{key}: {len(prose_by_site)} prose(s) against {len(sites)} site(s)")
            continue
        moved = [(site, said) for site, said in zip(sites, prose_by_site) if said != site.prose]
        unshaped = [site for site, _said in moved if not does_hold(fragment, site)]
        if unshaped:
            where = f"{unshaped[0].path}:{collect_fragments.site_lines(fragment, unshaped[0])[0]}"
            held_back.append(f"{key}: the site at {where} takes a hand edit, and this cannot shape it back")
            continue
        found = draft_refusals(fragment, prose_by_site)
        if found:
            held_back.append(f"{key}: the new prose breaks a rule\n" + "\n\n".join(found))
            continue
        for site, said in moved:
            numbers = collect_fragments.site_lines(fragment, site)
            by_path.setdefault(os.path.join(gate.TREE, site.path), []).append((numbers, said))
        landed.append(key)
    for path, sites_of_path in by_path.items():
        _written(path, sites_of_path)
    print(json.dumps({"landed": landed, "files": sorted(by_path), "held_back": held_back}, indent=2))
    if held_back:
        print(f"{len(held_back)} rewrite(s) reached no file. The list above names them.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
