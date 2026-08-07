#!/usr/bin/env python3
from __future__ import annotations

import argparse
import keyword
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ExtensionSource:
    relative_path: Path
    module_name: str


def collect_extension_sources(
    root: Path,
    packages: Sequence[str],
    keep_sources: Iterable[str | Path] = (),
) -> list[ExtensionSource]:
    root = root.resolve()
    keep = {Path(item) for item in keep_sources}
    sources: list[ExtensionSource] = []
    for package in packages:
        package_dir = root / package
        if not package_dir.is_dir():
            raise FileNotFoundError(f"包目录不存在: {package_dir}")
        for source_path in sorted(package_dir.rglob("*.py")):
            relative = source_path.relative_to(root)
            if source_path.name == "__init__.py" or relative in keep:
                continue
            module_parts = relative.with_suffix("").parts
            if not all(part.isidentifier() and not keyword.iskeyword(part) for part in module_parts):
                continue
            sources.append(
                ExtensionSource(
                    relative_path=relative,
                    module_name=".".join(module_parts),
                )
            )
    return sources


def build_extensions(
    root: Path,
    packages: Sequence[str],
    keep_sources: Iterable[str | Path] = (),
    remove_sources: bool = False,
) -> list[ExtensionSource]:
    from Cython.Build import cythonize
    from setuptools import Extension, setup

    sources = collect_extension_sources(root, packages, keep_sources)
    extensions = [
        Extension(item.module_name, [str(root / item.relative_path)])
        for item in sources
    ]
    cwd = Path.cwd()
    os.chdir(root)
    try:
        setup(
            script_args=["build_ext", "--inplace"],
            ext_modules=cythonize(
                extensions,
                compiler_directives={
                    "language_level": 3,
                    "binding": True,
                    "embedsignature": True,
                    "annotation_typing": False,
                },
                quiet=True,
            ),
        )
    finally:
        os.chdir(cwd)

    if remove_sources:
        for item in sources:
            source = root / item.relative_path
            source.unlink()
            for generated in (source.with_suffix(".c"), source.with_suffix(".cpp")):
                if generated.exists():
                    generated.unlink()
    return sources


def main() -> None:
    parser = argparse.ArgumentParser(description="将Python业务模块编译为Cython扩展")
    parser.add_argument("--root", default=".")
    parser.add_argument("--package", action="append", required=True)
    parser.add_argument("--keep-source", action="append", default=[])
    parser.add_argument("--remove-sources", action="store_true")
    args = parser.parse_args()
    sources = build_extensions(
        root=Path(args.root),
        packages=args.package,
        keep_sources=args.keep_source,
        remove_sources=args.remove_sources,
    )
    for item in sources:
        print(f"{item.module_name} <- {item.relative_path}")


if __name__ == "__main__":
    main()
