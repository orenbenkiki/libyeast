# libyeast build and verify orchestration.
#
# A check is a *stamp file* target. The target depends on the sources and configs. The recipe touches the stamp after
# the check succeeds. A failed command aborts the recipe before the touch. A stamp does not record a false success.
# `make <target>` with nothing changed does nothing at all. CMake does the heavy lifting of compilation, and CMake is
# itself incremental.
#
# A part of a mode sits beside the stamp rule that does the part's work.

# clang-format's LLVM style holds within a major version and shifts between majors. The format gate accepts a build of a
# single major.
#
# This `Makefile` reads `.clang-format-version` for the major the gate accepts. The dev-deps scripts read the same file
# when they install the clang-format wheel.
#
# `CLANG_FORMAT` resolves to that wheel's binary, and the format targets verify the major before they run the binary. A
# developer holding a build of that major on PATH needs no wheel. The gate refuses any other major and prints
# instructions.
CLANG_FORMAT_MAJOR_VERSION := $(shell cat .clang-format-version)
# The C formatter. The installed wheel provides the binary, and PATH provides it otherwise.
CLANG_FORMAT ?= $(shell python3 -c "import clang_format, os; \
                  print(os.path.join(os.path.dirname(clang_format.__file__), 'data', 'bin', 'clang-format'))" \
                  2>/dev/null || echo clang-format)
# The C linter. The Makefile looks on PATH and then in the Homebrew llvm prefix. macOS keeps clang-tidy in that prefix.
CLANG_TIDY ?= $(shell command -v clang-tidy 2>/dev/null \
                || command -v "$$(brew --prefix llvm 2>/dev/null)/bin/clang-tidy" 2>/dev/null \
                || echo clang-tidy)
# The C analyzer that runs beside clang-tidy.
CPPCHECK ?= cppcheck

# The tool that turns the coverage data into a report and a badge.
GCOVR ?= gcovr

# The markdown formatter.
MDFORMAT ?= mdformat

# The Python formatter. It leaves docstrings and comments untouched.
BLACK ?= black

# The tool that reflows a Python docstring.
FORMAT_DOCSTRING ?= format-docstring

# The CMake formatter.
GERSEMI ?= gersemi

# The shell formatter.
SHFMT ?= shfmt

# The Python linter that runs first. It covers line length and unused imports.
RUFF ?= ruff

# The Python linter that reads across a whole module.
PYLINT ?= pylint

# The Python type checker. It decides the annotations.
MYPY ?= mypy

# The coverage and leak tools differ by platform. The gcov reader is llvm-cov for Apple clang and for Homebrew clang.
# The reader for GCC is gcov.
#
# Leak detection differs too. Linux ASan ships LeakSanitizer, and the build enables it explicitly. LeakSanitizer runs at
# a forked test's exit. The run is per test and isolated.
#
# Apple clang has no LeakSanitizer. The Darwin gate instead runs the Release suite through the `leaks` tool.
ifeq ($(shell uname -s),Darwin)
GCOV_EXE      ?= xcrun llvm-cov gcov
# The path the SDK headers are on. clang-tidy has no compiler driver to ask.
TIDY_EXTRA := --extra-arg=-isysroot --extra-arg=$(shell xcrun --show-sdk-path)
# The variable stays empty. The Darwin leak check runs outside the test binary.
ASAN_TEST_ENV :=
LEAK_CHECK    := MallocStackLogging=1 leaks --atExit --
else
GCOV_EXE      ?= gcov
# The system headers are already on the default path.
TIDY_EXTRA :=
ASAN_TEST_ENV := ASAN_OPTIONS=detect_leaks=1
# LeakSanitizer in the Debug ASan build already leak-checks a test. The variable holds a no-op command.
LEAK_CHECK := :
endif

# `DEPS_OS` selects the install-deps script that `make install-deps*` runs. The `Makefile` detects the OS. A Linux
# without apt is neither Debian nor Ubuntu and has no script. `DEPS_OS` then stays empty, and the install-deps recipe
# fails with a message naming the next command.
ifeq ($(OS),Windows_NT)
DEPS_OS := windows
else ifeq ($(shell uname -s),Darwin)
DEPS_OS := macos
else ifneq ($(shell command -v apt-get 2>/dev/null),)
DEPS_OS := debian
else
DEPS_OS :=
endif
# The guard an install-deps target opens with. It refuses an OS no script covers and names what to install by hand.
DEPS_GUARD := test -n "$(DEPS_OS)" || { echo "install-deps: no dependency script for this OS. Debian and Ubuntu \
	install through apt, macOS through Homebrew, and Windows has its own way. Install CMake, a C compiler and \
	pkg-config yourself. The install-deps scripts list them. The other packages are formatters, linters, and \
	Python 3 with PyYAML." >&2; exit 1; }

# Input sets (wildcards catch untracked new files too).
LIB_SRC   := $(wildcard src/*.c)
# The published API. The package installs these.
PUB_HDR := $(wildcard include/*.h)
# The internal headers. The package leaves these behind.
PRIV_HDR := $(wildcard src/*.h)
# The tests over the public surface.
UNIT_TEST := tests/test_c.c
# The consumer that builds against the installed package.
PKG_SRC := tests/pkg_consumer.c
# The vendored test framework.
VENDOR_H := $(wildcard third_party/acutest/*.h)
# The inputs that configure the build.
CMAKE_IN := CMakeLists.txt $(wildcard cmake/*.in)
# The version that a shell command reads out of CMakeLists project(). `.` matches the literal '('. make then balances
# the parens in $(shell ...).
VERSION   := $(shell sed -n 's/^project.yeast VERSION \([0-9][0-9.]*\).*/\1/p' CMakeLists.txt)
# The sources the format, lint and comment checks scan.
ALL_SRC := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c)
# `FIND_PRUNE` holds the find prune that the recipes below reuse. The prune skips `.git` and vendored `third_party`. It
# also skips build dirs and untracked junk-* scratch.
#
# `.claude/agents` holds agent definitions. A markdown formatter would fold such a definition's `---` fences into a
# heading. The prune covers the agent definitions with the rest of the files this project does not own.
FIND_PRUNE := -name .git -prune -o -name third_party -prune -o -path './build*' -prune -o -path './junk*' -prune -o \
              -path './.claude/agents*' -prune -o
# The files the markdown formatter reads.
MD_FILES := $(shell find . $(FIND_PRUNE) -name '*.md' -print)
# The files the Python tools read.
PY_FILES := $(shell find . $(FIND_PRUNE) -name '*.py' -print)
# The files the CMake formatter reads.
CMAKE_FILES := CMakeLists.txt $(shell find . $(FIND_PRUNE) -name '*.cmake' -print)
# The files the shell formatter reads.
SH_FILES := $(shell find . $(FIND_PRUNE) -name '*.sh' -print)
# The scaffolding marker. A subst call builds the marker. This Makefile holds no literal copy.
TODO_X    := $(subst -,,todo-x)
# The leftover-marker scan covers the tracked files outside docs/. docs/ documents the marker.
SCAN_FILES := $(shell git ls-files -- . ':(exclude)docs')
# The tools a build reads.
BUILD_DEPS := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(VENDOR_H) $(CMAKE_IN)
# clang-tidy needs a compile database. It lints what build-debug builds. Those are the library sources and the tests
# less the packaging consumer. The consumer builds against the installed library and stays out of that database.
LINT_FILES := $(LIB_SRC) $(filter-out $(PKG_SRC),$(wildcard tests/*.c))
# The directory the package test installs to. The test throws it away after.
PKG_PREFIX := $(CURDIR)/build-pkgtest/prefix
# The directory `make install` puts the library in.
PREFIX ?= /usr/local
# The vendored official grammar.
GRAMMAR_SPEC := third_party/yaml-grammar/yaml-spec-1.2.yaml
# libyeast's grammar. The generator reads it.
ANNOTATED := grammar/yeast-spec-1.2.yaml
# The table the grammar's cuts and errors report from.
MESSAGES := grammar/messages.yaml
# The generator and its gates.
GEN_SRC := $(wildcard generator/*.py)
# The helpers the build and the gates call.
SCRIPT_SRC := $(wildcard scripts/*.py)
# The agent definitions. A definition that judges prose holds the conventions below a marker.
AGENT_SRC := $(wildcard .claude/agents/*.md)
# The list names the documents `check_documents` reads. That gate checks the counts and the citations a document states.
DOCUMENTS := DESIGN.md PLAN.md CHANGELOG.md
# The conformance corpus. A case holds an input and the tokens it expects.
FIXTURES := $(wildcard tests/spec/*.input tests/spec/*.output)
# The YAML Test Suite as git checks it out. A case holds an input, the events it expects, and an error marker.
STAR_DATA  := $(wildcard third_party/yaml-test-suite/*/in.yaml third_party/yaml-test-suite/*/*/in.yaml \
                         third_party/yaml-test-suite/*/test.event third_party/yaml-test-suite/*/*/test.event \
                         third_party/yaml-test-suite/*/error third_party/yaml-test-suite/*/*/error)

# The tool dependencies that check-build-deps and check-dev-deps verify. Python is not a C build dep. The build uses the
# committed generated files. Python rides with the dev tools. The verify and regen targets run the generator.
BUILD_DEP_TOOLS := cmake $(CC)
# A developer needs the formatters and the linters. A developer needs Python and the doc builder.
DEV_DEP_TOOLS   := python3 python3:yaml python3:conan $(CLANG_FORMAT) $(CLANG_TIDY) $(CPPCHECK) $(GCOVR) \
                   $(MDFORMAT) $(BLACK) $(FORMAT_DOCSTRING) $(RUFF) $(GERSEMI) $(SHFMT) $(MYPY) doxygen \
                   $(firstword $(GCOV_EXE))

.PHONY: all package install test test-debug test-release regen \
        verify verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star \
        verify-normalize verify-documents verify-dead-code verify-proposals verify-failures verify-conventions \
        verify-agent-prompts \
        verify-hooks verify-fragments verify-prose verify-ascii examine-prose \
        verify-wire verify-decoder verify-spaces \
        verify-grammar-base verify-grammar-base-coverage \
        vet vet-format vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-make \
        vet-comments vet-lint vet-pylint vet-mypy vet-version vet-packaging vet-$(TODO_X) \
        gh-pages gh-pages-docs gh-pages-coverage \
        reformat reformat-c reformat-md reformat-py reformat-cmake reformat-sh reformat-make \
        check-build-deps check-dev-deps install-deps pc clean

# The default goal. It packages the C library for a consumer.
all: package

# The directory a stamp lands in. A stamp rule takes the directory as an order-only prerequisite, and its time triggers
# nothing.
.stamps:
	@mkdir -p .stamps

# --- default build. The shippable library as a shared object and as an archive. The tests stay out. ---
build/.cfg: $(CMAKE_IN)
	cmake -S . -B build
	@touch $@
build/.package: build/.cfg $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR)
	cmake --build build --target yeast yeast_static
	@touch $@

# The shared and static libraries. The build reads the committed sources and runs no generator step.
package: build/.package

# --- Debug config. Sanitizers. ---
build-debug/.cfg: $(CMAKE_IN)
	cmake -S . -B build-debug -DCMAKE_BUILD_TYPE=Debug
	@touch $@
build-debug/.build: build-debug/.cfg $(BUILD_DEPS)
	cmake --build build-debug
	@touch $@
# On Linux `ASAN_TEST_ENV` turns LeakSanitizer on. LeakSanitizer runs at a forked test's exit. A leak fails the exact
# test that caused it. Apple clang has no LeakSanitizer, and `ASAN_TEST_ENV` is empty there. The Release run leak-checks
# instead.
#
# The fixtures are an input to the tests as well as to the generator gates. `emitter_reconstructs_fixtures` replays a
# fixture's tokens and requires the bytes back. Without the fixtures among this stamp's prerequisites, a new fixture
# leaves the stamp untouched. A fixture whose tokens do not span its input stays green until another prerequisite
# changes and the stamp rebuilds.
build-debug/.test: build-debug/.build $(FIXTURES)
	$(ASAN_TEST_ENV) ctest --test-dir build-debug --output-on-failure
	@touch $@

# --- Release config. Hardened. ---
build-release/.cfg: $(CMAKE_IN)
	cmake -S . -B build-release -DCMAKE_BUILD_TYPE=Release
	@touch $@
build-release/.build: build-release/.cfg $(BUILD_DEPS)
	cmake --build build-release
	@touch $@
# ctest runs the tests for correctness. On macOS the `LEAK_CHECK` line runs the Release binary through the `leaks` tool.
# That run passes `--no-exec` and stays in the same process. `leaks` cannot inspect the ASan Debug binary. On Linux
# `LEAK_CHECK` is a no-op. LeakSanitizer covers leaks in the Debug run.
build-release/.test: build-release/.build $(FIXTURES)
	ctest --test-dir build-release --output-on-failure
	$(LEAK_CHECK) build-release/test_c --no-exec
	@touch $@

# --- Coverage ---
#
# Configure the coverage build. Instrument, run, and REPORT. The coverage build gates nothing.
# `.stamps/gh-pages-coverage` is the gate, and it is a prerequisite of `gh-pages`. The recipe emits the machine-readable
# reports, the human HTML report and the shields summary. `gh-pages` publishes the HTML report and the summary. ---
build-coverage/.cfg: $(CMAKE_IN)
	cmake -S . -B build-coverage -DYEAST_COVERAGE=ON
	@touch $@
build-coverage/.cov: build-coverage/.cfg $(BUILD_DEPS) scripts/coverage_badge.py
	cmake --build build-coverage
	find build-coverage -name '*.gcda' -delete
	ctest --test-dir build-coverage --output-on-failure
	mkdir -p build-coverage/html
	$(GCOVR) --root . build-coverage --filter src/ --filter tests/ \
	    --gcov-executable "$(GCOV_EXE)" \
	    --cobertura build-coverage/coverage.xml --lcov build-coverage/coverage.lcov \
	    --json build-coverage/coverage.json --json-summary build-coverage/summary.json \
	    --html-details build-coverage/html/index.html --txt
	python3 scripts/coverage_badge.py build-coverage/summary.json build-coverage/coverage-badge.json
	@touch $@

# Enforce the // UNTESTED contract on the report data. This stamp is a target separate from `build-coverage/.cov`. This
# stamp runs under the name gh-pages-coverage.
.stamps/gh-pages-coverage: build-coverage/.cov scripts/coverage_gate.py | .stamps
	python3 scripts/coverage_gate.py build-coverage/coverage.json
	@touch $@

gh-pages-coverage: .stamps/gh-pages-coverage

# The version of clang-format on PATH, as the guard below reports it. Make reads it where the guard fails.
CLANG_FORMAT_FOUND = $(shell "$(CLANG_FORMAT)" --version 2>/dev/null | grep -o 'version [[:digit:].]*' || echo none)

# The version guard for clang-format. The check and the reformat read the same guard.
CLANG_FORMAT_CHECK = "$(CLANG_FORMAT)" --version | grep -q "version $(CLANG_FORMAT_MAJOR_VERSION)\." \
	|| { echo "this project needs clang-format of major version $(CLANG_FORMAT_MAJOR_VERSION). \
	     The clang-format on PATH reports $(CLANG_FORMAT_FOUND). Run make install-deps-vet, or put such a \
	     clang-format on PATH." >&2; exit 1; }

# --- format checks. A stamp per language. A stamp re-checks independently. ---
.stamps/vet-format-c: $(ALL_SRC) .clang-format .clang-format-version | .stamps
	@$(CLANG_FORMAT_CHECK)
	"$(CLANG_FORMAT)" --dry-run --Werror $(ALL_SRC)
	@touch $@

vet-format-c: .stamps/vet-format-c

# The markdown. A line ends at the column the rest of the tree wraps at.
.stamps/vet-format-md: $(MD_FILES) | .stamps
	$(MDFORMAT) --check --wrap 120 $(MD_FILES)
	@touch $@

vet-format-md: .stamps/vet-format-md
# black reformats code but leaves comments and docstrings untouched. format-docstring wraps the docstrings, and
# `wrap_long_comments` wraps the comments. ruff holds the `120`-column rule over the lines those tools leave behind.
# ruff also catches the import that nothing uses. It refuses a text `open` that leaves the encoding to the locale. Such
# an `open` decodes a document by the encoding LANG names. An em-dash then reads as UTF-8 on this machine and fails in a
# C-locale container. The encoding rule is in ruff's preview set. The `--preview` flag turns that rule on.
#
# format-docstring has no check mode. The check runs format-docstring on throwaway copies. The check fails if
# format-docstring would have rewritten a docstring.
.stamps/vet-format-py: $(PY_FILES) | .stamps
	$(BLACK) --check --line-length 120 $(PY_FILES)
	@tmp=$$(mktemp -d); files=""; \
	  for f in $(PY_FILES); do mkdir -p "$$tmp/$$(dirname $$f)"; cp "$$f" "$$tmp/$$f"; files="$$files $$tmp/$$f"; done; \
	  $(FORMAT_DOCSTRING) --line-length 120 --fix-rst-backticks False $$files >/dev/null 2>&1; rc=$$?; \
	  rm -rf "$$tmp"; test $$rc -eq 0 || { echo "docstrings need formatting - run make reformat-py"; exit 1; }
	python3 scripts/wrap_long_comments.py --check $(PY_FILES)
	$(RUFF) check --quiet --preview --select E501,F401,PLW1514 --line-length 120 $(PY_FILES)
	@touch $@

vet-format-py: .stamps/vet-format-py

# The CMake. That is `CMakeLists.txt` and the templates the install step fills in.
.stamps/vet-format-cmake: $(CMAKE_FILES) | .stamps
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --check $(CMAKE_FILES)
	@touch $@

vet-format-cmake: .stamps/vet-format-cmake

# The shell scripts, at an indent of `4` spaces. `shfmt -d` prints the diff it would apply and fails on any.
.stamps/vet-format-sh: $(SH_FILES) | .stamps
	$(SHFMT) -i 4 -d $(SH_FILES)
	@touch $@

vet-format-sh: .stamps/vet-format-sh
# The other languages here have a formatter holding them to the column limit. `make` has none, and this check does that
# job. The count is in characters rather than bytes. An em-dash is `3` bytes and a single character. A byte count
# reports a line that fits as a line that overruns.
.stamps/vet-format-make: Makefile | .stamps
	@python3 -c 'import sys;                                                                                           \
	  said = open("Makefile", encoding="utf-8").read().splitlines();                                                   \
	  wide = [(at, len(line)) for at, line in enumerate(said, 1) if len(line) > 120];                                   \
	  [print(f"Makefile:{at}: {n} columns, where 120 is the limit", file=sys.stderr) for at, n in wide];                \
	  sys.exit(1 if wide else 0)'
	@touch $@

vet-format-make: .stamps/vet-format-make

# Check the C comments. `check_comments.py` decides the form a C comment takes.
.stamps/vet-comments: $(ALL_SRC) scripts/check_comments.py | .stamps
	python3 scripts/check_comments.py $(ALL_SRC)
	@touch $@

vet-comments: .stamps/vet-comments

# clang-tidy rejects the GCC-only warning flags that a GCC build records in `compile_commands.json`.
# `--extra-arg=-Wno-unknown-warning-option` tells clang-tidy to ignore unknown -W options instead of erroring on them.
# `vet-pylint` lints the Python. `ruff` holds the rules a caller asks for. `pylint` reads across a whole module and
# finds faults a per-line rule cannot find. `.pylintrc` says which `pylint` checks this tree keeps.
.stamps/vet-pylint: $(PY_FILES) .pylintrc | .stamps
	$(PYLINT) $(PY_FILES)
	@touch $@

vet-pylint: .stamps/vet-pylint

# The Python types. A definition in a module needs an annotation. `mypy.ini` declares the modules exempt from that need.
#
# The check prints the declarations after mypy passes. A green run over a tree of excused modules reads as a typed tree.
# The check does not count `conanfile`. `mypy.ini` declares that exception.
.stamps/vet-mypy: $(PY_FILES) mypy.ini | .stamps
	$(MYPY) --config-file mypy.ini $(PY_FILES)
	@python3 -c 'import configparser, sys;                                                                             \
	  read = configparser.ConfigParser(); read.read("mypy.ini");                                                       \
	  owed = [name.strip() for section in read.sections() if section.startswith("mypy-")                               \
	          and read[section].get("disallow_untyped_defs") == "False"                                                \
	          for name in section[len("mypy-"):].split(",") if name.strip() != "conanfile"];                            \
	  print(f"types: {len(owed)} module(s) lack an annotation on each definition: {chr(32).join(sorted(owed))}"       \
	        if owed else "types: each module has an annotation on each definition")'
	@touch $@

vet-mypy: .stamps/vet-mypy

# The C lint. A pair of analyzers over the library and the tests. They read the compile database the debug build wrote.
.stamps/vet-lint: $(ALL_SRC) .clang-tidy build-debug/.cfg | .stamps
	$(CLANG_TIDY) --quiet --extra-arg=-Wno-unknown-warning-option $(TIDY_EXTRA) -p build-debug $(LINT_FILES)
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --std=c99 \
	    --suppress=missingIncludeSystem --suppress='*:*third_party*' \
	    -I include -I third_party/acutest src tests
	@touch $@

vet-lint: .stamps/vet-lint

# The leftover-marker scan fails if the marker appears in a tracked file's name or content. The scan ignores case. docs/
# falls outside the scan and documents the marker. This Makefile writes the marker as `$(TODO_X)` rather than literally.
.stamps/vet-$(TODO_X): $(SCAN_FILES) | .stamps
	@if git ls-files -- . ':(exclude)docs' | grep -in '$(TODO_X)'; then \
	    echo "marker found in a file name - rename it"; exit 1; fi
	@if git ls-files -z -- . ':(exclude)docs' | xargs -0 grep -HIni '$(TODO_X)' 2>/dev/null; then \
	    echo "marker found in file content - remove before completion"; exit 1; fi
	@echo "no leftover markers"
	@touch $@

# --- API docs (Doxygen) ---
#
# Generates HTML into `build-docs/html`. The Pages job publishes that. The target also enforces completeness.
# `WARN_AS_ERROR` in the Doxyfile fails on an undocumented public symbol. It fails on a missing @param or @return as
# well. The target is a deliverable and a gate. pc depends on this target directly. The Makefile holds no separate check
# target.
build-docs/.docs: $(PUB_HDR) Doxyfile DoxygenLayout.xml CMakeLists.txt
	YEAST_VERSION="$(VERSION)" doxygen Doxyfile
	@touch $@

# Guards against version drift. CMakeLists project() is the source of truth. The vcpkg port must match it. Conan derives
# its version and cannot drift.
.stamps/vet-version: CMakeLists.txt ports/yeast/vcpkg.json | .stamps
	@vcpkg=$$(sed -n 's/.*"version": *"\([0-9.]*\)".*/\1/p' ports/yeast/vcpkg.json | head -1); \
	if [ "$$vcpkg" != "$(VERSION)" ]; then \
	    echo "version drift: CMakeLists=$(VERSION) vcpkg=$$vcpkg"; exit 1; \
	else echo "version consistent: $(VERSION)"; fi
	@touch $@

vet-version: .stamps/vet-version

# Grammar round-trip. annotated2ir -> IR -> ir2annotated must reproduce libyeast's grammar exactly. This is the
# lossless-ingest gate for the generator. The token annotations count as part of the grammar.
.stamps/verify-roundtrip: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_annotated_roundtrip.py
	@touch $@

verify-roundtrip: .stamps/verify-roundtrip

# Grammar validation. A reference resolves to a production with a matching arity. A production is reachable.
.stamps/verify-references: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/validate_grammar.py
	@touch $@

verify-references: .stamps/verify-references

# Erase libyeast's token annotations and its indicator productions from `grammar/yeast-spec-1.2.yaml`. The remainder
# must be the vendored grammar. An edit meant to add a token annotation must not change an official production.
.stamps/verify-spec: $(GRAMMAR_SPEC) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@

verify-spec: .stamps/verify-spec

# Marker balance. A begin- marker closes on a matching end- marker. A rule balances the pair down any path a parse
# takes. A marker consumes no character. A rule placing a character within a token action says nothing about a marker.
.stamps/verify-markers: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_markers.py
	@touch $@

verify-markers: .stamps/verify-markers

# Grammar documentation. A rule that emits tokens holds a note. That note names the tokens in the order the rule emits
# them. The gate checks the note against the grammar itself. A wrong note fails like a missing one.
.stamps/verify-emits: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_docs.py
	@touch $@

verify-emits: .stamps/verify-emits

# Decoder tables. The committed `src/decoder_tables.h` must be exactly what the grammar produces. A bit of a key names a
# character set. The bit must agree with a direct evaluation of that set.
.stamps/verify-decoder: $(ANNOTATED) $(GEN_SRC) src/decoder_tables.h | .stamps
	python3 generator/check_decoder.py
	@touch $@

verify-decoder: .stamps/verify-decoder

# Conformance fixtures. libyeast's suite in `tests/spec/` must be intact. An input sits beside an output. A name decodes
# to a production the grammar still has. An output is a token stream whose marks chain. The suite came from the vendored
# reference fixtures.
.stamps/verify-fixtures: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_spec_tests.py
	@touch $@

verify-fixtures: .stamps/verify-fixtures

# The community YAML Test Suite, folded down to its events. YAMLStar is the reference libyeast follows. The YAML
# community wrote this suite from the spec. This suite catches a grammar bug libyeast's own fixtures would share.
# libyeast must fold a case to its events, or refuse the case where the suite says reject. The exceptions are the
# declared divergences. The spec and the suite disagree there, and the spec wins.
.stamps/verify-star: $(STAR_DATA) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_star.py
	@touch $@

verify-star: .stamps/verify-star

# The normalization pipeline preserves meaning step after step. A transformation takes the grammar toward the canonical
# form. The fixtures still reproduce token for token past that transformation. A case of the folded suite still passes,
# or sits among the declared divergences. The gate rejects and names a step that changes token identity or event
# identity.
#
# The gate also holds the pipeline to its own law. A count does not rise. A settling step drives a count to nothing, and
# the count stays there. The gate holds the spaces it computes to the places a parse really reached.
.stamps/verify-normalize: $(FIXTURES) $(STAR_DATA) $(MESSAGES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_normalize.py
	@touch $@

verify-normalize: .stamps/verify-normalize

# The gate reads the documents and the generator's prose. DESIGN states a count. The code measures that count and agrees
# with it. PLAN and CHANGELOG state none at all. A document here narrates no history. DESIGN and PLAN cite a name in
# backticks. The tree holds that name.
#
# The gate measures against the pipeline the documents describe. The gate reads the grammar and the steps. The gate
# reads the fixtures too. DESIGN states the number of fixtures.
#
# A C source is a prerequisite twice over. The gate resolves a citation against the sources that can hold a name. The
# same rules cover the prose beside the C and the prose beside the generator.
#
# `README.md` is a prerequisite too. The `verify:` target below decides which gates run. The README lists those gates,
# and that list has to match the target.
#
# The git index is a prerequisite as well. A citation may name a file. The tree holds what `git` tracks. Staging an
# addition or a removal changes the answer as much as editing a source does.
#
# This gate wants a git working tree, and says so where it has none. A wildcard sets the order of the prerequisites. The
# wildcard does not let the gate run without a tree.
.stamps/verify-documents: $(DOCUMENTS) README.md $(ANNOTATED) $(MESSAGES) $(FIXTURES) $(GEN_SRC) $(SCRIPT_SRC) \
                          $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(CMAKE_IN) Makefile \
                          generator/cited_names.json $(wildcard .git/index) | .stamps
	python3 generator/check_documents.py
	@touch $@

verify-documents: .stamps/verify-documents

# generator/ and scripts/ hold no unreachable code. A run or an import reaches a module. A run reaches a top-level
# function. A run reaches a class and a constant too. The generator constructs an `ir` kind. `KEPT_THOUGH_DEAD` and
# `RUN_FROM_ELSEWHERE` declare the exceptions with a reason, and the gate holds the reasons too. This file runs a
# module. The gate reads this file and lists it as a prerequisite.
.stamps/verify-dead-code: $(GEN_SRC) $(SCRIPT_SRC) Makefile | .stamps
	python3 generator/check_dead_code.py
	@touch $@

verify-dead-code: .stamps/verify-dead-code

# Proposals are conventions a review proposed and nobody ruled on. A review writes a proposal to the pending file. A
# ruling moves a proposal into one of a pair of files. Those files are prerequisites. Clearing either file re-runs this
# gate.
.stamps/verify-proposals: .claude/proposals-pending.md .claude/conventions.md .claude/rejected.md $(GEN_SRC) | .stamps
	python3 generator/check_proposals.py
	@touch $@

verify-proposals: .stamps/verify-proposals

# An agent that judges prose holds a copy of the conventions in its definition. A request caches a definition ahead of
# the message. `write_agent_prompts.py` writes that copy. This gate refuses a copy the sources have moved past.
.stamps/verify-agent-prompts: .claude/conventions.md .claude/rejected.md $(AGENT_SRC) $(GEN_SRC) | .stamps
	python3 generator/write_agent_prompts.py --check
	@touch $@

verify-agent-prompts: .stamps/verify-agent-prompts

# The gate runs the hooks against an edit a hook must answer and an edit a hook must pass. A hook that has gone blind is
# silent. A hook that passes a clean edit is silent too. `tests/hooks.json` holds the pair, and `.claude/settings.json`
# names the hooks.
.stamps/verify-hooks: tests/hooks.json .claude/settings.json $(wildcard .claude/hooks/*) $(GEN_SRC) | .stamps
	python3 generator/check_hooks.py
	@touch $@

verify-hooks: .stamps/verify-hooks

# The gate hunts an ignored failure. The gate reads the shell scripts and the recipes in this file. The gate reads the
# review workflow and the Python.
.stamps/verify-failures: $(GEN_SRC) $(SCRIPT_SRC) $(wildcard .claude/hooks/*.sh) $(wildcard .claude/workflows/*.js) \
                         Makefile | .stamps
	python3 generator/check_failures.py
	@touch $@

verify-failures: .stamps/verify-failures

# The conventions a gate can decide. A reader then has no call to judge them.
.stamps/verify-conventions: $(GEN_SRC) $(SCRIPT_SRC) .claude/conventions.md | .stamps
	python3 generator/check_conventions.py
	@touch $@

verify-conventions: .stamps/verify-conventions

# The gate breaks the project into the fragments the critic reads one at a time. A fragment holds prose and a key of its
# own. The rosters in `gate` pick out the files the critic reads. A scan of the tracked files covers those rosters.
.stamps/verify-fragments: $(SCAN_FILES) | .stamps
	python3 generator/collect_fragments.py
	@touch $@

verify-fragments: .stamps/verify-fragments

# The directory holding the settling queues.
PROSE_DIR ?= .git/critic

# The gate reads the prose already in the tree. The gate holds that prose to the rules the write-time hooks apply to an
# edit. A hook refuses a sentence as the writer types it. The hook reads no line the file already held. The gate also
# reads the unsettled prose queue, and a fragment the settling loop gave up on is a fault.
.stamps/verify-prose: $(SCAN_FILES) $(wildcard $(PROSE_DIR)/unsettled-prose.jsonl) | .stamps
	python3 generator/check_prose.py
	@touch $@

verify-prose: .stamps/verify-prose

# The unexamined fragments a run takes. An empty value takes the whole queue.
PROSE_COUNT ?=

# An agent that judges prose runs the model this variable names. An empty value leaves the default in place.
PROSE_MODEL ?=

# The settling pass between the critic and the comparator. The pass spends agent calls, and no gate depends on it.
# `converge_prose` keeps its state in a ledger and in the queues under `PROSE_DIR`. A later run of this target picks up
# that state. `converge_prose` exits with a distinct status where a later run can continue.
examine-prose:
	python3 generator/converge_prose.py $(PROSE_DIR) $(PROSE_COUNT) $(if $(PROSE_MODEL),--model $(PROSE_MODEL),)

# A tracked file holds ASCII. A character past ASCII looks like the ASCII character it replaces. Such a character also
# makes a column count over a line disagree with the editor. `check_ascii` declares the paths whose content may hold
# such a character.
.stamps/verify-ascii: $(SCAN_FILES) generator/check_ascii.py | .stamps
	python3 generator/check_ascii.py
	@touch $@

verify-ascii: .stamps/verify-ascii

# Wire code map. The character-per-code table in `wire.py` must match the table in `src/wire.c`. The interpreter cannot
# write a code the C parser would write differently.
.stamps/verify-wire: src/wire.c $(GEN_SRC) | .stamps
	python3 generator/check_wire.py
	@touch $@

verify-wire: .stamps/verify-wire

# A rewind undoes the emitter's work. An alternation rewinds to a single checkpoint once per branch. The gate checks
# that the second rewind restores the fields the first rewind restored. The fixtures cannot tell whether the rewinds
# match. An aliased checkpoint reproduces the fixtures and breaks the rewind.
.stamps/verify-emitter: $(GEN_SRC) | .stamps
	python3 generator/check_emitter.py
	@touch $@

verify-emitter: .stamps/verify-emitter

# A guard names a subset of the states a parse can decide in. The gate lists the states an operation holds. The list
# covers an alphabet and the answers a parse reaches at a guard. The gate compares union, intersection and containment
# with set arithmetic over that list.
.stamps/verify-spaces: $(GEN_SRC) | .stamps
	python3 generator/check_spaces.py
	@touch $@

verify-spaces: .stamps/verify-spaces

# Error messages. A `(cut)` in the grammar names a message defined in `grammar/messages.yaml`. The cut sites and their
# text are the source. The interpreter and the generated C table both derive from that source.
.stamps/verify-messages: $(ANNOTATED) $(MESSAGES) $(GEN_SRC) | .stamps
	python3 generator/check_messages.py
	@touch $@

verify-messages: .stamps/verify-messages

# The reference interpreter reproduces a fixture it covers. It runs the production the grammar describes. Its token
# stream must equal the fixture's stream byte for byte. The interpreter's coverage grows a node family at a time. The
# fixtures this gate reproduces grow with that coverage.
.stamps/verify-grammar-base: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_interpreter.py
	@touch $@

verify-grammar-base: .stamps/verify-grammar-base

# The fixtures exercise the productions the grammar has. The gate runs the reproducible fixtures. A fixture matches or
# evaluates a production. A production that no fixture reaches is a coverage gap. The gap counts the same way against
# the base grammar and against a transformed grammar.
.stamps/verify-grammar-base-coverage: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_coverage.py
	@touch $@

verify-grammar-base-coverage: .stamps/verify-grammar-base-coverage

# The `regen` target rewrites the committed generated files. The files are `src/decoder_tables.h`, the agent definitions
# that judge prose, and `generator/cited_names.json`.
regen:
	python3 generator/grammar2decoder.py
	python3 generator/write_agent_prompts.py
	python3 generator/write_cited_names.py

# --- package-consumption test. Install Release, then build and run a consumer via pkg-config. ---
build-pkgtest/.pkg: build-release/.build $(PKG_SRC)
	rm -rf "$(PKG_PREFIX)"
	cmake --install build-release --prefix "$(PKG_PREFIX)"
	@set -e; \
	export PKG_CONFIG_PATH="$(PKG_PREFIX)/lib/pkgconfig"; \
	cflags=$$(pkg-config --cflags yeast); \
	libs=$$(pkg-config --libs yeast); \
	libdir=$$(pkg-config --variable=libdir yeast); \
	test -n "$$cflags" || { echo "pkg-config resolved no yeast in $$PKG_CONFIG_PATH"; exit 1; }; \
	echo "-- shared --"; \
	$(CC) tests/pkg_consumer.c $$cflags $$libs -Wl,-rpath,"$$libdir" -o build-pkgtest/consumer_shared; \
	out=$$(build-pkgtest/consumer_shared); test -n "$$out" || { echo "shared consumer: empty or failed"; exit 1; }; \
	echo "   version: $$out"; \
	echo "-- static --"; \
	$(CC) tests/pkg_consumer.c $$cflags "$$libdir/libyeast.a" -o build-pkgtest/consumer_static; \
	out=$$(build-pkgtest/consumer_static); test -n "$$out" || { echo "static consumer: empty or failed"; exit 1; }; \
	echo "   version: $$out"; \
	echo "pkg-test OK"
	@touch $@

# --- goal tree. A sub-goal takes its parent's name. `make <parent>` runs the group, and `make <parent>-<part>` runs
# a part.
#
# The goals group by their job. Build and test the C library with `all`, `install` and `test`. Those are pure C and want
# no Python. Verify the generator pipeline with `verify`. Regenerate the pipeline's outputs with `regen`. `make pc` is
# the developer gate over those groups. ---

# The consumer mode. Pure C, with no Python and no staleness checks.
install: build-release/.build
	cmake --install build-release --prefix "$(PREFIX)"
test-debug: build-debug/.test      # the sanitized build. It catches what a release build hides.
test-release: build-release/.test  # the optimized build. A consumer links against this build.
test: test-debug test-release

# The verify mode. It checks that the generator pipeline is correct. It checks that the outputs are current.
#
# The base grammar reproduces the fixtures bottom-up. The fixtures exercise the base grammar throughout.
verify-grammar: verify-grammar-base verify-grammar-base-coverage
# In dependency order. First the grammar as itself. Then its compatibility with the official spec. Then the interpreter
# machinery. Then the fixtures intact. Then the fixtures reproduced. Then the independent star suite folded through the
# interpreter. Last the generator-to-C consistency the C parser rests on.
verify: verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star \
        verify-normalize verify-documents verify-dead-code verify-proposals verify-failures verify-conventions \
        verify-agent-prompts \
        verify-hooks verify-fragments verify-prose verify-ascii \
        verify-wire verify-decoder verify-spaces

# Static code quality.
vet-format: vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-make

# The packaging test. Its stamp sits under the build tree rather than under `.stamps`.
vet-packaging: build-pkgtest/.pkg

# The leftover-marker scan. This Makefile names the target through a substitution. That substitution keeps the marker
# out of the file.
vet-$(TODO_X): .stamps/vet-$(TODO_X)

# The static quality mode. It is the formatters, the linters and the packaging test together.
vet: vet-format vet-comments vet-lint vet-pylint vet-mypy vet-$(TODO_X) vet-version vet-packaging

# The GitHub Pages payload. That is the Doxygen API docs, the gcovr HTML report and the coverage gate.
gh-pages-docs: build-docs/.docs

# The tree the Pages job publishes. The docs and the coverage report land there together.
build-gh-pages/.assembled: build-docs/.docs build-coverage/.cov
	rm -rf build-gh-pages
	mkdir -p build-gh-pages/coverage
	cp -R build-docs/html/. build-gh-pages/
	cp -R build-coverage/html/. build-gh-pages/coverage/
	cp build-coverage/coverage-badge.json build-gh-pages/coverage.json
	@touch $@
gh-pages: build-gh-pages/.assembled gh-pages-coverage

# reformat-* CHANGE source files in place. vet-format-* only verify.
reformat-c:
	@$(CLANG_FORMAT_CHECK)
	"$(CLANG_FORMAT)" -i $(ALL_SRC)
# The markdown, rewrapped.
reformat-md:
	$(MDFORMAT) --wrap 120 $(MD_FILES)
# The Python. `format-docstring` exits `1` where it rewrote a file, and the recipe reads that as success.
reformat-py:
	$(BLACK) --line-length 120 $(PY_FILES)
	$(FORMAT_DOCSTRING) --line-length 120 --fix-rst-backticks False $(PY_FILES) || [ $$? -eq 1 ]
	python3 scripts/wrap_long_comments.py --apply $(PY_FILES)
# The CMake, rewritten in place.
reformat-cmake:
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --in-place $(CMAKE_FILES)

# The shell scripts. `shfmt` rewrites them in place.
reformat-sh:
	$(SHFMT) -i 4 -w $(SH_FILES)

# The `Makefile`'s own comments. `wrap_long_comments` wraps a comment block here at the column limit. That script wraps
# a comment block in a Python file the same way.
reformat-make:
	python3 scripts/wrap_long_comments.py --apply Makefile

# The languages above in a single run. A developer runs this before the gate.
reformat: reformat-c reformat-md reformat-py reformat-cmake reformat-sh reformat-make

# The tools a C build needs. The check names a missing tool rather than letting a recipe fail deep inside.
check-build-deps:
	@sh scripts/check-deps.sh $(BUILD_DEP_TOOLS)
check-dev-deps: check-build-deps
	@sh scripts/check-deps.sh $(DEV_DEP_TOOLS)

# Install the tools a goal needs. The script detects the OS. `make install-deps` installs the tools that `make all`,
# `make test` and `make install` need. `make install-deps-pc` adds the tools of the `pc` sub-gate. `vet`, `verify` and
# `gh-pages` take the same form.
install-deps:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-build-deps.sh
# The tools a named sub-gate adds on top. The stem is the goal. The dev-deps script reads the stem.
install-deps-%:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-dev-deps.sh $*

# --- The `pc` target is the pre-commit gate. It builds first. It then runs `test` and `verify`. It then runs `vet` and
# `publish`.
#
# A CI workflow runs a sub-gate and shows a badge. `test` and `verify` are sub-gates. `vet` and `gh-pages` are sub-gates
# as well. A sub-gate builds its own inputs. CI does not run `pc`. ---
pc: all test verify vet gh-pages

# Throw away the files a build and a gate wrote. The stamps go too. The next run re-checks from cold.
clean:
	rm -rf build build-debug build-release build-coverage build-pkgtest build-docs build-gh-pages .stamps
