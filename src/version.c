// SPDX-License-Identifier: MIT
// The version of this libyeast build. The components come from the build system, and the calls here report them.

#include <stdio.h>
#include <stdlib.h>
#include <yeast.h>

// Version components the build system (CMake) injects from `project(yeast VERSION ...)`. That call is the source of
// truth. The `0.0.0` fallback lets out-of-build tooling parse this file. A binary actually built at `0.0.0` refuses to
// load (below).
#ifndef YS_VERSION_MAJOR
#define YS_VERSION_MAJOR 0
#endif
#ifndef YS_VERSION_MINOR
#define YS_VERSION_MINOR 0 // the second component. CMake injects it the same way.
#endif
#ifndef YS_VERSION_PATCH
#define YS_VERSION_PATCH 0 // the third component. CMake injects it the same way.
#endif

// Compose the major, the minor and the patch into a dotted string at preprocess time.
#define YS_STRINGIFY_(x) #x
#define YS_STRINGIFY(x) YS_STRINGIFY_(x) // the outer pass. It expands x, and the inner pass then quotes the result.
// The components above joined with full stops. `ys_version` hands this back.
#define YS_VERSION_STRING                                                                                              \
    YS_STRINGIFY(YS_VERSION_MAJOR) "." YS_STRINGIFY(YS_VERSION_MINOR) "." YS_STRINGIFY(YS_VERSION_PATCH)

#if defined(__GNUC__) || defined(__clang__)
// Refuse to load a library built without a real version. A version of `0.0.0` is such a build.
__attribute__((constructor)) static void ys_assert_version(void) {
    if (YS_VERSION_MAJOR == 0 && YS_VERSION_MINOR == 0 && YS_VERSION_PATCH == 0) {
        (void)fputs("libyeast: built without a version number. refusing to load\n", stderr); // UNTESTED
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
