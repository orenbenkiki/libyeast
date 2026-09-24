# SPDX-License-Identifier: MIT
"""
Regenerate the yaml-grammar notation from the typed IR. This is the inverse of `annotated2ir.py`.

`check_annotated_roundtrip.py` uses this to prove the translation is lossless. `annotated2ir` then `ir2annotated` must
reproduce libyeast's grammar exactly. Run directly to dump the regenerated grammar as YAML.

**Usage:** `python3 generator/ir2annotated.py [spec.yaml] > regenerated.yaml`.
"""

import sys
from collections.abc import Mapping, Sequence

import annotated2ir
import ir

import yaml


def _hex_text(codepoint: int) -> str:
    """
    A codepoint as `xHH`. The padding runs to `2`, `4` or `6` digits, as the source does. A range's endpoints take this
    form.
    """
    width = 2 if codepoint <= 0xFF else 4 if codepoint <= 0xFFFF else 6
    return f"x{codepoint:0{width}X}"


def _char_text(codepoint: int) -> str:
    """
    A single character literal. The printable-ASCII character comes out as itself. Anything else takes its `xHH` form.
    """
    return chr(codepoint) if 0x21 <= codepoint <= 0x7E else _hex_text(codepoint)


def _args_yaml(arguments: Sequence[ir.Node]) -> object:
    """
    A reference's argument list. A single argument comes out as a bare expression. More than that comes out as a list.
    """
    if len(arguments) == 1:
        return _expr_yaml(arguments[0])
    return [_expr_yaml(argument) for argument in arguments]


def _expr_yaml(value: ir.Node) -> object:
    """Regenerate a value expression or a parameter expression."""
    return _EXPR_YAML(value)


_EXPR_YAML: ir.Question[object] = ir.Question(
    "the annotated form of a value expression. a string or a number writes itself, and an operator writes a "
    "single-entry mapping.",
    {
        ir.ParamValue: lambda value: value.name,
        ir.LitValue: lambda value: value.value,
        ir.MatchValue: "(match)",
        ir.ColumnValue: "<column>",
        # `ir2spec` writes this back where it regenerates the vendored grammar.
        ir.AutoDetectIndentValue: "<auto-detect-indent>",
        ir.AddValue: lambda value: {"(+)": [_expr_yaml(value.a), _expr_yaml(value.b)]},
        ir.SubValue: lambda value: {"(-)": [_expr_yaml(value.a), _expr_yaml(value.b)]},
        ir.AtoiValue: lambda value: {"(atoi)": _expr_yaml(value.arg)},
        ir.LenValue: lambda value: {"(len)": _expr_yaml(value.arg)},
        ir.FlipValue: lambda value: {
            "(flip)": {"var": value.var, **{branch.value: _expr_yaml(branch.item) for branch in value.branches}}
        },
        ir.RefCall: lambda value: {value.name: _args_yaml(value.args)},
    },
)


def _node_yaml(node: ir.Node) -> object:
    """Regenerate a grammar node."""
    return _NODE_YAML(node)


def _max_yaml(node: ir.MaxWrapper) -> dict[str, object]:
    """
    A `(max)`'s form. The wrapping form writes what the `(max)` covers. The vendored grammar's bare form writes just the
    limit.
    """
    if node.item is not None:
        return {"(max)": [_expr_yaml(node.limit), node.message, _node_yaml(node.item)]}
    return {"(max)": _expr_yaml(node.limit)}


def _case_yaml(node: ir.CaseTree) -> dict[str, object]:
    """
    A `(case)`'s form. The form writes the variable the case switches on and a branch per value. It writes the else
    where a case has an else.
    """
    default = {"else": _node_yaml(node.default)} if node.default is not None else {}
    branches = {branch.value: _node_yaml(branch.item) for branch in node.branches}
    return {"(case)": {"var": node.var, **branches, **default}}


_NODE_YAML: ir.Question[object] = ir.Question(
    "the annotated form of a grammar node. a character or a name writes itself. a range writes a pair of hex bounds. "
    "an operator writes a single-entry mapping.",
    {
        ir.OneCharSet: lambda node: _char_text(node.cp),
        ir.RangeSet: lambda node: [_hex_text(node.lo), _hex_text(node.hi)],
        ir.RefCall: lambda node: node.name if not node.args else {node.name: _args_yaml(node.args)},
        ir.EmptyTree: "<empty>",
        ir.FailTree: "<fail>",
        ir.StartOfLineGuard: "<start-of-line>",
        ir.EndOfStreamGuard: "<end-of-stream>",
        ir.InvalidSet: "<invalid>",
        ir.SeqTree: lambda node: {"(all)": [_node_yaml(item) for item in node.items]},
        ir.AltTree: lambda node: {"(any)": [_node_yaml(item) for item in node.items]},
        ir.StarTree: lambda node: {"(***)": _node_yaml(node.item)},
        ir.PlusTree: lambda node: {"(+++)": _node_yaml(node.item)},
        ir.OptTree: lambda node: {"(???)": _node_yaml(node.item)},
        # A fixed count comes out as a number. A parameterized count comes out as the parameter's name. A value
        # expression writes either.
        ir.RepTree: lambda node: {f"({{{_expr_yaml(node.count)}}})": _node_yaml(node.item)},
        ir.LookGuard: lambda node: {"(===)": _node_yaml(node.item)},
        ir.NegLookGuard: lambda node: {"(!==)": _node_yaml(node.item)},
        ir.LookBehindGuard: lambda node: {"(<==)": _node_yaml(node.item)},
        ir.DiffSet: lambda node: {
            "(---)": [_node_yaml(node.base), *(_node_yaml(subtracted) for subtracted in node.minus)]
        },
        ir.ExcludeAtAction: lambda node: {"(exclude)": _node_yaml(node.item)},
        ir.SetVarAction: lambda node: {"(set)": [node.param, _expr_yaml(node.value)]},
        ir.IncreaseAction: lambda node: {"(increase)": node.param},
        ir.MaxWrapper: _max_yaml,
        ir.IsLessThanGuard: lambda node: {"(<)": [_expr_yaml(node.a), _expr_yaml(node.b)]},
        ir.IsLessEqualGuard: lambda node: {"(<=)": [_expr_yaml(node.a), _expr_yaml(node.b)]},
        ir.CaseTree: _case_yaml,
        ir.FlipValue: lambda node: {
            "(flip)": {"var": node.var, **{branch.value: _expr_yaml(branch.item) for branch in node.branches}}
        },
        ir.BindTree: lambda node: {"(if)": _node_yaml(node.cond), "(set)": [node.param, _expr_yaml(node.value)]},
        ir.TokenWrapper: lambda node: {"(token)": [node.code, _node_yaml(node.item)]},
        ir.Wrapper: lambda node: {"(wrap)": [node.begin, node.end, _node_yaml(node.item)]},
        ir.EmitAction: lambda node: {"(emit)": node.code},
        ir.CutAction: lambda node: {"(cut)": node.message},
        ir.CommitWrapper: lambda node: {"(commit)": [node.message, _node_yaml(node.item)]},
        ir.ErrorAction: lambda node: {"(error)": node.message},
        ir.RecoverWrapper: lambda node: {"(recover)": [_node_yaml(node.recovery), _node_yaml(node.item)]},
    },
)


def regenerate(productions: Mapping[str, ir.Prod]) -> dict[str, object]:
    """Rebuild the yaml-grammar mapping (index entries + definitions) from `{name: Prod}`."""
    regenerated: dict[str, object] = {}
    for name, production in productions.items():
        regenerated[f":{production.number:03d}"] = name
        body = _node_yaml(production.body)
        if production.params:
            declared = production.params[0] if len(production.params) == 1 else list(production.params)
            if not isinstance(body, dict):
                raise ValueError(f"{name} declares parameters, and {body!r} is the annotated form of its body")
            regenerated[name] = {"(...)": declared, **body}
        else:
            regenerated[name] = body
    return regenerated


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else annotated2ir.DEFAULT_GRAMMAR
    yaml.safe_dump(regenerate(annotated2ir.load(source)), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
