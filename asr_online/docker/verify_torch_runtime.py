from __future__ import annotations

import importlib
from typing import Protocol, cast

EXPECTED_RUNTIME_VERSION = "2.6.0+cu124"
EXPECTED_CUDA_VERSION = "12.4"


class TorchVersion(Protocol):
    cuda: str | None


class TorchRuntime(Protocol):
    __version__: str
    version: TorchVersion


class TorchAudioRuntime(Protocol):
    __version__: str


def verify_torch_runtime(
    torch_module: TorchRuntime,
    torchaudio_module: TorchAudioRuntime,
) -> None:
    if torch_module.__version__ != EXPECTED_RUNTIME_VERSION:
        raise RuntimeError(
            f"expected torch runtime version {EXPECTED_RUNTIME_VERSION}, "
            f"got {torch_module.__version__}"
        )
    if torchaudio_module.__version__ != EXPECTED_RUNTIME_VERSION:
        raise RuntimeError(
            f"expected torchaudio runtime version {EXPECTED_RUNTIME_VERSION}, "
            f"got {torchaudio_module.__version__}"
        )
    if torch_module.version.cuda != EXPECTED_CUDA_VERSION:
        raise RuntimeError(
            f"expected CUDA-enabled torch for CUDA {EXPECTED_CUDA_VERSION}, "
            f"got torch.version.cuda={torch_module.version.cuda!r}"
        )


def main() -> None:
    torch_module = cast(TorchRuntime, importlib.import_module("torch"))
    torchaudio_module = cast(
        TorchAudioRuntime,
        importlib.import_module("torchaudio"),
    )

    verify_torch_runtime(torch_module, torchaudio_module)


if __name__ == "__main__":
    main()
