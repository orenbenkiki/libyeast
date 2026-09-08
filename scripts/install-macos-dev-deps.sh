#!/bin/sh
# Install the tools a sub-gate needs on macOS, on top of the C build deps. Those are the parser generator's PyYAML, the
# formatters and linters, and the coverage and docs tools.
#
# The goal argument `$1` picks the sub-gate. `c` or `test` add nothing. `verify` adds PyYAML. `vet` adds the formatters
# and linters. `gh-pages` adds the coverage and docs tools. `pc` installs the whole set. A call with no goal does the
# same.
#
# Run it from the project root. It reads .clang-format-version there.
set -eu
goal="${1:-}"

# Tool groups this goal needs.
gen=false
lint=false
cov=false
docs=false
case "$goal" in
'' | pc)
    gen=true
    lint=true
    cov=true
    docs=true
    ;;
c | test) ;;
verify) gen=true ;;
vet) lint=true ;;
gh-pages)
    cov=true
    docs=true
    ;;
*)
    echo "install-macos-dev-deps.sh: unknown goal '$goal'. the goals are pc and c. test and verify are goals. so are vet and gh-pages." >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
sh "$here/install-macos-build-deps.sh" "$goal"

# LLVM supplies clang-tidy (lint) and llvm-cov (coverage). Install LLVM once where either group needs it. clang-format
# comes from a pip wheel rather than from LLVM. Brew ships a different version. That version formats code the gate
# then rejects. Its major is .clang-format-version, the source the gate and both dev-deps scripts share. Python 3
# itself comes with the Xcode Command Line Tools the build-deps script installs. The pip install here covers PyYAML.
need_llvm=false
brew_pkgs=""
pip_pkgs=""
if $gen; then
    pip_pkgs="$pip_pkgs pyyaml"
fi
if $lint; then
    need_llvm=true
    brew_pkgs="$brew_pkgs cppcheck shfmt"
    pip_pkgs="$pip_pkgs clang-format==$(cat .clang-format-version).* mdformat mdformat-gfm black format-docstring gersemi ruff pylint mypy types-PyYAML conan"
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
# Homebrew LLVM is keg-only. On CI, put clang-tidy / clang / llvm-cov on PATH.
if $need_llvm && [ -n "${GITHUB_PATH:-}" ]; then
    echo "$(brew --prefix llvm)/bin" >>"$GITHUB_PATH"
fi
# Hand CI the pinned clang-format. The Makefile then uses that binary rather than the brew LLVM copy on the PATH above.
if $lint && [ -n "${GITHUB_ENV:-}" ]; then
    echo "CLANG_FORMAT=$(python3 -c 'import clang_format, os; print(os.path.join(os.path.dirname(clang_format.__file__), "data", "bin", "clang-format"))')" >>"$GITHUB_ENV"
fi
