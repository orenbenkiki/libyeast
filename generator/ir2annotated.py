# SPDX-License-Identifier: MIT
"""
Regenerate the yaml-grammar notation from the typed IR — the inverse of `annotated2ir.py`.

`check_annotated_roundtrip.py` uses this to prove the translation is lossless: `annotated2ir` then `ir2annotated` must
reproduce the vendored source exactly. Run directly to dump the regenerated grammar as YAML.

Usage: `python3 generator/ir2annotated.py [spec.yaml] > regenerated.yaml`
"""

import sys

import ir
import annotated2ir

import yaml


def hex_text(cp):
    """A codepoint as `xHH`, padded to 2/4/6 digits as the source does. Used for range endpoints (always hex)."""
    width = 2 if cp <= 0xFF else 4 if cp <= 0xFFFF else 6
    return f"x{cp:0{width}X}"


def char_text(cp):
    """A single character literal: the printable-ASCII character itself, else its `xHH` spelling."""
    return chr(cp) if 0x21 <= cp <= 0x7E else hex_text(cp)


def args_yaml(args):
    """A reference's argument list: a bare expression for one argument, else a list."""
    if len(args) == 1:
        return expr_yaml(args[0])
    return [expr_yaml(a) for a in args]


def expr_yaml(e):
    """Regenerate a value/parameter expression."""
    return _EXPR_YAML(e)


# A kind named nowhere raises: one written back by accident would be spelled the way something else is, and the
# roundtrip would compare the grammar against a reading of it rather than itself.
_EXPR_YAML = ir.Reading(
    "what a value expression is written as in the annotated grammar: the string or number it spells, or the "
    "single-entry mapping its operator names",
    {
        ir.ParamValue: lambda e: e.name,
        ir.LitValue: lambda e: e.value,
        ir.MatchValue: "(match)",
        ir.ColumnValue: "<column>",
        ir.AutoDetectIndentValue: "<auto-detect-indent>",  # written back only where the vendored grammar is regenerated
        ir.AddValue: lambda e: {"(+)": [expr_yaml(e.a), expr_yaml(e.b)]},
        ir.SubValue: lambda e: {"(-)": [expr_yaml(e.a), expr_yaml(e.b)]},
        ir.AtoiValue: lambda e: {"(atoi)": expr_yaml(e.arg)},
        ir.LenValue: lambda e: {"(len)": expr_yaml(e.arg)},
        ir.FlipValue: lambda e: {"(flip)": {"var": e.var, **{b.value: expr_yaml(b.item) for b in e.branches}}},
        ir.RefCall: lambda e: {e.name: args_yaml(e.args)},
    },
)


def node_yaml(n):
    """Regenerate a grammar node."""
    return _NODE_YAML(n)


def _max_yaml(n):
    """A `(max)`'s: the wrapping form spells what it covers, and the vendored grammar's bare form its limit alone."""
    if n.item is not None:
        return {"(max)": [expr_yaml(n.limit), n.message, node_yaml(n.item)]}
    return {"(max)": expr_yaml(n.limit)}


def _case_yaml(n):
    """A `(case)`'s: the variable it switches on, a branch per value, and the else where it has one."""
    default = {"else": node_yaml(n.default)} if n.default is not None else {}
    return {"(case)": {"var": n.var, **{b.value: node_yaml(b.item) for b in n.branches}, **default}}


# A kind named nowhere raises: one written back by accident would be spelled the way something else is, and the
# roundtrip would compare the grammar against a reading of it rather than itself.
_NODE_YAML = ir.Reading(
    "what a grammar node is written as in the annotated grammar: the character or name it spells, the pair of hex "
    "bounds a range spells, or the single-entry mapping its operator names",
    {
        ir.OneCharSet: lambda n: char_text(n.cp),
        ir.RangeSet: lambda n: [hex_text(n.lo), hex_text(n.hi)],
        ir.RefCall: lambda n: n.name if not n.args else {n.name: args_yaml(n.args)},
        ir.EmptyTree: "<empty>",
        ir.FailTree: "<fail>",
        ir.StartOfLineGuard: "<start-of-line>",
        ir.EndOfStreamGuard: "<end-of-stream>",
        ir.InvalidSet: "<invalid>",
        ir.SeqTree: lambda n: {"(all)": [node_yaml(i) for i in n.items]},
        ir.AltTree: lambda n: {"(any)": [node_yaml(i) for i in n.items]},
        ir.StarTree: lambda n: {"(***)": node_yaml(n.item)},
        ir.PlusTree: lambda n: {"(+++)": node_yaml(n.item)},
        ir.OptTree: lambda n: {"(???)": node_yaml(n.item)},
        # The count is a number where it is fixed and the parameter's name where it is carried, which is how each of
        # those is spelled as a value expression anyway.
        ir.RepTree: lambda n: {f"({{{expr_yaml(n.count)}}})": node_yaml(n.item)},
        ir.LookGuard: lambda n: {"(===)": node_yaml(n.item)},
        ir.NegLookGuard: lambda n: {"(!==)": node_yaml(n.item)},
        ir.LookBehindGuard: lambda n: {"(<==)": node_yaml(n.item)},
        ir.DiffSet: lambda n: {"(---)": [node_yaml(n.base), *(node_yaml(m) for m in n.minus)]},
        ir.ExcludeAtAction: lambda n: {"(exclude)": node_yaml(n.item)},
        ir.SetVarAction: lambda n: {"(set)": [n.param, expr_yaml(n.value)]},
        ir.IncreaseAction: lambda n: {"(increase)": n.param},
        ir.MaxWrapper: _max_yaml,
        ir.ColumnLtGuard: lambda n: {"(<)": [expr_yaml(n.a), expr_yaml(n.b)]},
        ir.ColumnLeGuard: lambda n: {"(<=)": [expr_yaml(n.a), expr_yaml(n.b)]},
        ir.CaseTree: _case_yaml,
        ir.FlipValue: lambda n: {"(flip)": {"var": n.var, **{b.value: expr_yaml(b.item) for b in n.branches}}},
        ir.BindTree: lambda n: {"(if)": node_yaml(n.cond), "(set)": [n.param, expr_yaml(n.value)]},
        ir.TokenWrapper: lambda n: {"(token)": [n.code, node_yaml(n.item)]},
        ir.Wrapper: lambda n: {"(wrap)": [n.begin, n.end, node_yaml(n.item)]},
        ir.EmitAction: lambda n: {"(emit)": n.code},
        ir.CutAction: lambda n: {"(cut)": n.message},
        ir.CommitWrapper: lambda n: {"(commit)": [n.message, node_yaml(n.item)]},
        ir.ErrorAction: lambda n: {"(error)": n.message},
        ir.RecoverWrapper: lambda n: {"(recover)": [node_yaml(n.recovery), node_yaml(n.item)]},
    },
)


def regenerate(productions):
    """Rebuild the yaml-grammar mapping (index entries + definitions) from `{name: Prod}`."""
    out = {}
    for name, prod in productions.items():
        out[f":{prod.number:03d}"] = name
        body = node_yaml(prod.body)
        if prod.params:
            declared = prod.params[0] if len(prod.params) == 1 else list(prod.params)
            out[name] = {"(...)": declared, **body}
        else:
            out[name] = body
    return out


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else annotated2ir.DEFAULT_GRAMMAR
    yaml.safe_dump(regenerate(annotated2ir.load(source)), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
