from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import shutil
import sys
import sysconfig


class BuildError(RuntimeError):
    pass


Compiler = Callable[[Path, Sequence[Path]], None]


def discover_modules(app_dir: Path) -> list[Path]:
    if not app_dir.is_dir():
        raise BuildError(f"应用目录不存在：{app_dir}")

    for initializer in app_dir.rglob("__init__.py"):
        if initializer.read_text(encoding="utf-8").strip():
            raise BuildError(f"包初始化文件包含业务实现：{initializer}")

    return sorted(
        path
        for path in app_dir.rglob("*.py")
        if path.name != "__init__.py"
    )


def extension_path(application_root: Path, source: Path) -> Path:
    try:
        source.relative_to(application_root)
    except ValueError as error:
        raise BuildError(f"模块不在应用根目录内：{source}") from error

    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    return source.with_name(f"{source.stem}{suffix}")


def _module_name(application_root: Path, source: Path) -> str:
    relative = source.relative_to(application_root).with_suffix("")
    return ".".join(relative.parts)


def compile_extensions(application_root: Path, modules: Sequence[Path]) -> None:
    try:
        from Cython.Build import cythonize
        from setuptools import Distribution, Extension
        from setuptools.command.build_ext import build_ext
    except ImportError as error:
        raise BuildError("Cython 构建依赖未安装") from error

    build_root = application_root / "build"
    extensions = [
        Extension(_module_name(application_root, source), [str(source)])
        for source in modules
    ]
    compiled_extensions = cythonize(
        extensions,
        build_dir=str(build_root / "cython"),
        compiler_directives={
            "language_level": 3,
            "binding": True,
            "always_allow_keywords": True,
        },
    )

    distribution = Distribution(
        {
            "name": "ocr-cython-application",
            "ext_modules": compiled_extensions,
        }
    )
    command = build_ext(distribution)
    command.build_lib = str(application_root)
    command.build_temp = str(build_root / "temp")
    command.inplace = False
    command.ensure_finalized()
    command.run()


def _verify_extensions(application_root: Path, modules: Sequence[Path]) -> None:
    missing = [
        source.relative_to(application_root).as_posix()
        for source in modules
        if not extension_path(application_root, source).is_file()
    ]
    if missing:
        raise BuildError("缺少原生扩展：" + ", ".join(missing))


def _remove_build_artifacts(
    application_root: Path,
    modules: Sequence[Path],
) -> None:
    for source in modules:
        source.unlink()

    for pattern in ("*.c", "*.cpp", "*.o", "*.obj", "*.pyc", "*.pyo"):
        for artifact in application_root.rglob(pattern):
            artifact.unlink()

    for cache_dir in sorted(
        application_root.rglob("__pycache__"),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        shutil.rmtree(cache_dir)

    shutil.rmtree(application_root / "build", ignore_errors=True)


def prepare_application(
    source_app: Path,
    output_app: Path,
    mode: str,
    compiler: Compiler = compile_extensions,
) -> None:
    if mode not in {"yes", "no"}:
        raise BuildError('cython 模式必须是 "yes" 或 "no"')
    if not source_app.is_dir():
        raise BuildError(f"应用目录不存在：{source_app}")
    if output_app.exists():
        raise BuildError(f"输出目录已存在：{output_app}")

    output_app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_app, output_app)
    if mode == "no":
        return

    application_root = output_app.parent
    modules = discover_modules(output_app)
    if not modules:
        raise BuildError("没有发现需要编译的应用模块")

    compiler(application_root, modules)
    _verify_extensions(application_root, modules)
    _remove_build_artifacts(application_root, modules)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 OCR 应用 Docker 产物")
    parser.add_argument("--source-app", type=Path, required=True)
    parser.add_argument("--output-app", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        prepare_application(args.source_app, args.output_app, args.mode)
    except BuildError as error:
        print(f"应用构建失败：{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
