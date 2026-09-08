# SPDX-License-Identifier: MIT
"""
Check the conventions a gate can decide.

`.claude/conventions.md` is the written list. This gate decides a rule that walking the tree settles, and a reader is
then free of it. A reader judges what no gate decides.

The rules held here are `a-repository-question-with-two-callers-is-asked-once` and
`a-literal-list-spells-each-word-once`. This gate also holds `a-private-name-carries-an-underscore`,
`an-enumeration-of-the-tree-lives-in-gate` and `a-parsed-source-is-split-on-the-newline`. A rule here asks about the
whole tree. A per-file linter asks no such question.

`a-boolean-name-is-a-question` is held here as well. This gate reads the answer off the type, in C and in Python alike.

`a-name-uses-the-words-this-project-uses` holds a bound name to the word list the prose obeys.

`a-comment-inside-a-body-is-a-note` and `a-nested-docstring-is-a-note` come off the syntax tree. The `short_comments`
hook refuses the shape as the writer writes it, and this gate holds the tree.

`a-make-assignment-carries-no-trailing-comment`. `make` keeps the whitespace before a `#` inside the value. The comment
then lands in the path.

`a-piece-of-prose-is-written-once`, over the fragments `collect_fragments` gives it. This gate compares a pair of proses
by the runs of words they share, and a paraphrase then counts as a copy. A C header and its source count together.

`a-mechanised-rule-names-its-convention`. A convention `.claude/conventions.md` marks mechanised names the module that
decides it, and that module's docstring names the convention back.

**Usage:** `python3 generator/check_conventions.py`.
"""

import ast
import io
import os
import re
import tokenize

from collections.abc import Iterable

import collect_fragments
import gate
import prose_rules

# The containers a module-level cache opens as.
_A_CACHE = (ast.Dict, ast.Set, ast.List)


def _cached_names(tree: ast.Module) -> set[str]:
    """
    The module-level names opened as an empty container. This tree writes a cache that way.

    A cache with a type reads as an annotated assignment rather than a plain one. Both are read here. Annotating a cache
    then does not hide it.
    """
    held: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr]
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        else:
            continue
        opened = isinstance(value, _A_CACHE) and not (getattr(value, "keys", None) or getattr(value, "elts", None))
        called = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in ("set", "dict", "list")
            and not value.args
        )
        if opened or called:
            held.update(one.id for one in targets if isinstance(one, ast.Name))
    return held


def _does_take_nothing(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether `node` is a function called with no argument at all."""
    held = node.args
    return not (held.args or held.posonlyargs or held.kwonlyargs or held.vararg or held.kwarg)


def _questions(tree: ast.Module) -> set[str]:
    """The names of the module-level functions that reach a subprocess, directly or through a function that does."""
    direct = {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and "subprocess" in ast.dump(node)}
    held = set(direct)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        said = ast.dump(node)
        if any(f"id='{name}'" in said for name in direct):
            held.add(node.name)
    return held


def _uncached_question_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a repository question that a pair of callers run afresh."""
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        questions, cached = _questions(tree), _cached_names(tree)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in questions or not _does_take_nothing(node):
                continue
            callers = sum(
                1
                for call in ast.walk(tree)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == node.name
            )
            if callers < 2 or cached & {one.id for one in ast.walk(node) if isinstance(one, ast.Name)}:
                continue
            at = f"{name}:{node.lineno}"
            held.append((at, f"{at} `{node.name}` reads the repository at {callers} call sites and caches nothing"))
    return held


def _repeated_word_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a literal list of words that writes one of them twice."""
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "split" or not isinstance(node.func.value, ast.Constant):
                continue
            said = node.func.value.value
            if not isinstance(said, str):
                continue
            twice = sorted({word for word in said.split() if said.split().count(word) > 1})
            if twice:
                at = f"{name}:{node.lineno}"
                held.append((at, f"{at} writes {', '.join(twice)} more than once in one literal list"))
    return held


def _bare_private_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a module-level name that has no underscore and that only its own module reads."""
    # A hook holds module-level names of its own, and a name only its own hook reads is as private as one in a module.
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in gate.modules() + gate.hook_modules()}
    # `(the module that reads, the module read, the name read)`, from each `module.name` in the tree.
    read = {
        (path.stem, node.value.id, node.attr)
        for path, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    held = []
    for path, tree in trees.items():
        name = str(path.relative_to(gate.TREE))
        for node in tree.body:
            for bound in gate.defined_names(node):
                if bound.startswith("_") or bound == "main":
                    continue
                if any(reader != path.stem and owner == path.stem and what == bound for reader, owner, what in read):
                    continue
                at = f"{name}:{node.lineno}"
                held.append((at, f"{at} only {path.stem} reads `{bound}`, and the name has no underscore"))
    return held


def _banned_word_name_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a bound name writing a word `prose_rules` refuses.

    This gate splits a name on its underscores and asks `prose_rules` about the words. A name and the prose beside it
    obey the same word list.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = node.name
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound = node.id
            elif isinstance(node, ast.arg):
                bound = node.arg
            else:
                continue
            refused = prose_rules.words_found(" ".join(bound.split("_")))["word we do not use"]
            if refused:
                at = f"{name}:{node.lineno}"
                held.append(
                    (f"{at} {bound}", f"{at} `{bound}` writes `{refused[0]}`. the project turned that word down.")
                )
    return held


# The calls a module asks the filesystem with. `walk` counts only where a caller asks it of `os`. The rest of this tree
# writes `ast.walk`.
_ENUMERATES = frozenset({"glob", "rglob", "iterdir", "scandir", "listdir"})


def _enumeration_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a module outside `gate` that asks the filesystem what the tree holds.

    A roster lists the files a question covers. A pair of rosters of the same thing drift. The roster a gate forgot
    reads exactly like a gate that passed. `gate` holds the rosters. Adding a category then touches a single file.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        if path.stem == "gate":
            continue
        name = str(path.relative_to(gate.TREE))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            asked = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            on = called.value.id if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name) else ""
            if asked in _ENUMERATES or (asked == "walk" and on == "os"):
                at = f"{name}:{node.lineno}"
                held.append((at, f"{at} asks the filesystem what is there. that is the job of `gate`."))
    return held


# A `Makefile` assignment with a comment on its own line. The text before the hash goes into the value.
_AN_ASSIGNMENT_AND_A_COMMENT = re.compile(r"^([A-Za-z0-9_]+)\s*[:?+]?=.*?\S\s+#")


def _make_trailing_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a `Makefile` assignment that has a comment sharing its line.

    `make` keeps the whitespace before the hash inside the value. A value spent as a command word survives that. A value
    concatenated into a path breaks.
    """
    held = []
    for path in gate.build_files():
        if path.name != "Makefile":
            continue
        for at, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            found = _AN_ASSIGNMENT_AND_A_COMMENT.match(line)
            if found:
                site = f"{path.name}:{at}"
                held.append(
                    (
                        site,
                        f"{site} `{found.group(1)}` takes a comment on the same line, and the spaces with that "
                        f"comment",
                    )
                )
    return held


# The share of the shorter fragment's wording the longer fragment repeats before the pair are the same claim. A
# paraphrase keeps the nouns and their order, and a shingle reads that.
_ALIKE_ENOUGH = 0.7

# The length in words of a shingle. A shorter run matches an idiom. A longer run lets a paraphrase through.
_A_SHINGLE = 4

# The shortest sentence the repeat check reads. A short sentence recurs as a label rather than as a claim.
_FEWEST_WORDS_SAID = 5


def _shingles(prose: str) -> set[tuple[str, ...]]:
    """The lower-cased runs of `_A_SHINGLE` words `prose` writes."""
    said = prose.lower().split()
    return {tuple(said[at : at + _A_SHINGLE]) for at in range(len(said) - _A_SHINGLE + 1)}


def _how_alike(first: str, second: str) -> float:
    """The share of the shorter prose that the other repeats, as a fraction."""
    one, other = _shingles(first), _shingles(second)
    if not one or not other:
        return 0.0
    return len(one & other) / min(len(one), len(other))


def _pairs_with(path: str) -> str:
    """
    The fragments this compares a fragment's prose against. A C header and its source are a single thing said in a pair
    of files.
    """
    return path[: -len(".h")] if path.endswith((".c", ".h")) else path


# A comment dividing a file into sections. The form is `--- what the section is ---`. A divider names a section and
# claims nothing. A header and its source use the same dividers in the same order.
_A_DIVIDER = re.compile(r"^-{2,}.*-{2,}$")

# The marker a Doxygen block opens on. The text from there on is the per-function contract. The published documentation
# wants a function's page whole, and a pair of functions sharing a signature write that contract alike.
_A_DOXYGEN_BLOCK = re.compile(r"^@(?:param|return|retval)\b", re.MULTILINE)


def _claimed_by(prose: str) -> str:
    """The claim `prose` makes. That is the text in front of its Doxygen block."""
    found = _A_DOXYGEN_BLOCK.search(prose)
    return prose[: found.start()] if found else prose


def _repeated_sentence_faults(one: collect_fragments.Fragment) -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a fragment that writes the same sentence twice."""
    seen: dict[str, int] = {}
    held = []
    at = f"{one.path}:{one.first}"
    for said in prose_rules.sentences(_claimed_by(one.prose.content)):
        if len(said.split()) < _FEWEST_WORDS_SAID:
            continue
        seen[said] = seen.get(said, 0) + 1
        if seen[said] == 2:
            held.append((at, f"{at} writes `{said[:60]}` twice in one fragment"))
    return held


def _repeated_piece_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a claim written twice.

    A fragment says a sentence twice. A pair of fragments of a file say the same thing. A C header and the source beside
    it both say the same claim. The pairing above reads that pair as a single place.

    This catches a paraphrase as it catches an exact copy. The pair of proses are compared by the runs of words they
    share.

    A divider is left out. It names a section rather than saying anything about the code. This leaves a Doxygen block
    out with it.
    """
    held: list[tuple[str, str]] = []
    by_module: dict[str, list[collect_fragments.Fragment]] = {}
    for one in collect_fragments.fragments():
        if not collect_fragments.is_about_the_tree(one.path):
            continue
        if _A_DIVIDER.match(one.prose.content):
            continue
        if not one.prose.content:
            continue
        held += _repeated_sentence_faults(one)
        by_module.setdefault(_pairs_with(one.path), []).append(one)
    for group in by_module.values():
        for at, one in enumerate(group):
            for other in group[at + 1 :]:
                if _how_alike(_claimed_by(one.prose.content), _claimed_by(other.prose.content)) >= _ALIKE_ENOUGH:
                    site = f"{one.path}:{one.first}"
                    held.append((site, f"{site} says what {other.path}:{other.first} says"))
    return held


# The length a comment inside a function body may run to. A note fits. A passage does not.
_MOST_COMMENT_LINES = 2

# The field whose trailing comment a run below it continues.
_A_FIELD = re.compile(r"\s*self\.\w+")


def _comment_runs(source: str) -> list[tuple[int, int]]:
    """
    The runs of consecutive comment lines, as `(the line it opens on, how many lines it runs)`.

    A comment sharing its line with code opens no run. `tokenize` is what tells a marker in a string from a comment.
    """
    lines = source.split("\n")
    alone = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and not lines[token.start[0] - 1][: token.start[1]].strip():
            alone.add(token.start[0])
    runs: list[tuple[int, int]] = []
    for at in sorted(alone):
        if runs and at == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((at, 1))
    return runs


def _long_comment_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a comment inside a function body that runs past `_MOST_COMMENT_LINES`.

    A run below a field's trailing comment continues that comment. Both count as one.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")
        inside: set[int] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inside.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for first, length in _comment_runs(source):
            before = lines[first - 2] if first >= 2 else ""
            whole = length + 1 if "#" in before and _A_FIELD.match(before) else length
            if whole <= _MOST_COMMENT_LINES or first not in inside:
                continue
            at = f"{name}:{first}"
            held.append((at, f"{at} explains for {whole} lines inside a body, where a note of two would do"))
    return held


def _long_nested_docstring_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a nested function whose docstring runs past `_MOST_COMMENT_LINES`.

    A nested function is no fragment of its own. `collect_fragments` folds its docstring into the enclosing function. A
    docstring worth more than a note therefore belongs to a function at the top level.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        outer = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        nested = {id(one) for top in outer for one in ast.walk(top) if one is not top}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or id(node) not in nested:
                continue
            if ast.get_docstring(node) is None:
                continue
            said = node.body[0]
            length = (said.end_lineno or said.lineno) - said.lineno + 1
            if length <= _MOST_COMMENT_LINES:
                continue
            at = f"{name}:{node.lineno}"
            held.append((at, f"{at}: `{node.name}` nests and says {length} lines, where a note of two would do"))
    return sorted(set(held))


# The words a name asks a yes-or-no question with. `ys_are_tokens_stable` is the form the public API uses. The word goes
# anywhere in the name. `ys_queue_is_ready` asks about the queue rather than about `ys`.
_ASKS = re.compile(r"(^|_)(is|has|did|does|are|was|were|can|may|should)_")

# The word a name says it is a command that runs and may fail with. A `bool` from such a call is whether it worked.
_TRIES = re.compile(r"(^|_)try_")

# A C function handing back a `bool`, as a definition or as a declaration. This reads the type and leaves the name to
# `_does_name_match`.
_C_ANSWERS_YES_OR_NO = re.compile(r"^(?:static\s+|YS_API\s+|inline\s+)*bool\s+(\w+)\s*\(", re.M)

# The names a `bool` a call hands back may take. A name asks a question, or a name says the call tries.
_A_CALL_MAY_BE_CALLED = (_ASKS, _TRIES)


def _does_name_match(named: str, allowed: Iterable[re.Pattern[str]]) -> bool:
    """Whether `named` has the name a `bool` under it needs."""
    return any(pattern.search(named) for pattern in allowed)


def _c_boolean_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a C function handing back a `bool` under a name which says neither.

    A `bool` is an answer to a question, and a question's name asks it. A command that runs and may fail says it tries.
    The `bool` from such a command says whether it worked. Holding the two apart is what lets the type decide the name.
    """
    held = []
    for path in gate.c_sources() + gate.test_sources():
        name = str(path.relative_to(gate.TREE))
        said = path.read_text(encoding="utf-8")
        for found in _C_ANSWERS_YES_OR_NO.finditer(said):
            named = found.group(1)
            if _does_name_match(re.sub(r"^ys_", "", named), _A_CALL_MAY_BE_CALLED):
                continue
            at = f"{name}:{said[: found.start()].count(chr(10)) + 1}"
            held.append((at, f"{at} `{named}` hands back a `bool` under a name which is neither a question nor a try"))
    return held


def _boolean_annotations(tree: ast.Module) -> list[tuple[int, str, tuple[re.Pattern[str], ...]]]:
    """
    `(line, name, what the name may be)` per `bool` the module writes down.

    A call hands a `bool` back, a parameter takes a `bool`, and an annotated assignment holds a `bool`. Python names the
    question `__bool__`, and the author does not choose that name.
    """
    held: list[tuple[int, str, tuple[re.Pattern[str], ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and _is_bool(node.annotation) and isinstance(node.target, ast.Name):
            held.append((node.lineno, node.target.id, (_ASKS,)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_bool(node.returns) and node.name != "__bool__":
                held.append((node.lineno, node.name, _A_CALL_MAY_BE_CALLED))
            arguments = node.args
            for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
                if _is_bool(argument.annotation):
                    held.append((node.lineno, argument.arg, (_ASKS,)))
    return held


def _is_bool(annotation: ast.expr | None) -> bool:
    """Whether `annotation` is `bool` itself. A union holding `bool` is a value that is sometimes something else."""
    return isinstance(annotation, ast.Name) and annotation.id == "bool"


def _python_boolean_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a Python `bool` under a name which says neither.

    The rule is the C one, read off the annotation rather than off the declaration. A name holding a `bool` asks a
    question. A call handing a `bool` back asks a question, or says it tries. The `bool` then says whether it worked.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, named, allowed in _boolean_annotations(tree):
            if _does_name_match(named, allowed):
                continue
            at = f"{name}:{line}"
            said = "a question" if allowed == (_ASKS,) else "a question nor a try"
            held.append((at, f"{at} `{named}` has a `bool` under a name which is not {said}"))
    return held


# The call that reads a source the way Python does. That call numbers its lines by the newline.
_PARSES_A_SOURCE = frozenset({"parse", "generate_tokens", "StringIO"})


def _parsed_names(holder: ast.AST) -> set[str]:
    """The names handed to a parse inside `holder`, whose lines the newline then numbers."""
    held: set[str] = set()
    for node in ast.walk(holder):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        asked = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        if asked in _PARSES_A_SOURCE:
            held.update(one.id for one in node.args if isinstance(one, ast.Name))
    return held


def _split_source_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a source that a parse reads and `splitlines` also cuts up.

    A parse numbers a line by the newline. `splitlines` cuts on more than that, U+2028 and U+0085 among them, and
    `star.py` writes both in string literals. A list cut that way is indexed by a number that counted differently, and
    it answers with the wrong line. Rebuilt into a source it stops parsing. Splitting on the newline says the same thing
    where a file holds nothing exotic, and the truth where a file does.
    """
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        for holder in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parsed = _parsed_names(holder)
            for node in ast.walk(holder):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "splitlines" or not isinstance(node.func.value, ast.Name):
                    continue
                if node.func.value.id in parsed:
                    at = f"{name}:{node.lineno}"
                    held.append(
                        (
                            at,
                            f"{at} cuts a parsed source with `splitlines`. that call counts more lines than the parse.",
                        )
                    )
    return held


# A convention, as `.claude/conventions.md` writes one. The name leads the list item in bold, and the item runs to the
# next one.
_A_CONVENTION = re.compile(r"^- \*\*([a-z0-9-]+)\*\*(.*?)(?=\n- \*\*|\n#|\Z)", re.MULTILINE | re.DOTALL)

# The note saying how far a module mechanises a convention. The note closes its item as an italic run.
_A_MECHANISED_NOTE = re.compile(r"\*((?:Mechanised|Partly mechanised)[^*]*)\*")


def _mechanised_by() -> dict[str, set[str]]:
    """The conventions `.claude/conventions.md` marks mechanised, against the modules of this tree their notes name."""
    with open(os.path.join(gate.TREE, ".claude", "conventions.md"), encoding="utf-8") as handle:
        said = handle.read()
    known = {path.stem for path in gate.modules() + gate.hook_modules()}
    held = {}
    for name, body in _A_CONVENTION.findall(said):
        note = _A_MECHANISED_NOTE.search(body)
        if note:
            held[name] = {one for one in re.findall(r"`([A-Za-z_][\w.]*)`", note.group(1)) if one in known}
    return held


def _uncited_convention_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a module that mechanises a convention and does not name it."""
    docstrings = {
        path.stem: ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        for path in gate.modules() + gate.hook_modules()
    }
    held = []
    for name, modules in sorted(_mechanised_by().items()):
        for stem in sorted(modules):
            if f"`{name}`" in docstrings[stem]:
                continue
            at = f".claude/conventions.md: {name}"
            held.append(
                (
                    f"{at} in {stem}",
                    f"{at} names `{stem}` as its mechanism, and that module's docstring does not name "
                    f"the rule back",
                )
            )
    return held


def _check() -> None:
    """Report the broken conventions."""
    found = (
        _uncited_convention_faults()
        + _uncached_question_faults()
        + _repeated_word_faults()
        + _bare_private_faults()
        + _banned_word_name_faults()
        + _enumeration_faults()
        + _split_source_faults()
        + _c_boolean_faults()
        + _python_boolean_faults()
        + _long_comment_faults()
        + _long_nested_docstring_faults()
        + _repeated_piece_faults()
        + _make_trailing_faults()
    )
    gate.report(
        [said for _site, said in found],
        "broken convention(s). Fix the site",
        "conventions: the tree holds to the rules a gate can decide",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
