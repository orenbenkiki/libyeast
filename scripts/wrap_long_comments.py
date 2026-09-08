# SPDX-License-Identifier: MIT
"""
Reflow standalone ``#`` comment blocks to a width. The ``# `` prefix and the block's indentation stay.

Black leaves comments untouched and docformatter reaches docstrings and stops there. This holds a comment to the column
limit, for the comments that are plain prose.

A standalone comment is a line whose first non-space character is ``#``. ``tokenize`` finds it. A ``#`` inside a string
passes for none. An inline comment after code stays in place.

This passes over a shebang, an ``SPDX`` header, and a directive such as ``noqa`` or ``type``. This passes over a block
with structure of its own, such as an extra indentation or a bullet. A ruler counts too.

A block is the run of same-indent standalone comment lines. An empty comment line splits it into paragraphs. This fills
a paragraph greedily to the width.

Run with ``--apply`` to rewrite files in place, or ``--check`` to report the blocks that want a reflow and exit with a
failing status. ``make reformat-py`` applies this, and ``make vet-format-py`` checks the result.
"""

import io
import re
import sys
import textwrap
import tokenize

_WIDTH = 120  # the column a reflow wraps a comment block to.

# The lines a reflow leaves unchanged. A tool reads a directive and wants it on a single line. A ruler is a drawing
# rather than a sentence. Wrapping a ruler would break the drawing.
_DIRECTIVE = re.compile(r"^(SPDX-|noqa|type:|pragma:|pylint:|fmt:|isort:|yapf|mypy:|nopep8|!)")
_RULER = re.compile(r"^[-=*|+~^]{2,}")  # a run of the same mark. A drawing rather than a sentence.


def _body_of(line: str) -> str:
    """
    The comment text after ``#`` and an optional space. A reflow keeps indentation beyond that space, as a signal.
    """
    after_hash = line.lstrip()[1:]
    return after_hash[1:] if after_hash.startswith(" ") else after_hash


def _standalone(source: str, lines: list[str]) -> dict[int, int]:
    """
    Line number to indentation column, over the comments that take a line of their own.
    """
    marks = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            if lines[row - 1][:column].strip() == "":
                marks[row] = column
    return marks


def _is_risky(block: list[str]) -> bool:
    """
    Whether a block has structure a greedy reflow would wreck. A reflow leaves such a block untouched.
    """
    for line in block:
        body = _body_of(line)
        if body.startswith(" ") or _DIRECTIVE.match(body.strip()) or _RULER.match(body.strip()):
            return True
        if body.strip().startswith(("- ", "* ")):
            return True
    return False


def _reflow(block: list[str], indent: int) -> list[str]:
    """
    The block rewritten, with a prose paragraph filled greedily to the width under the ``# `` prefix.

    An empty comment line splits a block into paragraphs. A paragraph with structure of its own stays verbatim. That is
    a ruler or a directive. An indent or a bullet counts too. The reflow still fills the plain-prose paragraphs around
    it.
    """
    prefix = " " * indent + "# "
    out: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        if _is_risky(paragraph):
            out.extend(paragraph)
        else:
            text = " ".join(_body_of(line).strip() for line in paragraph)
            for wrapped in textwrap.wrap(text, _WIDTH - len(prefix), break_long_words=False, break_on_hyphens=False):
                out.append(prefix + wrapped)
        paragraph.clear()

    for line in block:
        if _body_of(line).strip() == "":
            flush()
            out.append(" " * indent + "#")
        else:
            paragraph.append(line)
    flush()
    return out


def _offenders(path: str, is_applying: bool) -> list[int]:
    """
    Reflow the file's comment blocks. Return the starting line of a block that changed. This writes the file back when
    the caller asks.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    lines = source.split("\n")
    marks = _standalone(source, lines)
    result, index, changed = [], 0, []
    while index < len(lines):
        if index + 1 in marks:
            indent, start = marks[index + 1], index + 1
            block = []
            while index < len(lines) and index + 1 in marks and marks[index + 1] == indent:
                block.append(lines[index])
                index += 1
            reflowed = _reflow(block, indent)
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
