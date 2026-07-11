# libyeast

[![vet](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml)
[![C tests](https://github.com/orenbenkiki/libyeast/actions/workflows/test-c.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/test-c.yml)
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

- A C99 compiler (GCC, Clang, or MSVC)
- CMake ≥ 3.20
- For the full dev gate: the formatters, linters, coverage, and docs tools installed by `scripts/install-*-dev-deps.sh`
  (see [`CONTRIBUTING.md`](CONTRIBUTING.md))

## Build & install

```sh
make                       # build the library (shared + static)
cmake --install build --prefix /usr/local
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

`make pc` is the incremental pre-commit gate: it runs everything below and re-runs only what changed. Its three
second-level goals each map to one CI workflow and one status badge.

- `make pc` — the full pre-commit gate
  - `make vet` — static code quality + packaging
    - `make check-format` — every formatter, check-only
      - `make check-format-c` — clang-format
      - `make check-format-md` — mdformat
      - `make check-format-py` — black
      - `make check-format-cmake` — gersemi
      - `make check-format-sh` — shfmt
    - `make check-comments` — the `/* */`-only-when-inline comment rule
    - `make lint` — clang-tidy + cppcheck
    - the leftover-marker scan — its goal name is the scaffolding marker itself, so it is not spelled here (this scan is
      exactly why); `vet` runs it for you
    - `make check-version` — guards the vcpkg port against version drift
    - `make pkg-test` — installs, then builds a consumer against the shared and static libraries via pkg-config
  - `make test-c` — C unit tests (Debug + Release) plus the coverage gate
    - `make test-release` — Release build + tests
    - `make coverage` — coverage build enforcing the `// UNTESTED` contract
  - `make gh-pages` — assembles the GitHub Pages payload (Doxygen docs + coverage report)
    - `make docs` — Doxygen HTML, completeness-gated

Goals outside the gate:

- `make reformat` — apply every formatter in place (or one language: `reformat-c`, `reformat-md`, `reformat-py`,
  `reformat-cmake`, `reformat-sh`)
- `make package` (a.k.a. `make all`) — build the shared + static libraries only, no tests
- `make test` — `test-c` plus the `test-haskell` / `test-clojure` differential-oracle stubs (not wired yet)
- `make check-build-deps` / `make check-dev-deps` — verify the required tools are installed
- `make clean` — remove all build directories and stamps

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Documentation

The API reference lives at **<https://orenbenkiki.github.io/libyeast/>**. It is generated with Doxygen (`make docs` →
`build-docs/html`) and republished to GitHub Pages on each push to `main`.

## License

MIT — see [`LICENSE`](LICENSE).
