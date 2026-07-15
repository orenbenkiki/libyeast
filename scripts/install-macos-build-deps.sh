#!/bin/sh
# Compilation only: everything needed to build, test, and install the C library on macOS — a C compiler (Xcode Command
# Line Tools), CMake, and pkg-config. Building calls no Python, since the generated files are committed; running the
# generator (`make verify`, `make regen`) needs PyYAML, which the dev-deps script adds. An optional goal argument ($1)
# is accepted for parity with the dev-deps scripts but ignored: the C build deps are the same for every goal.
set -eu
goal="${1:-}"
: "$goal"
# The C compiler comes from the Xcode Command Line Tools, not Homebrew; install them if absent.
if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install
fi
brew install cmake pkg-config
