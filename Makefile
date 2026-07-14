# libyeast build/verify orchestration.
#
# Every check is a *stamp file* target that depends on its real inputs (sources, configs) and is `touch`ed only after
# the check succeeds. A failed command aborts the recipe before the touch, so a stamp never records a false success, and
# `make <target>` with nothing changed does nothing at all. The heavy lifting (compilation) is delegated to CMake, which
# is itself incremental.

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
GRAMMAR_SPEC := third_party/yaml-grammar/yaml-spec-1.2.yaml
ANNOTATED := grammar/yeast-spec-1.2.yaml
GEN_SRC    := $(wildcard generator/*.py)

# Tool dependencies, verified by check-build-deps / check-dev-deps.
BUILD_DEP_TOOLS := cmake $(CC) python3 python3:yaml
DEV_DEP_TOOLS   := clang-format $(CLANG_TIDY) $(CPPCHECK) $(GCOVR) $(MDFORMAT) $(BLACK) $(RUFF) $(GERSEMI) \
                   $(SHFMT) doxygen $(firstword $(GCOV_EXE))

.PHONY: all package test test-c test-release test-haskell test-clojure \
        reformat reformat-c reformat-md reformat-py reformat-cmake reformat-sh \
        check-format check-format-c check-format-md check-format-py check-format-cmake check-format-sh \
        check-comments check-build-deps check-dev-deps \
        lint $(TODO_X) docs check-version check-grammar regen-tables coverage pkg-test vet gh-pages pc clean

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

# --- Coverage config: instrument, run, and REPORT (no gate here — the gate is .stamps/coverage-gate, which rides with
# test-c). Emits the machine-readable reports plus the human HTML report and the shields summary that gh-pages
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

# Enforce the // UNTESTED contract on the report data. Separate from build-coverage/.cov so gh-pages can publish the
# report without gating; this stamp rides with test-c.
.stamps/coverage-gate: build-coverage/.cov scripts/coverage_gate.py | .stamps
	python3 scripts/coverage_gate.py build-coverage/coverage.json
	@touch $@

# --- format checks (one stamp per language, so each re-checks independently) ---
.stamps/format-c: $(ALL_SRC) .clang-format | .stamps
	clang-format --dry-run --Werror $(ALL_SRC)
	@touch $@
.stamps/format-md: $(MD_FILES) | .stamps
	$(MDFORMAT) --check --wrap 120 $(MD_FILES)
	@touch $@
# black reformats code but leaves comments and docstrings alone, so the 120-column rule needs ruff to be a rule at all —
# and ruff catches the import that nothing uses any more, which black has no opinion about either.
.stamps/format-py: $(PY_FILES) | .stamps
	$(BLACK) --check --line-length 120 $(PY_FILES)
	$(RUFF) check --quiet --select E501,F401 --line-length 120 $(PY_FILES)
	@touch $@
.stamps/format-cmake: $(CMAKE_FILES) | .stamps
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --check $(CMAKE_FILES)
	@touch $@
.stamps/format-sh: $(SH_FILES) | .stamps
	$(SHFMT) -i 4 -d $(SH_FILES)
	@touch $@
.stamps/check-comments: $(ALL_SRC) scripts/check_comments.py | .stamps
	python3 scripts/check_comments.py $(ALL_SRC)
	@touch $@

# --extra-arg=-Wno-unknown-warning-option: clang-tidy is clang-based, so it rejects the GCC-only warning flags that a
# GCC build records in compile_commands.json; tell it to ignore unknown -W options instead of erroring on them.
.stamps/lint: $(ALL_SRC) .clang-tidy build-debug/.cfg | .stamps
	$(CLANG_TIDY) --quiet --extra-arg=-Wno-unknown-warning-option $(TIDY_EXTRA) -p build-debug $(LINT_FILES)
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --std=c99 \
	    --suppress=missingIncludeSystem --suppress='*:*third_party*' \
	    -I include -I third_party/acutest src tests
	@touch $@

# Leftover-marker scan: fail if the marker appears in any tracked file's name or content (case-insensitive), except
# docs/ which documents it. The marker is referenced only via $(TODO_X), so this Makefile never contains it literally.
.stamps/$(TODO_X): $(SCAN_FILES) | .stamps
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
.stamps/version-check: CMakeLists.txt ports/yeast/vcpkg.json | .stamps
	@vcpkg=$$(sed -n 's/.*"version": *"\([0-9.]*\)".*/\1/p' ports/yeast/vcpkg.json | head -1); \
	if [ "$$vcpkg" != "$(VERSION)" ]; then \
	    echo "version drift: CMakeLists=$(VERSION) vcpkg.json=$$vcpkg"; exit 1; \
	else echo "version consistent: $(VERSION)"; fi
	@touch $@

# Grammar round-trip: annotated2ir -> IR -> ir2annotated must reproduce libyeast's grammar exactly. A lossless-ingest
# gate for the generator; nothing, the token annotations included, is silently dropped or mangled in translation.
.stamps/grammar-roundtrip: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_annotated_roundtrip.py
	@touch $@

# Grammar validation: every reference resolves to a production with a matching arity, and every production is reachable.
.stamps/grammar-validate: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/validate_grammar.py
	@touch $@

# The official grammar, recovered: erase libyeast's token annotations and its indicator productions from
# grammar/yeast-spec-1.2.yaml, and what remains must be the vendored grammar. What libyeast adds cannot quietly become what
# libyeast changes.
.stamps/vendor-spec: $(GRAMMAR_SPEC) $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_vendor_spec.py
	@touch $@

# Marker balance: every begin- marker must be closed by its own end-, on every path, and a rule must balance them the
# same way whichever path is taken through it. Nothing else catches this: a marker consumes no character, so the rule
# that every character lies within a token action says nothing about it.
.stamps/markers: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_markers.py
	@touch $@

# Grammar documentation: every rule that emits tokens must say which, in the order it emits them — checked against the
# grammar itself, so a note that is wrong fails as surely as one that is missing.
.stamps/grammar-docs: $(ANNOTATED) $(GEN_SRC) | .stamps
	python3 generator/check_grammar_docs.py
	@touch $@

# Decoder tables: the committed src/decoder_tables.h must be exactly what the grammar produces, and every bit of every
# key must agree with a direct evaluation of the character set it stands for. The classification cannot drift.
.stamps/decoder-tables: $(ANNOTATED) $(GEN_SRC) src/decoder_tables.h | .stamps
	python3 generator/check_decoder.py
	@touch $@

# And this is how they stop being stale. `src/decoder_tables.h` is the only generated file that is committed, so this is
# the whole of it; the parser's tables will be the second, and one more line.
regen-tables:
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

# --- human-facing aliases ---
test-c: build-debug/.test build-release/.test coverage
test-release: build-release/.test
test-haskell:
	@echo "test-haskell: differential oracle not wired yet (stub)"
test-clojure:
	@echo "test-clojure: differential oracle not wired yet (stub)"
test: test-c test-haskell test-clojure
# reformat-* CHANGE source files in place; check-format-* only verify.
reformat-c:
	clang-format -i $(ALL_SRC)
reformat-md:
	$(MDFORMAT) --wrap 120 $(MD_FILES)
reformat-py:
	$(BLACK) --line-length 120 $(PY_FILES)
reformat-cmake:
	$(GERSEMI) --no-warn-about-unknown-commands --line-length 120 --in-place $(CMAKE_FILES)
reformat-sh:
	$(SHFMT) -i 4 -w $(SH_FILES)
reformat: reformat-c reformat-md reformat-py reformat-cmake reformat-sh
check-format-c: .stamps/format-c
check-format-md: .stamps/format-md
check-format-py: .stamps/format-py
check-format-cmake: .stamps/format-cmake
check-format-sh: .stamps/format-sh
check-format: check-format-c check-format-md check-format-py check-format-cmake check-format-sh
check-comments: .stamps/check-comments
check-build-deps:
	@sh scripts/check-deps.sh $(BUILD_DEP_TOOLS)
check-dev-deps: check-build-deps
	@sh scripts/check-deps.sh $(DEV_DEP_TOOLS)
lint: .stamps/lint
$(TODO_X): .stamps/$(TODO_X)
docs: build-docs/.docs
check-version: .stamps/version-check
check-grammar: .stamps/grammar-roundtrip .stamps/grammar-validate .stamps/vendor-spec .stamps/markers \
               .stamps/grammar-docs .stamps/decoder-tables
coverage: .stamps/coverage-gate
pkg-test: build-pkgtest/.pkg

# --- GitHub Pages payload: Doxygen API docs at the root, the gcovr HTML report under coverage/, and the shields
# endpoint JSON the coverage badge reads. Report only — it depends on build-coverage/.cov, never the gate — so a
# coverage regression cannot fail gh-pages (the gate rides with test-c). ---
build-gh-pages/.assembled: build-docs/.docs build-coverage/.cov
	rm -rf build-gh-pages
	mkdir -p build-gh-pages/coverage
	cp -R build-docs/html/. build-gh-pages/
	cp -R build-coverage/html/. build-gh-pages/coverage/
	cp build-coverage/coverage-badge.json build-gh-pages/coverage.json
	@touch $@
gh-pages: build-gh-pages/.assembled

# --- the three sub-gates: static quality + packaging (vet), C tests + coverage gate (test-c), docs + coverage report
# (gh-pages). Each is one CI workflow of the same name. ---
vet: check-format check-comments lint $(TODO_X) check-version check-grammar pkg-test

# --- pre-commit gate: every check, run before every commit. A pure aggregator of the three sub-gates. CI never runs pc
# itself; it runs each sub-gate (vet, test-c, gh-pages) in its own workflow. ---
pc: vet test-c gh-pages

clean:
	rm -rf build build-debug build-release build-coverage build-pkgtest build-docs build-gh-pages .stamps
