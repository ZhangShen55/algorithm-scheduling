from types import SimpleNamespace

import pytest
from app.core.config import PROJECT_ROOT, GpuSettings, resolve_config_path
from pydantic import ValidationError


def test_gpu_settings_accepts_cpu_device() -> None:
    settings = GpuSettings(device="cpu")

    assert settings.device == "cpu"


def test_relative_config_path_is_resolved_from_project_root(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", "configs/local.toml")

    assert resolve_config_path() == (PROJECT_ROOT / "configs/local.toml").resolve()


def test_gpu_settings_accepts_cuda_device() -> None:
    settings = GpuSettings(device="cuda:2")

    assert settings.device == "cuda:2"


def test_gpu_settings_rejects_removed_gpu_id() -> None:
    with pytest.raises(ValidationError):
        GpuSettings(gpu_id=0)


@pytest.mark.parametrize("device", ["cuda", "cuda:x", "mps", "gpu:0", ""])
def test_gpu_settings_rejects_invalid_device(device: str) -> None:
    with pytest.raises(ValidationError):
        GpuSettings(device=device)


class FakeRuntimeOption:
    def __init__(self) -> None:
        self.calls = []

    def use_cpu(self) -> None:
        self.calls.append(("cpu", None))

    def use_gpu(self, index: int) -> None:
        self.calls.append(("cuda", index))


def test_configure_runtime_option_selects_cpu() -> None:
    try:
        from app.core.runtime_device import configure_runtime_option
    except ModuleNotFoundError as error:
        pytest.fail(str(error))

    option = FakeRuntimeOption()
    configure_runtime_option(option, "cpu")

    assert option.calls == [("cpu", None)]


def test_configure_runtime_option_selects_cuda_index(monkeypatch) -> None:
    try:
        from app.core.runtime_device import configure_runtime_option
    except ModuleNotFoundError as error:
        pytest.fail(str(error))

    option = FakeRuntimeOption()
    fastdeploy = SimpleNamespace(is_built_with_gpu=lambda: True)
    monkeypatch.delenv("NVIDIA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    configure_runtime_option(option, "cuda:3", fastdeploy_module=fastdeploy)

    assert option.calls == [("cuda", 3)]


def test_required_gpu_rejects_cpu_before_runtime_option_call(monkeypatch) -> None:
    from app.core import runtime_device

    monkeypatch.setattr(runtime_device, "require_gpu_enabled", lambda: True)
    option = FakeRuntimeOption()

    with pytest.raises(RuntimeError, match="部署要求使用 GPU.*cuda:<index>"):
        runtime_device.configure_runtime_option(option, "cpu")

    assert option.calls == []


def test_cuda_rejects_fastdeploy_without_gpu_before_use_gpu(monkeypatch) -> None:
    from app.core.runtime_device import configure_runtime_option

    option = FakeRuntimeOption()
    fastdeploy = SimpleNamespace(is_built_with_gpu=lambda: False)

    with pytest.raises(RuntimeError, match="FastDeploy.*GPU"):
        configure_runtime_option(option, "cuda:0", fastdeploy_module=fastdeploy)

    assert option.calls == []


def test_cuda_rejects_index_outside_visible_nvidia_devices(monkeypatch) -> None:
    from app.core.runtime_device import configure_runtime_option

    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "2")
    option = FakeRuntimeOption()
    fastdeploy = SimpleNamespace(is_built_with_gpu=lambda: True)

    with pytest.raises(RuntimeError, match="cuda:1.*可见 GPU 数量为 1"):
        configure_runtime_option(option, "cuda:1", fastdeploy_module=fastdeploy)

    assert option.calls == []


@pytest.mark.parametrize("visible", ["-1", "NoDevFiles", "none", "void", ""])
def test_cuda_rejects_environment_without_visible_devices(
    monkeypatch, visible: str
) -> None:
    from app.core.runtime_device import configure_runtime_option

    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", visible)
    option = FakeRuntimeOption()
    fastdeploy = SimpleNamespace(is_built_with_gpu=lambda: True)

    with pytest.raises(RuntimeError, match="可见 GPU 数量为 0"):
        configure_runtime_option(option, "cuda:0", fastdeploy_module=fastdeploy)

    assert option.calls == []


@pytest.mark.parametrize("device", ["cuda", "cuda:x", "gpu:0", "cuda:-1", ""])
def test_runtime_device_rejects_malformed_cuda_configuration(
    monkeypatch, device: str
) -> None:
    from app.core.runtime_device import configure_runtime_option

    option = FakeRuntimeOption()

    with pytest.raises(RuntimeError, match="cpu 或 cuda:<index>"):
        configure_runtime_option(option, device)

    assert option.calls == []


def test_project_root_contains_models_and_config() -> None:
    assert (PROJECT_ROOT / "config.toml").is_file()
    assert (PROJECT_ROOT / "ai_models").is_dir()
