import tomllib
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = WORKSPACE_ROOT / "algorithm-scheduling-platform/deploy/config/operators"

CONFIGS = {
    "asr_offline.gpu.toml": "asr_offline/config.toml",
    "asr_online.gpu.toml": "asr_online/config.toml",
    "ocr.gpu.toml": "ocr/config.toml",
    "vbas.gpu.toml": "vbas/config.toml",
    "facerec.gpu.toml": "facerec/config.toml",
    "screen_det.gpu.toml": "screen_det/config.toml",
    "ppt_slice.cpu.toml": "ppt_slice/config.toml",
    "text_analysis.cpu.toml": "text_analysis/config.toml",
}


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _key_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _key_shape(child) for key, child in value.items()}
    return None


def test_shared_operator_configs_preserve_supported_field_shape_and_comments() -> None:
    assert {path.name for path in CONFIG_ROOT.glob("*.toml")} == set(CONFIGS)
    for config_name, source_name in CONFIGS.items():
        shared_path = CONFIG_ROOT / config_name
        source_path = WORKSPACE_ROOT / source_name
        assert _key_shape(_load(shared_path)) == _key_shape(_load(source_path)), config_name
        assert "#" in shared_path.read_text(encoding="utf-8"), config_name


def test_gpu_configs_use_container_local_cuda_device_zero() -> None:
    assert _load(CONFIG_ROOT / "asr_offline.gpu.toml")["device"] == "cuda:0"
    assert _load(CONFIG_ROOT / "asr_offline.gpu.toml")["ngpu"] == 1
    assert _load(CONFIG_ROOT / "asr_online.gpu.toml")["device"] == "cuda:0"
    assert _load(CONFIG_ROOT / "asr_online.gpu.toml")["ngpu"] == 1
    assert _load(CONFIG_ROOT / "ocr.gpu.toml")["ocr"]["device"] == "cuda:0"
    assert _load(CONFIG_ROOT / "ocr.gpu.toml")["server"]["workers"] == 1
    vbas = _load(CONFIG_ROOT / "vbas.gpu.toml")
    assert vbas["GPU_ID"] == 0
    assert vbas["Teacher_Head_Pose"]["Device"] == "cuda:0"
    assert vbas["INSTANCE_COUNT"] == 1
    assert vbas["WORKERS_PER_INSTANCE"] == 1
    assert _load(CONFIG_ROOT / "facerec.gpu.toml")["gpu"]["device"] == "cuda:0"
    screen_det = _load(CONFIG_ROOT / "screen_det.gpu.toml")
    assert screen_det["yolo"]["device"] == "cuda:0"
    assert screen_det["server"]["workers"] == 1


def test_server_configs_keep_required_external_dependencies_and_paths() -> None:
    facerec = _load(CONFIG_ROOT / "facerec.gpu.toml")
    assert facerec["db"] == {
        "username": "root",
        "password": "root",
        "host": "mongodb",
        "port": "27017",
        "database": "facerecapi",
        "auth_source": "admin",
        "limit": 5000,
    }
    assert facerec["image"]["save_person_photo"] is False
    ppt_slice = _load(CONFIG_ROOT / "ppt_slice.cpu.toml")
    assert ppt_slice["paths"]["result_root"] == "/data/result"
    assert ppt_slice["task"]["max_concurrent_tasks"] > 1
    source_text_analysis = _load(WORKSPACE_ROOT / "text_analysis/config.toml")
    text_analysis = _load(CONFIG_ROOT / "text_analysis.cpu.toml")
    assert text_analysis["base_url"] == source_text_analysis["base_url"]
    assert text_analysis["model"] == source_text_analysis["model"]
