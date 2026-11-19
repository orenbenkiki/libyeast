# Make builds and checks libyeast.
#
# A check is a *stamp file* target. The target depends on the sources and configs. The recipe touches the stamp after
# the check succeeds. A [failed command](https://www.gnu.org/software/make/manual/html_node/Errors.html) aborts the
# recipe before the touch. A stamp does not record a false success. `make <target>` over a stamp with nothing changed
# runs no recipe. https://www.gnu.org/software/make/manual/html_node/Rule-Syntax.html says when a target is out of date.
#
# A `vet-format-*` part sits beside the stamp rule that does the part's work. A `test-*` part sits in the goal tree.

# The format gate accepts a build of a single major version of clang-format.
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
# The C linter. The Makefile looks on PATH and then in the Homebrew llvm prefix.
CLANG_TIDY ?= $(shell command -v clang-tidy 2>/dev/null \
                || command -v "$$(brew --prefix llvm 2>/dev/null)/bin/clang-tidy" 2>/dev/null \
                || echo clang-tidy)
# The C analyzer that runs beside clang-tidy.
CPPCHECK ?= cppcheck

# The tool that turns the coverage data into a report. `scripts/coverage_badge.py` builds the badge from the summary
# that tool writes.
GCOVR ?= gcovr

# The markdown formatter.
MDFORMAT ?= mdformat

# The Python formatter.
BLACK ?= black

# The [tool](https://github.com/jsh9/format-docstring) that reflows a Python docstring.
FORMAT_DOCSTRING ?= format-docstring

# The CMake formatter.
GERSEMI ?= gersemi

# The shell formatter.
SHFMT ?= shfmt

# The Python linter. Ruff checks line length and unused imports. Ruff faults a text `open` that names no encoding. Ruff
# faults a docstring with a backslash unless the docstring is raw.
RUFF ?= ruff

# The Python linter that `vet-pylint` runs.
PYLINT ?= pylint

# The Python type checker. It checks the annotations.
MYPY ?= mypy

# The coverage and leak tools differ by platform. On Darwin the gcov reader is `xcrun llvm-cov gcov`. On any other
# platform the reader is gcov.
#
# Leak detection differs too. `CMakeLists.txt` builds the Debug build with ASan. Off Darwin the Debug build's tests run
# with ASan's leak detection on. On Darwin the gate runs the Release build's `test_c` with `--no-exec` through the
# `leaks` tool.
ifeq ($(shell uname -s),Darwin)
GCOV_EXE      ?= xcrun llvm-cov gcov
# The clang-tidy arguments that set `-isysroot` to the macOS SDK path. Off Darwin the value is empty.
TIDY_EXTRA := --extra-arg=-isysroot --extra-arg=$(shell xcrun --show-sdk-path)
# On Darwin the variable stays empty. Elsewhere it holds `ASAN_OPTIONS=detect_leaks=1`. The Darwin leak check runs
# outside the test binary.
ASAN_TEST_ENV :=
LEAK_CHECK    := MallocStackLogging=1 leaks --atExit --
else
GCOV_EXE      ?= gcov
TIDY_EXTRA :=
ASAN_TEST_ENV := ASAN_OPTIONS=detect_leaks=1
# Off Darwin `LEAK_CHECK` holds `:`.
LEAK_CHECK := :
endif

# `DEPS_OS` selects the install-deps script that `make install-deps*` runs. The `Makefile` detects the OS. A Linux
# without apt is neither Debian nor Ubuntu and has no script. `DEPS_OS` then stays empty, and `DEPS_GUARD` fails the
# install-deps recipe with a message naming the tools to install by hand.
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

# Input sets.
LIB_SRC   := $(wildcard src/*.c)
# The published API. `CMakeLists.txt` installs `include/yeast.h`.
PUB_HDR := $(wildcard include/*.h)
# The internal headers. The install in `CMakeLists.txt` leaves these behind.
PRIV_HDR := $(wildcard src/*.h)
# The tests over the public surface.
UNIT_TEST := tests/test_c.c
# The consumer that builds against the installed package.
PKG_SRC := tests/pkg_consumer.c
# The vendored test framework.
VENDOR_H := $(wildcard third_party/acutest/*.h)
# The inputs that configure the build.
CMAKE_IN := CMakeLists.txt $(wildcard cmake/*.in)
# The version that a shell command reads out of CMakeLists project().
VERSION   := $(shell sed -n 's/^project.yeast VERSION \([0-9][0-9.]*\).*/\1/p' CMakeLists.txt)
# The sources the format check and the comment check scan. The lint check depends on those sources as well.
ALL_SRC := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c)
# `FIND_PRUNE` holds the find prune that the file lists below reuse. The prune skips `.git` and vendored `third_party`.
# The prune skips build dirs and untracked junk* scratch. The prune also skips the agent definitions in
# `.claude/agents`. `.claude/agents/reader.md` is one agent definition.
FIND_PRUNE := -name .git -prune -o -name third_party -prune -o -path './build*' -prune -o -path './junk*' -prune -o \
              -path './.claude/agents*' -prune -o
# The files the markdown formatter reads.
MD_FILES := $(shell find . $(FIND_PRUNE) -name '*.md' -print)
# The `.py` files of the tree, outside the directories `FIND_PRUNE` skips.
PY_FILES := $(shell find . $(FIND_PRUNE) -name '*.py' -print)
# The files the CMake formatter reads.
CMAKE_FILES := CMakeLists.txt $(shell find . $(FIND_PRUNE) -name '*.cmake' -print)
# The files the shell formatter reads.
SH_FILES := $(shell find . $(FIND_PRUNE) -name '*.sh' -print)
# The JavaScript files. `wrap_long_comments` reads their comments.
JS_FILES := $(shell find . $(FIND_PRUNE) -name '*.js' -print)
# The scaffolding marker. A subst call builds the marker. This Makefile holds no literal copy.
TODO_X    := $(subst -,,todo-x)
# `git ls-files` lists the tracked files, as https://git-scm.com/docs/git-ls-files says. A gate reading those files
# rebuilds whenever a tracked file changes.
SCAN_FILES := $(shell git ls-files)
# The files a build reads.
BUILD_DEPS := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(VENDOR_H) $(CMAKE_IN)
# clang-tidy lints what `CMakeLists.txt` builds in build-debug. Those are the library sources and the tests less the
# packaging consumer. The consumer builds against the installed library and stays out of that database.
LINT_FILES := $(LIB_SRC) $(filter-out $(PKG_SRC),$(wildcard tests/*.c))
# The directory the package test installs to. The package test deletes this directory before it installs.
PKG_PREFIX := $(CURDIR)/build-pkgtest/prefix
# The prefix `make install` hands to `cmake --install`.
PREFIX ?= /usr/local
# The vendored official grammar. https://github.com/yaml/yaml-grammar is its source.
GRAMMAR_SPEC := third_party/yaml-grammar/yaml-spec-1.2.yaml
# libyeast's grammar. `annotated2ir.load` reads the same file through `DEFAULT_GRAMMAR` where its caller names no
# `source`.
ANNOTATED := grammar/yeast-spec-1.2.yaml
# The table the grammar's cuts and errors report from.
MESSAGES := grammar/messages.yaml
# The generator and its gates.
GEN_SRC := $(wildcard generator/*.py)
# The helpers the build and the gates call. `vet-comments` runs `scripts/check_comments.py`. `vet-format-py` runs
# `scripts/wrap_long_comments.py`.
SCRIPT_SRC := $(wildcard scripts/*.py)
# The agent definitions. A definition that judges prose holds copied text below `collect_fragments.COPIED_BELOW`. The
# copy `write_agent_prompts` writes in `prose-claims.md` holds the answer shape and no conventions.
AGENT_SRC := $(wildcard .claude/agents/*.md)
# The list names the documents the stamp rule of `verify-documents` takes as prerequisites. Through
# `check_documents._check`, that gate checks its own declarations against the citations in the documents and in the
# prose beside the code. `check_documents._check` prints the counts the code measures.
DOCUMENTS := DESIGN.md PLAN.md CHANGELOG.md
# The conformance corpus. A case holds an input and the tokens it expects.
FIXTURES := $(wildcard tests/spec/*.input tests/spec/*.output)
# The YAML Test Suite, and the `test.event` files under `tests/star/`. A case holds an input, the events it expects, and
# an error marker.
STAR_DATA  := $(wildcard third_party/yaml-test-suite/*/in.yaml third_party/yaml-test-suite/*/*/in.yaml \
                         third_party/yaml-test-suite/*/test.event third_party/yaml-test-suite/*/*/test.event \
                         third_party/yaml-test-suite/*/error third_party/yaml-test-suite/*/*/error \
                         tests/star/*/test.event tests/star/*/*/test.event)

# The tool dependencies that check-build-deps and check-dev-deps verify. `DEV_DEP_TOOLS` holds Python rather than this
# list.
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
        verify-hooks verify-fragments verify-prose verify-claims verify-ascii examine-prose examine-claims \
        verify-wire verify-decoder verify-spaces \
        verify-grammar-base verify-grammar-base-coverage \
        vet vet-format vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-js \
        vet-format-make \
        vet-comments vet-lint vet-pylint vet-mypy vet-version vet-packaging vet-$(TODO_X) \
        gh-pages gh-pages-docs gh-pages-coverage \
        reformat reformat-c reformat-md reformat-py reformat-cmake reformat-sh reformat-js reformat-make \
        check-build-deps check-dev-deps install-deps pc clean

# The default goal. GNU Make takes the first target as the default goal, as
# https://www.gnu.org/software/make/manual/html_node/Goals.html says. Through `package` and the stamp rules of
# `build/.cfg`, it builds the shared and static C libraries that `CMakeLists.txt` declares.
all: package

# The directory a stamp lands in. A stamp rule takes the directory as an order-only prerequisite.
.stamps:
	@mkdir -p .stamps

# `all` builds this rule by default. It builds the shared and static libraries `CMakeLists.txt` declares. It builds no
# tests.
build/.cfg: $(CMAKE_IN)
	cmake -S . -B build
	@touch $@
build/.package: build/.cfg $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR)
	cmake --build build --target yeast yeast_static
	@touch $@

# The shared and static libraries. The build reads the committed sources and runs no generator step.
package: build/.package

# --- Debug config. `CMakeLists.txt` adds sanitizers to the Debug config. ---
build-debug/.cfg: $(CMAKE_IN)
	cmake -S . -B build-debug -DCMAKE_BUILD_TYPE=Debug
	@touch $@
build-debug/.build: build-debug/.cfg $(BUILD_DEPS)
	cmake --build build-debug
	@touch $@
# Off Darwin `ASAN_TEST_ENV` turns ASan's leak detection on, as
# https://github.com/google/sanitizers/wiki/AddressSanitizerLeakSanitizer says. On Darwin `ASAN_TEST_ENV` is empty, and
# the Release run leak-checks instead.
#
# The fixtures are an input to the tests as well as to the generator gates. `test_emitter_reconstructs_fixtures` replays
# a fixture's tokens and requires the bytes back. Without the fixtures among this stamp's prerequisites, a new fixture
# leaves the stamp untouched. A fixture whose tokens do not span its input stays green until another prerequisite
# changes and the stamp rebuilds.
build-debug/.test: build-debug/.build $(FIXTURES)
	$(ASAN_TEST_ENV) ctest --test-dir build-debug --output-on-failure
	@touch $@

# --- Release config. `CMakeLists.txt` hardens this config. ---
build-release/.cfg: $(CMAKE_IN)
	cmake -S . -B build-release -DCMAKE_BUILD_TYPE=Release
	@touch $@
build-release/.build: build-release/.cfg $(BUILD_DEPS)
	cmake --build build-release
	@touch $@
# ctest runs the tests for correctness. On macOS the `LEAK_CHECK` line runs the Release binary through the `leaks` tool.
# That run passes `--no-exec`. Off Darwin `LEAK_CHECK` is a no-op, and the Debug run checks for leaks.
build-release/.test: build-release/.build $(FIXTURES)
	ctest --test-dir build-release --output-on-failure
	$(LEAK_CHECK) build-release/test_c --no-exec
	@touch $@

# --- Coverage ---
#
# Configure the coverage build. Instrument, run, and REPORT. The coverage build gates nothing.
# `.stamps/gh-pages-coverage` is the gate. The target `gh-pages-coverage` depends on that stamp. `gh-pages` lists
# `gh-pages-coverage` as a prerequisite. The recipe emits the machine-readable reports, the human HTML report and the
# shields summary. `gh-pages` copies the HTML report and the summary into `build-gh-pages`. ---
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

# The version the clang-format binary named by `CLANG_FORMAT` reports, or `none`. The failure message of
# `CLANG_FORMAT_CHECK` prints it. Make runs this command whenever it expands that guard.
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
# `wrap_long_comments --check` fails on a comment block that wants a reflow.
#
# The target also runs format-docstring on throwaway copies. The target fails where format-docstring exits nonzero.
# `ruff` rule D301 refuses a docstring holding a backslash unless the docstring is raw, as
# https://docs.astral.sh/ruff/rules/escape-sequence-in-docstring/ says.
.stamps/vet-format-py: $(PY_FILES) | .stamps
	$(BLACK) --check --line-length 120 $(PY_FILES)
	@tmp=$$(mktemp -d); files=""; \
	  for f in $(PY_FILES); do mkdir -p "$$tmp/$$(dirname $$f)"; cp "$$f" "$$tmp/$$f"; files="$$files $$tmp/$$f"; done; \
	  $(FORMAT_DOCSTRING) --line-length 120 --fix-rst-backticks False $$files >/dev/null 2>&1; rc=$$?; \
	  rm -rf "$$tmp"; test $$rc -eq 0 || { echo "docstrings need formatting - run make reformat-py"; exit 1; }
	python3 scripts/wrap_long_comments.py --check $(PY_FILES)
	$(RUFF) check --quiet --preview --select D301,E501,F401,PLW1514 --line-length 120 $(PY_FILES)
	@touch $@

vet-format-py: .stamps/vet-format-py

# The target vets the format of `CMakeLists.txt` and of the `*.cmake` files that the find in `CMAKE_FILES` reaches.
.stamps/vet-format-cmake: $(CMAKE_FILES) | .stamps
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --check $(CMAKE_FILES)
	@touch $@

vet-format-cmake: .stamps/vet-format-cmake

# `vet-format-sh` holds a shell script to a `4`-space indent. The `-i` flag of `shfmt` sets that indent, as
# https://github.com/mvdan/sh/blob/master/cmd/shfmt/shfmt.1.scd says. `wrap_long_comments` checks a script's comment
# blocks against a reflow to the column limit.
.stamps/vet-format-sh: $(SH_FILES) scripts/wrap_long_comments.py | .stamps
	$(SHFMT) -i 4 -d $(SH_FILES)
	python3 scripts/wrap_long_comments.py --check $(SH_FILES)
	@touch $@

vet-format-sh: .stamps/vet-format-sh

# `wrap_long_comments` checks the comment blocks of a JavaScript file against a reflow to the column limit.
.stamps/vet-format-js: $(JS_FILES) scripts/wrap_long_comments.py | .stamps
	python3 scripts/wrap_long_comments.py --check $(JS_FILES)
	@touch $@

vet-format-js: .stamps/vet-format-js
# The other languages here have a formatter. This check holds the `Makefile` to a limit of `120` columns. The count is
# in characters rather than bytes.
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

# `vet-pylint` lints the Python. `ruff` runs the rules `vet-format-py` selects. `.pylintrc` says which `pylint` checks
# this tree keeps.
.stamps/vet-pylint: $(PY_FILES) .pylintrc | .stamps
	$(PYLINT) $(PY_FILES)
	@touch $@

vet-pylint: .stamps/vet-pylint

# The Python types. A definition in a module needs an annotation. `mypy.ini` declares the modules exempt from that need.
#
# The check prints the excused modules after mypy passes. A green run over a tree of excused modules reads as a typed
# tree. The check does not count `conanfile`. `mypy.ini` excuses `conanfile` for good.
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

# The C lint runs a pair of analyzers over the library and the tests. clang-tidy reads the compile database in
# `build-debug`. `CMakeLists.txt` tells cmake to write that database at configure. The lint runs after
# `build-debug/.build`.
.stamps/vet-lint: $(ALL_SRC) .clang-tidy build-debug/.build | .stamps
	$(CLANG_TIDY) --quiet --extra-arg=-Wno-unknown-warning-option $(TIDY_EXTRA) -p build-debug $(LINT_FILES)
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --std=c99 \
	    --suppress=missingIncludeSystem --suppress='*:*third_party*' \
	    -I include -I third_party/acutest src tests
	@touch $@

vet-lint: .stamps/vet-lint

# The leftover-marker scan fails if the marker appears in a tracked file's name or in the content of a tracked text
# file. The scan ignores case. This Makefile writes the marker as `$(TODO_X)` rather than literally.
.stamps/vet-$(TODO_X): $(SCAN_FILES) | .stamps
	@if git ls-files | grep -in '$(TODO_X)'; then \
	    echo "marker found in a file name - rename it"; exit 1; fi
	@if git ls-files -z | xargs -0 grep -HIni '$(TODO_X)' 2>/dev/null; then \
	    echo "marker found in file content - remove before completion"; exit 1; fi
	@echo "no leftover markers"
	@touch $@

# --- API docs (Doxygen) ---
#
# Generates HTML into `build-docs/html`. `.github/workflows/gh-pages.yml` publishes that. The target also enforces
# completeness. `WARN_AS_ERROR` in the Doxyfile fails on an undocumented public symbol. The Doxyfile's
# [`WARN_NO_PARAMDOC`](https://www.doxygen.nl/manual/config.html) fails a missing @param or @return too. The target is a
# deliverable and a gate. The `pc` target reaches this target through `gh-pages`. The `gh-pages-docs` target builds this
# target too.
build-docs/.docs: $(PUB_HDR) Doxyfile DoxygenLayout.xml CMakeLists.txt
	YEAST_VERSION="$(VERSION)" doxygen Doxyfile
	@touch $@

# Guards against version drift. CMakeLists project() is the source of truth. The vcpkg port must match it. The recipe in
# `conanfile.py` derives its version from CMakeLists and cannot drift.
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

# The gate validates the grammar. A reference resolves to a production with a matching arity. A production outside
# `_UNREFERENCED` has a reference.
.stamps/verify-references: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/validate_grammar.py
	@touch $@

verify-references: .stamps/verify-references

# `official` erases libyeast's token annotations from `grammar/yeast-spec-1.2.yaml`. `_erased_call` writes a reference
# to an indicator production back as the character it names. The remainder must be the vendored grammar, save the
# productions `check_vendor_spec._DEVIATIONS` declares. An edit meant to add a token annotation must not change an
# official production.
.stamps/verify-spec: $(GRAMMAR_SPEC) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@

verify-spec: .stamps/verify-spec

# Marker balance. A begin- marker closes on a matching end- marker. The branches of a rule agree on their markers. The
# stream leaves no marker open.
.stamps/verify-markers: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_markers.py
	@touch $@

verify-markers: .stamps/verify-markers

# Grammar documentation. A rule that emits tokens holds a note. That note names the tokens without repeats, in the order
# the rule first emits them. The gate checks the note against the grammar itself. A wrong note fails like a missing one.
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
# to a production the grammar still has. An output is a token stream whose marks chain.
.stamps/verify-fixtures: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_spec_tests.py
	@touch $@

verify-fixtures: .stamps/verify-fixtures

# libyeast's token stream, folded up to the community YAML Test Suite's events. The spec is the reference libyeast
# follows. libyeast must fold a case to its events, or refuse the case where the suite says reject. The exceptions are
# the declared divergences. The spec and the suite disagree there, and the spec wins.
.stamps/verify-star: $(STAR_DATA) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_star.py
	@touch $@

verify-star: .stamps/verify-star

# The normalization pipeline preserves meaning step after step. A fixture still reproduces token for token on the last
# stage that can run it. A suite case still folds to the events `check_star.disagreements` expects of it. A declared
# divergence expects its declared events. The gate rejects a step that changes token identity or event identity. The
# gate names that step only under `--bisect`, and this rule does not pass it.
#
# The gate also holds the pipeline to its own law. `normalize.invariant_faults` reads that law. A count does not rise. A
# settling step drives a count to nothing, and the count stays there. A step declaring a lapse may break either. The
# gate holds the spaces it computes to the places a parse really reached.
.stamps/verify-normalize: $(FIXTURES) $(STAR_DATA) $(MESSAGES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_normalize.py
	@touch $@

verify-normalize: .stamps/verify-normalize

# The documents and the prose beside the code cite names. The gate holds the declarations of `check_documents` to those
# citations. The checkers ask the rules of a text, and `check_prose` runs them over the tree. The gate prints the counts
# the code measures.
#
# The gate measures against the pipeline the documents describe. The gate reads the grammar and the steps. The gate
# reads the fixtures too. Through `no_tree_numbers`, `check_prose` refuses a fixture count written into a document.
#
# A C source is a prerequisite twice over. The gate resolves a citation against the sources that can hold a name. The
# same rules cover the prose beside the C and the prose beside the generator.
#
# The git index is a prerequisite as well. A citation may name a file. The tree holds what `git` tracks, as
# `_file_names` lists it. Staging an addition or a removal changes the answer as much as editing a source does.
#
# This gate wants a git working tree. `_file_names` says so where the checkout has no git working tree. A
# [wildcard](https://www.gnu.org/software/make/manual/html_node/Wildcard-Function.html) names the index only where the
# index exists. The wildcard does not let the gate run without a tree.
.stamps/verify-documents: $(DOCUMENTS) $(ANNOTATED) $(MESSAGES) $(FIXTURES) $(GEN_SRC) $(SCRIPT_SRC) \
                          $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(CMAKE_IN) Makefile \
                          generator/cited_names.json $(wildcard .git/index) | .stamps
	python3 generator/check_documents.py
	@touch $@

verify-documents: .stamps/verify-documents

# generator/ and scripts/ hold no unreachable code. A run or an import reaches a module. A reference keeps a top-level
# function, a class and a constant alive. The walk starts at the code at import, at `main`, and at the names a hook or
# an MCP server reads. A construction at import or in a reached definition keeps an `ir` kind alive. `_KEPT_THOUGH_DEAD`
# and `_RUN_FROM_ELSEWHERE` declare the exceptions with a reason, and the gate holds the reasons too. This file runs a
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

# A prose agent definition holds a copied answer schema. The critic, compare and condense definitions hold the
# conventions as well. `write_agent_prompts.py` writes that copy. This gate refuses a copy the sources have moved past.
.stamps/verify-agent-prompts: .claude/conventions.md .claude/rejected.md $(AGENT_SRC) $(GEN_SRC) | .stamps
	python3 generator/write_agent_prompts.py --check
	@touch $@

verify-agent-prompts: .stamps/verify-agent-prompts

# The gate runs the hooks against an edit a hook must answer and an edit a hook must pass. A hook that has gone blind is
# silent. A hook that passes a clean edit is silent too. `tests/hooks.json` holds the pairs, and `.claude/settings.json`
# names the hooks.
.stamps/verify-hooks: tests/hooks.json .claude/settings.json $(wildcard .claude/hooks/*) $(GEN_SRC) | .stamps
	python3 generator/check_hooks.py
	@touch $@

verify-hooks: .stamps/verify-hooks

# The gate hunts an ignored failure. The gate reads the shell scripts and the recipes in this file. The gate reads the
# review workflows and the Python.
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

# The directory where `generator/converge_prose.py` keeps the settling queues.
PROSE_DIR ?= .git/critic

# The gate breaks the project into fragments. A `Fragment` holds prose and a key of its own.
# `collect_fragments.fragments` reads the files of the roster `prose_files` that
# `check_documents.is_prose_of_the_project` accepts. The roster draws on `tracked_files`, and a scan of the tracked
# files covers the roster. `update_ledger_and_queue` keeps the stores keyed by digest in step with the fragments, and
# drops an entry the tree moved past. The queues sit under `PROSE_DIR`.
.stamps/verify-fragments: $(SCAN_FILES) $(wildcard $(PROSE_DIR)/prose-queue.jsonl $(PROSE_DIR)/unsettled-prose.jsonl) \
		| .stamps
	python3 generator/collect_fragments.py
	python3 generator/update_ledger_and_queue.py $(PROSE_DIR)
	@touch $@

verify-fragments: .stamps/verify-fragments

# The claim store.
CLAIM_STORE := .claude/prose-dependencies.json

# The gate reads the claim store against the tree. The store says what a claim pass read, and the digests it read at. A
# fragment whose own prose or code moved falls stale. A fragment that read a fragment whose code moved falls stale too.
# A stale fragment fails this gate. A fragment the claim pass gave up on fails it too. The target `examine-claims` runs
# `generator/converge_claims.py`, and that run reads a stale fragment. It reads a fragment the pass gave up on where
# that fragment's own prose or code moved. It also reads such a fragment where the file `CLAIM_KEYS` names holds that
# fragment's key.
.stamps/verify-claims: $(SCAN_FILES) $(wildcard $(CLAIM_STORE)) .stamps/verify-fragments | .stamps
	python3 generator/claim_state.py
	@touch $@

verify-claims: .stamps/verify-claims

# The gate reads the prose already in the tree. The gate holds that prose to the rules the write-time hooks apply to an
# edit. The `prose_gate` hook refuses a sentence as the writer types it. Outside a markdown file, `refusals_for_edit`
# hands the prose checkers only the fragments and literals the edit touches. The gate also reads the unsettled prose
# queue, and a fragment the settling loop gave up on is a fault. A fragment waiting in the prose queue is a fault too.
.stamps/verify-prose: $(SCAN_FILES) $(wildcard $(PROSE_DIR)/unsettled-prose.jsonl $(PROSE_DIR)/prose-queue.jsonl) \
		.stamps/verify-fragments | .stamps
	python3 generator/check_prose.py $(PROSE_DIR)
	@touch $@

verify-prose: .stamps/verify-prose

# Caps the unexamined fragments a run of `converge_prose.main` takes. An empty value takes the whole queue.
PROSE_COUNT ?=

# An agent that judges prose runs the model this variable names. An empty value leaves the default in place.
PROSE_MODEL ?=

# Runs the settling pass. `converge_prose.main` spends agent calls, and no gate runs this target. `verify-prose` runs
# `generator/check_prose.py`, and that script fails while a fragment waits in the prose queue. `converge_prose.main`
# calls `updated`. `updated` keeps the run's state in the `.claude` ledger that `LEDGER` names and in the queues under
# `PROSE_DIR`. A later run of this target picks up that state. `converge_prose.main` exits with the status `RESUMABLE`
# where a later run can continue.
examine-prose:
	python3 generator/converge_prose.py $(PROSE_DIR) $(PROSE_COUNT) $(if $(PROSE_MODEL),--model $(PROSE_MODEL),)

# The most fragments a claims run reads for the first time. A fragment read again in a later round does not count again.
# An empty value sets the cap to the count of fragments the run may read. `converge_claims.main` counts the keys
# `--only` and `--again` name, or else the fragments of the tree.
CLAIM_COUNT ?=

# The path of a file. That file holds a key per line, and `generator/converge_claims.py` reads the stale fragments among
# those keys. An empty value opens the run to the whole stale set. A key the file names comes back into the run where
# the store calls it unsettled.
CLAIM_KEYS ?=

# The path of a file in the form `CLAIM_KEYS` takes. `generator/converge_claims.py` reads the keys in that file again,
# whether the store calls a key settled or not.
CLAIM_AGAIN ?=

# The claim pass between the prose and the code it documents. The pass spends agent calls, and no gate depends on it.
# `converge_claims` keeps its state in the claim store, and a later run of this target picks up that state through
# `read_claims`. `converge_claims` exits with `RESUMABLE` where a later run can continue, and with `FAILED` where a
# person investigates.
examine-claims:
	python3 generator/converge_claims.py $(PROSE_DIR) $(CLAIM_COUNT) $(if $(PROSE_MODEL),--model $(PROSE_MODEL),) \
		$(if $(CLAIM_KEYS),--only $(CLAIM_KEYS),) $(if $(CLAIM_AGAIN),--again $(CLAIM_AGAIN),)

# A tracked file holds ASCII. A character past ASCII looks like the ASCII character it replaces. Such a character also
# makes a column count over a line disagree with the editor. `check_ascii` declares the paths whose content may hold
# such a character.
.stamps/verify-ascii: $(SCAN_FILES) generator/check_ascii.py | .stamps
	python3 generator/check_ascii.py
	@touch $@

verify-ascii: .stamps/verify-ascii

# Wire code map. The character-per-code table in `wire.py` must match the table in `src/wire.c`. The interpreter cannot
# write a code the C parser would write differently. `interpreter.Emitter` writes a code through `wire.CODE_CHAR` and
# `wire.ERROR`.
.stamps/verify-wire: src/wire.c $(GEN_SRC) | .stamps
	python3 generator/check_wire.py
	@touch $@

verify-wire: .stamps/verify-wire

# A rewind undoes the emitter's work. An alternation in `_try_alternation` rewinds to a single checkpoint once per
# failed branch. The gate checks that the second rewind restores the fields the first rewind restored.
.stamps/verify-emitter: $(GEN_SRC) | .stamps
	python3 generator/check_emitter.py
	@touch $@

verify-emitter: .stamps/verify-emitter

# A `SubSpace` names a subset of the states a parse can decide in. The gate lists the states an operation holds. The
# list covers an alphabet and the answers a parse reaches at a guard. The gate checks union, intersection and difference
# with set arithmetic over that list. The gate also checks containment and emptiness against that list.
.stamps/verify-spaces: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_spaces.py
	@touch $@

verify-spaces: .stamps/verify-spaces

# Error messages. A `(cut)` in the grammar names a message defined in `grammar/messages.yaml`.
# `generator/check_messages.py` fails the build where the grammar names a code the table lacks. It also fails where the
# table holds a message no site in the grammar names.
.stamps/verify-messages: $(ANNOTATED) $(MESSAGES) $(GEN_SRC) | .stamps
	python3 generator/check_messages.py
	@touch $@

verify-messages: .stamps/verify-messages

# The interpreter reproduces the fixtures `spec_tests.load` finds. The interpreter runs a fixture's production as the
# grammar describes it. The interpreter's token stream must equal the fixture's stream byte for byte. A fixture the
# interpreter crashes on fails the gate.
.stamps/verify-grammar-base: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_interpreter.py
	@touch $@

verify-grammar-base: .stamps/verify-grammar-base

# The fixtures exercise the productions the grammar has. The gate runs the fixtures that `spec_tests.load` finds in the
# tests directory. Running a fixture reaches the productions its run calls. A production that no fixture reaches is a
# coverage gap. This target checks the base grammar that `annotated2ir.load` returns.
.stamps/verify-grammar-base-coverage: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_coverage.py
	@touch $@

verify-grammar-base-coverage: .stamps/verify-grammar-base-coverage

# The `regen` target rewrites the committed generated files. The files are `src/decoder_tables.h`, the agent definitions
# that judge prose, and `generator/cited_names.json`. `CITED_NAMES` holds the path `generator/cited_names.json`.
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
# the developer gate. The `pc` target runs `all`, `test` and `verify`. The `pc` target adds `vet` and `gh-pages`. ---

# The consumer mode. Pure C, with no Python and no staleness checks.
install: build-release/.build
	cmake --install build-release --prefix "$(PREFIX)"
# the sanitized build, through `build-debug/.test`. `CMakeLists.txt` adds the sanitizers to the Debug config only. They
# catch what a release build hides.
test-debug: build-debug/.test
test-release: build-release/.test  # the Release build.
test: test-debug test-release

# `verify-grammar` runs `verify-grammar-base` and `verify-grammar-base-coverage`.
#
# `check_interpreter` runs the base grammar and reproduces the fixtures a production at a time. `check_grammar_coverage`
# checks that the fixtures exercise the base grammar throughout.
verify-grammar: verify-grammar-base verify-grammar-base-coverage
# The prerequisites name the verify gates this target runs. The list opens with `verify-roundtrip` and closes with
# `verify-spaces`.
verify: verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star \
        verify-normalize verify-documents verify-dead-code verify-proposals verify-failures verify-conventions \
        verify-agent-prompts \
        verify-hooks verify-fragments verify-prose verify-claims verify-ascii \
        verify-wire verify-decoder verify-spaces

# Check the format of the sources.
vet-format: vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh vet-format-js vet-format-make

# `vet-packaging` tests the packaging through `build-pkgtest/.pkg` and keeps its stamp under `build-pkgtest` rather than
# under `.stamps`.
vet-packaging: build-pkgtest/.pkg

# The leftover-marker scan. This Makefile names the target through a substitution. That substitution keeps the marker
# out of the file.
vet-$(TODO_X): .stamps/vet-$(TODO_X)

# The static quality mode. Vet runs the formatters in check mode, and it runs the linters. Vet also checks the C
# comments, the leftover markers and the version. Vet ends with the packaging test.
vet: vet-format vet-comments vet-lint vet-pylint vet-mypy vet-$(TODO_X) vet-version vet-packaging

# The Doxygen API docs, the part of the GitHub Pages payload that `build-docs/.docs` builds.
gh-pages-docs: build-docs/.docs

# The tree `.github/workflows/gh-pages.yml` publishes. The docs and the coverage report land there together.
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
# The Python. The recipe takes an exit of `1` from `format-docstring` as success. `black` runs again last, over the code
# `wrap_long_comments` left.
reformat-py:
	$(BLACK) --line-length 120 $(PY_FILES)
	$(FORMAT_DOCSTRING) --line-length 120 --fix-rst-backticks False $(PY_FILES) || [ $$? -eq 1 ]
	python3 scripts/wrap_long_comments.py --apply $(PY_FILES)
	$(BLACK) --line-length 120 $(PY_FILES)
# The CMake, rewritten in place.
reformat-cmake:
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --in-place $(CMAKE_FILES)

# Format the shell scripts. `shfmt -w` rewrites them in place, as
# https://github.com/mvdan/sh/blob/master/cmd/shfmt/shfmt.1.scd says. `wrap_long_comments` then wraps their comment
# blocks.
reformat-sh:
	$(SHFMT) -i 4 -w $(SH_FILES)
	python3 scripts/wrap_long_comments.py --apply $(SH_FILES)

# The comments of the JavaScript files. `wrap_long_comments` wraps a comment block at the column limit.
reformat-js:
	python3 scripts/wrap_long_comments.py --apply $(JS_FILES)

# The `Makefile`'s own comments. `wrap_long_comments` wraps a comment block here at the column limit. That script wraps
# a comment block in a Python file the same way.
reformat-make:
	python3 scripts/wrap_long_comments.py --apply Makefile

# Reformats the languages above in a single run.
reformat: reformat-c reformat-md reformat-py reformat-cmake reformat-sh reformat-js reformat-make

# check-build-deps checks the tools a C build needs. check-dev-deps checks those and then the tools a developer needs.
# Both targets run `check-deps.sh`. That script prints a missing tool by name.
check-build-deps:
	@bash scripts/check-deps.sh $(BUILD_DEP_TOOLS)
check-dev-deps: check-build-deps
	@bash scripts/check-deps.sh $(DEV_DEP_TOOLS)

# Install the tools a goal needs. The `Makefile` detects the OS in `DEPS_OS`. `make install-deps` runs a script such as
# `install-debian-build-deps.sh`. The script installs the tools that `make all`, `make test` and `make install` need.
# `make install-deps-pc` runs a script such as `install-debian-dev-deps.sh` with the goal `pc`. That adds the tools of
# the `pc` gate. `vet`, `verify` and `gh-pages` take the same form.
install-deps:
	@$(DEPS_GUARD)
	bash scripts/install-$(DEPS_OS)-build-deps.sh
# The tools a named sub-gate adds on top. The stem is the goal. The dev-deps script reads the stem.
install-deps-%:
	@$(DEPS_GUARD)
	bash scripts/install-$(DEPS_OS)-dev-deps.sh $*

# --- The `pc` target is the pre-commit gate. It builds first. It then runs `test` and `verify`. It then runs `vet` and
# `gh-pages`.
#
# `.github/workflows/test.yml` runs `test`, and `.github/workflows/verify.yml` runs `verify`.
# `.github/workflows/vet.yml` runs `vet`, and `.github/workflows/gh-pages.yml` runs `gh-pages`. ---
pc: all test verify vet gh-pages

# Throw away the build directories. The stamps go too. The next run re-checks from cold.
clean:
	rm -rf build build-debug build-release build-coverage build-pkgtest build-docs build-gh-pages .stamps
