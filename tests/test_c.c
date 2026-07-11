// SPDX-License-Identifier: MIT
#include "acutest.h"
#include <stdio.h>
#include <string.h>
#include <yeast.h>

static void test_ys_version_matches_components(void) {
    char expected[32];
    int written = snprintf(expected, sizeof(expected), "%d.%d.%d", ys_major(), ys_minor(), ys_patch());
    TEST_CHECK(written > 0 && (size_t)written < sizeof(expected));

    const char *version = ys_version();
    TEST_CHECK(version != NULL);
    if (version != NULL) {
        TEST_CHECK(strcmp(version, expected) == 0);
        TEST_MSG("ys_version()=\"%s\" components=\"%s\"", version, expected);
    }
}

TEST_LIST = {
    {"ys_version_matches_components", test_ys_version_matches_components},
    {NULL, NULL},
};
