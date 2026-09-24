#!/bin/sh
# Install the tools a sub-gate needs on Debian and Ubuntu, on top of the C build deps. The tools are the parser
# generator's Python, the formatters and linters, and the coverage and docs tools.
#
# The goal argument `$1` picks the sub-gate. `c` or `test` add nothing. `verify` adds Python and PyYAML. `vet` adds the
# formatters and linters. `gh-pages` adds the coverage and docs tools. `pc` installs the whole set. A call with no goal
# does the same.
#
# Assumes the apt index is current. Run it from the project root. It reads `.clang-format-version` there.
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
    echo "unknown goal '$goal'. the goals are pc and c. test and verify are goals. vet and gh-pages are goals too." >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
sh "$here/install-debian-build-deps.sh" "$goal"

apt=""
pip=""
# Python is not a C build dep. A group that runs the generator brings Python. So does a group that pip-installs its
# tools. PyYAML serves the generator, and pip serves the formatters and the coverage tool.
if $gen; then
    apt="$apt python3-yaml"
fi
if $lint || $cov; then
    apt="$apt python3-pip"
fi
if $lint; then
    # clang-format comes from a pip wheel rather than from apt. Apt ships a different version. That version formats
    # code the gate then rejects. The wheel's major version comes from `.clang-format-version`. The gate and both dev-deps
    # scripts share that file. clang-tidy is a linter rather than a formatter. Its version does not bear the same load.
    apt="$apt clang-tidy cppcheck shfmt"
    pip="$pip clang-format==$(cat .clang-format-version).* mdformat mdformat-gfm black format-docstring gersemi ruff pylint mypy types-PyYAML conan"
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

# Hand CI the pinned clang-format. The Makefile then uses that build ahead of another build on PATH.
if $lint && [ -n "${GITHUB_ENV:-}" ]; then
    echo "CLANG_FORMAT=$(python3 -c 'import clang_format, os; print(os.path.join(os.path.dirname(clang_format.__file__), "data", "bin", "clang-format"))')" >>"$GITHUB_ENV"
fi
