"""Stable domain-model facade while implementation modules are migrated incrementally."""

from services.orchestrator_service.ppt_work import PptImageWork, PptWorkLimits

__all__ = ["PptImageWork", "PptWorkLimits"]
