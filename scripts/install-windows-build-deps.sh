#!/bin/sh
# Windows build deps. GitHub's windows runner already ships CMake and the MSVC toolchain. This is a no-op there. On a
# bare Windows host, install CMake and the Visual Studio "Desktop development with C++" workload (e.g. via winget or
# choco). The `make pc` gate itself is Unix-only. There is no Windows dev-deps script. This script takes an optional
# goal argument `$1` for parity with the other install scripts, and ignores it.
set -eu
goal="${1:-}"
: "$goal"
if command -v cmake >/dev/null 2>&1; then
    echo "cmake present; nothing to install"
else
    echo "install CMake, the MSVC C++ build tools, and Python 3 with PyYAML (e.g. winget install Kitware.CMake, plus VS Build Tools, plus 'py -m pip install pyyaml')" >&2
    exit 1
fi
