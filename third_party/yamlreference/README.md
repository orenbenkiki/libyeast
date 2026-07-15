# yamlreference

The Haskell YAML reference parser, vendored from <https://github.com/orenbenkiki/yamlreference>.

`Text/Yaml/Reference.bnf` is the YAML grammar written as parser combinators, and — unlike the vendored
`yaml-grammar/yaml-spec-1.2.yaml` — it carries the **token annotations**: which productions wrap their match in
`Begin`/`End` markers, and which code each consumed character is given. Its `Code` type is where libyeast's `ys_code`
comes from, constructor for constructor.

It is here to be **read**. libyeast's grammar (`grammar/yeast-spec-1.2.yaml`) replicates its token decisions by hand, as
our own work and in our own idiom, and departs from it where we judge better — it uses `peek` for indentation detection,
where libyeast consumes and emits instead, so that libyeast needs no cross-line lookahead. No code is copied from here,
and none is linked against.

`tests/` is the source libyeast's own conformance suite is migrated from: each fixture is a YAML fragment named
`production[.n=N][.c=C][.t=T].case`, and its `.output` sibling is the exact YEAST token stream this parser produces for
that production — captured once, so libyeast is checked against it without ever running Haskell. The filename encodes
the production and its parameters; an `.invalid` case is an input this parser rejects. `generator/migrate_tests.py`
selects the fixtures that align with libyeast's grammar and rewrites each into what libyeast emits (see the differences
from the reference in `DESIGN.md`), writing `tests/spec/`; from there the suite is libyeast's to own. The bytes matter
(`b-break.crlf.input` is CR LF), so `.gitattributes` keeps both these fixtures and the migrated ones out of line-ending
normalization.

**It is LGPL** (`lgpl.txt`), while libyeast is MIT — but its author holds libyeast's copyright too, so the fixtures are
reused here with that permission. No source is copied: `tests/` is captured data, and nothing here is built, linked, or
distributed as part of libyeast.
