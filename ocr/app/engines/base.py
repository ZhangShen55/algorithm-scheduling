from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class EngineResult:
    text: str
    confidence: float
    text_region: list[list[int]]


@dataclass(frozen=True)
class FormulaEngineResult:
    latex: str
    formula_region: list[list[int]]
    detection_confidence: float


class OCREngine(Protocol):
    def predict(self, image: Image.Image) -> list[EngineResult]: ...

    def close(self) -> None: ...


class FormulaEngine(Protocol):
    def predict(self, image: Image.Image) -> list[FormulaEngineResult]: ...

    def close(self) -> None: ...
