# libyeast

[![vet](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/vet.yml)
[![test](https://github.com/orenbenkiki/libyeast/actions/workflows/test.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/test.yml)
[![verify](https://github.com/orenbenkiki/libyeast/actions/workflows/verify.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/actions/workflows/verify.yml)
[![docs](https://github.com/orenbenkiki/libyeast/actions/workflows/gh-pages.yml/badge.svg)](https://orenbenkiki.github.io/libyeast/)
[![CodeQL](https://github.com/orenbenkiki/libyeast/actions/workflows/codeql.yml/badge.svg)](https://github.com/orenbenkiki/libyeast/security/code-scanning)
[![coverage](https://img.shields.io/endpoint?url=https://orenbenkiki.github.io/libyeast/coverage.json)](https://orenbenkiki.github.io/libyeast/coverage/)

A fast, single-pass, pull-driven **YAML 1.2 parser in C**. The generator derives libyeast *from the formal grammar*, and
conformance follows from that derivation rather than from hand-testing.

> **Status: pre-alpha.** The parser does not exist yet. This repository holds the project framework and a placeholder
> API (`ys_version`). The framework is the build and the tests. It is also the linting and the coverage. It is the docs
> and the packaging. See [`DESIGN.md`](DESIGN.md) for the architecture and [`PLAN.md`](PLAN.md) for the roadmap.

## Why

Existing YAML parsers sit on a horn of a dilemma. The grammar-faithful parsers, Haskell YamlReference and YAMLStar, are
slow. The fast hand-written ones (libyaml-class) are faithful by luck.

libyeast generates a fast, O(n), committed automaton **from the formal productions**. The state machine gives the speed,
and the derivation gives the fidelity. libyeast targets a C shared library that drops in and keeps its ABI.

## Conformance

libyeast targets **YAML 1.2**, and derives conformance from the grammar rather than hand-testing it. A pair of
boundaries define what that claims.

- **It is a token parser.** libyeast turns the character stream into a _yeast_ token stream and stops there. That stream
  is a lossless representation of the document's structure.

  The work above the token stream belongs to a higher layer and is out of scope. That layer composes tokens into a node
  graph. It resolves anchors, aliases and tags, and constructs native values. It also owns the model decisions that ride
  along. A duplicate mapping key may be an error, and a consumer may keep mapping key order. libyeast emits the keys, in
  order, and leaves those decisions to whatever consumes the tokens.

- **It reads UTF-8 only.** That is its single conformance limitation. YAML 1.2 asks a conformant parser for UTF-16 as
  well. It asks for UTF-32 where a parser accepts JSON. libyeast stops at UTF-8.

  The decoder classifies UTF-8 bytes straight into the grammar without assembling codepoints. The other encodings would
  fight that design, and are forgone.

  A UTF-8 stream may still open with a byte-order mark. libyeast reads that mark and emits it as a mark (`U+FEFF`).
  libyeast does not switch encoding on a BOM, and reads a single encoding. See [`DESIGN.md`](DESIGN.md) for the details.

## Requirements

Building the C library needs a C99 compiler, CMake `>= 3.20`, and pkg-config on top of those. GCC serves as the
compiler, and so do Clang and MSVC. That is the list. The generated files sit in the tree, and the build calls no
Python. `make install-deps` installs those for you and detects your OS. That covers Debian and Ubuntu. It covers macOS
and Windows.

Working on the generator or running the gate needs more. That is Python 3 with PyYAML, the formatters and linters, and
the coverage and docs tools. `make install-deps-pc` installs the whole set, and a per-sub-gate
`make install-deps-<goal>` narrows the list. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Build & install

```sh
make install-deps                 # compiler, CMake, pkg-config - auto-detects your OS
make                              # build the shared + static libraries
make install PREFIX=/usr/local    # (optional) install them
```

Consume it via pkg-config or CMake.

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

Goal names reflect the tree. `make <parent>` runs a group, and `make <parent>-<part>` runs a part of that group.

You can build and test the C library, in pure C with no Python. You can verify the generator pipeline. You can
regenerate its outputs. `make pc` is the developer gate over the whole tree.

`make pc` is the incremental pre-commit gate. It runs these in order, and re-runs only what changed.

- `make all`
- `make test`
- `make verify`
- `make vet`
- `make gh-pages`

A CI workflow and a status badge sit behind `test` and `verify`, and behind `vet` and `gh-pages`. A target here runs on
a pull request as well as on `main`, and the gate that refuses a change here refuses it there. Publishing the docs is
what `main` keeps to itself.

- **`make all`:** the default. It builds the shared and static libraries, in pure C with no Python.
- **`make test`:** build and run the C parser tests. Pure C.
  - **`make test-debug`:** Debug build (sanitized) + tests
  - **`make test-release`:** Release build + tests
- **`make verify`:** the generator pipeline is correct and its outputs current.
  - **`make verify-roundtrip`:** the grammar round-trips through the IR losslessly
  - **`make verify-references`:** a reference resolves to a production of matching arity, and a production is reachable
  - **`make verify-spec`:** the vendored official grammar comes back once a pass erases libyeast's additions
  - **`make verify-markers`:** a `begin-` marker is closed by its own `end-` on any path
  - **`make verify-emits`:** a rule documents the tokens it emits, and the grammar bears that out
  - **`make verify-decoder`:** `src/decoder_tables.h` is exactly what the grammar produces (not stale)
  - **`make verify-wire`:** `wire.py`'s code map matches `src/wire.c`'s
  - **`make verify-emitter`:** the interpreter can undo a state. Backtracking rests on that
  - **`make verify-messages`:** a `(cut)` and an `(error)` name a message, and a message has a name
  - **`make verify-fixtures`:** the conformance fixtures in `tests/spec/` are intact
  - **`make verify-grammar`:** a grammar reproduces `tests/spec/` and is exercised by it from the bottom up.
    - **`make verify-grammar-base`:** the base grammar reproduces its fixtures through the interpreter
    - **`make verify-grammar-base-coverage`:** the fixtures exercise the productions of the base grammar
  - **`make verify-normalize`:** a step of the pipeline preserves the tokens and keeps the pipeline's law
  - **`make verify-spaces`:** the subspace algebra answers what set arithmetic over the enumerated states does
  - **`make verify-star`:** the YAML Test Suite, and the events it folds down to
  - **`make verify-documents`:** the documents and the prose beside the code describe the tree
  - **`make verify-dead-code`:** the code in `generator/` and `scripts/` is reachable
  - **`make verify-failures`:** the shell and the `Makefile` and the workflows and the Python let no failure pass
  - **`make verify-conventions`:** the whole tree obeys the conventions a gate can decide
  - **`make verify-proposals`:** a convention a review proposed has a ruling
  - **`make verify-hooks`:** a registered hook answers a faulty edit and passes a clean edit
  - **`make verify-agent-tools`:** an agent asked for nothing a hook withheld, and left no fragment unfixed
  - **`make verify-fragments`:** the project breaks into fragments, and a fragment holds prose of its own
  - **`make verify-prose`:** the prose in the tree says nothing a write-time hook would refuse
  - **`make verify-ascii`:** a tracked file holds ASCII, and `check_ascii` declares what holds a wider character
- **`make vet`:** static code quality.
  - **`make vet-format`:** the formatters run in check-only mode.
    - **`make vet-format-c`:** clang-format
    - **`make vet-format-md`:** mdformat
    - **`make vet-format-py`:** black + format-docstring + wrap_long_comments + ruff
    - **`make vet-format-cmake`:** gersemi
    - **`make vet-format-sh`:** shfmt
    - **`make vet-format-make`:** the column limit, over what no formatter reflows
  - **`make vet-comments`:** the `/* */`-only-when-inline comment rule
  - **`make vet-lint`:** clang-tidy + cppcheck
  - **`make vet-pylint`:** pylint over `generator/` and `scripts/`, and `.pylintrc` configures it
  - **`make vet-mypy`:** mypy over the same files, and `mypy.ini` configures it. It checks what has an annotation
  - **`make vet-version`:** guards the vcpkg port against version drift
  - **`make vet-packaging`:** installs, then builds a consumer against the shared and static libraries via pkg-config
  - the leftover-marker scan. Its goal is `vet-` followed by the scaffolding marker itself. This file leaves that goal
    unspelled, and the scan is exactly why. `vet` runs it for you.
- **`make gh-pages`:** the GitHub Pages payload.
  - **`make gh-pages-docs`:** Doxygen HTML, and completeness gates it
  - **`make gh-pages-coverage`:** the `// UNTESTED` coverage gate + HTML report

Goals outside the gate.

- **`make install-deps`:** install the C build deps. It detects the OS, and covers Debian and Ubuntu and macOS and
  Windows.
  - **`make install-deps-pc`:** the deps the gate needs. `make install-deps-<sub-gate>` narrows the list, as
    `install-deps-verify` and `install-deps-vet` and `install-deps-gh-pages` do.
- **`make install`:** install the built libraries. `PREFIX=...` sets the location, and `/usr/local` is the default.
- **`make regen`:** regenerate the generated files that sit in the tree. That is `src/decoder_tables.h`.
- **`make reformat`:** apply the formatters in place. A single language goes through `reformat-c` or `reformat-md`. A
  Python file goes through `reformat-py`, a CMake file through `reformat-cmake` and a shell file through `reformat-sh`.
- **`make check-build-deps` and `make check-dev-deps`:** report whether the required tools are there.
- **`make clean`:** remove the build directories and stamps.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Documentation

The API reference is online at **<https://orenbenkiki.github.io/libyeast/>**.

The pages come out of Doxygen (`make gh-pages-docs` -> `build-docs/html`), and a push to `main` republishes them to
GitHub Pages.

## License

MIT - see [`LICENSE`](LICENSE).
