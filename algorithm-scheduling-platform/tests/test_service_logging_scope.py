from __future__ import annotations

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
TARGET_PROJECTS = {
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
}


def test_logging_scope_excludes_text_analysis_and_vendor_code() -> None:
    assert "text_analysis" not in TARGET_PROJECTS
    assert len(TARGET_PROJECTS) == 11
    assert not any(
        path.name == "logging.py" and "vendor" in path.parts
        for project in TARGET_PROJECTS
        for path in (WORKSPACE_ROOT / project).rglob("logging.py")
    )


def test_text_analysis_is_not_a_current_platform_operator() -> None:
    root_agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "保留、非平台" in root_agents or "text_analysis" in root_agents
