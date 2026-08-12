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
SUPPORTED_VBAS_DOCKERFILES = (
    "vbas/docker/Dockerfile",
    "vbas/docker/Dockerfile.runtime",
)


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
        assert 'WORKERS="${UVICORN_WORKERS:-' in source, relative
        assert default_port in source, relative
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

    for relative in SUPPORTED_VBAS_DOCKERFILES:
        dockerfile = ROOT / relative
        source = dockerfile.read_text(encoding="utf-8")
        assert "EXPOSE 8981" in source, dockerfile
        assert "8881" not in source, dockerfile
        assert "nginx" not in source.lower(), dockerfile
        assert "start.sh" in source, dockerfile


def test_vbas_exposes_only_supported_dockerfiles() -> None:
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "vbas/docker").glob("Dockerfile*")
    }

    assert actual == set(SUPPORTED_VBAS_DOCKERFILES)


def test_vbas_build_context_includes_only_plain_model_image_inputs() -> None:
    source = (ROOT / "vbas/.dockerignore").read_text(encoding="utf-8").splitlines()

    assert "!models/**" in source
    assert "config*.toml" in source
    assert "models-encrypted/" in source
    assert "!models-encrypted/**" not in source
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
        assert secret_pattern in source


def test_text_analysis_defaults_to_one_worker_on_python311() -> None:
    start_source = (ROOT / "text_analysis/start.sh").read_text(encoding="utf-8")
    docker_source = (ROOT / "text_analysis/Dockerfile").read_text(encoding="utf-8")

    assert 'WORKERS="${UVICORN_WORKERS:-1}"' in start_source
    assert "UVICORN_WORKERS=1" in docker_source
    assert docker_source.count("FROM python:3.11") == 2
    assert "FROM python:3.10" not in docker_source


def _clean_text_analysis_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("UVICORN_WORKERS", "UVICORN_RELOAD", "UVICORN_EXTRA"):
        environment.pop(name, None)
    return environment


def test_text_analysis_rejects_multi_process_options_before_import() -> None:
    unsafe_environments = (
        {"UVICORN_WORKERS": "2"},
        {"UVICORN_RELOAD": "1"},
        {"UVICORN_EXTRA": "--workers 2"},
        {"UVICORN_EXTRA": "--workers=2"},
        {"UVICORN_EXTRA": "--reload"},
        {"UVICORN_EXTRA": "--reload=true"},
    )

    for overrides in unsafe_environments:
        environment = _clean_text_analysis_environment()
        environment.update(overrides)
        completed = subprocess.run(
            ["bash", str(ROOT / "text_analysis/start.sh")],
            cwd=ROOT / "text_analysis",
            env=environment,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

        assert completed.returncode != 0, overrides
        assert "requires exactly one Uvicorn process" in completed.stderr, (
            overrides,
            completed.stdout,
            completed.stderr,
        )


def test_text_analysis_preserves_safe_extra_arguments(tmp_path: Path) -> None:
    capture_path = tmp_path / "uvicorn-args"
    stub = tmp_path / "uvicorn"
    stub.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    environment = _clean_text_analysis_environment()
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment['PATH']}",
            "CAPTURE_PATH": str(capture_path),
            "UVICORN_EXTRA": "--timeout-keep-alive 10",
        }
    )

    completed = subprocess.run(
        ["bash", str(ROOT / "text_analysis/start.sh")],
        cwd=ROOT / "text_analysis",
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = capture_path.read_text(encoding="utf-8").splitlines()
    assert arguments[arguments.index("--workers") + 1] == "1"
    assert arguments[-2:] == ["--timeout-keep-alive", "10"]


def test_all_supported_vbas_images_install_registry_client() -> None:
    for relative in SUPPORTED_VBAS_DOCKERFILES:
        dockerfile = ROOT / relative
        source = dockerfile.read_text(encoding="utf-8")
        assert "algorithm_operator_registry_client-0.1.0-py3-none-any.whl" in source, (
            dockerfile
        )
        assert "pip install --no-deps" in source, dockerfile
