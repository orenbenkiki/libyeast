# libyeast

[![vet](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml)
[![test](https://github.com/orenbenkiki/libyeast/actions/workflows/test.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/test.yml)
[![verify](https://github.com/orenbenkiki/libyeast/actions/workflows/verify.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/verify.yml)
[![docs](https://github.com/orenbenkiki/libyeast/actions/workflows/gh-pages.yml/badge.svg)](https://orenbenkiki.github.io/libyeast/)
[![CodeQL](https://github.com/orenbenkiki/libyeast/actions/workflows/codeql.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/security/code-scanning)
[![coverage](https://img.shields.io/endpoint?url=https://orenbenkiki.github.io/libyeast/coverage.json)](https://orenbenkiki.github.io/libyeast/coverage/)

A fast, single-pass, pull-driven **YAML 1.2 parser in C** — *generated from the formal grammar*, so its conformance is
derived, not hand-tested.

> **Status: pre-alpha.** The parser is not implemented yet. This repository currently holds the project framework
> (build, test, lint, coverage, docs, packaging) and a placeholder API (`ys_version`). See [`DESIGN.md`](DESIGN.md) for
> the architecture and [`PLAN.md`](PLAN.md) for the roadmap.

## Why

Existing YAML parsers sit on one horn of a dilemma: the grammar-faithful reference parsers are slow, and the fast
hand-written ones (libyaml-class) are "faithful by luck." libyeast generates a fast, O(n), committed automaton **from
the ~211 formal productions**, so speed comes from the state machine and fidelity from the derivation. It targets a
drop-in, ABI-compatible C shared library.

## Requirements

Building the C library needs only a C99 compiler (GCC, Clang, or MSVC), CMake ≥ 3.20, and pkg-config — the generated
files are committed, so the build calls no Python. `make install-deps` installs those for you (Debian/Ubuntu, macOS, or
Windows, auto-detected). Working on the generator or running the gate needs more — Python 3 with PyYAML, the formatters,
linters, and the coverage and docs tools — which `make install-deps-pc` (or a per-sub-gate `make install-deps-<goal>`)
installs; see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Build & install

```sh
make install-deps                 # compiler, CMake, pkg-config — auto-detects your OS
make                              # build the shared + static libraries
make install PREFIX=/usr/local    # (optional) install them
```

Consume it via pkg-config or CMake:

```sh
cc app.c $(pkg-config --cflags --libs yeast)
```

```cmake
find_package(yeast CONFIG REQUIRED)
target_link_libraries(app PRIVATE yeast::yeast)
```

## Usage

```c
#include <yeast.h>
#include <stdio.h>

int main(void) {
    printf("libyeast %s (%d.%d.%d)\n", ys_version(), ys_major(), ys_minor(), ys_patch());
    return 0;
}
```

## Development

Goal names reflect the tree: `make <parent>` runs a group, `make <parent>-<part>` runs one part. There are three ways to
work — build and test the C library (pure C, no Python), verify the generator pipeline, or regenerate its outputs — and
`make pc` is the developer gate over all of it.

`make pc` is the incremental pre-commit gate: it runs `make all`, `make test`, `make verify`, `make vet`, and
`make gh-pages` in that order, re-running only what changed. Four of those map to one CI workflow and one status badge
each: `test`, `verify`, `vet`, `gh-pages`.

- **`make all`** (the default) — build the shared + static libraries. Pure C, no Python.
- **`make test`** — build and run the C parser tests. Pure C.
  - `make test-debug` — Debug build (sanitized) + tests
  - `make test-release` — Release build + tests
- **`make verify`** — the generator pipeline is correct and its outputs current:
  - `make verify-roundtrip` — the grammar round-trips through the IR losslessly
  - `make verify-references` — every reference resolves to a production of matching arity, every production reachable
  - `make verify-spec` — erasing libyeast's additions recovers the vendored official grammar
  - `make verify-markers` — every `begin-` marker is closed by its own `end-`, on every path
  - `make verify-emits` — every rule documents the tokens it emits, checked against the grammar
  - `make verify-decoder` — `src/decoder_tables.h` is exactly what the grammar produces (not stale)
  - `make verify-wire` — `wire.py`'s code map matches `src/wire.c`'s
  - `make verify-fixtures` — the conformance fixtures in `tests/spec/` are intact
  - `make verify-grammar` — every grammar reproduces `tests/spec/` and is wholly exercised by it, bottom-up:
    - `make verify-grammar-base` — the base grammar reproduces its fixtures, via the interpreter
    - `make verify-grammar-base-coverage` — the fixtures exercise every production of the base grammar
- **`make vet`** — static code quality:
  - `make vet-format` — every formatter, check-only:
    - `make vet-format-c` / `-md` / `-py` / `-cmake` / `-sh` — clang-format / mdformat / black / gersemi / shfmt
  - `make vet-comments` — the `/* */`-only-when-inline comment rule
  - `make vet-lint` — clang-tidy + cppcheck
  - `make vet-version` — guards the vcpkg port against version drift
  - `make vet-packaging` — installs, then builds a consumer against the shared and static libraries via pkg-config
  - the leftover-marker scan — its goal is `vet-` followed by the scaffolding marker itself, so it is not spelled here
    (this scan is exactly why); `vet` runs it for you
- **`make gh-pages`** — the GitHub Pages payload:
  - `make gh-pages-docs` — Doxygen HTML, completeness-gated
  - `make gh-pages-coverage` — the `// UNTESTED` coverage gate + HTML report

Goals outside the gate:

- `make install-deps` — install the C build deps, OS auto-detected (Debian/Ubuntu, macOS, Windows)
  - `make install-deps-pc` — everything the gate needs; `make install-deps-<sub-gate>` narrows it:
    `install-deps-verify`, `install-deps-vet`, `install-deps-gh-pages`
- `make install` — install the built libraries (`PREFIX=…`, default `/usr/local`)
- `make regen` — regenerate the committed generated files (today `src/decoder_tables.h`)
- `make reformat` — apply every formatter in place (or one language: `reformat-c`, `reformat-md`, `reformat-py`,
  `reformat-cmake`, `reformat-sh`)
- `make check-build-deps` / `make check-dev-deps` — verify the required tools are installed
- `make clean` — remove all build directories and stamps

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Documentation

The API reference lives at **<https://orenbenkiki.github.io/libyeast/>**. It is generated with Doxygen
(`make gh-pages-docs` → `build-docs/html`) and republished to GitHub Pages on each push to `main`.

## License

MIT — see [`LICENSE`](LICENSE).
