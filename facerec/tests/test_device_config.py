import pytest
from pydantic import ValidationError

from app.core.config import GpuSettings, PROJECT_ROOT


def test_gpu_settings_accepts_cpu_device() -> None:
    settings = GpuSettings(device="cpu")

    assert settings.device == "cpu"


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


def test_configure_runtime_option_selects_cuda_index() -> None:
    try:
        from app.core.runtime_device import configure_runtime_option
    except ModuleNotFoundError as error:
        pytest.fail(str(error))

    option = FakeRuntimeOption()
    configure_runtime_option(option, "cuda:3")

    assert option.calls == [("cuda", 3)]


def test_project_root_contains_models_and_config() -> None:
    assert (PROJECT_ROOT / "config.toml").is_file()
    assert (PROJECT_ROOT / "ai_models").is_dir()
