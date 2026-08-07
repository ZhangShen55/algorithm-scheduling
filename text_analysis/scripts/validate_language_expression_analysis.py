import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib import request as urlrequest


DIMENSIONS = [
    "expression_coherence",
    "expression_ability",
    "contextual_understanding",
    "semantic_accuracy",
]
PUNCTUATION_PATTERN = re.compile(r"[\s，。！？；：、,.!?;:]")


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


def validate_language_result(result: Dict[str, Any], segments: List[Dict[str, Any]]) -> None:
    if "warnings" in result:
        raise AssertionError("result must not contain warnings")
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, dict):
        raise AssertionError("missing dimensions")
    if set(dimensions.keys()) != set(DIMENSIONS):
        raise AssertionError(f"unexpected dimensions: {dimensions.keys()}")
    overall = result.get("overall_score")
    if not isinstance(overall, (int, float)):
        raise AssertionError("overall_score must be numeric")
    for name in DIMENSIONS:
        dim = dimensions[name]
        score = dim.get("score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise AssertionError(f"{name} score out of range: {score}")
        for key in ("advantages", "problems"):
            items = dim.get(key)
            if not isinstance(items, list):
                raise AssertionError(f"{name}.{key} must be list")
            for item in items:
                for field in ("summary", "detail", "evidence", "time_range", "related_content"):
                    if field not in item:
                        raise AssertionError(f"{name}.{key} missing {field}")
                if not str(item.get("evidence", "")).strip():
                    raise AssertionError(f"{name}.{key} must not contain empty evidence placeholders")
                time_range = item.get("time_range", {})
                if time_range == {"start": 0.0, "end": 0.0} or time_range == {"start": 0, "end": 0}:
                    raise AssertionError(f"{name}.{key} must not contain placeholder time_range")
                if not evidence_in_segments(item.get("evidence", ""), item.get("time_range", {}), segments):
                    raise AssertionError(
                        f"{name}.{key} evidence not found in time_range: "
                        f"evidence={item.get('evidence', '')[:80]} time_range={item.get('time_range')}"
                    )


def main() -> int:
    parser = argparse.ArgumentParser(description="验证语言表达分析接口")
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
        req = {
            "textSegments": segments,
            "course_start": (time_result.get("course_start") or {}).get("time"),
            "course_end": (time_result.get("course_end") or {}).get("time"),
            "breaks": [
                {"start": item.get("start"), "end": item.get("end")}
                for item in time_result.get("breaks") or []
            ],
        }
        lang_resp = post_json(f"{args.base_url}/v1/language_expression_analysis", req)
        result = lang_resp.get("result") or {}
        validate_language_result(result, segments)
        execution = result.get("execution") or {}
        print(
            f"{path.name}: overall={result.get('overall_score')} "
            f"chunks={execution.get('succeeded_chunks')}/{execution.get('chunk_count')} "
            f"model={execution.get('model')} temperature={execution.get('temperature')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
