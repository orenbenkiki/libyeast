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
    if isinstance(e, ir.Param):
        return e.name
    if isinstance(e, ir.Lit):
        return e.value
    if isinstance(e, ir.Match):
        return "(match)"
    if isinstance(e, ir.AutoDetectIndent):
        return "<auto-detect-indent>"
    if isinstance(e, ir.AutoDetectInLineIndent):
        return "<auto-detect-in-line-indent>"
    if isinstance(e, ir.Add):
        return {"(+)": [expr_yaml(e.a), expr_yaml(e.b)]}
    if isinstance(e, ir.Sub):
        return {"(-)": [expr_yaml(e.a), expr_yaml(e.b)]}
    if isinstance(e, ir.Ord):
        return {"(ord)": expr_yaml(e.arg)}
    if isinstance(e, ir.Len):
        return {"(len)": expr_yaml(e.arg)}
    if isinstance(e, ir.Flip):
        return {"(flip)": {"var": e.var, **{b.value: expr_yaml(b.item) for b in e.branches}}}
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
    if isinstance(n, ir.StartOfLine):
        return "<start-of-line>"
    if isinstance(n, ir.EndOfStream):
        return "<end-of-stream>"
    if isinstance(n, ir.Invalid):
        return "<invalid>"
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
    if isinstance(n, ir.Increase):
        return {"(increase)": n.param}
    if isinstance(n, ir.Max):
        if n.item is not None:
            return {"(max)": [expr_yaml(n.limit), n.message, node_yaml(n.item)]}
        return {"(max)": expr_yaml(n.limit)}
    if isinstance(n, ir.Lt):
        return {"(<)": [expr_yaml(n.a), expr_yaml(n.b)]}
    if isinstance(n, ir.Le):
        return {"(<=)": [expr_yaml(n.a), expr_yaml(n.b)]}
    if isinstance(n, ir.Case):
        default = {"else": node_yaml(n.default)} if n.default is not None else {}
        return {"(case)": {"var": n.var, **{b.value: node_yaml(b.item) for b in n.branches}, **default}}
    if isinstance(n, ir.Flip):
        return {"(flip)": {"var": n.var, **{b.value: expr_yaml(b.item) for b in n.branches}}}
    if isinstance(n, ir.Bind):
        return {"(if)": node_yaml(n.cond), "(set)": [n.param, expr_yaml(n.value)]}
    if isinstance(n, ir.Token):
        return {"(token)": [n.code, node_yaml(n.item)]}
    if isinstance(n, ir.Wrap):
        return {"(wrap)": [n.begin, n.end, node_yaml(n.item)]}
    if isinstance(n, ir.Emit):
        return {"(emit)": n.code}
    if isinstance(n, ir.Cut):
        return {"(cut)": n.message}
    if isinstance(n, ir.Commit):
        return {"(commit)": [n.message, node_yaml(n.item)]}
    if isinstance(n, ir.Error):
        return {"(error)": n.message}
    if isinstance(n, ir.Recover):
        return {"(recover)": [node_yaml(n.recovery), node_yaml(n.item)]}
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
    source = sys.argv[1] if len(sys.argv) > 1 else annotated2ir.DEFAULT_GRAMMAR
    yaml.safe_dump(regenerate(annotated2ir.load(source)), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
