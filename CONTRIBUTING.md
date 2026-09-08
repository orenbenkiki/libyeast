# Contributing

## Prerequisites

- **To build and install the C library:** a C99 compiler (GCC, Clang, or MSVC). CMake `>= 3.20` and GNU make.
  pkg-config. That is the list. The generated files sit in the tree, and the build calls no Python. `make install-deps`
  installs these, auto-detecting your OS. Debian and Ubuntu, macOS, and Windows.
- **To run the gate or work on the generator:** Python 3 with PyYAML. Also the formatters and linters, and the coverage
  and docs tools. `make install-deps-pc` installs the whole set. `make install-deps-<sub-gate>` (`verify`, `vet`,
  `gh-pages`) narrows it to a sub-gate's tools. These wrap the per-OS scripts in `scripts/`, and nobody chases a
  hand-maintained list.
- To check what is present without installing anything, run `make check-build-deps` for the build tools. Run
  `make check-dev-deps` for the gate tools. Both call `scripts/check-deps.sh`.

On macOS, `clang-tidy` ships in the keg-only Homebrew `llvm`. The Makefile finds it automatically.

## The gate

An incremental pre-commit target verifies the tree. CI does not run that target. A lighter workflow runs on its own.

```sh
make pc
```

The gate builds the Debug (ASan/UBSan) and Release (hardened) configs, then runs the tests. It checks formatting and
lints with clang-tidy and cppcheck. It enforces the `// UNTESTED` coverage contract. It checks documentation
completeness. It verifies the installed package is consumable. It re-runs only what changed.

**A non-WIP commit must pass `make pc`.** A commit that intentionally does not is an in-progress checkpoint, and its
message says `(WIP)`.

## Style

- `clang-format` formats the C code, on an LLVM base at 120 columns with a 4-space indent. `make reformat-c` reformats;
  `make pc` fails on drift.
- A public API symbol has `YS_API` and a Doxygen comment. An exposed function needs `@return`/`@param` (the docs gate
  enforces it).
- Any line the tests do not cover must have a `// UNTESTED` comment. A stale marker on a covered line fails too.
- Use conventional commit messages, such as `feat:` and `fix:` and `build:` and `chore:`.

## Design docs

[`DESIGN.md`](DESIGN.md) maps the architecture, and [`PLAN.md`](PLAN.md) holds the roadmap. Read them before large
changes.

A rule covers the documents and `make pc` enforces it. **A number a document states is a fault unless something answers
for it.** They differ in the list of what a document may write, and `check_documents` holds that list.

- **`DESIGN.md` may state a count of the tree, and such a count must be checkable.** Put the number immediately in front
  of the words that name the quantity. Name that quantity in `check_documents.NAMES_A_DOCUMENT_MAY_STATE` first. The
  gate then compares what the document says against what the code measures.

  A count written any other way falls to the gate. That makes "must be checkable" a rule rather than an intention.
  Somebody wrote the stale ones found by hand some other way. They reached nothing.

- **`PLAN.md` states no count of the tree.** It says what the project owes, rather than how much of the work remains.
  The gate prints the live figures per run. A number there duplicates a readout into a file somebody must then maintain.

- **`CHANGELOG.md` states no count of the tree either.** Once the tree has moved, nobody can check a number in it.

A count written in words is the same fault as a count written in digits. `six rules` reads exactly as `1454 productions`
reads. A heading counts the tree as much as a sentence does.

A document's pattern names what a document may write besides a checkable count. A spec version or a codepoint. An RFC or
a milestone. A bound the specification sets, or an effort estimate. For `DESIGN`, a width in bits and a numeral counting
what the sentence has just enumerated.

A number outside those wants either a name the code measures or a rewrite that states no number.

`check_documents` covers the prose beside the code, and a second rule covers it too. A comment or a docstring may state
no count of the tree. The narrower pattern there governs the generator and the C alike. A number in front of a noun this
project owns is a measurement. Any other number is prose.

A name such a text cites in backticks must name something the code writes. A hyphenated name names a step, an invariant
or a production of some stage. So a comment naming a helper a rename retired fails `make pc`. So does a comment with a
count nobody re-derived.

A citation the tree really cannot answer is declared in `check_documents`, by the file that writes it. That is a
placeholder a worked example invents, or a name an entry cites *as* gone.
