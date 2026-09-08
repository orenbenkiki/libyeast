#!/bin/sh
# Compilation only. The tools that build, test and install the C library on Debian/Ubuntu. CMake, a C compiler, and
# pkg-config. Building calls no Python. The generated files are committed. Running the generator (`make verify`,
# `make regen`) needs Python 3 and PyYAML. The dev-deps script adds those. Assumes the apt index is current (run
# `apt-get update` first if needed). This script takes an optional goal argument `$1` for parity with the dev-deps
# scripts, and ignores it. The C build deps do not depend on the goal.
set -eu
goal="${1:-}"
: "$goal"
sudo apt-get install -y cmake gcc clang pkg-config
