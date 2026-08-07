from copy import deepcopy
import math

from pydantic import ValidationError
import pytest

from app.schemas.ocr import FormulaResultItem, OCRRequest


VALID_FORMULA = {
    "latex": r"\frac{a}{b}",
    "formula_region": [[10, 20], [100, 20], [100, 50], [10, 50]],
    "detection_confidence": 0.96,
}


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("latex", ""),
        ("latex", "   "),
        ("formula_region", [[0, 0], [1, 0], [1, 1]]),
        ("formula_region", [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]),
        ("formula_region", [[0], [1, 0], [1, 1], [0, 1]]),
        ("formula_region", [[0, 0, 9], [1, 0], [1, 1], [0, 1]]),
        ("formula_region", [["0", 0], [1, 0], [1, 1], [0, 1]]),
        ("formula_region", [[0.0, 0], [1, 0], [1, 1], [0, 1]]),
        ("detection_confidence", -0.01),
        ("detection_confidence", 1.01),
        ("detection_confidence", math.nan),
        ("detection_confidence", math.inf),
        ("detection_confidence", -math.inf),
        ("detection_confidence", "0.96"),
        ("detection_confidence", True),
    ],
)
def test_formula_result_item_rejects_invalid_values(field, invalid_value):
    payload = deepcopy(VALID_FORMULA)
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        FormulaResultItem.model_validate(payload)


def test_formula_result_item_keeps_json_coordinates_as_arrays():
    item = FormulaResultItem.model_validate(VALID_FORMULA)

    assert item.model_dump(mode="json")["formula_region"] == [
        [10, 20],
        [100, 20],
        [100, 50],
        [10, 50],
    ]


@pytest.mark.parametrize("invalid_value", ["true", "false", 1, 0])
def test_ocr_request_rejects_non_boolean_formula_flag(invalid_value):
    with pytest.raises(ValidationError):
        OCRRequest.model_validate(
            {
                "key": ["image"],
                "value": ["data"],
                "enable_formula": invalid_value,
            }
        )
