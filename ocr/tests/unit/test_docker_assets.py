from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _runtime_stage(content: str) -> str:
    stages = re.split(r"(?m)^FROM ", content)
    return "FROM " + stages[-1]


def _builder_stage(content: str) -> str:
    stages = re.split(r"(?m)^FROM ", content)
    return "FROM " + stages[-2]


def test_cpu_gpu_dockerfile_supports_strict_optional_cython_build():
    content = (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/"
        "paddle:3.3.0-gpu-cuda11.8-cudnn8.9"
    ) in content
    assert content.count("FROM ") >= 2
    assert "AS app-builder" in content
    assert "AS runtime" in content
    assert "ARG cython=no" in content
    assert "ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" in content
    assert 'cython must be "yes" or "no"' in content
    assert "COPY docker/build_cython.py" in content
    assert '--mode "$cython"' in content
    assert "COPY --from=app-builder /build/application/app ./app" in content
    assert "/usr/local/cuda-11.8/compat" not in content
    assert "libgl1 libglib2.0-0" in content
    assert "COPY config.toml " not in content
    assert "EXPOSE 8866" in content
    assert 'CMD ["/usr/local/bin/ocr-entrypoint"]' in content


def test_builder_reuses_the_compiler_from_the_fixed_paddle_base_image():
    builder = _builder_stage(
        (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    )

    assert "command -v gcc" in builder
    assert "command -v make" in builder
    assert "apt-get install -y --no-install-recommends build-essential" not in builder


def test_runtime_prefers_the_nvidia_host_driver_over_cuda_compat():
    runtime = _runtime_stage(
        (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    )

    assert (
        "LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/nvidia/lib64:"
        "/usr/local/nvidia/lib:"
    ) in runtime


def test_runtime_stage_preserves_platform_assets_and_removes_build_assets():
    content = (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    runtime = _runtime_stage(content)

    assert "COPY requirements.txt /tmp/requirements/" in runtime
    assert "COPY wheel/${OPERATOR_REGISTRY_CLIENT_WHEEL}" in runtime
    assert "python -m pip install --no-deps" in runtime
    assert '--index-url "$PYPI_INDEX_URL"' in runtime
    assert "-r /tmp/requirements/requirements.txt" in runtime
    assert "version('paddlepaddle-gpu')" in runtime
    assert "actual == '3.3.0'" in runtime
    assert "requirements.gpu.txt" not in runtime
    assert "rm -rf /tmp/requirements /tmp/wheels" in runtime
    assert "COPY --from=app-builder /build/application/app ./app" in runtime
    assert "COPY models ./models" in runtime
    assert "COPY scripts/verify_models.py /app/.build/verify_models.py" in runtime
    assert "COPY config.toml.example /app/.build/config.toml" in runtime
    assert "--mount=type=secret,id=ocr_model_manifest,required=true" in runtime
    assert "--models-root /app/models" in runtime
    assert "--manifest /run/secrets/ocr_model_manifest" in runtime
    assert "--exact" in runtime
    assert (
        "install -m 0444 /run/secrets/ocr_model_manifest "
        "/app/models/manifest.sha256"
    ) in runtime
    assert "rm -rf /app/.build" in runtime
    assert "docker/build_cython.py" not in runtime
    assert "apt-get purge -y --auto-remove gcc g++ make" in runtime
    assert "Cython==" not in runtime
    assert "COPY tests" not in runtime
    assert "COPY docs" not in runtime
    assert "COPY README" not in runtime
    assert "COPY config.toml " not in runtime
    assert "COPY docker/entrypoint.sh /usr/local/bin/ocr-entrypoint" in runtime
    assert 'CMD ["/usr/local/bin/ocr-entrypoint"]' in runtime


def test_runtime_stage_imports_with_a_temporary_default_config():
    runtime = _runtime_stage(
        (PROJECT_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    )

    assert "cp /app/.build/config.toml /app/config.toml" in runtime
    assert "rm -f /app/config.toml" in runtime
    assert "CONFIG_PATH=" not in runtime
    for module_name in (
        "app.main",
        "app.engines.paddleocr_v6",
        "app.engines.paddle_formula",
        "app.services.ocr_service",
        "app.services.formula_service",
    ):
        assert module_name in runtime


def test_platform_entrypoint_keeps_one_worker_and_named_process():
    entrypoint = (PROJECT_ROOT / "docker" / "entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert 'PORT="${PORT:-8866}"' in entrypoint
    assert "--workers 1" in entrypoint
    assert 'exec -a "$PROCESS_NAME"' in entrypoint
    assert "app.main:app" in entrypoint


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
    assert "!config.toml.example" in ignored
    assert "app" not in ignored
    assert "models" not in ignored


def test_formula_models_are_part_of_the_verified_docker_model_assets():
    manifest = (PROJECT_ROOT / "models" / "manifest.sha256").read_text(
        encoding="utf-8"
    )

    assert "PP-DocLayout_plus-L/" in manifest
    assert "PP-FormulaNet_plus-M/" in manifest


def test_linux_delivery_guide_starts_from_the_offline_tar():
    content = (PROJECT_ROOT / "docker" / "README.md").read_text(encoding="utf-8")

    tar_position = content.index("ocr_v6_amd.tar")
    build_position = content.index("docker build")
    assert tar_position < build_position
    assert "sha256sum -c ocr_v6_amd.tar.sha256" in content
    assert "docker load -i ocr_v6_amd.tar" in content


def test_linux_delivery_guide_covers_gpu_run_and_operations():
    content = (PROJECT_ROOT / "docker" / "README.md").read_text(encoding="utf-8")

    required_fragments = (
        'device = "cuda:0"',
        'recognition_batch_size = 1',
        'enable_hpi = false',
        'max_concurrency = 1',
        '--name ocr-v6-amd',
        "--gpus '\"device=2\"'",
        'config.toml:/app/config.toml:ro',
        '--log-driver json-file',
        '--log-opt max-size=100m',
        '--log-opt max-file=3',
        "启动成功日志",
        "常见失败日志",
        "配置文件不存在",
        "GPU 不可用",
        "端口占用",
        "docker stop ocr-v6-amd",
        "回滚",
    )
    for fragment in required_fragments:
        assert fragment in content
