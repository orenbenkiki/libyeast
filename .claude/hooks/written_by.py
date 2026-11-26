#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Say whether a Python script writes a file. `no-shell-file-writes.sh` asks this of a script its `declared` list leaves
out.

A function writes where its body opens a file for writing. A function also writes where it calls a function that writes.
The call may reach a function of the same module or a function of a module of the tree. The code of a script includes
the functions the script defines. A script writes where that code writes. A script calling `claim_state.claims_written`
writes the claim store, and its own text opens nothing.

The modules of the tree sit under the directories `_SEARCHED` names. A module outside them is no module of the tree.

**Usage:** `python3 .claude/hooks/written_by.py <script>`. The command exits `0` and names the write where the script
writes. It exits `1` where the script writes nothing. A script that fails to parse makes the command exit `2`.
"""

import ast
import functools
import os
import re
import shlex
import sys

# The directories under the tree that hold its modules.
_SEARCHED = ("generator", ".claude/hooks", ".claude/mcp", "scripts")

# The tree, as the directory above `.claude`.
_TREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The `pathlib` methods that write a file, as https://docs.python.org/3/library/pathlib.html lists them.
_WRITING_METHODS = ("write_text", "write_bytes")

# The errors of a script or a command that reads as no Python. `open` raises `OSError` for a missing file, as
# https://docs.python.org/3/library/functions.html#open says. `shlex` raises `ValueError` on quoting it cannot split, as
# https://github.com/python/cpython/blob/main/Lib/shlex.py shows.
_UNREADABLE = (OSError, SyntaxError, UnicodeDecodeError, ValueError)

# A heredoc handing code to Python on its standard input. The first group names the marker, and the second holds the
# code.
_A_HEREDOC = re.compile(
    r"python3?\s+-\s[^\n]*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1\s*$", re.DOTALL | re.MULTILINE
)


def _inline_codes(command: str) -> list[str]:
    """
    The Python code `command` hands over inline. A `-c` after `python` or `python3` hands over the argument behind it. A
    heredoc to `python -` or `python3 -` hands over its body. A command whose quoting `shlex` cannot split raises
    `ValueError`, as https://github.com/python/cpython/blob/main/Lib/shlex.py shows.
    """
    # `punctuation_chars` makes `;`, `|` and `&` words of their own.
    splitter = shlex.shlex(command, posix=True, punctuation_chars=True)
    splitter.whitespace_split = True
    words = list(splitter)
    codes = [
        words[at + 2]
        for at, word in enumerate(words[:-2])
        if os.path.basename(word) in ("python", "python3") and words[at + 1] == "-c"
    ]
    return codes + [found.group(2) for found in _A_HEREDOC.finditer(command)]


def _module_path(name: str) -> str | None:
    """The file of the module `name` under `_SEARCHED`, or `None` where no directory there holds it."""
    for one in _SEARCHED:
        path = os.path.join(_TREE, one, f"{name}.py")
        if os.path.exists(path):
            return path
    return None


def _is_opening_to_write(call: ast.Call) -> bool:
    """Whether `call` is an `open` with a mode that writes, appends or creates."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "open"):
        return False
    modes = [one.value for one in call.keywords if one.arg == "mode"] + call.args[1:2]
    return any(isinstance(mode, ast.Constant) and set(str(mode.value)) & set("wax+") for mode in modes)


@functools.cache
def _parsed(path: str) -> tuple[dict[str, ast.AST], dict[str, str], ast.Module]:
    """The parse of the file `path`, as `_parsed_text` gives it."""
    with open(path, encoding="utf-8") as handle:
        return _parsed_text(handle.read(), path)


def _parsed_text(source: str, name: str) -> tuple[dict[str, ast.AST], dict[str, str], ast.Module]:
    """
    The parse of `source`. A function `source` defines maps to its node. A local name maps to the module of the tree
    `source` imports under it. A name imported from such a module maps to `module.name`. The parsed module comes last.
    `name` names `source` in a syntax error.
    """
    tree = ast.parse(source, name)
    functions: dict[str, ast.AST] = {
        function.name: function
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for one in node.names:
                if _module_path(one.name):
                    imported[one.asname or one.name] = one.name
        elif isinstance(node, ast.ImportFrom) and node.module and _module_path(node.module):
            for one in node.names:
                imported[one.asname or one.name] = f"{node.module}.{one.name}"
    return functions, imported, tree


def _writes(path: str, body: ast.AST, seen: frozenset[str]) -> str | None:
    """The write `body` reaches, as `_writes_in` gives it. The file `path` holds `body`."""
    functions, imported, _tree = _parsed(path)
    return _writes_in(path, body, seen, functions, imported)


def _writes_in(
    path: str, body: ast.AST, seen: frozenset[str], functions: dict[str, ast.AST], imported: dict[str, str]
) -> str | None:
    """
    The write `body` reaches, as the call that makes it, or `None` where `body` reaches none. `path` names the source
    holding `body`. `functions` and `imported` come from the parse of that source. `seen` holds the functions the walk
    has entered. A call back into `seen` adds nothing.
    """
    for node in ast.walk(body):
        if not isinstance(node, ast.Call):
            continue
        if _is_opening_to_write(node):
            return f"{os.path.relpath(path, _TREE)}:{node.lineno} opens a file for writing"
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WRITING_METHODS:
            return f"{os.path.relpath(path, _TREE)}:{node.lineno} calls `{func.attr}`"
        if isinstance(func, ast.Name) and func.id in functions:
            where = f"{path}:{func.id}"
            found = None if where in seen else _writes_in(path, functions[func.id], seen | {where}, functions, imported)
            if found:
                return found
            continue
        target = None
        if isinstance(func, ast.Name) and "." in imported.get(func.id, ""):
            module, _, name = imported[func.id].partition(".")
            target = (_module_path(module), name)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in imported:
            target = (_module_path(imported[func.value.id]), func.attr)
        if target is None or target[0] is None:
            continue
        where = f"{target[0]}:{target[1]}"
        callee = _parsed(target[0])[0].get(target[1])
        if callee is None or where in seen:
            continue
        found = _writes(target[0], callee, seen | {where})
        if found:
            return found
    return None


def _inline_writes(command: str) -> str | None:
    """
    The write the inline code of `command` reaches, or `None` where that code reaches none. The code runs from the
    working directory, and an import there reaches the modules of the tree the way a script's import does.
    """
    for code in _inline_codes(command):
        functions, imported, tree = _parsed_text(code, "<inline>")
        found = _writes_in(os.path.join(_TREE, "<inline>"), tree, frozenset(), functions, imported)
        if found:
            return found
    return None


def main() -> None:
    """
    Exit `0` and print the write where the code writes. Exit `1` where it writes nothing. Exit `2` on a wrong count of
    arguments. Exit `2` on any error in `_UNREADABLE` as well. A missing script raises one. The command line names a
    script, or `--command` reads a shell command off the standard input.
    """
    if len(sys.argv) != 2:
        print("usage: written_by.py <script> | written_by.py --command", file=sys.stderr)
        sys.exit(2)
    try:
        if sys.argv[1] == "--command":
            found = _inline_writes(sys.stdin.read())
        else:
            found = _writes(sys.argv[1], _parsed(sys.argv[1])[2], frozenset())
    except _UNREADABLE as caught:  # failure-is-reported: The hook refuses with exit status `2`.
        print(f"{sys.argv[1]} reads as no Python: {caught}", file=sys.stderr)
        sys.exit(2)
    if found is None:
        sys.exit(1)
    print(found)


if __name__ == "__main__":
    main()
