// SPDX-License-Identifier: MIT
#include <yeast.h>

#include <stdio.h>
#include <stdlib.h>

// Version components injected by the build system (CMake) from the one source of truth: project(yeast VERSION ...). The
// 0.0.0 fallback lets out-of-build tooling parse this file; a binary actually built at 0.0.0 refuses to load (below).
#ifndef YS_VERSION_MAJOR
#define YS_VERSION_MAJOR 0
#endif
#ifndef YS_VERSION_MINOR
#define YS_VERSION_MINOR 0
#endif
#ifndef YS_VERSION_PATCH
#define YS_VERSION_PATCH 0
#endif

// Compose "MAJOR.MINOR.PATCH" from the components at preprocess time.
#define YS_STRINGIFY_(x) #x
#define YS_STRINGIFY(x) YS_STRINGIFY_(x)
#define YS_VERSION_STRING                                                                                              \
    YS_STRINGIFY(YS_VERSION_MAJOR) "." YS_STRINGIFY(YS_VERSION_MINOR) "." YS_STRINGIFY(YS_VERSION_PATCH)

#if defined(__GNUC__) || defined(__clang__)
// Refuse to load a library built without a real version (all components zero).
__attribute__((constructor)) static void ys_assert_version(void) {
    if (YS_VERSION_MAJOR == 0 && YS_VERSION_MINOR == 0 && YS_VERSION_PATCH == 0) {
        (void)fputs("libyeast: built without a version number; refusing to load\n", stderr); // UNTESTED
        abort();                                                                             // UNTESTED
    }
}
#endif

const char *ys_version(void) {
    return YS_VERSION_STRING;
}

int ys_major(void) {
    return YS_VERSION_MAJOR;
}

int ys_minor(void) {
    return YS_VERSION_MINOR;
}

int ys_patch(void) {
    return YS_VERSION_PATCH;
}
