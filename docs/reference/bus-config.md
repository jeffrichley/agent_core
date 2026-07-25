# Bus config keys

All bus configuration lives under the `bus:` block in `agent_core.yaml` (and any
per-agent config that overrides it). The schema is enforced by Pydantic with
`extra="forbid"`, which means a typo'd key name raises a `ValidationError` at
daemon boot rather than silently using the default — you will know immediately if
you mis-spell a key.

The sections below list every recognised key with its YAML type, default value, and
a short description of what it controls.

## bus:

These keys appear directly under `bus:` in `agent_core.yaml`.

| Key | Type | Default | Effect |
|---|---|---|---|
| `storage_path` | `string` | `~/.agent-core/bus.sqlite` | Path to the SQLite mailbox file. Tilde expansion is supported. The bus writes every envelope to this file before dispatching, so a crash between publish and ack does not lose the message. Must point to a writable path; the bus creates the file on first run. |
| `redelivery_timeout_seconds` | `integer` | `300` | Seconds an in-flight delivery is allowed before being considered stuck and requeued. The redelivery sweep checks envelopes that have been in-flight longer than this and requeues them for another attempt (up to `max_delivery_attempts`). |
| `max_delivery_attempts` | `integer` | `5` | Maximum number of delivery attempts per envelope. Once exhausted, the envelope is moved to dead-letter and is no longer retried. Dead-lettered envelopes remain in the mailbox for inspection. |
| `ttl_sweep_seconds` | `integer` | `60` | How often (in seconds) the TTL sweep loop runs. The sweep marks any envelope whose `expires_at` timestamp is in the past as `expired`, preventing it from being delivered after its window closes. |
| `redelivery_sweep_seconds` | `integer` | `10` | How often (in seconds) the redelivery sweep loop runs. The sweep finds all envelopes that have been in-flight longer than `redelivery_timeout_seconds` and requeues them. |
| `acked_retention_days` | `integer` | `14` | Number of days to retain acknowledged envelopes in the mailbox before they are pruned. Lowering this value reduces disk usage; raising it extends the audit window. |
| `max_pending_per_endpoint` | `integer` | `10000` | Maximum number of pending envelopes allowed per endpoint. Publishing raises `MailboxFull` when this limit is reached, protecting the bus from unbounded queue growth if an endpoint falls behind. |
| `slow_deliver_warn_seconds` | `number` | `5.0` | Emit a `SlowDeliverWarning` log entry when `deliver()` takes longer than this many seconds. Set to `0` or a negative value to disable the watchdog entirely. |
| `watchdog_timeout_seconds` | `integer` | `90` | OS-level liveness watchdog: an OS thread calls `os._exit()` if the asyncio event loop stops bumping the heartbeat for this many seconds. Protects against a hung event loop that would otherwise block graceful shutdown. Set to `0` or a negative value to disable. |
| `backup_dir` | `string \| null` | `null` | Directory where periodic `VACUUM INTO` snapshots are written. `null` disables backups entirely. In production this must point to a **different physical volume** than `storage_path` — a backup on the same drive is worthless when that drive fails. |
| `backup_interval_seconds` | `integer` | `3600` | Cadence of the backup snapshot loop in seconds. Only takes effect when `backup_dir` is set. Default is 3 600 s (1 hour). |

## bus.supervisor:

These keys appear under `bus.supervisor:` in `agent_core.yaml` and control the
supervision layer (`EndpointSupervisor`) that monitors endpoint health and manages
restart and circuit-breaker behaviour.

| Key | Type | Default | Effect |
|---|---|---|---|
| `restart_backoff_base_seconds` | `integer` | `1` | Base delay in seconds for exponential restart backoff. After the first failure the supervisor waits this long before attempting to restart the endpoint. Must be greater than 0. |
| `restart_backoff_factor` | `integer` | `2` | Multiplier applied to the backoff delay on each successive restart. A factor of 2 doubles the wait time after every failure, capped at `restart_backoff_cap_seconds`. Must be at least 1. |
| `restart_backoff_cap_seconds` | `integer` | `60` | Maximum restart backoff delay in seconds. Prevents the wait from growing without bound no matter how many consecutive failures occur. Must be greater than 0. |
| `restart_jitter` | `string` | `"full"` | Jitter strategy applied to the computed restart backoff. `"full"` picks a delay uniformly between 0 and the computed value (recommended for large fleets to avoid thundering-herd restarts). `"equal"` uses half the computed value plus a random half. `"none"` uses the computed backoff directly. |
| `restarts_before_quarantine` | `integer` | `5` | Number of consecutive restart failures before the supervisor quarantines the endpoint. A quarantined endpoint is no longer restarted automatically; the supervisor probes it every `probe_interval_seconds` seconds to check whether it can recover. Must be at least 1. |
| `probe_interval_seconds` | `integer` | `300` | How often (in seconds) the supervisor probes a quarantined endpoint. A successful probe returns the endpoint to normal supervision. Must be greater than 0. |
| `delivery_backoff_base_seconds` | `integer` | `2` | Base delay in seconds for exponential delivery-failure backoff. Applied when consecutive `deliver()` calls raise an exception that does not indicate unavailability. Must be greater than 0. |
| `delivery_backoff_factor` | `integer` | `2` | Multiplier applied to the delivery backoff delay on each successive failure. Must be at least 1. |
| `delivery_backoff_cap_seconds` | `integer` | `60` | Maximum delivery backoff delay in seconds. Must be greater than 0. |
| `deliver_failures_before_breaker` | `integer` | `5` | Number of consecutive delivery failures before the circuit breaker opens. Once open, the supervisor backs off rather than retrying the endpoint immediately, giving it time to recover. Must be at least 1. |
