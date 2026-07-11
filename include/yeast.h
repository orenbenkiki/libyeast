// SPDX-License-Identifier: MIT
#ifndef YEAST_H
#define YEAST_H

/// @file yeast.h
/// @brief Public API for libyeast — a grammar-derived C YAML parser.

/// @mainpage libyeast
///
/// libyeast is a fast, single-pass, pull-driven YAML 1.2 parser in C, generated from the formal grammar so that its
/// conformance is derived rather than hand-tested. The parser itself is not implemented yet; the current public API is
/// the version query — ys_version(), ys_major(), ys_minor(), and ys_patch().
///
/// For the architecture, see the
/// [design document](https://github.com/orenbenkiki/libyeast/blob/main/DESIGN.md); for what is left to build, see the
/// [roadmap](https://github.com/orenbenkiki/libyeast/blob/main/PLAN.md).

// Export control. Public symbols are marked YS_API; everything else is hidden by default (the build compiles with
// -fvisibility=hidden). Define YS_STATIC when linking libyeast statically (the installed CMake and pkg-config targets
// do this for you).
#if defined(_WIN32) || defined(__CYGWIN__)
#define YS_EXPORT __declspec(dllexport)
#define YS_IMPORT __declspec(dllimport)
#else
#define YS_EXPORT __attribute__((visibility("default")))
#define YS_IMPORT
#endif

#if defined(YS_STATIC)
#define YS_API
#elif defined(YEAST_BUILDING)
#define YS_API YS_EXPORT
#else
#define YS_API YS_IMPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// Return the libyeast version as a static, NUL-terminated "MAJOR.MINOR.PATCH" string.
///
/// @return a pointer to the version string; valid for the lifetime of the program, and the caller must not free it.
YS_API const char *ys_version(void);

/// Return the major component of the libyeast version.
///
/// @return the major version number.
YS_API int ys_major(void);

/// Return the minor component of the libyeast version.
///
/// @return the minor version number.
YS_API int ys_minor(void);

/// Return the patch component of the libyeast version.
///
/// @return the patch version number.
YS_API int ys_patch(void);

#ifdef __cplusplus
}
#endif

#endif // YEAST_H
