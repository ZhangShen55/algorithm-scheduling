from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import app.core.model_verification as model_verification
from app.core.model_verification import (
    REQUIRED_MODEL_FILES,
    verify_configured_models,
)
from scripts.verify_models import ModelVerificationError, verify_manifest


MODEL_HASH = "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4"


def test_verify_manifest_accepts_matching_file(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "model.bin"
    model.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(
        "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4  model.bin\n",
        encoding="utf-8",
    )

    verified = verify_manifest(models, manifest)

    assert verified == [model]


def test_verify_manifest_rejects_hash_mismatch(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "model.bin"
    model.write_bytes(b"broken")
    manifest = models / "manifest.sha256"
    manifest.write_text(f"{'0' * 64}  model.bin\n", encoding="utf-8")

    with pytest.raises(ModelVerificationError, match="摘要不一致"):
        verify_manifest(models, manifest)


def test_verify_manifest_rejects_path_escape(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    manifest = models / "manifest.sha256"
    manifest.write_text(f"{'0' * 64}  ../outside.bin\n", encoding="utf-8")

    with pytest.raises(ModelVerificationError, match="非法路径"):
        verify_manifest(models, manifest)


def test_verify_manifest_checks_only_required_paths(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    required = models / "required.bin"
    required.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(
        "\n".join(
            [
                "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4  required.bin",
                f"{'0' * 64}  disabled-formula.bin",
            ]
        ),
        encoding="utf-8",
    )

    verified = verify_manifest(
        models,
        manifest,
        required_paths=[required],
    )

    assert verified == [required]


def test_verify_manifest_rejects_missing_required_manifest_entry(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "present.bin"
    model.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(
        "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4  present.bin\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelVerificationError, match="清单缺少.*missing.bin"):
        verify_manifest(
            models,
            manifest,
            required_paths=[models / "missing.bin"],
        )


def test_verify_manifest_without_selection_still_checks_every_entry(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    required = models / "required.bin"
    required.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(
        "\n".join(
            [
                "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4  required.bin",
                f"{'0' * 64}  disabled-formula.bin",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModelVerificationError, match="文件不存在.*disabled-formula.bin"):
        verify_manifest(models, manifest)


def test_verify_models_script_runs_directly_and_checks_all_manifest_entries(
    tmp_path: Path,
):
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    core_dir = project / "app" / "core"
    models = project / "models"
    scripts_dir.mkdir(parents=True)
    core_dir.mkdir(parents=True)
    models.mkdir()
    source_root = Path(__file__).resolve().parents[2]
    shutil.copy(source_root / "scripts" / "verify_models.py", scripts_dir)
    shutil.copy(
        source_root / "app" / "core" / "model_verification.py",
        core_dir,
    )
    (project / "app" / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "__init__.py").write_text("", encoding="utf-8")
    model = models / "model.bin"
    model.write_bytes(b"model")
    (models / "manifest.sha256").write_text(
        "\n".join(
            [
                "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4  model.bin",
                f"{'0' * 64}  unchecked.bin",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(scripts_dir / "verify_models.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "模型文件不存在：unchecked.bin" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


@pytest.mark.parametrize("second_name", ["model.bin", "nested/../model.bin"])
def test_verify_manifest_rejects_duplicate_normalized_declarations(
    tmp_path: Path,
    second_name: str,
):
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.bin").write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(
        f"{MODEL_HASH}  model.bin\n{MODEL_HASH}  {second_name}\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelVerificationError, match="重复声明.*model.bin"):
        verify_manifest(models, manifest)


def test_verify_manifest_rejects_manifest_symlink(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.bin").write_bytes(b"model")
    real_manifest = models / "real-manifest.sha256"
    real_manifest.write_text(
        f"{MODEL_HASH}  model.bin\n",
        encoding="utf-8",
    )
    manifest = models / "manifest.sha256"
    manifest.symlink_to(real_manifest)

    with pytest.raises(ModelVerificationError, match="模型清单.*符号链接"):
        verify_manifest(models, manifest)


def test_required_symlink_cannot_collapse_two_declared_assets(tmp_path: Path):
    models = tmp_path / "models"
    detection = models / "det" / "inference.json"
    recognition = models / "rec" / "inference.json"
    recognition.parent.mkdir(parents=True)
    detection.parent.mkdir()
    recognition.write_bytes(b"model")
    detection.symlink_to(recognition)
    manifest = models / "manifest.sha256"
    manifest.write_text(
        f"{MODEL_HASH}  det/inference.json\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelVerificationError, match="模型文件.*符号链接.*det"):
        verify_manifest(
            models,
            manifest,
            required_paths=[detection, recognition],
        )


@pytest.mark.parametrize("target_location", ["internal", "external"])
def test_verify_configured_models_rejects_symlink_model_directory(
    tmp_path: Path,
    target_location: str,
):
    models = tmp_path / "models"
    recognition = models / "rec"
    recognition.mkdir(parents=True)
    target = (
        models / "actual-det"
        if target_location == "internal"
        else tmp_path / "outside-det"
    )
    target.mkdir()
    detection = models / "det"
    detection.symlink_to(target, target_is_directory=True)
    manifest_lines = []
    for model_dir in (target, recognition):
        for file_name in REQUIRED_MODEL_FILES:
            model_file = model_dir / file_name
            model_file.write_bytes(b"model")
            if model_file.is_relative_to(models):
                manifest_lines.append(
                    f"{MODEL_HASH}  {model_file.relative_to(models)}"
                )
    (models / "manifest.sha256").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelVerificationError, match="配置模型目录.*符号链接"):
        verify_configured_models([detection, recognition])


def test_verify_manifest_wraps_manifest_stat_oserror(
    tmp_path: Path,
    monkeypatch,
):
    models = tmp_path / "models"
    models.mkdir()
    manifest = models / "manifest.sha256"
    original_is_file = Path.is_file

    def fail_manifest_stat(path):
        if path == manifest:
            raise OSError("secret stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fail_manifest_stat)

    with pytest.raises(ModelVerificationError, match="检查模型清单失败") as error:
        verify_manifest(models, manifest)

    assert isinstance(error.value.__cause__, OSError)


def test_verify_manifest_wraps_manifest_decode_error(
    tmp_path: Path,
    monkeypatch,
):
    models = tmp_path / "models"
    models.mkdir()
    manifest = models / "manifest.sha256"
    manifest.write_text("ignored", encoding="utf-8")

    def fail_manifest_read(path, *args, **kwargs):
        if path == manifest:
            raise UnicodeError("secret decode failure")
        raise AssertionError(f"unexpected read: {path}")

    monkeypatch.setattr(Path, "read_text", fail_manifest_read)

    with pytest.raises(ModelVerificationError, match="读取模型清单失败") as error:
        verify_manifest(models, manifest)

    assert isinstance(error.value.__cause__, UnicodeError)


def test_verify_manifest_wraps_model_stat_oserror(
    tmp_path: Path,
    monkeypatch,
):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "model.bin"
    model.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(f"{MODEL_HASH}  model.bin\n", encoding="utf-8")
    original_is_file = Path.is_file

    def fail_model_stat(path):
        if path == model:
            raise OSError("secret model stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fail_model_stat)

    with pytest.raises(
        ModelVerificationError,
        match="检查模型文件失败.*model.bin",
    ) as error:
        verify_manifest(models, manifest)

    assert isinstance(error.value.__cause__, OSError)


def test_verify_manifest_wraps_model_hash_oserror(
    tmp_path: Path,
    monkeypatch,
):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "model.bin"
    model.write_bytes(b"model")
    manifest = models / "manifest.sha256"
    manifest.write_text(f"{MODEL_HASH}  model.bin\n", encoding="utf-8")

    def fail_hash(path):
        raise OSError("secret model read failure")

    monkeypatch.setattr(model_verification, "_sha256", fail_hash)

    with pytest.raises(
        ModelVerificationError,
        match="读取模型文件失败.*model.bin",
    ) as error:
        verify_manifest(models, manifest)

    assert isinstance(error.value.__cause__, OSError)


def test_verify_models_cli_hides_manifest_decode_traceback(tmp_path: Path):
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    core_dir = project / "app" / "core"
    models = project / "models"
    scripts_dir.mkdir(parents=True)
    core_dir.mkdir(parents=True)
    models.mkdir()
    source_root = Path(__file__).resolve().parents[2]
    shutil.copy(source_root / "scripts" / "verify_models.py", scripts_dir)
    shutil.copy(
        source_root / "app" / "core" / "model_verification.py",
        core_dir,
    )
    (project / "app" / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "__init__.py").write_text("", encoding="utf-8")
    (models / "manifest.sha256").write_bytes(b"\xff")

    result = subprocess.run(
        [sys.executable, str(scripts_dir / "verify_models.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "模型验证失败：读取模型清单失败" in result.stderr
    assert "Traceback" not in result.stderr
