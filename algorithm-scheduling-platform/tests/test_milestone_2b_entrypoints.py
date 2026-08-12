import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GPU_ENTRYPOINTS = {
    "asr_offline/docker/start.sh": ("asr_offline", "8083"),
    "asr_online/docker/start.sh": ("asr_online", "8084"),
    "facerec/docker/entrypoint.sh": ("facerec", "8000"),
    "ocr/docker/entrypoint.sh": ("ocr", "8866"),
    "screen_det/docker/start.sh": ("screen_det", "8880"),
    "vbas/docker/start.sh": ("vbas", "8981"),
}


def test_gpu_entrypoints_reject_multiple_workers_before_initialization() -> None:
    environment = {**os.environ, "UVICORN_WORKERS": "2"}

    for relative in GPU_ENTRYPOINTS:
        completed = subprocess.run(
            ["bash", str(ROOT / relative)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

        assert completed.returncode != 0, relative
        assert "GPU operator requires exactly one Uvicorn worker" in completed.stderr, (
            relative,
            completed.stdout,
            completed.stderr,
        )


def test_gpu_entrypoints_set_process_name_one_worker_and_stable_port() -> None:
    for relative, (default_name, default_port) in GPU_ENTRYPOINTS.items():
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")

        assert os.access(path, os.X_OK), relative
        assert f'PROCESS_NAME="${{GPU_PROCESS_NAME:-{default_name}}}"' in source, relative
        assert 'WORKERS="${UVICORN_WORKERS:-1}"' in source, relative
        assert f'PORT="${{PORT:-{default_port}}}"' in source, relative
        assert 'exec -a "$PROCESS_NAME"' in source, relative
        assert "--workers 1" in source, relative


def test_facerec_and_ocr_images_use_shell_entrypoints() -> None:
    expected = {
        "facerec/docker/Dockerfile": "docker/entrypoint.sh",
        "ocr/docker/Dockerfile": "docker/entrypoint.sh",
    }

    for relative, entrypoint in expected.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert f"COPY {entrypoint}" in source, relative
        assert "entrypoint.sh" in source, relative


def test_vbas_uses_contract_port_and_no_internal_nginx() -> None:
    start_source = (ROOT / "vbas/docker/start.sh").read_text(encoding="utf-8")
    assert "8881" not in start_source
    assert "nginx" not in start_source.lower()
    assert "INSTANCE_COUNT" not in start_source
    assert "WORKERS_PER_INSTANCE" not in start_source
    assert not (ROOT / "vbas/docker/nginx.conf").exists()

    dockerfiles = sorted((ROOT / "vbas/docker").glob("Dockerfile*"))
    assert dockerfiles
    for dockerfile in dockerfiles:
        source = dockerfile.read_text(encoding="utf-8")
        assert "EXPOSE 8981" in source, dockerfile
        assert "8881" not in source, dockerfile
        assert "nginx" not in source.lower(), dockerfile
        assert "start.sh" in source, dockerfile


def test_vbas_build_context_includes_canonical_image_inputs() -> None:
    source = (ROOT / "vbas/.dockerignore").read_text(encoding="utf-8").splitlines()

    assert "!config.toml" in source
    assert "!models/**" in source
    assert "!models-encrypted/**" in source
    encrypted_models_included_at = source.index("!models-encrypted/**")
    secret_patterns = (
        "*.key",
        "**/tias_model_key",
        "**/secrets/",
        "*.pem",
        "*.crt",
        "*.env",
        ".env",
    )
    for secret_pattern in secret_patterns:
        assert source.index(secret_pattern, encrypted_models_included_at + 1) > (
            encrypted_models_included_at
        )


def test_text_analysis_defaults_to_one_worker_on_python311() -> None:
    start_source = (ROOT / "text_analysis/start.sh").read_text(encoding="utf-8")
    docker_source = (ROOT / "text_analysis/Dockerfile").read_text(encoding="utf-8")

    assert 'WORKERS="${UVICORN_WORKERS:-1}"' in start_source
    assert "UVICORN_WORKERS=1" in docker_source
    assert docker_source.count("FROM python:3.11") == 2
    assert "FROM python:3.10" not in docker_source
