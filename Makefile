# libyeast build/verify orchestration.
#
# A check is a *stamp file* target that depends on its real inputs (sources, configs) and is `touch`ed only after the
# check succeeds. A failed command aborts the recipe before the touch. A stamp does not record a false success. `make
# <target>` with nothing changed does nothing at all. CMake does the heavy lifting of compilation, and CMake is itself
# incremental.
#
# A part of a mode sits beside the stamp rule that does its work.

# clang-format's LLVM style shifts between major versions. The UTF-8 column width and the trailing-comment alignment
# move. The style is stable within a major. `22.1.5` and `22.1.8` agree. `18` and `22` do not. So the gate accepts any
# build of a single major.
#
# .clang-format-version is the source of truth for that major. The gate reads it here. The dev-deps scripts read the
# same file when they install the wheel.
#
# CLANG_FORMAT resolves to that wheel's binary, and the format targets verify the major before trusting it. A developer
# with any build of it on PATH works without the wheel. Any other major fails the gate with instructions.
CLANG_FORMAT_MAJOR_VERSION := $(shell cat .clang-format-version)
# The C formatter. The installed wheel provides the binary, and PATH provides it otherwise.
CLANG_FORMAT ?= $(shell python3 -c "import clang_format, os; \
                  print(os.path.join(os.path.dirname(clang_format.__file__), 'data', 'bin', 'clang-format'))" \
                  2>/dev/null || echo clang-format)
# The C linter, taken from PATH and then from the Homebrew llvm prefix. macOS keeps it there.
CLANG_TIDY ?= $(shell command -v clang-tidy 2>/dev/null \
                || command -v "$$(brew --prefix llvm 2>/dev/null)/bin/clang-tidy" 2>/dev/null \
                || echo clang-tidy)
# The second C analyzer. It runs beside clang-tidy.
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

# The Python linter that runs first, over line length and unused imports.
RUFF ?= ruff

# The Python linter that reads across a whole module.
PYLINT ?= pylint

# The Python type checker. It decides the annotations.
MYPY ?= mypy

# Platform split. The gcov reader is llvm-cov for Apple and Homebrew clang, and gcov directly for GCC.
#
# Leak detection differs too. Linux ASan ships LeakSanitizer, and the build enables it explicitly. It runs at a forked
# test's exit. The run is per test and isolated.
#
# Apple clang has no LeakSanitizer. The Darwin gate instead runs the whole Release suite through the `leaks` tool.
ifeq ($(shell uname -s),Darwin)
GCOV_EXE      ?= xcrun llvm-cov gcov
# The path the SDK headers are on. clang-tidy has no compiler driver to ask. This tells it the path.
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

# The install-deps scripts `make install-deps*` runs, auto-detected so the user does not name their OS. A Linux
# without apt is not Debian/Ubuntu and has no script. DEPS_OS then stays empty, and the install-deps recipe fails with
# what to do.
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
	pkg-config yourself. scripts/install-*-build-deps.sh lists them. The other packages are formatters, linters, and \
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
# The test framework, vendored.
VENDOR_H := $(wildcard third_party/acutest/*.h)
# The inputs that configure the build.
CMAKE_IN := CMakeLists.txt $(wildcard cmake/*.in)
# Version, taken from CMakeLists project(). `.` matches the literal '('. make's paren-balancing in $(shell ...) then
# stays happy.
VERSION   := $(shell sed -n 's/^project.yeast VERSION \([0-9][0-9.]*\).*/\1/p' CMakeLists.txt)
# The sources the format, lint and comment checks scan.
ALL_SRC := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c)
# Common find prune, reused below. It skips .git and vendored third_party. It also skips build dirs and untracked
# junk-* scratch.
#
# `.claude/agents` holds agent definitions. Those are YAML frontmatter with prose under it rather than documents. A
# markdown formatter reads the `---` fences as a horizontal rule. It folds the frontmatter into a heading. That leaves
# a definition nothing can parse. The prune covers them with the rest of what this project does not own.
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
# Scaffolding marker, written via subst so this Makefile does not contain it literally.
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
# The official grammar, vendored.
GRAMMAR_SPEC := third_party/yaml-grammar/yaml-spec-1.2.yaml
# libyeast's grammar. The generator reads it.
ANNOTATED := grammar/yeast-spec-1.2.yaml
# The table the grammar's cuts and errors report from.
MESSAGES := grammar/messages.yaml
# The generator and its gates.
GEN_SRC := $(wildcard generator/*.py)
# The helpers the build and the gates call.
SCRIPT_SRC := $(wildcard scripts/*.py)
# The documents `check_documents` holds to the counts and the citations they state.
DOCUMENTS := DESIGN.md PLAN.md CHANGELOG.md
# The conformance corpus. A case holds an input and the tokens it expects.
FIXTURES := $(wildcard tests/spec/*.input tests/spec/*.output)
# The YAML Test Suite as git checks it out. A case holds an input, the events it expects, and an error marker.
STAR_DATA  := $(wildcard third_party/yaml-test-suite/*/in.yaml third_party/yaml-test-suite/*/*/in.yaml \
                         third_party/yaml-test-suite/*/test.event third_party/yaml-test-suite/*/*/test.event \
                         third_party/yaml-test-suite/*/error third_party/yaml-test-suite/*/*/error)

# Tool dependencies, verified by check-build-deps / check-dev-deps. Python is not a C build dep. The build uses the
# committed generated files. Python rides with the dev tools, for the generator that verify and regen run.
BUILD_DEP_TOOLS := cmake $(CC)
# The tools a developer needs on top of those. The formatters and linters named above, plus Python and the doc builder.
DEV_DEP_TOOLS   := python3 python3:yaml python3:conan $(CLANG_FORMAT) $(CLANG_TIDY) $(CPPCHECK) $(GCOVR) \
                   $(MDFORMAT) $(BLACK) $(FORMAT_DOCSTRING) $(RUFF) $(GERSEMI) $(SHFMT) $(MYPY) doxygen \
                   $(firstword $(GCOV_EXE))

.PHONY: all package install test test-debug test-release regen \
        verify verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star \
        verify-normalize verify-documents verify-dead-code verify-proposals verify-failures verify-conventions \
        verify-hooks verify-agent-tools verify-fragments verify-prose verify-ascii \
        verify-wire verify-decoder verify-spaces \
        verify-grammar-base verify-grammar-base-coverage \
        vet vet-format vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-make \
        vet-comments vet-lint vet-pylint vet-mypy vet-version vet-packaging vet-$(TODO_X) \
        gh-pages gh-pages-docs gh-pages-coverage \
        reformat reformat-c reformat-md reformat-py reformat-cmake reformat-sh \
        check-build-deps check-dev-deps install-deps pc clean

# The default goal. It is the C library packaged for a consumer.
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

# The shared and static libraries, built from the committed sources with no generator step.
package: build/.package

# --- Debug config. Sanitizers. ---
build-debug/.cfg: $(CMAKE_IN)
	cmake -S . -B build-debug -DCMAKE_BUILD_TYPE=Debug
	@touch $@
build-debug/.build: build-debug/.cfg $(BUILD_DEPS)
	cmake --build build-debug
	@touch $@
# On Linux ASAN_TEST_ENV turns LeakSanitizer on. It runs at a forked test's exit. A leak fails the exact test that
# caused it. Apple clang has no LeakSanitizer, and ASAN_TEST_ENV is empty there. The Release run leak-checks instead.
#
# The fixtures are an input to the tests, not only to the generator gates. `emitter_reconstructs_fixtures` replays a
# fixture's tokens and requires the bytes back. Without them here a new fixture leaves this stamp untouched. A fixture
# whose tokens do not span its input stays green until something else rebuilds.
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
# ctest runs the tests for correctness. The LEAK_CHECK line then leak-checks. On macOS that runs the Release binary
# through the `leaks` tool single-process with `--no-exec`. The Release binary is no ASan build. `leaks` cannot inspect
# the ASan Debug binary. On Linux LEAK_CHECK is a no-op. LeakSanitizer already covered leaks in the Debug run.
build-release/.test: build-release/.build $(FIXTURES)
	ctest --test-dir build-release --output-on-failure
	$(LEAK_CHECK) build-release/test_c --no-exec
	@touch $@

# --- Coverage config. Instrument, run, and REPORT. This gates nothing. .stamps/gh-pages-coverage is the gate, and it
# rides gh-pages. Emits the machine-readable reports plus the human HTML report and the shields summary that gh-pages
# publishes. ---
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

# Enforce the // UNTESTED contract on the report data. Separate from build-coverage/.cov so the Pages job can publish
# the report without gating. This stamp rides with gh-pages, as gh-pages-coverage.
.stamps/gh-pages-coverage: build-coverage/.cov scripts/coverage_gate.py | .stamps
	python3 scripts/coverage_gate.py build-coverage/coverage.json
	@touch $@

gh-pages-coverage: .stamps/gh-pages-coverage

# The version guard for clang-format, shared by the check and the reformat so neither trusts the wrong major.
CLANG_FORMAT_CHECK = "$(CLANG_FORMAT)" --version | grep -q "version $(CLANG_FORMAT_MAJOR_VERSION)\." \
	|| { echo "clang-format $(CLANG_FORMAT_MAJOR_VERSION).x required, found: $$("$(CLANG_FORMAT)" --version 2>/dev/null \
	     | grep -o 'version [0-9.]*' || echo none). Run make install-deps-vet, or put a \
	     $(CLANG_FORMAT_MAJOR_VERSION).x clang-format on PATH." >&2; exit 1; }

# --- format checks. A stamp per language. A stamp re-checks independently. ---
.stamps/vet-format-c: $(ALL_SRC) .clang-format .clang-format-version | .stamps
	@$(CLANG_FORMAT_CHECK)
	"$(CLANG_FORMAT)" --dry-run --Werror $(ALL_SRC)
	@touch $@

vet-format-c: .stamps/vet-format-c

# The markdown, wrapped at the column the rest of the tree wraps at.
.stamps/vet-format-md: $(MD_FILES) | .stamps
	$(MDFORMAT) --check --wrap 120 $(MD_FILES)
	@touch $@

vet-format-md: .stamps/vet-format-md
# black reformats code but leaves comments and docstrings untouched. format-docstring wraps the docstrings, and
# wrap_long_comments wraps the comments. ruff holds the `120`-column rule over what those tools leave behind. ruff also
# catches the import that nothing uses. It refuses a text `open` that leaves the encoding to the locale. Such an
# `open` decodes a document by whatever LANG says. An em-dash then reads as UTF-8 on this machine and fails in a
# C-locale container.
# That last rule is in ruff's preview set. That is what --preview is for.
#
# format-docstring has no check mode. The check runs it on throwaway copies. It fails if format-docstring would have
# rewritten any. A failing exit means format-docstring would rewrite a docstring.
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

# The C comments, held to what `check_comments.py` decides about how a writer writes them.
.stamps/vet-comments: $(ALL_SRC) scripts/check_comments.py | .stamps
	python3 scripts/check_comments.py $(ALL_SRC)
	@touch $@

vet-comments: .stamps/vet-comments

# clang-tidy is clang-based. It rejects the GCC-only warning flags that a GCC build records in compile_commands.json.
# `--extra-arg=-Wno-unknown-warning-option` tells clang-tidy to ignore unknown -W options instead of erroring on them.
# The Python lint. `ruff` holds the rules a caller asks for and is fast. `pylint` reads across a whole module and finds
# what a per-line rule cannot. `.pylintrc` says which of its opinions this tree keeps.
.stamps/vet-pylint: $(PY_FILES) .pylintrc | .stamps
	$(PYLINT) $(PY_FILES)
	@touch $@

vet-pylint: .stamps/vet-pylint

# The Python types. A definition in a module needs an annotation. `mypy.ini` declares what falls outside that.
#
# The check prints the declarations after mypy passes. A green run over a tree of excused modules reads as a typed
# tree. The modules still owed say otherwise. `conanfile` is not counted. `mypy.ini` declares that exception.
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

# The leftover-marker scan fails if the marker appears in a tracked file's name or content. The scan ignores case.
# docs/ falls outside the scan and documents the marker. This Makefile writes the marker as $(TODO_X) rather than
# literally.
.stamps/vet-$(TODO_X): $(SCAN_FILES) | .stamps
	@if git ls-files -- . ':(exclude)docs' | grep -in '$(TODO_X)'; then \
	    echo "marker found in a file name - rename it"; exit 1; fi
	@if git ls-files -z -- . ':(exclude)docs' | xargs -0 grep -HIni '$(TODO_X)' 2>/dev/null; then \
	    echo "marker found in file content - remove before completion"; exit 1; fi
	@echo "no leftover markers"
	@touch $@

# --- API docs (Doxygen) ---
# Generates HTML into build-docs/html. The Pages job publishes that. It also enforces completeness. WARN_AS_ERROR in
# the Doxyfile fails on an undocumented public symbol. It fails on a missing @param or @return as well. Deliverable and
# gate in one. pc depends on it directly, and there is no separate check target.
build-docs/.docs: $(PUB_HDR) Doxyfile DoxygenLayout.xml CMakeLists.txt
	YEAST_VERSION="$(VERSION)" doxygen Doxyfile
	@touch $@

# Guards against version drift. CMakeLists project() is the source of truth. The vcpkg port must match it. Conan
# derives its version and cannot drift.
.stamps/vet-version: CMakeLists.txt ports/yeast/vcpkg.json | .stamps
	@vcpkg=$$(sed -n 's/.*"version": *"\([0-9.]*\)".*/\1/p' ports/yeast/vcpkg.json | head -1); \
	if [ "$$vcpkg" != "$(VERSION)" ]; then \
	    echo "version drift: CMakeLists=$(VERSION) vcpkg.json=$$vcpkg"; exit 1; \
	else echo "version consistent: $(VERSION)"; fi
	@touch $@

vet-version: .stamps/vet-version

# Grammar round-trip. annotated2ir -> IR -> ir2annotated must reproduce libyeast's grammar exactly. This is the
# lossless-ingest gate for the generator. The translation drops and mangles no part of the grammar, and the token
# annotations count as part of it.
.stamps/verify-roundtrip: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_annotated_roundtrip.py
	@touch $@

verify-roundtrip: .stamps/verify-roundtrip

# Grammar validation. A reference resolves to a production with a matching arity. A production is reachable.
.stamps/verify-references: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/validate_grammar.py
	@touch $@

verify-references: .stamps/verify-references

# The official grammar, recovered. Erase libyeast's token annotations and its indicator productions from
# grammar/yeast-spec-1.2.yaml. The remainder must be the vendored grammar. An addition libyeast makes cannot quietly
# become a change libyeast makes.
.stamps/verify-spec: $(GRAMMAR_SPEC) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@

verify-spec: .stamps/verify-spec

# Marker balance. A begin- marker closes on its own end- down any path. A rule balances them the same way down
# whichever path a parse takes. A marker consumes no character. The rule that a character lies within a token action
# says nothing about a marker.
.stamps/verify-markers: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_markers.py
	@touch $@

verify-markers: .stamps/verify-markers

# Grammar documentation. A rule that emits tokens names those tokens in the order the rule emits them. The gate checks
# the note against the grammar itself. A wrong note fails like a missing one.
.stamps/verify-emits: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_docs.py
	@touch $@

verify-emits: .stamps/verify-emits

# Decoder tables. The committed src/decoder_tables.h must be exactly what the grammar produces. A bit of a key
# must agree with a direct evaluation of the character set it names. The classification cannot drift.
.stamps/verify-decoder: $(ANNOTATED) $(GEN_SRC) src/decoder_tables.h | .stamps
	python3 generator/check_decoder.py
	@touch $@

verify-decoder: .stamps/verify-decoder

# Conformance fixtures. libyeast's suite in tests/spec/ must be intact. An input sits beside an output. A name
# decodes to a production the grammar still has. An output is a token stream whose marks chain. The suite came from
# the vendored reference fixtures. libyeast keeps it correct from here.
.stamps/verify-fixtures: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_spec_tests.py
	@touch $@

verify-fixtures: .stamps/verify-fixtures

# The community YAML Test Suite, folded down to its events. YAMLStar is the reference libyeast follows. This is the
# star net, an independent suite written by other hands from the same spec. libyeast's fixtures came from a single
# reference. The net catches a grammar bug those fixtures would share. A case must fold to its events, or reject where
# the suite says reject. The exceptions are the declared divergences, where the spec and the suite disagree and the
# spec wins.
.stamps/verify-star: $(STAR_DATA) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_star.py
	@touch $@

verify-star: .stamps/verify-star

# The normalization pipeline preserves meaning step after step. Past a transformation taking the grammar toward the
# canonical form, the fixtures still reproduce token for token. The folded suite still agrees green-or-declared. The
# fixtures keep exercising the final grammar throughout. The proof rests on token identity and event identity. The
# gate rejects and names a step that changes either.
#
# The gate also holds the pipeline to its own law. A count does not rise. A settling step leaves none, and none stays
# none. The gate holds the spaces it computes to the places a parse really reached.
.stamps/verify-normalize: $(FIXTURES) $(STAR_DATA) $(MESSAGES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_normalize.py
	@touch $@

verify-normalize: .stamps/verify-normalize

# The gate reads the documents and the generator's prose. A count DESIGN states is a count the code measures and
# agrees with. PLAN and CHANGELOG state none at all. A document here narrates no history. A name DESIGN and PLAN cite
# in backticks names something in the tree.
#
# It measures against the pipeline the documents describe. The grammar and the steps are among what it reads. So are
# the fixtures, whose number DESIGN states.
#
# A C source is a prerequisite twice over. The gate resolves a citation against the sources that can hold a name. The
# the same rules cover the prose beside the C and the prose beside the generator.
#
# `README.md` is a prerequisite too. The `verify:` target below decides what runs, and the list of gates the README
# gives readers has to match it.
#
# The index is among them. A citation may name a file. The tree holds what `git` tracks. Staging an addition or
# a removal changes the answer as much as editing a source does.
#
# This gate wants a git working tree, and says so where it has none. The wildcard fixes the ordering rather than
# letting the gate run without a tree.
.stamps/verify-documents: $(DOCUMENTS) README.md $(ANNOTATED) $(MESSAGES) $(FIXTURES) $(GEN_SRC) $(SCRIPT_SRC) \
                          $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(CMAKE_IN) Makefile \
                          $(wildcard .git/index) | .stamps
	python3 generator/check_documents.py
	@touch $@

verify-documents: .stamps/verify-documents

# generator/ and scripts/ hold no unreachable code. There is no module nothing runs and nothing imports. There is no
# top-level function that nothing reaches from what runs, and the same holds for a class and for a constant. There is
# no `ir` kind nothing constructs. `KEPT_THOUGH_DEAD` and `RUN_FROM_ELSEWHERE` declare the exceptions with a reason,
# and the gate holds the reasons too. This file runs a module. This file is a prerequisite of the check that reads it.
.stamps/verify-dead-code: $(GEN_SRC) $(SCRIPT_SRC) Makefile | .stamps
	python3 generator/check_dead_code.py
	@touch $@

verify-dead-code: .stamps/verify-dead-code

# Proposals are conventions a review proposed and nobody ruled on. The pending file is what the review writes them to.
# The pair of files a ruling moves them into are prerequisites. Clearing either re-runs this.
.stamps/verify-proposals: .claude/proposals-pending.md .claude/conventions.md .claude/rejected.md $(GEN_SRC) | .stamps
	python3 generator/check_proposals.py
	@touch $@

verify-proposals: .stamps/verify-proposals

# The tools `agent-tools.sh` withheld from a background agent. That hook appends a line per refusal, and a refusal
# raises no prompt. This is where a withheld tool becomes visible. Widening the hook's list re-runs this.
.stamps/verify-agent-tools: .claude/hooks/agent-tools.sh $(GEN_SRC) | .stamps
	python3 generator/check_agent_tools.py
	@touch $@

verify-agent-tools: .stamps/verify-agent-tools

# The hooks, run against an edit a hook must answer and an edit a hook must pass. A hook that has gone blind is silent,
# and silence is what it says about a clean edit. `tests/hooks.json` holds the pair, and `.claude/settings.json` is
# the roster.
.stamps/verify-hooks: tests/hooks.json .claude/settings.json $(wildcard .claude/hooks/*) $(GEN_SRC) | .stamps
	python3 generator/check_hooks.py
	@touch $@

verify-hooks: .stamps/verify-hooks

# The gate hunts an ignored failure, over the surfaces that run a command or catch an error. Those are the shell
# scripts and the recipes in this file. They are also the review workflow and the Python.
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

# The project, broken into the fragments the critic reads one at a time. A fragment holds prose and a key of its
# own. The rosters in `gate` pick out what it reads, and the whole tracked scan covers those.
.stamps/verify-fragments: $(SCAN_FILES) | .stamps
	python3 generator/collect_fragments.py
	@touch $@

verify-fragments: .stamps/verify-fragments

# The prose already in the tree, held to the rules the write-time hooks apply to an edit. A hook refuses a sentence as
# the writer types it and has read no line the file already held.
.stamps/verify-prose: $(SCAN_FILES) | .stamps
	python3 generator/check_prose.py
	@touch $@

verify-prose: .stamps/verify-prose

# A tracked file holds ASCII. A character past ASCII looks like the ASCII character it replaces, and a column
# count over the line disagrees with the editor. `check_ascii` declares the paths holding such a character as content.
.stamps/verify-ascii: $(SCAN_FILES) generator/check_ascii.py | .stamps
	python3 generator/check_ascii.py
	@touch $@

verify-ascii: .stamps/verify-ascii

# Wire code map. The character-per-code table in wire.py must match the table in src/wire.c. The interpreter cannot
# write a code the C parser would write differently.
.stamps/verify-wire: src/wire.c $(GEN_SRC) | .stamps
	python3 generator/check_wire.py
	@touch $@

verify-wire: .stamps/verify-wire

# The emitter can be undone. A checkpoint restores a field it must restore. The second restore matches the first. An
# alternation rewinds to a single checkpoint once per branch. The backtracking rests on this. The fixtures cannot see
# it. An aliased checkpoint reproduced the fixtures while breaking the rewind.
.stamps/verify-emitter: $(GEN_SRC) | .stamps
	python3 generator/check_emitter.py
	@touch $@

verify-emitter: .stamps/verify-emitter

# The subspace algebra. A guard names a subset of the states a parse can decide in. The states an operation holds
# judge that operation, enumerated over a small alphabet and over the guard answers the parse reaches. The gate checks
# union, intersection and containment against set arithmetic rather than against themselves.
.stamps/verify-spaces: $(GEN_SRC) | .stamps
	python3 generator/check_spaces.py
	@touch $@

verify-spaces: .stamps/verify-spaces

# Error messages. A `(cut)` in the grammar names a message defined in messages.yaml. A message is named by a cut.
# The cut sites and their text stay the source the interpreter and the generated C table both derive from.
.stamps/verify-messages: $(ANNOTATED) $(MESSAGES) $(GEN_SRC) | .stamps
	python3 generator/check_messages.py
	@touch $@

verify-messages: .stamps/verify-messages

# The reference interpreter reproduces a fixture it covers. It runs the production the grammar describes. Its token
# stream must equal the fixture's stream byte for byte. This is where the grammar proves it emits the reference's
# tokens.
# The interpreter's coverage grows a node family at a time, and with it the fixtures this gate reproduces.
.stamps/verify-grammar-base: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_interpreter.py
	@touch $@

verify-grammar-base: .stamps/verify-grammar-base

# The fixtures exercise the productions the grammar has. Running the reproducible ones matches or evaluates them. A
# production that no fixture reaches is a coverage gap, whichever grammar this runs against. That is the base grammar
# here, and a transformed grammar later. The same suite must still exercise the reshaped productions.
.stamps/verify-grammar-base-coverage: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_coverage.py
	@touch $@

verify-grammar-base-coverage: .stamps/verify-grammar-base-coverage

# The regenerate mode. It rewrites the committed generated files. `src/decoder_tables.h` is what that comes to here.
# The parser's tables will follow, as another line.
regen:
	python3 generator/grammar2decoder.py

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
	out=$$(build-pkgtest/consumer_shared); test -n "$$out" || { echo "shared consumer: empty/failed"; exit 1; }; \
	echo "   version: $$out"; \
	echo "-- static --"; \
	$(CC) tests/pkg_consumer.c $$cflags "$$libdir/libyeast.a" -o build-pkgtest/consumer_static; \
	out=$$(build-pkgtest/consumer_static); test -n "$$out" || { echo "static consumer: empty/failed"; exit 1; }; \
	echo "   version: $$out"; \
	echo "pkg-test OK"
	@touch $@

# --- goal tree. A sub-goal takes its parent's name. `make <parent>` runs the group, and `make <parent>-<part>` runs
# a part.
#
# There are `3` ways to work. Build and test the C library with `all`, `install` and `test`. Those are pure C and want
# no Python. Verify the generator pipeline with `verify`. Regenerate its outputs with `regen`. `make pc` is the
# developer gate over the `3`. ---

# The consumer mode. Pure C, with no Python and no staleness checks.
install: build-release/.build
	cmake --install build-release --prefix "$(PREFIX)"
test-debug: build-debug/.test      # the sanitized build. It catches what a release build hides.
test-release: build-release/.test  # the optimized build, the build a consumer links against.
test: test-debug test-release

# The verify mode. It checks the generator pipeline is correct and the outputs current.
#
# A grammar reproduces the fixtures bottom-up, and the fixtures exercise it throughout. That is the base grammar here,
# and the structural grammar once it exists.
verify-grammar: verify-grammar-base verify-grammar-base-coverage
# In dependency order. First the grammar as itself. Then its compatibility with the official spec. Then the interpreter
# machinery. Then the fixtures, intact and reproduced after that. Then the independent star suite folded through the
# interpreter. Last the generator-to-C consistency the eventual C parser rests on.
verify: verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star \
        verify-normalize verify-documents verify-dead-code verify-proposals verify-failures verify-conventions \
        verify-hooks verify-agent-tools verify-fragments verify-prose verify-ascii \
        verify-wire verify-decoder verify-spaces

# Static code quality.
vet-format: vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-make

# The packaging test. Its stamp sits under the build tree rather than under `.stamps`.
vet-packaging: build-pkgtest/.pkg

# The leftover-marker scan, named through the same substitution that keeps the marker out of this file.
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
reformat-py:
	$(BLACK) --line-length 120 $(PY_FILES)
	$(FORMAT_DOCSTRING) --line-length 120 --fix-rst-backticks False $(PY_FILES) || [ $$? -eq 1 ]  # `1`: it rewrote a file.
	python3 scripts/wrap_long_comments.py --apply $(PY_FILES)
# The CMake, rewritten in place.
reformat-cmake:
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --in-place $(CMAKE_FILES)

# The shell scripts. `shfmt` rewrites them in place.
reformat-sh:
	$(SHFMT) -i 4 -w $(SH_FILES)

# The languages above in a single run. A developer runs this before the gate.
reformat: reformat-c reformat-md reformat-py reformat-cmake reformat-sh

# The tools a C build needs. The check names a missing tool rather than letting a recipe fail deep inside.
check-build-deps:
	@sh scripts/check-deps.sh $(BUILD_DEP_TOOLS)
check-dev-deps: check-build-deps
	@sh scripts/check-deps.sh $(DEV_DEP_TOOLS)

# Install the tools a goal needs. The script detects the OS. `make install-deps` gets the C build deps. That is what
# `make all`, `make test` and `make install` want. `make install-deps-<goal>` adds a sub-gate's tools. The goals are
# `pc`, `vet`, `verify` and `gh-pages`.
install-deps:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-build-deps.sh
# The tools a named sub-gate adds on top. The stem is the goal, and the dev-deps script reads it.
install-deps-%:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-dev-deps.sh $*

# --- pre-commit gate. It builds first. Then test and verify. Then vet and publish.
#
# The sub-gates map to CI workflows and badges. Those sub-gates are `test`, `verify`, `vet` and `gh-pages`. A sub-gate
# also builds what it needs. CI does not run pc itself. ---
pc: all test verify vet gh-pages

# Throw away what a build and a gate wrote. The stamps go with them, and the next run re-checks from cold.
clean:
	rm -rf build build-debug build-release build-coverage build-pkgtest build-docs build-gh-pages .stamps
