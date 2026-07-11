# libyeast design

libyeast is a YAML 1.2 parser in C, generated from the formal grammar. This document is a map: it names the pieces and
how they relate, and points at where each piece's design and rationale live — in that piece's own source (its file or
its comments). The parser itself is not yet implemented; what exists is the project framework and a placeholder API.

## Pieces

- **Public API** — `include/yeast.h`: the API surface and its behavioral contract, documented inline as Doxygen comments
  (published to GitHub Pages). Currently the version query (`ys_version`, `ys_major`/`ys_minor`/`ys_patch`).
- **Library** — `src/yeast.c`: the implementation, including the load-time version-sanity constructor.
- **Build** — `CMakeLists.txt` is the source of truth for building, testing, installing, and the version. It defines the
  shared + static libraries (hardened, symbol-visibility controlled), the sanitized Debug and hardened Release configs,
  and the coverage option.
- **Gate** — `Makefile` wraps CMake as the incremental pre-commit gate `make pc`, a pure aggregator of three sub-gates:
  `vet` (formatting, lint, comment rule, marker scan, version-drift, packaging), `test-c` (Debug + Release tests and the
  `// UNTESTED` coverage gate), and `gh-pages` (Doxygen docs + gcovr coverage report). Stamp-file targets keep it
  incremental.
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

The Debug build compiles the library and tests with AddressSanitizer + UndefinedBehaviorSanitizer, so use-after-free,
buffer overflows, and undefined behavior fail the tests on every platform. Leaks are caught per platform: on Linux the
Debug build's LeakSanitizer flags them at each test's exit; on macOS — where Apple clang has no LeakSanitizer — the
Release test run is passed through the `leaks` tool. A deterministic, portable, per-test leak check via a counting
`ys_allocator` is on the roadmap.

The roadmap — what is left to build — lives in `PLAN.md`.
