# SPDX-License-Identifier: MIT
"""Translate libyeast's annotated grammar into the typed IR (the IR).

Faithful 1:1: every yaml-grammar operator maps to exactly one IR node, with no flattening or normalization, so the
round-trip through `ir2annotated.py` stays exact. Anything unexpected raises loudly rather than being dropped silently.

Usage: `python3 generator/annotated2ir.py [spec.yaml] > grammar.py`
"""

import os
import re
import sys

import ir

import yaml

# The tree this generator belongs to, so that a script finds the grammar wherever it is run from and not only from the
# root of the tree.
TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GRAMMAR = os.path.join(TREE, "grammar", "yeast-spec-1.2.yaml")

PARAMS = frozenset({"n", "c", "m", "t"})
HEX = re.compile(r"^x[0-9A-Fa-f]+$")
REP = re.compile(r"^\(\{(.+)\}\)$")  # ({2}) / ({n})
INT = re.compile(r"^-?[0-9]+$")
SPECIAL = re.compile(r"^<(.+)>$")  # <empty>, <start-of-line>, <end-of-stream>, <auto-detect-indent>
SPECIALS = {
    "empty": ir.Empty,
    "start-of-line": ir.StartOfLine,
    "end-of-stream": ir.EndOfStream,
    "auto-detect-indent": ir.AutoDetectIndent,
    "auto-detect-in-line-indent": ir.AutoDetectInLineIndent,
}


def special(x):
    """The marker node for a `<...>` token, or None if `x` is not one."""
    marker = SPECIAL.match(x) if isinstance(x, str) else None
    if marker is None:
        return None
    if marker.group(1) not in SPECIALS:
        raise ValueError(f"unknown special token {x!r}")
    return SPECIALS[marker.group(1)]()


def char(text):
    """A quoted character or `xHH` hex codepoint becomes a Char."""
    if HEX.match(text):
        return ir.Char(int(text[1:], 16))
    return ir.Char(ord(text))


def is_char(x):
    return isinstance(x, str) and (HEX.match(x) is not None or len(x) == 1)


def count(spec):
    """Parse the `({N})` count: a digit literal or a parameter name."""
    return ir.Lit(int(spec)) if INT.match(spec) else ir.Param(spec)


def args(value):
    """Argument list of a production reference: a list of expressions, or a single expression."""
    return tuple(expr(a) for a in value) if isinstance(value, list) else (expr(value),)


def branches(mapping, translate):
    """The value-keyed branches of a `(case)`/`(flip)`, minus the `var` selector."""
    return tuple(ir.Branch(key, translate(val)) for key, val in mapping.items() if key != "var")


def expr(x):
    """Translate a value/parameter expression."""
    if isinstance(x, dict):
        ((op, value),) = x.items()
        if op == "(+)":
            return ir.Add(expr(value[0]), expr(value[1]))
        if op == "(-)":
            return ir.Sub(expr(value[0]), expr(value[1]))
        if op == "(ord)":
            return ir.Ord(expr(value))
        if op == "(len)":
            return ir.Len(expr(value))
        if op == "(flip)":
            return ir.Flip(value["var"], branches(value, expr))
        return ir.Ref(op, args(value))  # a function call, e.g. {in-flow: c}
    if x is None:
        return ir.Lit(None)
    if isinstance(x, int):
        return ir.Lit(x)
    if x == "(match)":
        return ir.Match()
    if x in PARAMS:
        return ir.Param(x)
    if x == "null":
        return ir.Lit(None)
    if INT.match(x):
        return ir.Lit(int(x))
    marker = special(x)
    if marker is not None:
        return marker
    return ir.Lit(x)  # a value string, e.g. "block-in", "auto-detect"


def node(x):
    """Translate a grammar node (a matcher)."""
    if isinstance(x, list):
        if len(x) == 2 and is_char(x[0]) and is_char(x[1]):
            return ir.Range(char(x[0]).cp, char(x[1]).cp)
        raise ValueError(f"unexpected bare list in grammar position: {x!r}")
    if isinstance(x, dict):
        if set(x) == {"(if)", "(set)"}:
            target, value = x["(set)"]
            return ir.Bind(node(x["(if)"]), target, expr(value))
        ((op, value),) = x.items()
        if op == "(all)":
            return ir.Seq(tuple(node(i) for i in value))
        if op == "(any)":
            return ir.Alt(tuple(node(i) for i in value))
        if op == "(***)":
            return ir.Star(node(value))
        if op == "(+++)":
            return ir.Plus(node(value))
        if op == "(???)":
            return ir.Opt(node(value))
        if op == "(===)":
            return ir.Look(node(value))
        if op == "(!==)":
            return ir.NegLook(node(value))
        if op == "(<==)":
            return ir.LookBehind(node(value))
        if op == "(<<<)":
            return ir.Bound(node(value))
        if op == "(---)":
            return ir.Diff(node(value[0]), tuple(node(i) for i in value[1:]))
        if op == "(exclude)":
            return ir.ExcludeAt(node(value))
        if op == "(set)":
            return ir.SetVar(value[0], expr(value[1]))
        if op == "(max)":
            return ir.Max(expr(value))
        if op == "(<)":
            return ir.Lt(expr(value[0]), expr(value[1]))
        if op == "(<=)":
            return ir.Le(expr(value[0]), expr(value[1]))
        if op == "(case)":
            return ir.Case(value["var"], branches(value, node))
        if op == "(flip)":
            return ir.Flip(value["var"], branches(value, expr))
        if op == "(token)":
            return ir.Token(value[0], node(value[1]))
        if op == "(wrap)":
            return ir.Wrap(value[0], value[1], node(value[2]))
        if op == "(emit)":
            return ir.Emit(value)
        if op == "(cut)":
            return ir.Cut(value)
        if op == "(error)":
            return ir.Error(value)
        rep = REP.match(op)
        if rep:
            return ir.Rep(count(rep.group(1)), node(value))
        if op.startswith("("):
            raise ValueError(f"unhandled grammar operator {op!r}")
        return ir.Ref(op, args(value))  # a single non-operator key: a parameterized reference
    marker = special(x)
    if marker is not None:
        return marker
    if is_char(x):
        return char(x)
    if isinstance(x, str):
        return ir.Ref(x)  # a bare production name
    raise ValueError(f"unexpected grammar scalar: {x!r}")


def production(number, name, definition):
    """Translate one production, splitting its `(...)` parameter declaration from its body."""
    params = ()
    if isinstance(definition, dict) and "(...)" in definition:
        declared = definition["(...)"]
        params = tuple(declared) if isinstance(declared, list) else (declared,)
        definition = {k: v for k, v in definition.items() if k != "(...)"}
    return ir.Prod(number, name, params, node(definition))


def translate(grammar):
    """Translate the loaded yaml-grammar mapping into an ordered `{name: Prod}` dict."""
    return {name: production(int(key[1:]), name, grammar[name]) for key, name in grammar.items() if key.startswith(":")}


def load(source=DEFAULT_GRAMMAR):
    """Load and translate libyeast's grammar."""
    with open(source) as handle:
        return translate(yaml.safe_load(handle))


def main():
    productions = load(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GRAMMAR)
    out = sys.stdout
    out.write("# Generated by annotated2ir.py from the vendored yaml-grammar. Do not edit.\n")
    out.write("from ir import *  # noqa: F401,F403\n\n")
    out.write("GRAMMAR = {\n")
    for name, prod in productions.items():
        out.write(f"    {name!r}: {prod!r},\n")
    out.write("}\n")


if __name__ == "__main__":
    main()
