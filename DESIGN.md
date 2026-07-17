# libyeast design

libyeast is a YAML 1.2 parser in C, generated from the formal grammar. This document is a map: it names the pieces and
how they relate, and points at where each piece's design and rationale live — in that piece's own source (its file or
its comments). The full public API surface is declared, but the parser core is not yet implemented — parsing returns a
"not implemented" error — so what exists is the project framework and this facade.

## Pieces

- **Public API** — `include/yeast.h`: the API surface and its behavioral contract, documented inline as Doxygen comments
  (published to GitHub Pages). The version query; the pull-parser surface (`ys_new_string_parser` /
  `ys_new_stream_parser` / `ys_next_token` / `ys_free_parser`) with `ys_fd_reader`/`ys_fp_reader` adapters; a pluggable
  allocator; and the `ys_counting_allocator` leak counter.
- **Wire format** — `src/wire.c`: the yeast wire format, one character and its escaped text per token, which
  `ys_write_token` writes and `ys_read_token` reads back — together with the table that says which character each code
  is. That format is what a token stream is compared against the reference parser in, and what lets one be piped between
  tools; it is complete and works. The reader treats the wire as untrusted: a wire it cannot read ends on a
  `YS_CODE_WIRE_ERROR` token — a code no valid wire carries, so it is never mistaken for the error tokens a wire
  legitimately replays — with a located, specific message.
- **Memory** — `src/memory.h` and `src/memory.c`: allocation through a `ys_allocator`, and `ys_memory` — what an object
  may allocate and what it has. Everything that grows goes through it, so `ys_options::max_bytes` has exactly one door,
  and the parser and the wire-format reader are held to their cap by the same code rather than by two copies of it.
- **Source** — `src/source.h` and `src/source.c`: bytes read from a `ys_reader`, and the buffer they land in. Compact to
  what is still needed, grow only if that left no room, read into the tail. The parser reads its input through one of
  these and so does the wire-format reader; they had one each, and the two drifted — one grew a buffer the size of the
  whole stream and the other did not, one told a reader's failure from the end of the input and the other did not. That
  is the argument for their having one.
- **Version** — `src/version.c`: the version query, and the load-time constructor that refuses a library built without a
  version at all. **Counting allocator** — `src/counting_allocator.c`: the leak counter. **Stream adapters** —
  `src/streams.c`: the file-descriptor and `FILE *` adapters for `ys_reader` and `ys_writer`, which share a file because
  they share the act of closing.
- **Parser** — `src/parser.h` and `src/parser.c`: the parser's whole execution state, and the runtime that keeps it. A
  window over the input, a stack of the productions the parser is inside, a queue of the tokens it has built but not yet
  handed back, and the state it is in — none of it in the C call stack, which is what lets `ys_next_token` hand back a
  token from the middle of a production and resume there on the next call. `parser.h` says why each piece is shaped as
  it is: why the queue's undecided tokens are a suffix and the marker injected ahead of them needs no room made for it,
  why a frame carries `n` and nothing carries `c`, and why an error can always be reported. There is one automaton, not
  a scanner and a parser: in yeast the automaton's output already *is* the token stream, so a second layer would need a
  vocabulary that does not exist — and would be the one thing on the hot path the grammar did not derive. The automaton
  itself is not generated yet (see `PLAN.md`); what is here is everything it will run on.
- **Messages** — `src/messages.h` and `src/messages.c`: what libyeast says to its caller, as one table of static strings
  indexed by name, so that all of it can be read in one place and swapped for another language. The messages that depend
  on the grammar live in `grammar/messages.yaml`, keyed by the code a `(cut)` or an `(error)` names — the one source the
  interpreter reads and the C table will be generated from, gated so the two cannot drift. Each says what was expected
  and never what was found, the byte that failed being the first unparsed token behind the error. That table is not
  generated into `src/parser_tables.h` yet.
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
- **Parser generator** — `generator/`: `ir.py` (the typed grammar IR), `annotated2ir.py` (read
  `grammar/yeast-spec-1.2.yaml` into the IR), `ir2annotated.py` (the inverse), `ir2spec.py` (erase libyeast's additions
  and recover the official grammar), `chars.py` (the character model the decoder is built from), `grammar2decoder.py`
  (emit `src/decoder_tables.h`), `wire.py` (the yeast wire format in Python), `spec_tests.py` (the conformance
  fixtures), `interpreter.py` (a backtracking interpreter of the grammar, run against those fixtures), and the gate
  checks `check_annotated_roundtrip.py`, `check_vendor_spec.py`, `validate_grammar.py`, `check_markers.py`,
  `check_grammar_docs.py`, `check_messages.py`, `check_decoder.py`, `check_spec_tests.py`, `check_wire.py`,
  `check_interpreter.py` and `check_grammar_coverage.py`, which report through `gate.py`. This is where the
  grammar-derived parser will be generated (see `PLAN.md`); it runs on Python 3 + PyYAML.
- **Reference** — `third_party/yamlreference/`: the Haskell YAML reference parser, vendored to be read. Its grammar
  carries the token annotations `grammar/yeast-spec-1.2.yaml` replicates, and its `Code` type is where `ys_code` comes
  from. It is LGPL, while libyeast is MIT: no source is copied from it, nothing links against it, and nothing of it is
  built. Its `tests/` fixtures were the source libyeast's own conformance suite (`tests/spec/`) was built from once, and
  are kept only to be read against — see the differences from the reference below, and the reference-interpreter phase
  in `PLAN.md`.
- **Build** — `CMakeLists.txt` is the source of truth for building, testing, installing, and the version. It defines the
  shared + static libraries (hardened, symbol-visibility controlled), the sanitized Debug and hardened Release configs,
  and the coverage option. No list of files is kept by hand, here or in the `Makefile`: the sources and the tests are
  globbed, with `CONFIGURE_DEPENDS` to reconfigure when the set changes, so a new file cannot be left out of the build
  or slip past the gate — which a hand-kept list is exactly what allows.
- **Gate** — `Makefile` wraps CMake as the incremental pre-commit gate `make pc`, a pure aggregator of three sub-gates:
  `vet` (formatting, lint, comment rule, marker scan, version-drift, grammar round-trip, packaging), `test-c` (Debug +
  Release tests and the `// UNTESTED` coverage gate), and `gh-pages` (Doxygen docs + gcovr coverage report). Stamp-file
  targets keep it incremental.
- **CI** — `.github/workflows/`: one workflow per sub-gate (`vet.yml`, `test-c.yml`, `gh-pages.yml`) plus `codeql.yml`,
  each producing an independent status badge. `gh-pages.yml` publishes the Doxygen docs and the coverage report to
  GitHub Pages; the coverage-percentage badge reads a JSON published there. `dependabot.yml` keeps the pinned GitHub
  Actions current.
- **Quality scripts** — `scripts/`: `check_comments.py` (comment-style rule), `coverage_gate.py` (the `// UNTESTED`
  contract), `coverage_badge.py` (coverage-percentage badge JSON), `check-deps.sh` (tool presence), and the
  `install-*-deps.sh` dependency installers. Every generator gate reports through `generator/gate.py`, so that a failure
  reads the same wherever it came from and no gate can report success by forgetting to exit.
- **Packaging** — `cmake/*.in` (relocatable pkg-config + CMake package config), `conanfile.py` (Conan), and
  `ports/yeast/` (vcpkg). The version flows from the single CMake source into all of them; `make check-version` guards
  against vcpkg drift.
- **Docs** — `Doxyfile` drives the API docs from the header comments, completeness-gated: an undocumented public symbol
  or a missing `@param`/`@return` fails the build.

## Differences from the reference parser

libyeast's goal is a fast, correct YAML 1.2 parser for YAMLStar and its kin — not a byte-for-byte replica of the Haskell
reference. Where the token stream a caller sees differs from the reference's, it is a decision, and every one is here
with its reason. (Deviations from the _official grammar_ are a separate matter, declared with their reasons in
`generator/check_vendor_spec.py`.) The conformance suite is migrated with these differences applied, so the reference's
fixtures go on testing libyeast rather than a parser it is not.

- **UTF-8 only.** libyeast reads UTF-8 and nothing else; the reference detects and reads UTF-16 and UTF-32 too. The
  decoder classifies UTF-8 bytes straight into a key without ever assembling a codepoint, and tokens are spans of those
  bytes — a design the other encodings would fight (a second classifier, codepoint assembly to serialize a token,
  source-byte marks). YAML 1.2 asks a conformant parser for UTF-16, and UTF-32 where it accepts JSON; libyeast forgoes
  them for now. Non-UTF-8 inputs are simply left out of the suite.
- **A byte-order mark is the character, not the encoding.** libyeast's `bom` token holds the mark it matched (`U+FEFF`);
  the reference's holds the name of the encoding it detected (`UTF-8`). Detecting no encoding, libyeast has no name to
  give.
- **No token spans a line.** libyeast cuts every run at a line break: a skipped line comes back as two `unparsed`
  tokens, one for its content and one for its break, where the reference can hand back a single token across the break.
  A token that spanned a line would make a stream parser's output depend on how much of the input its buffer held.
- **After an error, libyeast stops parsing the document; the reference recovers and continues.** On a malformed document
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
  streams agree only up to the first error, and that is where the suite stops comparing. The message differs too:
  libyeast's names the production it was in and what it expected, not the reference's wording, and what carries the
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

- libyeast consumes and emits the indentation the reference peeks at, so it needs no cross-line lookahead.
- It flattens the character-class helpers it uses only inside a `Diff`, so a helper run _alone_ emits `unparsed` where
  the reference emits its tokens — invisible in a real document, since the helper only ever appears in a subtraction.
- It follows the spec's factoring of the plain-scalar `:`/`#` exclusion, and the reference does not. The spec keeps
  `ns-plain-safe-out`/`-in` (rules 128/129) as `ns-char` (and `ns-char - c-flow-indicator`) and excludes `:`/`#` in
  `ns-plain-char` (rule 130), with its two exceptions; the reference instead subtracts `:`/`#` up in 128/129 and makes
  130 just `ns-plain-safe`. So run alone, `ns-plain-safe-out(':')` matches for libyeast and errors for the reference —
  but a full plain scalar accepts the same characters either way (verified against the reference's own fixtures).

## Memory safety

The Debug build is AddressSanitizer-instrumented on all three OSes (plus UndefinedBehaviorSanitizer on Linux/macOS
Clang; MSVC has no UBSan), so use-after-free and buffer overflows fail the tests everywhere. Leaks are caught per
platform: on Linux the Debug build's LeakSanitizer flags them at each test's exit; on macOS — where Apple clang has no
LeakSanitizer — the Release test run is passed through the `leaks` tool; MSVC has no leak sanitizer. The portable,
deterministic net is `ys_counting_allocator`: route a parser's allocations through it and assert
`ys_counting_allocator_live_buffers()` is 0 after the parser is freed (the facade tests do exactly this).

The roadmap — what is left to build — lives in `PLAN.md`.
