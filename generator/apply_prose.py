# SPDX-License-Identifier: MIT
"""
Write new prose back into the files a fragment came from.

`collect_fragments` records the sites of a fragment, with the file and the lines a site occupies. This module replaces
those lines. `converge_prose` and `converge_claims` write an accepted rewrite this way.

A rewrite gives a prose per site, in the order the sites read. A site whose new prose matches the tree writes nothing.
That site takes no shaping and no hook.

The shape of the replacement comes off the lines it replaces. A line opening on `//` or `#` keeps that marker and its
indent. A line holding code and a comment keeps the code. A Python docstring keeps its quotes. A markdown paragraph
takes no marker.

A docstring and a markdown paragraph take the new prose as the caller wrote it. This module wraps a comment and a JSON
note at `gate.WIDEST` columns. A comment sharing a single line with code stays on that line. A JSON note written as a
single string stays a single string.

The new text of a file goes through the checkers before it lands. `checkers.refusals_for_edit` reads it as the
write-time hook reads a Write call. The `pc` target of the `Makefile` remains the gate.
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
import prose_rules

# The quotes a Python docstring opens and closes with.
_QUOTES = ('"""', "'''")

# The languages whose formatter rewraps a comment sharing a line with code, as
# https://clang.llvm.org/docs/ClangFormatStyleOptions.html#reflowcomments says of clang-format.
_REFLOWED = ("C",)

# Languages whose comment blocks `make reformat` reflows through `scripts/wrap_long_comments.py`. The `Makefile` also
# runs that script on CMake and YAML files, and this list leaves them out. `_shaped` writes such a block through
# `collect_fragments.comment_reflowed`, as that script does.
_REFLOWED_BLOCKS = ("Python", "Shell", "JS", "Make")

# The letters a Python string may open with in front of its quotes.
# https://docs.python.org/3/reference/lexical_analysis.html#string-and-bytes-literals lists those letters.
_A_PREFIX = re.compile(r"^[rbftuRBFTU]*")


def _indent_of(line: str) -> str:
    """The whitespace `line` opens with."""
    return line[: len(line) - len(line.lstrip())]


def _wrapped(opening: str, said: str, lines: int) -> list[str]:
    """
    A comment sharing a line with code, wrapped the way clang-format wraps it in a C file, as
    https://clang.llvm.org/docs/ClangFormatStyleOptions.html#reflowcomments says.

    The first line keeps the code and the marker. A line below it opens at the marker's column. A comment the file wrote
    on a single line goes back on a single line.
    """
    if lines == 1:
        return [f"{opening}{said}".rstrip()]
    marker = opening.strip().rsplit(" ", 1)[-1] if " " in opening.strip() else opening.strip()
    under = " " * (len(opening) - len(marker) - 1) + marker + " "
    return [line.rstrip() for line in collect_fragments.filled(said, opening, under)] or [opening.rstrip()]


def _shared_prefix(original: str, code: str, path: str) -> str:
    """
    The text in front of a comment that follows `code` on the line `original`. That text holds the code and the marker.
    An empty string where `code` is empty. `collect_fragments` reads `code` off the line at collection, and a line with
    no marker after it raises.
    """
    if not code:
        return ""
    marker = collect_fragments.MARKERS[collect_fragments.language_of(path) or ""]
    found = re.match(rf"\s*{re.escape(marker)}+[!<]?\s?", original[len(code) :])
    if found is None or not original.startswith(code):
        raise ValueError(f"no comment follows the code {code!r} on the line {original!r}")
    return code + found.group(0)


def _json_strings(said: str, room: int) -> list[str]:
    """
    The strings a paragraph of a JSON note takes. A string holds `room` characters at most.

    A word wider than `room` takes its own line. That line runs past the limit.
    """
    return collect_fragments.filled(said, "", width=room) or [""]


def _json_note(original: list[str], said: str) -> list[str]:
    """
    The lines of a JSON note holding `said`.

    A note on a single line with no `[` in it keeps the string shape. The prose goes back as a single string. A note
    whose value is an array keeps the array. A string there takes a line. An empty string sits between a pair of
    paragraphs. The comma of the note goes back where the note had one.

    A note of another shape gets the array form. `apply_prose.does_hold` then answers false for such a note, and the
    caller leaves that note untouched.
    """
    indent = _indent_of(original[0])
    ends = "," if original[-1].rstrip().endswith(",") else ""
    if len(original) == 1 and "[" not in original[0]:
        return [f'{indent}"_": {json.dumps(said)}{ends}']
    body: list[str] = []
    for at, paragraph in enumerate(prose_rules.A_PARAGRAPH_BREAK.split(said)):
        body += [""] if at else []
        body += _json_strings(paragraph, gate.WIDEST - len(indent) - len('  "",'))
    held = [f'{indent}"_": [']
    held += [f"{indent}  {json.dumps(one)}," for one in body[:-1]]
    return [*held, f"{indent}  {json.dumps(body[-1])}", f"{indent}]{ends}"]


def _shaped(original: list[str], said: str, path: str, code: str = "") -> list[str]:
    """
    The lines that replace `original`. They hold `said` in the shape the old lines had.

    A comment keeps its indent. `collect_fragments.comment_reflowed` shapes a `_REFLOWED_BLOCKS` comment that opens on
    its marker. Another comment that opens on its marker keeps that marker. `collect_fragments.filled` fills a line of
    `said` apart from the lines around it. `_wrapped` shapes a comment after code. A Python docstring keeps the quotes
    and any letter in front of them. `_json_note` shapes a JSON note. Any other line goes back as the lines of `said`,
    with no indent and no marker.
    """
    if collect_fragments.language_of(path) == "JSON":
        return _json_note(original, said)
    indent, opens = _indent_of(original[0]), original[0].strip()
    body = said.strip("\n").split("\n")
    marker = collect_fragments.MARKERS.get(collect_fragments.language_of(path) or "")
    if marker:
        run = re.match(rf"^(\s*{re.escape(marker)}+[!<]?\s?)", original[0])
        if run and collect_fragments.language_of(path) in _REFLOWED_BLOCKS:
            block = [f"{indent}{marker} {one}".rstrip() for one in body]
            return collect_fragments.comment_reflowed(block, len(indent), marker)
        if run:
            return [
                line.rstrip()
                for one in body
                for line in collect_fragments.filled(one, run.group(1)) or [run.group(1).rstrip()]
            ]
        shared = _shared_prefix(original[0], code, path)
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

    Public. `converge_prose` and `converge_claims` ask this before they write a fragment.

    A docstring holding a bullet indents the lines under that bullet deeper. `collect_fragments._python_fragments`
    strips only the docstring's own indent from a line, and the deeper indent stays. A caller writes only to a site this
    answers true for.

    A comment sharing a line with code in a language of `_REFLOWED` takes no such test. The formatter rewraps that
    comment, as https://clang.llvm.org/docs/ClangFormatStyleOptions.html#reflowcomments says. `_wrapped` rewraps the
    comment by a rule of its own.

    A JSON note goes through the same round trip. `_json_note` shapes the note, and this answers true where the shape
    comes back the way the file wrote it.
    """
    original = _original(fragment, site)
    if collect_fragments.language_of(site.path) in _REFLOWED and site.code_in_front:
        return True
    now = collect_fragments.located(fragment).sites[fragment.sites.index(site)]
    return _shaped(original, now.as_wrapped, site.path, now.code_in_front) == original


def _checkers() -> Any:
    """The `checkers` module of the hooks. `checkers.refusals_for_edit` runs the checkers prose obeys."""
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    return importlib.import_module("checkers")


def text_refusals(path: str, text: str) -> list[str]:
    """
    The refusals the checkers give for writing `text` into `path`, a path relative to the tree. The checkers read the
    write as the write-time hook reads a Write call.

    Public. `record_run.proposal_refusals` asks this of the pending proposals file with a proposal in it.
    `draft_refusals` asks this of a file a draft moves in.
    """
    edit = collect_fragments.written({"file_path": os.path.join(gate.TREE, path), "content": text})
    held: list[str] = _checkers().refusals_for_edit(edit, path)
    return held


def draft_refusals(fragment: Any, draft: list[str]) -> list[str]:
    """
    The refusals the checkers give for `draft`, the new prose per site of `fragment`. The checkers read a file the draft
    moves in, with the draft written into it.

    Public. `converge_prose` asks this of a critic's answer and again before a tree write. `converge_claims` asks this
    before a tree write. `prose_answer` asks this of the rewrites a `verdicts` answer holds. `update_ledger_and_queue`
    asks this of a recorded change.
    """
    return [found for path, text in _drafted(fragment, draft).items() for found in text_refusals(path, text)]


def is_held(prose_by_site: list[str], old: str) -> bool:
    """
    Whether `old` is non-empty and some site of `prose_by_site` holds it. A change rewrites the text of a single site.

    Public. `applied` raises on a change this refuses. `_is_sound` asks this before it hands a change to `applied`.
    `_placed` and `_not_held` ask it to refuse such a change.
    """
    return bool(old) and any(old in said for said in prose_by_site)


def applied(prose_by_site: list[str], changes: list[dict[str, str]]) -> list[str]:
    """
    The prose per site a rewrite's changes leave.

    A change names the text it replaces. This rewrites the earliest run of that text. The rewrite lands in the site
    holding the text. A change quoting text no site holds raises. A caller wanting to skip such a change asks `is_held`
    first.

    Public. `prose_answer` hands `draft_refusals` the prose that the changes of a `verdicts` answer leave.
    """
    held = list(prose_by_site)
    for one in changes:
        old, new = str(one.get("old", "")), str(one.get("new", ""))
        if not is_held(held, old):
            raise ValueError(f"a change quotes text no site holds: {old[:80]!r}")
        at = next(at for at, said in enumerate(held) if old in said)
        held[at] = held[at].replace(old, new, 1)
    return held


def _replaced(lines: list[str], sites: list[tuple[tuple[int, ...], str, str]], path: str) -> None:
    """
    Put the new prose of `sites` into `lines` of `path`. A site reads as `(the line numbers, the new prose, the code in
    front of the comment)`.

    A later site goes in first. The line numbers of an earlier site then still hold.
    """
    for numbers, said, code in sorted(sites, key=lambda one: -one[0][0]):
        first, last = numbers[0], numbers[-1]
        lines[first - 1 : last] = _shaped(lines[first - 1 : last], said, path, code)


def _placed(fragment: Any, prose_by_site: list[str]) -> dict[str, list[str]]:
    """
    `{a file: its new lines}` for the files the sites of `fragment` move in. A site whose prose holds still stays out.
    """
    by_path: dict[str, list[tuple[tuple[int, ...], str, str]]] = {}
    for site, said in zip(fragment.sites, prose_by_site):
        if said != site.prose:
            numbers = collect_fragments.site_lines(fragment, site)
            by_path.setdefault(site.path, []).append((numbers, said, site.code_in_front))
    held = {}
    for path, sites in by_path.items():
        with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        _replaced(lines, sites, path)
        held[path] = lines
    return held


def _drafted(fragment: Any, prose_by_site: list[str]) -> dict[str, str]:
    """`{a file: its new text}` for the files the sites of `fragment` move in."""
    return {path: "\n".join(lines) for path, lines in _placed(fragment, prose_by_site).items()}


def _swapped_in(path: str, text: str) -> None:
    """
    Put `text` at `path`.

    A reader sees the text before this write or the text after it, as
    https://docs.python.org/3/library/os.html#os.replace says.
    """
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    shutil.copymode(path, temporary)
    os.replace(temporary, path)


def fragment_written(fragment: Any, prose_by_site: list[str]) -> None:
    """
    Write the new prose of `fragment` into the tree. A file takes a write only where a site in it has prose that moves.

    Public. `converge_prose._written_into_tree` and `converge_claims._written_into_tree` call it.
    """
    for path, lines in _placed(fragment, prose_by_site).items():
        _swapped_in(os.path.join(gate.TREE, path), "\n".join(lines))
