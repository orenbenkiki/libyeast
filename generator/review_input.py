# SPDX-License-Identifier: MIT
"""
Prepare a change for review. The lookups that a reviewer would otherwise make by hand.

Writes `code`, `docs` and `fixtures` into a directory. Writes `hunks` and `references` alongside. Prints what it wrote.
A prepared file running past `_MOST_LINES` splits into numbered parts.

`_MANIFEST` names the paths the written files went to, as `{name: [path, ...]}`. A caller reads the manifest rather than
reading the printout back. A reviewer then gets what is on disk.

Usage: `python3 generator/review_input.py <directory>`. The directory defaults to the working directory.
"""

import ast
import collections
import json
import os
import re
import subprocess
import sys

import gate

# The file that holds the paths. It sits beside the prepared files themselves.
_MANIFEST = "manifest.json"

# The length past which a prepared file is split into numbered parts.
_MOST_LINES = 800

# Above this many mentions of a name, the report gives a count instead of naming the mention again.
_MOST_MENTIONS = 200

# A diff shows this many lines on both sides of a change.
_AROUND = 3

# Above how many mentions an ordinary word gets a count instead of its lines.
_FEW_MENTIONS = 150

# A word is worth asking about where the change took it out of a file. The words that a sweep retires are short. `run`
# is such a word.
_A_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Words too common for a surviving mention to say anything. A word this project renames stays out of this list. The
# ordinary look of the word changes nothing.
_TOO_COMMON_TO_SEARCH = frozenset(
    (
        "the that this with which where what when from into their there here have been were will would "
        "should could about after before because between other than they them these those while "
        "and are but for its not now off one out per too was who yes "
        "parse parser grammar production productions rule rules character characters "
        "return returns match matches value values name names line lines write writes "
        "check checks test tests every each only just also more most some none nothing something"
    ).split()
)


def _ran(*command: str) -> str:
    """The standard output of `command`, run at the tree's root."""
    return subprocess.run(command, cwd=gate.TREE, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _searched(*command: str, status_for_no_match: int) -> str:
    """
    The standard output of `command`. `status_for_no_match` is the status the command answers with after matching
    nothing.

    A search that matched nothing and a search that failed both write nothing. Telling the statuses apart keeps a broken
    search from reporting that the tree mentions nothing.
    """
    held = subprocess.run(command, cwd=gate.TREE, capture_output=True, text=True, encoding="utf-8", check=False)
    if held.returncode not in (0, status_for_no_match):
        raise RuntimeError(f"`{' '.join(command)}` failed with status {held.returncode}: {held.stderr.strip()}")
    return held.stdout


# The place `_staged_paths` keeps its answer. The questions below share the list rather than running git again.
_STAGED_PATHS: list[str] = []


def _staged_paths() -> list[str]:
    """The paths the staged change touches."""
    if not _STAGED_PATHS:
        _STAGED_PATHS.extend(line for line in _ran("git", "diff", "--cached", "--name-only").splitlines() if line)
    return _STAGED_PATHS


def _at_head(path: str) -> str | None:
    """`path` as HEAD holds it, or None where HEAD holds no such path."""
    if not _ran("git", "ls-tree", "HEAD", "--", path).strip():
        return None
    return _ran("git", "show", f"HEAD:{path}")


def _unstaged() -> list[str]:
    """
    The changes the working tree holds and the index lacks. A tracked file with edits nobody staged, or a file nobody
    added.

    `hunks` quotes line numbers taken from `git diff --cached` beside the file on disk. The staged numbers agree with
    the on-disk file where the tree is clean. A file that has grown quotes the wrong lines. A file that has shrunk sends
    the read off the end.
    """
    changed = _ran("git", "diff", "--name-only").splitlines()
    untracked = _ran("git", "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(path for path in changed + untracked if path)


def _changed_lines(path: str) -> set[int]:
    """The line numbers of `path` the staged change adds or alters."""
    changed, at = set[int](), None
    for line in _ran("git", "diff", "--cached", "-U0", "--", path).splitlines():
        header = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if header:
            at = int(header.group(1))
            continue
        if at is not None and line.startswith("+") and not line.startswith("+++"):
            changed.add(at)
            at += 1
    return changed


def _holders(source: str) -> dict[str, tuple[int, int, str]]:
    """
    `{what it is called: (first line, last line, its docstring)}` per function and class in `source`.

    The key is the path of definitions holding the name, as `_holders.walk` rather than `walk`. A pair of helpers of the
    same name in different functions are then a pair of entries. `normalize` holds a `move` under more than a single
    function, and a bare name would keep the helper the walk reached last. A HEAD lookup goes by that path too. The walk
    and the HEAD lookup agree on a path but may disagree on a line number.
    """
    tree = ast.parse(source)
    found: dict[str, tuple[int, int, str]] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if gate.is_a_definition(child):
                name = prefix + child.name
                last = child.end_lineno or child.lineno  # a parse gives a definition its end
                found[name] = (child.lineno, last, ast.get_docstring(child) or "")
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _hunks() -> str:
    """
    The changes in a Python file.

    A record gives the changed lines. It gives the function those lines fall in. The record holds that function's
    docstring from the working file. It holds the HEAD docstring beside that. The lines either side come with the
    docstrings. A change outside any function gets a record of its own changed lines. That record holds no docstring.
    This leaves deleted files out.

    The parse splits the source on the newline it counts. `splitlines` splits on more than that, and `star.py` writes
    U+2028 in a string literal. Once rebuilt from those pieces, the literal ran off its line, and the file stopped
    parsing.
    """
    written = []
    for path in _staged_paths():
        if not path.endswith(".py") or not os.path.exists(os.path.join(gate.TREE, path)):
            continue
        with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
            source = handle.read()
        lines = source.split("\n")
        held, was = _holders(source), _holders(_at_head(path) or "")
        changed = _changed_lines(path)
        for name, (first, last, docstring) in sorted(held.items(), key=lambda one: one[1]):
            mine = sorted(at for at in changed if first <= at <= last)
            if not mine:
                continue
            wanted = sorted(
                {at for one in mine for at in range(max(1, one - _AROUND), min(len(lines), one + _AROUND) + 1)}
            )
            body, seen = [], None
            for at in wanted:
                if seen is not None and at != seen + 1:
                    body.append("      ...")
                body.append(f"{'>' if at in mine else ' '}{at:>5} {lines[at - 1]}")
                seen = at
            older = was.get(name, (0, 0, "(this function is new)"))[2]
            written.append(
                f"### {path}:{first} {name}\n"
                f"--- the docstring in this change ---\n{docstring or '(none)'}\n"
                f"--- the docstring AT HEAD ---\n{older or '(none)'}\n"
                f"--- the change. `>` marks a changed line ---\n" + "\n".join(body) + "\n"
            )
        loose = sorted(
            at for at in changed if not any(first <= at <= last for first, last, _docstring in held.values())
        )
        if loose:
            shown = "\n".join(f">{at:>5} {lines[at - 1]}" for at in loose)
            written.append(f"### {path} (outside any function)\n--- the changed lines ---\n{shown}\n")
    return "\n".join(written)


def _took_out(path: str) -> set[str]:
    """
    The identifiers and prose words `path` held at HEAD and holds no more, less those `_TOO_COMMON_TO_SEARCH` names.
    """
    older, newer = _at_head(path), ""
    if older is None:
        return set()
    if os.path.exists(os.path.join(gate.TREE, path)):
        with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
            newer = handle.read()
    return {
        word
        for word in set(_A_WORD.findall(older)) - set(_A_WORD.findall(newer))
        if word.lower() not in _TOO_COMMON_TO_SEARCH
    }


def _is_a_name(word: str) -> bool:
    """Whether `word` is shaped like an identifier rather than like prose, by an underscore or an inner capital."""
    return "_" in word or any(letter.isupper() for letter in word[1:])


def _references() -> str:
    """The names and words the change took out and the tree has stopped binding, with what still mentions them."""
    # How many files each word went out of. A word taken from a single file is somebody rewriting a sentence, and the
    # same word taken from several is a sweep.
    left: collections.Counter[str] = collections.Counter()
    for path in _staged_paths():
        if path.startswith("tests/spec/"):
            continue  # a fixture is data; its words are YAML, not names anything refers to
        left.update(_took_out(path))
    # A name the tree still binds went nowhere. It moved between files, or a line holding it was rewritten. Listing its
    # mentions buries the names that really went. A live class read widely reads like a retired class.
    bound = gate.names_defined_in_modules()
    gone = {word for word, times in left.items() if (_is_a_name(word) or times > 1) and word not in bound}
    written = []
    for word in sorted(gone):
        # The status `git grep` answers where the pattern is nowhere in the tree, which is an answer not a failure.
        found = _searched("git", "grep", "-nI", "--", rf"\b{re.escape(word)}\b", status_for_no_match=1).splitlines()
        found = [one for one in found if not one.startswith("tests/spec/")]
        if not found:
            continue  # gone everywhere is the answer nobody needs to read
        opening = f"### `{word}`\nThe change took that name out of its place. {len(found)} mention(s) remain."
        # An ordinary word in many places is prose rather than a sweep. It gets its count without its lines. Under the
        # cap the count settles nothing. The lines go in. A word standing in a place is the sweep that missed.
        if not _is_a_name(word) and len(found) > _FEW_MENTIONS:
            written.append(f"{opening} That is too many for a sweep to have missed.\n")
            continue
        written.append(
            opening
            + "\n"
            + "\n".join(f"  {one}" for one in found[:_MOST_MENTIONS])
            + (f"\n  ... and {len(found) - _MOST_MENTIONS} more" if len(found) > _MOST_MENTIONS else "")
            + "\n"
        )
    head = (
        "The identifiers and swept words this change took out of a file, with what still mentions them. A mention in\n"
        "code is a caller a change may break. A mention in a comment or a document is prose describing what is not\n"
        "there. A word the tree still binds at a module's top level in `generator/` or `scripts/` stays out of this\n"
        "list. So does a word\n"
        f"mentioned only inside `tests/spec/`. There are {len(gone) - len(written)} of those.\n"
    )
    return head + "\n" + ("\n".join(written) or "(the change removed nothing that anything else mentions)")


# A pair of headings begins a prepared file's records. The heading this module writes, and the header `git` puts before
# a file it diffs. A split falls on such a heading, and no record comes out cut in half.
_STARTS_A_RECORD = ("### ", "diff --git ")


def _written(into: str, name: str, prepared: str) -> list[str]:
    """
    Write `prepared` under `name`, split at the first record boundary past `_MOST_LINES`. Returns the paths.

    The split falls past `_MOST_LINES` rather than before it. A record comes out whole, and a part runs as long as the
    record it is finishing. A part opens on a fresh record.
    """
    lines = prepared.splitlines()
    if len(lines) <= _MOST_LINES:
        path = os.path.join(into, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(prepared + "\n")
        return [path]
    written = []
    # Where there are no records to break on, the count is the boundary.
    has_records = any(line.startswith(_STARTS_A_RECORD) for line in lines)
    part: list[str] = []
    held: list[list[str]] = []
    for line in lines:
        if len(part) >= _MOST_LINES and (line.startswith(_STARTS_A_RECORD) or not has_records):
            held.append(part)
            part = []
        part.append(line)
    held.append(part)
    for at, body in enumerate(held, start=1):
        path = os.path.join(into, f"{name}.{at}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"({name}, part {at} of {len(held)})\n" + "\n".join(body) + "\n")
        written.append(path)
    return written


# The halves of the change itself, by what a reviewer asks about. A question about naming has no use for a changelog,
# and a question about a document's claims has no use for a decoder. This cuts the diff here rather than handing it over
# whole.
#
# `_THE_FIXTURES` names the fixtures instead. `_uncovered` refuses a staged path these slices leave out. Such a path is
# a change that reaches no reviewer.
_THE_CODE = (
    "generator",
    "scripts",
    "src",
    "include",
    "grammar",
    "tests/test_c.c",
    "tests/test_decoder.c",
    "tests/test_parser.c",
    "tests/pkg_consumer.c",
    "tests/hooks.json",
    "Makefile",
    "CMakeLists.txt",
    "conanfile.py",
    "Doxyfile",
    ".pylintrc",
    "mypy.ini",
    ".gitattributes",
    ".mcp.json",
    ".claude",
    ".github",
)
# The other half of the diff. That is the markdown at the root.
_THE_DOCUMENTS = (
    "DESIGN.md",
    "PLAN.md",
    "CHANGELOG.md",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "Skills.md",
)

# The paths named rather than diffed. A fixture is an input and its expected tokens, and a change to the corpus touches
# more fixtures than a diff can hold. A reviewer can act on a status and a path apiece.
_THE_FIXTURES = ("tests/spec",)

# The vendored paths. A change here is somebody else's code arriving, and this project's conventions do not reach it.
# `gate.NOT_OURS` and the `Makefile`'s find prune say the same of it.
_VENDORED = ("third_party/",)


def _code() -> str:
    """The staged change to the code."""
    return _ran("git", "diff", "--cached", "--", *_THE_CODE)


def _docs() -> str:
    """The staged change to the documents."""
    return _ran("git", "diff", "--cached", "--", *_THE_DOCUMENTS)


def _fixtures() -> str:
    """The staged change to the fixtures, as a status and a path apiece rather than as a diff."""
    return _ran("git", "diff", "--cached", "--name-status", "--", *_THE_FIXTURES)


def _uncovered() -> list[str]:
    """
    The staged paths that no prepared file shows. That set should come back empty.

    A slice takes the paths it names. A path the slices leave out reaches no reviewer, and drops out quietly. A vendored
    path reaches nobody on purpose.
    """
    covered = (*_THE_CODE, *_THE_DOCUMENTS, *_THE_FIXTURES)
    return [path for path in _staged_paths() if not path.startswith(covered) and not path.startswith(_VENDORED)]


def main() -> None:
    """
    Write the prepared files into the directory named on the command line, and print the paths.

    Refuses a tree with anything unstaged. Refuses a tree holding a path no prepared file would show. A reviewer has to
    approve the change that lands. A refused run leaves the directory empty. A reader finding an earlier run's files
    there would take them for this change.
    """
    into = sys.argv[1] if len(sys.argv) > 1 else "."
    prepared_by_name = (
        ("code", _code),
        ("docs", _docs),
        ("fixtures", _fixtures),
        ("hunks", _hunks),
        ("references", _references),
    )
    # What a previous run left goes first, and goes whether this run prepares anything or refuses. Only what this module
    # writes, matched by name rather than taken by suffix.
    stale_manifest = os.path.join(into, _MANIFEST)
    if os.path.exists(stale_manifest):
        os.remove(stale_manifest)
    for name, _prepare in prepared_by_name:
        whole = os.path.join(into, f"{name}.txt")
        if os.path.exists(whole):
            os.remove(whole)
        a_part = re.compile(rf"{re.escape(name)}\.[0-9]+\.txt")
        for stale in gate.named_in(into, ".txt"):
            if a_part.fullmatch(stale.name):
                os.remove(stale)
    gate.report(
        [f"{path}: changed and not staged" for path in _unstaged()],
        "unstaged path(s). Stage the whole change. A review reads what will land.",
        "",
    )
    gate.report(
        [f"{path}: staged and shown by no prepared file" for path in _uncovered()],
        "staged path(s) no reviewer would see. Name them in `THE_CODE`, `THE_DOCUMENTS` or `_THE_FIXTURES`.",
        "",
    )
    manifest = {}
    for name, prepare in prepared_by_name:
        manifest[name] = _written(into, name, prepare())
        for path in manifest[name]:
            with open(path, encoding="utf-8") as handle:
                print(f"  {path}: {len(handle.readlines())} lines")
    with open(os.path.join(into, _MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    print(f"  {os.path.join(into, _MANIFEST)}: the file a name went to")


if __name__ == "__main__":
    main()
