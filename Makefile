# libyeast build/verify orchestration.
#
# Every check is a *stamp file* target that depends on its real inputs (sources, configs) and is `touch`ed only after
# the check succeeds. A failed command aborts the recipe before the touch, so a stamp never records a false success, and
# `make <target>` with nothing changed does nothing at all. The heavy lifting (compilation) is delegated to CMake, which
# is itself incremental.

# clang-format's LLVM style shifts between major versions (UTF-8 column width, trailing-comment alignment) but is stable
# within one — 22.1.5 and 22.1.8 agree; 18 and 22 do not — so the gate accepts any build of one major. That major is the
# single source of truth in .clang-format-version, read here for the gate and by the dev-deps scripts for the wheel they
# install. CLANG_FORMAT resolves to that wheel's binary, and the format targets verify the major before trusting it; a
# developer with any build of it on PATH works without the wheel, and any other major fails the gate with instructions.
CLANG_FORMAT_MAJOR_VERSION := $(shell cat .clang-format-version)
CLANG_FORMAT ?= $(shell python3 -c "import clang_format, os; \
                  print(os.path.join(os.path.dirname(clang_format.__file__), 'data', 'bin', 'clang-format'))" \
                  2>/dev/null || echo clang-format)
CLANG_TIDY ?= $(shell command -v clang-tidy 2>/dev/null \
                || command -v "$$(brew --prefix llvm 2>/dev/null)/bin/clang-tidy" 2>/dev/null \
                || echo clang-tidy)
CPPCHECK   ?= cppcheck
GCOVR      ?= gcovr
MDFORMAT   ?= mdformat
BLACK      ?= black
GERSEMI    ?= gersemi
SHFMT      ?= shfmt
RUFF       ?= ruff

# Platform split. gcov reader: Apple/Homebrew clang need llvm-cov; GCC uses gcov directly. Leak detection: Linux ASan
# ships LeakSanitizer (enabled explicitly, it runs at each forked test's exit — per-test, isolated); Apple clang has no
# LeakSanitizer, so the Darwin gate runs the Release tests through the `leaks` tool instead (whole-suite).
ifeq ($(shell uname -s),Darwin)
GCOV_EXE      ?= xcrun llvm-cov gcov
TIDY_EXTRA    := --extra-arg=-isysroot --extra-arg=$(shell xcrun --show-sdk-path)
ASAN_TEST_ENV :=
LEAK_CHECK    := MallocStackLogging=1 leaks --atExit --
else
GCOV_EXE      ?= gcov
TIDY_EXTRA    :=
ASAN_TEST_ENV := ASAN_OPTIONS=detect_leaks=1
LEAK_CHECK    := : # LeakSanitizer in the Debug ASan build already leak-checks each test; nothing extra to run
endif

# Which install-deps scripts `make install-deps*` runs, auto-detected so the user never names their OS. A Linux without
# apt is not Debian/Ubuntu and has no script; DEPS_OS is left empty and the install-deps recipe fails with what to do.
ifeq ($(OS),Windows_NT)
DEPS_OS := windows
else ifeq ($(shell uname -s),Darwin)
DEPS_OS := macos
else ifneq ($(shell command -v apt-get 2>/dev/null),)
DEPS_OS := debian
else
DEPS_OS :=
endif
DEPS_GUARD := test -n "$(DEPS_OS)" || { echo "install-deps: no dependency script for this OS — only Debian/Ubuntu \
	(apt), macOS (Homebrew), and Windows are scripted. Install CMake, a C compiler, and pkg-config yourself (see \
	scripts/install-*-build-deps.sh for the list); the rest are formatters, linters, and Python 3 + PyYAML." >&2; exit 1; }

# Input sets (wildcards catch untracked new files too).
LIB_SRC   := $(wildcard src/*.c)
PUB_HDR   := $(wildcard include/*.h)
PRIV_HDR  := $(wildcard src/*.h)
UNIT_TEST := tests/test_c.c
PKG_SRC   := tests/pkg_consumer.c
VENDOR_H  := $(wildcard third_party/acutest/*.h)
CMAKE_IN  := CMakeLists.txt $(wildcard cmake/*.in)
# Version from the single source (CMakeLists project()); `.` matches the literal '(' so make's paren-balancing in
# $(shell ...) stays happy.
VERSION   := $(shell sed -n 's/^project.yeast VERSION \([0-9][0-9.]*\).*/\1/p' CMakeLists.txt)
ALL_SRC   := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c)   # what the format/lint/comment checks scan
# Common find prune (skip .git, vendored third_party, and build dirs), reused below.
FIND_PRUNE := -name .git -prune -o -name third_party -prune -o -path './build*' -prune -o
MD_FILES  := $(shell find . $(FIND_PRUNE) -name '*.md' -print)
PY_FILES  := $(shell find . $(FIND_PRUNE) -name '*.py' -print)
CMAKE_FILES := CMakeLists.txt $(shell find . $(FIND_PRUNE) -name '*.cmake' -print)
SH_FILES  := $(shell find . $(FIND_PRUNE) -name '*.sh' -print)
# Scaffolding marker, spelled via subst so this Makefile never contains it literally.
TODO_X    := $(subst -,,todo-x)
# Files the leftover-marker scan covers: every tracked file except docs/ (which documents the marker).
SCAN_FILES := $(shell git ls-files -- . ':(exclude)docs')
BUILD_DEPS := $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR) $(wildcard tests/*.c) $(VENDOR_H) $(CMAKE_IN)
# clang-tidy needs a compile database, so it lints what build-debug builds: every library source and every test but the
# packaging consumer, which is built against the installed library and so is not in that database.
LINT_FILES := $(LIB_SRC) $(filter-out $(PKG_SRC),$(wildcard tests/*.c))
PKG_PREFIX := $(CURDIR)/build-pkgtest/prefix
PREFIX ?= /usr/local
GRAMMAR_SPEC := third_party/yaml-grammar/yaml-spec-1.2.yaml
ANNOTATED := grammar/yeast-spec-1.2.yaml
MESSAGES := grammar/messages.yaml
GEN_SRC    := $(wildcard generator/*.py)
FIXTURES   := $(wildcard tests/spec/*.input tests/spec/*.output)
STAR_DATA  := $(wildcard third_party/yaml-test-suite/*/in.yaml third_party/yaml-test-suite/*/*/in.yaml \
                         third_party/yaml-test-suite/*/test.event third_party/yaml-test-suite/*/*/test.event \
                         third_party/yaml-test-suite/*/error third_party/yaml-test-suite/*/*/error)

# Tool dependencies, verified by check-build-deps / check-dev-deps. Python is not a C build dep — the build uses the
# committed generated files — so it rides with the dev tools, for the generator that verify and regen run.
BUILD_DEP_TOOLS := cmake $(CC)
DEV_DEP_TOOLS   := python3 python3:yaml $(CLANG_FORMAT) $(CLANG_TIDY) $(CPPCHECK) $(GCOVR) $(MDFORMAT) $(BLACK) $(RUFF) \
                   $(GERSEMI) $(SHFMT) doxygen $(firstword $(GCOV_EXE))

.PHONY: all package install test test-debug test-release regen \
        verify verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star verify-wire verify-decoder \
        verify-grammar-base verify-grammar-base-coverage \
        vet vet-format vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh \
        vet-comments vet-lint vet-version vet-packaging vet-$(TODO_X) \
        gh-pages gh-pages-docs gh-pages-coverage \
        reformat reformat-c reformat-md reformat-py reformat-cmake reformat-sh \
        check-build-deps check-dev-deps install-deps pc clean

all: package

.stamps:
	@mkdir -p .stamps

# --- default build: the shippable library only (shared + static), no tests ---
build/.cfg: $(CMAKE_IN)
	cmake -S . -B build
	@touch $@
build/.package: build/.cfg $(LIB_SRC) $(PUB_HDR) $(PRIV_HDR)
	cmake --build build --target yeast yeast_static
	@touch $@
package: build/.package

# --- Debug config: sanitizers ---
build-debug/.cfg: $(CMAKE_IN)
	cmake -S . -B build-debug -DCMAKE_BUILD_TYPE=Debug
	@touch $@
build-debug/.build: build-debug/.cfg $(BUILD_DEPS)
	cmake --build build-debug
	@touch $@
# On Linux, ASAN_TEST_ENV turns on LeakSanitizer: it runs at each forked test's exit, so a leak fails the exact test
# that caused it. Apple clang has no LeakSanitizer (ASAN_TEST_ENV is empty there); the Release run leak-checks instead.
build-debug/.test: build-debug/.build
	$(ASAN_TEST_ENV) ctest --test-dir build-debug --output-on-failure
	@touch $@

# --- Release config: hardened ---
build-release/.cfg: $(CMAKE_IN)
	cmake -S . -B build-release -DCMAKE_BUILD_TYPE=Release
	@touch $@
build-release/.build: build-release/.cfg $(BUILD_DEPS)
	cmake --build build-release
	@touch $@
# ctest runs the tests for correctness; the LEAK_CHECK line then leak-checks. On macOS that runs the (non-ASan) Release
# binary through the `leaks` tool single-process (`--no-exec`), since `leaks` cannot inspect the ASan Debug binary. On
# Linux LEAK_CHECK is a no-op — LeakSanitizer already covered leaks in the Debug run.
build-release/.test: build-release/.build
	ctest --test-dir build-release --output-on-failure
	$(LEAK_CHECK) build-release/test_c --no-exec
	@touch $@

# --- Coverage config: instrument, run, and REPORT (no gate here — the gate is .stamps/gh-pages-coverage, which rides with
# gh-pages). Emits the machine-readable reports plus the human HTML report and the shields summary that gh-pages
# publishes. ---
build-coverage/.cfg: $(CMAKE_IN)
	cmake -S . -B build-coverage -DYEAST_COVERAGE=ON
	@touch $@
build-coverage/.cov: build-coverage/.cfg $(BUILD_DEPS) scripts/coverage_badge.py
	cmake --build build-coverage
	find build-coverage -name '*.gcda' -delete 2>/dev/null || true
	ctest --test-dir build-coverage --output-on-failure
	mkdir -p build-coverage/html
	$(GCOVR) --root . build-coverage --filter src/ --filter tests/ \
	    --gcov-executable "$(GCOV_EXE)" \
	    --cobertura build-coverage/coverage.xml --lcov build-coverage/coverage.lcov \
	    --json build-coverage/coverage.json --json-summary build-coverage/summary.json \
	    --html-details build-coverage/html/index.html --txt
	python3 scripts/coverage_badge.py build-coverage/summary.json build-coverage/coverage-badge.json
	@touch $@

# Enforce the // UNTESTED contract on the report data. Separate from build-coverage/.cov so the report can be published
# without gating; this stamp rides with gh-pages, as gh-pages-coverage.
.stamps/gh-pages-coverage: build-coverage/.cov scripts/coverage_gate.py | .stamps
	python3 scripts/coverage_gate.py build-coverage/coverage.json
	@touch $@

# The version guard for clang-format, shared by the check and the reformat so neither trusts the wrong major.
CLANG_FORMAT_CHECK = "$(CLANG_FORMAT)" --version | grep -q "version $(CLANG_FORMAT_MAJOR_VERSION)\." \
	|| { echo "clang-format $(CLANG_FORMAT_MAJOR_VERSION).x required, found: $$("$(CLANG_FORMAT)" --version 2>/dev/null \
	     | grep -o 'version [0-9.]*' || echo none). Run make install-deps-vet, or put a \
	     $(CLANG_FORMAT_MAJOR_VERSION).x clang-format on PATH." >&2; exit 1; }

# --- format checks (one stamp per language, so each re-checks independently) ---
.stamps/vet-format-c: $(ALL_SRC) .clang-format .clang-format-version | .stamps
	@$(CLANG_FORMAT_CHECK)
	"$(CLANG_FORMAT)" --dry-run --Werror $(ALL_SRC)
	@touch $@
.stamps/vet-format-md: $(MD_FILES) | .stamps
	$(MDFORMAT) --check --wrap 120 $(MD_FILES)
	@touch $@
# black reformats code but leaves comments and docstrings alone, so the 120-column rule needs ruff to be a rule at all —
# and ruff catches the import that nothing uses any more, which black has no opinion about either.
.stamps/vet-format-py: $(PY_FILES) | .stamps
	$(BLACK) --check --line-length 120 $(PY_FILES)
	$(RUFF) check --quiet --select E501,F401 --line-length 120 $(PY_FILES)
	@touch $@
.stamps/vet-format-cmake: $(CMAKE_FILES) | .stamps
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --check $(CMAKE_FILES)
	@touch $@
.stamps/vet-format-sh: $(SH_FILES) | .stamps
	$(SHFMT) -i 4 -d $(SH_FILES)
	@touch $@
.stamps/vet-comments: $(ALL_SRC) scripts/check_comments.py | .stamps
	python3 scripts/check_comments.py $(ALL_SRC)
	@touch $@

# --extra-arg=-Wno-unknown-warning-option: clang-tidy is clang-based, so it rejects the GCC-only warning flags that a
# GCC build records in compile_commands.json; tell it to ignore unknown -W options instead of erroring on them.
.stamps/vet-lint: $(ALL_SRC) .clang-tidy build-debug/.cfg | .stamps
	$(CLANG_TIDY) --quiet --extra-arg=-Wno-unknown-warning-option $(TIDY_EXTRA) -p build-debug $(LINT_FILES)
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --std=c99 \
	    --suppress=missingIncludeSystem --suppress='*:*third_party*' \
	    -I include -I third_party/acutest src tests
	@touch $@

# Leftover-marker scan: fail if the marker appears in any tracked file's name or content (case-insensitive), except
# docs/ which documents it. The marker is referenced only via $(TODO_X), so this Makefile never contains it literally.
.stamps/vet-$(TODO_X): $(SCAN_FILES) | .stamps
	@if git ls-files -- . ':(exclude)docs' | grep -in '$(TODO_X)'; then \
	    echo "marker found in a file name — rename it"; exit 1; fi
	@if git ls-files -z -- . ':(exclude)docs' | xargs -0 grep -HIni '$(TODO_X)' 2>/dev/null; then \
	    echo "marker found in file content — remove before completion"; exit 1; fi
	@echo "no leftover markers"
	@touch $@

# --- API docs (Doxygen) ---
# Generates HTML (build-docs/html — what the Pages job publishes) AND enforces completeness: WARN_AS_ERROR in the
# Doxyfile fails on any undocumented public symbol or missing @param/@return. Deliverable and gate in one, so pc depends
# on it directly — there is no separate check target.
build-docs/.docs: $(PUB_HDR) Doxyfile DoxygenLayout.xml CMakeLists.txt
	YEAST_VERSION="$(VERSION)" doxygen Doxyfile
	@touch $@

# Guard against version drift: the vcpkg port must match the source of truth (CMakeLists project()). Conan derives its
# version, so it cannot drift.
.stamps/vet-version: CMakeLists.txt ports/yeast/vcpkg.json | .stamps
	@vcpkg=$$(sed -n 's/.*"version": *"\([0-9.]*\)".*/\1/p' ports/yeast/vcpkg.json | head -1); \
	if [ "$$vcpkg" != "$(VERSION)" ]; then \
	    echo "version drift: CMakeLists=$(VERSION) vcpkg.json=$$vcpkg"; exit 1; \
	else echo "version consistent: $(VERSION)"; fi
	@touch $@

# Grammar round-trip: annotated2ir -> IR -> ir2annotated must reproduce libyeast's grammar exactly. A lossless-ingest
# gate for the generator; nothing, the token annotations included, is silently dropped or mangled in translation.
.stamps/verify-roundtrip: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_annotated_roundtrip.py
	@touch $@

# Grammar validation: every reference resolves to a production with a matching arity, and every production is reachable.
.stamps/verify-references: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/validate_grammar.py
	@touch $@

# The official grammar, recovered: erase libyeast's token annotations and its indicator productions from
# grammar/yeast-spec-1.2.yaml, and what remains must be the vendored grammar. What libyeast adds cannot quietly become what
# libyeast changes.
.stamps/verify-spec: $(GRAMMAR_SPEC) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@

# Marker balance: every begin- marker must be closed by its own end-, on every path, and a rule must balance them the
# same way whichever path is taken through it. Nothing else catches this: a marker consumes no character, so the rule
# that every character lies within a token action says nothing about it.
.stamps/verify-markers: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_markers.py
	@touch $@

# Grammar documentation: every rule that emits tokens must say which, in the order it emits them — checked against the
# grammar itself, so a note that is wrong fails as surely as one that is missing.
.stamps/verify-emits: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_docs.py
	@touch $@

# Decoder tables: the committed src/decoder_tables.h must be exactly what the grammar produces, and every bit of every
# key must agree with a direct evaluation of the character set it stands for. The classification cannot drift.
.stamps/verify-decoder: $(ANNOTATED) $(GEN_SRC) src/decoder_tables.h | .stamps
	python3 generator/check_decoder.py
	@touch $@

# Conformance fixtures: libyeast's own suite in tests/spec/ must be intact — every input paired with an output, every
# name decoding to a production the grammar still has, every output a token stream whose marks chain. Built once from
# the vendored reference fixtures, now libyeast's to keep correct.
.stamps/verify-fixtures: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_spec_tests.py
	@touch $@

# The community YAML Test Suite, folded to events — YAMLStar is the reference libyeast follows, so this is the star net:
# an independent one, written by other hands from the same spec, so it catches a grammar bug libyeast's own fixtures —
# migrated from one reference — would share. Every case must fold to its events or reject where it must, save the
# divergences declared with their reasons, where the spec and the suite disagree and the spec wins.
.stamps/verify-star: $(STAR_DATA) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_star.py
	@touch $@

# Wire code map: wire.py's character-per-code table must match src/wire.c's, so the interpreter cannot write a code the
# C parser would write differently.
.stamps/verify-wire: src/wire.c $(GEN_SRC) | .stamps
	python3 generator/check_wire.py
	@touch $@

# The emitter can be undone: every field a checkpoint must restore is restored, and restored the same way twice, since
# an alternation rewinds to one checkpoint once per branch. The whole of the backtracking rests on it and the fixtures
# cannot see it — an aliased checkpoint reproduced all of them while breaking it.
.stamps/verify-emitter: $(GEN_SRC) | .stamps
	python3 generator/check_emitter.py
	@touch $@

# Error messages: every `(cut)` in the grammar names a message defined in messages.yaml, and every message is named by a
# cut — so the cut sites and their text stay the one source the interpreter and the generated C table both derive from.
.stamps/verify-messages: $(ANNOTATED) $(MESSAGES) $(GEN_SRC) | .stamps
	python3 generator/check_messages.py
	@touch $@

# The reference interpreter reproduces every fixture it covers: it runs the production the grammar describes and its
# token stream must equal the fixture's, byte for byte. This is where the grammar is proved to emit the reference's
# tokens — the interpreter's coverage grows a node family at a time, and with it the fixtures this gate reproduces.
.stamps/verify-grammar-base: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_interpreter.py
	@touch $@

# The fixtures exercise every production the grammar has: running the reproducible ones matches or evaluates each. A
# production no fixture reaches is a coverage gap, whichever grammar this runs against — the base now, a transformed one
# later, whose reshaped productions the same suite must still exercise.
.stamps/verify-grammar-base-coverage: $(FIXTURES) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_coverage.py
	@touch $@

# Mode #3 — regenerate the committed generated files. `src/decoder_tables.h` is the only one so far, so this is the whole
# of it; the parser's tables will be the second, and one more line.
regen:
	python3 generator/grammar2decoder.py

# --- package-consumption test: install Release, build+run a consumer via pkg-config ---
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

# --- goal tree. Sub-goals are named for their parent, so `make <parent>` runs the group and `make <parent>-<part>` runs
# one part. Three ways to work: build+test the C library (all, install, test — pure C, no Python); verify the generator
# pipeline (verify); regenerate its outputs (regen). `make pc` is the developer gate over all of it. ---

# Mode #1 — consumer, pure C (no Python, no staleness checks).
install: build-release/.build
	cmake --install build-release --prefix "$(PREFIX)"
test-debug: build-debug/.test
test-release: build-release/.test
test: test-debug test-release

# Mode #2 — verify the generator pipeline is correct and its outputs current.
verify-roundtrip: .stamps/verify-roundtrip
verify-references: .stamps/verify-references
verify-spec: .stamps/verify-spec
verify-markers: .stamps/verify-markers
verify-emits: .stamps/verify-emits
verify-decoder: .stamps/verify-decoder
verify-wire: .stamps/verify-wire
verify-messages: .stamps/verify-messages
verify-emitter: .stamps/verify-emitter
verify-fixtures: .stamps/verify-fixtures
verify-star: .stamps/verify-star
verify-grammar-base: .stamps/verify-grammar-base
verify-grammar-base-coverage: .stamps/verify-grammar-base-coverage
# Every grammar reproduces the fixtures and is wholly exercised by them, bottom-up: the base grammar now, the structural
# grammar once it exists.
verify-grammar: verify-grammar-base verify-grammar-base-coverage
# In dependency order: the grammar as itself, then its compatibility with the official spec, then the interpreter
# machinery, then the fixtures (intact, then reproduced), then the independent star suite folded through the
# interpreter, and last the generator-to-C consistency the eventual C parser rests on.
verify: verify-roundtrip verify-references verify-markers verify-emits verify-messages verify-spec \
        verify-emitter verify-fixtures verify-grammar verify-star verify-wire verify-decoder

# Static code quality.
vet-format-c: .stamps/vet-format-c
vet-format-md: .stamps/vet-format-md
vet-format-py: .stamps/vet-format-py
vet-format-cmake: .stamps/vet-format-cmake
vet-format-sh: .stamps/vet-format-sh
vet-format: vet-format-c vet-format-md vet-format-py vet-format-cmake vet-format-sh
vet-comments: .stamps/vet-comments
vet-lint: .stamps/vet-lint
vet-version: .stamps/vet-version
vet-packaging: build-pkgtest/.pkg
vet-$(TODO_X): .stamps/vet-$(TODO_X)
vet: vet-format vet-comments vet-lint vet-$(TODO_X) vet-version vet-packaging

# The GitHub Pages payload: Doxygen API docs, the gcovr HTML report, and the coverage gate.
gh-pages-docs: build-docs/.docs
gh-pages-coverage: .stamps/gh-pages-coverage
build-gh-pages/.assembled: build-docs/.docs build-coverage/.cov
	rm -rf build-gh-pages
	mkdir -p build-gh-pages/coverage
	cp -R build-docs/html/. build-gh-pages/
	cp -R build-coverage/html/. build-gh-pages/coverage/
	cp build-coverage/coverage-badge.json build-gh-pages/coverage.json
	@touch $@
gh-pages: build-gh-pages/.assembled gh-pages-coverage

# reformat-* CHANGE source files in place; vet-format-* only verify.
reformat-c:
	@$(CLANG_FORMAT_CHECK)
	"$(CLANG_FORMAT)" -i $(ALL_SRC)
reformat-md:
	$(MDFORMAT) --wrap 120 $(MD_FILES)
reformat-py:
	$(BLACK) --line-length 120 $(PY_FILES)
reformat-cmake:
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --in-place $(CMAKE_FILES)
reformat-sh:
	$(SHFMT) -i 4 -w $(SH_FILES)
reformat: reformat-c reformat-md reformat-py reformat-cmake reformat-sh
check-build-deps:
	@sh scripts/check-deps.sh $(BUILD_DEP_TOOLS)
check-dev-deps: check-build-deps
	@sh scripts/check-deps.sh $(DEV_DEP_TOOLS)

# Install the tools a goal needs, OS auto-detected. `make install-deps` gets the C build deps — all `make all`,
# `make test`, and `make install` need; `make install-deps-<goal>` adds a sub-gate's tools (install-deps-pc,
# install-deps-vet, install-deps-verify, install-deps-gh-pages).
install-deps:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-build-deps.sh
install-deps-%:
	@$(DEPS_GUARD)
	sh scripts/install-$(DEPS_OS)-dev-deps.sh $*

# --- pre-commit gate: build, test, verify, vet, publish — in that order. Four sub-gates map to four CI workflows and
# badges: test, verify, vet, gh-pages (each also builds what it needs). CI never runs pc itself. ---
pc: all test verify vet gh-pages

clean:
	rm -rf build build-debug build-release build-coverage build-pkgtest build-docs build-gh-pages .stamps
