# Contributing

## Prerequisites

- **To build and install the C library:** a C99 compiler (GCC, Clang, or MSVC) and CMake `>= 3.20`. The `Makefile` needs
  GNU make, and the package test needs pkg-config. The generated files sit in the tree, and the build calls no Python.
  `make install-deps` detects the OS and runs the script for that OS. The target covers Debian and Ubuntu, macOS and
  Windows.

- **To run the gate or work on the generator:** Python 3 with PyYAML, the formatters and linters, and the coverage and
  docs tools. `make install-deps-pc` installs the whole set. `make install-deps-<sub-gate>` (`verify`, `vet`,
  `gh-pages`) narrows the set to a sub-gate's tools. Both targets wrap the per-OS scripts in `scripts/`.

- To check what is present without installing anything, run `make check-build-deps` for the build tools. Run
  `make check-dev-deps` for the gate tools. Both call `scripts/check-deps.sh`.

On macOS, `clang-tidy` ships in the keg-only Homebrew `llvm`. The Makefile finds it automatically.

## The gate

An incremental pre-commit target verifies the tree. CI does not run that target. A lighter workflow runs there instead.

```sh
make pc
```

The gate builds the Debug config under ASan and UBSan, and the hardened Release config, then runs the tests. It checks
formatting and lints with clang-tidy and cppcheck. It enforces the `// UNTESTED` coverage contract. It checks
documentation completeness. It verifies the installed package is consumable. It re-runs only what changed.

**A non-WIP commit must pass `make pc`.** A commit that fails `make pc` on purpose is an in-progress checkpoint. The
message of such a commit says `(WIP)`.

**A bug we find comes with a test case that demonstrates it.** That test must trigger before the fix goes in.

## Style

- `clang-format` formats the C code. The format builds on the LLVM style. `make reformat-c` reformats the code.
  `make pc` fails on C code that `clang-format` would change.
- A public API symbol has `YS_API` and a Doxygen comment. An exposed function needs `@return` and `@param`. The docs
  gate refuses a function missing either.
- Any line the tests do not cover must have a `// UNTESTED` comment. A stale marker on a covered line fails too.
- Use conventional commit messages, such as `feat:` and `fix:` and `build:` and `chore:`.

## Design docs

[`DESIGN.md`](DESIGN.md) maps the architecture. Read it before a large change.

`make pc` enforces a rule over the documents. **A document may state a number only where a list in `check_documents`
allows that number.** The documents share that list.

- **A document states no count of the tree.** `check_documents` refuses any number in it that
  `_NOT_A_NUMBER_OF_THE_TREE` does not excuse. `check_documents._NAMES_A_DOCUMENT_MAY_STATE` names the quantities the
  gate measures. The gate prints those counts and compares none against a document.

  `check_documents` refuses a count written any other way.

A count written in words is the same fault as a count written in digits. `six rules` reads exactly as `1454 productions`
reads. A heading counts the tree as much as a sentence does.

`_NOT_A_NUMBER_OF_THE_TREE` lists the numbers a document or a comment may write. A pattern passes a spec version and a
codepoint. A pattern passes an RFC and a milestone. A pattern passes a bound the specification sets. A pattern passes an
effort estimate. The pattern also passes a width in bits. `no_tree_numbers` refuses a numeral the list leaves out.

`check_documents` refuses a number outside the list a document may write. A number in front of a name the code measures
fails too. The gate prints the measured figures, and the writer drops the number from the sentence.

`check_documents` also reads the prose beside the code. A comment or a docstring may state no count of the tree.
`check_documents` matches a narrower number pattern in a comment than in a document. The narrower pattern covers the
generator and the C alike. A number in front of a noun this project owns is a measurement. Any other number is prose.

A comment or a document may cite a name in backticks. Such a name must name something the code writes. A hyphenated name
names a step, an invariant or a production of some stage. `make pc` fails a comment citing a helper that a rename
retired. `make pc` also fails a comment stating a count that nobody re-derived.

`check_documents` declares a citation of a name the tree lacks. The declaration names the file that writes the citation.
Such a citation may be a placeholder that a worked example invents. It may also be a name that the prose beside a check
cites *as* gone.
