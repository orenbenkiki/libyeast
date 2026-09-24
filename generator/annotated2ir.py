# SPDX-License-Identifier: MIT
"""
Translate libyeast's annotated grammar into the typed IR (the IR).

A yaml-grammar operator maps to a single IR node. The translation neither flattens nor normalizes. The round-trip
through `ir2annotated.py` stays exact. The translation raises an error on an unexpected input rather than dropping that
input.

Usage: `python3 generator/annotated2ir.py [spec.yaml] > grammar.py`.
"""

import os
import re
import sys
from collections.abc import Callable, Mapping
from typing import TypeGuard

import gate
import ir

import yaml

# Read off `gate.TREE`. A script then finds the grammar from wherever the caller runs it, and not just from the root of
# the tree. The root has a single answer here rather than an answer per module.
DEFAULT_GRAMMAR = os.path.join(gate.TREE, "grammar", "yeast-spec-1.2.yaml")

# The grammar's parameters. `n` and `m` and `f` are indentations. `c` is a context and `t` a chomping mode. `r` is the
# resume policy. `i` says whether a block scalar's indicator gives the indentation or the first content line owes it. A
# bare name among these is that parameter. Any other bare name is the value it names.
PARAMS = frozenset({"c", "f", "i", "m", "n", "r", "t"})  # in alphabetical order.

# The values of the finite parameters. Generation specializes those parameters away. A gate holding the grammar to those
# values enumerates them here. `n` and `m` are indentations and have no such list. `r` reaches as far as the policies
# the build knows, and no further.
CONTEXTS = ("block-in", "block-out", "block-key", "flow-in", "flow-out", "flow-key")
CHOMPINGS = (
    "strip",
    "clip",
    "keep",
)  # the values `t` may take. The chomping mode decides how a block scalar treats its trailing breaks.
RESUMES = ("n", "d", "i")  # the values `r` may take. That policy governs a flow scalar's continuation lines.
INDENT_MODES = ("given", "detected")  # whether a block scalar's indicator gave the indentation or the content says it.
_HEX = re.compile(r"^x[0-9A-Fa-f]+$")  # a character written as its codepoint `xHH`.
_REP = re.compile(r"^\(\{(.+)\}\)$")  # a repetition written `({2})` or `({n})`.
_INT = re.compile(r"^-?[0-9]+$")  # a count written as a number. A parameter name is not that.
_SPECIAL = re.compile(r"^<(.+)>$")  # <empty>, <fail>, <start-of-line>, <end-of-stream>, <auto-detect-indent>.
_SPECIALS: dict[str, type[ir.Node]] = {
    "empty": ir.EmptyTree,
    "fail": ir.FailTree,
    "start-of-line": ir.StartOfLineGuard,
    "end-of-stream": ir.EndOfStreamGuard,
    "invalid": ir.InvalidSet,
    "column": ir.ColumnValue,
    # the vendored grammar writes this and libyeast's grammar leaves it out.
    "auto-detect-indent": ir.AutoDetectIndentValue,
}


def _special(x: object) -> ir.Node | None:
    """The marker node for a `<...>` token, or None where `x` is no such token."""
    marker = _SPECIAL.match(x) if isinstance(x, str) else None
    if marker is None:
        return None
    if marker.group(1) not in _SPECIALS:
        raise ValueError(f"unknown special token {x!r}")
    return _SPECIALS[marker.group(1)]()


def _char(text: str) -> ir.OneCharSet:
    """A quoted character or `xHH` hex codepoint becomes a `OneCharSet`."""
    if _HEX.match(text):
        return ir.OneCharSet(int(text[1:], 16))
    return ir.OneCharSet(ord(text))


def _is_char(x: object) -> TypeGuard[str]:
    """Whether `x` is a single character or an `xHH` hex codepoint. `char` translates those forms."""
    return isinstance(x, str) and (_HEX.match(x) is not None or len(x) == 1)


def _count(spec: str) -> ir.Node:
    """Parse the `({N})` count. A digit literal or a parameter name."""
    return ir.LitValue(int(spec)) if _INT.match(spec) else ir.ParamValue(spec)


def _args(value: object) -> tuple[ir.Node, ...]:
    """A production reference passes its arguments as a list of expressions or as a single expression."""
    return tuple(_expr(a) for a in value) if isinstance(value, list) else (_expr(value),)


def _branches(mapping: Mapping[str, object], translated: Callable[[object], ir.Node]) -> tuple[ir.BranchPart, ...]:
    """The value-keyed branches of a `(case)`/`(flip)`, minus the `var` selector and a `(case)`'s `else` default."""
    return tuple(ir.BranchPart(key, translated(value)) for key, value in mapping.items() if key not in ("var", "else"))


def _expr(x: object) -> ir.Node:
    """Translate a value expression or a parameter expression."""
    if isinstance(x, dict):
        ((op, value),) = x.items()
        if op == "(+)":
            return ir.AddValue(_expr(value[0]), _expr(value[1]))
        if op == "(-)":
            return ir.SubValue(_expr(value[0]), _expr(value[1]))
        if op == "(atoi)":
            return ir.AtoiValue(_expr(value))
        # The vendored grammar's own form. `(ord)` is a single character there and `(atoi)` a whole string here. The two
        # agree at the one digit it is applied to.
        if op == "(ord)":
            return ir.AtoiValue(_expr(value))
        if op == "(len)":
            return ir.LenValue(_expr(value))
        if op == "(flip)":
            return ir.FlipValue(value["var"], _branches(value, _expr))
        return ir.RefCall(op, _args(value))  # a function call, e.g. {in-flow: c}
    if x is None:
        return ir.LitValue(None)
    if isinstance(x, int):
        return ir.LitValue(x)
    if not isinstance(x, str):
        raise ValueError(f"unexpected grammar scalar: {x!r}")
    if x == "(match)":
        return ir.MatchValue()
    if x in PARAMS:
        return ir.ParamValue(x)
    if x == "null":
        return ir.LitValue(None)
    if _INT.match(x):
        return ir.LitValue(int(x))
    marker = _special(x)
    if marker is not None:
        return marker
    return ir.LitValue(x)  # a value string, e.g. "block-in", "auto-detect"


def _node(x: object) -> ir.Node:
    """Translate a grammar node (a matcher)."""
    if isinstance(x, list):
        if len(x) == 2 and _is_char(x[0]) and _is_char(x[1]):
            return ir.RangeSet(_char(x[0]).cp, _char(x[1]).cp)
        raise ValueError(f"unexpected bare list in grammar position: {x!r}")
    if isinstance(x, dict):
        if set(x) == {"(if)", "(set)"}:
            target, value = x["(set)"]
            return ir.BindTree(_node(x["(if)"]), target, _expr(value))
        ((op, value),) = x.items()
        if op == "(all)":
            return ir.SeqTree(tuple(_node(i) for i in value))
        if op == "(any)":
            return ir.AltTree(tuple(_node(i) for i in value))
        if op == "(***)":
            return ir.StarTree(_node(value))
        if op == "(+++)":
            return ir.PlusTree(_node(value))
        if op == "(???)":
            return ir.OptTree(_node(value))
        if op == "(===)":
            return ir.LookGuard(_node(value))
        if op == "(!==)":
            return ir.NegLookGuard(_node(value))
        if op == "(<==)":
            return ir.LookBehindGuard(_node(value))
        # The vendored form. Match `value`, giving nothing back if a predicate within it then fails. Its two uses wrap a
        # repetition, which matches possessively here and so hands back nothing already.
        if op == "(<<<)":
            return _node(value)
        if op == "(---)":
            return ir.DiffSet(_node(value[0]), tuple(_node(i) for i in value[1:]))
        if op == "(exclude)":
            return ir.ExcludeAtAction(_node(value))
        if op == "(set)":
            return ir.SetVarAction(value[0], _expr(value[1]))
        if op == "(increase)":
            return ir.IncreaseAction(value)
        if op == "(max)":
            if isinstance(value, list):  # libyeast wraps a production, the vendored grammar precedes it
                return ir.MaxWrapper(_expr(value[0]), value[1], _node(value[2]))
            return ir.MaxWrapper(_expr(value))
        if op == "(<)":
            return ir.IsLessThanGuard(_expr(value[0]), _expr(value[1]))
        if op == "(<=)":
            return ir.IsLessEqualGuard(_expr(value[0]), _expr(value[1]))
        if op == "(case)":
            default = _node(value["else"]) if "else" in value else None
            return ir.CaseTree(value["var"], _branches(value, _node), default)
        if op == "(flip)":
            return ir.FlipValue(value["var"], _branches(value, _expr))
        if op == "(token)":
            return ir.TokenWrapper(value[0], _node(value[1]))
        if op == "(wrap)":
            return ir.Wrapper(value[0], value[1], _node(value[2]))
        if op == "(emit)":
            return ir.EmitAction(value)
        if op == "(cut)":
            return ir.CutAction(value)
        if op == "(commit)":
            return ir.CommitWrapper(value[0], _node(value[1]))
        if op == "(error)":
            return ir.ErrorAction(value)
        if op == "(recover)":
            return ir.RecoverWrapper(_node(value[0]), _node(value[1]))
        rep = _REP.match(op)
        if rep:
            return ir.RepTree(_count(rep.group(1)), _node(value))
        if op.startswith("("):
            raise ValueError(f"unhandled grammar operator {op!r}")
        return ir.RefCall(op, _args(value))  # a single non-operator key, being a parameterized reference
    marker = _special(x)
    if marker is not None:
        return marker
    if _is_char(x):
        return _char(x)
    if isinstance(x, str):
        return ir.RefCall(x)  # a bare production name
    raise ValueError(f"unexpected grammar scalar: {x!r}")


def _production(number: int, name: str, definition: object) -> ir.Prod:
    """Translate a production. The `(...)` parameter declaration splits off from the body."""
    params = ()
    if isinstance(definition, dict) and "(...)" in definition:
        declared = definition["(...)"]
        params = tuple(declared) if isinstance(declared, list) else (declared,)
        definition = {k: v for k, v in definition.items() if k != "(...)"}
    return ir.Prod(number, name, params, _node(definition))


def translate(grammar: Mapping[str, object]) -> dict[str, ir.Prod]:
    """Translate the loaded yaml-grammar mapping into an ordered `{name: Prod}` dict."""
    productions = {}
    for number, name in grammar.items():
        if not number.startswith(":"):
            continue
        if not isinstance(name, str):
            raise ValueError(f"the production numbered {number} holds {name!r} rather than a name")
        productions[name] = _production(int(number[1:]), name, grammar[name])
    return productions


def load(source: str = DEFAULT_GRAMMAR) -> dict[str, ir.Prod]:
    """Load and translate the grammar at `source`. libyeast's grammar fills in where the caller names none."""
    with open(source, encoding="utf-8") as handle:
        return translate(yaml.safe_load(handle))


def written(source: str = DEFAULT_GRAMMAR) -> dict[str, object]:
    """
    The grammar at `source` as the file writes it, as `{name: what it says}`. A caller asking about the source rather
    than about the IR reads this.

    The file names a production twice. A `:N` key gives the number and the name it goes under, and that name keys what
    the production says. So the loop's key is the number and its value the name. `translate` reads the file that way
    too.
    """
    with open(source, encoding="utf-8") as handle:
        grammar = yaml.safe_load(handle)
    return {name: grammar[name] for number, name in grammar.items() if number.startswith(":")}


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GRAMMAR
    productions = load(source)
    out = sys.stdout
    out.write(f"# Generated by `annotated2ir.py` from {os.path.basename(source)}. Do not edit.\n")
    out.write("from ir import *  # noqa: F401,F403\n\n")
    out.write("GRAMMAR = {\n")
    for name, translated in productions.items():
        out.write(f"    {name!r}: {translated!r},\n")
    out.write("}\n")


if __name__ == "__main__":
    main()
