# SPDX-License-Identifier: MIT
"""
Check the conventions a gate can decide.

`.claude/conventions.md` is the written list. This gate decides a rule that walking the tree settles, and a reader is
then free of it. A reader judges what no gate decides.

The rules held here are `a-repository-question-with-two-callers-is-asked-once` and
`a-literal-list-spells-each-word-once`. This gate also holds `a-private-name-carries-an-underscore`,
`an-enumeration-of-the-tree-lives-in-gate` and `a-parsed-source-is-split-on-the-newline`. A rule here asks about the
whole tree.

This gate holds `a-boolean-name-is-a-question` as well. It reads the answer off the type, in C and in Python alike. This
gate leaves `a-boolean-is-a-pure-question` to the reader. The `bool` checks here read only the name.

`a-name-uses-the-words-this-project-uses` holds a bound name to the word list the prose obeys.

`a-comment-inside-a-body-is-a-note` and `a-nested-docstring-is-a-note` come off the syntax tree. `long_comment_runs` and
`long_nested_docstrings` read it, and the `short_comments` and `nested_docstrings` checkers refuse the shape.

`a-make-assignment-carries-no-trailing-comment`. `make` keeps the whitespace before a `#` inside the value, as
https://www.gnu.org/software/make/manual/html_node/Simple-Assignment.html says. The spaces then land in the path.

`a-mechanised-rule-names-its-convention`. A convention `.claude/conventions.md` marks mechanised names the module that
decides it, and that module's docstring names the convention back. `convention_note_faults` reads the notes, and the
`document_rules` checker refuses a broken note. A hook refusal ends on the rule it enforces, and this gate reads that
name back the other way. The rule's note then credits the hook.

`a-hook-refuses-through-one-helper`. A hook answers the tool through `refusal.py` or through `.claude/hooks/refusal.sh`.
This gate reads the payload keys off a hook. This gate passes `refusal.py` and `.claude/hooks/refusal.sh`.

`a-gate-walks-the-hooks-or-says-why-not`. This gate reads the calls a module makes on `gate`. A gate may skip the hooks.
Its docstring then names them.

`a-matched-name-is-spelled-somewhere-else`. This gate reads a module-level list of identifier names and looks for the
names across the tree. This gate skips a list holding a name too short for `_A_MATCHED_NAME`.

`a-fragment-is-found-by-its-key`. This gate refuses a read of `starts` or `start_line` outside `collect_fragments`.

**Usage:** `python3 generator/check_conventions.py`.
"""

import ast
import re
import tokenize

from collections.abc import Iterable

import collect_fragments
import gate
import prose_rules

# The containers a module-level cache opens as.
_A_CACHE = (ast.Dict, ast.Set, ast.List)

# The keys the tool reads a hook's answer under, as https://code.claude.com/docs/en/hooks says. A hook writing one of
# these builds the payload itself.
_A_PAYLOAD_KEY = ('"decision"', "decision:", "permissionDecision", "hookSpecificOutput")

# The files here hold the payload for the hooks beside them. A helper writes the keys `_A_PAYLOAD_KEY` holds.
_THE_REFUSAL_HELPERS = ("refusal",)

# The word a gate's docstring writes where that gate walks a roster without the hooks.
_A_HOOK = re.compile(r"\bhooks?\b", re.IGNORECASE)

# The text of the files that hold the project's prose. `_tree_text` fills this once.
_TREE_TEXT: dict[str, str] = {}

# A name long enough to look for across the tree. A grammar parameter is a single letter, as `annotated2ir.PARAMS`
# shows. The search matches a name as a whole word. A single letter occurs as a whole word in text unrelated to the
# parameter.
_A_MATCHED_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")

# The fewest names a literal list holds before a check matches source against that list.
_FEWEST_LISTED = 2

# The line a hook refusal names its rule on. A hook names the convention it enforces there, and `check_conventions`
# reads the name back.
_A_SIGNED_RULE = re.compile(r"Rule: ([a-z][a-z0-9-]*)")


def _cached_names(tree: ast.Module) -> set[str]:
    """
    The module-level names opened as an empty container. This tree writes a cache that way.

    A cache with a type reads as an annotated assignment rather than a plain one. This function reads both. Annotating a
    cache then does not hide it.
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
    """Whether the function `node` defines declares no parameter at all."""
    held = node.args
    return not (held.args or held.posonlyargs or held.kwonlyargs or held.vararg or held.kwarg)


def _questions(tree: ast.Module) -> set[str]:
    """
    The names of the module-level functions whose code mentions `subprocess`, and of the module-level functions that
    name one of those.
    """
    direct = {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and "subprocess" in ast.dump(node)}
    held = set(direct)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        said = ast.dump(node)
        if any(f"id='{name}'" in said for name in direct):
            held.add(node.name)
    return held


# The decorator keeps a function's answer, as https://docs.python.org/3/library/functools.html#functools.cache says. A
# function wearing it reads the repository once. A later caller reads what the decorator kept.
_A_CACHING_DECORATOR = "cache"


def _is_memoized(node: ast.FunctionDef) -> bool:
    """Whether `node` wears a decorator that keeps its answer."""
    named = [one.func if isinstance(one, ast.Call) else one for one in node.decorator_list]
    return any(
        (isinstance(one, ast.Attribute) and one.attr == _A_CACHING_DECORATOR)
        or (isinstance(one, ast.Name) and one.id == _A_CACHING_DECORATOR)
        for one in named
    )


def _uncached_question_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a repository question that two or more call sites run afresh."""
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
            if callers < 2 or _is_memoized(node):
                continue
            if cached & {one.id for one in ast.walk(node) if isinstance(one, ast.Name)}:
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
                held.append((at, f"{at} writes {', '.join(twice)} more than once in a literal list"))
    return held


def _bare_private_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a bare module-level name with no reader outside its own module. A bare name lacks a
    leading underscore and is not `main`. A module, hook or MCP server reads a name as `module.name`. A shell script
    that writes `module.name` also reads it.
    """
    # A hook and an MCP server hold module-level names of their own. A name its own module reads counts as private.
    outside = gate.hook_modules() + gate.mcp_modules()
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in gate.modules() + outside}
    # `read` holds `(the module that reads, the module read, the name read)` for a `module.name` in the tree.
    read = {
        (path.stem, node.value.id, node.attr)
        for path, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    # `shell` holds the text of the shell scripts. A `module.name` in that text counts as a read.
    shell = "\n".join(path.read_text(encoding="utf-8") for path in gate.shell_scripts())
    held = []
    for path, tree in trees.items():
        name = str(path.relative_to(gate.TREE))
        for node in tree.body:
            for bound in gate.defined_names(node):
                if bound.startswith("_") or bound == "main":
                    continue
                if any(reader != path.stem and owner == path.stem and what == bound for reader, owner, what in read):
                    continue
                if f"{path.stem}.{bound}" in shell:
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


# The calls a module asks the filesystem with. `walk` counts only where a caller asks it of `os`.
_ENUMERATES = frozenset({"glob", "rglob", "iterdir", "scandir", "listdir"})


def _enumeration_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a module outside `gate` that asks the filesystem what the tree holds.

    `gate` holds the rosters of the files a question covers.
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


# The names a fragment keeps its stored lines under.
_STORED_LINES = frozenset({"starts", "start_line"})


def _stored_line_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a read of a fragment's stored line outside `collect_fragments`."""
    held = []
    for path in gate.modules() + gate.hook_modules():
        if path.stem == "collect_fragments":
            continue
        name = str(path.relative_to(gate.TREE))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            read: object
            if isinstance(node, ast.Attribute):
                read = node.attr
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                read = node.slice.value
            else:
                continue
            if read in _STORED_LINES:
                at = f"{name}:{node.lineno}"
                held.append((at, f"{at} reads a stored line. `collect_fragments.site_lines` finds it by key."))
    return held


# A `Makefile` assignment with a value and then a comment on the same line.
_AN_ASSIGNMENT_AND_A_COMMENT = re.compile(r"^([A-Za-z0-9_]+)\s*[:?+]?=.*?\S\s+#")


def _make_trailing_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a `Makefile` assignment whose value runs into whitespace and then a comment on the same
    line.

    `make` keeps the whitespace before the hash inside the value, as
    https://www.gnu.org/software/make/manual/html_node/Simple-Assignment.html says. A value spent as a command word
    survives that. A value concatenated into a path breaks. The example on that page builds such a path.
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


# The length a comment inside a function body may run to. A note fits. A passage does not. The `short_comments` hook
# reads this same length.
MOST_COMMENT_LINES = 2

# A line that opens on a field of `self`. A run of comment lines right below a field's trailing comment continues that
# comment.
_A_FIELD = re.compile(r"\s*self\.\w+")


def _comment_runs(source: str) -> list[tuple[int, int]]:
    """
    The runs of consecutive comment lines, as `(the line it opens on, how many lines it runs)`.

    A comment sharing its line with code opens no run. `collect_fragments.python_comments` reads the comments.
    """
    alone = {at for at, (before, _said) in collect_fragments.python_comments(source).items() if not before.strip()}
    runs: list[tuple[int, int]] = []
    for at in sorted(alone):
        if runs and at == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((at, 1))
    return runs


def long_comment_runs(source: str) -> list[tuple[int, int]]:
    """
    `(the line a run opens on, how many lines it runs)` for a comment run that opens inside a function's body and passes
    the length limit.

    A run that `_A_FIELD` joins to a field's trailing comment counts as part of that comment.

    Public. The `short_comments` hook reads an edit through here. A source the parser refuses answers with nothing.
    """
    lines = source.split("\n")
    inside: set[int] = set()
    try:
        tree = ast.parse(source)
        runs = _comment_runs(source)
    except (SyntaxError, tokenize.TokenError, IndentationError):  # not-a-failure: A half-written source holds no fault.
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    held = []
    for first, length in runs:
        before = lines[first - 2] if first >= 2 else ""
        whole = length + 1 if "#" in before and _A_FIELD.match(before) else length
        if whole > MOST_COMMENT_LINES and first in inside:
            held.append((first, whole))
    return held


def long_nested_docstrings(source: str) -> list[tuple[int, str, int]]:
    """
    `(the line, the function, the lines its docstring runs)` for a function nested inside a top-level function whose
    docstring runs past `MOST_COMMENT_LINES`.

    A nested function is no fragment of its own. `collect_fragments` folds its docstring into the enclosing function. A
    docstring worth more than a note therefore belongs to a function that is a fragment of its own. Such a function sits
    at the top level or in a class.

    Public. The `nested_docstrings` checker reads an edit through here. A source the parser refuses answers with
    nothing.

    A docstring on a single line that runs past `gate.WIDEST` counts as a block. The quotes add a line above the text
    and a line below it. `collect_fragments.filled` wraps the text at the docstring's indent.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not-a-failure: A half-written source holds no fault.
        return []
    outer = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    nested = {id(one) for top in outer for one in ast.walk(top) if one is not top}
    held = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or id(node) not in nested:
            continue
        if ast.get_docstring(node) is None:
            continue
        said = node.body[0]
        length = (said.end_lineno or said.lineno) - said.lineno + 1
        if length == 1 and (said.end_col_offset or 0) > gate.WIDEST:
            docstring = ast.get_docstring(node) or ""
            length = 2 + len(collect_fragments.filled(docstring, "", width=gate.WIDEST - said.col_offset))
        if length > MOST_COMMENT_LINES:
            held.add((node.lineno, node.name, length))
    return sorted(held)


# The words a name asks a yes-or-no question with. `ys_are_tokens_stable` is the form the public API uses. The word goes
# anywhere in the name but its end. `ys_queue_is_ready` asks about the queue rather than about `ys`.
_ASKS = re.compile(r"(^|_)(is|has|did|does|are|was|were|can|may|should)_")

# The word that marks a name as a command that runs and may fail. A `bool` from such a call is whether it worked.
_TRIES = re.compile(r"(^|_)try_")

# A C function handing back a `bool`, as a definition or as a declaration. This reads the type and leaves the name to
# `_does_name_match`.
_C_ANSWERS_YES_OR_NO = re.compile(r"^(?:static\s+|YS_API\s+|inline\s+)*bool\s+(\w+)\s*\(", re.M)

# The names a call may take where it hands back a `bool`. A name asks a question, or a name says the call tries.
_A_CALL_MAY_BE_CALLED = (_ASKS, _TRIES)


def _does_name_match(named: str, allowed: Iterable[re.Pattern[str]]) -> bool:
    """Whether `named` has the name a `bool` under it needs."""
    return any(pattern.search(named) for pattern in allowed)


def _c_boolean_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a C function handing back a `bool` under a name which neither asks a question nor says
    it tries.

    A `bool` settles a question, and a question's name asks it. A command that runs and may fail says it tries. The
    `bool` from such a command says whether it worked.
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

    A call hands a `bool` back, a parameter takes a `bool`, and an annotated assignment holds a `bool`. The walk passes
    over the return of `__bool__`, a name Python chooses rather than the author.
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
    `(site, what is wrong)` for a Python `bool` under a name the rule below does not allow.

    The rule is `a-boolean-is-a-pure-question`. This check reads the type off the annotation rather than off the
    declaration. A name holding a `bool` asks a question. A call handing a `bool` back asks a question, or says it
    tries.
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


# The calls that read a source the way Python does. That call numbers its lines by the newline, as
# https://docs.python.org/3/reference/lexical_analysis.html#physical-lines says.
_PARSES_A_SOURCE = frozenset({"parse", "generate_tokens", "StringIO"})


def _parsed_names(holder: ast.AST) -> set[str]:
    """The names a call inside `holder` hands to a parse. `_PARSES_A_SOURCE` names the calls that count as a parse."""
    held: set[str] = set()
    for node in ast.walk(holder):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        asked = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        if asked in _PARSES_A_SOURCE:
            held.update(one.id for one in node.args if isinstance(one, ast.Name))
    return held


def _uncredited_hook_faults(conventions: dict[str, str]) -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a hook whose signed rule goes uncredited. A hook may sign a rule
    `.claude/conventions.md` does not hold. A hook may also enforce a rule whose note does not name the hook.

    A hook refusal names the rule it enforces. `_uncited_convention_faults` reads the other direction, from the note to
    the module.
    """
    held = {}
    for name, body in conventions.items():
        note = _A_MECHANISED_NOTE.search(body)
        held[name] = note.group(1) if note else ""
    faults = []
    for path in gate.hooks():
        at = str(path.relative_to(gate.TREE))
        for name in sorted(set(_A_SIGNED_RULE.findall(path.read_text(encoding="utf-8")))):
            if name not in held:
                faults.append((at, f"{at} signs `{name}`, and `.claude/conventions.md` holds no such rule"))
            elif path.stem not in held[name]:
                faults.append((at, f"{at} enforces `{name}`, and that rule's note leaves the hook uncredited"))
    return faults


def _tree_text() -> dict[str, str]:
    """
    `{the path: the text}` over the files that hold the project's prose.
    """
    if not _TREE_TEXT:
        for path in gate.prose_files():
            _TREE_TEXT[str(path.relative_to(gate.TREE))] = path.read_text(encoding="utf-8")
    return _TREE_TEXT


def _listed_names(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    """
    `(the name bound, the line, the names listed)` for a module-level literal tuple, set or list. The literal holds
    `_FEWEST_LISTED` strings or more. `_listed_names` skips a literal holding a string `_A_MATCHED_NAME` refuses.
    """
    held = []
    for node in tree.body:
        targets: list[ast.expr]
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        else:
            continue
        if not isinstance(value, (ast.Tuple, ast.Set, ast.List)):
            continue
        words = [one.value for one in value.elts if isinstance(one, ast.Constant) and isinstance(one.value, str)]
        if len(words) < _FEWEST_LISTED or not all(_A_MATCHED_NAME.match(word) for word in words):
            continue
        bound = next((one.id for one in targets if isinstance(one, ast.Name)), "")
        held.append((bound, node.lineno, words))
    return held


def _unmatched_name_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a literal list of names holding a name the tree writes in a single place.

    A check matches source against such a list. A name whose single occurrence is the list itself is a guess about code
    that does not exist. The arm covering that name reads exactly like an arm that fires.
    """
    texts = _tree_text()
    held = []
    for path in gate.modules() + gate.hook_modules():
        rel = str(path.relative_to(gate.TREE))
        for bound, line, words in _listed_names(ast.parse(path.read_text(encoding="utf-8"))):
            lonely = []
            for word in words:
                pattern = rf"\b{re.escape(word)}\b"
                beside = sum(len(re.findall(pattern, said)) for name, said in texts.items() if name != rel)
                own = len(re.findall(pattern, texts.get(rel, "")))
                if not beside and own <= 1:
                    lonely.append(word)
            if lonely:
                at = f"{rel}:{line}"
                held.append(
                    (at, f"{at} `{bound}` lists {', '.join(lonely)}, and the tree writes those in a single place")
                )
    return held


def _gate_calls(tree: ast.Module) -> set[str]:
    """The names a module calls on `gate`."""
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "gate"
    }


def _unwalked_hook_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a gate that walks the modules and leaves the hooks out in silence.

    The hooks are Python this project maintains, and the same faults land in them. A reader cannot tell a gate that
    picked the narrower roster from a gate that forgot the wider one. A gate calling `gate.modules()` and skipping
    `gate.hook_modules()` therefore says in its docstring why the hooks fall outside.
    """
    held = []
    for path in gate.modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = _gate_calls(tree)
        if "modules" not in called or "hook_modules" in called:
            continue
        if _A_HOOK.search(ast.get_docstring(tree) or ""):
            continue
        at = str(path.relative_to(gate.TREE))
        held.append((at, f"{at} walks `gate.modules()` and says nothing about the hooks"))
    return held


def _own_payload_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a hook that builds the tool's answer payload rather than calling the shared helper.

    The payload's keys are a contract with the tool, and https://code.claude.com/docs/en/hooks states that contract. A
    copy with a wrong key prints nothing, and the tool reads that as an edit that passed. `refusal.py` holds the payload
    for a hook written in Python. `.claude/hooks/refusal.sh` holds it for a hook written in shell. A helper naming its
    own keys is the helper rather than a copy.
    """
    held = []
    for path in gate.hooks():
        if path.stem in _THE_REFUSAL_HELPERS:
            continue
        said = path.read_text(encoding="utf-8")
        named = sorted(key for key in _A_PAYLOAD_KEY if key in said)
        if not named:
            continue
        at = str(path.relative_to(gate.TREE))
        held.append((at, f"{at} names {', '.join(named)} rather than calling the refusal helper beside it"))
    return held


def _split_source_faults() -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` per `splitlines` call on a name that its function also hands to a parse.
    `a-parsed-source-is-split-on-the-newline` says why.
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


# The note saying how far a module mechanises a convention. The note is an italic run inside its item.
_A_MECHANISED_NOTE = re.compile(r"\*((?:Not\s+)?[Mm]echanised[^*]*)\*")

# The opening of a note saying the tree leaves a rule to a reader. `_mechanised_by` reads no module out of such a note.
_UNMECHANISED = "Not "


def _mechanised_by(conventions: dict[str, str]) -> dict[str, set[str]]:
    """
    The conventions `conventions` marks mechanised, against the modules and the hooks of this tree their notes name.
    """
    known = {path.stem for path in gate.modules() + gate.hook_modules()}
    held = {}
    for name, body in conventions.items():
        note = _A_MECHANISED_NOTE.search(body)
        if note and not " ".join(note.group(1).split()).startswith(_UNMECHANISED):
            held[name] = {one for one in re.findall(r"`([A-Za-z_][\w.]*)`", note.group(1)) if one in known}
    return held


def mechanised_rules() -> set[str]:
    """
    The conventions a checker decides. A note opening on `Not` keeps its rule out.

    Public. `write_agent_prompts` leaves such a rule out of the definitions of the agents that judge prose.
    `agent_answers` leaves it out of the rules a writer of this project breaks. A reader deciding nothing reads nothing
    about such a rule.
    """
    return set(_mechanised_by(gate.conventions()))


def _unstated_mechanism_faults(conventions: dict[str, str]) -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a convention whose item holds no note about the gate deciding it.

    A note says which gate decides the rule, or that a reader does. A rule with no note leaves that undecided.
    """
    held = []
    for name, body in conventions.items():
        if _A_MECHANISED_NOTE.search(body):
            continue
        at = f"`.claude/conventions.md`: {name}"
        held.append((at, f"{at} closes on no note. A note names the gate deciding the rule, or names a reader"))
    return held


def _uncited_convention_faults(conventions: dict[str, str]) -> list[tuple[str, str]]:
    """`(site, what is wrong)` for a module that mechanises a convention and does not name it."""
    docstrings = {
        path.stem: ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        for path in gate.modules() + gate.hook_modules()
    }
    held = []
    for name, modules in sorted(_mechanised_by(conventions).items()):
        for stem in sorted(modules):
            if f"`{name}`" in docstrings[stem]:
                continue
            at = f"`.claude/conventions.md`: {name}"
            held.append(
                (
                    f"{at} in {stem}",
                    f"{at} names `{stem}` as its mechanism, and that module's docstring does not name "
                    f"the rule back",
                )
            )
    return held


def _note_faults(conventions: dict[str, str]) -> list[tuple[str, str]]:
    """
    `(site, what is wrong)` for a rule of `conventions` whose note is missing, or disagrees with the modules and hooks
    of the tree. A hook signing a rule `conventions` does not hold is a fault too.
    """
    return (
        _uncited_convention_faults(conventions)
        + _unstated_mechanism_faults(conventions)
        + _uncredited_hook_faults(conventions)
    )


def convention_note_faults(path: str, text: str) -> list[str]:
    """
    The faults of the notes in `text`, where `path` names the conventions file. Any other path takes no such reading.

    Public. The `document_rules` checker reads an edit through here.
    """
    if path != ".claude/conventions.md":
        return []
    return [said for _site, said in _note_faults(gate.conventions_in(text))]


def _check() -> None:
    """Report the broken conventions."""
    found = (
        _uncached_question_faults()
        + _repeated_word_faults()
        + _bare_private_faults()
        + _banned_word_name_faults()
        + _enumeration_faults()
        + _stored_line_faults()
        + _own_payload_faults()
        + _unwalked_hook_faults()
        + _unmatched_name_faults()
        + _split_source_faults()
        + _c_boolean_faults()
        + _python_boolean_faults()
        + _make_trailing_faults()
    )
    gate.report(
        [said for _site, said in found],
        "broken convention(s). Fix the site",
        "conventions: the tree satisfies the rules a gate can decide",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
