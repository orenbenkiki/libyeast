# libyeast design

libyeast is a YAML 1.2 parser in C, generated from the formal grammar. It is a _token_ parser: it produces a yeast token
stream — a lossless representation of the document's structure — and stops there. Composing those tokens into a node
graph, resolving anchors, aliases and tags, constructing native values, and the model questions that ride along
(duplicate mapping keys, mapping key order) are a higher layer's and out of scope. Its one departure from YAML 1.2 is
that it reads UTF-8 only, forgoing the UTF-16 and UTF-32 the spec also asks for. This document is a map: it names the
pieces and how they relate, and points at where each piece's design and rationale live — in that piece's own source (its
file or its comments). The full public API surface is declared, but the parser core is not yet implemented — reading a
token from a parser fills a single error token reading "not implemented" — so what exists is the project framework and
this facade.

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
  path the grammar did not derive. The automaton is not generated, and the runtime here is partial — built for what the
  grammar asked when it was written, and narrower than what it asks now: the queue holds one undivided open run with a
  single marker ahead of it, where the grammar's provisional vocabulary marks a run and names a side of that mark.
- **Messages** — `src/messages.h` and `src/messages.c`: what libyeast says to its caller, as one table of static strings
  indexed by name, so that all of it can be read in one place and swapped for another language. The messages that depend
  on the grammar live in `grammar/messages.yaml`, keyed by the code a `(cut)` or an `(error)` names — the one source the
  interpreter reads, gated against the grammar's own error sites so a renamed code or an orphaned message fails the
  build. Each says what was expected and never what was found, the byte that failed being the first unparsed token
  behind the error. That table is not generated into `src/parser_tables.h` yet. The messages `src/messages.c` keeps for
  itself are the ones no grammar can reach: the wire reader's, which answer for a broken wire rather than for a parse.
  Running out of memory and a reader that failed are not among them — they are `ys_read_token`'s return value, a
  `ys_status`, not a token with text, so the token model stays about the data and not about the machine running on it.
  That is also why `tests/spec/` pins `YS_CODE_ERROR` and no other error code: a fixture is a grammar and an input, and
  a host failure is a property of neither — when a cap trips depends on how `ys_memory_grow` grows a buffer, and a
  reader fails for reasons the input cannot express. So the host failures are the C parser's alone, and
  `tests/test_parser.c` covers them there with a refusing allocator and a drip reader.
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
  names it; 104 of the 224 productions carry the yeast token codes — which productions bracket their match in
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
  speculation replaced, a bare monomorphic copy only a fixture enters, `c-reserved`, which the spec defines and nothing
  references) go on guarding the last grammar that reaches them. It runs on Python 3 + PyYAML.
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
  `all` (the build), `test` (Debug + Release tests and the `// UNTESTED` coverage gate), `verify` (the fourteen
  generator gates, `verify-roundtrip` through `verify-decoder`; `verify-provisional` and `verify-determinize` stand
  aside, neither having anything to read in the pipeline as it stands), `vet` (formatting, lint, comment rule, marker
  scan, version-drift, packaging), and `gh-pages` (Doxygen docs + gcovr coverage report). Stamp-file targets keep it
  incremental.
- **CI** — `.github/workflows/`: one workflow per sub-gate (`vet.yml`, `test.yml`, `verify.yml`, `gh-pages.yml`) plus
  `codeql.yml`, each producing an independent status badge. `gh-pages.yml` publishes the Doxygen docs and the coverage
  report to GitHub Pages; the coverage-percentage badge reads a JSON published there. `dependabot.yml` keeps the pinned
  GitHub Actions current.
- **Quality scripts** — `scripts/`: `check_comments.py` (comment-style rule), `wrap_long_comments.py` (reflows a comment
  block to the column limit, and checks one), `coverage_gate.py` (the `// UNTESTED` contract), `coverage_badge.py`
  (coverage-percentage badge JSON), `check-deps.sh` (tool presence), and the `install-*-deps.sh` dependency installers.
  Every generator gate reports through `generator/gate.py`, so that a failure reads the same wherever it came from and
  no gate can report success by forgetting to exit.
- **Packaging** — `cmake/*.in` (relocatable pkg-config + CMake package config), `conanfile.py` (Conan), and
  `ports/yeast/` (vcpkg). The version flows from the single CMake source into all of them; `make check-version` guards
  against vcpkg drift.
- **Docs** — `Doxyfile` drives the API docs from the header comments, completeness-gated: an undocumented public symbol
  or a missing `@param`/`@return` fails the build.

## Why the parser is shaped this way

A YAML parser sits on one horn of a dilemma. A mechanically faithful one — backtracking over the productions as written
— can go superlinear; a hand-written state machine is fast and O(n) but its conformance is established test by test,
faithful by luck rather than by derivation. libyeast is meant to be both: a deterministic, committed,
character-at-a-time automaton with an indentation stack and bounded deferred-token tracking, generated from the
parameterized productions, so its speed comes from the state machine and its fidelity from the derivation.

**The one load-bearing fact:** YAML restricts implicit ("simple") keys to a **single line**. That restriction is what
makes determinization finite, makes the deferred-token set bounded, and makes a pull `ys_read_token` able to return
without draining the whole document. It does triple duty, and the architecture rests on it. It bounds the *key* deferral
and nothing else — there is a second deferral, and the block scalar is where it lives.

**Indentation detection is not the problem.** A block collection's indentation, and an inline one's, are read straight
off the current column — YamlReference peeks past comment lines first, but `s-l-comments` has already eaten them, so
there is nothing to peek past. Those two cost no lookahead at all.

**The empty lines that open a block scalar are the problem, and the chomping is why.** An empty line there is content if
a content line follows it — `l-empty` — and is chomped away if none does — `b-non-content`. The same line, told apart by
something that has not happened yet. So none of those tokens can be handed back until the parser reaches a content line
or the end of the scalar, and the run of them has no bound: YAML bounds lookahead only for implicit keys, at 1024
characters, and says nothing at all here. Nor is this an artefact of yeast — the *value* depends on it too: `|-` with
two blank lines and nothing after is `""`, and with `text` after is `"\n\ntext"`, so any parser that produces a value
looks exactly as far.

So libyeast queues them. The tokens of the run are built and held, none handed back; when the run resolves, either they
become content and the scalar's end arrives later, or `end-scalar` is **injected ahead of them** and they become the
breaks that were chomped away — the marker's position is what says they were never content. `end-block-scalar` exists to
emit that marker, and without it an empty stripped block scalar opens a scalar it never closes.

`max_bytes` bounds it, being the same guard a single enormous token needs. The buffered input, the tokens held back with
it, and the stack that deep nesting grows are capped together, since a run that is never resolved grows all three; past
the cap `ys_read_token` returns `YS_FAILED_MEMORY`, the caller's sizing to fix.

**An explicit pushdown automaton, not a call stack.** The pull API — the caller invokes `ys_read_token` and the parser
does not call back — forces the lowering target, and the speed and streaming requirements agree with it: all three want
the same non-recursive machine. A recursive-descent shape keeps "where am I" in the C return-address chain, which cannot
suspend to hand back a token, so what the generator emits is a state enum, an explicit heap stack and a single dispatch
loop. Suspension is then free: run the loop until a token is produced, save the state, return. That is libyaml's shape,
and libyaml's API is pull for the same reason.

**The deferred set is the token queue with a resolution tag.** The possibly-key, possibly-scalar hypothesis is a queue
entry marked undecided; a read hands back the frontmost decided token, advancing the input only where the head is still
undecided or the queue is empty. Because the ambiguity is line-bounded, the buffering before an honest return is bounded
too.

**Where the rewind problem went.** A backtracking parser would have to discard emitted tokens on every failed
alternative. A committed automaton does not backtrack — a transition emits on commit — so there is nothing to rewind.
The only undecided tokens are the ones inside a line-bounded lookahead; they live in the queue above and are retyped
there if the hypothesis fails. The single-line rule that bounds the deferral bounds the retention with it. Indentation
detection, the other deferral, retains nothing at all: it consumes and emits as it goes.

## Six parameters, two fates

The productions are indexed by six parameters, and the move the whole generator turns on — a binding-time analysis — is
to sort them into two fates and treat those completely differently. `c` and `n` are the exemplars.

- **`c` — context · static.** `c` ranges over a **finite** set (block-in, block-out, flow-in, flow-out, block-key,
  flow-key), so it is specialized away at generation time: each `c`-parameterized production monomorphizes into at most
  six concrete ones, and it is gone from the runtime.
- **`n` — indentation · runtime.** `n` is an **unbounded** integer threaded as `s-indent(n)`, `s-indent(<n)`,
  `s-indent(≤n)`. It cannot be specialized away and survives into the emitted automaton, carried on the indentation
  stack.

The others follow the same two fates: **`t`** (chomping — strip, clip, keep) and **`r`** (the resume policy
`ys_options.resume` chooses) are finite and specialize away like `c`; **`m`** (the auto-detected indent) and **`f`**
(the floor a block scalar's leading empty lines set for its first content line) are unbounded integers and are held like
`n` — not threaded, in the end, but read off the one slot each, which is what *What the parse may hold* is about. So the
runtime carries `n`, `m` and `f` and never sees `c`, `t` or `r`: the emitted C is one automaton per resume policy, and
`ys_options.resume` picks the start state.

Getting the split right is the crux: partial-evaluate over `c` while *preserving* `n`.

```
# a production, before and after c-specialization
ns-plain(n, c)          ::= parameterized on both

  # becomes, at generation time:
ns-plain-blockKey(n)    # c pinned → concrete automaton fragment
ns-plain-flowIn(n)      # n still threaded → indentation stack
```

## The normalization pipeline, one goal at a time

The pipeline in `generator/normalize.py` is a sequence of phases, each owning one invariant: a phase adds steps until
that count is none, and from its end the law's "none stays none" makes every later step keep it. A phase finished with a
green corpus is a checkpoint that lands on its own. A step written where its goal's other steps already ran is a smaller
step, against a grammar with less in it.

Which phase owns which invariant, and which steps serve it, is declared in `STEPS` beside the steps themselves and is
not repeated here: a second telling is a second thing to hold true, and it is the telling rather than the list that goes
stale. What is worth saying here is the shape. The order is dependency's rather than the meter's: the parameters go
before anything that reads a grammar without resolving a call, the character questions follow the specialization because
a set a context picks denotes nothing until a caller is known, the scopes become pairs before a way is cut into a call
and a continuation, and the gates come last because there is nothing to gate until a body is an ordered list of ways.
`invariant_faults` holds the whole list to the law, and `unsettled_invariants` names what the final grammar still breaks
— one count, `every-conditional-way-is-gated`, which the last phase lowers and does not finish.

**The shape comes before the determinizing, and the reasons are structural rather than a preference.**

- A decision point in the tree has no identity. `Seq(a, (x | y), b)` decides in the middle of a sequence, and its follow
  is `b` and whatever the caller's is, so a commit-safety certificate would be a statement about a context that minting
  a continuation then changes. Determinizing first means proving each one, reshaping, and proving it again.
- A determinizer walks configurations of `(production, alternative, cursor)` — subset construction over gated ways. On a
  tree there is nothing for it to park at, so determinizing first means a second determinizer for a shape on its way
  out.
- The reshaping breaks what determinism rests on, by design: giving a choice a production of its own hands nullability
  back, and splitting a way into a call and a continuation moves what a certificate was written against. Determinism
  first pays that cost once per shape step, for ever.
- A speculation's mark and injections stand where a shared prefix ends, which is a cursor into an alternative. In the
  tree that boundary is a path through nested nodes, and it moves whenever the nesting does.

Only the *universal* shape lands blind. Reshaping that a conflict alone justifies waits and is pulled at named sites,
and the determinize meter arrives with the gates rather than before them: a count over the tree measures a shape about
to be discarded, and its number would not be comparable to the one that matters.

One invariant belongs to no phase of its own. `every-option-is-reachable` is settled where the contexts are
monomorphized, and every step after answers for it: a choice goes on to its next way exactly where the one in front of
it fails and is handed back, so a way no input refuses leaves nothing for the ways behind it to be entered on.
Backtracking hides it — the way matches, the continuation fails, the parse tries the next — and a machine that commits
simply loses them. It reads none from there through the whole pipeline.

**Gate, peek, guard — three words, each for one thing.**

- A **gate** is the *field* of an alternative on which the choice is made. It is a set of guards — no order between
  them, each a question about the one position the alternative is entered at — and the alternative is taken only where
  every one holds. A gate is asked where the alternative is entered, which is what makes it the only place a decision
  can stand. A gate holding nothing is the unconditional fallthrough, which only the last alternative may carry.
- A **peek** is the question about the character in front of the parse: a `LookGuard` over a `CharSet`. It is one guard
  among the rest rather than a field of its own, and the one question the generated parser answers by indexing the
  decoder's key.
- A **guard** is a zero-width node that decides — `Look`, `NegLook`, `LookBehind`, `StartOfLine`, `EndOfStream`, `Le`,
  `Lt`, `EndMustConsume`. A guard belongs in a gate; one reached among a way's actions is a decision asked a step too
  late. A `(cut)` is not one of these: it takes nothing either, but it commits the parse rather than asking it anything,
  and it stands with the actions. Nor do all of them ask about the input — `EndMustConsume` asks whether the turn it
  closes took a character, which is why the gate holding it is the one entered after that turn rather than the turn's
  own.

"Test" is none of these and names nothing: it has stood for all three and for a probe besides. No name in the generator
uses it in any of those senses — `Invariant.test` is the one that keeps the word, and there it means what it says, the
check that counts where an invariant is broken.

A way is refused where a character it needs is not there, where a guard it asks declines, or where its gate turns it
away. It is not refused past a `(cut)`, nor inside a committed region, a failure there being the message that region
names rather than a way handed back. That is one question — whether *some* input refuses the way — and `_can_be_refused`
is the one reading that answers it, `every-option-is-reachable` being what holds the grammar to it. What a gate hoist
needs is narrower: not whether some input refuses the way, but what the character in front of it can be, which is
`_ahead_of_gate`. Whether every way something decides to enter carries a gate at all is
`every-conditional-way-is-gated`, asked from where the gates exist.

**The states a decision stands in are a small finite space.** Every guard asks about one axis of it: the character in
front of the parse, the character behind, whether it stands at a line start, whether it stands under indentation, and
two bits of its own bookkeeping — what the last limited scan did, and whether the region a `StartMustConsume` opened has
taken anything. Five of those are booleans, so a *standing* is one of 32 assignments to them, and `spaces.SubSpace` is a
set of states: the characters admitted under each standing it admits anything under. Every axis being finite, union,
intersection and containment are computed standing by standing and nothing is widened to make an answer fit. The end of
the stream is the character axis's own value rather than the absence of a character, a way entered there being a way
entered somewhere. `check_spaces` judges the algebra by the states it holds — enumerating every standing over an
alphabet spanning each boundary its cases name, and comparing the operations against set arithmetic on the enumerations
— because an algebra checked against itself proves nothing.

The two are read from different halves of a way, which is what makes their agreement mean something: the gated subspace
from its guards, the accepted subspace from its consumes and calls, and neither from the other's. Every consume names
the set it consumes and consumes it — no consume has ever consumed nothing, at any stage, over the whole corpus — so a
gate moving to a caller takes nothing away from what the way says it does.

`normalize._admits` reads each guard as one of these, and `accepted_spaces` says where each production can begin taking
a character: a least fixpoint from nothing, the walk over a way carrying what it can still stand in having taken
nothing, so that a guard past a take — which asks about a later position — narrows nothing. It is **sound rather than
decisive**. A comparison between two of the parse's own values fixes no coordinate and admits everywhere, which is true
of it rather than a shrug; a literal's first character is a real constraint where the rest of the literal is a residual
the axes never speak for. So what the space refuses, the grammar refuses, and three residuals stay runtime tests by
design: the indentation comparisons, a literal past its first character, and a literal peek's follow class. Whether a
way can take nothing is `_split_ways`' answer and is not folded in — a production that succeeds taking nothing succeeds
anywhere, which is true and says nothing, and holding the two apart is what keeps it from spreading through the
fixpoint.

What the two are held to so far is the leaf ways — the ways holding no call, which answer entirely by their own actions.
`_asked_where_entered` gives the guards asked along each path into a production, gathered from the last take onward, and
met with a way's own gate those are the states a parse may enter it in. Two things are read of every leaf way:
`accepted-and-gated-charsets-are-equal`, that it takes exactly the characters it is entered on, compared standing by
standing so that a caller knowing more than the way asks — that it stands at a line start where the way asks only about
the character — is not a disagreement; and `every-path-reaches-a-leaf-way`, that a path reaches some way of the
production it enters. Not every way: `l-document-prefix` offers one that takes a byte order mark and one that takes
nothing, and the path reaching it at the end of the stream can take only the second, there being no mark there. A path
reaching none of them is a call no input completes, and that is what is counted.

**Every question about a node is asked through `ir.Question`**, a table from node kind to what to do about it, because
the alternative — a chain of `isinstance` tests ending in a fallthrough — answers permissively for whatever spelling its
author did not think of, and reports its own blindness as a property of the grammar. A kind the table was not told about
raises, naming the reading; a handler nothing ever reaches is reported by `unexercised` once the whole corpus has run,
since only then is a kind known to be unreachable rather than merely unmet. Those two pin each table to exactly the
kinds that occur, so a reading says nothing about what it cannot see and a kind added to the IR touches only the
readings that actually meet it, on the day they do. `NEVER` is how a reading keeps a wide group and takes back the part
of it that cannot arrive — checked rather than believed, since reaching one raises. A table naming a kind twice will not
build: the groups overlap — `Error` and `PushMessage` are actions and commits both — and a chain of tests settles that
by its order with nothing saying which order was meant.

**So a question about the grammar is decided, never recognised.** A question that matches shapes and answers for
whatever it did not match is wrong in a way that hides: every step which rewrites a shape silently changes its answer.
`_takes_none_only_at_the_end` admitted as much in its own docstring — *"recognised rather than decided"* — and the
`l-recover` circuit it reported read none, then twenty, then none again, each number saying something about the question
rather than about the grammar. The permissive polarity is worse still: a walk answering "yes, this may move" for a shape
nobody classified is a transformation nobody checked. What tells a recognizer from an honest conclusion is where the
decision is made — a recognizer decides per node with an untyped tail, a conclusion decides every node through a total
dispatch and returns what the loop found, so a trailing `return False` is not itself the smell.

**And a question about a way reads the way's gate.** `_items_of_way` gives what a way *performs* and deliberately not
its gate, which is right for a rewrite — a rewrite keeps the gate untouched — and wrong for nearly every question, since
the gate is what decides whether the way is entered at all. A walk without it reports what a way would do if it were
always taken. That defect reached three readings in one sitting: a span's `Look` moved into the gate and the scan read
as empty again, a hoisted `EndOfStream` went invisible and a recovery circuit appeared from nowhere, and
`_entered_unconsumed` walked through an end-of-stream gate as though a parse with input left could enter it.
`_parts_of_way` is the accessor for a question — the gate's guards, then what the way performs, in the order the parse
meets them. Either rule may be broken with a written reason at the site; neither may be broken silently.

A question answers with whatever is asked of it: `_is_one_char` a yes-or-no, `_split` the pair of halves a match has,
`_peek_spans` the codepoint intervals a set admits or nothing where it is not pinned down. What every one of them shares
is the dispatch, not the answer.

Not every scope is a pair. What answers for a failed cut rides the edge an alternative already has — `recover` beside
the call it protects — so a rewrite that moves the way moves the handler with it, and there is nothing to open and
nothing to close. The IR does name a `PushRecovery`/`PopRecovery` pair and the interpreter has handlers for both,
holding the recovery and the resume together so an unwind reads where to stop and where to carry on from one place; no
step writes them, which the unexercised-handler report says out loud by listing both. Among the pairs that are written,
a close is not always silent: a settled region's says where a failure unwinds to, and a must-consume region's decides,
being a guard rather than an action.

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
The markers are not that: a pair of them crosses productions by design, which the phase that splits a way into a call
and a continuation is the first thing to make happen — a `begin` in one production and its `end` in another.

What a wrapper displaced waited in the frame of the match that was running, where a pair's waits on the parse's own
state — which is the point, a frame being gone once a way is split into a call and a continuation — so what a frame
unwound for free is now something to clear. An abandoned parse's scopes are taken off where it is abandoned, at the
in-grammar `(recover)` that answers for the cut and at the stream's own level where nothing does, and a parse that
matches is refused if it ends holding any scope open, whichever of the seven kinds it is.

The empties are what a caller cannot decide on. Entering a production that may match nothing is a choice made with no
character to go on, and it stays one while both answers live under a single name. What the pipeline does about that
today is to take the empty match out of the nodes that hide it: an optional becomes the alternation it already is, so
its empty way stands beside the way that reads; a run over a character class becomes the scan it is, entered on the
class, since what such a run takes is a value the input decides rather than a way the parse chooses; and both
repetitions are said as the ways they are, a turn and a recursion under a region that settles them.

That is as far as it goes. A production something decides to enter may still match empty, no step gives one a name for
each of the two things it is, and `no-conditional-production-matches-empty` is carried by no step in the pipeline —
which `STEPS` says where the phase is declared, and `unsettled_invariants` would report the day a step claimed it. The
reading that tells the two apart does exist and is used: `_is_nullable` and `_split` are what
`no-production-reaches-itself-unconsumed` asks whether a cycle can turn without taking a character. The rest is
`PLAN.md`'s.

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
enters detecting its own in between — and the count names exactly those. So the shape is answerable rather than argued
over: the number says something is wrong, a step changes the shape, and the number says whether that was it.

## The canonical form

A **terminal production** is a set of characters, nothing more. Every other production is an ordered list of
alternatives, and an alternative is `gate  actions…  [P1  actions…]  [P2]`:

- The **gate** is a conjunction tested without consuming, as above. An empty gate is the unconditional fallthrough,
  allowed only as the last alternative.
- **actions** operate on the parse's own state. Taking the peeked character is itself an action rather than part of the
  gate — `ConsumePeeked` likewise takes a peeked literal on the gate's word, the bytes never scanned twice.
- **P1, P2** are zero, one or two productions the alternative hands control to. Two means run P1 and carry on at P2:
  push P2 as where to carry on, go to P1 — so P1 is the call, P2 the continuation, and there is at most one push per
  edge. One is a tail goto. Nothing follows P2, so a sequence of three splits through a helper, `A → B A₁` with
  `A₁ → C D`, and the `_<N>` suffix names where it came from.

Alternatives are asked in order and the first whose gate holds is the one taken. Two alternatives may share a gate;
order resolves the overlap, and proving the earlier one safe to commit to is the whole of determinization.

**No unbounded lookahead survives.** A `Look`, `NegLook`, `LookBehind` or `(exclude)` over more than one character is
transformed away — into a character-set gate, a literal peek, a cheap guard, or a speculation — so the canonical grammar
holds none of them. The one bounded exception is the gate's own `LiteralPeek`: the longest literal plus one character of
follow test, within the window the parser's fill already guarantees, lowered to a single comparison.

## What the parse may hold

**Every value the parse carries is a global singleton, possibly empty, or an entry in the one unified stack.** There is
no third place — nothing a call holds of its own, no scope implied by the tree shape, no slot reachable only from where
it was written. The C parser is a state machine and that stack, so a value fitting neither is one it cannot hold, and a
transformation producing one has produced something the parser cannot run whatever the corpus says.

- *Globals* are what does not nest: the position and its mark, the open run and the code its characters carry, the
  `(max)` window and the count of opens standing over it, `m` and `f` — computed indentations rather than scopes.
- *The unified stack* is what nests: the indentation in force, the code a `(token)` displaced, the regions a commit, a
  recovery, a settled run and a must-consume turn open, and where to carry on. All are pushed and popped by actions the
  grammar writes, never by anything a call does on their behalf.

**And every push is written down.** There is no call, and so nothing a call implicitly pushes or pops: a production that
goes on to another pushes where to carry on and jumps, and where it carries on from is what a pop takes. Reading a call
as "push a continuation, then go" is what keeps the stack safe to transform. A value riding on something a call pushes
is a footgun, because the pipeline inlines — the sweep splices do-nothing calls, and `expand-called-ways` writes a
callee's ways where the call stood — and a value living on what they remove has nowhere to go and no gate that could see
it coming. Written as actions, an inlining deletes a continuation push and a jump and touches nothing else, because
nothing was ever riding them.

```
call P, carry on at Q       PushContinuation(Q) ; GOTO P
carry on                    Pop ; GOTO what it held
the indentation changes     PushIndent(n) … PopIndent
a `(token)` opens           PushCode(code) … PopCode
```

The parser holds one other store, and it is not state: the **pending-token run**, the output a speculation has emitted
but not yet committed to. It is a second stack in the implementation and nothing like the first in kind — the unified
stack holds what the parse must give back, this holds what the parse has produced and may still retype. Only one is open
at a time, its extent is written in the grammar by `OpenProvisional`/`CommitProvisional`, and nothing reads a value out
of it. So the invariant covers state, and the pending run is accounted for separately rather than smuggled into it.

## The six rules the normalization pipeline is held to

Six rules bind every transformation in `generator/normalize.py`, and they matter more than any one step does, so they
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
`Step(name, transform, settles, reduces, establishes, lapses, untestable)`. An invariant counts the places it is broken,
being a count and not a yes-or-no; `settles` names the ones it takes to none, `reduces` the ones it only lowers, for a
count several steps share, and `establishes` the ones that were no question in front of it, the shape they are about
being what the step builds. `lapses` is a written reason for breaking one and the only licence to, and `untestable` is
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

**A transformation is a local, mechanical rule.** It reads a production's own nodes, plus at most the grammar-wide
tables that are themselves defined production by production — what can be in front, what follows, the reference graph —
and its correctness argument is stated against exactly that. No step may lean on a global property of the parse ("this
is only ever attempted at a line start", "this position is always preceded by X") however true by construction: a rule
that needs one is the wrong rule, and the right one spells the same fact locally, usually in a device the grammar
already owns. The only judgment a step may embody is *where* it applies, never what the result looks like at a site.

**Every transform is read for correctness against the semantics of the nodes it moves**, by hand, whatever the gates
say. The gates are a net and not a substitute: a transformation that moves an action into another production, or copies
one without binding its parameters, changes nothing the corpus can see wherever the sites it hits happen to be
identities, and stays wrong at the next site. The reading is cheapest where the answer is structural, which is the
argument for keeping the shapes simple enough to read.

**Not yet held — the one thing in this document that is not yet true.** Everything else here describes what the code
does; this section describes what it must do, and the pipeline does not satisfy it today. The comparisons the second
rule asks for are not all written. What the first rule asks for is met: no step does two things, and every step must
change the grammar — one that does not is a fault named where it stands, since a step goes idle when what it looks for
has stopped reaching it, which is a regression in the step before it. The fourth is met too, and nothing is left of the
machinery that served it: nothing names a site, nothing tracks a point of interest, and no declaration table stands,
each phase re-deriving what it needs. So of the six, five are met and the second is not: the comparisons it asks for are
not all written, and a factoring that cannot see two ways share a prefix is a canonical form nobody has written down
rather than an inherent conflict.

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
the result: `s-indent-le` is `(***) s-space` followed by a guard on `(len) (match)`, and the delayed detections are
written the same way. The indentation is never split into two consumes, and never consumed twice.

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
  them. Non-UTF-8 inputs are left out of the fixtures.
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
- It flattens the character-class helpers it uses only inside a `DiffSet`, so a helper run _alone_ emits `unparsed`
  where YamlReference emits its tokens — invisible in a real document, since the helper only ever appears in a
  subtraction.
- It follows the spec's factoring of the plain-scalar `:`/`#` exclusion, and YamlReference does not. The spec keeps
  `ns-plain-safe-out`/`-in` (rules 128/129) as `ns-char` (and `ns-char - c-flow-indicator`) and excludes `:`/`#` in
  `ns-plain-char` (rule 130), with its two exceptions; YamlReference instead subtracts `:`/`#` up in 128/129 and makes
  130 just `ns-plain-safe`. So run alone, `ns-plain-safe-out(':')` matches for libyeast and errors for YamlReference —
  but a full plain scalar accepts the same characters either way (verified against YamlReference's own fixtures).

## How fidelity is earned

Passing tests is a floor, not a proof: every place backtracking is replaced by a committed decision is a place the
automaton can diverge from the productions' meaning. What stands against that is two differential oracles, each
authoritative for a different half of the pipeline. Against **YamlReference** the comparison is token-for-token on yeast
— the codes are identical, so it judges the syntactic layer, production structure and character classes, at the finest
grain there is. Against **YAMLStar** the comparison is value-for-value on the folded load output, judging composition
and schema resolution — the semantic layer. Agreement with both spans the whole pipeline, and a mismatch with exactly
one half localizes the fault. The YAML Test Suite, folded to events, is the empirical floor beneath both.

## Memory safety

The Debug build is AddressSanitizer-instrumented on all three OSes (plus UndefinedBehaviorSanitizer on Linux/macOS
Clang; MSVC has no UBSan), so use-after-free and buffer overflows fail the tests everywhere. Leaks are caught per
platform: on Linux the Debug build's LeakSanitizer flags them at each test's exit; on macOS — where Apple clang has no
LeakSanitizer — the Release test run is passed through the `leaks` tool; MSVC has no leak sanitizer. The portable,
deterministic net is `ys_counting_allocator`: route a parser's allocations through it and assert
`ys_counting_allocator_live_buffers()` is 0 after the parser is freed (the facade tests do exactly this).

The roadmap — what is left to build — lives in `PLAN.md`.
