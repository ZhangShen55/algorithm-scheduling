import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib import request as urlrequest


PUNCTUATION_PATTERN = re.compile(r"[\s，。！？；：、,.!?;:]")
ALLOWED_TYPES = {"t_s", "s_s"}


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def evidence_in_segments(evidence: str, time_range: Dict[str, Any], segments: List[Dict[str, Any]]) -> bool:
    try:
        start = float(time_range["start"])
        end = float(time_range["end"])
    except (KeyError, TypeError, ValueError):
        return False
    text = "".join(
        str(seg.get("text", ""))
        for seg in segments
        if float(seg.get("ed", 0)) >= start and float(seg.get("bg", 0)) <= end
    )
    if evidence in text:
        return True
    return PUNCTUATION_PATTERN.sub("", evidence) in PUNCTUATION_PATTERN.sub("", text)


def overlaps(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return left_start < right_end and left_end > right_start


def validate_interaction_result(
    result: Dict[str, Any],
    *,
    segments: List[Dict[str, Any]],
    course_start: float,
    course_end: float,
    breaks: List[Dict[str, Any]],
) -> None:
    interactions = result.get("interactions")
    if not isinstance(interactions, list):
        raise AssertionError("result.interactions must be list")

    for item in interactions:
        if not isinstance(item, dict):
            raise AssertionError("interaction item must be object")
        if set(item.keys()) != {"type", "time_range", "summary", "evidence"}:
            raise AssertionError(f"unexpected interaction keys: {item.keys()}")
        if item.get("type") not in ALLOWED_TYPES:
            raise AssertionError(f"invalid interaction type: {item.get('type')}")
        if not str(item.get("summary", "")).strip():
            raise AssertionError("interaction summary must not be empty")
        evidence = str(item.get("evidence", "")).strip()
        if not evidence:
            raise AssertionError("interaction evidence must not be empty")
        time_range = item.get("time_range") or {}
        try:
            start = float(time_range["start"])
            end = float(time_range["end"])
        except (KeyError, TypeError, ValueError):
            raise AssertionError(f"invalid time_range: {time_range}") from None
        if start >= end:
            raise AssertionError(f"time_range.start must be less than end: {time_range}")
        if start < float(course_start) or end > float(course_end):
            raise AssertionError(f"time_range outside course range: {time_range}")
        for br in breaks:
            try:
                br_start = float(br["start"])
                br_end = float(br["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if overlaps(start, end, br_start, br_end):
                raise AssertionError(f"time_range overlaps break: time_range={time_range} break={br}")
        if not evidence_in_segments(evidence, time_range, segments):
            raise AssertionError(f"evidence not found in time_range: evidence={evidence[:80]} time_range={time_range}")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证学生互动分析接口")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fixtures", default="tests/text_segments")
    args = parser.parse_args()

    fixture_dir = Path(args.fixtures)
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"no fixture files found: {fixture_dir}")

    for path in files:
        print(f"{path.name}: start", flush=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = payload.get("textSegments") or []
        time_resp = post_json(f"{args.base_url}/v1/course_time_analysis", payload)
        time_result = time_resp.get("result") or {}
        course_start = (time_result.get("course_start") or {}).get("time")
        course_end = (time_result.get("course_end") or {}).get("time")
        breaks = [
            {"start": item.get("start"), "end": item.get("end")}
            for item in time_result.get("breaks") or []
        ]
        req = {
            "textSegments": segments,
            "course_start": course_start,
            "course_end": course_end,
            "breaks": breaks,
        }
        interaction_resp = post_json(f"{args.base_url}/v1/student_interaction_analysis", req)
        result = interaction_resp.get("result") or {}
        validate_interaction_result(
            result,
            segments=segments,
            course_start=float(course_start),
            course_end=float(course_end),
            breaks=breaks,
        )
        print(f"{path.name}: interactions={len(result.get('interactions') or [])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
