# agent-core-inbound

Deny-by-default inbound notifications router for agent-core beings.
External signals (GitHub webhooks, Gmail messages, calendar events)
flow through per-source **connectors** that classify each event as
`Allow{tier, reason}` or `Deny`. The router de-dupes, rate-limits,
delivers via the agent-core bus, and writes an audit log.

See `docs/superpowers/specs/2026-06-20-inbound-notifications-design.md`
in the agent_core repo for the full design.

## Bringing v1.a online (operator runbook)

### 1. Generate the GitHub webhook secret

Pick any high-entropy string; e.g.:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as an env var in the daemon's environment (e.g., your `~/.agent-core/.env` or systemd unit):

```bash
FOREMAN_GITHUB_WEBHOOK_SECRET=<paste-here>
```

### 2. Write Wren's allowance file

`~/.wren/.config/inbound/github-allowance.toml`:

```toml
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"
```

The router watches the file's mtime and reloads on every webhook delivery — edit the TOML and the next event picks up the new rules without restarting the daemon.

### 3. Register the endpoint in agent_core.yaml

The inbound-notifications endpoint registers via the `agent_core` pluggy hook (see `agent_core_inbound/plugin.py` — the `inbound.github` type is registered automatically once the package is installed).

Add this entry to your `agent_core.yaml`'s `endpoints:` list:

```yaml
endpoints:
  - type: inbound.github
    name: inbound
    params:
      target_being: wren
      listen_host: 127.0.0.1
      listen_port: 8765
      webhook_secret_env: FOREMAN_GITHUB_WEBHOOK_SECRET
      github_allowance_path: ~/.wren/.config/inbound/github-allowance.toml
      audit_log_path: ~/.wren/state/inbound-audit.jsonl
      rate_limit_per_minute: 30
```

The runner reads each entry's `type` and looks it up in the pluggy-registered endpoint types map. The `name` is the bus addressing name (also surfaces in `agent-core ps`). All `params` are passed as constructor kwargs to `InboundEndpoint`.

### 4. Start Tailscale Funnel

```bash
tailscale funnel 8765
```

Note the issued `https://router.<tailnet>.ts.net` URL.

### 5. Configure the GitHub webhook

In the `jeffrichley/foreman` repo settings → Webhooks → Add webhook:

- **Payload URL:** `https://router.<tailnet>.ts.net/github`
- **Content type:** `application/json`
- **Secret:** the same value you stored in `FOREMAN_GITHUB_WEBHOOK_SECRET`
- **Which events:** `Pull request reviews` (specifically `Pull request review requested`)

### 6. Smoke test

On any PR in `jeffrichley/foreman`, request a review from `@wrenrichley`. Within ~10s:

- `~/.wren/state/inbound-audit.jsonl` gains an `allow` line with `rule_id=pr_review_requested_foreman`.
- Wren's bus inbox receives a `Notification` envelope (urgency `red`).

If you instead see a `deny` line, double-check `reviewer = "wrenrichley"` in the allowance TOML against the actual reviewer GitHub login.

### Troubleshooting

- **All POSTs land 401:** the env var secret does not match the GitHub webhook secret. Re-paste both ends.
- **`BusBootError: unknown endpoint type 'inbound.github'`:** the `agent-core-inbound` package isn't installed in the daemon's environment, or its entry point isn't being discovered. Run `uv sync` (or your install path equivalent) and confirm `python -c "import agent_core_inbound.plugin"` succeeds.
- **Webhook delivers but no bus envelope:** check the audit log first. If `deny` lines appear, the allowance rule isn't matching — verify `event`, `repo`, `reviewer` fields against the actual webhook payload (visible in GitHub's webhook delivery history).
- **No audit log writes at all:** the endpoint isn't seeing the POST. Confirm Tailscale Funnel is active (`tailscale funnel status`) and the daemon log shows `InboundEndpoint(name=inbound) started on 127.0.0.1:8765`.
- **Operator missing `X-GitHub-Event` header (e.g. testing with curl):** the handler returns 204 silently for unmodeled event types — exercise the wiring via GitHub's "Recent Deliveries" / "Redeliver" UI which sends the correct headers.
