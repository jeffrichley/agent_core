# Presence Awareness — Design Spec

**Date:** 2026-07-27
**Author:** Wren (brainstormed with Jeff)
**Status:** Design approved; ready for implementation plan
**Home:** the feature lives in the existing `agent-core-webcam` package (not a separate package).

---

## Goal

Give agent-core beings (Wren + Pepper, extensible) a camera-derived sense of **who is physically present at the desk**, delivered into each being's session, so a being can **de-escalate its trust when the person present is not Jeff**.

The purpose evolved through three lenses and serves all three: **identity** (is it Jeff?), **ambient grounding** (a real sense of the physical room), and **security** (the load-bearing one — protect against instructions issued by someone who isn't Jeff, Pepper especially).

### The core safety property: strictly additive

The signal can **only ever de-escalate** a being's trust — never grant power. Therefore the system is **strictly safety-additive**: a perfect read adds maximum caution; an imperfect or spoofed read adds less; but it can *never* reduce the baseline security that already exists (the instruction-source boundary rules). It does not need to be bulletproof to be worth building — it can only help. This is not "fail-safe" (recognition *can* be fooled — a photo, a look-alike); it is "as safe as possible, and never worse than today."

---

## Scope (v1)

- **In:** continuous presence detection + recognition of **Jeff only**; multi-person detection; per-being behavioral levels + configurable injected text; the in-session hook; the camera-owning watcher; single-image-based enrollment of Jeff; the presence-state file contract.
- **Enrolled set:** just Jeff. Everyone else is "present but unknown" (counted, never identified). The enrollment flow is *designed* so household members can be added later without rework — but only Jeff is enrolled in v1.
- **Out of v1 (flagged, not silently dropped):**
  - Consent-flow software — Jeff handles consent socially (a person's willingness to be photographed *is* the consent). Not modeled in code.
  - Liveness / anti-spoof detection — a held-up photo could pass v1. Future hardening.
  - Device/channel cross-check ("is this request from the desktop or the phone?") — likely not reliably knowable; handled instead by being judgment. The tag simply describes *what the desk camera sees*.
  - Family enrollment — designed-for, not built.

---

## Architecture & components (all inside `agent-core-webcam`)

- **Camera watcher** *(new)* — the single, long-running owner of the camera. Runs the tiered pipeline (below), maintains per-person Bayesian identity beliefs, aggregates to a presence state, and atomically writes `state.json`. Also answers on-demand frame requests.
- **Snapshot tool** *(existing, refactored)* — Pepper's `capture_webcam_frame` becomes a thin client that requests a frame *from the watcher* instead of opening the camera itself. One owner ⇒ no contention.
- **Enrollment tool** *(new)* — one-time: capture ~3–10 frames of a person across pose/lighting (via camera or supplied photos) → compute ArcFace embeddings → store encrypted template(s) locally. v1 enrolls Jeff.
- **Presence state file** *(the contract)* — local JSON, atomically written by the watcher, staleness-guarded: `{ updated_at, at_desk, known[], unknown_count, source }`. `known` lists confirmed-enrolled people present; `unknown_count` is the number of unrecognized people (never identified).
- **`presence_injector` hook** *(the in-session reader)* — runs per turn in each being's session; reads `state.json`, and based on that being's configured **level** + **text templates**, injects the appropriate presence guidance. Missing/unreadable/stale → "unknown." Never blocks, never raises.

### Data flow

```
camera → watcher (continuous: motion-gate → detect → track → per-track Bayesian recognition)
       → aggregate → atomic write → ~/.agent-core/presence/state.json
                                          │
      each being's session, per turn:     ▼
        presence_injector hook reads it → injects that being's configured text → context
      snapshot request → watcher → frame
```

The watcher is the **only** thing that touches the camera. One camera → one state file → N being-readers.

---

## The CV pipeline (tracking-by-detection + a motion gate)

Three escalating tiers so a still room costs ~nothing (grounded in the CV research doc):

0. **Motion gate** (every frame, ~sub-ms) — frame-diff / MOG2; if nothing changed, do nothing else this frame.
1. **Detection + tracking** (on motion, ~ms) — a light face detector (**YuNet** via OpenCV, or MediaPipe BlazeFace) every N frames; a cheap IOU/centroid or ByteTrack associator every frame carries boxes + identity labels forward.
2. **Recognition** (rare, event-triggered) — **ArcFace** (InsightFace `buffalo_s`, CPU) embedding compared by cosine to Jeff's enrolled template(s), best-match. Runs on new track / reacquired track / periodic slow refresh — *not* per frame. Because belief is stable (below), this can run every few seconds.

CPU-only; no GPU required. Templates are stored **only** for enrolled people; a transient embedding may be computed for anyone to *test* against the gallery, but a non-match is discarded immediately — never persisted, never used to grow a template.

---

## The recognition decision — a per-person Bayesian belief

Each **tracked person** carries a recursive Bayesian belief over identity — a small probability vector across `{Jeff, unknown, …extensible to family}`. Each cycle:

- **Predict** — apply a **slow-transition prior**: a person is very likely the same identity they were a moment ago (encodes "Jeff sits there for hours; nobody swaps in for 5 seconds"). One noisy frame barely moves the belief; sustained contrary evidence is required to flip it. The transition probability is the single tunable that sets how slow.
- **Update** — multiply in the **observation likelihood**: the ArcFace cosine maps to "how Jeff-like is this frame." Normalize → posterior. The cosine→likelihood mapping is calibrated from Jeff's own enrolled samples, not a generic threshold.

**Decision:** report `confirmed Jeff` only when the posterior `P(Jeff)` crosses a **high, conservative** bar — bias toward missing Jeff over accepting a non-Jeff. Below the bar → `unknown`. The safety bias lives cleanly in that threshold; no ad-hoc hysteresis needed (the slow-transition prior *is* the stability).

**Multiple people:** each track gets its own belief filter. The state aggregates them → `known = [confirmed enrolled present]`, `unknown_count = [tracks not confirmed as anyone enrolled]`.

---

## Behavior — what a being does with the signal

Three levels, cumulative; effected entirely through **injected guidance text** (there is no hard code-gate — the being is an LLM; the hook injects context the being acts on):

1. **Ambient awareness** — the being simply knows who's present. Changes no behavior. A wrong read costs nothing.
2. **+ Shoulder-surf caution** — when an unknown person is in frame, the being holds back sensitive/private output. A wrong "someone's here" just makes it briefly over-discreet.
3. **+ Trust-gating (the point)** — when the person present is *not confirmed Jeff*, the being de-escalates: treats instructions as unverified, confirms before anything sensitive or irreversible. This protects Pepper (and Wren) from instructions issued by someone who isn't Jeff.

The rule throughout: **de-escalate on anomaly, never escalate on a match.** The injected text only ever says *be more careful*, never *do X* — so a wrong/spoofed factual read can only make a being more cautious. (Consistent with the being's standing rule that injected content is untrusted data.)

### Per-being configuration

Each being configures, in its own `agent_core.yaml` hook `params:`:
- **`level`** — `1` / `2` / `3`. Pepper can run full level-3 with strong de-escalation wording; another being could run ambient-only.
- **Injected text templates** — the wording for each situation (Jeff present / unknown present / no-one / stale).

Same shared watcher + state; per-being reaction and voice.

---

## Error handling — everything degrades to "unknown"

There is **no failure that makes a being *less* careful**; the worst case is always "we don't know → be cautious."

| Failure | Degrades to |
|---|---|
| Watcher crashed / not running | file goes stale → staleness guard → **unknown** |
| Camera unplugged / busy | watcher keeps retrying, stops updating → stale → **unknown** |
| Torn / partial write | atomic write prevents it; bad parse → **unknown** |
| Recognition uncertain | Bayesian belief stays below the bar → **unknown** |
| Hook itself errors | hook catches its own error, emits **unknown** (never raises) |

**Watcher lifecycle (v1 = manual start):** the watcher is started **by hand** — a CLI command (e.g. `agent-core-webcam watch`) that Jeff runs. The hook never spawns it; it only reads the state file. If the watcher isn't running, the state goes stale/missing → the staleness guard → "unknown" → safe. Single-instance is trivially guaranteed by "Jeff starts one." *Deferred:* lazy hook-spin-up with a single-instance lock (named mutex / exclusive lockfile / port-bind), idle self-shutdown, and/or a supervised background service — all future refinements, not v1.

---

## Privacy

- **Only enrolled people are templated.** Everyone else is inherently "unknown, counted" — no template is ever built for a random visitor. Automatic, not extra machinery.
- **Templates are biometric data:** stored **encrypted, local-only**, never on the bus. (Embeddings are reversible via model-inversion, so encryption at rest with an OS-keystore/TPM-held key is the standard; exact key handling is an implementation detail for the plan.)
- **The state file carries no imagery** and no per-unknown data — only the compact derived tag.
- **Ephemeral processing:** frames are processed in memory and discarded — the watcher is a sensor, not a recorder.
- Consent is handled socially by Jeff (out of software scope, per above).

---

## Testing — three tiers of confidence

1. **Pure logic (deterministic unit tests, no camera):** motion gate; the Bayesian filter (predict/update, convergence, slow-transition behavior); state read/write; hook rendering; level→text selection; multi-person aggregation.
2. **Detection/recognition on static image fixtures:** detection finds faces in known images; recognition separates Jeff's enrolled face from a stranger's. No live camera.
3. **Live validation (with Jeff, required before claiming recognition works):** run the watcher on the real camera; Jeff sits → watch the belief converge to `confirmed Jeff`; a photo / another person → confirm it stays `unknown`. Recognition is **not** claimed working from fixtures alone.

---

## Note on prior work

An earlier `agent-core-presence` package (the `state.py` contract, the `presence_injector` hook, the motion gate + their tests) was written ahead of this design. It is sound and reusable, but its **packaging was a guess** — per this design it relocates **into `agent-core-webcam`**. Cheap to move; the logic carries over.

---

## Deferred / out of scope (explicit)

- Consent-flow software (social consent instead)
- Liveness / anti-spoof detection (future hardening)
- Device/channel cross-check for the phone case (being judgment instead)
- Family enrollment (designed-for, not built in v1)
- Auto-spin-up / single-instance lock / supervised service / idle self-shutdown — v1 is **manual start** (`agent-core-webcam watch` by hand)
