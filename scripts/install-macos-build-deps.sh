#!/bin/sh
# Minimum to build and install libyeast on macOS: a C compiler (Xcode Command Line Tools), CMake, pkg-config, and
# Python 3 with PyYAML (the parser generator runs at build time). An optional goal argument ($1) is accepted for parity
# with the dev-deps scripts but ignored: the build deps are the same for every goal.
set -eu
goal="${1:-}"
: "$goal"
# The C compiler and Python 3 come from the Xcode Command Line Tools, not Homebrew; install them if absent.
if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install
fi
brew install cmake pkg-config
python3 -m pip install --break-system-packages pyyaml
