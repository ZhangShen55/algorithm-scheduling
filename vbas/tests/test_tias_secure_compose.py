import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PROJECT_ROOT / "docker/docker-compose.gpu.secure.yml"
RUNTIME_DOCKERFILE = PROJECT_ROOT / "docker/Dockerfile.runtime"


def test_secure_compose_uses_one_registered_vbas_instance_and_image_startup() -> None:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_PATH),
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert list(document["services"]) == ["tias-gpu-8981"]

    service = document["services"]["tias-gpu-8981"]
    assert service.get("command") is None
    assert service.get("entrypoint") is None
    assert service["ports"] == [
        {
            "mode": "ingress",
            "target": 8981,
            "published": "8981",
            "protocol": "tcp",
        }
    ]
    assert service["environment"]["PORT"] == "8981"
    assert service["environment"]["UVICORN_WORKERS"] == "1"
    assert service["environment"]["GPU_PROCESS_NAME"] == "vbas"

    volume_targets = {volume["target"] for volume in service["volumes"]}
    assert {
        "/workspace/config.toml",
        "/workspace/models-encrypted",
        "/workspace/model-assets/cmu_panoptic_coco.yaml",
        "/run/bootstrap-secrets/tias_model_key",
    }.issubset(volume_targets)
    devices = service["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["driver"] == "nvidia"
    assert devices[0]["capabilities"] == ["gpu"]

    dockerfile = RUNTIME_DOCKERFILE.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["/usr/local/bin/tias-secure-entrypoint"]' in dockerfile
    assert 'CMD ["/usr/local/bin/vbas-start"]' in dockerfile
