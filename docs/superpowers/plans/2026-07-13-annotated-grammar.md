# Annotated Grammar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give libyeast a grammar of its own — the YAML productions *plus* the token annotations that say what to emit —
and prove mechanically that it is still the official grammar.

**Architecture:** The vendored `yaml-spec-1.2.yaml` is a lossy rendering: it inlined the indicator characters and so
threw away the structure the token layer hangs on. `grammar/yeast.yaml` restores that structure and adds three
annotation operators. A gate erases the annotations, re-inlines the indicators, and checks that what remains is the
vendored grammar, production for production — so the grammar is hand-authored where it must be and machine-proved where
it can be.

**Tech Stack:** Python 3 + PyYAML (generator), C99 (library), Make (gate).

**Sources.** `third_party/yaml-grammar/yaml-spec-1.2.yaml` is the official grammar and never changes.
`third_party/yamlreference/Text/Yaml/Reference.bnf` is the Haskell reference, vendored to be **read**: it carries the
token annotations this plan replicates. Its decisions are copied by understanding, not by translation, and Task 8 marks
the one place we deliberately depart from it.

______________________________________________________________________

## Conventions this codebase enforces

Violating any of these fails `make pc`:

- **Comments:** `//` only in C, and `#` in Python; public C symbols take `///` Doxygen comments.
- **Line length:** 120 columns everywhere.
- **Names are descriptive.** No `i`, `n`, `buf`, `tmp` — except `n`, `c`, `m`, `t`, which are the grammar's own
  parameter names and must keep them.
- **Formatters:** `black --line-length 120` (Python), `mdformat --wrap 120` (Markdown). `make reformat` before every
  commit, `make pc` before every commit.

## File structure

| file                                   | responsibility                                                               |
| -------------------------------------- | ---------------------------------------------------------------------------- |
| `grammar/yeast.yaml`                   | **New, ours.** The productions, the indicator productions, the annotations   |
| `generator/ir.py`                      | Gains `Token`, `Wrap`, `Emit`                                                |
| `generator/yeast2ir.py`                | Was `spec2grammar.py` — now reads `grammar/yeast.yaml`                       |
| `generator/ir2yeast.py`                | Was `grammar2spec.py` — the inverse, for the round-trip gate                 |
| `generator/ir2spec.py`                 | **New.** Erase the annotations and re-inline the indicators                  |
| `generator/check_grammar_roundtrip.py` | Was `check_spec_roundtrip.py` — the round-trip gate                          |
| `generator/check_vendor_spec.py`       | **New.** The erasure gate, and the deviation allowlist                       |
| `generator/chars.py`                   | Unchanged but for its import                                                 |
| `generator/grammar2decoder.py`         | Unchanged but for its import; the tables now come from the annotated grammar |
| `generator/check_decoder.py`           | Unchanged but for its import                                                 |
| `generator/validate_grammar.py`        | `UNREFERENCED` loses the 18 indicator productions, now referenced            |

______________________________________________________________________

## Task 1: The annotation operators in the IR

**Files:**

- Modify: `generator/ir.py`

- [ ] **Step 1: Add the three nodes**

Append to the grammar-node section of `generator/ir.py`, after `SetVar`:

```python
@dataclass(frozen=True)
class Token:
    """`(token)`: the text `item` matches is one token, of the given yeast code."""

    code: str
    item: object


@dataclass(frozen=True)
class Wrap:
    """`(wrap)`: zero-width `begin`/`end` markers around whatever `item` matches."""

    begin: str
    end: str
    item: object


@dataclass(frozen=True)
class Emit:
    """`(emit)`: a zero-width marker at this point in a sequence."""

    code: str
```

The codes are the `ys_code` values in kebab case, with the `YS_CODE_` prefix dropped: `indicator`, `meta`, `text`,
`indent`, `white`, `break`, `line-feed`, `line-fold`, `directives-end`, `document-end`, `begin-scalar`, `end-scalar`,
`begin-comment`, `end-comment`, `begin-directive`, `end-directive`, `begin-tag`, `end-tag`, `begin-handle`,
`end-handle`, `begin-anchor`, `end-anchor`, `begin-properties`, `end-properties`, `begin-alias`, `end-alias`,
`begin-sequence`, `end-sequence`, `begin-mapping`, `end-mapping`, `begin-pair`, `end-pair`, `begin-node`, `end-node`,
`begin-document`, `end-document`, `begin-stream`, `end-stream`, `bom`, `error`, `unparsed`, `detected`.

- [ ] **Step 2: Check it imports**

Run: `python3 -c "import sys; sys.path.insert(0,'generator'); import ir; print(ir.Token('indicator', None))"`

Expected: `Token(code='indicator', item=None)`

- [ ] **Step 3: Format**

```bash
black --line-length 120 generator/ir.py
```

Do not commit yet — Task 2 makes this usable.

______________________________________________________________________

## Task 2: Read and write the annotation operators

**Files:**

- Modify: `generator/spec2grammar.py` (renamed in Task 7; leave the name alone for now)

- Modify: `generator/grammar2spec.py`

- [ ] **Step 1: Read them**

In `generator/spec2grammar.py`, inside `node()`, before the `rep = REP.match(op)` line:

```python
        if op == "(token)":
            return ir.Token(value[0], node(value[1]))
        if op == "(wrap)":
            return ir.Wrap(value[0], value[1], node(value[2]))
        if op == "(emit)":
            return ir.Emit(value)
```

- [ ] **Step 2: Write them**

In `generator/grammar2spec.py`, inside `node_yaml()`, before the `raise TypeError`:

```python
    if isinstance(n, ir.Token):
        return {"(token)": [n.code, node_yaml(n.item)]}
    if isinstance(n, ir.Wrap):
        return {"(wrap)": [n.begin, n.end, node_yaml(n.item)]}
    if isinstance(n, ir.Emit):
        return {"(emit)": n.code}
```

- [ ] **Step 3: Check the round-trip of an annotation**

Run:

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "generator")
import spec2grammar, grammar2spec, yaml
source = {"(wrap)": ["begin-scalar", "end-scalar",
                     {"(all)": [{"(emit)": "begin-comment"}, {"(token)": ["indicator", "-"]}]}]}
node = spec2grammar.node(source)
print(node)
assert grammar2spec.node_yaml(node) == source, "round-trip lost something"
print("round-trip OK")
PY
```

Expected: the IR printed, then `round-trip OK`.

- [ ] **Step 4: Confirm the existing gate still passes, and commit**

```bash
make check-grammar && make reformat && make pc
git add generator/ir.py generator/spec2grammar.py generator/grammar2spec.py
git commit -m "feat: annotation operators in the grammar IR"
```

______________________________________________________________________

## Task 3: Bootstrap `grammar/yeast.yaml`

The productions are a mechanical transcription of a file we already parse losslessly, so generate them; the *thinking*
is in Task 4's annotations. Bootstrapping is safe because Task 6's gate proves the result equals the vendored grammar.

The one change the bootstrap makes: **un-inline the indicator characters.** The vendored grammar writes `'-'`; the
reference writes `c-sequence-entry`, and that is what an annotation can attach to. There are 18 such productions and
each is a single character, so the rewrite is unambiguous *in the reference's structure* — see the mapping below, which
is exhaustive.

**Files:**

- Create: `generator/bootstrap_yeast.py`

- Create: `grammar/yeast.yaml`

- [ ] **Step 1: Write the bootstrap script**

```python
# SPDX-License-Identifier: MIT
"""Write the skeleton of `grammar/yeast.yaml` from the vendored grammar — the productions, none of the annotations.

The vendored grammar inlines the indicator characters, which leaves a token annotation nothing to attach to. This
restores the reference's structure: every occurrence of an indicator character in a matching position becomes a
reference to the production that names it. Run once; the annotations are then written by hand.

Usage: `python3 generator/bootstrap_yeast.py > grammar/yeast.yaml`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grammar2spec  # noqa: E402
import ir  # noqa: E402
import spec2grammar  # noqa: E402

import yaml  # noqa: E402

# The characters the reference reaches through a named production, so that the production can carry the token. Every
# occurrence of the character in any other production becomes a reference to the one that names it.
INDICATORS = {
    ord("-"): "c-sequence-entry",
    ord("?"): "c-mapping-key",
    ord(":"): "c-mapping-value",
    ord(","): "c-collect-entry",
    ord("["): "c-sequence-start",
    ord("]"): "c-sequence-end",
    ord("{"): "c-mapping-start",
    ord("}"): "c-mapping-end",
    ord("#"): "c-comment",
    ord("&"): "c-anchor",
    ord("*"): "c-alias",
    ord("!"): "c-tag",
    ord("|"): "c-literal",
    ord(">"): "c-folded",
    ord("'"): "c-single-quote",
    ord('"'): "c-double-quote",
    ord("%"): "c-directive",
    ord("\\"): "c-escape",
}


def un_inline(node, owner):
    """Rewrite an inlined indicator character into a reference to the production that names it."""
    if isinstance(node, ir.Char) and node.cp in INDICATORS and INDICATORS[node.cp] != owner:
        return ir.Ref(INDICATORS[node.cp])
    if isinstance(node, ir.Seq):
        return ir.Seq(tuple(un_inline(item, owner) for item in node.items))
    if isinstance(node, ir.Alt):
        return ir.Alt(tuple(un_inline(item, owner) for item in node.items))
    if isinstance(node, ir.Star):
        return ir.Star(un_inline(node.item, owner))
    if isinstance(node, ir.Plus):
        return ir.Plus(un_inline(node.item, owner))
    if isinstance(node, ir.Opt):
        return ir.Opt(un_inline(node.item, owner))
    if isinstance(node, ir.Rep):
        return ir.Rep(node.count, un_inline(node.item, owner))
    if isinstance(node, ir.Look):
        return ir.Look(un_inline(node.item, owner))
    if isinstance(node, ir.NegLook):
        return ir.NegLook(un_inline(node.item, owner))
    if isinstance(node, ir.LookBehind):
        return ir.LookBehind(un_inline(node.item, owner))
    if isinstance(node, ir.Bound):
        return ir.Bound(un_inline(node.item, owner))
    if isinstance(node, ir.ExcludeAt):
        return ir.ExcludeAt(un_inline(node.item, owner))
    if isinstance(node, ir.Bind):
        return ir.Bind(un_inline(node.cond, owner), node.param, node.value)
    if isinstance(node, ir.Case):
        return ir.Case(node.var, tuple((key, un_inline(branch, owner)) for key, branch in node.branches))
    return node  # Char (not an indicator, or its own production), Range, Ref, Diff, and the zero-width markers


def main():
    grammar = spec2grammar.load()
    # A character-class production tests characters rather than matching an indicator, so it keeps its literals.
    classes = {name for name, production in grammar.items() if isinstance(production.body, (ir.Alt, ir.Diff, ir.Range))}
    rewritten = {}
    for name, production in grammar.items():
        body = production.body if name in classes else un_inline(production.body, name)
        rewritten[name] = ir.Prod(production.number, name, production.params, body)
    yaml.safe_dump(grammar2spec.regenerate(rewritten), sys.stdout, sort_keys=False, allow_unicode=True, width=120)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the skeleton and eyeball it**

Run:

```bash
mkdir -p grammar
python3 generator/bootstrap_yeast.py > grammar/yeast.yaml
python3 -c "
import sys; sys.path.insert(0,'generator')
import spec2grammar
g = spec2grammar.load('grammar/yeast.yaml')
print('productions:', len(g))
print('c-l+literal:', g['c-l+literal'].body)
"
```

Expected: `productions: 211`, and `c-l+literal` shows `Ref(name='c-literal')` where the vendored grammar had
`Char(cp=124)`.

- [ ] **Step 3: Confirm every indicator production is now referenced**

Run:

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "generator")
import spec2grammar, ir
from validate_grammar import walk
g = spec2grammar.load("grammar/yeast.yaml")
referenced = {r.name for p in g.values() for r in walk(p.body) if isinstance(r, ir.Ref)}
unreferenced = sorted(set(g) - referenced - {"l-yaml-stream"})
print("still unreferenced:", unreferenced)
PY
```

Expected: `still unreferenced: ['c-reserved']` — `c-reserved` is the §5.4 reserved-character definition, which the
grammar genuinely never uses.

- [ ] **Step 4: Commit the skeleton, before any annotation**

```bash
make reformat && make pc
git add generator/bootstrap_yeast.py grammar/yeast.yaml
git commit -m "feat: bootstrap grammar/yeast.yaml, with the indicator productions restored"
```

______________________________________________________________________

## Task 4: Annotate

This is the work. Every annotation is a decision *we* make, having read `Reference.bnf`; none is translated.

Only the success path is annotated in this pass. The reference's error messages (`! "node"`, `^ "header"`, `?!`) and its
three `detect_*` productions are **out of scope** — see Task 8.

**Files:**

- Modify: `grammar/yeast.yaml`

- [ ] **Step 1: Annotate the 72 whole-production cases**

For each row of the table in [Appendix A](#appendix-a--the-whole-production-annotations), wrap the production's body.
`indicator`, `meta` and `text` are `(token)` with that code; `wrapTokens A B` is `(wrap)`. Two examples, in full:

```yaml
# [004] c-sequence-entry — was: `c-sequence-entry: '-'`
c-sequence-entry:
  (token): [indicator, '-']

# [103] ns-anchor-name — was: `ns-anchor-name: {(+++): ns-anchor-char}`
ns-anchor-name:
  (token):
  - meta
  - (+++): ns-anchor-char

# [101] c-ns-anchor-property — was: `{(all): [c-anchor, ns-anchor-name]}`
c-ns-anchor-property:
  (wrap):
  - begin-anchor
  - end-anchor
  - (all): [c-anchor, ns-anchor-name]
```

- [ ] **Step 2: Annotate the 24 internal cases**

For each of [Appendix B](#appendix-b--the-internal-annotations), place the annotation on the *node* it wraps, not on the
production. The reference body is given for each so the placement is unambiguous. Worked example, production 098:

```haskell
c_verbatim_tag = c_tag & indicator '<' & meta ( ns_uri_char +) & indicator '>'
```

```yaml
# [098] c-verbatim-tag
c-verbatim-tag:
  (all):
  - c-tag
  - (token): [indicator, '<']
  - (token):
    - meta
    - (+++): ns-uri-char
  - (token): [indicator, '>']
```

- [ ] **Step 3: Check it still loads, and that every code is a real one**

Run:

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "generator")
import spec2grammar, ir
from validate_grammar import walk
CODES = {"indicator", "meta", "text", "indent", "white", "break", "line-feed", "line-fold", "directives-end",
         "document-end", "begin-scalar", "end-scalar", "begin-comment", "end-comment", "begin-directive",
         "end-directive", "begin-tag", "end-tag", "begin-handle", "end-handle", "begin-anchor", "end-anchor",
         "begin-properties", "end-properties", "begin-alias", "end-alias", "begin-sequence", "end-sequence",
         "begin-mapping", "end-mapping", "begin-pair", "end-pair", "begin-node", "end-node", "begin-document",
         "end-document", "begin-stream", "end-stream", "bom", "error", "unparsed", "detected"}
g = spec2grammar.load("grammar/yeast.yaml")
used, bad = set(), []
for name, p in g.items():
    for node in walk(p.body):
        codes = ([node.code] if isinstance(node, (ir.Token, ir.Emit)) else
                 [node.begin, node.end] if isinstance(node, ir.Wrap) else [])
        for code in codes:
            used.add(code)
            if code not in CODES:
                bad.append(f"{name}: unknown code {code!r}")
annotated = sum(1 for p in g.values() if any(isinstance(x, (ir.Token, ir.Wrap, ir.Emit)) for x in walk(p.body)))
print(f"annotated productions: {annotated}")
print(f"codes used: {len(used)}")
for error in bad: print(error)
assert not bad, "unknown codes"
PY
```

Expected: `annotated productions: 96`, no unknown codes.

- [ ] **Step 4: Commit**

```bash
make reformat && make pc
git add grammar/yeast.yaml
git commit -m "feat: annotate the grammar with the yeast token codes"
```

______________________________________________________________________

## Task 5: The round-trip gate

`grammar/yeast.yaml` → IR → `grammar/yeast.yaml` must be lossless, exactly as the vendored grammar's round-trip already
is. This is what proves the IR holds everything the annotated grammar says.

**Files:**

- Modify: `generator/spec2grammar.py` (its `DEFAULT_SPEC`)

- [ ] **Step 1: Point the round-trip at our grammar**

Change `DEFAULT_SPEC` in `generator/spec2grammar.py`:

```python
DEFAULT_SPEC = "grammar/yeast.yaml"
```

and its module docstring's first line to `"""Translate libyeast's annotated grammar into the typed IR."""`.

- [ ] **Step 2: Run the round-trip**

Run: `python3 generator/check_spec_roundtrip.py`

Expected: `grammar round-trip OK: 211 productions`

- [ ] **Step 3: Prove it catches a lost annotation**

Run:

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "generator")
import grammar2spec, spec2grammar, ir, yaml
original = grammar2spec.node_yaml  # a version that silently drops (emit) nodes
grammar2spec.node_yaml = lambda n: None if isinstance(n, ir.Emit) else original(n)
with open("grammar/yeast.yaml") as handle:
    source = yaml.safe_load(handle)
regenerated = grammar2spec.regenerate(spec2grammar.translate(source))
print("a dropped annotation is caught:", regenerated != source)
PY
```

Expected: `a dropped annotation is caught: True`

- [ ] **Step 4: Commit**

```bash
make reformat && make pc
git add generator/spec2grammar.py
git commit -m "feat: libyeast's annotated grammar becomes the generator's source"
```

______________________________________________________________________

## Task 6: The erasure gate — prove it is still the official grammar

Erase the annotations, re-inline the indicators, and what remains must be the vendored grammar, production for
production. Compared as parsed data, not text, so YAML formatting cannot cause a false failure.

**Files:**

- Create: `generator/ir2spec.py`

- Create: `generator/check_vendor_spec.py`

- Modify: `Makefile`

- [ ] **Step 1: Write the erasure**

```python
# SPDX-License-Identifier: MIT
"""Erase libyeast's additions and recover the official grammar, to prove we still speak its language.

Three rewrites, each the exact inverse of something libyeast added: an annotation is dropped and its child kept; a
zero-width marker is dropped outright; a reference to an indicator production becomes the character it names. What comes
out must be the vendored grammar.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap_yeast  # noqa: E402
import grammar2spec  # noqa: E402
import ir  # noqa: E402
import spec2grammar  # noqa: E402

import yaml  # noqa: E402

NAMES_INDICATOR = {name: codepoint for codepoint, name in bootstrap_yeast.INDICATORS.items()}


def erase(node, owner):
    """The node the official grammar writes where libyeast writes `node`."""
    if isinstance(node, ir.Token):
        return erase(node.item, owner)
    if isinstance(node, ir.Wrap):
        return erase(node.item, owner)
    if isinstance(node, ir.Ref) and not node.args and node.name in NAMES_INDICATOR and node.name != owner:
        return ir.Char(NAMES_INDICATOR[node.name])
    if isinstance(node, ir.Seq):
        kept = [erase(item, owner) for item in node.items if not isinstance(item, ir.Emit)]
        return ir.Seq(tuple(kept))
    if isinstance(node, ir.Alt):
        return ir.Alt(tuple(erase(item, owner) for item in node.items))
    if isinstance(node, ir.Star):
        return ir.Star(erase(node.item, owner))
    if isinstance(node, ir.Plus):
        return ir.Plus(erase(node.item, owner))
    if isinstance(node, ir.Opt):
        return ir.Opt(erase(node.item, owner))
    if isinstance(node, ir.Rep):
        return ir.Rep(node.count, erase(node.item, owner))
    if isinstance(node, ir.Look):
        return ir.Look(erase(node.item, owner))
    if isinstance(node, ir.NegLook):
        return ir.NegLook(erase(node.item, owner))
    if isinstance(node, ir.LookBehind):
        return ir.LookBehind(erase(node.item, owner))
    if isinstance(node, ir.Bound):
        return ir.Bound(erase(node.item, owner))
    if isinstance(node, ir.ExcludeAt):
        return ir.ExcludeAt(erase(node.item, owner))
    if isinstance(node, ir.Bind):
        return ir.Bind(erase(node.cond, owner), node.param, node.value)
    if isinstance(node, ir.Case):
        return ir.Case(node.var, tuple((key, erase(branch, owner)) for key, branch in node.branches))
    return node


def official(grammar):
    """The official grammar's mapping, recovered from libyeast's."""
    recovered = {}
    for name, production in grammar.items():
        recovered[name] = ir.Prod(production.number, name, production.params, erase(production.body, name))
    return grammar2spec.regenerate(recovered)


def main():
    yaml.safe_dump(official(spec2grammar.load()), sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the gate**

```python
# SPDX-License-Identifier: MIT
"""Check that libyeast's grammar is still the official one.

Erases libyeast's annotations and indicator productions from `grammar/yeast.yaml` and compares what remains, production
by production, against the vendored `yaml-spec-1.2.yaml`. A production that differs is either a mistake or a deliberate
departure; a deliberate one must be listed in DEVIATIONS, with its reason, or this fails.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir2spec  # noqa: E402
import spec2grammar  # noqa: E402

import yaml  # noqa: E402

VENDORED = "third_party/yaml-grammar/yaml-spec-1.2.yaml"

# Productions where libyeast deliberately departs from the official grammar. Empty for now: the annotations are additive
# and erase cleanly. Task 8's indentation detection will be the first entry, and it must say why.
DEVIATIONS = {}


def main():
    with open(VENDORED) as handle:
        vendored = yaml.safe_load(handle)
    recovered = ir2spec.official(spec2grammar.load())

    errors = []
    for key in vendored:
        if key.startswith(":"):
            continue
        if recovered.get(key) != vendored[key] and key not in DEVIATIONS:
            errors.append(f"{key}: differs from the official grammar, and is not a declared deviation")
            errors.append(f"    official: {vendored[key]!r}")
            errors.append(f"    libyeast: {recovered.get(key)!r}")
    for key in sorted(set(recovered) - set(vendored)):
        if not key.startswith(":"):
            errors.append(f"{key}: libyeast has a production the official grammar does not")
    for key in sorted(DEVIATIONS):
        if key not in vendored:
            errors.append(f"{key}: declared as a deviation, but no such production")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        sys.exit(1)
    productions = sum(1 for key in vendored if not key.startswith(":"))
    print(f"official grammar recovered: {productions} productions, {len(DEVIATIONS)} declared deviation(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it**

Run: `python3 generator/check_vendor_spec.py`

Expected: `official grammar recovered: 211 productions, 0 declared deviation(s)`

If a production differs, the annotation is in the wrong place or the un-inlining is wrong. Fix the grammar — do not add
a deviation to make the error go away. A deviation is a decision, not an escape.

- [ ] **Step 4: Wire both gates into the Makefile**

Replace the `check-grammar` alias and add the rule:

```make
# The official grammar, recovered: erase libyeast's annotations and indicator productions from grammar/yeast.yaml, and
# what remains must be the vendored grammar. Additions cannot quietly become changes.
.stamps/vendor-spec: $(GRAMMAR_SPEC) $(YEAST_GRAMMAR) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@
```

with, in the input sets:

```make
YEAST_GRAMMAR := grammar/yeast.yaml
```

and:

```make
check-grammar: .stamps/grammar-roundtrip .stamps/grammar-validate .stamps/vendor-spec .stamps/decoder-tables
```

Add `$(YEAST_GRAMMAR)` to the prerequisites of `.stamps/grammar-roundtrip`, `.stamps/grammar-validate` and
`.stamps/decoder-tables`.

- [ ] **Step 5: Commit**

```bash
make reformat && make pc
git add generator/ir2spec.py generator/check_vendor_spec.py Makefile
git commit -m "feat: prove libyeast's grammar is still the official grammar"
```

______________________________________________________________________

## Task 7: Regenerate the decoder, and make the whole thing consistent

The decoder tables now come from the annotated grammar. Nothing about them should change — the annotations are additive
and the character sets are untouched — so a diff here is a bug, and the check will say so.

**Files:**

- Modify: `generator/validate_grammar.py`

- Modify: `src/decoder_tables.h`

- Rename: `generator/spec2grammar.py` → `generator/yeast2ir.py`

- Rename: `generator/grammar2spec.py` → `generator/ir2yeast.py`

- Rename: `generator/check_spec_roundtrip.py` → `generator/check_grammar_roundtrip.py`

- Modify: every generator module's imports, and `Makefile`

- [ ] **Step 1: Shrink the unreferenced list**

The 18 indicator productions are referenced now. In `generator/validate_grammar.py`, `UNREFERENCED` becomes:

```python
# The productions nothing references: the stream root, and c-reserved — the §5.4 reserved characters, which the grammar
# defines but never uses.
UNREFERENCED = frozenset({"l-yaml-stream", "c-reserved"})
```

- [ ] **Step 2: Regenerate the decoder tables and confirm they are unchanged**

Run:

```bash
python3 generator/grammar2decoder.py > src/decoder_tables.h
git diff --stat src/decoder_tables.h
```

Expected: **no diff**. The annotations do not touch the character sets. If there is a diff, an annotation has changed a
production's character structure, which it must not.

- [ ] **Step 3: Rename the modules to say what they now do**

```bash
git mv generator/spec2grammar.py generator/yeast2ir.py
git mv generator/grammar2spec.py generator/ir2yeast.py
git mv generator/check_spec_roundtrip.py generator/check_grammar_roundtrip.py
```

Then update every `import spec2grammar` to `import yeast2ir`, every `import grammar2spec` to `import ir2yeast`, and
every `spec2grammar.` / `grammar2spec.` reference, in: `generator/chars.py`, `generator/grammar2decoder.py`,
`generator/check_decoder.py`, `generator/validate_grammar.py`, `generator/check_grammar_roundtrip.py`,
`generator/check_vendor_spec.py`, `generator/ir2spec.py`, `generator/bootstrap_yeast.py`. Rename `DEFAULT_SPEC` to
`DEFAULT_GRAMMAR` while you are there, and update the Makefile's `check_spec_roundtrip.py` invocation.

- [ ] **Step 4: Delete the bootstrap**

`generator/bootstrap_yeast.py` was a one-shot. `grammar/yeast.yaml` is now hand-maintained, and a script that would
overwrite it with an unannotated skeleton is a loaded gun.

```bash
git rm generator/bootstrap_yeast.py
```

`generator/ir2spec.py` imports `INDICATORS` from it — move that dict into `ir2spec.py`, which is where it belongs: it is
the erasure's business, not the bootstrap's.

- [ ] **Step 5: Run everything**

Run: `make pc`

Expected: green. Every gate — round-trip, validation, vendor-spec recovery, decoder tables, C tests, coverage.

- [ ] **Step 6: Update the docs**

`DESIGN.md`, under `## Pieces`, replace the "Parser generator" bullet:

```markdown
- **Grammar** — `grammar/yeast.yaml`: libyeast's grammar. The YAML 1.2 productions, with the indicator characters
  restored to the productions that name them, and annotated with the yeast token codes — which production wraps its
  match in `Begin`/`End` markers, and what code each consumed character is given. The vendored
  `third_party/yaml-grammar/yaml-spec-1.2.yaml` cannot serve: it inlines the indicator characters, and so cannot say
  that `"` is an indicator opening a scalar but meta inside an escape. `make check-grammar` erases the annotations and
  the indicator productions and checks that what remains is the vendored grammar, so the additions cannot quietly become
  changes.
- **Parser generator** — `generator/`: `ir.py` (the typed IR), `yeast2ir.py` (read `grammar/yeast.yaml`), `ir2yeast.py`
  (the inverse), `ir2spec.py` (erase libyeast's additions), `chars.py` (the decoder's character model),
  `grammar2decoder.py` (emit `src/decoder_tables.h`), and the gates `check_grammar_roundtrip.py`,
  `check_vendor_spec.py`, `validate_grammar.py` and `check_decoder.py`. Python 3 + PyYAML.
```

`CHANGELOG.md`, under `## [Unreleased]` / `### Added`:

```markdown
- Annotated grammar: `grammar/yeast.yaml` carries the YAML 1.2 productions together with the yeast token codes they
  emit, which the official grammar cannot express — it inlines the indicator characters and so loses the structure the
  token layer hangs on. A gate erases the annotations and recovers the official grammar exactly, so libyeast's grammar
  is hand-authored where it must be and machine-proved where it can be.
```

- [ ] **Step 7: Update `PLAN.md`, which now says two false things**

`PLAN.md` §2 claims the single-line simple-key rule bounds all deferral, and treats the vendored grammar as a complete
source. Both are now known false: the vendored grammar has no token layer, and the indentation detections look ahead
across unbounded runs of comment or empty lines. Rewrite the two paragraphs — "The one load-bearing fact" in §0 and
"Where the rewind problem went" in §2 — to say that the simple-key rule bounds the *key* deferral only, that the
indentation detections are the other deferral, and that libyeast resolves them by consuming and emitting rather than
looking ahead (Task 8). Add `grammar/yeast.yaml` to the §2 architecture as the generator's input.

- [ ] **Step 8: Run the gate and commit**

```bash
make reformat && make pc
git add -A
git commit -m "refactor: the annotated grammar becomes the generator's source"
```

______________________________________________________________________

## Task 8: Indentation detection — the second pass

**Not part of this plan's commits.** It is written down here so it is not forgotten, and because it is the first thing
to do next.

The vendored grammar leaves two things undefined, and says so:

> The m variable is set explicitly in rules 183 and 187, albeit by an undefined special rule called
> `<auto-detect-indent>`. In rules like 185 it assumes that m is stored as a state/stack variable and has been set
> somewhere else.

The reference defines them, and libyeast will **not** copy how:

```haskell
detect_scalar_indentation n     = peek $ (nb_char *) & (b_non_content & (l_empty n BlockIn *) ?) & count_spaces (-n)
detect_collection_indentation n = peek $ (nonEmpty l_comment*) & count_spaces (-n)
count_spaces n                  = (s_space & count_spaces (n .+ 1)) / result (max 1 n)
```

`peek` looks ahead across an unbounded run of comment lines (collections) or empty lines (block scalars) without
consuming them, and the reference then parses them a second time. A backtracking parser can afford that. libyeast
cannot: unbounded lookahead means unbounded input retention, which for a streaming parser is a memory bound an attacker
chooses.

**libyeast consumes instead of peeking.** The tokens of the skipped lines do not depend on `m` — comment lines are
tokenized against nothing at all, and the empty lines are matched at the *outer* `n`, producing exactly the tokens they
would produce at `n+m`. So they can be emitted as they are crossed, and only then is `m` measured, from the first line
that is not skippable. One forward pass, nothing retained, no lookahead.

That needs: value-returning productions in the IR (the reference's `do m <- …`), the three detection productions in
`grammar/yeast.yaml`, and the first entry in `check_vendor_spec.py`'s `DEVIATIONS` — stating that libyeast consumes
where the official grammar's `<auto-detect-indent>` is silent, and why.

It also needs a decision we have not made: the reference treats a leading all-space line longer than the detected indent
as *content*, where the YAML spec calls it an empty line and an error. That is a real divergence between the reference
and the spec, and the differential oracle will trip over it. Settle it before writing the productions, not after.

______________________________________________________________________

## Appendix A — the whole-production annotations

Read from `third_party/yamlreference/Text/Yaml/Reference.bnf`. `indicator`/`meta`/`text` become `(token)` with that
code; `wrapTokens A B` becomes `(wrap)`.

| #   | production                   | annotation                                 |
| --- | ---------------------------- | ------------------------------------------ |
| 004 | `c-sequence-entry`           | `indicator`                                |
| 005 | `c-mapping-key`              | `indicator`                                |
| 006 | `c-mapping-value`            | `indicator`                                |
| 007 | `c-collect-entry`            | `indicator`                                |
| 008 | `c-sequence-start`           | `indicator`                                |
| 009 | `c-sequence-end`             | `indicator`                                |
| 010 | `c-mapping-start`            | `indicator`                                |
| 011 | `c-mapping-end`              | `indicator`                                |
| 012 | `c-comment`                  | `indicator`                                |
| 013 | `c-anchor`                   | `indicator`                                |
| 014 | `c-alias`                    | `indicator`                                |
| 015 | `c-tag`                      | `indicator`                                |
| 016 | `c-literal`                  | `indicator`                                |
| 017 | `c-folded`                   | `indicator`                                |
| 018 | `c-single-quote`             | `indicator`                                |
| 019 | `c-double-quote`             | `indicator`                                |
| 020 | `c-directive`                | `indicator`                                |
| 021 | `c-reserved`                 | `indicator`                                |
| 029 | `b-as-line-feed`             | `token LineFeed`                           |
| 030 | `b-non-content`              | `token Break`                              |
| 041 | `c-escape`                   | `indicator`                                |
| 042 | `ns-esc-null`                | `meta`                                     |
| 043 | `ns-esc-bell`                | `meta`                                     |
| 044 | `ns-esc-backspace`           | `meta`                                     |
| 045 | `ns-esc-horizontal-tab`      | `meta`                                     |
| 046 | `ns-esc-line-feed`           | `meta`                                     |
| 047 | `ns-esc-vertical-tab`        | `meta`                                     |
| 048 | `ns-esc-form-feed`           | `meta`                                     |
| 049 | `ns-esc-carriage-return`     | `meta`                                     |
| 050 | `ns-esc-escape`              | `meta`                                     |
| 051 | `ns-esc-space`               | `meta`                                     |
| 052 | `ns-esc-double-quote`        | `meta`                                     |
| 053 | `ns-esc-slash`               | `meta`                                     |
| 054 | `ns-esc-backslash`           | `meta`                                     |
| 055 | `ns-esc-next-line`           | `meta`                                     |
| 056 | `ns-esc-non-breaking-space`  | `meta`                                     |
| 057 | `ns-esc-line-separator`      | `meta`                                     |
| 058 | `ns-esc-paragraph-separator` | `meta`                                     |
| 062 | `c-ns-esc-char`              | `wrapTokens BeginEscape EndEscape`         |
| 063 | `s-indent`                   | `token Indent`                             |
| 064 | `s-indent-lt`                | `token Indent`                             |
| 065 | `s-indent-le`                | `token Indent`                             |
| 066 | `s-separate-in-line`         | `token White`                              |
| 072 | `b-as-space`                 | `token LineFold`                           |
| 084 | `ns-directive-name`          | `meta`                                     |
| 085 | `ns-directive-parameter`     | `meta`                                     |
| 086 | `ns-yaml-directive`          | `meta`                                     |
| 087 | `ns-yaml-version`            | `meta`                                     |
| 088 | `ns-tag-directive`           | `meta`                                     |
| 090 | `c-primary-tag-handle`       | `wrapTokens BeginHandle EndHandle`         |
| 091 | `c-secondary-tag-handle`     | `wrapTokens BeginHandle EndHandle`         |
| 093 | `ns-tag-prefix`              | `wrapTokens BeginTag EndTag`               |
| 095 | `ns-global-tag-prefix`       | `meta`                                     |
| 096 | `c-ns-properties`            | `wrapTokens BeginProperties EndProperties` |
| 097 | `c-ns-tag-property`          | `wrapTokens BeginTag EndTag`               |
| 101 | `c-ns-anchor-property`       | `wrapTokens BeginAnchor EndAnchor`         |
| 103 | `ns-anchor-name`             | `meta`                                     |
| 104 | `c-ns-alias-node`            | `wrapTokens BeginAlias EndAlias`           |
| 105 | `e-scalar`                   | `wrapTokens BeginScalar EndScalar`         |
| 106 | `e-node`                     | `wrapTokens BeginNode EndNode`             |
| 137 | `c-flow-sequence`            | `wrapTokens BeginSequence EndSequence`     |
| 140 | `c-flow-mapping`             | `wrapTokens BeginMapping EndMapping`       |
| 142 | `ns-flow-map-entry`          | `wrapTokens BeginPair EndPair`             |
| 159 | `ns-flow-yaml-node`          | `wrapTokens BeginNode EndNode`             |
| 160 | `c-flow-json-node`           | `wrapTokens BeginNode EndNode`             |
| 161 | `ns-flow-node`               | `wrapTokens BeginNode EndNode`             |
| 163 | `c-indentation-indicator`    | `indicator`                                |
| 188 | `ns-l-block-map-entry`       | `wrapTokens BeginPair EndPair`             |
| 198 | `s-l+block-in-block`         | `wrapTokens BeginNode EndNode`             |
| 203 | `c-directives-end`           | `token DirectivesEnd`                      |
| 204 | `c-document-end`             | `token DocumentEnd`                        |
| 210 | `l-any-document`             | `wrapTokens BeginDocument EndDocument`     |

## Appendix B — the internal annotations

The annotation attaches to a node inside the production, not to the production. The reference body is given verbatim so
the placement is unambiguous. `&` is sequence, `/` is ordered choice, `%` is repetition, `!` and `^` are error messages
(ignored in this pass).

### [059] `ns-esc-8-bit`

```haskell
indicator 'x' ! "escaped" & meta ( ns_hex_digit % 2 )
```

### [060] `ns-esc-16-bit`

```haskell
indicator 'u' ! "escaped" & meta ( ns_hex_digit % 4 )
```

### [061] `ns-esc-32-bit`

```haskell
indicator 'U' ! "escaped" & meta ( ns_hex_digit % 8 )
```

### [075] `c-nb-comment-text`

```haskell
wrapTokens BeginComment EndComment
                           $ c_comment & meta ( nb_char *)
```

### [082] `l-directive`

```haskell
( wrapTokens BeginDirective EndDirective
                       $ c_directive ! "doc"
                       & "directive"
                       ^ ( ns_yaml_directive
                         / ns_tag_directive
                         / ns_reserved_directive ) )
                     & s_l_comments
```

### [092] `c-named-tag-handle`

```haskell
wrapTokens BeginHandle EndHandle
                                $ c_tag & meta ( ns_word_char +) & c_tag
```

### [094] `c-ns-local-tag-prefix`

```haskell
c_tag & meta ( ns_uri_char *)
```

### [098] `c-verbatim-tag`

```haskell
c_tag & indicator '<' & meta ( ns_uri_char +) & indicator '>'
```

### [099] `c-ns-shorthand-tag`

```haskell
c_tag_handle & meta ( ns_tag_char +)
```

### [109] `c-double-quoted`

```haskell
wrapTokens BeginScalar EndScalar
                              $ c_double_quote ! "node" & text ( nb_double_text n c ) & c_double_quote
```

### [112] `s-double-escaped`

```haskell
( s_white *)
                             & wrapTokens BeginEscape EndEscape ( c_escape ! "escape" & b_non_content )
                             & ( l_empty n FlowIn *)
                             & s_flow_line_prefix n
```

### [117] `c-quoted-quote`

```haskell
wrapTokens BeginEscape EndEscape
                         $ c_single_quote ! "escape" & meta '\''
```

### [120] `c-single-quoted`

```haskell
wrapTokens BeginScalar EndScalar
                               $ c_single_quote ! "node" & text ( nb_single_text n c ) & c_single_quote
```

### [131] `ns-plain`

```haskell
wrapTokens BeginScalar EndScalar
                                $ text (case c of
                                             FlowOut  -> ns_plain_multi_line n c
                                             FlowIn   -> ns_plain_multi_line n c
                                             BlockKey -> ns_plain_one_line c
                                             FlowKey  -> ns_plain_one_line c)
```

### [150] `ns-flow-pair`

```haskell
wrapTokens BeginMapping EndMapping
                           $ wrapTokens BeginPair EndPair
                           $ ( ( c_mapping_key ! "pair" & s_separate n c
                               & ns_flow_map_explicit_entry n c )
                             / ns_flow_pair_entry n c )
```

### [164] `c-chomping-indicator`

```haskell
indicator '-' & result Strip
                               / indicator '+' & result Keep
                               / result Clip
```

### [165] `b-chomped-last`

```haskell
case t of
                                  Strip -> emptyToken EndScalar & b_non_content
                                  Clip  -> b_as_line_feed & emptyToken EndScalar
                                  Keep  -> b_as_line_feed
```

### [168] `l-keep-empty`

```haskell
( l_empty n BlockIn *)
                              & emptyToken EndScalar
                              & ( l_trail_comments n ?)
```

### [170] `c-l+literal`

```haskell
do emptyToken BeginScalar
                              c_literal ! "node"
                              (m, t) <- c_b_block_header n `prefixErrorWith` emptyToken EndScalar
                              text ( l_literal_content (n .+ m) t )
```

### [174] `c-l+folded`

```haskell
do emptyToken BeginScalar
                             c_folded ! "node"
                             (m, t) <- c_b_block_header n `prefixErrorWith` emptyToken EndScalar
                             text ( l_folded_content (n .+ m) t )
```

### [183] `l+block-sequence`

```haskell
do m  <- detect_collection_indentation n
                                     wrapTokens BeginSequence EndSequence $ ( s_indent (n .+ m) & c_l_block_seq_entry (n .+ m) +)
```

### [186] `ns-l-compact-sequence`

```haskell
wrapTokens BeginNode EndNode
                                  $ wrapTokens BeginSequence EndSequence
                                  $ c_l_block_seq_entry n
                                  & ( s_indent n & c_l_block_seq_entry n *)
```

### [195] `ns-l-compact-mapping`

```haskell
wrapTokens BeginNode EndNode
                                 $ wrapTokens BeginMapping EndMapping
                                 $ ns_l_block_map_entry n
                                 & ( s_indent n & ns_l_block_map_entry n *)
```

### [211] `l-yaml-stream`

```haskell
( nonEmpty l_document_prefix *)
                        & ( eof / ( c_document_end & ( b_char / s_white / eof ) >?) / l_any_document )
                        & ( nonEmpty ( "more" ^ ( ( l_document_suffix ! "more" +) & ( nonEmpty l_document_prefix *) & ( eof / l_any_document )
                                                / ( nonEmpty l_document_prefix *) & "doc" ^ ( wrapTokens BeginDocument EndDocument l_explicit_document ?) ) ) *)
```
