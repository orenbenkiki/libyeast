#!/bin/sh
# Minimum to build and install libyeast on Debian/Ubuntu: CMake, a C compiler, pkg-config, and Python 3 with PyYAML
# (the parser generator runs at build time). Assumes the apt index is already current (run `apt-get update` yourself
# first if needed). An optional goal argument ($1) is accepted for parity with the dev-deps scripts but ignored: the
# build deps are the same for every goal.
set -eu
goal="${1:-}"
: "$goal"
sudo apt-get install -y cmake gcc clang pkg-config python3 python3-yaml
