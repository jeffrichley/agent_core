# Presence v2 — per-track multi-class identity

**Author:** Wren · **Date:** 2026-08-03 · **Status:** design, not approved
**Supersedes:** the single-template / scene-level-threshold model in
`2026-07-27-presence-awareness-design.md` and `2026-07-28-presence-state-loop.md`
**Evidence:** `E:\workspaces\ai\presence-calibration\`, measurements in
`Memory/projects/presence-awareness/calibration-2026-08-03.md`

## Why the current design has to change

v1 asks one question per frame: *is this cosine above a threshold?* Live
calibration on 2026-08-03 showed that question cannot be answered safely.

- The threshold shipped at **0.50**, uncalibrated, **below** the range an impostor
  can reach. A specific family member is estimated to score ~0.635 at this desk.
  They would have been accepted as Jeff.
- Jeff's own score is **not a stable quantity**. Posed at the camera: 0.914.
  Working at the screen: 0.674, rising to 0.776 after pose-matched enrollment.
  Drifting **0.07 within a single 80-second sitting.**
- Every tick decides independently and writes straight to state, so one bad frame
  is one wrong state. One false alarm per hour at a 2s tick needs 99.94%
  per-frame accuracy. Head-turns will never clear that.

No threshold fixes any of this, because the failure is structural: an absolute
score compared against an absolute number, with no memory and no alternative
hypothesis.

## What the measurements support instead

**Relative beats absolute.** Multi-class identification over five enrolled people
scored **100% (93/93)** leave-one-image-out, median margin 0.496, never negative.
The same test cross-domain (webcam query, photo galleries) scored **9/10** — the
absolute scores collapsed from ~0.78 to ~0.35, but the *ranking* survived,
because a domain shift moves every class together and argmax ignores a common
offset. That is exactly the parameter v1's threshold could not survive.

**Margin beats score for open-set rejection.** Leave-one-*person*-out gives real
stranger data:

```
            best score                        margin to runner-up
STRANGER    median 0.185  p95 0.267  max 0.312       ~0.05
ENROLLED    median 0.695  p05 0.456  min 0.282       ~0.50
```

Best-score distributions overlap in the tails (0.312 vs 0.282) — a score cut
alone cannot separate them. Margins differ by an order of magnitude. A stranger
resembles everyone equally badly; an enrolled person resembles exactly one person
distinctively.

**Association is free at this cadence.** Measured over 783 frames:

```
gap 0.06s  IoU median 0.980   gap 2.0s  IoU median 0.937   gap 5.0s  IoU median 0.900
zero pairs below 0.3 at any gap
```

A seated person barely moves. Tracking, normally a 15–30 fps technique, works
with enormous headroom at a 2-second tick.

## Design

### Tracks

Detections are associated frame-to-frame by IoU (cut 0.3). An unmatched detection
births a track; a track with no detection for `grace_ticks` dies.

**Identity is constant within a track.** This is the load-bearing property. It
removes the transition prior, the decay, and `p_stay` entirely — those existed
only because scene-level state must allow the person to change between ticks. A
different person means a *different track*.

Evidence accumulates per track, per enrolled identity `i`:

```
log_odds[i] += clip( log P(scores_t | i) − log P(scores_t | ¬i), −C, +C )
```

The clip is not a tuning knob, it is a correctness requirement. Unclipped
Gaussian likelihoods assign `e^−20` to a single outlying frame; in simulation
that inverted the filter, making false alarms **worse** than raw per-frame
decisions (28% vs 5.1%) because a knocked-down belief needs many good frames to
recover.

**Observation is the full score vector**, not the best score. `{0.7 Jeff, 0.2
others}` and `{0.7 Jeff, 0.65 Brandon}` are very different evidence; a scalar
discards the difference that matters most.

### States and observations, kept separate

Hidden per track: `{jeff, cindy, monica, jacob, brandon, stranger}`.
Hidden per scene: derived — seat = largest/most central live track; bystanders =
other live tracks; `nobody` = no live tracks.

**"No face" is an observation, not a state.** Its likelihood is high under
`nobody` and *equal* under every person, so it moves belief toward an empty desk
without moving it between people. Concretely `P(no face | person present) ≈ 0.5%`
— measured 0/1424, 0/1400, 13/1485 across three sittings — which is small but
must not be zero: Jeff bending down must not erase Jeff. Driving confidence to
zero on a missing face would encode "someone else is probably here," which the
observation does not support.

### The stranger class

`P(scores | stranger)` is fitted from leave-one-person-out data — five held-out
people, genuinely open-set. Rejection uses **both** a minimum best-score and a
minimum margin, with the margin carrying most of the weight.

Five people is a thin basis. It is, however, measured rather than invented, which
is what the v1 threshold was not.

### Security properties

- **Track birth resets trust.** A new person sits down → new track → starts at the
  population prior with zero accumulated confidence. Jeff's hop-out/hop-in
  scenario is handled structurally, not by a paranoid prior.
- **Detection latency is one tick (2.0s)** in simulation across every prior and
  bar tested. The slow-transition intuition costs nothing in security: a filter
  smooths in proportion to *surprise*, and a different person is maximally
  surprising.
- **Multiple simultaneous people** fall out of multiple tracks, preserving the
  shoulder-surf capability a single-state model structurally cannot express.

## Known gaps — do not ship claiming these are solved

1. **ID-switching is untested and is the security-relevant tracking failure.**
   Two faces crossing can hand track A's accumulated trust to person B. Mitigation
   is an identity-consistency check on association with a track split on
   contradiction. **Requires two people in frame to validate.**
2. **The stranger model rests on five held-out people.**
3. **Confidence bounds are weak where it counts.** Zero errors in n trials bounds
   the error rate at ~3/n. n=64 images → ≥94.4%. But the operational claim is
   cross-domain, where n=10 → **≥69.2%**. Demonstrating 99.9% needs ~3000
   *independent* trials, and frames within a sitting are not independent.
   ⇒ Bound the tail from the **margin distribution**, a continuous statistic, not
   from counting successes. Far more information per sample.
4. **Drift is unaddressed.** 0.07 inside one sitting. All galleries need
   multi-session data before any number is treated as settled.
5. **Family galleries are photos; Jeff's is webcam.** Enrolling everyone at this
   camera makes every comparison within-domain (100%, margin 0.496) and deletes
   the domain-offset parameter that currently underwrites the whole security
   argument.

## Sequencing

1. **Enroll each person at this webcam** (~2 min each, `shoot.py`). Collapses
   gaps 3 and 5 and is the highest-value action available.
2. Cosine + margin logging in the watcher, persisted. Turns time into data;
   addresses gap 4. Strictly additive, no behavior change.
3. Tracks + association + per-track accumulation.
4. Stranger rejection calibrated from enrolled data.
5. Audit log — falls out of per-track identity, and is the reason Jeff wants
   this. Own retention policy, own decision on who can read it.

**Do not set a final threshold before step 1 and 2.** The current committed value
of 0.75 is known-wrong (accepts Jeff 55% of the time) and is a placeholder.
