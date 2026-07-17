# Spec: de-Wren-ify adopter-path docs and config samples (issue #396)

## Goal

Replace hardcoded being-identity strings (`~/.wren`, `wrenrichley`, `jeffrichley/foreman`, "Pepper + Wren home", `target_being: wren`) in every adopter-facing document and config sample with generic placeholders so that an external adopter — not Wren or Pepper — can follow the docs without being confused or blocked by someone else's identity. The two primary targets named in the world-class eval are `packages/agent-core-inbound/README.md` ("hardcodes `~/.wren`/`wrenrichley`/foreman") and `docs/setup/daemon.md` ("the live Pepper+Wren home"). See issue #396.

## Acceptance criteria

- `grep -ri 'wrenrichley\|\.wren[^/a-z]\|target_being:.*wren\|jeffrichley/foreman' packages/agent-core-inbound/README.md docs/setup/daemon.md docs/getting-started/ docs/examples/ docs/index.md` returns zero matches.
- `grep -i 'pepper.*wren\|wren.*pepper\|pepper + wren\|wren.*home\|pepper.*home' docs/setup/daemon.md` returns zero matches.
- `packages/agent-core-inbound/README.md`: section "2. Write Wren's allowance file" is renamed to "2. Write your allowance file"; every hardcoded `~/.wren`, `wrenrichley`, and `jeffrichley/foreman` is replaced with `~/.<being>`, `<your-github-login>`, and `<org>/<repo>` respectively.
- A new file `docs/examples/being-agent-core.yaml` exists, uses `~/.<being>/Memory` style path placeholders throughout, and contains no hardcoded being name, no `C:\\Users\\` paths.
- A new file `docs/examples/endpoints.d/being.yaml` exists with the same generic-path convention.
- `docs/getting-started/index.md` "Real-world examples" tip points to `being-agent-core.yaml`, not `pepper-agent-core.yaml`.
- `docs/index.md` contains no named being (Wren/Pepper) references in the adopter-visible note.
- `docs/examples/playbooks/morning-brief.md` YAML config block uses `<your-being>` instead of `pepper` for `voice:` and `scheduler:`; the file header describes it as an example, not Pepper's specific config.
- `docs/examples/voice-agent-core.yaml` model path uses `<path-to-Qwen3-TTS-model>` instead of `C:\\workspaces\\ai\\...`.
- No production code or test files changed. `just check` passes (docs changes do not affect Python coverage).

## Approach

No GoF pattern applies. This is a targeted search-and-replace across adopter-path markdown and YAML files; the approach is SRP — each file change has one responsibility (remove being-identity; preserve technical accuracy).

**Placeholder conventions used consistently across all changes:**
| Old string | Replacement |
|---|---|
| `~/.wren/` | `~/.<being>/` |
| `wrenrichley` | `<your-github-login>` |
| `jeffrichley/foreman` | `<org>/<repo>` |
| `target_being: wren` | `target_being: <your-being>` |
| `C:\\Users\\jeffr\\.pepper\\Memory` | `~/.<being>/Memory` |
| `voice: pepper` | `voice: <your-being>` |
| `agent_name: "Pepper"` | `agent_name: "<YourBeing>"` |
| "Pepper + Wren home" / "Pepper/Wren home" | "the live daemon home" / "the live agent home" |

**Adopter path vs. internal:** The adopter path consists of `README.md`, `docs/index.md`, `docs/getting-started/`, `docs/setup/daemon.md`, `packages/*/README.md`, and `docs/examples/`. Everything else — `docs/cutover/`, `docs/requirements/`, `docs/BACKLOG.md`, `docs/ROADMAP.md`, `docs/HANDOFF-*.md`, `docs/superpowers/`, `docs/migrations/`, `docs/hatchery/`, `packages/agent-core-hatchery/docs/`, `packages/core/skills/`, and `packages/core/src/agent_core/skills/` — is internal documentation not on the adopter path and is explicitly out of scope.

**`packages/agent-core-inbound/README.md`** is the highest-impact change: the entire runbook reads as Wren's personal setup guide. The section "Write Wren's allowance file" walks through `~/.wren/...` paths and hardcodes her GitHub login and the `jeffrichley/foreman` repo. All technical content (TOML schema, endpoint YAML shape, smoke-test steps) is preserved; only the identity strings are replaced with the conventions above. Prose like "GitHub metadata Wren never uses" becomes "GitHub metadata your being doesn't need"; "Wren's bus inbox" becomes "your being's bus inbox".

**`docs/setup/daemon.md`** has three sentences that assume a Pepper+Wren audience. Only those three phrases change; the historical incident note ("Pepper went offline mid-session on 2026-05-10") stays because it is concrete causality for a design decision, not an identity assumption for the reader.

**`docs/examples/pepper-agent-core.yaml`** and **`docs/examples/endpoints.d/pepper.yaml`** are Pepper's actual runtime configs. The right approach is to add new generic files (`being-agent-core.yaml`, `endpoints.d/being.yaml`) with `~/.<being>/` path templates, then update the getting-started link to point there. `pepper-agent-core.yaml` and `pepper.yaml` remain untouched as Pepper's runtime reference configs. The world-class-eval concern was that the getting-started guide directed new adopters to a Pepper-specific file — fixing the pointer is sufficient.

**`docs/index.md`** names "Wren, Pepper, and others" in a factual `!!! note` about the project. This is safe to genericize: "AI agent beings alongside their human partners" communicates the same intent without identifying the operators.

**`docs/examples/playbooks/morning-brief.md`** is placed under `docs/examples/` (adopter-visible) but is clearly Pepper's brief spec. Genericize the YAML config block at the top (lines ~17–37 set `voice: pepper`, `scheduler: "pepper-scheduler"`, Discord endpoint comment) to `<your-being>` placeholders. The Jinja2 content template blocks that use "Jeff" for narrative voice are illustrative and can stay — an adopter understands they'd substitute their human partner's name. Add a header comment making the file's intended example role explicit.

**`docs/examples/voice-agent-core.yaml`** has Jeff's real Windows model path on the `model_path:` line (line 40). Replace with `<path-to-Qwen3-TTS-12Hz-1.7B-Base>` to match the `C:\\path\\to\\...` placeholder style used on lines 46 and 50 of the same file.

## Sub-requests (topologically sorted)

1. **De-Wren-ify `packages/agent-core-inbound/README.md`**

   Specific changes (file is 115 lines, verified above):
   - L28: `### 2. Write Wren's allowance file` → `### 2. Write your allowance file`
   - L30: `` `~/.wren/.config/inbound/github-allowance.toml` `` → `` `~/.<being>/.config/inbound/github-allowance.toml` ``
   - L40: `"requested_reviewer.login" = "wrenrichley"` → `"requested_reviewer.login" = "<your-github-login>"`
   - L43: `reason = "PR review requested on me"` — keep as-is (generic)
   - L47: `repo = "jeffrichley/foreman"` → `repo = "<org>/<repo>"`
   - L49: `reason = "Foreman escalation — needs operator unstick"` — keep as-is (generic example; remove or keep as illustrative)
   - L55: `GitHub metadata Wren never uses` → `GitHub metadata your being doesn't need`
   - L70: `target_being: wren` → `target_being: <your-being>`
   - L74: `github_allowance_path: ~/.wren/.config/inbound/github-allowance.toml` → `github_allowance_path: ~/.<being>/.config/inbound/github-allowance.toml`
   - L75: `audit_log_path: ~/.wren/state/inbound-audit.jsonl` → `audit_log_path: ~/.<being>/state/inbound-audit.jsonl`
   - L91: `In the \`jeffrichley/foreman\` repo settings → Webhooks → Add webhook:` → `In your repo settings → Webhooks → Add webhook:`
   - L93: `https://router.<tailnet>.ts.net/github` — keep as-is (generic)
   - L100: `On any PR in \`jeffrichley/foreman\`, request a review from \`@wrenrichley\`.` → `On any PR in your configured repo, request a review from \`@<your-github-login>\`.`
   - L102: `~/.wren/state/inbound-audit.jsonl` → `~/.<being>/state/inbound-audit.jsonl`
   - L103: `Wren's bus inbox` → `your being's bus inbox`
   - L105: `match = { "requested_reviewer.login" = "wrenrichley" }` → `match = { "requested_reviewer.login" = "<your-github-login>" }`

2. **De-Wren-ify `docs/setup/daemon.md`**

   Three targeted phrase replacements (verified at the lines below):
   - L22 (instance table prose): `every command resolves to \`prod\` with port 8789 — the live Pepper + Wren home.` → `every command resolves to \`prod\` with port 8789 — the live agent home.`
   - L81 (source instance section): `The source instance lets you iterate on daemon code without bouncing the prod daemon that Pepper and Wren depend on.` → `The source instance lets you iterate on daemon code without bouncing the live prod daemon.`
   - L154 (venv isolation rationale): `The source instance accepts the editable workspace venv on purpose — it exists for iteration, and is never the live Pepper/Wren home.` → `The source instance accepts the editable workspace venv on purpose — it exists for iteration, and is never the live prod home.`
   - All other content unchanged (the "Pepper went offline" historical note at L157 stays).

3. **Create `docs/examples/being-agent-core.yaml`**

   New file. Mirrors `pepper-agent-core.yaml`'s pipeline structure (SessionStart identity_injector chain, UserPromptSubmit time_injector, PreCompact/SessionEnd handoff_writer, bus_hooks daily_raw_jsonl, handoff-jobs endpoint) but with all hardcoded paths replaced by `~/.<being>/Memory` style placeholders and `<your-being>`/`<YourBeing>` for being names. Top comment explains this is the adopter template; points to `pepper-agent-core.yaml` as a production-shaped real-world reference. Do NOT modify `pepper-agent-core.yaml`.

4. **Create `docs/examples/endpoints.d/being.yaml`**

   New file. Mirrors `pepper.yaml`'s endpoint list (claude_code_mcp, briefs_orchestrator, discord, webcam) with `~/.<being>/` paths, `<your-being>` names, `<YOUR_BEING>_DISCORD_TOKEN` env var style, `<your-being>` targets and description strings. Comment at top notes this is the template; `pepper.yaml` is the production-shaped real instance. Do NOT modify `pepper.yaml`.

5. **Update `docs/getting-started/index.md`**

   Change the "Real-world examples" tip (L44–L46):
   - Old: `See [\`docs/examples/pepper-agent-core.yaml\`](...) for a production-shaped config with session hooks, handoff pipelines, and bus logging.`
   - New: `See [\`docs/examples/being-agent-core.yaml\`](...) for a template config with session hooks, handoff pipelines, and bus logging. For a fully-populated real-world shape, \`docs/examples/pepper-agent-core.yaml\` is a production config for one specific being.`
   (URL in the new link points to `docs/examples/being-agent-core.yaml` in the repo.)

6. **Update `docs/index.md`**

   Change the `!!! note` body (L9):
   - Old: `agent-core is developed and operated by AI agent beings (Wren, Pepper, and others) alongside their human partner.`
   - New: `agent-core is developed and operated by AI agent beings alongside their human partners.`

7. **Update `docs/examples/playbooks/morning-brief.md`** (YAML config block only)

   Only the YAML frontmatter/config block at the top of the file changes. Content template sections (the Jinja prompt strings) that use "Jeff" as illustrative human-partner text are left as-is with a header comment noting adopters should substitute their own human-partner name.
   - L1: `# Morning brief — Pepper` → `# Morning brief — example`
   - L3: `Pepper's daily morning brief.` → `An example daily morning brief.`
   - L17: `voice: pepper` → `voice: <your-being>`
   - L20: `scheduler: "pepper-scheduler"` → `scheduler: "<your-being>-scheduler"`
   - L37 (Discord comment): `# is registered under a different name (e.g. \`\`discord-pepper\`\` if` → `# is registered under a different name (e.g. \`\`discord-<your-being>\`\` if`
   - Add a comment near the top of the file: `# This file uses "Jeff" as an example human-partner name in content templates. Substitute your own.`

8. **Update `docs/examples/voice-agent-core.yaml`**

   Single line change (L40):
   - Old: `model_path: "C:\\workspaces\\ai\\Qwen3-TTS-EasyFinetuning\\models\\Qwen\\Qwen3-TTS-12Hz-1.7B-Base"`
   - New: `model_path: "<path-to-Qwen3-TTS-12Hz-1.7B-Base>"`

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-inbound/README.md` | **Modify** — replace ~10 being-specific strings with generic `~/.<being>/`, `<your-github-login>`, `<org>/<repo>` placeholders; all technical content preserved |
| `docs/setup/daemon.md` | **Modify** — replace 3 "Pepper + Wren home" / "Pepper and Wren depend on" phrases with generic "live agent home" / "live prod daemon" |
| `docs/examples/being-agent-core.yaml` | **Create** — generic pipeline config using `~/.<being>/Memory` placeholders; adopter template counterpart to `pepper-agent-core.yaml` |
| `docs/examples/endpoints.d/being.yaml` | **Create** — generic endpoints.d fragment using `~/.<being>/` paths; adopter template counterpart to `pepper.yaml` |
| `docs/getting-started/index.md` | **Modify** — "Real-world examples" tip updated to reference `being-agent-core.yaml` as the primary template, `pepper-agent-core.yaml` noted as a real-world reference |
| `docs/index.md` | **Modify** — remove named beings from the `!!! note` block |
| `docs/examples/playbooks/morning-brief.md` | **Modify** — YAML config block: `pepper` → `<your-being>` in `voice:` and `scheduler:`; file header genericized; header comment added for template use |
| `docs/examples/voice-agent-core.yaml` | **Modify** — `model_path:` value replaced with `<path-to-Qwen3-TTS-12Hz-1.7B-Base>` placeholder |

## Alternatives considered

1. **Genericize `pepper-agent-core.yaml` in place instead of creating `being-agent-core.yaml`.** Simpler (fewer files), but Pepper's runtime config is a living document referenced by her own `agent_core.yaml` setup. Modifying it risks confusing the real config with the template. Creating a parallel file preserves Pepper's reference while giving adopters a clean template. Ruled out.

2. **Remove `docs/examples/pepper-agent-core.yaml` and `pepper.yaml` entirely.** Avoids any confusion about which is the "real" example, but these files are production-shaped configs with real operational detail (bus_hooks, handoff_jobs endpoint, source of truth for Pepper's pipeline). Removing them loses value. Ruled out.

3. **Scope only `packages/agent-core-inbound/README.md` and `docs/setup/daemon.md` (the two named in the world-class eval).** Narrower, faster. But `docs/getting-started/index.md` continues linking to `pepper-agent-core.yaml` as the primary example, and the `!!! note` in `docs/index.md` still names Wren and Pepper — an adopter landing on either page would still see being-specific material. The acceptance criterion ("grep of adopter-path docs/config samples shows no hardcoded being/user identity") requires broader coverage. Ruled out.

4. **Rename `packages/agent-core-inbound/README.md`'s TOML example to use a made-up login like `your-agent-login`.** This was the approach taken; `<your-github-login>` is the placeholder convention because it matches the style used for `<AGENT>` in `daemon.md`'s `.mcp.json` example. Chosen.

## Open questions

None. The world-class eval and Track A spec (#389) are unambiguous about the scope; all affected file paths and line numbers have been verified against the actual codebase. The files out of scope (internal docs, being-personal skills, `docs/cutover/`, `docs/requirements/`) were confirmed internal by review.

## Out of scope

- Internal docs not on the adopter path: `docs/cutover/`, `docs/requirements/`, `docs/BACKLOG.md`, `docs/ROADMAP.md`, `docs/HANDOFF-*.md`, `docs/superpowers/`, `docs/migrations/`, `docs/hatchery/wren.yaml`, `packages/agent-core-hatchery/docs/`.
- Being-personal skills: `packages/core/skills/scheduler/references/` (documents live running jobs for Wren and Pepper by name; these are internal operational docs, not adopter templates) and `packages/core/src/agent_core/skills/email/SKILL.md` (Pepper's email skill, intentionally Pepper-specific).
- The `jeffrichley/agent_core` GitHub repo URLs in doc cross-references — these are legitimate source pointers for the project owner, not being-identity strings.
- Changing the install source from GitHub releases to PyPI — that is A1's and A2.1's concern; A2.3 has no hard A1 dependency.
- A2.4 (hatch-your-own-being walkthrough, CONTRIBUTING, per-package READMEs) — a separate later ticket.
- A2.5 (per-package READMEs for `core`, `busproxy`, etc.) — a separate later ticket.
