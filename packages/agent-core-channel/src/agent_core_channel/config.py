"""Issue #70: relay configuration with layered precedence.

Order of precedence (highest first):
    1. CLI flag (--inline-mode, etc.)
    2. Env var (AGENT_CORE_CHANNEL_INLINE_MODE, etc.)
    3. YAML config (agent_core.yaml endpoints[name=<agent>].params.channel_relay)
    4. Hardcoded defaults
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass(frozen=True)
class RelayConfig:
    inline_mode: Literal["full", "preview", "disabled"] = "full"
    max_envelopes: int = 5
    max_bytes: int = 8192
    per_envelope_bytes: int = 4096


_ENV_PREFIX = "AGENT_CORE_CHANNEL_"
_VALID_MODES = ("full", "preview", "disabled")


def _coerce_mode(value: Any) -> Literal["full", "preview", "disabled"] | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _VALID_MODES:
        return s  # type: ignore[return-value]
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _yaml_layer(path: Path, agent: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    endpoints = data.get("endpoints", []) if isinstance(data, dict) else []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        if ep.get("name") != agent:
            continue
        params = ep.get("params", {}) or {}
        block = params.get("channel_relay") if isinstance(params, dict) else None
        if isinstance(block, dict):
            return block
    return {}


def _env_layer(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MODE")) is not None:
        out["inline_mode"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MAX_ENVELOPES")) is not None:
        out["max_envelopes"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_MAX_BYTES")) is not None:
        out["max_bytes"] = v
    if (v := env.get(f"{_ENV_PREFIX}INLINE_PER_ENVELOPE_BYTES")) is not None:
        out["per_envelope_bytes"] = v
    return out


def _cli_layer(args: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(args, "inline_mode", None) is not None:
        out["inline_mode"] = args.inline_mode
    if getattr(args, "inline_max_envelopes", None) is not None:
        out["max_envelopes"] = args.inline_max_envelopes
    if getattr(args, "inline_max_bytes", None) is not None:
        out["max_bytes"] = args.inline_max_bytes
    if getattr(args, "inline_per_envelope_bytes", None) is not None:
        out["per_envelope_bytes"] = args.inline_per_envelope_bytes
    return out


def load_config(
    *,
    agent: str,
    config_path: Path,
    cli_args: Any,
    env: Mapping[str, str],
) -> RelayConfig:
    """Resolve RelayConfig with precedence: CLI > env > YAML > defaults."""
    config = RelayConfig()

    layers = [_yaml_layer(config_path, agent), _env_layer(env), _cli_layer(cli_args)]

    for layer in layers:
        if (mode := _coerce_mode(layer.get("inline_mode"))) is not None:
            config = replace(config, inline_mode=mode)
        if (n := _coerce_int(layer.get("max_envelopes"))) is not None:
            config = replace(config, max_envelopes=n)
        if (n := _coerce_int(layer.get("max_bytes"))) is not None:
            config = replace(config, max_bytes=n)
        if (n := _coerce_int(layer.get("per_envelope_bytes"))) is not None:
            config = replace(config, per_envelope_bytes=n)

    return config
