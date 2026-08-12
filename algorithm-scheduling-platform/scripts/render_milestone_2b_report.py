#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

STATUSES = ("通过", "失败", "未执行及原因")
FIELDS = {
    "case_id",
    "status",
    "started_at",
    "finished_at",
    "target",
    "command",
    "evidence",
    "reason",
    "mock",
    "release_tag",
    "git_sha",
}
FORBIDDEN_COMMAND = re.compile(
    r"(?i)(repository\s*\.\s*(complete|finish|mark)|complete_node|mark_node_completed|authorization\s*:|bearer\s+|token\s*=)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总里程碑 2B Harness 用例")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def safe_relative(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"证据路径不安全: {value}")
    return relative


def require_regular(path: Path, root: Path) -> None:
    absolute_root = root.resolve(strict=True)
    candidate = root.joinpath(*safe_relative(path.as_posix()).parts)
    try:
        metadata = os.lstat(candidate)
    except FileNotFoundError as exc:
        raise ValueError(f"证据文件不存在: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"证据必须是普通文件: {path}")
    if (
        candidate.resolve(strict=True).parent != absolute_root
        and absolute_root not in candidate.resolve(strict=True).parents
    ):
        raise ValueError(f"证据越出 release 目录: {path}")


def validate(cases: Any, release_root: Path) -> tuple[list[dict[str, Any]], str, str]:
    if not isinstance(cases, list):
        raise ValueError("输入必须是用例数组")
    seen: set[str] = set()
    release_tag = ""
    git_sha = ""
    validated: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"用例 {index} 不是对象")
        unknown = set(case) - FIELDS
        if unknown:
            raise ValueError(f"用例包含未知字段: {sorted(unknown)}")
        missing = FIELDS - set(case)
        if missing:
            raise ValueError(f"用例缺少字段: {sorted(missing)}")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case_id 重复或无效: {case_id}")
        seen.add(case_id)
        if case["status"] not in STATUSES:
            raise ValueError(f"未知测试状态: {case['status']}")
        if not isinstance(case["mock"], bool):
            raise ValueError("mock 必须是布尔值")
        if case["status"] == "通过":
            if not case["command"] or not case["evidence"]:
                raise ValueError(f"通过用例缺少 command 或 evidence: {case_id}")
        if case["status"] == "未执行及原因" and not str(case["reason"]).strip():
            raise ValueError(f"未执行用例缺少原因: {case_id}")
        if FORBIDDEN_COMMAND.search(str(case["command"])):
            raise ValueError(f"用例命令包含仓储完成捷径或敏感 token: {case_id}")
        if not isinstance(case["evidence"], list):
            raise ValueError(f"evidence 必须是数组: {case_id}")
        for evidence in case["evidence"]:
            if not isinstance(evidence, str):
                raise ValueError(f"证据路径必须是字符串: {case_id}")
            require_regular(Path(evidence), release_root)
        if not release_tag:
            release_tag, git_sha = case["release_tag"], case["git_sha"]
        if case["release_tag"] != release_tag or case["git_sha"] != git_sha:
            raise ValueError("用例跨越不同 release tag 或 Git SHA")
        if release_root.name != git_sha or release_root.parent.name != release_tag:
            raise ValueError("用例 release/SHA 与归档目录不匹配")
        validated.append(case)
    return validated, release_tag, git_sha


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_safe_output(path: Path, content: bytes) -> str:
    if path.is_symlink():
        raise ValueError(f"输出路径不安全: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"输出路径不安全: {path}")
        if path.read_bytes() == content:
            return "same"
        raise ValueError(f"拒绝覆盖同一 release/SHA 的不同报告: {path}")
    return "missing"


def write_private_temp(parent: Path, name: str, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=name, dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        return temporary
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def recover_transaction(parent: Path, journal: Path) -> None:
    if not journal.exists():
        return
    state = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or set(state) != {"published", "temporaries"}:
        raise ValueError("报告事务 journal 不合法")
    for name in state["published"]:
        candidate = parent / safe_relative(str(name))
        if candidate.parent != parent or candidate.is_symlink():
            raise ValueError("报告事务发布路径不安全")
        candidate.unlink(missing_ok=True)
    for name in state["temporaries"]:
        candidate = parent / safe_relative(str(name))
        if candidate.parent != parent or candidate.is_symlink():
            raise ValueError("报告事务临时路径不安全")
        candidate.unlink(missing_ok=True)
    journal.unlink()
    fsync_directory(parent)


def publish_report_transaction(
    release_root: Path,
    outputs: tuple[tuple[Path, bytes], tuple[Path, bytes]],
) -> None:
    parents = {path.parent.resolve(strict=True) for path, _ in outputs}
    if len(parents) != 1:
        raise ValueError("JSON 与 Markdown 必须位于同一个 release 汇总目录")
    parent = next(iter(parents))
    if parent != (release_root / "summary").resolve(strict=True):
        raise ValueError("报告输出必须位于当前 release 的 summary 目录")
    lock = parent / ".report-transaction.lock"
    lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    journal = parent / ".report-transaction.journal"
    temporaries: list[Path] = []
    published: list[Path] = []
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        recover_transaction(parent, journal)
        states = [require_safe_output(path, content) for path, content in outputs]
        if states == ["same", "same"]:
            return
        if "same" in states:
            raise ValueError("报告双文件状态不一致，拒绝补写半套报告")
        transaction_id = uuid.uuid4().hex
        for path, content in outputs:
            temporaries.append(
                write_private_temp(parent, f".{path.name}.{transaction_id}.", content)
            )
        journal_payload = {
            "published": [],
            "temporaries": [path.name for path in temporaries],
        }
        journal.write_text(json.dumps(journal_payload), encoding="utf-8")
        os.chmod(journal, 0o600)
        with journal.open("rb") as stream:
            os.fsync(stream.fileno())
        fsync_directory(parent)
        for index, ((destination, _), temporary) in enumerate(
            zip(outputs, temporaries, strict=True)
        ):
            require_safe_output(destination, outputs[index][1])
            os.replace(temporary, destination)
            published.append(destination)
            journal_payload["published"] = [path.name for path in published]
            journal_payload["temporaries"] = [
                path.name for path in temporaries if path.exists()
            ]
            journal.write_text(json.dumps(journal_payload), encoding="utf-8")
            with journal.open("rb") as stream:
                os.fsync(stream.fileno())
            fsync_directory(parent)
            if index == 0 and os.getenv("REPORT_TRANSACTION_CRASH_AFTER_FIRST_RENAME"):
                os._exit(86)
            if index == 0 and os.getenv("REPORT_TRANSACTION_FAIL_AFTER_FIRST_RENAME"):
                raise RuntimeError("注入第一文件发布后的事务失败")
        journal.unlink()
        fsync_directory(parent)
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        for path in temporaries:
            path.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)
        fsync_directory(parent)
        raise
    finally:
        os.close(lock_fd)


def render(cases: list[dict[str, Any]], tag: str, sha: str) -> tuple[dict[str, Any], str]:
    real = [case for case in cases if not case["mock"]]
    mock = [case for case in cases if case["mock"]]
    counts = {status: sum(case["status"] == status for case in real) for status in STATUSES}
    mock_counts = {status: sum(case["status"] == status for case in mock) for status in STATUSES}
    document = {
        "schema_version": 1,
        "release_tag": tag,
        "git_sha": sha,
        "counts": counts,
        "mock_counts": mock_counts,
        "cases": cases,
    }
    lines = [
        "# 里程碑 2B 验证报告",
        "",
        f"- Release：`{tag}`",
        f"- Git SHA：`{sha}`",
        "",
        "## 真实验证",
        "",
        f"通过 {counts['通过']}，失败 {counts['失败']}，未执行 {counts['未执行及原因']}。",
        "",
        "## Mock 合同验证",
        "",
        (
            f"通过 {mock_counts['通过']}，失败 {mock_counts['失败']}，"
            f"未执行 {mock_counts['未执行及原因']}。"
        ),
        "",
        "| 用例 | 类型 | 状态 | 目标 | 原因 |",
        "|---|---|---|---|---|",
    ]
    for case in cases:
        kind = "Mock" if case["mock"] else "真实"
        reason = str(case["reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {case['case_id']} | {kind} | {case['status']} | {case['target']} | {reason} |"
        )
    return document, "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        cases = json.loads(args.input.read_text(encoding="utf-8"))
        validated, tag, sha = validate(cases, args.release_root)
        document, markdown = render(validated, tag, sha)
        publish_report_transaction(
            args.release_root,
            (
                (
                    args.output_json,
                    (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(),
                ),
                (args.output_markdown, markdown.encode()),
            ),
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"报告生成失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
