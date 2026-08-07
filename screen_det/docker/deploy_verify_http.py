#!/usr/bin/env python3
"""HTTP 部署验收：test/tilt_img、test/ok_img、test/error_img"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def post_json(base_url: str, path: str, payload: dict, timeout: float = 120) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def post_raw(base_url: str, path: str, b64: str, timeout: float = 120) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=b64.encode(),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def image_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png"))


def check_tilt_folder(base_url: str, folder: Path, report: Path) -> tuple[int, int]:
    rows = []
    ok = fail = 0
    for p in image_files(folder):
        b64 = base64.b64encode(p.read_bytes()).decode()
        try:
            status, data = post_json(base_url, "/detect_tilt", {"images": b64})
            good = (
                status == 200
                and data.get("code") == 200
                and "is_tilted" in data.get("result", {})
                and "tilt_threshold" in data
            )
            rows.append({"file": p.name, "ok": good, "angle": data.get("result", {}).get("angle")})
            ok += good
            fail += not good
        except Exception as exc:
            rows.append({"file": p.name, "ok": False, "error": str(exc)})
            fail += 1
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fail


def check_screen_folder(base_url: str, folder: Path, report: Path) -> tuple[int, int]:
    rows = []
    ok = fail = 0
    for p in image_files(folder):
        b64 = base64.b64encode(p.read_bytes()).decode()
        try:
            status, data = post_json(base_url, "/detect_screen", {"images": b64, "conf": 0.25})
            r0 = (data.get("results") or [{}])[0]
            primary = r0.get("primary")
            good = status == 200 and data.get("code") == 200 and (
                primary is None or "label" in primary
            )
            label = primary.get("label") if primary else None
            rows.append({"file": p.name, "ok": good, "label": label})
            ok += good
            fail += not good
        except Exception as exc:
            rows.append({"file": p.name, "ok": False, "error": str(exc)})
            fail += 1
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fail


def check_quality_abnormal_routes(base_url: str, root: Path, report: Path) -> tuple[int, int]:
    sample = root / "test/图像检测/画面异常/偏色/偏色1.png"
    if not sample.exists():
        report.write_text(
            json.dumps({"error": f"missing sample {sample}"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0, 1

    rows = []
    ok = fail = 0
    b64 = base64.b64encode(sample.read_bytes()).decode()
    for path in ("/detect_quality_abnormal",):
        try:
            status, data = post_json(base_url, path, {"image": b64})
            good = (
                status == 200
                and data.get("code") == 200
                and data.get("is_abnormal") is True
                and 2 in data.get("abnormal_types", [])
                and all(item.get("type") in data.get("abnormal_types", []) for item in data.get("results", []))
            )
            rows.append({"path": path, "ok": good, "response": data})
            ok += good
            fail += not good
        except Exception as exc:
            rows.append({"path": path, "ok": False, "error": str(exc)})
            fail += 1
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fail


def check_occlusion_routes(base_url: str, root: Path, report: Path) -> tuple[int, int]:
    sample = root / "test/图像检测/遮挡/横幅遮挡.png"
    if not sample.exists():
        report.write_text(
            json.dumps({"error": f"missing sample {sample}"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0, 1

    rows = []
    ok = fail = 0
    b64 = base64.b64encode(sample.read_bytes()).decode()
    for path in ("/detect_occlusion",):
        try:
            status, data = post_json(base_url, path, {"image": b64})
            good = (
                status == 200
                and data.get("code") == 200
                and data.get("is_occluded") is True
                and data.get("occlusion_area_ratio", 0) > 0
                and 0 <= data.get("score", -1) <= 1
                and 0 <= data.get("threshold", -1) <= 1
                and 0 <= data.get("area_ratio", -1) <= 1
            )
            rows.append({"path": path, "ok": good, "response": data})
            ok += good
            fail += not good
        except Exception as exc:
            rows.append({"path": path, "ok": False, "error": str(exc)})
            fail += 1
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fail


def check_detect_all_routes(base_url: str, root: Path, report: Path) -> tuple[int, int]:
    sample = root / "test/ok_img/snapshot_计算机科学导论.png"
    if not sample.exists():
        report.write_text(
            json.dumps({"error": f"missing sample {sample}"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0, 1

    rows = []
    ok = fail = 0
    b64 = base64.b64encode(sample.read_bytes()).decode()
    payload = {
        "image": b64,
        "include": ["tilt", "screen", "quality_abnormal", "occlusion"],
    }
    for path in ("/detect_all",):
        try:
            status, data = post_json(base_url, path, payload)
            good = (
                status == 200
                and data.get("code") == 200
                and "effective_params" in data
                and isinstance(data.get("problem_types"), list)
                and data.get("tilt") is not None
                and data.get("screen") is not None
                and data.get("quality_abnormal") is not None
                and data.get("occlusion") is not None
                and set(data.get("executed_modules", []))
                == {"tilt", "screen", "quality_abnormal", "occlusion"}
            )
            rows.append({"path": path, "ok": good, "response": data})
            ok += good
            fail += not good
        except Exception as exc:
            rows.append({"path": path, "ok": False, "error": str(exc)})
            fail += 1
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok, fail


def emit(status: str, name: str, detail: str) -> None:
    print(f"{status}\t{name}\t{detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("report_dir")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    report = Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]

    suites: list[tuple[str, int, int, str]] = []

    ok, fail = check_tilt_folder(base_url, root / "test/tilt_img", report / "tilt_img.json")
    suites.append(("suite:tilt_img", ok, fail, f"{ok+fail} images"))

    ok, fail = check_screen_folder(base_url, root / "test/ok_img", report / "screen_ok.json")
    suites.append(("suite:screen_ok_img", ok, fail, f"{ok+fail} images"))

    ok, fail = check_screen_folder(base_url, root / "test/error_img", report / "screen_error.json")
    suites.append(("suite:screen_error_img", ok, fail, f"{ok+fail} images"))

    ok, fail = check_quality_abnormal_routes(base_url, root, report / "quality_abnormal_routes.json")
    suites.append(("suite:quality_abnormal_routes", ok, fail, "2 routes"))

    ok, fail = check_occlusion_routes(base_url, root, report / "occlusion_routes.json")
    suites.append(("suite:occlusion_routes", ok, fail, "2 routes"))

    ok, fail = check_detect_all_routes(base_url, root, report / "detect_all_routes.json")
    suites.append(("suite:detect_all_routes", ok, fail, "2 routes"))

    text0 = root / "test/tilt_img/text0.jpg"
    extra_ok = 0
    if text0.exists():
        b64 = base64.b64encode(text0.read_bytes()).decode()
        try:
            d1 = post_json(base_url, "/detect_tilt", {"images": b64})[1]
            d2 = post_json(base_url, "/detect_tilt", {"images": b64, "tilt_threshold": 0.5})[1]
            d3 = post_raw(base_url, "/detect_tilt", b64)[1]
            (report / "tilt_extra.json").write_text(
                json.dumps({"default": d1, "threshold": d2, "plain": d3}, ensure_ascii=False, indent=2)
            )
            extra_ok = sum(1 for d in (d1, d2, d3) if d.get("code") == 200)
            suites.append(("suite:tilt_extra", extra_ok, 3 - extra_ok, "3 cases"))
        except Exception as exc:
            suites.append(("suite:tilt_extra", 0, 1, str(exc)))

    ok_imgs = image_files(root / "test/ok_img")[:2]
    if len(ok_imgs) >= 2:
        try:
            payload = {"images": [base64.b64encode(p.read_bytes()).decode() for p in ok_imgs]}
            batch = post_json(base_url, "/detect_screen", payload)[1]
            (report / "screen_batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2))
            good = batch.get("code") == 200 and batch.get("total") == 2
            suites.append(("suite:screen_batch", int(good), int(not good), "2 images"))
        except Exception as exc:
            suites.append(("suite:screen_batch", 0, 1, str(exc)))

    try:
        post_json(base_url, "/detect_tilt", {"images": ""})
        suites.append(("suite:error_400", 0, 1, "expected 400"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode())
        good = exc.code == 400 and body.get("code") == 400
        suites.append(("suite:error_400", int(good), int(not good), "empty images"))

    if args.summary_only:
        for name, ok_c, fail_c, detail in suites:
            if fail_c == 0 and ok_c > 0:
                emit("PASS", name, detail)
            else:
                emit("FAIL", name, f"ok={ok_c} fail={fail_c} {detail}")
    else:
        for name, ok_c, fail_c, detail in suites:
            print(f"{name}: ok={ok_c} fail={fail_c} ({detail})")

    return 0 if all(s[2] == 0 and s[1] > 0 for s in suites) else 1


if __name__ == "__main__":
    sys.exit(main())
