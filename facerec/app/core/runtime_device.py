from typing import Protocol


class RuntimeOptionLike(Protocol):
    def use_cpu(self) -> None: ...

    def use_gpu(self, device_id: int) -> None: ...


def configure_runtime_option(option: RuntimeOptionLike, device: str) -> None:
    if device == "cpu":
        option.use_cpu()
        return

    option.use_gpu(int(device.split(":", 1)[1]))
