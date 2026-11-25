# SPDX-License-Identifier: MIT
"""
Reflow standalone comment blocks to a width. The comment marker and the indentation of a block stay.

A standalone comment is a line that opens on its comment marker. `collect_fragments.standalone_comments` names such
lines, and `collect_fragments.MARKERS` names the marker.

A block is a run of standalone comment lines at a single indentation. An empty comment line splits a block into
paragraphs. This script fills a paragraph greedily to the width.

An inline comment stays beside its code where the line fits the width. In a Python file and on a Makefile line outside a
recipe, this script moves an overlong inline comment above the code. A moved comment takes the indentation of the code.
A shell script and a JavaScript file keep their inline comments in place. A directive and a comment after a backslash
continuation stay in place as well.

This script skips a shebang and an ``SPDX`` header. This script skips a ruler and a directive such as ``noqa`` or
``type``. `gate.TOOLS` names the tools such a directive addresses. This script also skips a paragraph with structure of
its own, such as an extra indentation or a bullet.

A triple-quoted Python string may hold a ``- `` bullet past the width. This script refills such a bullet to the width
and keeps the hanging indent. A bullet whose lines fit stays as written.

``--apply`` rewrites files in place. ``--check`` reports the blocks, moved comments and bullets that want a change, and
it exits with a failing status on a find. ``make reformat`` applies this script through ``make reformat-py`` and ``make
reformat-make``. ``make vet-format`` checks the files with this script through ``make vet-format-py``. Neither target
runs this script on a C or Markdown file. ``make reformat-c`` and ``make reformat-md`` hand those files to other tools.
"""

import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generator"))

import collect_fragments  # noqa: E402  # pylint: disable=wrong-import-position
import gate  # noqa: E402  # pylint: disable=wrong-import-position


def _inline(source: str, path: str) -> dict[int, int]:
    """
    Line number to the column of the ``#``, over the comments that follow code on their line.

    `collect_fragments.trailing_comments` finds such comments in a Python file and a `Makefile`. `_inline` gives back
    none for a file of another language, such as a shell script or a JavaScript file.
    """
    language = collect_fragments.language_of(path)
    return collect_fragments.trailing_comments(source, language) if language in ("Python", "Make") else {}


def _statement_starts(source: str) -> dict[int, int]:
    """
    The map takes a continuation line of the Python `source` to the line its statement opens on. `tokenize` ends a
    statement at a `NEWLINE` token, as https://docs.python.org/3/library/token.html#token.NEWLINE says.
    """
    starts: dict[int, int] = {}
    opened = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NEWLINE:
            opened = 0
        elif token.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT) and not opened:
            opened = token.start[0]
        if opened:
            for row in range(opened + 1, token.end[0] + 1):
                starts[row] = opened
    return starts


def _hoisted(
    lines: list[str], inline: dict[int, int], starts: dict[int, int] | None = None
) -> tuple[list[str], list[int]]:
    """
    `_hoisted` moves the inline comment of a line past the width onto a line of its own. It returns the new lines. It
    also returns the numbers of the moved lines. `inline` comes from `_inline`. For a Python file `starts` comes from
    `_statement_starts`. `starts` names the line a statement opens on. Any other file gives an empty `starts`. A line
    can open on a closing bracket. The comment of such a line moves above the line `starts` gives. Any other line takes
    its comment above itself. A directive stays in place. A comment that opens with a match of `gate.A_MARK` stays in
    place too. A line after a line ending in a backslash keeps its comment too.

    The moved comment opens a paragraph of its own. An empty comment line goes above the moved comment where a comment
    at the same indent ends the lines above.
    """
    held = starts or {}
    out: list[str] = []
    placed: dict[int, int] = {}
    moved = []
    for at, line in enumerate(lines, 1):
        column = inline.get(at)
        is_continuing = at > 1 and lines[at - 2].endswith("\\")
        body = collect_fragments.comment_body(line[column:], "#").strip() if column is not None else ""
        placed[at] = len(out)
        is_kept = gate.TOOL_DIRECTIVE.match(body) or gate.A_MARK.match(body)
        if column is None or len(line) <= gate.WIDEST or is_continuing or is_kept:
            out.append(line)
            continue
        opening = held.get(at, at) if line.lstrip()[:1] in ")]}" else at
        where = placed[opening]
        first = lines[opening - 1]
        indent = first[: len(first) - len(first.lstrip())]
        above = out[where - 1] if where else ""
        comment = [indent + "# " + body]
        if above.lstrip().startswith("#") and above[: len(above) - len(above.lstrip())] == indent:
            comment.insert(0, indent + "#")
        out[where:where] = comment
        for row, index in placed.items():
            if index >= where:
                placed[row] = index + len(comment)
        out.append(line[:column].rstrip())
        moved.append(at)
    return out, moved


# A bullet line, with the indent before its dash. `_bullets_refilled` tries it only inside a triple-quoted string.
_A_BULLET = re.compile(r"^( *)- \S")


def _string_rows(source: str) -> set[int]:
    """The line numbers a triple-quoted string of the Python `source` spans."""
    rows: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and token.string.lstrip("rRbBuUfF").startswith(('"""', "'''")):
            rows.update(range(token.start[0], token.end[0] + 1))
    return rows


def _bullets_refilled(lines: list[str], rows: set[int]) -> tuple[list[str], list[int]]:
    """
    The lines with a bullet past the width refilled, and the line numbers where the changed bullets open. `rows` comes
    from `_string_rows`. `_A_BULLET` opens a bullet. The refill keeps the dash and the hanging indent.
    """
    out: list[str] = []
    changed = []
    index = 0
    while index < len(lines):
        found = _A_BULLET.match(lines[index]) if index + 1 in rows else None
        if found is None:
            out.append(lines[index])
            index += 1
            continue
        hang = found.group(1) + "  "
        end = index + 1
        while (
            end < len(lines) and end + 1 in rows and lines[end].startswith(hang) and lines[end][len(hang) :][:1] != " "
        ):
            if _A_BULLET.match(lines[end]) or not lines[end].strip():
                break
            end += 1
        bullet = lines[index:end]
        if any(len(line) > gate.WIDEST for line in bullet):
            text = " ".join(line.strip() for line in bullet)[2:]
            out.extend(collect_fragments.filled(text, found.group(1) + "- ", hang))
            changed.append(index + 1)
        else:
            out.extend(bullet)
        index = end
    return out, changed


def _is_past_the_width(line: str, marker: str) -> bool:
    """
    Whether the comment `line` runs past the width at a space a fill could break at. A URL wider than the width takes a
    line of its own.
    """
    return len(line) > gate.WIDEST and " " in collect_fragments.comment_body(line, marker).strip()


def _offenders(path: str, is_applying: bool) -> list[int]:
    """
    Reflow the file's comment blocks. Return the starting lines of blocks the reflow changed or left past the width. Add
    the lines of inline comments `_hoisted` moved. Add the lines of bullets `_bullets_refilled` refilled. Such a bullet
    sits in a triple-quoted Python string. This writes the file back when the caller asks and the returned list is not
    empty.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    refilled: list[int] = []
    if path.endswith(".py"):
        held, refilled = _bullets_refilled(source.split("\n"), _string_rows(source))
        source = "\n".join(held)
    starts = _statement_starts(source) if path.endswith(".py") else {}
    lines, changed = _hoisted(source.split("\n"), _inline(source, path), starts)
    changed = refilled + changed
    source = "\n".join(lines)
    marks = collect_fragments.standalone_comments(source, collect_fragments.language_of(path))
    result, index = [], 0
    while index < len(lines):
        if index + 1 in marks:
            indent, start = marks[index + 1], index + 1
            block = []
            while index < len(lines) and index + 1 in marks and marks[index + 1] == indent:
                block.append(lines[index])
                index += 1
            marker = collect_fragments.MARKERS[collect_fragments.language_of(path) or ""]
            reflowed = collect_fragments.comment_reflowed(block, indent, marker)
            if reflowed != block or any(_is_past_the_width(line, marker) for line in reflowed):
                changed.append(start)
            result.extend(reflowed)
            continue
        result.append(lines[index])
        index += 1
    if is_applying and changed:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(result))
    return changed


def main() -> None:
    arguments = sys.argv[1:]
    is_checking = "--check" in arguments
    is_applying = "--apply" in arguments
    paths = [argument for argument in arguments if not argument.startswith("-")]
    total = 0
    for path in paths:
        changed = _offenders(path, is_applying and not is_checking)
        total += len(changed)
        if is_checking:
            for start in changed:
                print(f"{path}:{start}: the reflow leaves the comment block off {gate.WIDEST} columns")
    if is_checking and total:
        print(f"wrap-long-comments: {total} block(s) not reflowed - run `make reformat`")
        sys.exit(1)
    if is_applying:
        print(f"wrap-long-comments: {total} block(s) reflowed")


if __name__ == "__main__":
    main()
