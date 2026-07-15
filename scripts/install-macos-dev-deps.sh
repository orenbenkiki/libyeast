#!/bin/sh
# Install the tools the `make pc` gate needs on macOS: the build deps plus formatters, linters, coverage, and docs
# tools. An optional goal argument ($1: vet, test-c, gh-pages) narrows the install to just that sub-gate's tools;
# omitted or "pc" installs everything. Run it from the project root: it reads .clang-format-version there.
set -eu
goal="${1:-}"

# Tool groups this goal needs.
lint=false
cov=false
docs=false
case "$goal" in
'' | pc)
    lint=true
    cov=true
    docs=true
    ;;
vet) lint=true ;;
test-c) cov=true ;;
gh-pages)
    cov=true
    docs=true
    ;;
*)
    echo "install-macos-dev-deps.sh: unknown goal '$goal' (expected: pc, vet, test-c, gh-pages)" >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
sh "$here/install-macos-build-deps.sh" "$goal"

# LLVM supplies clang-tidy (lint) and llvm-cov (coverage); install it once if either group needs it. clang-format is not
# taken from it: it comes from a pip wheel, since brew's version differs from a developer's and formats code the gate
# then rejects. Its major is .clang-format-version, the one source the gate and both dev-deps scripts read.
need_llvm=false
brew_pkgs=""
pip_pkgs=""
if $lint; then
    need_llvm=true
    brew_pkgs="$brew_pkgs cppcheck shfmt"
    pip_pkgs="$pip_pkgs clang-format==$(cat .clang-format-version).* mdformat mdformat-gfm black gersemi ruff"
fi
if $cov; then
    need_llvm=true
    pip_pkgs="$pip_pkgs gcovr"
fi
if $docs; then
    brew_pkgs="$brew_pkgs doxygen"
fi
if $need_llvm; then
    brew_pkgs="$brew_pkgs llvm"
fi

if [ -n "$brew_pkgs" ]; then
    brew install $brew_pkgs
fi
if [ -n "$pip_pkgs" ]; then
    python3 -m pip install --break-system-packages $pip_pkgs
fi
# Homebrew LLVM is keg-only; on CI, put clang-tidy / clang / llvm-cov on PATH.
if $need_llvm && [ -n "${GITHUB_PATH:-}" ]; then
    echo "$(brew --prefix llvm)/bin" >>"$GITHUB_PATH"
fi
# Hand CI the pinned clang-format so the Makefile uses exactly it, not brew LLVM's on the PATH above.
if $lint && [ -n "${GITHUB_ENV:-}" ]; then
    echo "CLANG_FORMAT=$(python3 -c 'import clang_format, os; print(os.path.join(os.path.dirname(clang_format.__file__), "data", "bin", "clang-format"))')" >>"$GITHUB_ENV"
fi
