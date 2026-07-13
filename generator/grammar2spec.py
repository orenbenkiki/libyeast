# SPDX-License-Identifier: MIT
"""Regenerate the yaml-grammar notation from the typed IR — the inverse of `spec2grammar.py`.

`check_spec_roundtrip.py` uses this to prove the translation is lossless: `spec2grammar` then `grammar2spec` must
reproduce the vendored source exactly. Run directly to dump the regenerated grammar as YAML.

Usage: `python3 generator/grammar2spec.py [spec.yaml] > regenerated.yaml`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir  # noqa: E402
import spec2grammar  # noqa: E402

import yaml  # noqa: E402


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
    if isinstance(e, ir.Param):
        return e.name
    if isinstance(e, ir.Lit):
        return e.value
    if isinstance(e, ir.Match):
        return "(match)"
    if isinstance(e, ir.Add):
        return {"(+)": [expr_yaml(e.a), expr_yaml(e.b)]}
    if isinstance(e, ir.Sub):
        return {"(-)": [expr_yaml(e.a), expr_yaml(e.b)]}
    if isinstance(e, ir.Ord):
        return {"(ord)": expr_yaml(e.arg)}
    if isinstance(e, ir.Len):
        return {"(len)": expr_yaml(e.arg)}
    if isinstance(e, ir.Flip):
        return {"(flip)": {"var": e.var, **{k: expr_yaml(v) for k, v in e.branches}}}
    if isinstance(e, ir.Ref):
        return {e.name: args_yaml(e.args)}
    raise TypeError(f"not an expression: {e!r}")


def node_yaml(n):
    """Regenerate a grammar node."""
    if isinstance(n, ir.Char):
        return char_text(n.cp)
    if isinstance(n, ir.Range):
        return [hex_text(n.lo), hex_text(n.hi)]
    if isinstance(n, ir.Ref):
        return n.name if not n.args else {n.name: args_yaml(n.args)}
    if isinstance(n, ir.Empty):
        return "<empty>"
    if isinstance(n, ir.Seq):
        return {"(all)": [node_yaml(i) for i in n.items]}
    if isinstance(n, ir.Alt):
        return {"(any)": [node_yaml(i) for i in n.items]}
    if isinstance(n, ir.Star):
        return {"(***)": node_yaml(n.item)}
    if isinstance(n, ir.Plus):
        return {"(+++)": node_yaml(n.item)}
    if isinstance(n, ir.Opt):
        return {"(???)": node_yaml(n.item)}
    if isinstance(n, ir.Rep):
        inner = n.count.value if isinstance(n.count, ir.Lit) else n.count.name
        return {f"({{{inner}}})": node_yaml(n.item)}
    if isinstance(n, ir.Look):
        return {"(===)": node_yaml(n.item)}
    if isinstance(n, ir.NegLook):
        return {"(!==)": node_yaml(n.item)}
    if isinstance(n, ir.LookBehind):
        return {"(<==)": node_yaml(n.item)}
    if isinstance(n, ir.Bound):
        return {"(<<<)": node_yaml(n.item)}
    if isinstance(n, ir.Diff):
        return {"(---)": [node_yaml(n.base), *(node_yaml(m) for m in n.minus)]}
    if isinstance(n, ir.ExcludeAt):
        return {"(exclude)": node_yaml(n.item)}
    if isinstance(n, ir.SetVar):
        return {"(set)": [n.param, expr_yaml(n.value)]}
    if isinstance(n, ir.Max):
        return {"(max)": expr_yaml(n.limit)}
    if isinstance(n, ir.Lt):
        return {"(<)": [expr_yaml(n.a), expr_yaml(n.b)]}
    if isinstance(n, ir.Le):
        return {"(<=)": [expr_yaml(n.a), expr_yaml(n.b)]}
    if isinstance(n, ir.Case):
        return {"(case)": {"var": n.var, **{k: node_yaml(v) for k, v in n.branches}}}
    if isinstance(n, ir.Flip):
        return {"(flip)": {"var": n.var, **{k: expr_yaml(v) for k, v in n.branches}}}
    if isinstance(n, ir.Bind):
        return {"(if)": node_yaml(n.cond), "(set)": [n.param, expr_yaml(n.value)]}
    raise TypeError(f"not a grammar node: {n!r}")


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
    source = sys.argv[1] if len(sys.argv) > 1 else spec2grammar.DEFAULT_SPEC
    yaml.safe_dump(regenerate(spec2grammar.load(source)), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
