# SPDX-License-Identifier: MIT
"""
Break the project into fragments.

A fragment pairs prose with the code that prose documents. The code may be empty. A Python class is a fragment. So is a
C function, a Makefile target or a markdown paragraph. A comment block documenting no declaration is a fragment too.

A fragment's extent runs from the first line to the last. The fragment owns those lines, apart from the lines owned by
the fragments inside it. A docstring on an owned line is prose. So is a comment outside a function body. The remaining
lines are code.

A key names the language, the file and the fragment. `Python:ir.py:Question` is a key. An edit to the prose or to the
code leaves the key unchanged. A fragment holds a prose digest and a code digest. `update_ledger_and_queue` compares
them and names the digest that changed.

The prose of a fragment cites other fragments by name. `_resolved` turns a citation into a key.

This module decides `a-file-says-what-it-is`, `a-file-description-comes-first`, `a-piece-of-prose-is-written-once` and
`a-file-of-the-tree-has-a-reader`. A file fragment with no prose is a fault. The block at the top of a file describes
that file, and a lower block describes nothing. `check_conventions` reads the prose collected here and finds a piece of
prose written twice. `language_of` names the reader a file has, and `_unnamed_prose` reports a tracked file with none.

`prose_literals` reads a string literal that says something. `a-refusal-may-write-the-words-it-bans` keeps a hook
refusal out of that reading. `not_prose_lines` reads the `not-prose:` marker a writer puts on a literal holding a
layout.

`CHANGELOG.md` yields no fragments.

The write-time hooks call this module. `a-write-time-hook-reads-the-edit-in-place` is the rule. `written` builds the
file an edit would leave, and `edited` takes that file where it holds prose of this project. A hook then asks of that
file what the gate asks of the tree. `edited` places a comment slice where the file would hold it. The comment marker
need not appear in the slice.

**Usage:** `python3 generator/collect_fragments.py`. The `--json` flag writes the fragments.
"""

import ast
import hashlib
import io
import json
import os
import re
import sys
import tokenize

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

import check_documents
import gate
import prose_rules

# The file that records what a change did rather than the tree's current shape. Its entries stay true as the code moves
# on.
_NOT_ABOUT_THE_TREE = ("CHANGELOG.md",)

# A markdown heading, and a markdown list item opening at column zero.
_A_HEADING = re.compile(r"^(#+)\s+(.*)$", re.M)
_A_TOP_ITEM = re.compile(r"^([-*+]|\d+\.)\s+")  # bulleted or numbered, and nested only by its indent.

# The fence that makes a markdown block code rather than prose.
_A_FENCE = re.compile(r"^\s*```")

# The fence around the YAML a document may open with. An agent definition names itself and its tools there. Those fields
# are data. This module reads no fragment out of them.
_A_FRONT_MATTER = re.compile(r"^---\s*$")

# The lines a file opens with rather than prose. The interpreter line and the licence tag, once their marker is off.
_NOT_PROSE = re.compile(r"^(!|SPDX-[A-Za-z-]+:)")

# The interpreter line and the licence tag, with their marker still on. Those may come above a file's description.
_SITS_ABOVE_IT = re.compile(r"^\s*(#!|(//+|#)\s*SPDX-[A-Za-z-]+:)")

# The quotes a docstring opens with. A prefix letter may come in front of them.
_OPENS_A_STRING = re.compile("^[rbfuRBFU]*('''|\"\"\"|'|\")")
_CLOSES_A_STRING = re.compile("('''|\"\"\"|'|\")$")  # the same quotes, with no prefix in front.

# The shape a name takes where prose cites it. C prose cites a public name by its bare form.
_A_CITED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
# the prefix a public name takes where the prose writes it bare.
_A_CITED_C_NAME = re.compile(r"\b((?:ys|YS)_[A-Za-z0-9_]+)\b")

# The language a file is written in, by the suffix or the name it has.
_LANGUAGES = (
    ((".py",), "Python"),
    ((".c", ".h"), "C"),
    ((".sh",), "Shell"),
    ((".yml", ".yaml"), "YAML"),
    ((".js",), "JS"),
    ((".md",), "Markdown"),
    ((".json",), "JSON"),
    (("Makefile",), "Make"),
    (("CMakeLists.txt",), "CMake"),
    (
        (
            ".clang-format",
            ".clang-format-version",
            ".clang-tidy",
            ".editorconfig",
            ".gitignore",
            "yeast.pc.in",
            "yeastConfig.cmake.in",
        ),
        "Conf",
    ),
)

# The line a `_` key opens on, matched against the whole line.
_A_JSON_NOTE = re.compile(r'^\s*"_"\s*:')

# The text a JSON string holds, with the escapes left as the file writes them.
_A_JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')

# The marker a file's comments use, by the language of the file. JSON has no marker, and `_json_fragments` reads a JSON
# file instead.
_MARKERS = {"C": "//", "Shell": "#", "YAML": "#", "JS": "//", "Make": "#", "CMake": "#", "Conf": "#"}


@dataclass(frozen=True)
class _Text:
    """A half of a fragment, with the digest that says whether it moved."""

    content: str
    sha: str


@dataclass(frozen=True)
class _Site:
    """
    A comment block of a fragment, and the lines of a file it occupies.

    A struct's own comment and the comment on a field count as sites apart. A C function declared in a header and
    defined in a source has a site per file. A rewrite comes back a part per site, and an applier writes a part back
    through the lines its site names.
    """

    path: str
    lines: tuple[int, ...]
    prose: str


@dataclass
class Fragment:
    """A prose and the code it documents, keyed by place and name."""

    key: str
    path: str
    kind: str
    first: int
    prose: _Text
    code: _Text
    sites: tuple[_Site, ...] = ()  # the comment blocks the prose comes from, in the order the prose reads.
    references: dict[str, str] = field(default_factory=dict)


def _digest(content: str) -> str:
    """The digest a half of a fragment is compared by."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _text(lines: Iterable[str]) -> _Text:
    """The text `lines` write, with the blank lines around it trimmed."""
    content = "\n".join(lines).strip("\n")
    return _Text(content, _digest(content))


def language_of(path: str) -> str | None:
    """The language of `path`, or None where no checker here covers it."""
    named = os.path.basename(path)
    for forms, language in _LANGUAGES:
        if path.endswith(forms) or named in forms:
            return language
    return None


def _keyed(language: str, path: str, name: str) -> str:
    """The key a fragment has. A file fragment writes no third part."""
    named = os.path.basename(path)
    return f"{language}:{named}" if not name else f"{language}:{named}:{name}"


def _cited_by(prose: str, language: str) -> set[str]:
    """The names `prose` cites. Backticks in any language. C prose also cites a public name bare."""
    cited = set(_A_CITED_NAME.findall(prose))
    if language == "C":
        cited.update(_A_CITED_C_NAME.findall(prose))
    return cited


def _indent_of(line: str) -> str:
    """The whitespace `line` opens with."""
    return line[: len(line) - len(line.lstrip())]


def _undented(said: str) -> str:
    """
    The text a comment says with the space after its marker off.

    A deeper indent stays. A bullet indents the lines under it. Flattening those lines would lose the bullet.
    """
    return said.removeprefix(" ").rstrip()


def _unmarked(said: str, marker: str) -> str:
    """
    The text a comment line says with its marker off.

    A doxygen C line comment opens on `///` or `//!`, and a member's comment on `//<`. Those go too. A `#` marker keeps
    what follows it. An interpreter line then still writes its `!`.
    """
    body = said[len(marker) :]
    return _undented(body.lstrip("/!<") if marker == "//" else body)


def _comments(source: str, marker: str) -> dict[int, str]:
    """
    `{line: what the comment on it says}` for the lines opening with `marker`.

    A line continuing a trailing comment goes to `_marked_trailing` instead. Reading such a line here would say its
    words twice, and would report the tail of a sentence as a whole one.
    """
    wrapped = _wrapped_trailing(source, marker)
    said = {}
    for at, line in enumerate(source.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith(marker) and at not in wrapped:
            said[at] = _unmarked(stripped, marker)
    return said


def _python_comments(source: str) -> dict[int, str]:
    """`{line: what the comment on it says}`, over the Python comments on lines of their own."""
    lines = source.split("\n")
    return {
        token.start[0]: _undented(token.string.lstrip("#"))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not lines[token.start[0] - 1][: token.start[1]].strip()
    }


def _python_trailing(source: str) -> dict[int, tuple[str, str]]:
    """`{line: (its code, what the comment sharing it says)}`, over the Python comments beside code."""
    lines = source.split("\n")
    held = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        before = lines[token.start[0] - 1][: token.start[1]]
        if token.type == tokenize.COMMENT and before.strip():
            held[token.start[0]] = (before.rstrip(), _undented(token.string.lstrip("#")))
    return held


def _wrapped_trailing(source: str, marker: str) -> set[int]:
    """
    The lines that continue a comment sharing a line with code.

    `make reformat` wraps a long trailing comment onto the line below, and it indents that line to the marker above. The
    line holds no code of its own. Reading it apart cuts a sentence in half, and the halves come back as prose ending on
    no full stop.

    The marker's column tells a wrap from a comment block. A block above a declaration opens at the code's indent.
    """
    held: set[int] = set()
    column = None
    for at, line in enumerate(source.split("\n"), start=1):
        before, found, _said = line.partition(marker)
        if not found:
            column = None
        elif before.strip():
            column = len(before)
        elif column is not None and len(before) == column:
            held.add(at)
        else:
            column = None
    return held


def _marked_trailing(source: str, marker: str) -> dict[int, tuple[str, str, int]]:
    """
    `{line: (its code, what the comment sharing it says, the line the comment ends on)}`, by the marker.

    A wrapped line joins the comment above it. A caller drops what sits on a branch line. A comment on an `#endif` names
    the branch that directive closes rather than saying anything about the branch.
    """
    wrapped = _wrapped_trailing(source, marker)
    held: dict[int, tuple[str, str, int]] = {}
    opened = None
    for at, line in enumerate(source.split("\n"), start=1):
        before, found, said = line.partition(marker)
        if at in wrapped and opened is not None:
            code, was, _last = held[opened]
            held[opened] = (code, f"{was} {_unmarked(marker + said, marker)}".strip(), at)
        elif found and before.strip() and before.count('"') % 2 == 0:
            held[at] = (before.rstrip(), _unmarked(marker + said, marker), at)
            opened = at
        else:
            opened = None
    return held


def _blocks(alone: dict[int, str]) -> list[tuple[int, int, str]]:
    """
    The runs of consecutive comment lines, as `(the line it opens on, the line it ends on, its text)`.

    A comment line saying nothing stays inside the run rather than ending it. A comment block is one prose whatever
    paragraphs it holds. The interpreter line and the licence tag are not prose and do end one.
    """
    held: list[tuple[int, int, str]] = []
    opens = -2
    for at in sorted(alone):
        if _NOT_PROSE.match(alone[at]):
            opens = -2
            continue
        if not alone[at]:
            opens = at
            continue
        if held and at == opens + 1:
            held[-1] = (held[-1][0], at, held[-1][2] + "\n" + alone[at])
        else:
            held.append((at, at, alone[at]))
        opens = at
    return held


def _opens_above(blocks: list[tuple[int, int, str]], line: int, branches: set[int]) -> int:
    """
    The line a thing's prose opens on. That is where the comment block directly above it opens.

    A line in `branches` sits between the block and the thing without parting them.
    """
    at = line - 1
    while at in branches:
        at -= 1
    for first, last, _said in blocks:
        if last == at:
            return first
    return line


def _first_line_of(split: "_Split") -> int:
    """The line a file's description opens on. The licence tag and the interpreter line come above that line."""
    at = 1
    while at <= len(split.lines) and _SITS_ABOVE_IT.match(split.lines[at - 1]):
        at += 1
    return at


def _branch_lines(source: str, marker: str) -> set[int]:
    """The lines choosing between branches, in C or in make."""
    return {
        at
        for at, line in enumerate(source.split("\n"), start=1)
        if _ONLY_A_BRANCH.match(line.partition(marker)[0].strip())
    }


class _Split:
    """The text a file's lines say, and which of them are prose."""

    def __init__(self, source: str, is_prose: set[int]) -> None:
        self.lines = source.split("\n")
        self.is_prose = is_prose
        self.said: dict[int, str] = {}
        self.shared: dict[int, tuple[str, str]] = {}
        self.wrapped: dict[int, int] = {}  # the line a shared comment opens on, by a line that continues it.

    def says(self, at: int, said: str) -> None:
        """Record that the prose on line `at` reads as `said` once its marker is off."""
        self.said[at] = said

    def shares(self, at: int, code: str, said: str, last: int | None = None) -> None:
        """
        Record that line `at` holds `code` and a comment saying `said`.

        A comment wrapping onto the lines below runs to `last`. Those lines say the same comment, and an applier writes
        the whole comment back through the run.
        """
        self.shared[at] = (code, said)
        for line in range(at + 1, (last or at) + 1):
            self.wrapped[line] = at

    def blocks(self, owned: Iterable[int]) -> list[tuple[tuple[int, ...], str]]:
        """
        The prose of `owned`, broken into the comment blocks the file writes. A block reads as `(the lines, what they
        say)`.

        A gap in the line numbers ends a block. A comment sharing a line with code becomes a block of its own. Such a
        comment documents the field on that line. A struct's own comment and the comments on its fields come back apart.
        """
        held: list[tuple[list[int], list[str]]] = []
        opens = -2
        for at in sorted(owned):
            if at in self.wrapped:
                if held and held[-1][0][-1] == at - 1:
                    held[-1][0].append(at)
                opens = at
                continue
            if at in self.shared:
                held.append(([at], [self.shared[at][1]]))
            elif at in self.is_prose:
                said = self.said.get(at, self.lines[at - 1].strip())
                if held and at == opens + 1 and at - 1 not in self.shared:
                    held[-1][0].append(at)
                    held[-1][1].append(said)
                else:
                    held.append(([at], [said]))
            else:
                continue
            opens = at
        return [(tuple(lines), "\n".join(said)) for lines, said in held]

    def halves(self, owned: Iterable[int]) -> tuple[_Text, _Text]:
        """The prose and the code of the lines `owned`, in the order the file writes them."""
        prose, code = [], []
        for at in sorted(owned):
            if at in self.wrapped:
                continue
            if at in self.shared:
                code.append(self.shared[at][0])
                prose.append(self.shared[at][1])
            elif at in self.is_prose:
                prose.append(self.said.get(at, self.lines[at - 1].strip()))
            else:
                code.append(self.lines[at - 1])
        return _text(prose), _text(code)


# The kinds a registration links, as `(what is registered, what registers it)`.
_A_REGISTRATION = (("function", "binding"), ("target", "target"))

# The directives choosing between branches, in C and in make. A comment above such a directive documents what the
# branches declare.
_ONLY_A_BRANCH = re.compile(r"^(#\s*(if|ifdef|ifndef|else|elif|endif)\b|(ifeq|ifneq|ifdef|ifndef|else|endif)\b)")


def _has_prose(split: "_Split", first: int, last: int) -> bool:
    """Whether any line between `first` and `last` is prose."""
    return any(at in split.is_prose or at in split.shared for at in range(first, last + 1))


def _grouped(
    extents: list[tuple[str, str, int, int]], split: "_Split", branches: set[int]
) -> list[tuple[str, str, int, int]]:
    """
    A binding registering the function above it, as a single extent named by both.

    A declaration holds prose of its own. A comment above a run of them documents the first of the run, and a later
    declaration reports as holding nothing.

    A registration is what gets taken in. A line choosing between branches sits between a registration and its function
    without parting the pair.
    """
    held: list[tuple[str, str, int, int]] = []
    for one in sorted(extents, key=lambda held_one: held_one[2]):
        at = _closes_above(held, one[2])
        if at is not None and _does_absorb(held[at], one, split, branches):
            above = held[at]
            held[at] = (above[0], f"{above[1]}, {one[1]}", above[2], one[3])
        else:
            held.append(one)
    return held


def _closes_above(held: list[tuple[str, str, int, int]], first: int) -> int | None:
    """
    The place in `held` of the extent closing last before `first`.

    Extents are sorted by the line they open on. A function holding a nested extent is followed by that nested extent,
    and the last extent appended is then the inner rather than the outer.
    """
    ended = [at for at, one in enumerate(held) if one[3] < first]
    return max(ended, key=lambda at: held[at][3]) if ended else None


def _does_sit_under(last: int, first: int, split: "_Split", branches: set[int]) -> bool:
    """
    Whether the declaration opening on `first` sits under the declaration closing on `last`, over blank lines as well.
    """
    return first > last and all(at in branches or not split.lines[at - 1].strip() for at in range(last + 1, first))


def _does_register(above: tuple[str, str, int, int], one: tuple[str, str, int, int], split: "_Split") -> bool:
    """
    Whether the declaration `one` registers the declaration `above` it.

    `_NO_CHOICE_OF_CHOICES = _Invariant("no-choice-of-choices", _no_choice_of_choices)` is such a binding. The binding
    names the function and writes no value of its own. A docstring on that function already says as much. A make target
    whose single prerequisite is the stamp rule over it is the same shape.

    The naming runs either way. A function whose body reads the binding under it is the wrapper over that binding. The
    docstring of that function documents the pair.
    """
    if (above[0], one[0]) not in _A_REGISTRATION:
        return False
    return _does_name(split, one, above[1]) or _does_name(split, above, one[1])


def _does_name(split: "_Split", extent: tuple[str, str, int, int], named: str) -> bool:
    """Whether the lines of `extent` write `named`. A dotted Python name is looked for by its last part."""
    said = re.escape(named if "/" in named else named.rpartition(".")[2])
    return re.search(rf"(?<![\w./-]){said}(?![\w./-])", "\n".join(split.lines[extent[2] - 1 : extent[3]])) is not None


def _does_absorb(
    above: tuple[str, str, int, int], one: tuple[str, str, int, int], split: "_Split", branches: set[int]
) -> bool:
    """Whether the extent `above` takes in the declaration `one` under it."""
    if _has_prose(split, one[2], one[3]):
        return False
    return _does_register(above, one, split) and _does_sit_under(above[3], one[2], split, branches)


def _own(first: int, last: int, extents: list[tuple[str, str, int, int]]) -> set[int]:
    """The lines an extent owns, less the lines of the extents inside it."""
    owned = set(range(first, last + 1))
    for _kind, _name, opens, closes in extents:
        if (opens, closes) != (first, last) and first <= opens and closes <= last:
            owned -= set(range(opens, closes + 1))
    return owned


def _a_fragment(
    language: str, path: str, kind: str, name: str, first: int, split: _Split, owned: Iterable[int]
) -> Fragment:
    """A fragment, with a pair of halves drawn from the lines the extent owns."""
    owned = sorted(owned)
    prose, code = split.halves(owned)
    sites = tuple(_Site(path, lines, said) for lines, said in split.blocks(owned))
    return Fragment(_keyed(language, path, name), path, kind, first, prose, code, sites)


def _gathered(
    language: str,
    path: str,
    split: _Split,
    extents: list[tuple[str, str, int, int]],
    blocks: list[tuple[int, int, str]],
    branches: set[int],
    bodies: set[int],
) -> list[Fragment]:
    """
    The fragments of a file that declares things. A fragment per name, a fragment per stray comment, and the file
    itself.

    An `#if` may declare a name under a branch and declare it again under the other. Those extents are a single
    fragment. Python has no such branch. A module binding a name twice reports as a colliding key.

    The comment block at the top of the file describes the file. `a-file-description-comes-first` says so. A comment
    block that no extent holds lower down documents no declaration. It is a fragment with no code.

    A block opening inside a function body is code. `a-comment-inside-a-body-is-a-note` says so. Such a block is no
    stray whether or not an extent encloses it.
    """
    extents = _grouped(extents, split, branches)
    taken = {at for _kind, _name, first, last in extents for at in range(first, last + 1)}
    loose = [one for one in blocks if one[0] not in taken and one[0] not in bodies]
    describes_the_file = [one for one in loose[:1] if one[0] == _first_line_of(split)]
    strays = loose[len(describes_the_file) :]
    owned_by: dict[tuple[str, str, int], set[int]] = {}
    for kind, name, first, last in extents:
        under = 0 if language in ("C", "Make") else first
        owned_by.setdefault((kind, name, under), set()).update(_own(first, last, extents))
    held = [
        _a_fragment(language, path, kind, name, min(owned), split, owned)
        for (kind, name, _under), owned in owned_by.items()
    ]
    for first, last, said in strays:
        held.append(_a_fragment(language, path, "stray", f"#{_digest(said)[:8]}", first, split, range(first, last + 1)))
    left = set(range(1, len(split.lines) + 1)) - taken - {at for one in strays for at in range(one[0], one[1] + 1)}
    kind = "module" if language == "Python" else "file"
    held.append(_a_fragment(language, path, kind, "", 1, split, left))
    return held


def _overridden(tree: ast.Module) -> set[str]:
    """
    The method names a class of this module declares with a docstring.

    A method of that name with none is an override. The docstring above is its contract. `pylint` reads an override the
    same way and asks the override for no docstring.
    """
    held: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if gate.is_a_definition(child) and ast.get_docstring(child):
                held.add(child.name)
    return held


def _python_extents(tree: ast.Module, blocks: list[tuple[int, int, str]]) -> list[tuple[str, str, int, int]]:
    """
    The declarations a Python module makes, as `(kind, dotted name, first line, last line)`.

    A definition's extent opens at the comment block above it. `__init__` is not a fragment of its own. Its lines stay
    with the class. A field's declaration and its comments then reach the class rather than the method.

    An override with no docstring is not a fragment either. The docstring on the overridden method is its contract. A
    bare `main` is not a fragment, and the module's docstring describes it. `.pylintrc` writes both in its docstring
    pattern.

    A definition inside a function body is not a fragment. Its lines stay with the function.
    `a-comment-inside-a-body-is-a-note` says the same of a comment there.
    """
    held: list[tuple[str, str, int, int]] = []
    overrides = _overridden(tree)

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if not gate.is_a_definition(child) or child.name == "__init__":
                continue
            if not ast.get_docstring(child) and child.name == "main" and isinstance(node, ast.Module):
                continue
            if isinstance(node, ast.ClassDef) and child.name in overrides and not ast.get_docstring(child):
                continue
            name = prefix + child.name
            opens = min([child.lineno] + [one.lineno for one in child.decorator_list])
            kind = "class" if isinstance(child, ast.ClassDef) else "function"
            held.append((kind, name, _opens_above(blocks, opens, set()), child.end_lineno or child.lineno))
            if isinstance(child, ast.ClassDef):
                walk(child, name + ".")

    walk(tree, "")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and gate.defined_names(node):
            name = ", ".join(gate.defined_names(node))
            held.append(("binding", name, _opens_above(blocks, node.lineno, set()), node.end_lineno or node.lineno))
    return held


def _python_bodies(tree: ast.Module) -> set[int]:
    """The lines inside a function body, where a comment is code rather than prose."""
    held: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            held.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return held


def _docstring_extents(tree: ast.Module) -> list[tuple[int, int]]:
    """The lines a docstring opens and closes on, over the module and what it declares."""
    held: list[tuple[int, int]] = []
    for node in [tree, *ast.walk(tree)]:
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            held.append((first.lineno, first.end_lineno or first.lineno))
    return held


def _python_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a Python file. A module makes a fragment. So does a class, a function and a module-level binding.

    A comment block that documents no declaration is a fragment too. A block inside a function body is code, held to a
    pair of lines by `a-comment-inside-a-body-is-a-note`.
    """
    tree = ast.parse(source)
    lines = source.split("\n")
    blocks = _blocks(_python_comments(source))
    bodies = _python_bodies(tree)
    strings = _docstring_extents(tree)
    is_prose = {at for opens, closes in strings for at in range(opens, closes + 1)} | {
        at for first, last, _said in blocks for at in range(first, last + 1) if at not in bodies
    }
    split = _Split(source, is_prose)
    for at, said in _python_comments(source).items():
        split.says(at, said)
    for at, (code, said) in _python_trailing(source).items():
        if at not in bodies:
            split.shares(at, code, said)
    for opens, closes in strings:
        indent = _indent_of(lines[opens - 1])
        for at in range(opens, closes + 1):
            said = lines[at - 1].removeprefix(indent).rstrip()
            said = _OPENS_A_STRING.sub("", said, count=1) if at == opens else said
            split.says(at, _CLOSES_A_STRING.sub("", said) if at == closes else said)
    return _gathered("Python", path, split, _python_extents(tree, blocks), blocks, set(), bodies)


# The brace opening a type's body rather than a function body, and the shape of a C declaration.
_A_TYPE_BODY = re.compile(r"\b(struct|enum|union)\b")
# the first name before the arguments, the body or the value.
_A_DECLARED_NAME = re.compile(r"(\w+)\s*[({;=\[]")

# The name a member of a C type has, in the body that declares it.
_A_MEMBER_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[;,=]")

# The close of a brace block a typedef names there. A line at file scope declaring nothing matches too.
_A_CLOSING_NAME = re.compile(r"^\}\s*(\w+)?")
_A_DIRECTIVE = re.compile(r"^#\s*(?!define\b)")  # an include or a branch. Such a line declares nothing.

# The line opening an include guard. The `#define` under it repeats the name and declares nothing.
_AN_INCLUDE_GUARD = re.compile(r"^#\s*ifndef\s+(\w+)\s*$")


def _c_declared(said: str, closing: str) -> tuple[str, str]:
    """The kind and the name a C declaration writes. A typedef names its type after the closing brace."""
    if said.strip().startswith("#define"):
        return "define", said.split()[1].partition("(")[0]
    named = _A_CLOSING_NAME.match(closing)
    if _A_TYPE_BODY.search(said.partition("{")[0]) or "typedef" in said:
        if named and named.group(1):
            return "type", named.group(1)
        found = _A_DECLARED_NAME.search(said)
        return "type", found.group(1) if found else closing
    found = _A_DECLARED_NAME.search(said)
    if not found:
        return "binding", closing
    kind = "function" if f"{found.group(1)}(" in said.replace(" (", "(") else "binding"
    return kind, found.group(1)


def _c_extents(source: str, marker: str) -> list[tuple[str, str, int, int]]:
    """
    The declarations a C file makes at file scope, as `(kind, name, first line, last line)`.

    A brace block runs to the brace that closes it. Anything else runs to the semicolon that ends it.
    """
    lines = _at_file_scope([line.partition(marker)[0] for line in source.split("\n")])
    held: list[tuple[str, str, int, int]] = []
    opens, said, depth = 0, "", 0
    for at, line in enumerate(lines, start=1):
        stripped = line.strip()
        if depth == 0 and not said and (not stripped or _A_DIRECTIVE.match(stripped)):
            continue
        if depth == 0 and not said:
            opens = at
        said += " " + stripped
        depth += line.count("{") - line.count("}")
        if depth > 0:
            continue
        if stripped.endswith("\\"):
            continue
        is_open = "{" in said
        if is_open and not stripped.endswith(";") and not stripped.startswith("}"):
            continue
        if not is_open and not stripped.endswith(";") and not said.lstrip().startswith("#define"):
            continue
        if not _is_an_include_guard(lines, at):
            held.append((*_c_declared(said, stripped), opens, at))
        said = ""
    return held


def _is_an_include_guard(lines: list[str], at: int) -> bool:
    """Whether the `#define` on line `at` repeats the name an `#ifndef` on the line above it guards."""
    guarded = _AN_INCLUDE_GUARD.match(lines[at - 2].strip()) if at > 1 else None
    return bool(guarded and lines[at - 1].strip() == f"#define {guarded.group(1)}")


def _at_file_scope(lines: list[str]) -> list[str]:
    """The lines with the braces of an `extern "C"` dropped. A declaration it holds is at file scope."""
    held, depth = list(lines), 0
    for at, line in enumerate(lines):
        if 'extern "C"' in line and "{" in line:
            held[at] = ""
            continue
        depth += line.count("{") - line.count("}")
        if depth < 0:
            held[at] = held[at].replace("}", "", 1)
            depth += 1
    return held


def _c_bodies(source: str, marker: str, extents: list[tuple[str, str, int, int]]) -> set[int]:
    """The lines a C function's braces hold. A comment there is code."""
    held: set[int] = set()
    lines = source.split("\n")
    for kind, _name, first, last in extents:
        if kind != "function":
            continue
        for at in range(first, last + 1):
            if "{" in lines[at - 1].partition(marker)[0]:
                held.update(range(at, last + 1))
                break
    return held


def _c_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a C file. The file makes a fragment. A type makes a fragment, and so does a function and so does a
    `#define`.

    A member of a type is part of that type rather than a fragment of its own. A comment block inside a function body is
    code.
    """
    marker = "//"
    blocks = _blocks(_comments(source, marker))
    branches = _branch_lines(source, marker)
    extents = [
        (kind, name, _opens_above(blocks, first, branches), last)
        for kind, name, first, last in _c_extents(source, marker)
    ]
    bodies = _c_bodies(source, marker, extents)
    is_prose = {at for first, last, _said in blocks for at in range(first, last + 1) if at not in bodies}
    split = _Split(source, is_prose)
    for at, said in _comments(source, marker).items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, marker).items():
        if at not in bodies and at not in branches:
            split.shares(at, code, said, last)
    return _gathered("C", path, split, extents, blocks, branches, bodies)


# The declaration a Makefile line makes. A target opens at the line's start, and an assignment there names a variable.
_A_TARGET = re.compile(r"^([A-Za-z0-9_./%-]+)\s*:(?!=)")
_A_VARIABLE = re.compile(r"^([A-Za-z0-9_]+)\s*[:?+]?=")  # plain, immediate, conditional or appending.

# The lines make reads as an instruction to itself rather than as a target. `.PHONY` is one and `.stamps` is not.
_A_MAKE_DIRECTIVE = re.compile(r"^\.[A-Z_]+$")


def _make_extents(source: str) -> list[tuple[str, str, int, int]]:
    """
    The declarations a Makefile makes, as `(kind, name, first line, last line)`. A target owns the recipe under it.
    """
    lines = source.split("\n")
    held: list[tuple[str, str, int, int]] = []
    at = 0
    while at < len(lines):
        found = _A_TARGET.match(lines[at]) or _A_VARIABLE.match(lines[at])
        if not found:
            at += 1
            continue
        kind = "target" if _A_TARGET.match(lines[at]) else "variable"
        last = at
        while last + 1 < len(lines) and (lines[last].endswith("\\") or lines[last + 1].startswith("\t")):
            last += 1
        if not _A_MAKE_DIRECTIVE.match(found.group(1)):
            held.append((kind, found.group(1), at + 1, last + 1))
        at = last + 1
    return held


def _make_fragments(path: str, source: str) -> list[Fragment]:
    """The fragments of a Makefile. The file, the targets and the variables."""
    blocks = _blocks(_comments(source, "#"))
    branches = _branch_lines(source, "#")
    extents = [
        (kind, name, _opens_above(blocks, first, branches), last) for kind, name, first, last in _make_extents(source)
    ]
    split = _Split(source, {at for first, last, _said in blocks for at in range(first, last + 1)})
    for at, said in _comments(source, "#").items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, "#").items():
        if at not in branches:
            split.shares(at, code, said, last)
    return _gathered("Make", path, split, extents, blocks, branches, set())


def _json_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a JSON file. A `_` value is the prose, and any other value is data.

    JSON writes no comment. A file of this tree says its own prose under `_`, at the top and inside an entry. A payload
    beside that prose is a fixture. A fixture may hold the very fault a hook must refuse.
    """
    held: list[Fragment] = []
    lines = source.split("\n")
    at, under = 0, 0
    while at < len(lines):
        if not _A_JSON_NOTE.match(lines[at]):
            at += 1
            continue
        first, run = at, [lines[at]]
        depth = lines[at].count("[") - lines[at].count("]")
        while depth > 0 and at + 1 < len(lines):
            at += 1
            run.append(lines[at])
            depth += lines[at].count("[") - lines[at].count("]")
        at += 1
        said = " ".join(_A_JSON_STRING.findall("\n".join(run))[1:]).strip()
        if not said:
            continue
        under += 1
        sites = (_Site(path, tuple(range(first + 1, first + 1 + len(run))), said),)
        key = _keyed("JSON", path, f"#{under}")
        held.append(Fragment(key, path, "note", first + 1, _text([said]), _text([]), sites))
    return held


def _flat_fragments(language: str, path: str, source: str) -> list[Fragment]:
    """
    The fragments of a straight-line file. The file itself, and the comment blocks lower down.

    The block at the top is the file's prose and the rest of the file is its code. Such a file declares nothing, and a
    block lower down documents no declaration.

    A settings file says its prose in a comment. Such a file with no comment at the top makes no fragment for the file
    itself. The data below a missing comment documents nothing.
    """
    marker = _MARKERS[language]
    blocks = _blocks(_comments(source, marker))
    split = _Split(source, {at for first, last, _said in blocks for at in range(first, last + 1)})
    for at, said in _comments(source, marker).items():
        split.says(at, said)
    for at, (code, said, last) in _marked_trailing(source, marker).items():
        split.shares(at, code, said, last)
    held = _gathered(language, path, split, [], blocks, set(), set())
    return [one for one in held if one.prose.content] if language == "Conf" else held


def _document_fragments(path: str, source: str) -> list[Fragment]:
    """
    The fragments of a markdown document. A paragraph, or a top-level list item taken with the items under it.

    A nested bullet read without its parent means nothing. A fenced block is code and makes no fragment. The heading
    above a paragraph names that paragraph, together with its place under the heading.
    """
    held: list[Fragment] = []
    heading, under = "", 0
    block: list[str] = []
    first = 1
    is_fenced = False

    def close(opens: int) -> None:
        nonlocal under
        content = "\n".join(block).strip("\n")
        if not content:
            return
        under += 1
        name = f"{heading} #{under}" if heading else f"#{under}"
        sites = (_Site(path, tuple(range(opens, opens + len(block))), content),)
        held.append(
            Fragment(_keyed("Markdown", path, name), path, "paragraph", opens, _text([content]), _text([]), sites)
        )

    is_front = source.startswith("---")
    for at, line in enumerate(source.split("\n"), start=1):
        if is_front:
            is_front = at == 1 or not _A_FRONT_MATTER.match(line)
            continue
        if _A_FENCE.match(line):
            is_fenced = not is_fenced
            continue
        if is_fenced:
            continue
        found = _A_HEADING.match(line)
        # A top-level item closes the item before it. A nested item continues with the item it stands under.
        opens = bool(_A_TOP_ITEM.match(line)) and bool(block)
        if found or not line.strip() or opens:
            close(first)
            block, first = [], at
            if found:
                heading, under = found.group(2), 0
                continue
        if line.strip():
            if not block:
                first = at
            block.append(line)
    close(first)
    return held


def _fragments_of(path: str) -> list[Fragment]:
    """The fragments of a single file of the tree, by the language of that file."""
    if language_of(path) is None:
        return []
    with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
        return _fragments_of_source(path, handle.read())


def _fragments_of_source(path: str, source: str) -> list[Fragment]:
    """
    The fragments of `path` as `source` writes it. The language of `path` decides the checker.

    A caller hands the text over rather than reading it. A write-time hook holds the file an edit would leave and asks
    about that.
    """
    language = language_of(path)
    if language is None:
        return []
    if language == "Markdown":
        return _document_fragments(path, source)
    if language == "Python":
        return _python_fragments(path, source)
    if language == "C":
        return _c_fragments(path, source)
    if language == "Make":
        return _make_fragments(path, source)
    if language == "JSON":
        return _json_fragments(path, source)
    return _flat_fragments(language, path, source)


def _joined(held: list[Fragment]) -> list[Fragment]:
    """
    A C function or type as a single fragment. The header declares the name and the source defines it.

    `_joined` matches a pair by name. The joined fragment keeps the source's key and the prose of both sites.
    """
    is_split = ("function", "type")
    bodies = {one.key.rpartition(":")[2]: one for one in held if one.kind in is_split and one.path.endswith(".c")}
    joined = []
    for one in held:
        named = one.key.rpartition(":")[2]
        if one.kind in is_split and one.path.endswith(".h") and named in bodies:
            body = bodies[named]
            said = "\n".join(part for part in (one.prose.content, body.prose.content) if part)
            code = "\n".join(part for part in (one.code.content, body.code.content) if part)
            body.prose, body.code = _Text(said, _digest(said)), _Text(code, _digest(code))
            body.sites = one.sites + body.sites
            continue
        joined.append(one)
    return joined


def _c_members(code: str) -> set[str]:
    """The names a C type's body declares. That is a struct's fields and an enum's constants."""
    return set(_A_MEMBER_NAME.findall(code.partition("{")[2]))


def _resolved(held: list[Fragment]) -> dict[str, str]:
    """
    `{a name prose may cite: the key it names}`, over the names that resolve to a single fragment.

    A fragment takes its bare name, and the file and the name together. A fragment named by a run of declarations takes
    any of them. A file fragment takes its file. A C type also takes any member it declares. A member is part of a type
    rather than a fragment of its own.

    A name a pair of fragments both take resolves to neither. Prose citing it has to name the file.
    """
    by_name: dict[str, set[str]] = {}
    for one in held:
        language, _, rest = one.key.partition(":")
        named, _, name = rest.partition(":")
        forms = {named} if not name else {name}
        for part in name.split(", ") if name else []:
            forms |= {part, f"{named}.{part}"}
            if language == "Python":
                forms |= {part.rpartition(".")[2], f"{os.path.splitext(named)[0]}.{part}"}
        if one.kind == "type":
            forms |= _c_members(one.code.content)
        for form in forms:
            by_name.setdefault(form, set()).add(one.key)
    return {form: keys.pop() for form, keys in by_name.items() if len(keys) == 1}


def fragments() -> list[Fragment]:
    """
    The fragments the project holds. A citation in a fragment's prose resolves to the fragment it names.

    A citation naming a field, a parameter or a local names no fragment, and this drops it. `every-cited-name-exists`
    holds citations to what the tree writes at all.

    A caller wanting the `is_about_the_tree` skip asks for it. The shape rules want that skip. The word rules reach the
    file it leaves out.
    """
    held: list[Fragment] = []
    for path in gate.prose_files():
        named = str(path.relative_to(gate.TREE))
        if check_documents.is_prose_of_the_project(named):
            held.extend(_fragments_of(named))
    held = _joined(held)
    resolves = _resolved(held)
    by_key = {one.key: one for one in held}
    for one in held:
        cited = {resolves[name] for name in _cited_by(one.prose.content, one.key.partition(":")[0]) if name in resolves}
        one.references = {key: by_key[key].code.sha for key in sorted(cited - {one.key})}
    return sorted(held, key=lambda one: (one.path, one.first))


@dataclass(frozen=True)
class _Edit:
    """
    A single edit a write-time hook is deciding. The named file, as the file is and as the edit would leave it.
    """

    path: str
    was: str
    now: str


# The words a literal says before it reads as prose. A shorter literal names something rather than saying it. The gate
# prints a short report line, and the rules hold that line too.
_FEWEST_WORDS_SAID = 2

# A literal the tree writes source into. `batch_pending_fragments` holds a workflow script that way. The `Makefile` and
# a dependency script hand a Python program to `python3 -c`.
_HOLDS_SOURCE = re.compile(r"(?:\b(?:const |function |phase\(|export |import |print\()|=>|#!/)")

# A pattern a shell hands to `grep`. A character class and a class shorthand belong to a regular expression rather than
# to a sentence.
_HOLDS_A_PATTERN = re.compile(r"\[[0-9a-zA-Z]-[0-9a-zA-Z]\]|\\[bwsdWSD]")

# The marker a writer puts on a literal that holds a layout rather than a sentence. The reason follows the colon.
_A_NOT_PROSE_MARKER = re.compile(r"(?:#|//).*\bnot-prose:\s*(\S.*)$")

# A block comment, with the prose between the markers. C and JS write one.
_A_BLOCK_COMMENT = re.compile(r"/\*(.*?)\*/", re.S)

# A heredoc, with the prose between the opener and the word that closes it.
_A_HEREDOC = re.compile(r"<<-?'?(\w+)'?\n(.*?)\n\1", re.S)

# An `echo` or a `printf` whose argument no quote holds. A shell and a Makefile write one.
_AN_ECHOED_WORD = re.compile(r"^[ \t]*@?(?:echo|printf)[ \t]+(?![\"'])(\S.*)$", re.M)

# A setting, with the prose after the key. A comment line opens on its marker and matches nothing here.
_A_SETTING = re.compile(r"^[ \t]*[\w.\-]+[ \t]*[:=][ \t]*(\S.*)$", re.M)

# The file whose strings are hook payloads rather than prose. `tests/hooks.json` holds the edits a hook must refuse. A
# payload there writes the very fault a checker names. The file's own prose sits under `_`, and `_json_fragments` reads
# that.
_HOOK_PAYLOADS = "tests/hooks.json"

# The quotes a language opens a string with. C writes a double quote. JS writes a double quote, a single quote and a
# backtick, and the backtick form interpolates.
_QUOTES = {"C": '"', "JS": "\"'`", "Shell": "\"'", "Make": "\"'", "CMake": '"', "YAML": "\"'", "JSON": '"'}

# The marker a language opens a line comment with. C and JS write a block comment too, and `_quoted_runs` skips a block
# comment under the `//` marker. JSON writes no comment, and the empty marker says so.
_A_LINE_COMMENT = {"C": "//", "JS": "//", "Shell": "#", "Make": "#", "CMake": "#", "YAML": "#", "JSON": ""}

# A conversion a C format string writes. The value it takes the place of is a name to a reader.
_A_CONVERSION = re.compile(r"%[-+ #0]*[0-9*]*(?:\.[0-9*]+)?(?:hh|h|ll|l|z|j|t|L)?[diouxXeEfFgGaAcspn%]")

# An interpolation a JS template literal writes. Its braces hold an expression rather than a sentence.
_AN_INTERPOLATION = re.compile(r"\$\{[^{}]*\}")

# The value a shell, a Makefile or a workflow expands. That is a parameter, a command substitution or a bare name behind
# a dollar.
_AN_EXPANSION = re.compile(r"\$\{[^{}]*\}|\$\([^()]*\)|\$[A-Za-z_@*#?!$0-9][A-Za-z_0-9]*")

# An escape a C or JS string writes. A break and a tab read as a space, and any other escape reads as its character.
_AN_ESCAPE = re.compile(r"\\(.)")

# A line break a C string writes before its last character. C has no multi-line string. A writer who wants such a string
# escapes the break. Such a string is a record. Prose in C gives a line to a literal, and the compiler joins the lines.
_A_BROKEN_LINE = re.compile(r"\\n(?!$)")


def _said_by_literal(node: ast.expr) -> str | None:
    """
    The text a literal says. This answers None where the node holds no string. An interpolation reads as a name in
    backticks.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "`name`"
            for part in node.values
        )
    return None


def _quoted_runs(source: str, quotes: str, marker: str) -> list[tuple[int, int, int, str]]:
    """
    `(the line a string opens on, where it opens, where it ends, the text between its quotes)` over `source`.

    A comment holds no literal. A fragment already holds a comment's prose. Reading a comment here would say the same
    thing twice. A `#` marker opens a comment at a word start, and `$#` inside a word opens none. An empty marker says
    the language writes no comment.

    A backslash takes the character after it. A shell single quote takes none, and the run there ends at the next quote
    whatever is in front of it.
    """
    held, at, line, length = [], 0, 1, len(source)
    while at < length:
        char = source[at]
        if char == "\n":
            line, at = line + 1, at + 1
        elif marker and source.startswith(marker, at) and (marker != "#" or at == 0 or source[at - 1] in " \t\n"):
            found = source.find("\n", at)
            at = length if found < 0 else found
        elif marker == "//" and source.startswith("/*", at):
            found = source.find("*/", at + 2)
            shut = length if found < 0 else found + 2
            line, at = line + source.count("\n", at, shut), shut
        elif char in quotes:
            escapes = char != "'" or marker == "//"
            opens, opened, walk = at, line, at + 1
            while walk < length and source[walk] != char:
                walk += 2 if escapes and source[walk] == "\\" else 1
            line += source.count("\n", opens, min(walk, length))
            held.append((opened, opens, min(walk + 1, length), source[opens + 1 : walk]))
            at = min(walk + 1, length)
        else:
            at += 1
    return held


def _unwrapped_runs(runs: list[tuple[int, int, int, str]]) -> list[tuple[int, int, int, str]]:
    """
    `runs` with a program unwrapped, as the same tuples.

    A shell single quote holds a program where the text inside it holds a double-quoted string. The prose there is the
    double-quoted strings, and the program around them is code. A single-quoted run holding no such string is prose
    itself. A run `_HOLDS_SOURCE` matches stays wrapped. Its inner strings are source as well.
    """
    held = []
    for line, opens, shuts, said in runs:
        inside = _quoted_runs(said, '"', "#") if '"' in said and not _HOLDS_SOURCE.search(said) else []
        if not inside:
            held.append((line, opens, shuts, said))
            continue
        held += [
            (line + said.count("\n", 0, at), opens + at + 1, opens + shut + 1, text) for _line, at, shut, text in inside
        ]
    return held


def _joined_runs(source: str, runs: list[tuple[int, int, int, str]]) -> list[tuple[int, str]]:
    """
    `(the line it opens on, what it says)` per string, with the strings a writer wraps over lines taken as a single one.

    C joins a pair of strings that only whitespace separates. JS joins a pair with a `+` between them. A message wrapped
    over a pair of lines reads as a single message, and a sentence may cross the wrap.
    """
    held: list[tuple[int, str]] = []
    ends: list[int] = []
    for line, opens, shuts, said in runs:
        if held and not source[ends[-1] : opens].strip(" \t\r\n+"):
            held[-1] = (held[-1][0], held[-1][1] + said)
            ends[-1] = shuts
        else:
            held.append((line, said))
            ends.append(shuts)
    return held


def _said_by_run(said: str, language: str) -> str:
    """
    The text a quoted run says, with what it interpolates reading as a name in backticks.

    A C conversion, a JS interpolation and a shell expansion take a value's place. An escaped break or tab reads as a
    space.
    """
    marked = _A_CONVERSION.sub("`name`", said) if language == "C" else said
    marked = _AN_INTERPOLATION.sub("`name`", marked) if language == "JS" else marked
    marked = _AN_EXPANSION.sub("`name`", marked) if language in ("Shell", "Make", "CMake", "YAML") else marked
    return _AN_ESCAPE.sub(lambda found: " " if found.group(1) in "ntr" else found.group(1), marked)


def _does_read_as_prose(said: str) -> bool:
    """
    Whether `said` reads as prose rather than as data.

    A string of fewer than `_FEWEST_WORDS_SAID` words names something rather than saying it. A string holding source is
    data, and so is a string holding a pattern. So is a line opening on `usage:`. A hook refusal ends on the name of the
    rule it enforces, and this leaves that refusal out too.
    """
    if _HOLDS_SOURCE.search(said) or _HOLDS_A_PATTERN.search(said):
        return False
    if said.lstrip().lower().startswith("usage:"):
        return False
    if prose_rules.is_stating_its_rule(said):
        return False
    return len(prose_rules.said_by(said).split()) >= _FEWEST_WORDS_SAID


def not_prose_lines(source: str) -> dict[int, str]:
    """
    `{the line a `not-prose:` marker is on: the reason it states}`.

    A literal may hold a record layout or a fixture line rather than a sentence. The marker covers the line it is on and
    the line under that. A writer trails the literal's own line with the marker, or writes the marker above.
    `check_prose` holds the marker in both directions.
    """
    return {
        at: found.group(1).strip()
        for at, line in enumerate(source.splitlines(), start=1)
        if (found := _A_NOT_PROSE_MARKER.search(line))
    }


def _unquoted_prose(source: str, language: str) -> list[tuple[int, str]]:
    """
    The prose of `source` that no quote holds, as `(the line it opens on, what it says)`.

    Prose sits outside a quote as well. A block comment holds prose between `/*` and `*/`. A heredoc holds prose between
    the opener and the word that closes it. An `echo` takes prose with no quote around it. A settings file writes prose
    after the key. A markdown heading names the section under it.

    `_quoted_runs` reaches none of those.
    """
    held: list[tuple[int, str]] = []

    def says(at: int, said: str) -> None:
        held.append((source[:at].count("\n") + 1, " ".join(said.split())))

    if language in ("C", "JS"):
        for found in _A_BLOCK_COMMENT.finditer(source):
            says(found.start(), found.group(1).replace("*", " "))
    if language == "Shell":
        for found in _A_HEREDOC.finditer(source):
            says(found.start(), found.group(2))
    for pattern, language_of_it in ((_AN_ECHOED_WORD, ("Shell", "Make")), (_A_SETTING, ("Conf",))):
        if language in language_of_it:
            for found in pattern.finditer(source):
                says(found.start(), found.group(1))
    if language == "Markdown":
        for found in _A_HEADING.finditer(source):
            says(found.start(), found.group(2))
    return held


def prose_literals(source: str, language: str = "Python") -> list[tuple[int, str]]:
    """
    The string literals of `source` that read as prose, as `(the line it opens on, what it says)`.

    A language `_LANGUAGES` names has a reader here. `_unquoted_prose` reads what no quote holds.

    `_does_read_as_prose` holds the tests a reader shares.

    A docstring is a fragment's prose and belongs elsewhere. An f-string reads whole, and an interpolation inside it
    comes back as a name in backticks. A literal of fewer than `_FEWEST_WORDS_SAID` words is data. That leaves out a
    rule name, a grammar operator and a format string. A literal holding source is data, and so is a pattern handed to
    `re` or a line opening on `usage:`. A literal a caller splits into words is a word list. A hook refusal ends on the
    name of the rule it enforces. This leaves that refusal out too.
    """
    bare = [(at, said) for at, said in _unquoted_prose(source, language) if _does_read_as_prose(said)]
    if language in _QUOTES:
        scanned = _quoted_runs(source, _QUOTES[language], _A_LINE_COMMENT[language])
        if language in ("Shell", "Make", "YAML"):
            scanned = _unwrapped_runs(scanned)
        runs = _joined_runs(source, scanned)
        if language == "C":
            runs = [(at, one) for at, one in runs if not _A_BROKEN_LINE.search(one)]
        return sorted(
            set(bare)
            | {
                (at, said)
                for at, said in ((at, _said_by_run(one, language)) for at, one in runs)
                if _does_read_as_prose(said)
            }
        )
    if language != "Python":
        return sorted(set(bare))
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    # An f-string counts as a single literal. Its own pieces are parts of it rather than literals beside it.
    pieces = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
        for inner in ast.walk(part)
    }
    # A pattern is data. `re.compile` says so, and its alternations read as words to anything counting them.
    patterns = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("compile", "match", "search", "fullmatch", "sub", "findall", "finditer", "split")
        for arg in node.args
        for inner in ast.walk(arg)
    }
    # A word list is data. A caller splitting a literal into words wants the words rather than a sentence.
    word_lists = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split"
        for inner in ast.walk(node.func.value)
    }
    held = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if id(node) in docstrings or id(node) in pieces or id(node) in patterns or id(node) in word_lists:
            continue
        said = _said_by_literal(node)
        if said is not None and _does_read_as_prose(said):
            held.append((node.lineno, said))
    return sorted(set(held))


def written(tool_input: Mapping[str, object]) -> _Edit | None:
    """
    The edit `tool_input` describes, whatever the file holds.

    Text an Edit replaces that is not in the file decides nothing. The tool refuses such an edit before anything lands.
    """
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return None
    was = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            was = handle.read()
    content = tool_input.get("content")
    if isinstance(content, str):
        return _Edit(path, was, content)
    old, new = tool_input.get("old_string"), tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or old not in was:
        return None
    return _Edit(path, was, was.replace(old, new, -1 if tool_input.get("replace_all") else 1))


def edited(tool_input: Mapping[str, object]) -> _Edit | None:
    """The edit `tool_input` describes, or None where the named path holds no prose of this project."""
    one = written(tool_input)
    return one if one is not None and check_documents.is_prose_of_the_project(one.path) else None


def is_about_the_tree(path: str) -> bool:
    """
    Whether the prose of `path` describes the tree. `CHANGELOG.md` records what a change did, and the shape rules skip
    it.

    A hook hands over an absolute path. This reads the base name. `is_prose_of_the_project` reads it the same way.
    """
    return os.path.basename(path) not in _NOT_ABOUT_THE_TREE


def _fragments_left(one: _Edit) -> list[Fragment]:
    """
    The fragments the file would hold once `one` lands. A fragment rule asks about these.

    A caller wanting the `is_about_the_tree` skip asks for it here. The word rules and the number rule reach a file that
    skip leaves out.
    """
    return _fragments_of_source(one.path, one.now)


def fragments_touched(one: _Edit) -> list[Fragment]:
    """
    The fragments `one` writes prose into. A write-time hook judges these, and passes over the rest of the file.

    A fragment comes back where the edit mints its key. A fragment comes back where its prose digest moves. A fragment
    whose code moved and whose prose held still has no prose to judge.

    A hook reading the whole file refuses an edit for a fault the edit did not write. A file holding many faults then
    takes no edit at all. `check_prose` reads the whole tree, and it refuses the commit over a fault the hook here
    passes over.
    """
    before = {said.key: said.prose.sha for said in _fragments_of_source(one.path, one.was)}
    return [said for said in _fragments_left(one) if before.get(said.key) != said.prose.sha]


def does_hold_hook_payloads(path: str) -> bool:
    """Whether the strings of `path` are hook payloads rather than prose. `_HOOK_PAYLOADS` names that file."""
    return os.path.basename(path) == os.path.basename(_HOOK_PAYLOADS)


def literals_touched(one: _Edit) -> list[tuple[int, str]]:
    """
    The prose literals `one` writes, as `(the line it opens on, what it says)`.

    A literal comes back where the edit writes its text. The text decides this. An edit above a literal moves the line
    under it, and the text there says the same thing.

    `does_hold_hook_payloads` names the file whose strings are payloads. This reads none of them.
    """
    if does_hold_hook_payloads(one.path):
        return []
    language = language_of(one.path) or ""
    before = {said for _at, said in prose_literals(one.was, language)}
    marked = not_prose_lines(one.now)
    return [
        (at, said)
        for at, said in prose_literals(one.now, language)
        if said not in before and at not in marked and at - 1 not in marked
    ]


def _unnamed_prose() -> list[str]:
    """
    The tracked files this project writes prose in that no reader here covers.

    `language_of` names the reader. A file with no reader goes unread in silence. Either a reader learns the language,
    or `gate.UNREAD` names the file and the comment there says why.
    """
    return sorted(
        path
        for path in gate.tracked_files()
        if check_documents.is_prose_of_the_project(path) and language_of(path) is None
    )


def _undescribed_fragments(held: list[Fragment]) -> list[str]:
    """The fragments holding no prose."""
    return [f"{one.path}:{one.first}: {one.key} holds no prose" for one in held if not one.prose.content]


def _colliding_keys(held: list[Fragment]) -> list[str]:
    """The keys that a pair of fragments share. A key names a single fragment."""
    seen: dict[str, int] = {}
    for one in held:
        seen[one.key] = seen.get(one.key, 0) + 1
    return sorted(key for key, count in seen.items() if count > 1)


def main() -> None:
    gate.report(
        [
            f"{path}: nothing here can read this file. Teach `language_of` its language, or name the file in "
            f"`gate.UNREAD`."
            for path in _unnamed_prose()
        ],
        "file(s) of prose no reader covers",
        "",
    )
    held = fragments()
    gate.report([f"{key}: fragments share this key" for key in _colliding_keys(held)], "colliding fragment key(s)", "")
    gate.report(_undescribed_fragments(held), "fragment(s) holding no prose", "")
    if "--json" in sys.argv:
        json.dump([asdict(one) for one in held], sys.stdout, indent=2)
        return
    by_kind: dict[str, int] = {}
    for one in held:
        by_kind[one.kind] = by_kind.get(one.kind, 0) + 1
    cited = sum(len(one.references) for one in held)
    print(f"prose: {len(held)} fragment(s) over {len({one.path for one in held})} file(s), {cited} reference(s)")
    for kind, count in sorted(by_kind.items()):
        print(f"    {count} {kind}")


if __name__ == "__main__":
    main()
