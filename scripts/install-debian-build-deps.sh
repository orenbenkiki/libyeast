#!/usr/bin/env bash
# Install CMake, the C compilers gcc and clang, and pkg-config on Debian or Ubuntu. The script ignores its optional goal
# argument `$1`.
set -euo pipefail
goal="${1:-}"
: "$goal"
sudo apt-get install -y cmake gcc clang pkg-config
