import os
from typing import Any, Protocol


class RuntimeOptionLike(Protocol):
    def use_cpu(self) -> None: ...

    def use_gpu(self, device_id: int) -> None: ...


def require_gpu_enabled() -> bool:
    return os.getenv("REQUIRE_GPU", "false").strip().lower() in {"1", "true", "yes"}


def _visible_nvidia_device_count() -> int | None:
    raw = os.getenv("NVIDIA_VISIBLE_DEVICES")
    if raw is None:
        raw = os.getenv("CUDA_VISIBLE_DEVICES")
    if raw is None or raw.strip().lower() == "all":
        return None
    normalized = raw.strip().lower()
    if normalized in {"", "none", "void"}:
        return 0
    return len([item for item in raw.split(",") if item.strip()])


def configure_runtime_option(
    option: RuntimeOptionLike,
    device: str,
    *,
    fastdeploy_module: Any | None = None,
) -> None:
    configured = device.strip().lower()
    if configured == "cpu":
        if require_gpu_enabled():
            raise RuntimeError("部署要求使用 GPU，但人脸算子配置不是 cuda:<index>")
        option.use_cpu()
        return

    index = int(configured.split(":", 1)[1])
    if fastdeploy_module is None:
        import fastdeploy as fastdeploy_module
    if not fastdeploy_module.is_built_with_gpu():
        raise RuntimeError("人脸算子要求使用 GPU，但 FastDeploy 未构建 GPU 能力")
    visible_count = _visible_nvidia_device_count()
    if visible_count is not None and index >= visible_count:
        raise RuntimeError(
            f"GPU 设备 {configured} 不可用，容器可见 GPU 数量为 {visible_count}"
        )
    option.use_gpu(index)
