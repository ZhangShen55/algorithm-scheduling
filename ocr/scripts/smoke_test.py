from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
from typing import Any

import httpx


VERSION_FIELDS = {
    "status",
    "AppVersion",
    "AppStartTime",
    "NowTime",
    "RunTime",
    "Memory usage",
    "GPU usage",
    "Total_RegProcess_Tasks",
    "Total_DetectProcess_Tasks",
}


class SmokeTestError(RuntimeError):
    pass


def validate_version(payload: dict[str, Any]) -> None:
    missing = sorted(VERSION_FIELDS.difference(payload))
    if missing:
        raise SmokeTestError(f"版本接口缺少字段：{', '.join(missing)}")
    if payload["status"] != "success":
        raise SmokeTestError("版本接口状态不是 success")


def validate_prediction(
    payload: dict[str, Any],
    expected_keys: list[str],
    require_formula: bool = False,
) -> None:
    if payload.get("err_no") != 0:
        raise SmokeTestError(f"OCR 接口返回错误：{payload.get('err_msg', '未知错误')}")
    if payload.get("key") != expected_keys:
        raise SmokeTestError("OCR 接口未按请求顺序返回图片 ID")
    values = payload.get("value")
    if not isinstance(values, list) or len(values) != len(expected_keys):
        raise SmokeTestError("OCR 接口 key 和 value 数量不一致")
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise SmokeTestError(f"OCR 接口 value[{index}] 必须是 JSON 字符串")
        try:
            results = json.loads(value)
        except json.JSONDecodeError as error:
            raise SmokeTestError(
                f"OCR 接口 value[{index}] 不是有效 JSON 字符串"
            ) from error
        if not isinstance(results, list):
            raise SmokeTestError(f"OCR 接口 value[{index}] 必须表示结果数组")
    formula_results = payload.get("formula_results")
    if not require_formula:
        if formula_results != []:
            raise SmokeTestError("未请求公式识别时 formula_results 必须为空数组")
        return
    if not isinstance(formula_results, list) or len(formula_results) != len(
        expected_keys
    ):
        raise SmokeTestError("公式结果数量与图片 ID 数量不一致")
    for image_id, result in zip(expected_keys, formula_results):
        if not isinstance(result, dict) or result.get("image_id") != image_id:
            raise SmokeTestError("公式结果未按请求顺序返回图片 ID")
        if result.get("status") == "disabled":
            raise SmokeTestError("服务端未启用公式识别功能")
        if result.get("status") != "success":
            raise SmokeTestError(f"图片 {image_id} 公式识别失败")
        if not isinstance(result.get("formulas"), list):
            raise SmokeTestError(f"图片 {image_id} 的 formulas 必须是数组")


def run(
    base_url: str,
    image_path: Path,
    timeout: float,
    enable_formula: bool = False,
) -> None:
    if not image_path.is_file():
        raise SmokeTestError(f"测试图片不存在：{image_path}")
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        version_response = client.get("/ocr/getVersion")
        version_response.raise_for_status()
        validate_version(version_response.json())

        image_ids = [image_path.stem]
        request = {"key": image_ids, "value": [image_base64]}
        if enable_formula:
            request["enable_formula"] = True
        prediction_response = client.post(
            "/ocr/prediction",
            json=request,
        )
        prediction_response.raise_for_status()
        validate_prediction(
            prediction_response.json(),
            expected_keys=image_ids,
            require_formula=enable_formula,
        )


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="验证 OCR 服务接口")
    parser.add_argument("--base-url", default="http://127.0.0.1:8866")
    parser.add_argument(
        "--image",
        type=Path,
        default=project_root / "tests" / "fixtures" / "ocr-test.jpg",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--enable-formula",
        action="store_true",
        help="要求服务执行公式识别并校验成功状态",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args.base_url, args.image, args.timeout, args.enable_formula)
    except (SmokeTestError, httpx.HTTPError, ValueError, OSError) as error:
        print(f"服务冒烟测试失败：{error}", file=sys.stderr)
        return 1
    print("服务冒烟测试通过：版本接口和 OCR 接口响应正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
