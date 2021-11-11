import platform

from setuptools import Extension, setup


def get_ext_modules() -> list:
    """
    获取三方模块

    Linux需要编译封装接口
    Windows直接使用预编译的pyd即可
    Mac由于缺乏二进制库支持无法使用
    """
    if platform.system() != "Linux":
        return []

    compiler_flags = [
        "-std=c++17",
        "-O3",
        "-Wno-delete-incomplete", "-Wno-sign-compare",
    ]
    extra_link_args = ["-lstdc++"]
    runtime_library_dirs = ["$ORIGIN"]

    vnksgoldmd = Extension(
        "vnpy_ksgold.api.vnksgoldmd",
        [
            "vnpy_ksgold/api/vnksgold/vnksgoldmd/vnksgoldmd.cpp",
        ],
        include_dirs=["vnpy_ksgold/api/include",
                      "vnpy_ksgold/api/vnksgold"],
        define_macros=[],
        undef_macros=[],
        library_dirs=["vnpy_ksgold/api/libs", "vnpy_ksgold/api"],
        libraries=["ksgoldquotmarketdataapi", "ksgoldtraderapi"],
        extra_compile_args=compiler_flags,
        extra_link_args=extra_link_args,
        runtime_library_dirs=runtime_library_dirs,
        depends=[],
        language="cpp",
    )

    vnksgoldtd = Extension(
        "vnpy_ksgold.api.vnksgoldtd",
        [
            "vnpy_ksgold/api/vnksgold/vnksgoldtd/vnksgoldtd.cpp",
        ],
        include_dirs=["vnpy_ksgold/api/include",
                      "vnpy_ksgold/api/vnksgold"],
        define_macros=[],
        undef_macros=[],
        library_dirs=["vnpy_ksgold/api/libs", "vnpy_ksgold/api"],
        libraries=["ksgoldquotmarketdataapi", "ksgoldtraderapi"],
        extra_compile_args=compiler_flags,
        extra_link_args=extra_link_args,
        runtime_library_dirs=runtime_library_dirs,
        depends=[],
        language="cpp",
    )

    return [vnksgoldtd, vnksgoldmd]


setup(
    ext_modules=get_ext_modules(),
)
