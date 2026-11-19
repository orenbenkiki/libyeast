#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
A checker of `checkers`. This checker refuses a pipeline step whose argument is missing. `a-step-opens-its-argument` is
the rule.

A step of `generator/normalize.py` is a `_Step(` call. The comment block directly above that call holds the step's
argument. The argument answers the rules a pipeline step must satisfy. `generator/normalize.py` holds those rules. A
script cannot decide whether the argument is right.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import ast
import os

# The file declaring `STEPS`, the steps of the pipeline.
_THE_PIPELINE = "generator/normalize.py"


def _unargued(source: str) -> set[str]:
    """
    The names of the steps in `source` with no comment directly above the `_Step(` call. A step's name is the text
    `ast.unparse` gives for its first argument. A call without arguments gets the name `line N`.
    """
    lines = source.split("\n")
    held = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_Step"):
            continue
        if node.lineno < 2 or not lines[node.lineno - 2].lstrip().startswith("#"):
            held.add(ast.unparse(node.args[0]) if node.args else f"line {node.lineno}")
    return held


def unargued_of(source: str, path: str) -> tuple[str, ...]:
    """
    The steps of `source` that hold no argument. A path other than the pipeline holds none.

    Public. `checkers` fills its readings of a file through here.
    """
    if not os.path.normpath(path).endswith(os.path.normpath(_THE_PIPELINE)):
        return ()
    return tuple(sorted(_unargued(source)))


def refusal_for(source: str, path: str, unargued: tuple[str, ...] | None = None) -> str | None:
    """
    The refusal the argument rule gives at `path`, or None where `unargued_of` finds no step without an argument.

    `unargued` comes from `unargued_of`. A caller that gives none has `unargued_of` read `source`.
    """
    found = unargued_of(source, path) if unargued is None else unargued
    if not found:
        return None
    return (
        f"This edit leaves a pipeline step with no argument in {path}: {'; '.join(found)}.\n\n"
        f"Read the rules a pipeline step must satisfy, in {_THE_PIPELINE}. Write the argument in a comment directly "
        "above the step. The argument names the invariant the step establishes, settles or reduces, or says why it "
        "names none. It says that the transform reads its own production's nodes and the grammar-wide tables. It says "
        "that a value the step produces is a global or an entry in the unified stack.\n\n"
        "Rule: a-step-opens-its-argument."
    )
