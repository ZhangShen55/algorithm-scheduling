import os
from typing import Any


def require_gpu_enabled() -> bool:
    return os.getenv("REQUIRE_GPU", "false").strip().lower() in {"1", "true", "yes"}


def resolve_runtime_device(configured: int | str, *, torch_module: Any | None = None):
    if torch_module is None:
        import torch as torch_module

    value = str(configured).strip().lower()
    if value == "cpu":
        if require_gpu_enabled():
            raise RuntimeError("部署要求使用 GPU，但 VBas GPU_ID 不是 CUDA 设备")
        return torch_module.device("cpu")

    if value.isdigit():
        index = int(value)
    elif value.startswith("cuda:") and value[5:].isdigit():
        index = int(value[5:])
    else:
        raise RuntimeError("VBas GPU_ID 必须是 cpu、非负整数或 cuda:<index>")

    device = f"cuda:{index}"
    if not torch_module.cuda.is_available():
        raise RuntimeError(f"算子要求使用 GPU {device}，但 CUDA 不可用")
    visible_count = torch_module.cuda.device_count()
    if index >= visible_count:
        raise RuntimeError(
            f"GPU 设备 {device} 索引越界，可见 CUDA 设备数量为 {visible_count}"
        )
    return torch_module.device(device)
