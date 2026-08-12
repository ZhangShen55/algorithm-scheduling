#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import uuid
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
DEFAULT_CASES = PLATFORM_ROOT / "deploy" / "operator-smoke-cases.json"
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
FIXTURE_FIELDS = {"fixture_id", "source_kind", "source", "server_target", "bytes", "sha256"}
CASE_FIELDS = {"case_id", "operator_code", "fixtures", "checks"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直接调用八类算子的 Smoke Harness")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--external-fixture-root", type=Path, required=True)
    parser.add_argument("--fixture-target-root", type=Path, required=True)
    parser.add_argument("--endpoints-json", required=True)
    parser.add_argument("--cases", default="all")
    parser.add_argument("--case-manifest", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--mock", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_component(value: str, pattern: re.Pattern[str], name: str) -> str:
    if value in {".", ".."} or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} 不是安全的单路径段")
    return value


def reject_symlink_chain(path: Path, name: str) -> None:
    absolute = path.absolute()
    for candidate in (*reversed(absolute.parents), absolute):
        if (
            candidate == Path(candidate.anchor)
            or not candidate.exists()
            and not candidate.is_symlink()
        ):
            continue
        metadata = os.lstat(candidate)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{name} 不能包含软链接: {candidate}")


def safe_relative(value: str, name: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{name} 必须是安全的仓库相对路径")
    return Path(*relative.parts)


def inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved == resolved_root or resolved_root in resolved.parents


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_chain(path.parent, "报告目录")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        if path.exists():
            if path.read_bytes() == content:
                temporary.unlink()
                return
            raise ValueError(f"拒绝覆盖不同运行证据: {path}")
        os.link(temporary, path)
        temporary.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def load_cases(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "cases"} or document["schema_version"] != 1:
        raise ValueError("Smoke case manifest schema 不受支持")
    cases = document["cases"]
    if not isinstance(cases, list) or len(cases) != 8:
        raise ValueError("Smoke case manifest 必须精确包含八类算子")
    codes: set[str] = set()
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            raise ValueError("Smoke case manifest 包含未知或缺失字段")
        if case["case_id"] in ids or case["operator_code"] in codes:
            raise ValueError("Smoke case_id 或 operator_code 重复")
        if not isinstance(case["checks"], list) or not case["checks"]:
            raise ValueError("每个 Smoke case 必须声明 checks")
        ids.add(case["case_id"])
        codes.add(case["operator_code"])
    return cases


def hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def load_and_stage_fixtures(
    manifest_path: Path,
    external_root: Path,
    target_root: Path,
) -> tuple[dict[str, Path], dict[str, str]]:
    reject_symlink_chain(manifest_path, "fixture manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        set(manifest) != {"schema_version", "fixtures", "missing_fixtures"}
        or manifest["schema_version"] != 1
    ):
        raise ValueError("fixture manifest schema 不受支持")
    fixtures = manifest["fixtures"]
    if not isinstance(fixtures, list):
        raise ValueError("fixture manifest fixtures 必须是数组")
    staged: dict[str, Path] = {}
    missing: dict[str, str] = {}
    missing_entries = manifest["missing_fixtures"]
    if not isinstance(missing_entries, list):
        raise ValueError("fixture manifest missing_fixtures 必须是数组")
    for item in missing_entries:
        if not isinstance(item, dict) or set(item) != {"fixture_id", "reason"}:
            raise ValueError("missing fixture 包含未知或缺失字段")
        fixture_id = safe_component(str(item["fixture_id"]), TAG_PATTERN, "fixture_id")
        reason = str(item["reason"]).strip()
        if not reason or fixture_id in missing:
            raise ValueError("missing fixture 原因为空或 fixture_id 重复")
        missing[fixture_id] = reason
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_chain(target_root, "fixture 目标根")
    for item in fixtures:
        if not isinstance(item, dict) or set(item) != FIXTURE_FIELDS:
            raise ValueError("fixture manifest 包含未知或缺失字段")
        fixture_id = safe_component(str(item["fixture_id"]), TAG_PATTERN, "fixture_id")
        if fixture_id in staged:
            raise ValueError(f"fixture_id 重复: {fixture_id}")
        source_kind = item["source_kind"]
        relative = safe_relative(str(item["source"]), "fixture source")
        if source_kind == "external":
            source = external_root / relative
        elif source_kind == "repository":
            source = WORKSPACE_ROOT / relative
            if not inside(source, WORKSPACE_ROOT):
                raise ValueError("仓库 fixture 越出工作区")
        else:
            raise ValueError(f"未知 fixture source_kind: {source_kind}")
        reject_symlink_chain(source, "fixture source")
        metadata = os.lstat(source)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"fixture source 不是普通文件: {fixture_id}")
        size, digest = hash_file(source)
        if size != item["bytes"]:
            raise ValueError(f"fixture 字节数不匹配: {fixture_id}")
        if digest != item["sha256"]:
            raise ValueError(f"fixture SHA-256 不匹配: {fixture_id}")
        destination = Path(str(item["server_target"]))
        if not destination.is_absolute() or not inside(destination, target_root):
            raise ValueError(f"fixture server_target 越出目标根: {fixture_id}")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        reject_symlink_chain(destination.parent, "fixture 目标目录")
        if destination.exists():
            existing_size, existing_digest = hash_file(destination)
            if (existing_size, existing_digest) != (size, digest):
                raise ValueError(f"fixture 目标已存在不同内容: {fixture_id}")
        else:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.", dir=destination.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            try:
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        staged[fixture_id] = destination
    if set(staged) & set(missing):
        raise ValueError("fixture 不能同时声明为可用和缺失")
    return staged, missing


def data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
        suffix, "application/octet-stream"
    )
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


class CallbackCapture:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.event = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError
                    owner.payload = value
                    owner.event.set()
                    self.send_response(204)
                    self.end_headers()
                except (ValueError, json.JSONDecodeError):
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, *_: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> CallbackCapture:
        self.thread.start()
        return self

    @property
    def url(self) -> str:
        address = self.server.server_address
        host = str(address[0])
        port = int(address[1])
        return f"http://{host}:{port}/terminal"

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()


def require_http(response: httpx.Response, name: str) -> dict[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"{name} HTTP {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} 响应不是 JSON 对象")
    return value


def smoke_asr_offline(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], _: float
) -> dict[str, Any]:
    path = fixtures["asr_offline_audio"]
    with path.open("rb") as stream:
        response = http.post(
            endpoint.rstrip("/") + "/v1.1.8/seacraft_asr",
            files={"audioFile": (path.name, stream, "audio/wav")},
            data={
                "language": "auto",
                "showSpk": "true",
                "showEmotion": "true",
                "showRoleIdentify": "false",
                "wordTimestamps": "false",
            },
        )
    body = require_http(response, "ASR Offline")
    if body.get("code") not in {None, 0} or not body.get("text") or not body.get("segments"):
        raise RuntimeError("ASR Offline 未返回非空 text/segments")
    return {"segment_count": len(body["segments"]), "text_non_empty": True}


async def _online(endpoint: str, audio: Path, timeout: float) -> dict[str, Any]:
    with wave.open(str(audio), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError("ASR Online fixture 必须是 16kHz 单声道 PCM16 WAV")
        pcm = stream.readframes(stream.getnframes())
    url = endpoint.rstrip("/") + "/v1.0.1/seacraft_asr_online"
    texts: list[str] = []
    async with websockets.connect(url, open_timeout=timeout, close_timeout=timeout) as socket:
        for offset in range(0, len(pcm), 7680 * 2):
            await socket.send(pcm[offset : offset + 7680 * 2])
            response = json.loads(await asyncio.wait_for(socket.recv(), timeout))
            if response.get("text"):
                texts.append(str(response["text"]))
            if texts:
                break
    if not texts:
        raise RuntimeError("ASR Online 未返回增量文本")
    return {"incremental_messages": len(texts), "text_non_empty": True}


def smoke_asr_online(
    _: httpx.Client, endpoint: str, fixtures: dict[str, Path], timeout: float
) -> dict[str, Any]:
    return asyncio.run(_online(endpoint, fixtures["asr_online_audio"], timeout))


def smoke_ocr(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], _: float
) -> dict[str, Any]:
    body = require_http(
        http.post(
            endpoint.rstrip("/") + "/ocr/prediction",
            json={
                "key": ["smoke-image"],
                "value": [base64.b64encode(fixtures["ocr_image"].read_bytes()).decode()],
                "enable_formula": False,
            },
        ),
        "OCR",
    )
    if body.get("err_no") != 0 or len(body.get("value") or []) != 1:
        raise RuntimeError("OCR 响应合同失败")
    parsed = json.loads(body["value"][0])
    if not parsed or not any(item.get("text") for item in parsed):
        raise RuntimeError("OCR 未返回文本")
    return {"image_count": 1, "text_items": len(parsed)}


def smoke_vbas(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], _: float
) -> dict[str, Any]:
    payload = {
        "ImageList": [{"StoragePath": data_url(fixtures["vbas_image"]), "ImageId": "smoke-frame"}],
        "task_id": "smoke-vbas",
        "batch_id": "smoke-batch",
    }
    checks = {}
    for role in ("student", "teacher"):
        body = require_http(
            http.post(endpoint.rstrip("/") + f"/ImageDetect/{role}/v1.0.0", json=payload),
            f"VBas {role}",
        )
        if not isinstance(body.get("DataList"), list):
            raise RuntimeError(f"VBas {role} 未返回 DataList")
        checks[role] = len(body["DataList"])
    return checks


def smoke_facerec(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], _: float
) -> dict[str, Any]:
    image = data_url(fixtures["facerec_image"])
    number = "harness-person"
    created = False
    cleanup_ok = False
    try:
        created_body = require_http(
            http.post(
                endpoint.rstrip("/") + "/persons",
                json={"photo": image, "name": "Harness", "number": number},
            ),
            "FaceRec persons",
        )
        if created_body.get("status_code") != 200:
            raise RuntimeError("FaceRec 人物创建失败")
        data = created_body.get("data") or {}
        if data.get("photo_path") not in {None, ""}:
            raise RuntimeError("FaceRec save_person_photo=false 未生效")
        created = True
        recognized = require_http(
            http.post(
                endpoint.rstrip("/") + "/recognize", json={"photo": image, "targets": [number]}
            ),
            "FaceRec recognize",
        )
        if recognized.get("status_code") != 200:
            raise RuntimeError("FaceRec 未识别到已创建人物")
        return {"created": True, "recognized": True, "photo_saved": False, "cleanup": True}
    finally:
        if created:
            cleanup = require_http(
                http.request(
                    "DELETE", endpoint.rstrip("/") + "/persons/delete", json={"number": number}
                ),
                "FaceRec cleanup",
            )
            cleanup_ok = cleanup.get("status_code") == 200
            if not cleanup_ok:
                raise RuntimeError("FaceRec 测试人物清理失败")


def smoke_screen_det(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], _: float
) -> dict[str, Any]:
    body = require_http(
        http.post(
            endpoint.rstrip("/") + "/detect_all",
            json={"image": base64.b64encode(fixtures["screen_det_image"].read_bytes()).decode()},
        ),
        "ScreenDet",
    )
    if body.get("code") != 200 or not isinstance(body.get("executed_modules"), list):
        raise RuntimeError("ScreenDet detect_all 响应合同失败")
    return {
        "executed_modules": body["executed_modules"],
        "failed_module_count": len(body.get("failed_modules") or []),
    }


def smoke_ppt(
    http: httpx.Client, endpoint: str, fixtures: dict[str, Path], timeout: float
) -> dict[str, Any]:
    operator_task_id = "smoke-ppt-" + uuid.uuid4().hex
    with CallbackCapture() as callback:
        accepted = require_http(
            http.post(
                endpoint.rstrip("/") + "/LocalVideoPPTSliceTasks/v1.0.0",
                json={
                    "video_path": str(fixtures["ppt_video"]),
                    "task_id": "smoke-ppt",
                    "operator_task_id": operator_task_id,
                    "result_callback_uri": callback.url,
                    "threshold": 0.98,
                },
            ),
            "PPT Slice",
        )
        if accepted.get("status") != 50:
            raise RuntimeError("PPT Slice 未受理任务")
        if not callback.event.wait(timeout):
            raise RuntimeError("PPT Slice status 50 后未收到终态回调")
        terminal = callback.payload or {}
        if terminal.get("status") != 60 or terminal.get("operator_task_id") != operator_task_id:
            raise RuntimeError("PPT Slice 终态回调不是成功终态")
        manifest = Path(str(terminal.get("manifest_path", "")))
        reject_symlink_chain(manifest, "PPT manifest")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        count = terminal.get("count")
        slides = payload.get("slides") if isinstance(payload, dict) else None
        if (
            not isinstance(count, int)
            or count < 0
            or not isinstance(slides, list)
            or len(slides) != count
        ):
            raise RuntimeError("PPT manifest 与终态 count 不一致")
        return {"terminal_status": 60, "slide_count": count, "manifest_verified": True}


def smoke_text(http: httpx.Client, endpoint: str, _: dict[str, Path], __: float) -> dict[str, Any]:
    keywords = require_http(
        http.post(
            endpoint.rstrip("/") + "/v1/extract_keywords", json={"text": "函数与图像课堂内容"}
        ),
        "Text Analysis keywords",
    )
    overview = require_http(
        http.post(
            endpoint.rstrip("/") + "/v1/course_overviews",
            json={
                "textSegments": [{"segment_text": "函数课程", "bg": 0, "ed": 10, "role": "teacher"}]
            },
        ),
        "Text Analysis overview",
    )
    if not (keywords.get("result") and overview.get("result")):
        raise RuntimeError("Text Analysis 两个接口未返回结果")
    return {"extract_keywords": True, "course_overviews": True}


RUNNERS: dict[str, Callable[[httpx.Client, str, dict[str, Path], float], dict[str, Any]]] = {
    "asr_offline": smoke_asr_offline,
    "asr_online": smoke_asr_online,
    "ocr": smoke_ocr,
    "vbas": smoke_vbas,
    "facerec": smoke_facerec,
    "screen_det": smoke_screen_det,
    "ppt_slice": smoke_ppt,
    "text_analysis": smoke_text,
}


def make_case(
    case: dict[str, Any],
    *,
    status: str,
    started: str,
    finished: str,
    reason: str,
    evidence: list[str],
    mock: bool,
    tag: str,
    sha: str,
) -> dict[str, Any]:
    code = case["operator_code"]
    return {
        "case_id": case["case_id"],
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "target": code,
        "command": f"deploy/scripts/run-operator-smoke --cases {code}",
        "evidence": evidence,
        "reason": reason,
        "mock": mock,
        "release_tag": tag,
        "git_sha": sha,
    }


def main() -> int:
    args = parse_args()
    try:
        tag = safe_component(args.release_tag, TAG_PATTERN, "release tag")
        sha = safe_component(args.git_sha.lower(), SHA_PATTERN, "Git SHA")
        if args.timeout_seconds <= 0:
            raise ValueError("命令超时必须大于 0")
        endpoints = json.loads(args.endpoints_json)
        if not isinstance(endpoints, dict):
            raise ValueError("endpoints-json 必须是对象")
        all_cases = load_cases(args.case_manifest)
        selected_codes = set(RUNNERS) if args.cases == "all" else set(args.cases.split(","))
        unknown = selected_codes - set(RUNNERS)
        if unknown:
            raise ValueError(f"未知 Smoke case: {sorted(unknown)}")
        selected = [case for case in all_cases if case["operator_code"] in selected_codes]
        for case in selected:
            endpoint = endpoints.get(case["operator_code"])
            parsed = urlsplit(str(endpoint))
            allowed = {"ws", "wss"} if case["operator_code"] == "asr_online" else {"http", "https"}
            if parsed.scheme not in allowed or not parsed.netloc:
                raise ValueError(f"{case['operator_code']} endpoint 协议不合法")
        fixtures, missing_fixtures = load_and_stage_fixtures(
            args.fixture_manifest,
            args.external_fixture_root,
            args.fixture_target_root,
        )
        needed = {fixture for case in selected for fixture in case["fixtures"]}
        undeclared = needed - set(fixtures) - set(missing_fixtures)
        if undeclared:
            raise ValueError(f"fixture manifest 未声明: {sorted(undeclared)}")
        smoke_root = args.reports_root / "milestone-2b" / "releases" / tag / sha / "smoke"
        results: list[dict[str, Any]] = []
        failed = False
        with httpx.Client(timeout=args.timeout_seconds, follow_redirects=False) as http:
            for case in selected:
                started = utc_now()
                code = case["operator_code"]
                evidence_path = smoke_root / f"{code}.json"
                relative = f"smoke/{code}.json"
                unavailable = [
                    missing_fixtures[fixture]
                    for fixture in case["fixtures"]
                    if fixture in missing_fixtures
                ]
                if unavailable:
                    failed = True
                    reason = "；".join(unavailable)
                    atomic_json(
                        evidence_path,
                        {
                            "schema_version": 1,
                            "operator_code": code,
                            "status": "未执行及原因",
                            "reason": reason,
                            "mock": args.mock,
                            "release_tag": tag,
                            "git_sha": sha,
                        },
                    )
                    results.append(
                        make_case(
                            case,
                            status="未执行及原因",
                            started=started,
                            finished=utc_now(),
                            reason=reason,
                            evidence=[],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                        )
                    )
                    print(f"{code} Smoke 未执行: {reason}", file=sys.stderr)
                    continue
                try:
                    summary = RUNNERS[code](
                        http, str(endpoints[code]), fixtures, args.timeout_seconds
                    )
                    evidence_payload = {
                        "schema_version": 1,
                        "operator_code": code,
                        "status": "通过",
                        "checks": case["checks"],
                        "summary": summary,
                        "mock": args.mock,
                        "release_tag": tag,
                        "git_sha": sha,
                    }
                    atomic_json(evidence_path, evidence_payload)
                    results.append(
                        make_case(
                            case,
                            status="通过",
                            started=started,
                            finished=utc_now(),
                            reason="直接调用响应符合算子合同",
                            evidence=[relative],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one failed case must not hide other evidence
                    failed = True
                    reason = str(exc)
                    atomic_json(
                        evidence_path,
                        {
                            "schema_version": 1,
                            "operator_code": code,
                            "status": "失败",
                            "reason": reason,
                            "mock": args.mock,
                            "release_tag": tag,
                            "git_sha": sha,
                        },
                    )
                    results.append(
                        make_case(
                            case,
                            status="失败",
                            started=started,
                            finished=utc_now(),
                            reason=reason,
                            evidence=[relative],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                        )
                    )
                    print(f"{code} Smoke 失败: {reason}", file=sys.stderr)
        atomic_json(smoke_root / "cases.json", results)
        return 1 if failed else 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"Smoke Harness 失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
