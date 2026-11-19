# SPDX-License-Identifier: MIT
"""
`review_input.py` makes lookups a reviewer would otherwise make by hand. The script writes prepared files into a
directory. `code`, `docs` and `fixtures` show staged changes. `previous` holds findings of a previous review. `hunks`
and `references` hold the lookups. A prepared file longer than `_MOST_LINES` lines splits into numbered parts. The
script prints the length of a prepared file beside its path.

The file `_MANIFEST` maps a name to the paths of its prepared files, as `{name: [path, ...]}`.

Usage: `python3 generator/review_input.py <directory>`. The directory defaults to the working directory.
"""

import ast
import collections
import functools
import json
import os
import re
import subprocess
import sys

from typing import Any

import gate

# The file that holds the paths. It sits beside the prepared files themselves.
_MANIFEST = "manifest.json"

# The length past which a prepared file is split into numbered parts.
_MOST_LINES = 800

# The report lists at most this many mentions of a word. It counts the mentions past that cap.
_MOST_MENTIONS = 200

# The file into which `main` writes the id of the staged tree and the id of HEAD. `record_run` writes the contents of
# this file to `REVIEWED` with the findings of the review added, then deletes this file.
PREPARED = os.path.join(gate.TREE, ".git", "critic", "review-prepared.json")

# The tree the last review read, beside the HEAD it sat on. `_reviewed` reads this file.
REVIEWED = os.path.join(gate.TREE, ".git", "critic", "reviewed.json")

# The record of a change inside a function shows this many lines on both sides of a changed line.
_AROUND = 3

# Above how many mentions an ordinary word gets a count instead of its lines.
_FEW_MENTIONS = 150

# A word has three or more characters. A word a change took out of a file is worth asking about. `_TOO_COMMON_TO_SEARCH`
# names the exceptions. `parse` is such a word.
_A_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Words too common for a surviving mention to say anything.
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

    A search that matched nothing and a search that failed both write nothing. https://git-scm.com/docs/git-grep gives
    their statuses under `--quiet`. Telling the statuses apart keeps a broken search from reporting that the tree
    mentions nothing.
    """
    held = subprocess.run(command, cwd=gate.TREE, capture_output=True, text=True, encoding="utf-8", check=False)
    if held.returncode not in (0, status_for_no_match):
        raise RuntimeError(f"`{' '.join(command)}` failed with status {held.returncode}: {held.stderr.strip()}")
    return held.stdout


@functools.cache
def _head() -> str:
    """The id of the commit HEAD names."""
    return _ran("git", "rev-parse", "HEAD").strip()


@functools.cache
def _reviewed() -> dict[str, Any]:
    """
    The record `REVIEWED` holds. The record names the tree the last review read and the findings of that review. The
    answer is empty where the file is missing or where the commit the record names differs from HEAD.
    """
    if not os.path.exists(REVIEWED):
        return {}
    with open(REVIEWED, encoding="utf-8") as handle:
        held: dict[str, Any] = json.load(handle)
    return held if held["head"] == _head() else {}


def _base() -> str:
    """The tree the prepared diff starts from. `_reviewed` names that tree. HEAD counts where `_reviewed` is empty."""
    return str(_reviewed().get("tree") or "HEAD")


# The place `_staged_paths` keeps its answer. The questions below share a non-empty list rather than running git again.
_STAGED_PATHS: list[str] = []


def _staged_paths() -> list[str]:
    """The paths where the index differs from the tree `_base` names."""
    if not _STAGED_PATHS:
        _STAGED_PATHS.extend(
            line for line in _ran("git", "diff", "--cached", "--name-only", _base()).splitlines() if line
        )
    return _STAGED_PATHS


def _at_base(path: str) -> str | None:
    """`path` as the tree `_base` names holds it, or None where that tree holds no such path."""
    if not _ran("git", "ls-tree", _base(), "--", path).strip():
        return None
    return _ran("git", "show", f"{_base()}:{path}")


def _unstaged() -> list[str]:
    """
    The paths of the files whose changes the working tree holds and the index lacks. Such a path names a tracked file
    with edits nobody staged, or a file nobody added and no ignore rule covers. https://git-scm.com/docs/git-diff and
    https://git-scm.com/docs/git-ls-files describe the commands that list them.

    `_hunks` quotes line numbers taken from `git diff --cached` beside the file on disk. The staged numbers agree with
    the on-disk file where the tree is clean. A file with unstaged lines added above a change quotes the wrong lines. A
    file cut short of a changed line sends the read past the end of the file.
    """
    changed = _ran("git", "diff", "--name-only").splitlines()
    untracked = _ran("git", "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(path for path in changed + untracked if path)


def _changed_lines(path: str) -> set[int]:
    """The line numbers of `path` that the index adds or alters against `_base`."""
    changed, at = set[int](), None
    for line in _ran("git", "diff", "--cached", "-U0", _base(), "--", path).splitlines():
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
    same name in different functions are then a pair of entries. A bare name would keep the helper the walk reached
    last. A lookup at `_base` goes by that path too. The walk and that lookup agree on a path but may disagree on a line
    number.
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
    r"""
    The staged changes in the Python files.

    A record gives the changed lines. It gives the innermost function or class those lines fall in. The record holds
    that function's docstring from the working file. It holds the docstring at `_base` beside that. The record holds the
    lines either side of the change too. The changes outside any function or class in a file share a single record of
    their changed lines. That record holds no docstring. `_hunks` leaves deleted files out.

    `_hunks` splits the source on `"\n"` to show the lines. `_holders` parses the whole source.
    """
    written = []
    for path in _staged_paths():
        if not path.endswith(".py") or not os.path.exists(os.path.join(gate.TREE, path)):
            continue
        with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
            source = handle.read()
        lines = source.split("\n")
        held, was = _holders(source), _holders(_at_base(path) or "")
        changed = _changed_lines(path)
        width = {name: last - first for name, (first, last, _docstring) in held.items()}
        owner = {
            at: min(
                (name for name, (first, last, _docstring) in held.items() if first <= at <= last), key=width.__getitem__
            )
            for at in changed
            if any(first <= at <= last for first, last, _docstring in held.values())
        }
        for name, (first, last, docstring) in sorted(held.items(), key=lambda one: one[1]):
            mine = sorted(at for at in changed if owner.get(at) == name)
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
                f"--- the docstring AT THE BASE ---\n{older or '(none)'}\n"
                f"--- the change. `>` marks a changed line ---\n" + "\n".join(body) + "\n"
            )
        loose = sorted(at for at in changed if at not in owner)
        if loose:
            shown = "\n".join(f">{at:>5} {lines[at - 1]}" for at in loose)
            written.append(f"### {path} (outside any function)\n--- the changed lines ---\n{shown}\n")
    return "\n".join(written)


def _took_out(path: str) -> set[str]:
    """
    The words of three or more characters that `path` held at `_base` and holds no more, less those whose lowercase form
    `_TOO_COMMON_TO_SEARCH` holds.
    """
    older, newer = _at_base(path), ""
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
    """
    `_references` lists names a change took out of a file. A plain word joins that list unless the change took it out of
    a single file. `_references` drops a name that a module of `generator/` or `scripts/` still defines at its top
    level. The list keeps a word only where something outside `gate.NOT_OURS` still mentions it.
    """
    # How many files each word went out of. A word taken from a single file is somebody rewriting a sentence, and the
    # same word taken from several is a sweep.
    left: collections.Counter[str] = collections.Counter()
    for path in _staged_paths():
        if not path.startswith((*gate.NOT_OURS, *gate.TOOL_STORES)):
            left.update(_took_out(path))
    # A name the tree still binds went nowhere. It moved between files, or a line holding it was rewritten.
    bound = gate.names_defined_in_modules()
    gone = {word for word, times in left.items() if (_is_a_name(word) or times > 1) and word not in bound}
    written, held_back = [], []
    for word in sorted(gone):
        # The status `git grep` answers where the pattern is nowhere in the tree, which is an answer not a failure.
        said = _searched("git", "grep", "-nI", "--", rf"\b{re.escape(word)}\b", status_for_no_match=1).splitlines()
        found = [one for one in said if not one.startswith(gate.NOT_OURS)]
        if said and not found:
            held_back.append(word)
        if not found:
            continue
        opening = f"### `{word}`\nThe change took that name out of its place. {len(found)} mention(s) remain."
        # An ordinary word in more than `_FEW_MENTIONS` places is prose rather than a missed sweep. It gets its count
        # without its lines.
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
        "list. The list takes no word out of a file under `gate.NOT_OURS`. It also leaves out a word mentioned only\n"
        "inside those paths. Those words:\n"
        f"{', '.join(held_back) or '(none)'}.\n"
    )
    return head + "\n" + ("\n".join(written) or "(the change removed nothing that anything else mentions)")


# A record in a prepared file opens with a `### ` heading or a `diff --git ` header. This module writes the `### `
# heading, and `git diff` writes the `diff --git ` header, as
# https://git-scm.com/docs/git-diff#generate_patch_text_with_p says.
_STARTS_A_RECORD = ("### ", "diff --git ")


def _written(into: str, name: str, prepared: str) -> list[str]:
    """
    Write `prepared` under `name` and return the paths. A text longer than `_MOST_LINES` lines goes into numbered parts.
    Text with no record start breaks at `_MOST_LINES` lines.

    Text with records breaks at the first record start past `_MOST_LINES`. A part runs past `_MOST_LINES` to finish its
    last record. A record stays in a single part. A later part opens on a record start. The first part opens on the
    first line of `prepared`.
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


# `_THE_CODE` cuts the diff by the question a reviewer asks. A reviewer asking about naming needs no changelog. A
# reviewer asking about a document's claims needs no decoder.
#
# `_THE_FIXTURES` names the fixtures. `_uncovered` lists the staged paths that no slice, no vendored prefix and no path
# of `gate.TOOL_STORES` holds. `review_input.main` hands such a path to `report`. `report` exits with a failure.
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
    "cmake",
    "conanfile.py",
    "Doxyfile",
    ".pylintrc",
    "mypy.ini",
    ".gitattributes",
    ".mcp.json",
    ".claude",
    ".github",
)
# The part of the diff that `_docs` reads, beside the code and the fixtures.
_THE_DOCUMENTS = (
    "DESIGN.md",
    "PLAN.md",
    "CHANGELOG.md",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "Skills.md",
)

# The paths the review names rather than diffs. `spec_tests.Fixture` pairs an input with its expected tokens.
# `tests/star` holds the events `check_star` compares. A reviewer can act on a status and a path apiece.
_THE_FIXTURES = ("tests/spec", "tests/star")

# The vendored paths. A change here is somebody else's code arriving, and this project's conventions do not reach it.
# `gate.NOT_OURS` and the `Makefile`'s find prune say the same of it.
_VENDORED = ("third_party/",)


def _code() -> str:
    """The diff of the index against `_base`, cut to the paths `_THE_CODE` names. `gate.TOOL_STORES` stays out."""
    return _ran(
        "git", "diff", "--cached", _base(), "--", *_THE_CODE, *(f":(exclude){path}" for path in gate.TOOL_STORES)
    )


def _docs() -> str:
    """The staged change to the documents."""
    return _ran("git", "diff", "--cached", _base(), "--", *_THE_DOCUMENTS)


def _fixtures() -> str:
    """The staged change to the fixtures, as a status and a path apiece rather than as a diff."""
    return _ran("git", "diff", "--cached", "--name-status", _base(), "--", *_THE_FIXTURES)


def _previous() -> str:
    """The findings of the review that read the tree `_base` names, as a JSON line per finding."""
    return "\n".join(json.dumps(one) for one in _reviewed().get("findings") or [])


def _uncovered() -> list[str]:
    """
    The staged paths that no prepared file shows. That set should come back empty.

    A slice takes the paths it names. A path the slices leave out reaches no reviewer, and `main` refuses the tree
    holding it. A vendored path and a path of `gate.TOOL_STORES` reach nobody on purpose.
    """
    covered = (*_THE_CODE, *_THE_DOCUMENTS, *_THE_FIXTURES)
    unread = (*_VENDORED, *gate.TOOL_STORES)
    return [path for path in _staged_paths() if not path.startswith(covered) and not path.startswith(unread)]


def main() -> None:
    """
    Write the prepared files into the directory the command line names. The default is the current directory. Print the
    paths of the prepared files and of the manifest.

    Refuses a tree with anything unstaged. Refuses a tree holding a staged path that no prepared file would show. A
    staged path inside the vendored tree passes. A run deletes the files of an earlier run before it checks the tree.
    """
    into = sys.argv[1] if len(sys.argv) > 1 else "."
    prepared_by_name = (
        ("code", _code),
        ("docs", _docs),
        ("fixtures", _fixtures),
        ("previous", _previous),
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
    os.makedirs(os.path.dirname(PREPARED), exist_ok=True)
    with open(PREPARED, "w", encoding="utf-8") as handle:
        json.dump({"head": _head(), "tree": _ran("git", "write-tree").strip()}, handle)
    print(f"  the diff starts from {_base()}")


if __name__ == "__main__":
    main()
