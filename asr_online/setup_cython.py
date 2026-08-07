#!/usr/bin/env python3
"""将业务模块编译为 Cython 扩展（.so）。"""
from __future__ import annotations

import sys
from pathlib import Path

from setuptools import Extension, setup

ROOT = Path(__file__).resolve().parent

# 不编译：构建脚本、测试与脚本工具
SKIP_FILES = {"setup_cython.py"}
SKIP_PREFIXES = ("scripts/", "test/", "tests/")


def ensure_package_inits() -> None:
    for pkg in ("app", "app/api", "app/api/routes", "app/core", "app/utils"):
        init_py = ROOT / pkg / "__init__.py"
        init_py.parent.mkdir(parents=True, exist_ok=True)
        init_py.touch(exist_ok=True)


def collect_sources() -> list[str]:
    sources: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or any(rel.startswith(p) for p in SKIP_PREFIXES):
            continue
        sources.append(rel)
    return sources


def module_name(py_path: str) -> str:
    return py_path[:-3].replace("/", ".")


def create_extensions(sources: list[str]) -> list[Extension]:
    return [
        Extension(module_name(s), [s], extra_compile_args=["-std=c99"])
        for s in sources
    ]


def main() -> None:
    try:
        from Cython.Build import cythonize
    except ImportError:
        print("请先安装 Cython: pip install cython", file=sys.stderr)
        sys.exit(1)

    ensure_package_inits()
    sources = collect_sources()
    if not sources:
        print("未找到需要编译的模块", file=sys.stderr)
        sys.exit(1)

    print("Cython 编译模块:")
    for s in sources:
        print(f"  - {s}")

    setup(
        name="seacraft-asr-online-cython",
        ext_modules=cythonize(
            create_extensions(sources),
            compiler_directives={
                "language_level": "3",
                "boundscheck": False,
                "wraparound": False,
            },
            nthreads=0,
        ),
        zip_safe=False,
    )


if __name__ == "__main__":
    main()
