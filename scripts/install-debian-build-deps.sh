#!/bin/sh
# Compilation only: everything needed to build, test, and install the C library on Debian/Ubuntu — CMake, a C compiler,
# and pkg-config. That is all; building calls no Python, since the generated files are committed. Running the generator
# (`make verify`, `make regen`) needs Python 3 and PyYAML, which the dev-deps script adds. Assumes the apt index is
# current (run `apt-get update` first if needed). An optional goal argument ($1) is accepted for parity with the
# dev-deps scripts but ignored: the C build deps are the same for every goal.
set -eu
goal="${1:-}"
: "$goal"
sudo apt-get install -y cmake gcc clang pkg-config
