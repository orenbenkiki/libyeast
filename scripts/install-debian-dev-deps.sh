#!/bin/sh
# Install the tools the `make pc` gate needs on Debian/Ubuntu: the build deps plus formatters, linters, coverage, and
# docs tools. An optional goal argument ($1: vet, test-c, gh-pages) narrows the install to just that sub-gate's tools;
# omitted or "pc" installs everything. Assumes the apt index is current. Run it from the project root: it reads
# .clang-format-version there.
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
    echo "install-debian-dev-deps.sh: unknown goal '$goal' (expected: pc, vet, test-c, gh-pages)" >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
sh "$here/install-debian-build-deps.sh" "$goal"

apt=""
pip=""
if $lint; then
    # clang-format comes from a pip wheel, not apt: apt's version differs from a developer's and formats code the gate
    # then rejects. Its major is .clang-format-version, the one source the gate and both dev-deps scripts read.
    # clang-tidy is a linter, not a formatter, so its version is not load-bearing the same way.
    apt="$apt clang-tidy cppcheck shfmt"
    pip="$pip clang-format==$(cat .clang-format-version).* mdformat mdformat-gfm black gersemi ruff"
fi
if $cov; then
    apt="$apt llvm"
    pip="$pip gcovr"
fi
if $docs; then
    apt="$apt doxygen"
fi

if [ -n "$apt" ]; then
    sudo apt-get install -y $apt
fi
if [ -n "$pip" ]; then
    python3 -m pip install --break-system-packages $pip
fi

# Hand CI the pinned clang-format so the Makefile uses exactly it, whatever else is on PATH.
if $lint && [ -n "${GITHUB_ENV:-}" ]; then
    echo "CLANG_FORMAT=$(python3 -c 'import clang_format, os; print(os.path.join(os.path.dirname(clang_format.__file__), "data", "bin", "clang-format"))')" >>"$GITHUB_ENV"
fi
