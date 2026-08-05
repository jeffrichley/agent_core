# Presence Awareness — Phase 2: Recognition Proof

**Date:** 2026-07-28
**Author:** Wren (re-scoped with Jeff)
**Status:** Design approved; ready for implementation plan
**Amends:** `2026-07-27-presence-awareness-design.md` (the umbrella spec). This
narrows and re-orders that spec's CV work.

---

## Why this re-scope

The umbrella spec's CV pipeline was three tiers (motion gate → detect+**track** →
recognize) plus a per-person **Bayesian** identity belief. Tracking and the
Bayesian filter exist to solve problems we are *assuming* we will have — jitter,
temporal instability, multi-person association — but **we have not seen a single
real recognition result yet.**

So we front-load the actual unknown: *can ArcFace recognize Jeff on his real
camera at all?* This slice answers that with the least machinery possible, and
its output (raw per-frame cosine scores) becomes the **evidence** for whether the
tracking/Bayesian layer is ever worth building. YAGNI: don't build the smoothing
until we've watched the raw signal be jittery.

**Re-ordered phasing:**
- **Phase 1** ✅ — presence contract + configurable hook (done).
- **Phase 2 (THIS SPEC)** — recognition proof: enroll Jeff, detect + recognize a
  face in one frame, via a one-shot CLI, validated live.
- **Phase 3 (was 2/3), conditional** — tracking + Bayesian smoothing + the
  continuous watcher, **only if** Phase 2's observed jitter justifies it.
- **Phase 4** — snapshot refactor + `webcam watch` + live-wire into a session.

---

## Scope (Phase 2)

- **In:** single-frame face detection + ArcFace recognition against Jeff's
  enrolled template; enrollment of Jeff from a handful of frames; a one-shot
  `recognize` CLI that prints verdict + **raw cosine** + bbox per detected face;
  the pure decision logic (cosine → verdict) and template load/save.
- **Out (deferred, flagged):**
  - The continuous watcher / any `state.json` writing (Phase 3+). This slice does
    **not** touch the Phase-1 hook or its state file.
  - Tracking (cross-frame association) and the Bayesian belief filter — deferred
    until Phase 2's jitter evidence justifies them.
  - The Tier-0 motion gate (an optimization for a continuous loop we're not
    building yet).
  - **Template encryption.** Per this slice's decision, the enrolled template is
    stored **plaintext, local-only**, with a loud in-code TODO that it MUST be
    encrypted (OS keystore) before Phase 4 / live-wire / any family enrollment.
    Acceptable now: it is Jeff's own face, on Jeff's own machine, in a spike we
    may discard.
  - Family / multi-identity enrollment (designed-for, not built).

---

## Architecture & components (all inside `agent-core-webcam`)

The recognition code lives alongside the Phase-1 presence code but is **import-
isolated** from the hook: nothing the hook imports (`injector` → `state`,
`levels`) may import `insightface`, `onnxruntime`, `cv2`, or `numpy`. Recognition
deps are an **optional extra** (`recognition`), so a being that only runs the
hook never installs the heavy model stack.

- **`presence/recognition.py`** *(new)* — the recognition core.
  - A thin wrapper over InsightFace `FaceAnalysis(name="buffalo_s")` (bundles the
    SCRFD detector + ArcFace recognizer; ONNXRuntime, CPU). `.get(bgr_frame)`
    returns detected faces each carrying a normalized embedding.
  - `cosine(a, b)` and a `decide(cosine, threshold) -> verdict` — **pure**,
    unit-testable without the model.
  - `recognize_frame(frame, template, *, threshold) -> list[FaceResult]` where
    `FaceResult = (verdict, cosine, bbox)`. Best-match cosine of each detected
    face against the template's embeddings.
- **`presence/enrollment.py`** *(new)* — build + persist Jeff's template.
  - Capture ~5–10 frames (reusing the existing webcam `capture()` → PNG →
    `cv2.imdecode` → BGR array), embed each, collect the embeddings.
  - Persist as **plaintext** `~/.agent-core/presence/enrollment/<name>.json`
    (embeddings as float lists + metadata). Load returns the template.
  - Module docstring + a `# SECURITY TODO` at the write site: encrypt before live.
- **`recognize` CLI** *(new entry point)* — grabs **one** frame from the webcam,
  runs `recognize_frame` against the loaded template, prints one line per
  detected face: `verdict | cosine=0.7x | bbox=(x,y,w,h)`; prints "no face" if the
  detector finds none. Exits. No loop, no state file. A sibling `enroll` command
  drives enrollment.

### Data flow

```
enroll:   camera → capture() → decode → buffalo_s.get() → embeddings
                 → ~/.agent-core/presence/enrollment/jeff.json   (plaintext, TODO: encrypt)

recognize: camera → capture() → decode → buffalo_s.get() → per-face embedding
                  → cosine vs template (best match) → decide(threshold)
                  → print "jeff | cosine=0.73 | bbox=..."   (stdout only; no state)
```

The frame source is the **existing** `OpenCVCameraBackend.capture()` (returns PNG
bytes); recognition decodes those bytes to a BGR array. No new camera code.

---

## The recognition decision (Phase 2 = a plain threshold, on purpose)

For each detected face: cosine-similarity its embedding to each of the template's
embeddings, take the **best** match, and report `jeff` if that best cosine ≥ a
configurable **threshold** (default ~0.5 for buffalo_s/ArcFace, to be calibrated
live), else `unknown`. **The raw cosine is always printed**, verdict or not —
that number is the deliverable, not just the label.

No temporal smoothing, no hysteresis, no Bayesian prior. Whether per-frame
recognition is stable *enough* without them is exactly what this slice measures.

**Multiple faces in one frame:** report all of them, each with its own best-match
cosine + verdict. (Still no association across frames — that's tracking.)

---

## Error handling

This slice is a CLI spike, not an always-on safety component, so it may fail
loudly rather than degrade silently:

| Condition | Behavior |
|---|---|
| Camera busy/absent | reuse webcam's existing typed errors; print a clear message, non-zero exit |
| No template enrolled | clear "run `enroll` first" message, non-zero exit |
| No face detected in frame | print "no face detected", exit 0 (a valid, useful result) |
| `insightface`/`onnxruntime` not installed | clear "install the `recognition` extra" message |
| Model weights missing | InsightFace auto-downloads on first use; surface progress/errors |

(The Phase-1 hook's "everything degrades to unknown → cautious" contract is
untouched — this slice never writes the state file the hook reads.)

---

## Testing — the same three tiers, honestly

1. **Pure logic (deterministic, no model, runs in CI):** `cosine()` correctness;
   `decide(cosine, threshold)` boundaries; template save/load round-trip;
   PNG-bytes → BGR-array decode.
2. **Model-dependent (marked, self-skips when the model/onnxruntime is absent):**
   `buffalo_s` detects a face in a bundled fixture image; recognition separates
   two *different* faces (a positive fixture scores high vs itself, low vs a
   stranger). Never gates CI on model presence.
3. **Live validation (with Jeff — REQUIRED before claiming recognition works):**
   enroll Jeff → he sits → `recognize` prints `jeff` with a high cosine; a photo
   or an empty chair prints `unknown` / "no face". Recognition is **not** claimed
   working from fixtures alone.

---

## Install risk — surfaced up front

`insightface` + `onnxruntime` on Windows can be finicky: `insightface` may need
C++ build tools, and first use downloads the `buffalo_s` weights (tens of MB). So
the **first implementation task is a spike** that only installs the `recognition`
extra and runs `buffalo_s` on a single image — if the install fights us, we learn
it before building enrollment/recognition/CLI on top.

---

## Deferred / out of scope (explicit)

- Continuous watcher + `state.json` writing (Phase 3+)
- Tracking (cross-frame association) + Bayesian belief filter — **conditional on
  Phase 2 jitter evidence**
- Tier-0 motion gate
- Template encryption (plaintext + loud TODO for this slice; required before Phase 4)
- Family / multi-identity enrollment
- Any change to the Phase-1 hook, its state contract, or the live env
