from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator


class OCRRequest(BaseModel):
    key: list[str]
    value: list[str]
    enable_formula: StrictBool = False

    model_config = {"extra": "ignore"}


class OCRResultItem(BaseModel):
    text: str
    confidence: float
    text_region: list[list[int]]


FormulaPoint = Annotated[
    list[StrictInt],
    Field(min_length=2, max_length=2),
]
FormulaRegion = Annotated[
    list[FormulaPoint],
    Field(min_length=4, max_length=4),
]


class FormulaResultItem(BaseModel):
    latex: Annotated[str, Field(min_length=1)]
    formula_region: FormulaRegion
    detection_confidence: Annotated[
        float,
        Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False),
    ]

    @field_validator("latex")
    @classmethod
    def validate_latex(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("latex 不能为空")
        return value


class FormulaImageResult(BaseModel):
    image_id: str
    status: Literal["success", "disabled", "error"]
    message: str
    formulas: list[FormulaResultItem] = Field(default_factory=list)


class OCRResponse(BaseModel):
    err_no: int = 0
    err_msg: str = ""
    key: list[str] = Field(default_factory=lambda: ["results"])
    value: list[str] = Field(default_factory=lambda: ["[]"])
    formula_results: list[FormulaImageResult] = Field(default_factory=list)

    @classmethod
    def error(cls, err_no: int, message: str) -> "OCRResponse":
        return cls(err_no=err_no, err_msg=message)
