#!/bin/sh
# Install the tools a sub-gate needs on Debian/Ubuntu, on top of the C build deps: the parser generator's Python, the
# formatters and linters, and the coverage and docs tools. The goal argument ($1) picks the sub-gate: `c` or `test`
# (nothing beyond the C build deps), `verify` (Python + PyYAML), `vet` (formatters + linters), `gh-pages` (coverage +
# docs); omitted or `pc` installs everything. Assumes the apt index is current. Run it from the project root: it reads
# .clang-format-version there.
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
    echo "install-debian-dev-deps.sh: unknown goal '$goal' (expected: pc, c, test, verify, vet, gh-pages)" >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
sh "$here/install-debian-build-deps.sh" "$goal"

apt=""
pip=""
# Python is not a C build dep, so the groups that run the generator or pip-install their tools bring it: PyYAML for the
# generator, pip for the formatters and coverage tool.
if $gen; then
    apt="$apt python3-yaml"
fi
if $lint || $cov; then
    apt="$apt python3-pip"
fi
if $lint; then
    # clang-format comes from a pip wheel, not apt: apt's version differs from a developer's and formats code the gate
    # then rejects. Its major is .clang-format-version, the one source the gate and both dev-deps scripts read.
    # clang-tidy is a linter, not a formatter, so its version is not load-bearing the same way.
    apt="$apt clang-tidy cppcheck shfmt"
    pip="$pip clang-format==$(cat .clang-format-version).* mdformat mdformat-gfm black format-docstring gersemi ruff"
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
