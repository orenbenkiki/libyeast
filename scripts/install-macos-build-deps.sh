#!/bin/sh
# Compilation only. The tools that build, test and install the C library on macOS. A C compiler (Xcode Command Line
# Tools), CMake, and pkg-config. Building calls no Python. The generated files are committed. The generator needs
# PyYAML, and `make verify` and `make regen` run it. The dev-deps script adds that. This script takes an optional goal
# argument `$1` for parity with the dev-deps scripts, and ignores it. The C build deps do not depend on the goal.
set -eu
goal="${1:-}"
: "$goal"
# The C compiler comes from the Xcode Command Line Tools rather than Homebrew. Install them if absent.
if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install
fi
brew install cmake pkg-config
