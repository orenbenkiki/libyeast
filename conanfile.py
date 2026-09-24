# SPDX-License-Identifier: MIT
"""
The Conan recipe for the `yeast` package.

Conan calls a method below by name. Conan reads the version from `CMakeLists.txt`. The recipe states no version of its
own.
"""

import os
import re

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout


class YeastConan(ConanFile):
    """The `yeast` package. CMake builds it."""

    name = "yeast"
    license = "MIT"
    description = "Grammar-derived C YAML parser"
    homepage = "https://github.com/orenbenkiki/libyeast"
    topics = ("yaml", "parser", "c")

    def set_version(self):
        """Read the version out of `CMakeLists.txt`."""
        with open(os.path.join(self.recipe_folder, "CMakeLists.txt"), encoding="utf-8") as handle:
            text = handle.read()
        self.version = re.search(r"project\(yeast VERSION (\d+\.\d+\.\d+)", text).group(1)

    settings = "os", "arch", "compiler", "build_type"
    options = {"fPIC": [True, False]}
    default_options = {"fPIC": True}

    exports_sources = (
        "CMakeLists.txt",
        "cmake/*",
        "include/*",
        "src/*",
        "third_party/*",
        "LICENSE",
    )

    def config_options(self):
        """Drop `fPIC` on Windows. Windows has no such option."""
        # Conan replaces the `settings` tuple above with an object of its own before calling this.
        if self.settings.os == "Windows":  # pylint: disable=no-member
            self.options.rm_safe("fPIC")

    def layout(self):
        """Take the source and build layout CMake sets."""
        cmake_layout(self)

    def generate(self):
        """Write the CMake toolchain and turn the tests off."""
        tc = CMakeToolchain(self)
        tc.cache_variables["YEAST_BUILD_TESTS"] = False
        tc.generate()

    def build(self):
        """Configure and build with CMake."""
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        """Install the built artifacts into the package."""
        cmake = CMake(self)
        cmake.install()

    def package_info(self):
        """Say what a consumer links against and what CMake calls it."""
        self.cpp_info.libs = ["yeast"]
        self.cpp_info.set_property("cmake_file_name", "yeast")
        self.cpp_info.set_property("cmake_target_name", "yeast::yeast")
        self.cpp_info.set_property("pkg_config_name", "yeast")
