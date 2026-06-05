# Cross-being scheduler coordination

The agent-core scheduler delivers events to a single target endpoint per job. If a being needs to verify or coordinate with another being's scheduled job, that crosses a trust boundary — the receiving being is the only one who knows whether they actually acted on the event. This file documents the standard patterns.

## Pattern 1: Verify another being's job fired

You want to know if a job fired into someone else's inbox. The DB / `list_jobs` reply tells you whether the daemon dispatched the envelope; only the receiver tells you whether they got it and acted.

### Step 1: check what the daemon thinks (you can do this alone)

```python
# Canonical: call list_jobs and find the job in the reply
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}},
)
# Consume the Acknowledgment, parse payload.note as JSON, find your job by name.
# The reply has `next_run` per job but NOT `last_fire_time` — for that, fall back
# to the SQLite read recipe in inspect.md.
```

If the `next_run` field matches when you'd expect (e.g., 7 days after the just-passed fire), the daemon dispatched. If you need explicit `last_fire_time`, read it directly from SQLite per the recipe in `references/inspect.md`.

### Step 2: ask the receiving being via bus

Even with a confirmed dispatch, the envelope might have been dropped, ignored, or routed wrong on receipt. Confirm by asking:

```python
mcp__agent-core__send(
    to="<receiving-being>",
    kind="TextMessage",
    payload={
        "kind": "TextMessage",
        "text": (
            f"Cross-check ping: did `{job_id}` fire to your inbox at <expected-time>? "
            "One-line answer is enough: fired / did not fire / fired but at a different time."
        )
    }
)
```

A one-line answer is usually all you need. Don't request long explanations unless the answer is unexpected — keep the cross-being chatter light.

### Anti-pattern (caught 2026-05-28)

Concluding "the schedule didn't fire" from the absence of evidence in YOUR inbox when the schedule actually targets someone else's inbox. Your inbox only carries jobs whose target is you. Always check the args tuple's third element (or the `target` field in `list_jobs` results) to know who a job targets before reasoning about fire history.

## Pattern 2: Request a schedule change in another being's domain

You believe a schedule needs editing but the job targets someone else (so it's part of their operational domain). The scheduler endpoint doesn't gate by caller — you CAN call `update_job` yourself from your session and the change will take effect. But there's a social-trust dimension: another being's scheduled jobs are part of how they work, and changing their schedule unannounced is rude at best and breaks coordination at worst.

### Step 0: check the current schedule first

Before proposing a change, call `list_jobs` and confirm the change isn't already live (or the job isn't already different from what you think it is). It's an embarrassing 30-second mistake to pitch a proposal that's already been applied — especially across a session boundary where you may be working from stale recall.

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={"kind": "ToolInvocation", "tool": "list_jobs", "args": {}},
)
# Consume the Acknowledgment, find the target job, verify current state.
```

### Step 1: ask first

The right shape is usually: **ask first (one line), get a quick yes, then apply yourself OR let them apply.** Two flavors depending on context:

### Flavor A — apply-after-ack (you're authorized to apply)

```python
mcp__agent-core__send(
    to="<owning-being>",
    kind="TextMessage",
    payload={
        "kind": "TextMessage",
        "text": (
            f"Schedule change request: `{job_id}`\n\n"
            f"**Current prompt:**\n```\n<paste current>\n```\n\n"
            f"**Proposed prompt:**\n```\n<paste new>\n```\n\n"
            f"**Reason:** <why this matters>\n\n"
            f"OK if I apply via `update_job` from my session? One-line ack is "
            f"enough — I'll skip if you'd rather drive it yourself."
        ),
    },
)
# Wait for ack. On yes, fire the update_job ToolInvocation. On no, hand the
# proposed change back to them and let them apply it.
```

### Flavor B — let-them-apply (you're surfacing, not driving)

```python
mcp__agent-core__send(
    to="<owning-being>",
    kind="TextMessage",
    payload={
        "kind": "TextMessage",
        "text": (
            f"Heads up: I think `{job_id}` needs a prompt change. Reason: "
            f"<why>. Proposed text:\n\n```\n<paste new>\n```\n\n"
            f"Apply via `update_job` from your session when you have a moment — "
            f"`{job_id}` is in your domain so I'll leave the actual edit to you."
        ),
    },
)
```

Default to Flavor B when the receiving being is active and has scheduler access. Default to Flavor A when they're asleep/away and the change is time-sensitive.

### Don't bypass with direct SQL

Even though the SQLite store is filesystem-writable from any being's process, hand-mutating it is dangerous (see `inspect.md` "Why no direct SQL mutation"). The bus path is always safer. There's no scenario where direct SQL is the right tool for a mutation.

## Pattern 3: Two beings coordinating on a shared schedule

Some jobs need acknowledgment from a being other than the firing target. Example: Pepper's `apex_weekly_slots` fires at Thu 16:00 ET to Pepper. The design intent was that Wren would fire a +5min check-on-Pepper job to verify Pepper's apex run succeeded — but that Wren-side check job was never created.

### How to do it right

If you need a follow-up verification from a different being, create a SEPARATE scheduled job targeting the verifying being, offset by N minutes:

```python
mcp__agent-core__send(
    to="scheduler",
    kind="ToolInvocation",
    payload={
        "kind": "ToolInvocation",
        "tool": "create_job",
        "args": {
            "name": "wren-apex-verify",
            "trigger": "cron",
            "schedule": {"day_of_week": "thu", "hour": 16, "minute": 5},
            "target": "wren",
            "prompt": (
                "Verify pepper's `apex_weekly_slots` job fired at 16:00 ET today.\n"
                "1. Call list_jobs and find apex_weekly_slots — verify next_run matches "
                "next Thursday 16:00 ET (means today's fire happened).\n"
                "2. For explicit last_fire_time, read it from scheduler.db directly.\n"
                "3. If timestamp matches today 16:00 ET, ping pepper to confirm she "
                "received and acted.\n"
                "4. If timestamp is stale OR pepper says she didn't get it, surface "
                "to Jeff in <channel>."
            ),
            "timezone": "America/New_York",
        },
    },
)
```

The +5 minute offset gives Pepper time to receive and start processing before Wren checks. Adjust to fit the verifying being's own latency budget.

### Don't fake the check inside the same firing

The temptation is to put "and also tell Wren" inside Pepper's apex prompt. Two reasons not to:

1. Pepper would have to send the cross-check envelope every fire, even when nothing's wrong. That's noise.
2. If Pepper's fire FAILED (daemon down, prompt error), there's no apex prompt running to send the check envelope. The whole point of the verification is to catch failures Pepper can't self-report.

A separate scheduled job in the verifying being's name is the right shape.

## Pattern 4: Quiet-when-clear cross-being status

If a job targets another being but you'd reasonably want to know about a failure, the right shape is **silent on success, page on failure**:

The owning being's job is responsible for self-reporting only on failure. Their default behavior should be no output when everything works. Cross-being verification (Pattern 3) catches the cases where the OWNING being's failure prevents them from self-reporting at all.

Two layers of safety:
- Layer 1: owning being's prompt instructs "send a red embed to <channel> on failure" (catches errors the being's prompt can detect)
- Layer 2: cross-being verifier from Pattern 3 (catches errors that take the being's prompt offline entirely)

**Concrete production exemplars of Layer 1** (live in `~/.agent-core/jobs.d/`):

- `auth_health_probe` (pepper-targeted, hourly at :15) — runs `gog gmail search`, `gog calendar events`, `gh auth status`. Stays silent if all three exit 0. Sends a red embed (color 15548997) to `#pepper-chat` titled "🔴 Auth probe failure" only on a non-zero exit, with the failing check + error tail.
- `service_liveness_probe` (pepper-targeted, hourly on the hour) — calls `mcp__agent-core__list_endpoints` (expects ~9 endpoints), runs an `ack` round-trip via the `stub` endpoint, sanity-checks daemon log size. Silent on success. Yellow FYI embed (color 16776960) to `#pepper-chat` on the first failing surface.

Both are good shapes to model new probes on: the on-failure embed has color + title + the exact failing-check evidence, so the consumer (Jeff, in those cases) gets enough to diagnose without re-running the probe. The silent-on-success default keeps the channel uncluttered.

## Quick reference: what each being typically owns

This is a snapshot, not a contract — verify against `list_jobs` for current truth.

- **pepper** owns: daily_sync, heartbeat, pepper_thinking, pepper_time, weekly_war, weekly_digest, weekly_reflection, vault_lint, github_backup, monthly_nise_reports, apex_weekly_slots, weekend_daily_sync, auth_health_probe, service_liveness_probe, pepper_self_check, nightly_reflection
- **wren** owns: wren-heartbeat, wren-service_liveness_probe, wren-auth_health_probe, wren-vault_lint, wren-nightly_reflection
- **briefs.orchestrator** owns: testbot-morning-brief
- **briefs.pepper** owns: morning_briefing, evening_routine *(live; these are the canonical briefs-routed variants. If a `list_jobs` reply also shows `morning_briefing` / `evening_routine` targeting bare `pepper` instead of `briefs.pepper`, those are legacy entries that pre-date the briefs orchestrator routing — flag for removal next time you're in the area)*

The wren-* naming convention is the cleanest. Older pepper jobs use bare names; new jobs should prefix with the being name for clarity.
