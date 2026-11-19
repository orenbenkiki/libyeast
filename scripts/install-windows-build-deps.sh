#!/usr/bin/env bash
# Windows build deps. GitHub's windows runner already ships CMake and the MSVC toolchain, as
# https://github.com/actions/runner-images says. This is a no-op there. This script installs nothing on a host without
# `cmake`. This script prints a list and fails. The list holds CMake, the MSVC C++ build tools, and Python 3 with
# PyYAML. This script takes an optional goal argument `$1`, and ignores it, as `scripts/install-debian-build-deps.sh`
# does.
set -euo pipefail
goal="${1:-}"
: "$goal"
if command -v cmake >/dev/null 2>&1; then
    echo "cmake present; nothing to install"
else
    echo "install CMake, the MSVC C++ build tools, and Python 3 with PyYAML (e.g. winget install the Kitware CMake package, plus VS Build Tools, plus 'py -m pip install pyyaml')" >&2
    exit 1
fi
