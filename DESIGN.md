# libyeast design

libyeast is a YAML 1.2 parser in C, generated from the formal grammar. It is a _token_ parser: it produces a yeast token
stream — a lossless representation of the document's structure — and stops there. Composing those tokens into a node
graph, resolving anchors, aliases and tags, constructing native values, and the model questions that ride along
(duplicate mapping keys, mapping key order) are a higher layer's and out of scope. Its one departure from YAML 1.2 is
that it reads UTF-8 only, forgoing the UTF-16 and UTF-32 the spec also asks for. This document is a map: it names the
pieces and how they relate, and points at where each piece's design and rationale live — in that piece's own source (its
file or its comments). The full public API surface is declared, but the parser core is not yet implemented — parsing
returns a "not implemented" error — so what exists is the project framework and this facade.

## Pieces

- **Public API** — `include/yeast.h`: the API surface and its behavioral contract, documented inline as Doxygen comments
  (published to GitHub Pages). The version query; the token-source surface — `ys_new_yaml_memory_parser` /
  `ys_new_yaml_stream_parser` / `ys_new_yeast_stream_reader` make a `ys_token_source`, `ys_read_token` pulls from it,
  and `ys_delete_token_source` releases it — with `ys_fd_reader`/`ys_fp_reader` adapters; the yeast wire writer and YAML
  emitter; a pluggable allocator; and the `ys_counting_allocator` leak counter. An `int`-returning function reports a
  `ys_status`: `YS_OK`, or a negative failure with `errno` set — `YS_FAILED_ACTION` where the call fails on its own
  terms (nothing left to read, nothing writable), the others where a delegate under it does.
- **Token source** — `src/token_source.h` and `src/token_source.c`: one `ys_token_source` over tokens however they are
  made — YAML parsed from memory or a stream, or a yeast wire replayed from a stream — so code over tokens runs the same
  whichever it is. It is a tagged union: the parser's arm and the wire's arm hold genuinely different state (a window,
  queue, stack and automaton against a line buffer), so only the kind is above them. `ys_read_token`,
  `ys_are_tokens_stable` and `ys_delete_token_source` dispatch on it.
- **Token sink** — `src/token_sink.h` and `src/token_sink.c`: the mirror, a `ys_token_sink` over tokens however they are
  consumed. Two arms — the yeast writer, which serializes tokens to a wire through a `ys_bytes_writer`, and the YAML
  emitter, which writes the bytes each token spans so a wire replayed through it reconstructs the input the tokens came
  from — and code writing tokens does not know which took them. `ys_write_token` feeds it and `ys_delete_token_sink`
  releases it, the delete flushing the byte transport — where a buffered write finally reaches its destination, and so
  where a full disk is first seen.
- **Wire format** — `src/wire.c` and `src/wire.h`: the yeast wire format, one character and its escaped text per token,
  which the writer arm writes and a yeast-wire token source reads back with `ys_read_token` — together with the table
  that says which character each code is. That format is what a token stream is compared against YamlReference in, and
  what lets one be piped between tools; it is complete and works. The reader treats the wire as untrusted: a malformed
  wire is a `YS_CODE_ERROR` token, bad data like a bad document, with a located message; a host failure while reading it
  — out of memory, or a byte source that fails — is `ys_read_token`'s return value, not a token.
- **Memory** — `src/memory.h` and `src/memory.c`: allocation through a `ys_allocator`, and `ys_memory` — what an object
  may allocate and what it has. Everything that grows goes through it, so `ys_options::max_bytes` has exactly one door,
  and the parser and the wire-format reader are held to their cap by the same code rather than by two copies of it.
- **Source** — `src/source.h` and `src/source.c`: bytes read from a `ys_bytes_reader`, and the buffer they land in.
  Compact to what is still needed, grow only if that left no room, read into the tail. The parser reads its input
  through one of these and so does the wire-format reader; they had one each, and the two drifted — one grew a buffer
  the size of the whole stream and the other did not, one told a reader's failure from the end of the input and the
  other did not. That is the argument for their having one.
- **Version** — `src/version.c`: the version query, and the load-time constructor that refuses a library built without a
  version at all. **Counting allocator** — `src/counting_allocator.c`: the leak counter. **Stream adapters** —
  `src/streams.c`: the file-descriptor and `FILE *` adapters for `ys_bytes_reader` and `ys_bytes_writer`, which share a
  file because they share the act of closing.
- **Parser** — `src/parser.h` and `src/parser.c`: the parsing arm of a token source — its whole execution state, and the
  runtime that keeps it. A window over the input, a stack of the productions the parser is inside, a queue of the tokens
  it has built but not yet handed back, and the state it is in — none of it in the C call stack, which is what lets
  `ys_read_token` hand back a token from the middle of a production and resume there on the next call. `parser.h` says
  why each piece is shaped as it is: why the queue's undecided tokens are a suffix and the marker injected ahead of them
  needs no room made for it, why the stack carries `n` and nothing carries `c`, and why an error can always be reported.
  A resource failure — the reader, or the cap or the allocator — is recorded as the arm's `fault` and reported as
  `ys_read_token`'s return, not as a token; there is one terminal `is_done` state, reached by a clean end-of-stream or
  by a fault alike. There is one automaton, not a scanner and a parser: in yeast the automaton's output already *is* the
  token stream, so a second layer would need a vocabulary that does not exist — and would be the one thing on the hot
  path the grammar did not derive. The automaton itself is not generated yet (see `PLAN.md`); what is here is everything
  it will run on.
- **Messages** — `src/messages.h` and `src/messages.c`: what libyeast says to its caller, as one table of static strings
  indexed by name, so that all of it can be read in one place and swapped for another language. The messages that depend
  on the grammar live in `grammar/messages.yaml`, keyed by the code a `(cut)` or an `(error)` names — the one source the
  interpreter reads and the C table will be generated from, gated so the two cannot drift. Each says what was expected
  and never what was found, the byte that failed being the first unparsed token behind the error. That table is not
  generated into `src/parser_tables.h` yet. The messages `src/messages.c` keeps for itself are the ones no grammar can
  reach: the wire reader's, which answer for a broken wire rather than for a parse. Running out of memory and a reader
  that failed are not among them — they are `ys_read_token`'s return value, a `ys_status`, not a token with text, so the
  token model stays about the data and not about the machine running on it. That is also why `tests/spec/` pins
  `YS_CODE_ERROR` and no other error code: a fixture is a grammar and an input, and a host failure is a property of
  neither — when a cap trips depends on how `ys_memory_grow` grows a buffer, and a reader fails for reasons the input
  cannot express. So the host failures are the C parser's alone, and `tests/test_parser.c` covers them there with a
  refusing allocator and a drip reader.
- **Decoder** — `src/decoder.h`, `src/decoder.c` and the generated `src/decoder_tables.h`: the bottom layer, which turns
  input bytes into characters the parser can branch on. A character becomes a 32-bit key holding the id of the character
  if the grammar names it, one bit per character set the grammar tests, and the bytes it consumed — so a test is one
  comparison or one AND, with the grammar's unions and subtractions already evaluated into the bits. No Unicode
  codepoint is ever assembled: tokens are spans of input bytes, so nothing compares a character's numeric value, and the
  decoder validates and classifies instead of decoding. That is also why no existing UTF-8 library serves — they all
  exist to produce the codepoint we do not want, or to validate in bulk without classifying at all. `decoder.h`
  documents the key; `decoder.c` holds the UTF-8 mechanics, which are RFC 3629's and not the grammar's.
- **Grammar** — `grammar/yeast-spec-1.2.yaml`: libyeast's grammar, and the source everything else is generated from. It
  is the YAML 1.2 productions with three additions: each indicator character is reached through the production that
  names it; 98 of the 211 productions carry the yeast token codes — which productions bracket their match in
  `Begin`/`End` markers, and what code each consumed character is given; and six rules are libyeast's own — the root the
  parser runs, `l-yeast-stream` (a YAML stream, and then the end of the input), and the
  `l-recover`/`l-recover-entry`/`l-unparsed`/`nb-unparsed`/`s-indent-le-line` that answer for input it cannot parse.
  Four of them carry `r`, the resume policy, which is `ys_options.resume` and the grammar's fifth parameter: finite like
  `c` and `t`, so it specializes away at generation time rather than being threaded like `n`. The vendored
  `third_party/yaml-grammar/yaml-spec-1.2.yaml` cannot serve as the source: it inlines the indicator characters, so it
  cannot say that a quotation mark opens a scalar as an indicator but is meta inside an escape, and it names no token at
  all. `make verify-spec` erases the annotations and the indicator productions, sets libyeast's own rules aside, and
  checks that what remains is the vendored grammar, production for production — so what libyeast adds cannot quietly
  become what libyeast changes, and a departure must be declared, with its reason, in `check_vendor_spec.py`.
- **Parser generator** — `generator/`: `ir.py` (the typed grammar IR — every node a dataclass, and every node spelling
  the productions it references via `references()`, so reachability is read off the nodes themselves), `annotated2ir.py`
  (read `grammar/yeast-spec-1.2.yaml` into the IR), `ir2annotated.py` (the inverse), `ir2spec.py` (erase libyeast's
  additions and recover the official grammar), `chars.py` (the character model the decoder is built from),
  `grammar2decoder.py` (emit `src/decoder_tables.h`), `wire.py` (the yeast wire format in Python), `spec_tests.py` (the
  conformance fixtures), `interpreter.py` (a backtracking interpreter of the grammar, run against those fixtures),
  `normalize.py` (the ordered pipeline of semantics-preserving transformations toward the canonical form, each step's
  output swept of the shape it leaves in a body, the productions that only call something else, the ones that behave
  alike, and the ones no parse can enter — reachability closed from the productions a parse enters by name, the root's
  copy under each resume policy and the recovery a failed cut lands on), `determinize.py` (the divergence analysis that
  derives a conflict's provisional decision), `star.py` (the YAML Test Suite folded to events), and the gate checks
  `check_annotated_roundtrip.py`, `check_vendor_spec.py`, `validate_grammar.py`, `check_markers.py`,
  `check_grammar_docs.py`, `check_messages.py`, `check_decoder.py`, `check_spec_tests.py`, `check_wire.py`,
  `check_emitter.py`, `check_provisional.py`, `check_interpreter.py`, `check_grammar_coverage.py`, `check_star.py`,
  `check_normalize.py` and `check_determinize.py`, which report through `gate.py`. A fixture whose production the sweep
  takes out of a pipeline stage is not dropped: `check_normalize` pins it to the last stage whose grammar can run it,
  holds it there token for token, and credits coverage from where it stands — so the stranded fixtures (a family a
  speculation replaced, a nullable production its consuming copy replaced, a bare monomorphic copy only a fixture
  enters, `c-reserved`, which the spec defines and nothing references) go on guarding the last grammar that reaches
  them. This is where the grammar-derived parser will be generated (see `PLAN.md`); it runs on Python 3 + PyYAML.
- **YamlReference** — `third_party/yamlreference/`: the Haskell YAML 1.2 reference parser, vendored to be read. Its
  grammar carries the token annotations `grammar/yeast-spec-1.2.yaml` replicates, and its `Code` type is where `ys_code`
  comes from. It is LGPL, while libyeast is MIT: no source is copied from it, nothing links against it, and nothing of
  it is built. Its `tests/` fixtures were the source libyeast's own conformance suite (`tests/spec/`) was built from
  once, and are kept only to be read against — see the differences from YamlReference below, and the
  reference-interpreter phase in `PLAN.md`.
- **Build** — `CMakeLists.txt` is the source of truth for building, testing, installing, and the version. It defines the
  shared + static libraries (hardened, symbol-visibility controlled), the sanitized Debug and hardened Release configs,
  and the coverage option. No list of files is kept by hand, here or in the `Makefile`: the sources and the tests are
  globbed, with `CONFIGURE_DEPENDS` to reconfigure when the set changes, so a new file cannot be left out of the build
  or slip past the gate — which a hand-kept list is exactly what allows.
- **Gate** — `Makefile` wraps CMake as the incremental pre-commit gate `make pc`, a pure aggregator of five sub-gates:
  `all` (the build), `test` (Debug + Release tests and the `// UNTESTED` coverage gate), `verify` (the fifteen generator
  gates, `verify-roundtrip` through `verify-decoder`), `vet` (formatting, lint, comment rule, marker scan,
  version-drift, packaging), and `gh-pages` (Doxygen docs + gcovr coverage report). Stamp-file targets keep it
  incremental.
- **CI** — `.github/workflows/`: one workflow per sub-gate (`vet.yml`, `test.yml`, `verify.yml`, `gh-pages.yml`) plus
  `codeql.yml`, each producing an independent status badge. `gh-pages.yml` publishes the Doxygen docs and the coverage
  report to GitHub Pages; the coverage-percentage badge reads a JSON published there. `dependabot.yml` keeps the pinned
  GitHub Actions current.
- **Quality scripts** — `scripts/`: `check_comments.py` (comment-style rule), `coverage_gate.py` (the `// UNTESTED`
  contract), `coverage_badge.py` (coverage-percentage badge JSON), `check-deps.sh` (tool presence), and the
  `install-*-deps.sh` dependency installers. Every generator gate reports through `generator/gate.py`, so that a failure
  reads the same wherever it came from and no gate can report success by forgetting to exit.
- **Packaging** — `cmake/*.in` (relocatable pkg-config + CMake package config), `conanfile.py` (Conan), and
  `ports/yeast/` (vcpkg). The version flows from the single CMake source into all of them; `make check-version` guards
  against vcpkg drift.
- **Docs** — `Doxyfile` drives the API docs from the header comments, completeness-gated: an undocumented public symbol
  or a missing `@param`/`@return` fails the build.

## The normalization pipeline, one goal at a time

The pipeline in `generator/normalize.py` is a sequence of phases, each owning one invariant: a phase adds steps until
that count is none, and from its end the law's "none stays none" makes every later step keep it. A phase finished with a
green corpus is a checkpoint that lands on its own. The order is dependency's rather than the meter's — Phase 0 settles
`no-i-t-parameters`, neither the chomping nor a block scalar's indentation mode declared, passed or read; Phase 1
settles `every-difference-is-between-character-sets` and then `every-character-question-is-a-character-set`, and follows
the specialization because a set the context picks denotes nothing until a caller is known; Phase 2 settles
`no-f-parameter`, the block scalar's leading-empty floor; Phase 3 settles `no-m-parameter`, the detected indent; Phase 4
settles `no-n-parameter`, the indentation itself; Phase 5 is the empties, and settles
`every-way-is-either-empty-or-consumes`, then `every-production-is-either-empty-or-consumes`, then
`no-nested-production-matches-empty`; Phase 6 is the wrappers, one invariant per kind of scope; Phase 8 is the
flattening, which settles `no-choice-of-choices` by writing out a choice that a way of a choice calls, so every way of
it stands where a gate can be put on it rather than one call below. A step written where its goal's other steps already
ran is a smaller step, against a grammar with less in it.

One invariant belongs to no phase. `every-option-is-reachable` is claimed from the moment the contexts are monomorphized
and every step after answers for it: a choice goes on to its next way exactly where the one in front of it fails and is
handed back, so a way no input refuses leaves nothing for the ways behind it to be entered on. Backtracking hides it —
the way matches, the continuation fails, the parse returns and tries the next — and a machine that never returns simply
loses them. It reads none from the claim through the whole pipeline.

A way is refused where a character it needs is not there, where a guard it asks declines, or where its gate turns it
away. It is not refused past a `(cut)`, nor inside a committed region, a failure there being the message that region
names rather than a way handed back. That is one question — whether *some* input refuses the way — and not the narrower
one of what happens on a character the way cannot start with, which is what a gate hoist needs and what `_does_refuse`
answers for it. What a machine could tell the ways apart by is `every-way-carries-a-test`, asked where the gates exist.
One reading answers each — `_can_be_refused` for the first, `_entry_of` for the second.

**Every question about a node is asked through `ir.Reading`**, a table from node kind to what to do about it, because
the alternative — a chain of `isinstance` tests ending in a fallthrough — answers permissively for whatever spelling its
author did not think of, and reports its own blindness as a property of the grammar. A kind the table was not told about
raises, naming the reading; a handler nothing ever reaches is reported by `unexercised` once the whole corpus has run,
since only then is a kind known to be unreachable rather than merely unmet. Those two pin each table to exactly the
kinds that occur, so a reading says nothing about what it cannot see and a kind added to the IR touches only the
readings that actually meet it, on the day they do. `NEVER` is how a reading keeps a wide group and takes back the part
of it that cannot arrive — checked rather than believed, since reaching one raises. A table naming a kind twice will not
build: the groups overlap — `Error` and `PushMessage` are actions and commits both — and a chain of tests settles that
by its order with nothing saying which order was meant.

Three of the readings answer with a `Verdict` rather than a value, which is what lets a walk over a way's items be a
table too: whether to take the item, step over it, stop there, follow the call it makes, or treat it as the commit past
which failing is an error rather than a refusal.

Every scope is a pair, the recovery included. What answers for a failed cut is `PushRecovery(recovery, resume)` before
the call it covers and `PopRecovery` where that call returns — both named outright, so nothing about the region is
implied by where it sits and a rewrite that moves a way moves it with the actions. It is the last scope to be written
because it is the only one whose close carries information: the others restore and are done, this one resumes, and where
a way carries on only has a name once the way is a call and a continuation. Two productions are minted per site, one
holding the pop and one holding the resume, since a way that ends at the call it covers has neither a place to close the
region nor a name to carry on at.

A scope that holds what it covers has nowhere to stand in an alternative — `gate  actions…  [P1  actions…]  [P2]` has a
place for an action and none for a node enclosing a call, and a `(token)` around a call is an action that must run where
the call returns. So each becomes the pair that brackets it, which the interpreter already implements independently of
the wrapper it stands for: a `(wrap)` its two markers, a `(max)` the window pair, a `(commit)` the message pair, and a
`(token)` the code pair. A `(recover)` is not among them — a handler rather than a scope, with no close whose position
means anything — and rides the alternative's edge instead. What the wrapper gave by construction — `ir.Wrap` is a node
rather than its two markers precisely so a `begin` cannot lose its `end` — the pairs are held to instead: a scope is
closed on the path that opens it, the ways of a choice agree on what they leave open, and a run's turn leaves none,
since a second turn would open it again. The path and not the way, once a way hands control on: what stands past a call
is a production of its own, so a `PushCode` before the call and its `PopCode` in the continuation are one pair meeting
on the parse's own stack. A way's calls are not alike there — the one it carries on at is the rest of the same path, and
one it comes back from must come back level, the continuation waiting behind it being the caller's and not the callee's.
The markers are not that: a pair of them crosses productions by design, and what follows them through the pipeline is
owed by the phase that splits a way into a call and a continuation, which is the first thing that can put a `begin` in
one production and its `end` in another.

What a wrapper displaced waited in the frame of the match that was running, where a pair's waits on the parse's own
state — which is the point, a frame being gone once a way is split into a call and a continuation — so what a frame
unwound for free is now something to clear. An abandoned parse's scopes are taken off where it is abandoned, at the
in-grammar `(recover)` that answers for the cut and at the stream's own level where nothing does, and a parse that
matches is refused if it ends with a window or a committed region still open.

The empties are what a caller cannot decide on. Entering a production that may match nothing is a choice made with no
character to go on, and it stays one while both answers live under a single name — so each such production is given a
name for the ways that take a character and a name for the ways that take none, and becomes the choice between the two.
The split is the same match in the same order: a sequence's ways come out as its parts already offer them, and no
alternation in the grammar has an empty way ahead of a reading one. Where a shape cannot say the two apart locally it is
said differently rather than argued about — a possessive scan's empty way is the negative peek that is exactly when it
takes nothing, a counted repetition's is the count being non-positive, and a commit is lifted over the choice so one
message scope stands around both ways rather than one around each, which would make the reading way's failure the error
instead of a step on the way to the empty one.

Naming the two ways is half of it: while the choice sits behind the production's own name, a caller still reaches it
without knowing whether anything will be taken, and there is nowhere to put a gate. So the choice is written at the call
site instead — `A ::= F (X_reads | X_empty)` — where the parse already stands. The way around it is not split to do
that: `A ::= F X_reads | F` would run `F` twice, where one alternation inside the sequence duplicates nothing.

What is left matching empty is then what only ever took nothing — the residue a split named, and the productions that
were actions alone — and a name is worth having where it stands for a decision. There is none in a way that consumes
nothing and always ends where it began, so each is written into the call sites that enter it and the caller's own way
says what it does. What keeps its empty ways is the root and the recovery: a parse enters both by name rather than by a
call, so there is no call site to hold the choice and no caller to make it blind.

A carried value stops being one parameter at a time, smallest first, because the mechanism is what is being proved and
not the value: `f` is read by one production, so a floor that nested would show up over four reads rather than over
hundreds. What licenses a global is that its value does not nest, and the interpreter says so rather than the argument —
a `(set)` puts the value on a stack of that global's own and a `(clear)` takes it off, the single slot stands beside it,
and every read where the two differ is one a slot could not have answered. `check_normalize` gates that count at none.
The clear is what makes the count mean anything: without one nothing pops, the stack is the slot by construction, and
the net cannot fail.

The indentation is not one of those. It is one value per region rather than one for the parse — a nested collection's
entries are measured against their own — so it goes on the parse's own stack, pushed where it changes and taken back
where that region ends. Both halves of a pair sit in one way of one production, the level being known nowhere else, and
what says a push stands where it should is that the parameter stays beside the stack while they go in: every read
compares the two over the whole corpus, and only then does the parameter go. The one indentation a call establishes
rather than is entered under — a block scalar's, which its first content line measures — is made a push of its own
first, since a write whose readers are a production away is exactly what the parameter's removal would take apart.

What the count is for is naming the shapes that have to change before a value can be one. A detected indent read once,
beside the write, is a value one place holds; the same value read on every turn of a loop is not, everything the loop
enters detecting its own in between — and the count named exactly those, 843 of them, before the loop was given the
indentation it had already measured. So the shape is answerable rather than argued over: the number says something is
wrong, a step changes the shape, and the number says whether that was it.

## The three rules the normalization pipeline is held to

Three rules bind every transformation in `generator/normalize.py`, and they matter more than any one step does, so they
are written here rather than left to be inferred from the code.

**Many simple steps, never few clever ones.** A step does one thing. One found doing two is split — a split changes no
grammar, buys a name on the corpus diff and a smaller rule to prove by eye, and costs nothing, the pipeline being a
list.

**Two things are compared by making them look alike and testing equality**, never by an equivalence rule that knows what
they mean. Where a factoring cannot see that two ways share a prefix, what is missing is a step that puts the shared
part where a prefix is — an explicit reordering into a canonical form, an inlining that brings the pieces into one list
— after which `==` decides it. A rule that reasons about whether two different-looking chains amount to the same thing
is a rule in the wrong shape: it cannot be proved by eye, it is where the subtle bugs live, and every one met so far
turned out to be a canonical form that had not been written down. When a simplification looks impossible, the first
hypothesis is a missing normalizing step, not an inherent conflict.

**A step establishes an invariant, and carries the test for it.** The normalization pipeline works by accumulating
properties: each step makes the grammar simpler in one stated way, everything after it may lean on that, and it is the
accumulation that brings the grammar within reach of simple machinery — common-prefix factoring, gate disjointness —
rather than any one clever transformation. So a step is not a function; it is
`Step(name, transform, invariants, reduces, lapses, untestable)`. An invariant counts the places it is broken, being a
count and not a yes-or-no; a step naming one is taken to finish it, and `reduces` names the ones it only lowers, for a
count several steps share. `lapses` is a written reason for breaking one and the only licence to, and `untestable` is
the reason a step has no invariant at all — what it makes true being momentary, or a property of a run rather than of a
shape. The pipeline enforces the law itself — a count never rises, a settling step leaves none, none stays none, a lapse
nobody takes is stale — so a property established in the middle cannot lapse silently at the end, which is exactly what
happened while properness was checked at one site only. The phase's own meter is one of these counts and not a number
beside them. A new step is designed in that order: the invariant first, then how to measure it, then how to achieve it —
and the test is written before the transform and watched to fail, a test never seen to fail proving nothing.

**A named site is a code smell, and the target is none.** Every declaration in the pipeline's tables — a site named for
inlining, a two-way choice named for reordering, a helper named for absorbing, a production named as committed — is a
place a universal rule was not found and a hand-picked target stood in for it. The base grammar matches the right
language and emits the right tokens, so mechanical universal steps should reach a deterministic grammar with nothing
singled out; a singled-out site is evidence of a step not yet written. A reordering rule is the sharpest of the four,
alternative order being semantics under backtracking-with-commits: a swap needs a per-site argument, where the right
transformation would have made the two orderings compare equal and left nothing to swap. Each declaration carries its
reason, the counts are printed on the gate line, and the staleness net refuses one the analysis has caught up with — but
the standing question at every one of them is *what universal step would retire this?*, never *what other site deserves
one?*

**Not yet held — the one thing in this document that is not yet true.** Everything else here describes what the code
does; this section describes what it must do, and the pipeline does not satisfy it today. The comparisons the second
rule asks for are not all written. What the first rule asks for is met: no step does two things, and every step must
change the grammar — one that does not is a fault named where it stands, since a step goes idle when what it looks for
has stopped reaching it, which is a regression in the step before it. Nothing is declared, so nothing is singled out: no
point of interest is tracked and no declaration table stands, each phase re-deriving what it needs. Reaching conformance
with all three comes before driving the determinize meter down: a meter driven down over steps of the wrong shape buys a
number and keeps the debt. The qualification in this paragraph comes out when the pipeline conforms, and the three rules
then stand as a hard constraint on every step after.

## An indentation is measured where it is consumed

The official grammar establishes an indentation by looking ahead for it. `<auto-detect-indent>` skips the rest of the
current line where the parse stands mid-line, skips however many empty lines follow, and answers with the indentation of
the first line holding a character other than a space — a read of unbounded length before a single character is
consumed. A block collection then measures every entry against that answer, and a block scalar its every content line.
libyeast cannot run that: the parser reads forward, decides on the character in hand, and holds tokens only where a
decision genuinely spans them. So the grammar says it another way, and the reasons are worth having in one place because
they explain a class of departures rather than a single rule.

**Every indentation is a column.** The arithmetic the official grammar writes around a detection is one value said three
ways: a compact collection's `n+1+m` is the column its run of spaces ends at, since the run begins one past an indicator
at column `n`; a block collection's `n+m` is its first entry line's column; a block scalar's `n+m` is its first content
line's column. `<column>` says that directly, and the `max(1, …)` clamp the official grammar carries becomes what it
always meant — the run must leave the parse deeper than the indentation in force, or this way does not apply.

**One span, then a gate on what it measured.** A line's indentation is taken as a single run and the checks are made on
the result: `s-indent-le` has always been `(***) s-space` followed by a test on `(len) (match)`, and the delayed
detections are written the same way. The indentation is never split into two consumes, and never consumed twice.

This is sound because **a run over a character class is possessive** and gives nothing back, so peeking a length and
then consuming exactly that many characters is the same parse as consuming the run — the peek was buying nothing. Each
rewrite is therefore an identity, and the conformance corpus and the YAML Test Suite hold every one of them to it, token
for token.

**The value is passed, not written**, unless the write is meant to travel. A parameter passed as itself is passed by
reference in this grammar, which is how a block header hands its detected indent up to the scalar that asked. It also
means a `(set)` of a declared parameter escapes into every caller that passes it bare: `s-l+block-collection` calls
`l+block-mapping` with a plain `n` where the sequence goes through `seq-spaces`, an asymmetry the official grammar
itself writes, so a write in one would escape and in the other would not. The collections therefore hand the established
indentation to `l-block-seq-entries` and `l-block-map-entries` — libyeast's own — as an argument.

**The block scalar was the last of them, and the hardest**, because its indicator decides and its first content line is
three calls away. So the decision travels as `i`, a finite parameter with two values — `given`, the indicator named the
indentation, and `detected`, it did not and the first content line is what says it — and `monomorphize` specializes it
into the names exactly as it does the context, so the generated parser tests no mode. The two differ by one node in
`s-indent-floor`: exactly `n` spaces, or one span with the guards on what it measured. Where the scalar has no content
line at all, the trailing empty lines are what would have said the indentation, so each is taken whole and the widest of
them is the floor — which is what `keep` needs, those lines being the scalar's own content, and what the YAML Test
Suite's `JEF9/01` holds it to.

`generator/interpreter.py` evaluates no `<auto-detect-indent>`, and no production libyeast runs reads ahead of what it
has consumed. The IR node stays only because the vendored grammar spells it and one reader loads them both.

## Differences from YamlReference

libyeast's goal is a fast, correct YAML 1.2 parser for YAMLStar and its kin — not a byte-for-byte replica of
YamlReference. Where the token stream a caller sees differs from YamlReference's, it is a decision, and every one is
here with its reason. (Deviations from the _official grammar_ are a separate matter, declared with their reasons in
`generator/check_vendor_spec.py`.) The conformance suite is migrated with these differences applied, so YamlReference's
fixtures go on testing libyeast rather than a parser it is not.

- **UTF-8 only.** libyeast reads UTF-8 and nothing else; YamlReference detects and reads UTF-16 and UTF-32 too. The
  decoder classifies UTF-8 bytes straight into a key without ever assembling a codepoint, and tokens are spans of those
  bytes — a design the other encodings would fight (a second classifier, codepoint assembly to serialize a token,
  source-byte marks). YAML 1.2 asks a conformant parser for UTF-16, and UTF-32 where it accepts JSON; libyeast forgoes
  them for now. Non-UTF-8 inputs are simply left out of the fixtures.
- **A byte-order mark is the character, not the encoding.** libyeast's `bom` token holds the mark it matched (`U+FEFF`);
  YamlReference's holds the name of the encoding it detected (`UTF-8`). Detecting no encoding, libyeast has no name to
  give.
- **No token spans a line.** libyeast cuts every run at a line break: a skipped line comes back as two `unparsed`
  tokens, one for its content and one for its break, where YamlReference can hand back a single token across the break.
  A token that spanned a line would make a stream parser's output depend on how much of the input its buffer held.
- **After an error, libyeast stops parsing the document; YamlReference recovers and continues.** On a malformed document
  libyeast ends the parse and, by default, returns the rest of the input as `unparsed` tokens — or, with
  `YS_RESUME_DOCUMENT`, skips to the next document and parses that, the skipped lines coming back unparsed. It restarts
  *at* the `---` or `...`, which the resumed document then parses as its own: that marker is the only thing the recovery
  stops for, being where the grammar's `c-forbidden` says a document may begin, so a `---` mid-line or without white or
  a break after it is not a boundary and stays unparsed. `YS_RESUME_INDENT` stops at the next line no more indented than
  the entry that failed as well, and carries on *inside* the document: a malformed entry costs its container that entry
  and not the rest of them, and the recovered entries are its siblings rather than children of the one that failed. The
  three are one hierarchy — nothing, then a document marker, then a marker or a less-indented line — so each gives up
  less of the input than the one before it, and where nothing encloses the failure the indent policy *is* the document
  one. The reference instead keeps tokenizing past the error, recovering into structured tokens of its own. So the two
  streams agree only up to the first error, and that is where the fixtures stop comparing. The message differs too:
  libyeast's names the production it was in and what it expected, not YamlReference's wording, and what carries the
  meaning is the position — the first `unparsed` token behind the error begins at the byte that failed.
- **An error closes what it opened.** A `begin-` marker gets its `end-` on every path, the errored ones included: the
  abandoned parse's open markers are closed at the error, after the error token and before the first `unparsed` — all
  three are zero-width and at the byte that failed, so only their order says that the error is inside what failed and
  the unparsed run is inside nothing. Without this the fold that rebuilds the production tree has nothing to stand on
  once a document is malformed, and `YS_RESUME_DOCUMENT` could not keep its promise: the documents after the error would
  parse as children of the one that failed rather than as its siblings, and a caller could not reach them. What is open
  is read off the marker codes and never off the rule that emitted them, which is the only way a block scalar is read at
  all: it opens with a marker of its own rather than a bracketing rule, the position of its close depending on the
  chomping. Every fixture's output is held to this, `check_markers` having nothing to say about a path that failed.

Some further differences never reach the token stream a caller sees, so they are not in the list above — they are helper
productions that diverge only when run alone, and agree once composed into a document:

- libyeast consumes and emits the indentation YamlReference peeks at, so it needs no cross-line lookahead.
- It flattens the character-class helpers it uses only inside a `Diff`, so a helper run _alone_ emits `unparsed` where
  YamlReference emits its tokens — invisible in a real document, since the helper only ever appears in a subtraction.
- It follows the spec's factoring of the plain-scalar `:`/`#` exclusion, and YamlReference does not. The spec keeps
  `ns-plain-safe-out`/`-in` (rules 128/129) as `ns-char` (and `ns-char - c-flow-indicator`) and excludes `:`/`#` in
  `ns-plain-char` (rule 130), with its two exceptions; YamlReference instead subtracts `:`/`#` up in 128/129 and makes
  130 just `ns-plain-safe`. So run alone, `ns-plain-safe-out(':')` matches for libyeast and errors for YamlReference —
  but a full plain scalar accepts the same characters either way (verified against YamlReference's own fixtures).

## Memory safety

The Debug build is AddressSanitizer-instrumented on all three OSes (plus UndefinedBehaviorSanitizer on Linux/macOS
Clang; MSVC has no UBSan), so use-after-free and buffer overflows fail the tests everywhere. Leaks are caught per
platform: on Linux the Debug build's LeakSanitizer flags them at each test's exit; on macOS — where Apple clang has no
LeakSanitizer — the Release test run is passed through the `leaks` tool; MSVC has no leak sanitizer. The portable,
deterministic net is `ys_counting_allocator`: route a parser's allocations through it and assert
`ys_counting_allocator_live_buffers()` is 0 after the parser is freed (the facade tests do exactly this).

The roadmap — what is left to build — lives in `PLAN.md`.
