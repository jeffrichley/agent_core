"""Issue #70: relay configuration. Phase 6 of the implementation plan
will replace this stub with full CLI/env/YAML resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RelayConfig:
    inline_mode: Literal["full", "preview", "disabled"] = "full"
    max_envelopes: int = 5
    max_bytes: int = 8192
    per_envelope_bytes: int = 4096
