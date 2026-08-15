from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_config_example_keeps_production_safe_defaults_and_comments():
    path = PROJECT_ROOT / "config.toml.example"
    content = path.read_text(encoding="utf-8")
    config = tomllib.loads(content)

    assert config["ocr"]["enable_hpi"] is False
    assert config["ocr"]["max_concurrency"] == 1
    assert config["ocr"]["recognition_batch_size"] == 4
    assert config["ocr"]["detection"]["box_threshold"] == 0.5
    assert config["formula"]["recognition_batch_size"] == 1
    assert "仅在 device = \"cpu\" 时生效" in content
    assert "容器逻辑编号" in content
    assert "单引擎串行推理" in content
    assert "请求会排队" in content
    assert "单位：字节" in content
    assert "单位：MiB" in content
