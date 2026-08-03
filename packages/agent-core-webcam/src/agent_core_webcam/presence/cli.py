"""Enroll / recognize / watch CLI for presence.

    python -m agent_core_webcam.presence.cli enroll --name jeff --frames 10 --append
    python -m agent_core_webcam.presence.cli recognize
    python -m agent_core_webcam.presence.cli watch --name jeff

`recognize` grabs ONE frame and prints, per face, the verdict plus EVERY
gallery's score and the runner-up margin — the evidence, not just the answer.
`watch` runs the state loop. Both identify against every enrolled template
rather than testing one against a threshold; see ``recognition.identify``.
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
    load_all_templates,
    load_template,
    merge_templates,
    save_template,
)
from agent_core_webcam.presence.recognition import (
    MIN_BEST_SCORE,
    MIN_MARGIN,
    embed_faces,
    identify,
    load_analyzer,
)
from agent_core_webcam.presence.watcher import run_watch


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
    fresh = len(template.embeddings)
    if args.append and out.exists():
        prior = load_template(out)
        template = merge_templates(prior, template)
        print(f"Appending to {len(prior.embeddings)} existing shot(s) in {out}")
    save_template(template, out)  # SECURITY TODO: plaintext — encrypt before live
    print(
        f"Enrolled {args.name}: {fresh}/{args.frames} new shots usable, "
        f"{len(template.embeddings)} total -> {out}"
    )
    return 0


def _cmd_recognize(args: argparse.Namespace) -> int:
    directory = Path(args.enrollment_dir) if args.enrollment_dir else DEFAULT_ENROLLMENT_DIR
    templates = load_all_templates(directory)
    if not templates:
        print(f"error: no templates in {directory}. Run `enroll` first.", file=sys.stderr)
        return 2
    galleries = {name: t.embeddings for name, t in templates.items()}
    analyzer = load_analyzer()
    with _open_session(args.camera_index) as cam:
        cam.warmup(2)
        frame = cam.read_bgr()
    faces = embed_faces(analyzer, frame)
    if not faces:
        print("no face detected")
        return 0
    for emb, bbox, det in faces:
        verdict, ranked = identify(
            emb, galleries, min_best=args.min_best, min_margin=args.min_margin
        )
        # Every gallery's score, always — the ranking IS the evidence, and a
        # rejection is only interpretable next to what it nearly matched.
        scores = "  ".join(f"{n}={s:.3f}" for n, s in ranked)
        margin = (ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else float("nan")
        print(f"{verdict} | margin={margin:.3f} | {scores} | bbox={bbox} | det={det:.2f}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    directory = Path(args.enrollment_dir) if args.enrollment_dir else DEFAULT_ENROLLMENT_DIR
    templates = load_all_templates(directory)
    if args.name not in templates:
        print(
            f"error: no template for principal {args.name!r} in {directory}. "
            f"Found: {sorted(templates) or 'none'}. Run `enroll` first.",
            file=sys.stderr,
        )
        return 2
    state_path = (
        Path(args.state_path)
        if args.state_path
        else Path.home() / ".agent-core" / "presence" / "state.json"
    )
    roster = ", ".join(f"{n}({len(t.embeddings)})" for n, t in sorted(templates.items()))
    print(
        f"Watching camera {args.camera_index} every {args.interval}s -> {state_path}\n"
        f"Enrolled: {roster}   principal={args.name}\n"
        f"(Ctrl-C to stop.)"
    )
    run_watch(
        templates=templates,
        state_path=state_path,
        principal=args.name,
        min_best=args.min_best,
        min_margin=args.min_margin,
        interval=args.interval,
        camera_index=args.camera_index,
    )
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
    e.add_argument(
        "--append",
        action="store_true",
        help="merge these shots into the existing template instead of replacing it",
    )
    e.set_defaults(func=_cmd_enroll)

    r = sub.add_parser("recognize", help="identify every face in one frame")
    r.add_argument("--enrollment-dir", default=None)
    r.add_argument("--min-best", type=float, default=MIN_BEST_SCORE)
    r.add_argument("--min-margin", type=float, default=MIN_MARGIN)
    r.set_defaults(func=_cmd_recognize)

    w = sub.add_parser("watch", help="continuously write presence state.json")
    w.add_argument("--name", default="jeff", help="the principal (whose presence sets at_desk)")
    w.add_argument(
        "--enrollment-dir",
        default=None,
        help="directory of templates; ALL are loaded and identified against",
    )
    w.add_argument("--state-path", default=None)
    w.add_argument("--min-best", type=float, default=MIN_BEST_SCORE)
    w.add_argument("--min-margin", type=float, default=MIN_MARGIN)
    w.add_argument("--interval", type=float, default=2.0)
    w.set_defaults(func=_cmd_watch)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
