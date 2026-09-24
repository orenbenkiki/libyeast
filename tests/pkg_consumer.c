// SPDX-License-Identifier: MIT
// Package-consumption smoke test. The Makefile's pkg-test target builds this test against the installed libyeast
// through pkg-config. The CMake build leaves this test out. Prints the version. The harness checks that the version is
// non-empty.
#include <stdio.h>
#include <yeast.h>

// Print the version the installed library reports. A non-zero exit says the package did not link or did not answer.
int main(void) {
    const char *version = ys_version();
    if (version == NULL || version[0] == '\0') {
        return 1;
    }
    printf("%s\n", version);
    return 0;
}
