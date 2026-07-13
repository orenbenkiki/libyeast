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
- **Parser generator** — `generator/`: `ir.py` (the typed grammar IR), `spec2grammar.py` (translate the vendored
  `third_party/yaml-grammar/yaml-spec-1.2.yaml` into the IR), `grammar2spec.py` (the inverse), and
  `check_spec_roundtrip.py` (the gate check that the two are lossless inverses). This is where the grammar-derived
  parser will be generated (see `PLAN.md`); it runs on Python 3 + PyYAML.
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
