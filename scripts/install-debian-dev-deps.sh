#!/bin/sh
# Install the tools the `make pc` gate needs on Debian/Ubuntu: the build deps plus formatters, linters, coverage, and
# docs tools. An optional goal argument ($1: vet, test-c, gh-pages) narrows the install to just that sub-gate's tools;
# omitted or "pc" installs everything. Assumes the apt index is current.
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
    apt="$apt clang-format clang-tidy cppcheck shfmt"
    pip="$pip mdformat mdformat-gfm black gersemi"
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
