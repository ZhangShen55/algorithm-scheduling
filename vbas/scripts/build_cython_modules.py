#!/usr/bin/env python3
import argparse
import os
import keyword
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
        exclude_globs: Iterable[str] = ()) -> list[ExtensionSource]:
    keep = {_normalize_relative_path(item) for item in keep_sources}
    excludes = tuple(str(item) for item in exclude_globs)
    sources: list[ExtensionSource] = []
    root = root.resolve()
    for package in packages:
        package_dir = root / package
        if not package_dir.exists():
            raise FileNotFoundError(f"包目录不存在: {package_dir}")
        for source_path in sorted(package_dir.rglob("*.py")):
            relative_path = source_path.relative_to(root)
            if source_path.name == "__init__.py":
                continue
            if relative_path in keep:
                continue
            if _matches_any(relative_path, excludes):
                continue
            if not _is_importable_module_path(relative_path):
                continue
            sources.append(ExtensionSource(
                relative_path=relative_path,
                module_name=_module_name(relative_path),
            ))
    return sources


def build_extensions(
        root: Path,
        packages: Sequence[str],
        keep_sources: Iterable[str | Path] = (),
        exclude_globs: Iterable[str] = (),
        remove_sources: bool = False) -> list[ExtensionSource]:
    sources = collect_extension_sources(root, packages, keep_sources, exclude_globs)
    if not sources:
        return sources

    try:
        from Cython.Build import cythonize
        from setuptools import Extension, setup
    except ImportError as exc:  # pragma: no cover - 依赖由 Docker 构建安装
        raise RuntimeError("缺少 Cython 构建依赖，请安装 Cython 和 setuptools") from exc

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
                compiler_directives={"language_level": "3"},
                quiet=True,
            ),
        )
    finally:
        os.chdir(cwd)

    if remove_sources:
        for item in sources:
            (root / item.relative_path).unlink()
            generated_c = root / item.relative_path.with_suffix(".c")
            generated_cpp = root / item.relative_path.with_suffix(".cpp")
            if generated_c.exists():
                generated_c.unlink()
            if generated_cpp.exists():
                generated_cpp.unlink()
    return sources


def _normalize_relative_path(value: str | Path) -> Path:
    return Path(str(value)).as_posix() and Path(str(value))


def _matches_any(path: Path, patterns: Sequence[str]) -> bool:
    text = path.as_posix()
    return any(path.match(pattern) or text.startswith(pattern.rstrip("/**")) for pattern in patterns)


def _module_name(relative_path: Path) -> str:
    return ".".join(relative_path.with_suffix("").parts)


def _is_importable_module_path(relative_path: Path) -> bool:
    return all(part.isidentifier() and not keyword.iskeyword(part) for part in relative_path.with_suffix("").parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="编译指定 Python 包为 Cython 扩展。")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--package", action="append", required=True, help="需要编译的包目录，可重复")
    parser.add_argument("--keep-source", action="append", default=[], help="保留明文源码的相对路径，可重复")
    parser.add_argument("--exclude-glob", action="append", default=[], help="排除编译的 glob，可重复")
    parser.add_argument("--remove-sources", action="store_true", help="编译成功后删除已编译源码")
    args = parser.parse_args()

    sources = build_extensions(
        root=Path(args.root),
        packages=args.package,
        keep_sources=args.keep_source,
        exclude_globs=args.exclude_glob,
        remove_sources=args.remove_sources,
    )
    for item in sources:
        print(f"{item.module_name} <- {item.relative_path}")


if __name__ == "__main__":
    main()
