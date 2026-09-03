from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "deploy/scripts/online_vbas_recovery_probe.py"
)


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("online_vbas_recovery_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payloads_keep_the_three_operator_contracts_separate() -> None:
    module = _load_module()
    person = module._payload("person-count", "encoded", "person-1")
    student = module._payload("student", "encoded", "student-1")

    assert person["ImageList"] == [{"ImageID": "person-1", "Data": "encoded"}]
    assert student["ImageList"][0]["ImageId"] == "student-1"
    assert student["ImageList"][0]["StoragePath"] == "encoded"
    assert "Data" not in student["ImageList"][0]


def test_result_code_supports_operator_and_gateway_failures() -> None:
    module = _load_module()

    assert module._result_code("person-count", {"Response": {"ErrCode": 0}}) == "0"
    assert module._result_code("student", {"StatusObject": {"StatusCode": 0}}) == "0"
    assert module._result_code("teacher", {"code": 50201}) == "50201"
