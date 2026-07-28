"""One-shot enroll / recognize CLI — the Phase-2 proof harness.

    python -m agent_core_webcam.presence.cli enroll  --name jeff --frames 5
    python -m agent_core_webcam.presence.cli recognize --threshold 0.5

No watcher, no state file: `recognize` grabs ONE frame, prints per-face
`verdict | cosine=.. | bbox=..`, and exits. The raw cosine is always printed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import numpy.typing as npt

from agent_core_webcam.presence.camera_session import CameraSession
from agent_core_webcam.presence.enrollment import (
    DEFAULT_ENROLLMENT_DIR,
    build_template,
    load_template,
    save_template,
)
from agent_core_webcam.presence.recognition import (
    embed_faces,
    load_analyzer,
    match_embedding,
)

_DEFAULT_THRESHOLD = 0.5


def _open_session(camera_index: int) -> CameraSession:
    """Open a camera session held for the whole command (seam for tests)."""
    return CameraSession(camera_index, (1280, 720))


def _countdown(seconds: float, *, shot: int, total: int) -> None:
    """Print a per-second countdown before a shot so the human can get ready."""
    print(f"Get ready — shot {shot} of {total}:", flush=True)
    for n in range(int(round(seconds)), 0, -1):
        print(f"  {n}...", flush=True)
        time.sleep(1)


def _cmd_enroll(args: argparse.Namespace) -> int:
    analyzer = load_analyzer()  # load the model first so the countdown reflects real timing
    frames: list[npt.NDArray[np.uint8]] = []
    secs = int(round(args.interval))
    print(
        f"Enrolling {args.name}: {args.frames} shots with a {secs}s countdown each. "
        f"Look at the camera; shift a little between shots."
    )
    with _open_session(args.camera_index) as cam:
        cam.warmup()  # settle exposure once so the first shot isn't dark/slow
        for i in range(args.frames):
            _countdown(args.interval, shot=i + 1, total=args.frames)
            frames.append(cam.read_bgr())
            print(f"  snap — shot {i + 1}/{args.frames} captured", flush=True)
    try:
        template = build_template(analyzer, frames, name=args.name)
    except ValueError:
        print(
            "error: no face detected in any shot. Try again with better lighting/framing.",
            file=sys.stderr,
        )
        return 1
    out = Path(args.out) if args.out else DEFAULT_ENROLLMENT_DIR / f"{args.name}.json"
    save_template(template, out)  # SECURITY TODO: plaintext — encrypt before live
    print(f"Enrolled {args.name}: {len(template.embeddings)}/{args.frames} shots usable -> {out}")
    return 0


def _cmd_recognize(args: argparse.Namespace) -> int:
    tpath = Path(args.template) if args.template else DEFAULT_ENROLLMENT_DIR / f"{args.name}.json"
    if not tpath.exists():
        print(f"error: no template at {tpath}. Run `enroll` first.", file=sys.stderr)
        return 2
    template = load_template(tpath)
    analyzer = load_analyzer()
    with _open_session(args.camera_index) as cam:
        cam.warmup(2)
        frame = cam.read_bgr()
    faces = embed_faces(analyzer, frame)
    if not faces:
        print("no face detected")
        return 0
    for emb, bbox, det in faces:
        verdict, score = match_embedding(
            emb, template.embeddings, principal=template.name, threshold=args.threshold
        )
        print(f"{verdict} | cosine={score:.2f} | bbox={bbox} | det={det:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="presence")
    parser.add_argument("--camera-index", type=int, default=0)
    sub = parser.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enroll", help="capture frames and build a template")
    e.add_argument("--name", default="jeff")
    e.add_argument("--frames", type=int, default=5)
    e.add_argument("--interval", type=float, default=3.0)
    e.add_argument("--out", default=None)
    e.set_defaults(func=_cmd_enroll)

    r = sub.add_parser("recognize", help="recognize the face in one frame")
    r.add_argument("--name", default="jeff")
    r.add_argument("--template", default=None)
    r.add_argument("--threshold", type=float, default=_DEFAULT_THRESHOLD)
    r.set_defaults(func=_cmd_recognize)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
