from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cpu_gpu_dockerfile_uses_official_paddle_runtime():
    content = (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/"
        "paddle:3.3.0-gpu-cuda11.8-cudnn8.9"
    ) in content
    assert "COPY app ./app" in content
    assert "COPY models ./models" in content
    assert "python scripts/verify_models.py" in content
    assert "/usr/local/cuda-11.8/compat" in content
    assert "libgl1 libglib2.0-0" in content
    assert "COPY config.toml" not in content
    assert "EXPOSE 8866" in content
    assert "COPY docker/entrypoint.sh /usr/local/bin/ocr-entrypoint" in content
    assert 'CMD ["/usr/local/bin/ocr-entrypoint"]' in content

    entrypoint = (PROJECT_ROOT / "docker" / "entrypoint.sh").read_text(
        encoding="utf-8"
    )
    assert 'PORT="${PORT:-8866}"' in entrypoint
    assert "--workers 1" in entrypoint
    assert 'exec -a "$PROCESS_NAME"' in entrypoint


def test_npu_dockerfile_reuses_application_and_models_without_cuda_base():
    content = (PROJECT_ROOT / "docker" / "Dockerfile.npu").read_text(
        encoding="utf-8"
    )

    assert "ARG NPU_BASE_IMAGE" in content
    assert "paddlepaddle/paddle" not in content
    assert "cuda" not in content.lower()
    assert "COPY app ./app" in content
    assert "COPY models ./models" in content
    assert "COPY config.toml" not in content


def test_docker_context_excludes_formal_config_and_development_assets():
    content = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    ignored = set(content.splitlines())
    assert {"config.toml", "tests", "docs", "logs"}.issubset(ignored)
    assert "app" not in ignored
    assert "models" not in ignored


def test_formula_models_are_part_of_the_verified_docker_model_assets():
    manifest = (PROJECT_ROOT / "models" / "manifest.sha256").read_text(
        encoding="utf-8"
    )

    assert "PP-DocLayout_plus-L/" in manifest
    assert "PP-FormulaNet_plus-M/" in manifest
