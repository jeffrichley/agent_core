"""Webcam channel scaffolding."""

from __future__ import annotations

from typing import Any

from agent_core_hatchery.config import HatchConfig


def scaffold_webcam(config: HatchConfig) -> dict[str, Any] | None:
    if not config.channels.webcam.enabled:
        return None
    return {
        "type": "builtin.webcam",
        "name": f"webcam-{config.being_name_lower}",
        "description": f"{config.being_name}'s webcam endpoint.",
        "params": {
            "enabled": True,
            "captures_root": f"~/.agent-core/webcam/{config.being_name_lower}",
            "audit_log_path": f"~/.agent-core/webcam/{config.being_name_lower}/audit.jsonl",
            "default_camera_index": 0,
            "default_resolution": [1280, 720],
            "max_resolution": [3840, 2160],
            "capture_timeout_seconds": 3.0,
        },
    }
