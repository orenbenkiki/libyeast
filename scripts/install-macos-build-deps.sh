#!/bin/sh
# Install a C compiler, CMake and pkg-config on macOS. The Xcode Command Line Tools supply the compiler. These tools build, test and install the C library. A build runs no Python. The repository holds the generated files. The generator needs PyYAML. `make verify` and `make regen` run the generator. The dev-deps script installs PyYAML. This script ignores its optional goal argument `$1`. The dev-deps scripts take the same argument.
set -eu
goal="${1:-}"
: "$goal"
# The C compiler comes from the Xcode Command Line Tools rather than Homebrew. Install them if absent.
if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install
fi
brew install cmake pkg-config
