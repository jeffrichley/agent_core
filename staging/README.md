# agent_core staging harness — the pre-prod gate

A **standing test container** you run a candidate `agent_core` version through
**before promoting it to the live Wren + Pepper daemon.** Nothing here touches
the host: the container is the isolation wall — its own filesystem, ports,
SQLite storage, and (later) its own Discord bot. The live beings are never the
test subject.

## The promotion flow

```
candidate version ──▶ ./shakedown.sh ──▶ PASS ──▶ promote to live daemon
   (main / a tag)          │                         (agent-core daemon
                           └─▶ FAIL ──▶ do NOT promote   refresh --instance prod)
```

## Usage

```bash
# Default: test the published PyPI package (also validates the publish).
./shakedown.sh

# Test a specific published version:
INSTALL_MODE=pypi VERSION=0.8.2 ./shakedown.sh

# Test an unpublished branch / main from source (for pre-release candidates):
INSTALL_MODE=source REF=main ./shakedown.sh
```

Exit 0 = the candidate booted clean in isolation → cleared to promote.
Non-zero = do **not** promote; the output names what broke.

## What Phase 1 checks (this version)

The high-risk unknown is: *does the assembled daemon actually boot and run its
endpoints on the candidate version?* — the thing 140 commits of CI-green work
never proved as a live system. Phase 1 asserts:

- the candidate **installs** at all (build fails loudly if not),
- the **daemon boots** (`bus status` responds),
- **endpoints register** (stub + scheduler),
- **boot logs are clean** (no tracebacks/fatals),
- the **HTTP surface is reachable**.

No Discord, no brain, no vault — deliberately. Zero risk to the live beings,
no Discord-token conflicts.

## Isolation guarantees

| resource | live daemon | this harness |
|---|---|---|
| process/filesystem | host | **container** |
| bus HTTP port | `127.0.0.1:8789` | container `8789` → host `127.0.0.1:18789` |
| storage | host `~/.agent-core` | container `/data/bus.sqlite` (ephemeral) |
| Discord bots | discord-wren / -pepper / -testbot | **none** (Phase 1) |
| vault / creds | host KeePass | **none** (Phase 1) |

## Roadmap

- **Phase 1 (done):** isolated substrate boot gate (above).
- **Phase 2:** add the `discord-testbot` bot so a real Discord message exercises
  the v0.8.x Discord endpoint. Requires the one-connection host-handoff:
  disable the `discord-testbot` endpoint in the host daemon
  (`~/.agent-core/agent_core.yaml`) first — one gateway connection per token —
  then pass `DISCORD_TESTBOT_TOKEN` into the container.
- **Phase 3:** attach a Claude session (brain) that drains testbot's mailbox and
  replies — the full loop: Discord → v0.8.x bus → endpoint → mailbox → brain →
  reply. Plus an explicit supervision test (kill an endpoint, confirm restart).

## Design notes

- **Install from source *or* PyPI.** A staging gate should test the *code*
  (main/a branch), but Phase 1 defaults to PyPI because it's the fastest path
  and doubles as a publish-validation. Use `INSTALL_MODE=source REF=<branch>`
  for pre-release candidates.
- **Why a container, given agent_core is native-first (no Docker)?** The
  container is a *test harness*, not the deployment model — adopters still run
  native. It just gives the strongest isolation for a first live shakedown.
- **agent_core already has the pieces:** `agent-core daemon install
  --instance {prod|source|test}` gives per-instance frozen venvs, and `.testbot`
  + `discord-testbot` are a ready-made test being + isolated Discord surface.
  This harness wraps that machinery in a container for full isolation.
