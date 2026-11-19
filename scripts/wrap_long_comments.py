# SPDX-License-Identifier: MIT
"""
Reflow standalone comment blocks to a width. The comment marker and the indentation of a block stay.

A standalone comment is a line that opens on its comment marker. The marker is ``#``, or ``//`` in a JavaScript file.
``tokenize`` finds such a line in a Python file. A ``#`` inside a Python string opens no comment. A standalone comment
in the ``Makefile`` opens at the start of its line.

A block is a run of standalone comment lines at a single indentation. An empty comment line splits a block into
paragraphs. This script fills a paragraph greedily to the width.

An inline comment stays beside its code where the line fits the width. In a Python file and on a Makefile line outside a
recipe, this script moves an overlong inline comment above the code. A moved comment takes the indentation of the code.
A shell script and a JavaScript file keep their inline comments in place. A directive and a comment after a backslash
continuation stay in place as well.

This script skips a shebang and an ``SPDX`` header. This script skips a ruler and a directive such as ``noqa`` or
``type``. This script also skips a paragraph with structure of its own, such as an extra indentation or a bullet.

A triple-quoted Python string may hold a ``- `` bullet past the width. This script refills such a bullet to the width
and keeps the hanging indent. A bullet whose lines fit stays as written.

``--apply`` rewrites files in place. ``--check`` reports the blocks, moved comments and bullets that want a change, and
it exits with a failing status on a find. ``make reformat-py`` applies this script to Python files, and ``make
vet-format-py`` checks them. ``make reformat-sh`` applies this script to shell scripts, and ``make vet-format-sh``
checks them. ``make reformat-js`` applies this script to JavaScript files, and ``make vet-format-js`` checks them.
``make reformat-make`` applies this script to the ``Makefile``.
"""

import io
import os
import re
import sys
import textwrap
import tokenize

_WIDTH = 120  # the column a reflow wraps a comment block to.

# The lines a reflow leaves unchanged. A directive is an instruction to a tool. This reflow skips a directive such as
# `noqa`.
_DIRECTIVE = re.compile(r"^(SPDX-|noqa|type:|pragma:|pylint:|fmt:|isort:|yapf|mypy:|nopep8|!)")
_RULER = re.compile(
    r"^[-=*|+~^]{2,}(\s|$)"
)  # a line opening with two or more marks from `-=*|+~^`, then whitespace or the end. `**bold**` opens no ruler.

# The ``#`` opening a comment on a `Makefile` line. A backslash before a ``#`` escapes it.
_AN_UNESCAPED_HASH = re.compile(r"(?<!\\)#")


def _marker_of(path: str) -> str:
    """
    The mark opening a comment in `path`. A path ending in `.js` opens a comment on ``//``. Another file opens it on
    ``#``.
    """
    return "//" if path.endswith(".js") else "#"


def _body_of(line: str, marker: str = "#") -> str:
    """
    The comment text after `marker` and an optional space. A reflow keeps indentation beyond that space.
    """
    after_marker = line.lstrip()[len(marker) :]
    return after_marker[1:] if after_marker.startswith(" ") else after_marker


def _standalone_of_make(lines: list[str]) -> dict[int, int]:
    """
    Line number to indentation column, over a `Makefile`'s standalone comments.

    Such a comment opens its line at the left margin. A recipe opens on a tab, and a `#` there belongs to the shell, as
    https://www.gnu.org/software/make/manual/html_node/Recipe-Syntax.html says.
    """
    return {at: 0 for at, line in enumerate(lines, 1) if line.startswith("#")}


# The line opening a heredoc in a shell script. The word after `<<` names the line that ends the body.
_A_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _standalone_of_shell(lines: list[str]) -> dict[int, int]:
    """
    Line number to indentation column, over a shell script's standalone comments.

    Such a comment opens on a `#` after its indentation. The shebang is no comment. A `#` in the body of a heredoc is
    text the heredoc hands over, and this skips the body.
    """
    found = {}
    ends = ""
    for at, line in enumerate(lines, 1):
        if ends:
            if line.strip() == ends:
                ends = ""
            continue
        opened = _A_HEREDOC.search(line)
        if opened is not None:
            ends = opened.group(2)
        if line.lstrip().startswith("#") and not (at == 1 and line.startswith("#!")):
            found[at] = len(line) - len(line.lstrip())
    return found


def _standalone_of_js(source: str) -> dict[int, int]:
    """
    Line number to indentation column, over a JavaScript file's standalone ``//`` comments.

    A scan follows the quoted strings, the template literals and the ``${`` expressions a template holds. A ``//``
    inside a string or the literal text of a template opens no comment. A block comment takes no line here. The scan
    reads no regular expression literal.
    """
    found: dict[int, int] = {}
    modes = ["code"]  # the open strings, templates, expressions and comments, the innermost last.
    braces: list[int] = []  # the braces open in an expression a template holds, per open expression.
    row, line_start, at = 1, 0, 0
    while at < len(source):
        mark, mode = source[at], modes[-1]
        if mark == "\n":
            row, line_start = row + 1, at + 1
            if mode == "line":
                modes.pop()
            at += 1
        elif mode == "line":
            at += 1
        elif mode == "block":
            if source.startswith("*/", at):
                modes.pop()
                at += 1
            at += 1
        elif mode in ("'", '"', "`") and mark == "\\":
            at += 2
            if source[at - 1] == "\n":
                row, line_start = row + 1, at
        elif mode in ("'", '"'):
            if mark == mode:
                modes.pop()
            at += 1
        elif mode == "`":
            if mark == "`":
                modes.pop()
            elif source.startswith("${", at):
                modes.append("${")
                braces.append(0)
                at += 1
            at += 1
        elif source.startswith("//", at):
            if not source[line_start:at].strip():
                found[row] = at - line_start
            modes.append("line")
            at += 2
        elif source.startswith("/*", at):
            modes.append("block")
            at += 2
        else:
            if mark in ("'", '"', "`"):
                modes.append(mark)
            elif mode == "${" and mark == "{":
                braces[-1] += 1
            elif mode == "${" and mark == "}":
                if braces[-1]:
                    braces[-1] -= 1
                else:
                    modes.pop()
                    braces.pop()
            at += 1
    return found


def _standalone(source: str, lines: list[str], path: str) -> dict[int, int]:
    """
    Line number to indentation column, over the comments that take a line of their own.

    The Python goes through `tokenize`, and only a `COMMENT` token counts. A `Makefile` writes a standalone comment at
    the left margin, and `_standalone_of_make` reads it. `_standalone_of_shell` reads a shell script.
    `_standalone_of_js` reads a JavaScript file.
    """
    if os.path.basename(path) == "Makefile":
        return _standalone_of_make(lines)
    if path.endswith(".sh"):
        return _standalone_of_shell(lines)
    if path.endswith(".js"):
        return _standalone_of_js(source)
    marks = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            if lines[row - 1][:column].strip() == "":
                marks[row] = column
    return marks


def _inline(source: str, lines: list[str], path: str) -> dict[int, int]:
    """
    Line number to the column of the ``#``, over the comments that follow code on their line.

    The Python goes through `tokenize`. A `Makefile` line outside a recipe opens a comment at its first ``#`` that no
    backslash escapes. A recipe opens on a tab, and a ``#`` there belongs to the shell. A shell script gives back none.
    A JavaScript file gives back none either.
    """
    if path.endswith((".sh", ".js")):
        return {}
    if os.path.basename(path) == "Makefile":
        found = {}
        for at, line in enumerate(lines, 1):
            if line.startswith(("\t", "#")):
                continue
            mark = _AN_UNESCAPED_HASH.search(line)
            if mark is not None and line[: mark.start()].strip():
                found[at] = mark.start()
        return found
    marks = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            if lines[row - 1][:column].strip():
                marks[row] = column
    return marks


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
    also returns the numbers of the moved lines. `inline` comes from `_inline`. `starts` comes from `_statement_starts`,
    and it names the line a statement opens on. A line can open on a closing bracket. The comment of such a line moves
    above the line `starts` gives. Any other line takes its comment above itself. A directive stays in place. A line
    that continues the line above keeps its comment too.

    The moved comment opens a paragraph of its own. An empty comment line goes above it where a comment at the same
    indent ends the lines above.
    """
    held = starts or {}
    out: list[str] = []
    placed: dict[int, int] = {}
    moved = []
    for at, line in enumerate(lines, 1):
        column = inline.get(at)
        is_continuing = at > 1 and lines[at - 2].endswith("\\")
        body = _body_of(line[column:]).strip() if column is not None else ""
        placed[at] = len(out)
        if column is None or len(line) <= _WIDTH or is_continuing or _DIRECTIVE.match(body):
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


def _is_risky(block: list[str], marker: str = "#") -> bool:
    """
    Whether a block has structure a greedy reflow would wreck. A reflow keeps such a paragraph verbatim.
    """
    for line in block:
        body = _body_of(line, marker)
        if body.startswith(" ") or _DIRECTIVE.match(body.strip()) or _RULER.match(body.strip()):
            return True
        if body.strip().startswith(("- ", "* ")):
            return True
    return False


def _reflow(block: list[str], indent: int, marker: str = "#") -> list[str]:
    """
    The rewritten block. A reflow fills a prose paragraph greedily to the width under the prefix of the indent, `marker`
    and a space.

    An empty comment line splits a block into paragraphs. The reflow keeps a ruler verbatim. The reflow keeps a
    directive verbatim. The reflow keeps an indented or bulleted paragraph verbatim. The reflow fills the plain-prose
    paragraphs between such paragraphs.
    """
    prefix = " " * indent + marker + " "
    out: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        if _is_risky(paragraph, marker):
            out.extend(paragraph)
        else:
            text = " ".join(_body_of(line, marker).strip() for line in paragraph)
            for wrapped in textwrap.wrap(text, _WIDTH - len(prefix), break_long_words=False, break_on_hyphens=False):
                out.append(prefix + wrapped)
        paragraph.clear()

    for line in block:
        if _body_of(line, marker).strip() == "":
            flush()
            out.append(" " * indent + marker)
        else:
            paragraph.append(line)
    flush()
    return out


# A bullet line inside a triple-quoted string, with the indent before its dash.
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
        if any(len(line) > _WIDTH for line in bullet):
            text = " ".join(line.strip() for line in bullet)[2:]
            wrapped = textwrap.wrap(
                text,
                _WIDTH,
                initial_indent=found.group(1) + "- ",
                subsequent_indent=hang,
                break_long_words=False,
                break_on_hyphens=False,
            )
            out.extend(wrapped)
            changed.append(index + 1)
        else:
            out.extend(bullet)
        index = end
    return out, changed


def _offenders(path: str, is_applying: bool) -> list[int]:
    """
    Reflow the file's comment blocks. Return the starting lines of blocks that changed. Add the lines of inline comments
    `_hoisted` moved. Add the lines of bullets `_bullets_refilled` refilled. Such a bullet sits in a triple-quoted
    Python string. This writes the file back when the caller asks and a line changed.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    refilled: list[int] = []
    if path.endswith(".py"):
        held, refilled = _bullets_refilled(source.split("\n"), _string_rows(source))
        source = "\n".join(held)
    starts = _statement_starts(source) if path.endswith(".py") else {}
    lines, changed = _hoisted(source.split("\n"), _inline(source, source.split("\n"), path), starts)
    changed = refilled + changed
    source = "\n".join(lines)
    marks = _standalone(source, lines, path)
    result, index = [], 0
    while index < len(lines):
        if index + 1 in marks:
            indent, start = marks[index + 1], index + 1
            block = []
            while index < len(lines) and index + 1 in marks and marks[index + 1] == indent:
                block.append(lines[index])
                index += 1
            reflowed = _reflow(block, indent, _marker_of(path))
            if reflowed != block:
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
                print(f"{path}:{start}: the reflow leaves the comment block off {_WIDTH} columns")
    if is_checking and total:
        print(f"wrap-long-comments: {total} block(s) not reflowed - run `make reformat-py`")
        sys.exit(1)
    if is_applying:
        print(f"wrap-long-comments: {total} block(s) reflowed")


if __name__ == "__main__":
    main()
