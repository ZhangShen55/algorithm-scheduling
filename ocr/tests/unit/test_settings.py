from pathlib import Path

import pytest

from app.core.exceptions import ConfigurationError
from app.core.settings import load_settings, parse_device


def test_load_settings_resolves_paths_from_config_directory(settings_file):
    settings = load_settings(settings_file)

    assert settings.server.port == 8866
    assert settings.ocr.device == "cpu"
    assert settings.ocr.detection_model_dir == (
        settings_file.parent / "models" / "PP-OCRv6_medium_det"
    ).resolve()
    assert settings.formula.enabled is False
    assert settings.formula.layout_model_dir == (
        settings_file.parent / "models" / "PP-DocLayout_plus-L"
    ).resolve()
    assert settings.formula.recognition_model_dir == (
        settings_file.parent / "models" / "PP-FormulaNet_plus-M"
    ).resolve()
    assert settings.logging.directory == (settings_file.parent / "logs").resolve()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cpu", ("cpu", None)),
        ("cuda:0", ("cuda", 0)),
        ("cuda:12", ("cuda", 12)),
        ("npu:0", ("npu", 0)),
    ],
)
def test_parse_device_accepts_supported_values(raw, expected):
    assert parse_device(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["gpu", "npu", "cuda:-1", "gpu:0", "cuda:a", ""],
)
def test_parse_device_rejects_unsupported_values(raw):
    with pytest.raises(ConfigurationError, match="device"):
        parse_device(raw)


def test_load_settings_requires_config_file(tmp_path):
    with pytest.raises(ConfigurationError, match="配置文件不存在"):
        load_settings(tmp_path / "config.toml")


def test_load_settings_rejects_out_of_range_threshold(settings_file):
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace("threshold = 0.3", "threshold = 1.2"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="threshold"):
        load_settings(settings_file)


def test_load_settings_rejects_missing_model_directory(settings_file):
    settings = load_settings(settings_file)
    Path(settings.ocr.detection_model_dir).rmdir()

    with pytest.raises(ConfigurationError, match="检测模型目录不存在"):
        load_settings(settings_file)


def test_load_settings_defaults_formula_to_disabled_when_section_is_missing(
    settings_file,
):
    content = settings_file.read_text(encoding="utf-8")
    formula_start = content.index("[formula]")
    logging_start = content.index("[logging]")
    settings_file.write_text(
        content[:formula_start] + content[logging_start:],
        encoding="utf-8",
    )

    settings = load_settings(settings_file)

    assert settings.formula.enabled is False


def test_load_settings_requires_formula_models_only_when_enabled(settings_file):
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="公式.*模型目录不存在"):
        load_settings(settings_file)

    (settings_file.parent / "models" / "PP-DocLayout_plus-L").mkdir()
    (settings_file.parent / "models" / "PP-FormulaNet_plus-M").mkdir()

    settings = load_settings(settings_file)

    assert settings.formula.enabled is True


def test_load_settings_rejects_invalid_formula_threshold(settings_file):
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace("layout_threshold = 0.5", "layout_threshold = 1.5"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="layout_threshold"):
        load_settings(settings_file)


def test_load_settings_rejects_invalid_formula_path_type_when_disabled(
    settings_file,
):
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace(
            'layout_model_dir = "models/PP-DocLayout_plus-L"',
            "layout_model_dir = [1, 2]",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="配置内容无效"):
        load_settings(settings_file)


def test_load_settings_preserves_model_directory_symlink_for_engine_audit(
    settings_file,
):
    models = settings_file.parent / "models"
    detection = models / "PP-OCRv6_medium_det"
    target = models / "actual-det"
    detection.rename(target)
    detection.symlink_to(target, target_is_directory=True)

    settings = load_settings(settings_file)

    assert settings.ocr.detection_model_dir == detection
    assert settings.ocr.detection_model_dir.is_symlink()
