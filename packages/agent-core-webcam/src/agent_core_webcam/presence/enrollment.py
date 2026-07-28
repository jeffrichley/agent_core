"""Enroll a principal: build + persist a face template.

SECURITY TODO: templates are biometric data. This Phase-2 spike stores them
PLAINTEXT, local-only. They MUST be encrypted at rest (OS keystore) before
Phase 4 / live-wire / any family enrollment. Do not ship this plaintext path
past the proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.floating]

DEFAULT_ENROLLMENT_DIR = Path.home() / ".agent-core" / "presence" / "enrollment"


@dataclass(frozen=True)
class Template:
    """A principal's enrolled face template: a name + a set of embeddings."""

    name: str
    embeddings: list[Vector]


def save_template(template: Template, path: Path) -> None:
    """Persist a template as plaintext JSON (embeddings as float lists).

    SECURITY TODO: encrypt at rest before this leaves the Phase-2 spike.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": template.name,
        "embeddings": [e.astype(float).tolist() for e in template.embeddings],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_template(path: Path) -> Template:
    """Load a template written by :func:`save_template`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    embeddings = [np.asarray(e, dtype=np.float32) for e in raw["embeddings"]]
    return Template(name=str(raw["name"]), embeddings=embeddings)
