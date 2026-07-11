#!/bin/sh
# Install the tools the `make pc` gate needs on macOS: the build deps plus formatters, linters, coverage, and docs
# tools. An optional goal argument ($1: vet, test-c, gh-pages) narrows the install to just that sub-gate's tools;
# omitted or "pc" installs everything.
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

# LLVM supplies clang-format / clang-tidy (lint) and llvm-cov (coverage); install it once if either group needs it.
need_llvm=false
brew_pkgs=""
pip_pkgs=""
if $lint; then
    need_llvm=true
    brew_pkgs="$brew_pkgs cppcheck shfmt"
    pip_pkgs="$pip_pkgs mdformat mdformat-gfm black gersemi"
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
# Homebrew LLVM is keg-only; on CI, put clang-format / clang-tidy / clang / llvm-cov on PATH.
if $need_llvm && [ -n "${GITHUB_PATH:-}" ]; then
    echo "$(brew --prefix llvm)/bin" >>"$GITHUB_PATH"
fi
