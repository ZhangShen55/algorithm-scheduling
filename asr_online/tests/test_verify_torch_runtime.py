from __future__ import annotations

import importlib.util
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "docker/verify_torch_runtime.py"


def _load_verifier() -> Callable[[Any, Any], None]:
    assert SCRIPT_PATH.is_file(), f"runtime verifier is missing: {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("verify_torch_runtime", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, ModuleType)
    return cast(
        Callable[[Any, Any], None],
        module.verify_torch_runtime,
    )


def _torch(version: str = "2.6.0+cu124", cuda: str | None = "12.4") -> Any:
    return SimpleNamespace(__version__=version, version=SimpleNamespace(cuda=cuda))


def _torchaudio(version: str = "2.6.0+cu124") -> Any:
    return SimpleNamespace(__version__=version)


class VerifyTorchRuntimeTests(unittest.TestCase):
    def test_accepts_the_approved_cuda_runtime_pair(self) -> None:
        verify_torch_runtime = _load_verifier()

        verify_torch_runtime(_torch(), _torchaudio())

    def test_rejects_a_cpu_torch_build(self) -> None:
        verify_torch_runtime = _load_verifier()

        with self.assertRaisesRegex(RuntimeError, "CUDA 12.4"):
            verify_torch_runtime(_torch(cuda=None), _torchaudio())

    def test_rejects_runtime_version_mismatches(self) -> None:
        verify_torch_runtime = _load_verifier()
        cases = (
            (_torch(version="2.6.0+cpu"), _torchaudio(), "torch"),
            (_torch(), _torchaudio(version="2.6.0+cpu"), "torchaudio"),
        )

        for torch_module, torchaudio_module, package in cases:
            with (
                self.subTest(package=package),
                self.assertRaisesRegex(RuntimeError, package),
            ):
                verify_torch_runtime(torch_module, torchaudio_module)


if __name__ == "__main__":
    unittest.main()
