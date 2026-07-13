# yamlreference

The Haskell YAML reference parser, vendored from <https://github.com/orenbenkiki/yamlreference>.

`Text/Yaml/Reference.bnf` is the YAML grammar written as parser combinators, and — unlike the vendored
`yaml-grammar/yaml-spec-1.2.yaml` — it carries the **token annotations**: which productions wrap their match in
`Begin`/`End` markers, and which code each consumed character is given. Its `Code` type is where libyeast's `ys_code`
comes from, constructor for constructor.

It is here to be **read**. libyeast's grammar (`grammar/yeast.yaml`) replicates its token decisions by hand, as our own
work and in our own idiom, and departs from it where we judge better — it uses `peek` for indentation detection, where
libyeast consumes and emits instead, so that libyeast needs no cross-line lookahead. No code is copied from here, and
none is linked against.

Later it becomes the differential oracle: libyeast's token stream is compared against this parser's, token for token.

**It is LGPL** (`lgpl.txt`), while libyeast is MIT. Nothing here is built, linked, or distributed as part of libyeast.
