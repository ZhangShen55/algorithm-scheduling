"""检查当前平台调用点是否把请求/媒体对象直接交给 logger。"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECTS = (
    "asr_offline",
    "asr_online",
    "facerec",
    "ocr",
    "screen_det",
    "ppt_slice",
    "vbas",
    "control_service",
    "orchestrator_service",
    "vision_orchestrator_service",
    "online_gateway_service",
)
LOGGER_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
UNSAFE_NAME_MARKERS = {
    "storagepath",
    "request_body",
    "base64_data",
    "audio_bytes",
    "pcm_bytes",
    "embedding",
    "embeddings",
}
SAFE_SIZE_FUNCTIONS = {"len"}


def _is_logger_call(node: ast.Call) -> bool:
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr in LOGGER_METHODS
        and isinstance(function.value, ast.Name)
        and function.value.id.lower() in {"logger", "log", "access_logger"}
    )


def _contains_unsafe_expression(node: ast.AST, *, under_safe_size_call: bool = False) -> bool:
    """只检查实际传给 logger 的表达式，避免把日志文案或变量名误判为媒体数据。"""
    if isinstance(node, ast.Constant):
        return False
    if isinstance(node, ast.Name):
        return not under_safe_size_call and node.id.lower() in UNSAFE_NAME_MARKERS
    if isinstance(node, ast.Attribute):
        if node.attr.lower() == "model_dump":
            return True
        return _contains_unsafe_expression(node.value, under_safe_size_call=under_safe_size_call)
    if isinstance(node, ast.Call):
        function_name = node.func.id.lower() if isinstance(node.func, ast.Name) else ""
        safe_call = function_name in SAFE_SIZE_FUNCTIONS
        return any(
            _contains_unsafe_expression(argument, under_safe_size_call=safe_call)
            for argument in node.args
        ) or any(
            _contains_unsafe_expression(keyword.value, under_safe_size_call=safe_call)
            for keyword in node.keywords
        )
    if isinstance(node, ast.JoinedStr):
        return any(_contains_unsafe_expression(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _contains_unsafe_expression(node.value)
    return any(_contains_unsafe_expression(child) for child in ast.iter_child_nodes(node))


def find_unsafe_logging(workspace_root: Path) -> list[str]:
    findings: list[str] = []
    for project in PROJECTS:
        app_root = workspace_root / project / "app"
        if not app_root.is_dir():
            continue
        for source_path in app_root.rglob("*.py"):
            if "vendor" in source_path.parts or source_path.name.endswith("-bak.py"):
                continue
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(source_path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_logger_call(node):
                    continue
                segment = ast.get_source_segment(source, node) or ""
                if any(_contains_unsafe_expression(argument) for argument in node.args) or any(
                    _contains_unsafe_expression(keyword.value) for keyword in node.keywords
                ):
                    findings.append(f"{source_path}:{node.lineno}: {segment.splitlines()[0]}")
    return findings


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[2]
    findings = find_unsafe_logging(workspace_root)
    if findings:
        print("发现禁止直接记录请求或媒体对象的日志调用：", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("sensitive logging check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
