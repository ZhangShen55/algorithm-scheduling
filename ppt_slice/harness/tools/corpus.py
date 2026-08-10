"""Discover and freeze the remote P-video corpus without persisting MP4 files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import subprocess
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


@dataclass(frozen=True)
class DiscoveryResult:
    video_urls: list[str]
    directory_count: int
    filtered_count: int
    errors: list[dict[str, str]]


def _canonical_url(url: str, *, directory: bool | None = None) -> str:
    parts = urlsplit(url)
    decoded_path = unquote(parts.path)
    normalized_path = posixpath.normpath(decoded_path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    is_directory = decoded_path.endswith("/") if directory is None else directory
    if is_directory and not normalized_path.endswith("/"):
        normalized_path += "/"
    encoded_path = quote(normalized_path, safe="/:@-._~!$&'()*+,;=")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), encoded_path, "", ""))


def canonical_url(url: str, *, directory: bool | None = None) -> str:
    return _canonical_url(url, directory=directory)


def _within_root(url: str, root_url: str) -> bool:
    candidate = urlsplit(url)
    root = urlsplit(root_url)
    return (
        candidate.scheme == root.scheme
        and candidate.netloc == root.netloc
        and candidate.path.startswith(root.path)
    )


def _fetch_html(url: str, timeout_seconds: float = 15.0) -> str:
    request = Request(url, headers={"User-Agent": "ppt-slice-harness/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def discover_ppt_videos(
    root_url: str,
    *,
    fetch_html: Callable[[str], str] = _fetch_html,
) -> DiscoveryResult:
    root = _canonical_url(root_url, directory=True)
    pending = [root]
    visited: set[str] = set()
    videos: set[str] = set()
    errors: list[dict[str, str]] = []
    filtered_count = 0

    while pending:
        directory_url = pending.pop(0)
        if directory_url in visited:
            continue
        visited.add(directory_url)
        try:
            html = fetch_html(directory_url)
        except Exception as exc:
            errors.append({"url": directory_url, "reason": str(exc)})
            continue

        parser = _LinkParser()
        parser.feed(html)
        for href in parser.links:
            joined = urljoin(directory_url, href)
            href_path = urlsplit(joined).path
            is_directory = href_path.endswith("/")
            candidate = _canonical_url(joined, directory=is_directory)
            if not _within_root(candidate, root):
                filtered_count += 1
                continue
            if is_directory:
                if candidate not in visited and candidate not in pending:
                    pending.append(candidate)
                continue

            basename = unquote(posixpath.basename(urlsplit(candidate).path))
            if "ppt" in basename.lower() and basename.lower().endswith(".mp4"):
                videos.add(candidate)
            else:
                filtered_count += 1

    return DiscoveryResult(
        video_urls=sorted(videos),
        directory_count=len(visited),
        filtered_count=filtered_count,
        errors=errors,
    )


def _head_resource(url: str, timeout_seconds: float = 15.0) -> dict:
    request = Request(
        url,
        method="HEAD",
        headers={"User-Agent": "ppt-slice-harness/1.0"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        content_length = response.headers.get("Content-Length")
        return {
            "content_length": int(content_length) if content_length else None,
            "last_modified": response.headers.get("Last-Modified"),
        }


def _fraction_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    numerator, separator, denominator = value.partition("/")
    if not separator:
        return float(value)
    denominator_value = float(denominator)
    return float(numerator) / denominator_value if denominator_value else None


def _probe_video(url: str, timeout_seconds: float = 60.0) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate:format=duration",
        "-of",
        "json",
        url,
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("未发现视频流")
    stream = streams[0]
    duration = (payload.get("format") or {}).get("duration")
    return {
        "duration": float(duration) if duration is not None else None,
        "codec": stream.get("codec_name"),
        "fps": _fraction_to_float(stream.get("avg_frame_rate")),
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def _course_url(video_url: str) -> str:
    parts = urlsplit(video_url)
    parent = posixpath.dirname(parts.path.rstrip("/")) + "/"
    return urlunsplit((parts.scheme, parts.netloc, parent, "", ""))


def split_for_url(video_url: str, *, course_url: str | None = None) -> str:
    identity = _canonical_url(course_url or _course_url(video_url), directory=True)
    bucket = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) % 10
    return "CALIBRATION" if bucket < 7 else "HOLDOUT"


def _resource_fingerprint(url: str, metadata: dict) -> str:
    identity = {
        "url": url,
        "content_length": metadata.get("content_length"),
        "last_modified": metadata.get("last_modified"),
        "duration": metadata.get("duration"),
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_inventory(
    root_url: str,
    *,
    fetch_html: Callable[[str], str] = _fetch_html,
    head_resource: Callable[[str], dict] = _head_resource,
    probe_video: Callable[[str], dict] = _probe_video,
    run_id: str,
    discovered_at: str | None = None,
    known_calibration_urls: set[str] | None = None,
) -> dict:
    discovery = discover_ppt_videos(root_url, fetch_html=fetch_html)
    items: list[dict] = []
    known_urls = {
        _canonical_url(url, directory=False)
        for url in (known_calibration_urls or set())
    }

    for url in discovery.video_urls:
        course_url = _course_url(url)
        is_known_truth = url in known_urls
        item = {
            "url": url,
            "course_url": course_url,
            "course_name": unquote(posixpath.basename(urlsplit(course_url).path.rstrip("/"))),
            "file_name": unquote(posixpath.basename(urlsplit(url).path)),
            "content_length": None,
            "last_modified": None,
            "duration": None,
            "codec": None,
            "fps": None,
            "width": None,
            "height": None,
            "split": (
                "CALIBRATION"
                if is_known_truth
                else split_for_url(url, course_url=course_url)
            ),
            "split_reason": (
                "KNOWN_TRUTH" if is_known_truth else "STABLE_HASH_70_30"
            ),
            "probe_status": "PENDING",
            "processing_status": "PENDING",
            "evidence_status": "PENDING",
            "review_status": "PENDING",
            "error_reason": "",
        }
        try:
            item.update(head_resource(url))
            item.update(probe_video(url))
            item["probe_status"] = "COMPLETED"
        except Exception as exc:
            item["probe_status"] = "FAILED"
            item["error_reason"] = str(exc)
        item["resource_fingerprint"] = _resource_fingerprint(url, item)
        items.append(item)

    fingerprint_input = [
        {
            "url": item["url"],
            "resource_fingerprint": item["resource_fingerprint"],
            "split": item["split"],
        }
        for item in items
    ]
    inventory_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_input, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "root_url": _canonical_url(root_url, directory=True),
        "discovered_at": discovered_at or datetime.now().astimezone().isoformat(),
        "directory_count": discovery.directory_count,
        "video_count": len(items),
        "filtered_count": discovery.filtered_count,
        "discovery_errors": discovery.errors,
        "split_policy": "KNOWN_TRUTH_THEN_STABLE_HASH_70_30",
        "known_calibration_urls": sorted(known_urls),
        "inventory_fingerprint": inventory_fingerprint,
        "items": items,
    }


def write_inventory(destination: Path, inventory: dict) -> None:
    destination = Path(destination)
    if destination.suffix.lower() == ".mp4":
        raise ValueError("Harness 不允许写入 MP4")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    try:
        partial.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="发现并冻结远程课程 P 视频清单（MP4 不落盘）")
    parser.add_argument("--root-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--known-calibration-url", action="append", default=[])
    args = parser.parse_args()

    inventory = build_inventory(
        args.root_url,
        run_id=args.run_id,
        known_calibration_urls=set(args.known_calibration_url),
    )
    write_inventory(args.output, inventory)
    print(
        json.dumps(
            {
                "run_id": inventory["run_id"],
                "directory_count": inventory["directory_count"],
                "video_count": inventory["video_count"],
                "failed_count": sum(
                    item["probe_status"] == "FAILED" for item in inventory["items"]
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
