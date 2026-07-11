cmake_minimum_required(VERSION 3.20)
project(yeast VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Coverage instrumentation (gcov; GCC/Clang). A dedicated build with no sanitizers or hardening, so the gcov data is
# clean. MSVC is unsupported.
option(YEAST_COVERAGE "Build with gcov coverage instrumentation (GCC/Clang)" OFF)
if(YEAST_COVERAGE)
    if(MSVC)
        message(FATAL_ERROR "YEAST_COVERAGE requires GCC or Clang (gcov); MSVC is unsupported.")
    endif()
    add_compile_options(--coverage -O0 -g)
    add_link_options(--coverage)
endif()

include(CheckCCompilerFlag)
include(CheckLinkerFlag)

# Flags used to guard flag probes. -Werror makes an unknown positive -Wfoo fail the probe (GCC otherwise only warns on
# unknown -W). Hardening probes override this to "-O2 -Werror": -O2 silences -D_FORTIFY_SOURCE=3's -O0-only #warning (so
# it is not falsely rejected), while -Werror still fails flags that are inert on this target (e.g.
# -fstack-clash-protection on macOS emits -Wunused-command-line-argument), so they are pruned instead of breaking the
# real build under the library's -Werror.
set(YEAST_PROBE_FLAGS "-Werror")

# Collect, into out_var, those of ARGN the current compiler accepts.
function(yeast_supported_flags out_var)
    set(_ok "")
    foreach(flag IN LISTS ARGN)
        string(MAKE_C_IDENTIFIER "YEAST_HAS_${flag}" _var)
        if(NOT MSVC AND YEAST_PROBE_FLAGS)
            set(CMAKE_REQUIRED_FLAGS "${YEAST_PROBE_FLAGS}")
        endif()
        check_c_compiler_flag("${flag}" ${_var})
        unset(CMAKE_REQUIRED_FLAGS)
        if(${_var})
            list(APPEND _ok "${flag}")
        endif()
    endforeach()
    set(${out_var} "${_ok}" PARENT_SCOPE)
endfunction()

# Collect, into out_var, those of ARGN accepted as linker flags.
function(yeast_supported_link_flags out_var)
    set(_ok "")
    foreach(flag IN LISTS ARGN)
        string(MAKE_C_IDENTIFIER "YEAST_HAS_LINK_${flag}" _var)
        check_linker_flag(C "${flag}" ${_var})
        if(${_var})
            list(APPEND _ok "${flag}")
        endif()
    endforeach()
    set(${out_var} "${_ok}" PARENT_SCOPE)
endfunction()

# Strict warnings — full set on the library.
add_library(yeast_warnings INTERFACE)
if(MSVC)
    yeast_supported_flags(YEAST_WARN_FLAGS /W4 /WX /permissive- /sdl /analyze)
else()
    yeast_supported_flags(YEAST_WARN_FLAGS
        -Wall -Wextra -Wpedantic -pedantic-errors -Werror
        -Wshadow -Wconversion -Wsign-conversion -Wcast-qual -Wcast-align
        -Wpointer-arith -Wstrict-prototypes -Wmissing-prototypes
        -Wmissing-declarations -Wold-style-definition -Wredundant-decls
        -Wnested-externs -Wwrite-strings -Wundef -Wformat=2 -Wswitch-enum
        -Wfloat-equal -Winit-self -Wnull-dereference -Wdouble-promotion -Wvla
        -Wstrict-overflow=5 -Wduplicated-cond -Wduplicated-branches -Wlogical-op
    )
endif()
target_compile_options(yeast_warnings INTERFACE ${YEAST_WARN_FLAGS})

# POSIX baseline, Debug sanitizers, Release hardening — applied to the library and the test executable (ASan needs the
# whole program instrumented).
add_library(yeast_build INTERFACE)
if(NOT MSVC)
    target_compile_options(
        yeast_build
        INTERFACE $<$<CONFIG:Debug>:-fsanitize=address,undefined;-fno-omit-frame-pointer>
    )
    target_link_options(yeast_build INTERFACE $<$<CONFIG:Debug>:-fsanitize=address,undefined>)

    # See YEAST_PROBE_FLAGS note: -O2 spares FORTIFY, -Werror prunes inert flags.
    set(YEAST_PROBE_FLAGS "-O2 -Werror")
    yeast_supported_flags(YEAST_HARDEN_C
        -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -fstack-protector-strong
        -fstack-clash-protection -fstrict-flex-arrays=3 -fcf-protection=full
        -mbranch-protection=standard -fno-strict-aliasing -fno-strict-overflow
        -fno-delete-null-pointer-checks -ftrivial-auto-var-init=zero
    )
    foreach(_f IN LISTS YEAST_HARDEN_C)
        target_compile_options(yeast_build INTERFACE $<$<CONFIG:Release>:${_f}>)
    endforeach()

    yeast_supported_link_flags(YEAST_HARDEN_LINK
        "-Wl,-z,relro" "-Wl,-z,now" "-Wl,-z,noexecstack" "-Wl,-z,nodlopen"
    )
    foreach(_f IN LISTS YEAST_HARDEN_LINK)
        target_link_options(yeast_build INTERFACE $<$<CONFIG:Release>:${_f}>)
    endforeach()
else()
    # MSVC: AddressSanitizer on the Debug build (heap/stack/global overflow + use-after-free; MSVC has no UBSan or
    # LeakSanitizer counterpart). ASan is incompatible with the Debug run-time checks (/RTC1, which CMake injects into
    # CMAKE_C_FLAGS_DEBUG) and with incremental linking, so strip /RTC* and disable /INCREMENTAL for the Debug config.
    string(REGEX REPLACE "/RTC[1csu]+" "" CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG}")
    target_compile_options(yeast_build INTERFACE $<$<CONFIG:Debug>:/fsanitize=address>)
    target_link_options(yeast_build INTERFACE $<$<CONFIG:Debug>:/INCREMENTAL:NO>)
endif()

# Library — shared and static from the same source, same base name.
add_library(yeast SHARED src/yeast.c)
add_library(yeast_static STATIC src/yeast.c)
foreach(_t IN ITEMS yeast yeast_static)
    target_include_directories(
        ${_t}
        PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include> $<INSTALL_INTERFACE:include>
    )
    # POSIX baseline is our code's contract, not imposed on third-party test code. Version string comes from the project
    # version — single source of truth.
    target_compile_definitions(
        ${_t}
        PRIVATE
            _POSIX_C_SOURCE=200809L
            YS_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
            YS_VERSION_MINOR=${PROJECT_VERSION_MINOR}
            YS_VERSION_PATCH=${PROJECT_VERSION_PATCH}
    )
    # Build-only usage requirements — must not leak into the exported package.
    target_link_libraries(${_t} PRIVATE $<BUILD_INTERFACE:yeast_warnings> $<BUILD_INTERFACE:yeast_build>)
endforeach()
# PIC so the static archive can be linked into shared objects and PIE executables.
set_target_properties(yeast_static PROPERTIES OUTPUT_NAME yeast POSITION_INDEPENDENT_CODE ON)
set_target_properties(yeast PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION ${PROJECT_VERSION_MAJOR})

# Symbol export control: hide everything by default, export only YS_API symbols.
set_target_properties(yeast yeast_static PROPERTIES C_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
target_compile_definitions(yeast PRIVATE YEAST_BUILDING) # shared build exports the public API
target_compile_definitions(yeast_static PUBLIC YS_STATIC) # static: no import/export decoration

# Tests — optional so packagers can skip them (and the acutest dev dependency).
option(YEAST_BUILD_TESTS "Build the test suite" ON)
if(YEAST_BUILD_TESTS)
    enable_testing()
    add_executable(test_c tests/test_c.c)
    target_include_directories(test_c PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/acutest)
    target_link_libraries(test_c PRIVATE yeast)
    # The test TU includes vendored acutest.h, not held to the full strict set.
    if(NOT MSVC)
        target_compile_options(test_c PRIVATE -Wall -Wextra -Werror)
    endif()
    target_link_libraries(test_c PRIVATE yeast_build)
    add_test(NAME test_c COMMAND test_c)
endif()

# Install / packaging.
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(
    TARGETS yeast yeast_static
    EXPORT yeastTargets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(FILES include/yeast.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(EXPORT yeastTargets FILE yeastTargets.cmake NAMESPACE yeast:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/yeast)

configure_package_config_file(
    cmake/yeastConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/yeastConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/yeast
)
write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/yeastConfigVersion.cmake
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)
install(
    FILES ${CMAKE_CURRENT_BINARY_DIR}/yeastConfig.cmake ${CMAKE_CURRENT_BINARY_DIR}/yeastConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/yeast
)

configure_file(cmake/yeast.pc.in ${CMAKE_CURRENT_BINARY_DIR}/yeast.pc @ONLY)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/yeast.pc DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
