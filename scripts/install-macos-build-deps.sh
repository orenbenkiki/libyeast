#!/usr/bin/env bash
# Install a C compiler, CMake and pkg-config on macOS. The Xcode Command Line Tools supply the compiler. CMake runs
# `CMakeLists.txt`. `CMakeLists.txt` builds, tests and installs the C library. A CMake build runs no Python. The
# repository holds `src/decoder_tables.h`, the file `generator/grammar2decoder.py` writes. That generator reads the
# grammar with `annotated2ir.load`. `annotated2ir.load` needs PyYAML. `scripts/install-macos-dev-deps.sh` installs
# PyYAML. This script ignores its optional goal argument `$1`. `scripts/install-macos-dev-deps.sh` takes the same
# argument and passes it on to this script.
set -euo pipefail
goal="${1:-}"
: "$goal"
# The C compiler comes from the Xcode Command Line Tools rather than Homebrew. Install them if absent.
if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install
fi
brew install cmake pkg-config
