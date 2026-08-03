"""Enroll a principal: build + persist a face template.

SECURITY TODO: templates are biometric data. This Phase-2 spike stores them
PLAINTEXT, local-only. They MUST be encrypted at rest (OS keystore) before
Phase 4 / live-wire / any family enrollment. Do not ship this plaintext path
past the proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.floating]

DEFAULT_ENROLLMENT_DIR = Path.home() / ".agent-core" / "presence" / "enrollment"


@dataclass(frozen=True)
class Template:
    """A principal's enrolled face template: a name + a set of embeddings.

    ``sources`` runs PARALLEL to ``embeddings`` — ``sources[i]`` is the image
    ``embeddings[i]`` came from, or ``""`` for embeddings enrolled before frames
    were kept. Without it a template is a bag of opaque vectors: you cannot tell
    what poses it covers, cannot re-derive it when the model changes, and cannot
    drop one bad shot without redoing the whole enrollment.
    """

    name: str
    embeddings: list[Vector]
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Pad ``sources`` so it always aligns with ``embeddings``."""
        if len(self.sources) != len(self.embeddings):
            padded = [*self.sources, *([""] * (len(self.embeddings) - len(self.sources)))]
            object.__setattr__(self, "sources", padded[: len(self.embeddings)])


def save_template(template: Template, path: Path) -> None:
    """Persist a template as plaintext JSON (embeddings as float lists).

    SECURITY TODO: encrypt at rest before this leaves the Phase-2 spike.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": template.name,
        "embeddings": [e.astype(float).tolist() for e in template.embeddings],
        "sources": list(template.sources),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_template(path: Path) -> Template:
    """Load a template written by :func:`save_template`.

    ``sources`` is optional so templates written before frame-keeping still load.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    embeddings = [np.asarray(e, dtype=np.float32) for e in raw["embeddings"]]
    return Template(
        name=str(raw["name"]),
        embeddings=embeddings,
        sources=[str(s) for s in raw.get("sources", [])],
    )


def merge_templates(base: Template, extra: Template) -> Template:
    """Concatenate ``extra``'s embeddings onto ``base``'s, keeping both.

    Matching takes the BEST cosine across a template's whole embedding list, so
    adding shots can only ever raise a genuine score — a session captured under
    new lighting or a new seat position adds coverage without discarding the
    old. Names must agree; merging two people into one template would silently
    make each recognizable as the other.
    """
    if base.name != extra.name:
        raise ValueError(f"refusing to merge templates for {base.name!r} and {extra.name!r}")
    return Template(
        name=base.name,
        embeddings=[*base.embeddings, *extra.embeddings],
        sources=[*base.sources, *extra.sources],
    )


def build_template(
    analyzer: object,
    frames: list[npt.NDArray[np.uint8]],
    *,
    name: str,
    frame_paths: list[str] | None = None,
) -> Template:
    """Build a template from enrollment frames — the largest face per frame.

    ``frame_paths`` (same length and order as ``frames``) records where each
    embedding came from; frames with no detected face are skipped in BOTH lists
    so the pairing stays true. Raises ValueError if no face is found at all.
    """
    from agent_core_webcam.presence.recognition import embed_faces

    embeddings: list[Vector] = []
    used: list[str] = []
    for i, frame in enumerate(frames):
        faces = embed_faces(analyzer, frame)
        if not faces:
            continue
        # Largest detected face (bbox area) — the person being enrolled.
        emb, _bbox, _score = max(faces, key=lambda t: (t[1][2] - t[1][0]) * (t[1][3] - t[1][1]))
        embeddings.append(emb)
        used.append(frame_paths[i] if frame_paths and i < len(frame_paths) else "")
    if not embeddings:
        raise ValueError("no face detected in any enrollment frame")
    return Template(name=name, embeddings=embeddings, sources=used)
