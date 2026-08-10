"""Generate machine-readable and Chinese reports for reviewed detections."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import numpy as np

from harness.tools.review import calculate_metrics
from harness.tools.corpus import canonical_url


CSV_FIELDS = [
    "course_name",
    "video_url",
    "duration_seconds",
    "start_ms",
    "end_ms",
    "type",
    "confidence",
    "evidence_path",
    "review_label",
    "notes",
    "algorithm_version",
    "config_summary",
]


def _time_text(milliseconds: int) -> str:
    seconds = milliseconds / 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _config_summary(config: dict) -> str:
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_report(inventory: dict, detections: dict, *, reviews: list[dict], audit_windows: list[dict]) -> dict:
    inventory_by_url = {
        canonical_url(item["url"], directory=False): item
        for item in inventory.get("items", [])
    }
    detection_items = detections.get("items")
    if detection_items is None and detections.get("url"):
        detection_items = [detections]
    detections_by_url = {
        canonical_url(item["url"], directory=False): {**item, "url": canonical_url(item["url"], directory=False)}
        for item in detection_items or []
    }
    review_by_key = {
        (
            item["url"],
            item["candidate_kind"],
            int(item["start_ms"]),
            int(item["end_ms"]),
        ): item
        for item in reviews
    }
    algorithm_version = detections.get("algorithm_version", "")
    config = detections.get("effective_config", {})
    config_summary = _config_summary(config)
    rows = []
    per_video_metrics = {}

    for url, detection in detections_by_url.items():
        inventory_item = inventory_by_url.get(url, {})
        video_reviews = [item for item in reviews if item["url"] == url]
        per_video_metrics[url] = calculate_metrics(
            detection.get("dynamic_segments", []),
            video_reviews,
        )
        for segment in detection.get("dynamic_segments", []):
            key = (url, "DETECTION", int(segment["start_ms"]), int(segment["end_ms"]))
            review = review_by_key.get(key, {})
            rows.append(
                {
                    "course_name": inventory_item.get("course_name", detection.get("course_name", "")),
                    "video_url": url,
                    "duration_seconds": inventory_item.get("duration"),
                    "start_ms": int(segment["start_ms"]),
                    "end_ms": int(segment["end_ms"]),
                    "type": segment.get("type", "SUSPECTED_VIDEO_PLAYBACK"),
                    "confidence": segment.get("confidence"),
                    "evidence_path": review.get("evidence_path", ""),
                    "review_label": review.get("label", "UNREVIEWED"),
                    "notes": review.get("notes", ""),
                    "algorithm_version": algorithm_version,
                    "config_summary": config_summary,
                }
            )

    for review in reviews:
        if review["candidate_kind"] != "AUDIT" or not review["label"].startswith("CONFIRMED_"):
            continue
        inventory_item = inventory_by_url.get(review["url"], {})
        rows.append(
            {
                "course_name": inventory_item.get("course_name", ""),
                "video_url": review["url"],
                "duration_seconds": inventory_item.get("duration"),
                "start_ms": int(review["start_ms"]),
                "end_ms": int(review["end_ms"]),
                "type": "MISSED_DETECTION",
                "confidence": "",
                "evidence_path": review.get("evidence_path", ""),
                "review_label": review["label"],
                "notes": review.get("notes", ""),
                "algorithm_version": algorithm_version,
                "config_summary": config_summary,
            }
        )
    rows.sort(key=lambda item: (item["course_name"], item["start_ms"]))

    total_predictions = sum(item["prediction_count"] for item in per_video_metrics.values())
    total_confirmed = sum(item["confirmed_predictions"] for item in per_video_metrics.values())
    total_truth = sum(
        item["confirmed_predictions"] + item["missed_detection_count"]
        for item in per_video_metrics.values()
    )
    predicted_duration = sum(item["predicted_duration_ms"] for item in per_video_metrics.values())
    truth_duration = sum(item["truth_duration_ms"] for item in per_video_metrics.values())
    intersection_duration = sum(item["intersection_duration_ms"] for item in per_video_metrics.values())
    boundary_errors = [
        error
        for item in per_video_metrics.values()
        for error in item["boundary_errors_ms"]
    ]
    metrics = {
        "prediction_count": total_predictions,
        "confirmed_predictions": total_confirmed,
        "false_positive_count": sum(item["false_positive_count"] for item in per_video_metrics.values()),
        "missed_detection_count": sum(item["missed_detection_count"] for item in per_video_metrics.values()),
        "uncertain_count": sum(item["uncertain_count"] for item in per_video_metrics.values()),
        "unreviewed_prediction_count": sum(item["unreviewed_prediction_count"] for item in per_video_metrics.values()),
        "segment_precision": round(total_confirmed / total_predictions, 6) if total_predictions else 1.0,
        "segment_recall": round(total_confirmed / total_truth, 6) if total_truth else 1.0,
        "time_coverage_precision": round(intersection_duration / predicted_duration, 6) if predicted_duration else 1.0,
        "time_coverage_recall": round(intersection_duration / truth_duration, 6) if truth_duration else 1.0,
        "boundary_error_p50_ms": round(float(np.percentile(boundary_errors, 50)), 3) if boundary_errors else None,
        "boundary_error_p95_ms": round(float(np.percentile(boundary_errors, 95)), 3) if boundary_errors else None,
    }

    inventory_count = len(inventory_by_url)
    discovered_count = int(inventory.get("video_count", inventory_count))
    completed_detection_count = sum(
        item.get("status") == "COMPLETED" for item in detections_by_url.values()
    )
    reviewed_detection_count = sum(
        item.get("candidate_kind") == "DETECTION" and bool(item.get("label"))
        for item in reviews
    )
    reviewed_audit_count = sum(
        item.get("candidate_kind") == "AUDIT" and bool(item.get("label"))
        for item in reviews
    )
    total_video_seconds = sum(
        float(item.get("duration") or 0) for item in inventory_by_url.values()
    )
    processed_video_seconds = sum(
        float(inventory_by_url.get(url, {}).get("duration") or 0)
        for url, item in detections_by_url.items()
        if item.get("status") == "COMPLETED"
    )
    total_elapsed_seconds = sum(
        float(item.get("elapsed_seconds") or 0)
        for item in detections_by_url.values()
        if item.get("status") == "COMPLETED"
    )
    metrics.update(
        {
            "discovery_coverage": round(inventory_count / discovered_count, 6) if discovered_count else 1.0,
            "processing_completion": round(completed_detection_count / inventory_count, 6) if inventory_count else 1.0,
            "candidate_review_coverage": round(reviewed_detection_count / total_predictions, 6) if total_predictions else 1.0,
            "audit_review_coverage": round(reviewed_audit_count / len(audit_windows), 6) if audit_windows else 0.0,
            "false_positives_per_video_hour": round(
                metrics["false_positive_count"] / (total_video_seconds / 3600), 6
            ) if total_video_seconds else 0.0,
            "suppressed_candidate_count": sum(
                int(item.get("suppressed_candidate_count") or 0)
                for item in detections_by_url.values()
            ),
            "processing_speed_x": round(processed_video_seconds / total_elapsed_seconds, 6)
            if total_elapsed_seconds
            else None,
            "peak_memory_bytes": max(
                (
                    int(item["peak_memory_bytes"])
                    for item in detections_by_url.values()
                    if item.get("peak_memory_bytes") is not None
                ),
                default=None,
            ),
        }
    )

    reasons = []
    blocking = False
    if inventory.get("discovery_errors"):
        reasons.append(f"目录发现失败 {len(inventory['discovery_errors'])} 项")
        blocking = True
    failed_probe = [item for item in inventory_by_url.values() if item.get("probe_status") != "COMPLETED"]
    for item in failed_probe:
        reasons.append(
            f"不可访问或探测失败: {item.get('course_name', item['url'])} ({item.get('error_reason', '')})"
        )
        blocking = True
    missing_detection = set(inventory_by_url) - set(detections_by_url)
    if missing_detection:
        reasons.append(f"尚未处理视频 {len(missing_detection)} 项")
    failed_detection = [item for item in detections_by_url.values() if item.get("status") != "COMPLETED"]
    if failed_detection:
        reasons.append(f"动态检测失败 {len(failed_detection)} 项")
        blocking = True

    detection_candidate_count = sum(
        len(item.get("dynamic_segments", [])) for item in detections_by_url.values()
    )
    reviewed_detection_keys = {
        (item["url"], int(item["start_ms"]), int(item["end_ms"]))
        for item in reviews
        if item["candidate_kind"] == "DETECTION"
    }
    if len(reviewed_detection_keys) < detection_candidate_count:
        reasons.append(
            f"检测候选尚未全部复核: {len(reviewed_detection_keys)}/{detection_candidate_count}"
        )
    reviewed_audit_keys = {
        (item["url"], int(item["start_ms"]), int(item["end_ms"]))
        for item in reviews
        if item["candidate_kind"] == "AUDIT"
    }
    if not audit_windows:
        reasons.append("尚未生成漏报检查窗口")
    elif len(reviewed_audit_keys) < len(audit_windows):
        reasons.append(f"漏报检查尚未全部复核: {len(reviewed_audit_keys)}/{len(audit_windows)}")
    if metrics["false_positive_count"] or metrics["missed_detection_count"]:
        reasons.append("仍存在已知误报或漏报")
    if metrics["uncertain_count"]:
        reasons.append(f"仍有 UNCERTAIN 复核项 {metrics['uncertain_count']} 个")

    meets_quality = (
        metrics["segment_precision"] >= 0.95
        and metrics["segment_recall"] >= 0.95
        and (metrics["boundary_error_p95_ms"] is None or metrics["boundary_error_p95_ms"] <= 20000)
        and metrics["false_positive_count"] == 0
        and metrics["missed_detection_count"] == 0
        and metrics["uncertain_count"] == 0
        and metrics["unreviewed_prediction_count"] == 0
    )
    fully_covered = (
        not missing_detection
        and not failed_probe
        and not failed_detection
        and detection_candidate_count == len(reviewed_detection_keys)
        and bool(audit_windows)
        and len(reviewed_audit_keys) == len(audit_windows)
    )
    status = "PASSED" if fully_covered and meets_quality and not reasons else ("BLOCKED" if blocking else "INCOMPLETE")
    completion = {"status": status, "reasons": reasons}
    return {
        "schema_version": 1,
        "run_id": detections.get("run_id") or inventory["run_id"],
        "inventory_run_id": detections.get("inventory_run_id") or inventory["run_id"],
        "inventory_fingerprint": inventory["inventory_fingerprint"],
        "algorithm_version": algorithm_version,
        "code_commit": detections.get("code_commit", ""),
        "algorithm_source_fingerprint": detections.get("algorithm_source_fingerprint", ""),
        "effective_config": config,
        "metrics": metrics,
        "completion": completion,
        "segments": rows,
        "failures": [
            {
                "course_name": item.get("course_name", ""),
                "url": item["url"],
                "stage": "PROBE",
                "reason": item.get("error_reason", ""),
            }
            for item in failed_probe
        ]
        + [
            {
                "course_name": item.get("course_name", ""),
                "url": item["url"],
                "stage": "DETECTION",
                "reason": item.get("error_reason", ""),
            }
            for item in failed_detection
        ],
    }


def _write_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    partial = path.with_name(f"{path.name}.part")
    try:
        partial.write_text(content, encoding=encoding)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def write_report(output_dir: Path, report: dict) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "segments.json"
    csv_path = output_dir / "segments.csv"
    markdown_path = output_dir / "疑似动态区间复核报告.md"
    _write_atomic(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    csv_partial = csv_path.with_name(f"{csv_path.name}.part")
    try:
        with csv_partial.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows({field: row.get(field, "") for field in CSV_FIELDS} for row in report["segments"])
        os.replace(csv_partial, csv_path)
    finally:
        csv_partial.unlink(missing_ok=True)

    metrics = report["metrics"]
    lines = [
        "# 疑似动态区间复核报告",
        "",
        f"- 运行标识：`{report['run_id']}`",
        f"- 语料冻结标识：`{report['inventory_run_id']}`",
        f"- 算法版本：`{report['algorithm_version']}`",
        f"- 代码提交：`{report['code_commit'] or '未记录'}`",
        f"- 算法源文件指纹：`{report['algorithm_source_fingerprint'] or '未记录'}`",
        f"- 完成状态：`{report['completion']['status']}`",
        f"- 区间准确率/召回率：`{metrics['segment_precision']:.2%}` / `{metrics['segment_recall']:.2%}`",
        f"- 时间覆盖准确率/召回率：`{metrics['time_coverage_precision']:.2%}` / `{metrics['time_coverage_recall']:.2%}`",
        "",
        "## 疑似区间",
        "",
        "| 课程 | 视频 URL | 时长(s) | 疑似开始 | 疑似结束 | 类型 | 置信度 | 证据位置 | 复核结论 | 备注 | 算法版本 | 配置摘要 |",
        "| --- | --- | ---: | --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in report["segments"]:
        values = [
            row["course_name"],
            row["video_url"],
            row["duration_seconds"],
            _time_text(row["start_ms"]),
            _time_text(row["end_ms"]),
            row["type"],
            row["confidence"],
            row["evidence_path"],
            row["review_label"],
            row["notes"],
            row["algorithm_version"],
            row["config_summary"],
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    if not report["segments"]:
        lines.append("| 无 |  |  |  |  |  |  |  |  |  |  |  |")

    lines.extend(["", "## 未完成与阻塞", ""])
    if report["completion"]["reasons"]:
        lines.extend(f"- {reason}" for reason in report["completion"]["reasons"])
    else:
        lines.append("- 无。当前完成声明只适用于本报告绑定的冻结语料和算法版本。")
    _write_atomic(markdown_path, "\n".join(lines) + "\n")
    return {"json": json_path, "csv": csv_path, "markdown": markdown_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成机器可读结果和中文疑似动态区间报告")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    detections = json.loads(args.detections.read_text(encoding="utf-8"))
    queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    reviews = [
        item
        for item in queue.get("candidates", [])
        if item.get("review_status") == "COMPLETED" and item.get("label")
    ]
    report = build_report(
        inventory,
        detections,
        reviews=reviews,
        audit_windows=queue.get("audit_windows", []),
    )
    paths = write_report(args.output_dir, report)
    print(
        json.dumps(
            {
                "status": report["completion"]["status"],
                "json": str(paths["json"]),
                "csv": str(paths["csv"]),
                "markdown": str(paths["markdown"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
