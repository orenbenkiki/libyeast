# vcpkg port for libyeast.
#
# At first release: set REF to the release tag and replace the SHA512 with the
# tarball hash. (Build once with SHA512 "0" and vcpkg will print the correct
# value to paste back.)
vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO orenbenkiki/libyeast
    REF "v${VERSION}"
    SHA512 "0"
    HEAD_REF main
)

vcpkg_cmake_configure(
    SOURCE_PATH "${SOURCE_PATH}"
    OPTIONS -DYEAST_BUILD_TESTS=OFF
)
vcpkg_cmake_install()
vcpkg_cmake_config_fixup(PACKAGE_NAME yeast CONFIG_PATH lib/cmake/yeast)
vcpkg_fixup_pkgconfig()

file(INSTALL "${SOURCE_PATH}/LICENSE" DESTINATION "${CURRENT_PACKAGES_DIR}/share/${PORT}" RENAME copyright)
