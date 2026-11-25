# The build, the tests and the install of libyeast. This builds the library shared and static from a single source list.
# The build hardens the library and hides the symbols outside the public API. This builds the test suite under acutest.
# It installs the header, the CMake package and the pkg-config file.

cmake_minimum_required(VERSION 3.20)
project(yeast VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# The option instruments the build for gcov coverage under GCC and Clang. The option adds `--coverage -O0 -g` to the
# compile and `--coverage` to the link. It turns off neither the sanitizers nor the hardening. The build config still
# decides those. The option stops with a fatal error under MSVC.
option(YEAST_COVERAGE "Build with gcov coverage instrumentation (GCC and Clang)" OFF)
if(YEAST_COVERAGE)
    if(MSVC)
        message(FATAL_ERROR "`YEAST_COVERAGE` requires GCC or Clang for gcov. MSVC does not provide it.")
    endif()
    add_compile_options(--coverage -O0 -g)
    add_link_options(--coverage)
endif()

include(CheckCCompilerFlag)
include(CheckLinkerFlag)

# The flags below guard the flag probes. -Werror makes an unknown -Wfoo fail the probe.
# [Clang](https://clang.llvm.org/docs/DiagnosticsReference.html#wunknown-warning-option) only warns on an unknown -Wfoo.
# [GCC](https://gcc.gnu.org/onlinedocs/gcc/Warning-Options.html) already fails on one. Hardening probes override those
# guard flags to "-O2 -Werror". glibc raises a #warning for `_FORTIFY_SOURCE` without optimization, and -O2 silences it.
# [`features.h`](https://sourceware.org/git/?p=glibc.git;a=blob;f=include/features.h) holds that #warning. The probe
# then accepts FORTIFY rather than rejecting it.
#
# -Werror still fails flags that are inert on this target. -fstack-clash-protection on macOS is such a flag. Clang emits
# -Wunused-command-line-argument for that flag. [Clang's
# diagnostics](https://clang.llvm.org/docs/DiagnosticsReference.html#wunused-command-line-argument) enable that warning
# by default. The probe prunes those flags.
set(YEAST_PROBE_FLAGS "-Werror")

# Collect into `out_var` the flags in ARGN that the linker accepts for C.
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

# Collect into `out_var` the flags in ARGN that the linker accepts.
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

# Strict warnings. The library takes the full set.
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

# The library build and the test executables take the Debug sanitizer flags and the Release hardening flags. ASan
# catches errors in the code that the compiler instruments with ASan. [The AddressSanitizer
# wiki](https://github.com/google/sanitizers/wiki/AddressSanitizer) says so.
add_library(yeast_build INTERFACE)
if(NOT MSVC)
    target_compile_options(
        yeast_build
        INTERFACE $<$<CONFIG:Debug>:-fsanitize=address,undefined;-fno-omit-frame-pointer>
    )
    target_link_options(yeast_build INTERFACE $<$<CONFIG:Debug>:-fsanitize=address,undefined>)

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
    # Under MSVC, the Debug build turns on AddressSanitizer. AddressSanitizer catches a heap, stack or global overflow,
    # and a use-after-free. MSVC has no UBSan or LeakSanitizer counterpart. AddressSanitizer clashes with the Debug
    # run-time checks and with incremental linking.
    # [AddressSanitizer](https://learn.microsoft.com/en-us/cpp/sanitizers/asan?view=msvc-170) documents these limits.
    # Under the policies of the minimum version this file names, CMake puts the run-time check flags in
    # `CMAKE_C_FLAGS_DEBUG`. [CMP0184](https://cmake.org/cmake/help/latest/policy/CMP0184.html) documents that
    # placement. Strip the run-time check flags and disable `/INCREMENTAL` for the Debug config.
    string(REGEX REPLACE "/RTC[1csu]+" "" CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG}")
    target_compile_options(yeast_build INTERFACE $<$<CONFIG:Debug>:/fsanitize=address>)
    target_link_options(yeast_build INTERFACE $<$<CONFIG:Debug>:/INCREMENTAL:NO>)
endif()

# The targets below build the library. The shared build and the static build compile the same source. Both builds take
# the same base name.
#
# The source glob uses `CONFIGURE_DEPENDS`. CMake re-checks the glob at build time. CMake reconfigures when the set of
# files changes, as https://cmake.org/cmake/help/latest/command/file.html#glob says.
file(GLOB YEAST_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.c)
add_library(yeast SHARED ${YEAST_SOURCES})
add_library(yeast_static STATIC ${YEAST_SOURCES})
foreach(_t IN ITEMS yeast yeast_static)
    target_include_directories(
        ${_t}
        PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include> $<INSTALL_INTERFACE:include>
    )
    # The POSIX baseline governs the library's code. The baseline does not cover third-party test code. The version
    # string comes from the project version.
    target_compile_definitions(
        ${_t}
        PRIVATE
            _POSIX_C_SOURCE=200809L
            YS_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
            YS_VERSION_MINOR=${PROJECT_VERSION_MINOR}
            YS_VERSION_PATCH=${PROJECT_VERSION_PATCH}
    )
    # Build-only usage requirements. These must not leak into the exported package.
    target_link_libraries(${_t} PRIVATE $<BUILD_INTERFACE:yeast_warnings> $<BUILD_INTERFACE:yeast_build>)
endforeach()
# The build compiles the static library as PIC. A linker can then put the static archive into a shared object, as
# https://gcc.gnu.org/onlinedocs/gcc/Code-Gen-Options.html#index-fPIC says.
set_target_properties(yeast_static PROPERTIES OUTPUT_NAME yeast POSITION_INDEPENDENT_CODE ON)
set_target_properties(yeast PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION ${PROJECT_VERSION_MAJOR})

# Symbol export control. Hide a symbol by default, and export the `YS_API` ones. The shared build exports the public
# API. The static build has no import or export decoration.
set_target_properties(yeast yeast_static PROPERTIES C_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
target_compile_definitions(yeast PRIVATE YEAST_BUILDING)
target_compile_definitions(yeast_static PUBLIC YS_STATIC)

# Tests. A packager may turn the tests off, along with the acutest dev dependency.
option(YEAST_BUILD_TESTS "Build the test suite" ON)
if(YEAST_BUILD_TESTS)
    enable_testing()
    add_executable(test_c tests/test_c.c)
    target_include_directories(test_c PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/acutest)
    # `test_emitter_reconstructs_fixtures` reads the `.output` wires and `.input` fragments in `tests/spec/`.
    target_compile_definitions(test_c PRIVATE YS_SPEC_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/spec")
    target_link_libraries(test_c PRIVATE yeast)
    # The test TU includes vendored `acutest.h`. The build does not hold that TU to the full strict set.
    if(NOT MSVC)
        target_compile_options(test_c PRIVATE -Wall -Wextra -Werror)
    endif()
    target_link_libraries(test_c PRIVATE yeast_build)
    add_test(NAME test_c COMMAND test_c)

    # A `tests/test_*.c` file other than `tests/test_c.c` links the static archive and includes `src/` directly. The
    # archive's symbols survive -fvisibility=hidden. That flag sets the symbol visibility of an ELF image, as
    # https://gcc.gnu.org/onlinedocs/gcc/Code-Gen-Options.html#index-fvisibility says. A glob over `tests/test_*.c`
    # picks the files up, and a new test file is a test the moment it exists.
    file(GLOB YEAST_INTERNAL_TESTS CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_*.c)
    list(REMOVE_ITEM YEAST_INTERNAL_TESTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_c.c)
    foreach(_test IN LISTS YEAST_INTERNAL_TESTS)
        get_filename_component(_name ${_test} NAME_WE)
        add_executable(${_name} ${_test})
        target_include_directories(
            ${_name}
            PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/acutest ${CMAKE_CURRENT_SOURCE_DIR}/src
        )
        target_link_libraries(${_name} PRIVATE yeast_static yeast_build)
        # `test_stress_test` reads the cases of Markus Kuhn's UTF-8 decoder stress test from
        # `third_party/utf8-stress-test/UTF-8-test.txt`.
        target_compile_definitions(
            ${_name}
            PRIVATE YS_UTF8_STRESS_TEST="${CMAKE_CURRENT_SOURCE_DIR}/third_party/utf8-stress-test/UTF-8-test.txt"
        )
        if(NOT MSVC)
            target_compile_options(${_name} PRIVATE -Wall -Wextra -Werror)
        endif()
        add_test(NAME ${_name} COMMAND ${_name})
    endforeach()
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
