#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker refuses a test of a node's kind. A total `ir.Question` looks at the kind instead.
`a-kind-dispatch-raises` is the rule.

A dispatch looks at a node's kind. A selection and a rewrite look at it too. Somebody adds a kind to `ir`, and a place
that looks at the kind then owes a decision about it. A total `ir.Question` raises on the new kind until somebody makes
that decision. An `isinstance` test lets the new kind through silently.

The checker reads the syntax tree of a module of the generator, the scripts or the hooks. An `isinstance` call whose
second argument names a kind or a family of `ir` is a fault. The argument may write `ir.SeqTree` or a bare `SeqTree`. An
`isinstance` call against a parameter annotated as a `type` is a fault too. So is a `type()` comparison that names a
kind. A test against `ir.Node` names no kind. Such a test asks whether a value is a node at all, and this checker passes
it.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import ast
import os
import re
from typing import TypeGuard

import gate
import ir

# The names a test may write for a kind or a family of kinds. `ir.KINDS` leaves out `ir.Node`, and this set leaves it
# out too.
_KIND_NAMES = frozenset(
    name
    for name, value in vars(ir).items()
    if (isinstance(value, type) and value in ir.KINDS)
    or (isinstance(value, tuple) and value and all(one in ir.KINDS for one in value))
)


def _names_in(argument: ast.AST) -> set[str]:
    """The names `argument` writes, bare or behind `ir.`, and inside a tuple or a starred family."""
    if isinstance(argument, ast.Tuple):
        return {name for one in argument.elts for name in _names_in(one)}
    if isinstance(argument, ast.Starred):
        return _names_in(argument.value)
    if isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name) and argument.value.id == "ir":
        return {argument.attr}
    if isinstance(argument, ast.Name):
        return {argument.id}
    return set()


def _is_a_call_of(node: ast.AST, called: str) -> TypeGuard[ast.Call]:
    """Whether `node` calls the bare name `called`."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == called


def _typed_as_a_type(top: ast.AST) -> set[str]:
    """
    The parameters of the functions inside `top` whose annotation names `type`. Such a name holds a kind. The annotation
    may name `type` inside a union, and the parameter may be a `*` or a `**` one.
    """
    return {
        argument.arg
        for function in ast.walk(top)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
            *(held for held in (function.args.vararg, function.args.kwarg) if held is not None),
        )
        if argument.annotation is not None and re.search(r"\btype\b", ast.unparse(argument.annotation))
    }


def _does_test_a_kind(node: ast.AST, passed: set[str]) -> TypeGuard[ast.Call | ast.Compare]:
    """
    Whether `node` tests a kind of `ir`. An `isinstance` call naming a kind is such a test. A parameter of `passed`
    holds a kind a caller hands in. An `isinstance` call naming such a parameter is such a test too. A comparison
    holding a `type(` call and naming a kind is such a test as well.
    """
    if _is_a_call_of(node, "isinstance"):
        return len(node.args) == 2 and bool(_names_in(node.args[1]) & (_KIND_NAMES | passed))
    if not isinstance(node, ast.Compare):
        return False
    held = [node.left, *node.comparators]
    is_typed = any(_is_a_call_of(inner, "type") for side in held for inner in ast.walk(side))
    return is_typed and any(_names_in(inner) & _KIND_NAMES for side in held for inner in ast.walk(side))


def _kind_tests(source: str) -> list[str]:
    """
    `name:line` per test of a kind of `ir` in `source`, keyed by the top-level definition, or by `line` for a top-level
    statement with no name.
    """
    held = []
    for top in ast.parse(source).body:
        passed = _typed_as_a_type(top)
        held += [
            f"{getattr(top, 'name', 'line')}:{node.lineno}" for node in ast.walk(top) if _does_test_a_kind(node, passed)
        ]
    return held


def dispatches_of(source: str, path: str) -> tuple[str, ...]:
    """
    The tests of a kind `source` writes. A path holds none unless its directory ends in a directory of `gate.modules` or
    `gate.hook_modules`. A probe of `check_hooks` sits in such a directory under a scratch directory.

    Public. `checkers` fills its readings of a file through here.
    """
    read = {os.path.relpath(module.parent, gate.TREE) for module in (*gate.modules(), *gate.hook_modules())}
    held = os.sep + os.path.dirname(os.path.normpath(path))
    if not (path.endswith(".py") and any(held.endswith(os.sep + one) for one in read)):
        return ()
    return tuple(_kind_tests(source))


def refusal_for(source: str, path: str, dispatches: tuple[str, ...] | None = None) -> str | None:
    """
    The refusal the kind rule gives at `path`, or None where `dispatches_of` finds no test of a kind.

    `dispatches` comes from `dispatches_of`. A caller that gives none has `dispatches_of` read `source`.
    """
    found = dispatches_of(source, path) if dispatches is None else dispatches
    if not found:
        return None
    return (
        f"This edit leaves a test of the kind of a node in {path}: {'; '.join(found)}.\n\n"
        "A total `ir.Question` names the kinds that reach it, and raises on a kind it does not name. A kind somebody "
        "adds then raises here until somebody decides what this place does with it. Build the question from the "
        "families of `ir`.\n\n"
        "Rule: a-kind-dispatch-raises."
    )
