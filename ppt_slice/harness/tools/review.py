"""Create missed-detection audit windows and calculate reviewed metrics."""
from __future__ import annotations

from collections import Counter

import numpy as np


CONFIRMED_LABELS = {"CONFIRMED_VIDEO", "CONFIRMED_SCROLL"}
REVIEW_LABELS = CONFIRMED_LABELS | {"FALSE_POSITIVE", "UNCERTAIN"}


def _overlaps(start_ms: int, end_ms: int, segments: list[dict]) -> bool:
    return any(start_ms < int(item["end_ms"]) and end_ms > int(item["start_ms"]) for item in segments)


def _window(center_ms: int, duration_ms: int, width_ms: int = 10000) -> tuple[int, int]:
    half = width_ms // 2
    return max(0, center_ms - half), min(duration_ms, center_ms + half)


def build_audit_windows(
    detection: dict,
    *,
    duration_ms: int,
    fixed_grid_ms: int = 300000,
    long_gap_ms: int = 120000,
    dense_window_ms: int = 60000,
    dense_slice_count: int = 5,
) -> list[dict]:
    segments = detection.get("dynamic_segments", [])
    candidates: list[dict] = []

    for center_ms in range(0, duration_ms, fixed_grid_ms):
        start_ms, end_ms = _window(center_ms, duration_ms)
        candidates.append({"source": "FIXED_GRID", "start_ms": start_ms, "end_ms": end_ms})

    slice_times_ms = sorted(int(item["snap_time"]) * 1000 for item in detection.get("slices", []))
    boundaries = [0, *slice_times_ms, duration_ms]
    for left, right in zip(boundaries, boundaries[1:]):
        if right - left >= long_gap_ms:
            start_ms, end_ms = _window((left + right) // 2, duration_ms)
            candidates.append({"source": "LONG_SLICE_GAP", "start_ms": start_ms, "end_ms": end_ms})

    buckets = Counter(timestamp // dense_window_ms for timestamp in slice_times_ms)
    for bucket, count in buckets.items():
        if count >= dense_slice_count:
            bucket_values = [value for value in slice_times_ms if value // dense_window_ms == bucket]
            center_ms = bucket_values[len(bucket_values) // 2]
            start_ms, end_ms = _window(center_ms, duration_ms)
            candidates.append({"source": "DENSE_SLICES", "start_ms": start_ms, "end_ms": end_ms})

    for item in detection.get("near_threshold_windows", []):
        candidates.append(
            {
                "source": "NEAR_THRESHOLD",
                "start_ms": int(item["start_ms"]),
                "end_ms": int(item["end_ms"]),
            }
        )

    unique = {}
    for item in candidates:
        if item["start_ms"] >= item["end_ms"] or _overlaps(item["start_ms"], item["end_ms"], segments):
            continue
        key = (item["source"], item["start_ms"], item["end_ms"])
        unique[key] = item
    return sorted(unique.values(), key=lambda item: (item["start_ms"], item["source"]))


def _merged_length(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _intersection_length(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> int:
    intersections = []
    for left_start, left_end in left:
        for right_start, right_end in right:
            start = max(left_start, right_start)
            end = min(left_end, right_end)
            if start < end:
                intersections.append((start, end))
    return _merged_length(intersections)


def _percentile(values: list[int], percentile: int) -> float | None:
    return round(float(np.percentile(values, percentile)), 3) if values else None


def calculate_metrics(predicted_segments: list[dict], reviews: list[dict]) -> dict:
    for review in reviews:
        if review.get("label") not in REVIEW_LABELS:
            raise ValueError(f"未知复核标签: {review.get('label')}")
    review_by_prediction = {
        (int(item["start_ms"]), int(item["end_ms"])): item
        for item in reviews
        if item.get("candidate_kind") == "DETECTION"
    }
    confirmed_predictions = []
    false_positive_count = 0
    unreviewed_prediction_count = 0
    boundary_errors = []
    for predicted in predicted_segments:
        key = (int(predicted["start_ms"]), int(predicted["end_ms"]))
        review = review_by_prediction.get(key)
        if review is None or review["label"] == "UNCERTAIN":
            unreviewed_prediction_count += 1
        elif review["label"] == "FALSE_POSITIVE":
            false_positive_count += 1
        else:
            confirmed_predictions.append(key)
            truth_start = int(review.get("truth_start_ms", key[0]))
            truth_end = int(review.get("truth_end_ms", key[1]))
            boundary_errors.extend([abs(key[0] - truth_start), abs(key[1] - truth_end)])

    confirmed_reviews = [item for item in reviews if item["label"] in CONFIRMED_LABELS]
    truth_intervals = [
        (
            int(item.get("truth_start_ms", item["start_ms"])),
            int(item.get("truth_end_ms", item["end_ms"])),
        )
        for item in confirmed_reviews
    ]
    missed_detection_count = sum(
        item.get("candidate_kind") == "AUDIT" for item in confirmed_reviews
    )
    uncertain_count = sum(item["label"] == "UNCERTAIN" for item in reviews)
    prediction_count = len(predicted_segments)
    truth_count = len(confirmed_reviews)
    segment_precision = len(confirmed_predictions) / prediction_count if prediction_count else 1.0
    segment_recall = len(confirmed_predictions) / truth_count if truth_count else 1.0

    predicted_intervals = [
        (int(item["start_ms"]), int(item["end_ms"])) for item in predicted_segments
    ]
    intersection = _intersection_length(predicted_intervals, truth_intervals)
    predicted_duration = _merged_length(predicted_intervals)
    truth_duration = _merged_length(truth_intervals)
    time_precision = intersection / predicted_duration if predicted_duration else 1.0
    time_recall = intersection / truth_duration if truth_duration else 1.0

    return {
        "prediction_count": prediction_count,
        "confirmed_predictions": len(confirmed_predictions),
        "false_positive_count": false_positive_count,
        "missed_detection_count": missed_detection_count,
        "uncertain_count": uncertain_count,
        "unreviewed_prediction_count": unreviewed_prediction_count,
        "segment_precision": round(segment_precision, 6),
        "segment_recall": round(segment_recall, 6),
        "time_coverage_precision": round(time_precision, 6),
        "time_coverage_recall": round(time_recall, 6),
        "predicted_duration_ms": predicted_duration,
        "truth_duration_ms": truth_duration,
        "intersection_duration_ms": intersection,
        "boundary_errors_ms": boundary_errors,
        "boundary_error_p50_ms": _percentile(boundary_errors, 50),
        "boundary_error_p95_ms": _percentile(boundary_errors, 95),
        "has_no_known_errors": (
            false_positive_count == 0
            and missed_detection_count == 0
            and uncertain_count == 0
            and unreviewed_prediction_count == 0
        ),
    }
