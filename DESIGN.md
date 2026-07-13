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
- **Library** — `src/yeast.c`: the implementation of everything the header declares, including the load-time
  version-sanity constructor. The parser facade returns "not implemented" until the grammar-derived core lands.
- **Decoder** — `src/decoder.h`, `src/decoder.c` and the generated `src/decoder_tables.h`: the bottom layer, which turns
  input bytes into characters the parser can branch on. A character becomes a 32-bit key holding the id of the character
  if the grammar names it, one bit per character set the grammar tests, and the bytes it consumed — so a test is one
  comparison or one AND, with the grammar's unions and subtractions already evaluated into the bits. No Unicode
  codepoint is ever assembled: tokens are spans of input bytes, so nothing compares a character's numeric value, and the
  decoder validates and classifies instead of decoding. That is also why no existing UTF-8 library serves — they all
  exist to produce the codepoint we do not want, or to validate in bulk without classifying at all. `decoder.h`
  documents the key; `decoder.c` holds the UTF-8 mechanics, which are RFC 3629's and not the grammar's.
- **Grammar** — `grammar/yeast-spec-1.2.yaml`: libyeast's grammar, and the source everything else is generated from. It
  is the YAML 1.2 productions with two additions: each indicator character is reached through the production that names
  it, and 97 of the 211 productions carry the yeast token codes — which productions bracket their match in `Begin`/`End`
  markers, and what code each consumed character is given. The vendored `third_party/yaml-grammar/yaml-spec-1.2.yaml`
  cannot serve as the source: it inlines the indicator characters, so it cannot say that a quotation mark opens a scalar
  as an indicator but is meta inside an escape, and it names no token at all. `make check-grammar` erases the
  annotations and the indicator productions and checks that what remains is the vendored grammar, production for
  production — so what libyeast adds cannot quietly become what libyeast changes, and a departure must be declared, with
  its reason, in `check_vendor_spec.py`.
- **Parser generator** — `generator/`: `ir.py` (the typed grammar IR), `annotated2ir.py` (read
  `grammar/yeast-spec-1.2.yaml` into the IR), `ir2annotated.py` (the inverse), `ir2spec.py` (erase libyeast's additions
  and recover the official grammar), `chars.py` (the character model the decoder is built from), `grammar2decoder.py`
  (emit `src/decoder_tables.h`), and the gate checks `check_annotated_roundtrip.py`, `check_vendor_spec.py`,
  `validate_grammar.py` and `check_decoder.py`. This is where the grammar-derived parser will be generated (see
  `PLAN.md`); it runs on Python 3 + PyYAML.
- **Reference** — `third_party/yamlreference/`: the Haskell YAML reference parser, vendored to be read. Its grammar
  carries the token annotations `grammar/yeast-spec-1.2.yaml` replicates, and its `Code` type is where `ys_code` comes
  from. It is LGPL, while libyeast is MIT: nothing is copied from it, nothing links against it, and nothing of it is
  built. Later it becomes the differential oracle.
- **Build** — `CMakeLists.txt` is the source of truth for building, testing, installing, and the version. It defines the
  shared + static libraries (hardened, symbol-visibility controlled), the sanitized Debug and hardened Release configs,
  and the coverage option.
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
  `install-*-deps.sh` dependency installers.
- **Packaging** — `cmake/*.in` (relocatable pkg-config + CMake package config), `conanfile.py` (Conan), and
  `ports/yeast/` (vcpkg). The version flows from the single CMake source into all of them; `make check-version` guards
  against vcpkg drift.
- **Docs** — `Doxyfile` drives the API docs from the header comments, completeness-gated: an undocumented public symbol
  or a missing `@param`/`@return` fails the build.

## Memory safety

The Debug build is AddressSanitizer-instrumented on all three OSes (plus UndefinedBehaviorSanitizer on Linux/macOS
Clang; MSVC has no UBSan), so use-after-free and buffer overflows fail the tests everywhere. Leaks are caught per
platform: on Linux the Debug build's LeakSanitizer flags them at each test's exit; on macOS — where Apple clang has no
LeakSanitizer — the Release test run is passed through the `leaks` tool; MSVC has no leak sanitizer. The portable,
deterministic net is `ys_counting_allocator`: route a parser's allocations through it and assert
`ys_counting_allocator_live_buffers()` is 0 after the parser is freed (the facade tests do exactly this).

The roadmap — what is left to build — lives in `PLAN.md`.
