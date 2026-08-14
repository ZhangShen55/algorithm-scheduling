from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "docker" / "build_cython.py"


def _load_build_module():
    assert BUILD_SCRIPT.is_file(), "缺少 docker/build_cython.py"
    spec = spec_from_file_location("ocr_cython_build", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_application(root: Path) -> Path:
    app_dir = root / "app"
    (app_dir / "core").mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "core" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app_dir / "core" / "settings.py").write_text("VALUE = 2\n", encoding="utf-8")
    return app_dir


def _fake_compiler(module, application_root: Path, modules):
    for source in modules:
        extension = module.extension_path(application_root, source)
        extension.write_bytes(b"compiled")
        source.with_suffix(".c").write_text("generated", encoding="utf-8")
    (application_root / "build" / "temp.o").parent.mkdir(parents=True)
    (application_root / "build" / "temp.o").write_bytes(b"object")
    (application_root / "app" / "__pycache__").mkdir()


def test_discover_modules_excludes_empty_package_initializers(tmp_path):
    module = _load_build_module()
    app_dir = _write_application(tmp_path)

    modules = module.discover_modules(app_dir)

    assert [path.relative_to(tmp_path).as_posix() for path in modules] == [
        "app/core/settings.py",
        "app/main.py",
    ]


def test_cython_mode_builds_isolated_application_and_removes_sources(tmp_path):
    module = _load_build_module()
    source_dir = _write_application(tmp_path / "source")
    output_dir = tmp_path / "output" / "app"

    module.prepare_application(
        source_dir,
        output_dir,
        mode="yes",
        compiler=lambda root, modules: _fake_compiler(module, root, modules),
    )

    assert (source_dir / "main.py").is_file()
    assert (source_dir / "core" / "settings.py").is_file()
    assert sorted(path.name for path in output_dir.rglob("*.py")) == [
        "__init__.py",
        "__init__.py",
    ]
    assert len(list(output_dir.rglob("*.so"))) == 2
    assert not list(output_dir.rglob("*.c"))
    assert not list(output_dir.rglob("*.o"))
    assert not list(output_dir.rglob("__pycache__"))
    assert not (output_dir.parent / "build").exists()


def test_cython_mode_fails_if_any_extension_is_missing(tmp_path):
    module = _load_build_module()
    source_dir = _write_application(tmp_path / "source")
    output_dir = tmp_path / "output" / "app"

    def incomplete_compiler(root: Path, modules):
        module.extension_path(root, modules[0]).write_bytes(b"compiled")

    with pytest.raises(module.BuildError, match="缺少原生扩展"):
        module.prepare_application(
            source_dir,
            output_dir,
            mode="yes",
            compiler=incomplete_compiler,
        )


def test_plain_mode_copies_source_without_invoking_compiler(tmp_path):
    module = _load_build_module()
    source_dir = _write_application(tmp_path / "source")
    output_dir = tmp_path / "output" / "app"

    module.prepare_application(
        source_dir,
        output_dir,
        mode="no",
        compiler=lambda *_: pytest.fail("普通模式不应调用编译器"),
    )

    assert (output_dir / "main.py").is_file()
    assert (output_dir / "core" / "settings.py").is_file()


@pytest.mark.parametrize("mode", ["", "YES", "true", "1"])
def test_build_mode_rejects_values_other_than_yes_or_no(tmp_path, mode):
    module = _load_build_module()
    source_dir = _write_application(tmp_path / "source")

    with pytest.raises(module.BuildError, match='必须是 "yes" 或 "no"'):
        module.prepare_application(source_dir, tmp_path / "output" / "app", mode)
