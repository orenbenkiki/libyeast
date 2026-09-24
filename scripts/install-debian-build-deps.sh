#!/bin/sh
# Install CMake, a C compiler and pkg-config on Debian or Ubuntu. A C build needs no more. The build calls no Python. The repository holds the generated files. The generator runs under `make verify` and `make regen`. The generator needs Python 3 and PyYAML, and the dev-deps script installs both. Run `apt-get update` first where the apt index is stale. The script ignores its optional goal argument `$1`. The dev-deps scripts take the same argument.
set -eu
goal="${1:-}"
: "$goal"
sudo apt-get install -y cmake gcc clang pkg-config
