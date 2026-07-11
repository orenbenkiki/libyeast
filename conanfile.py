from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout


class YeastConan(ConanFile):
    name = "yeast"
    license = "MIT"
    description = "Grammar-derived C YAML parser"
    homepage = "https://github.com/orenbenkiki/libyeast"
    topics = ("yaml", "parser", "c")

    def set_version(self):
        import os
        import re

        text = open(os.path.join(self.recipe_folder, "CMakeLists.txt")).read()
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
        if self.settings.os == "Windows":
            self.options.rm_safe("fPIC")

    def layout(self):
        cmake_layout(self)

    def generate(self):
        tc = CMakeToolchain(self)
        tc.cache_variables["YEAST_BUILD_TESTS"] = False
        tc.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        cmake = CMake(self)
        cmake.install()

    def package_info(self):
        self.cpp_info.libs = ["yeast"]
        self.cpp_info.set_property("cmake_file_name", "yeast")
        self.cpp_info.set_property("cmake_target_name", "yeast::yeast")
        self.cpp_info.set_property("pkg_config_name", "yeast")
