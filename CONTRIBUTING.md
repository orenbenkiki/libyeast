# Contributing

## Prerequisites

- A C99 compiler (GCC, Clang, or MSVC), CMake ≥ 3.20, GNU make, and Python 3 with PyYAML (the parser generator).
- The formatters, linters, coverage, and docs tools the gate uses. Install them with the provided scripts rather than
  chasing a hand-maintained list:
  - `scripts/install-debian-dev-deps.sh` / `scripts/install-macos-dev-deps.sh` install everything the gate needs. Each
    takes an optional sub-gate argument (`vet`, `test-c`, `gh-pages`) to install only that sub-gate's tools; omitted or
    `pc` installs everything.
  - `scripts/install-*-build-deps.sh` install just the compiler, CMake, and pkg-config needed to build and install the
    library.
- To check what is present without installing anything, run `make check-build-deps` (build tools) or
  `make check-dev-deps` (all gate tools); both call `scripts/check-deps.sh`.

On macOS, `clang-tidy` ships in the keg-only Homebrew `llvm`; the Makefile finds it automatically.

## The gate

Everything is verified by a single incremental pre-commit target (CI never runs it — each light runs in its own
workflow):

```sh
make pc
```

It builds the Debug (ASan/UBSan) and Release (hardened) configs, runs the tests, checks formatting, lints (clang-tidy +
cppcheck), enforces the `// UNTESTED` coverage contract, checks documentation completeness, and verifies the installed
package is consumable. It re-runs only what changed.

**Every non-WIP commit must pass `make pc`.** A commit that intentionally does not — an in-progress checkpoint — must be
marked `(WIP)` in its message.

## Style

- C code is formatted by `clang-format` (LLVM base, 120 columns, 4-space indent). `make reformat-c` reformats; `make pc`
  fails on drift.
- Public API symbols are marked `YS_API` and documented with Doxygen comments; every exposed function needs
  `@return`/`@param` (the docs gate enforces it).
- Any line the tests do not cover must carry a `// UNTESTED` comment; a stale one on a now-covered line fails too.
- Use conventional commit messages (`feat:`, `fix:`, `build:`, `chore:`, …).

## Design docs

The architecture map is [`DESIGN.md`](DESIGN.md) and the roadmap is [`PLAN.md`](PLAN.md). Read them before large
changes.
