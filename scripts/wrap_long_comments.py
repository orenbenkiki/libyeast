# SPDX-License-Identifier: MIT
"""
Reflow standalone ``#`` comment blocks to a width, keeping the ``# `` prefix and the block's indentation.

Black leaves comments alone and docformatter only touches docstrings, so nothing else holds a comment to the column
limit or fills it. This does, for the comments that are plain prose. A standalone comment — a line whose first non-space
character is ``#`` — is found through ``tokenize``, so a ``#`` inside a string is never mistaken for one; an inline
comment after code is left where it is. A shebang, an ``SPDX`` header, a directive such as ``noqa`` or ``type``, and any
block that carries its own structure (extra indentation, a bullet, a ruler) are left alone too, since a greedy reflow
would wreck them. A block is the run of same-indent standalone comment lines; an empty comment line splits it into
paragraphs, and each paragraph is filled greedily to the width.

Run with ``--apply`` to rewrite files in place, or ``--check`` to report the blocks that are not reflowed and exit non-
zero. ``make reformat-py`` applies it; ``make vet-format-py`` checks it.
"""

import io
import re
import sys
import textwrap
import tokenize

WIDTH = 120
DIRECTIVE = re.compile(r"^(SPDX-|noqa|type:|pragma:|pylint:|fmt:|isort:|yapf|mypy:|nopep8|!)")
RULER = re.compile(r"^[-=*|+~^]{2,}")


def body_of(line):
    """
    The comment text after ``#`` and one optional space; indentation beyond that space is kept, as a signal.
    """
    after_hash = line.lstrip()[1:]
    return after_hash[1:] if after_hash.startswith(" ") else after_hash


def standalone(source, lines):
    """
    Line number to indentation column, for every comment that stands alone on its line.
    """
    marks = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            if lines[row - 1][:column].strip() == "":
                marks[row] = column
    return marks


def risky(block):
    """
    Whether a block carries structure a greedy reflow would wreck, and so must be left untouched.
    """
    for line in block:
        body = body_of(line)
        if body.startswith(" ") or DIRECTIVE.match(body.strip()) or RULER.match(body.strip()):
            return True
        if body.strip().startswith(("- ", "* ")):
            return True
    return False


def reflow(block, indent):
    """
    The block rewritten, each prose paragraph filled greedily to the width under the ``# `` prefix.

    A block is split into paragraphs on empty comment lines, and a paragraph that carries its own structure — a ruler, a
    directive, an indent, a bullet — is kept verbatim while the plain-prose ones around it are still filled. That is
    what lets a section-header comment sit above a paragraph without the paragraph being left long.
    """
    prefix = " " * indent + "# "
    out, paragraph = [], []

    def flush():
        if not paragraph:
            return
        if risky(paragraph):
            out.extend(paragraph)
        else:
            text = " ".join(body_of(line).strip() for line in paragraph)
            for wrapped in textwrap.wrap(text, WIDTH - len(prefix), break_long_words=False, break_on_hyphens=False):
                out.append(prefix + wrapped)
        paragraph.clear()

    for line in block:
        if body_of(line).strip() == "":
            flush()
            out.append(" " * indent + "#")
        else:
            paragraph.append(line)
    flush()
    return out


def offenders(path, apply):
    """
    Reflow the file's comment blocks; return the starting line of each that changed, writing back if ``apply``.
    """
    source = open(path).read()
    lines = source.split("\n")
    marks = standalone(source, lines)
    result, index, changed = [], 0, []
    while index < len(lines):
        if index + 1 in marks:
            indent, start = marks[index + 1], index + 1
            block = []
            while index < len(lines) and index + 1 in marks and marks[index + 1] == indent:
                block.append(lines[index])
                index += 1
            reflowed = reflow(block, indent)
            if reflowed != block:
                changed.append(start)
            result.extend(reflowed)
            continue
        result.append(lines[index])
        index += 1
    if apply and changed:
        with open(path, "w") as handle:
            handle.write("\n".join(result))
    return changed


def main():
    arguments = sys.argv[1:]
    check = "--check" in arguments
    apply = "--apply" in arguments
    paths = [argument for argument in arguments if not argument.startswith("-")]
    total = 0
    for path in paths:
        changed = offenders(path, apply and not check)
        total += len(changed)
        if check:
            for start in changed:
                print(f"{path}:{start}: comment block is not reflowed to {WIDTH} columns")
    if check and total:
        print(f"wrap-long-comments: {total} block(s) not reflowed — run `make reformat-py`")
        sys.exit(1)
    if apply:
        print(f"wrap-long-comments: {total} block(s) reflowed")


if __name__ == "__main__":
    main()
