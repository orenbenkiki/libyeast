// SPDX-License-Identifier: MIT
// Package-consumption smoke test: built externally against the *installed* libyeast via pkg-config (see the Makefile's
// pkg-test target), not part of the CMake build. Prints the version so the harness can check it is non-empty.
#include <stdio.h>
#include <yeast.h>

int main(void) {
    const char *version = ys_version();
    if (version == NULL || version[0] == '\0') {
        return 1;
    }
    printf("%s\n", version);
    return 0;
}
