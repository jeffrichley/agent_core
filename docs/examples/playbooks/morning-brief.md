# Morning brief — Pepper

Pepper's daily morning brief. Composed by the brief framework
(cutover #09): the orchestrator gathers context against the
playbook's `gather_config`, an agent composes section content via
the MCP tool surface, and the result is delivered to Discord and
to a daily markdown file.

Place at `${agent_root}/Memory/playbooks/morning_brief.md`. The
filename's stem must match `brief_type` (the orchestrator loads
`<playbooks_path>/<brief_type>.md`).

## Metadata

```yaml
brief_type: morning_brief
voice: pepper
schedule:
  cron: "0 7 * * *"
  scheduler: "pepper-scheduler"
gather_config: ${agent_root}/Memory/gather/morning.yaml
```

## Destinations

Two parallel deliveries — Discord embed for the live read,
markdown file for the durable record. Both are configured here so
the framework fans out at submit time.

```yaml
destinations:
  - type: discord_embed
    config:
      channel_id: "REPLACE_WITH_CHANNEL_ID"
      # Bus endpoint name of the Discord adapter that will publish this
      # embed. Default is ``discord``; override when the DiscordEndpoint
      # is registered under a different name (e.g. ``discord-pepper`` if
      # following the agent-* convention). Caught on testbot 2026-05-05:
      # without this, the brief silently failed Discord delivery with
      # ``publish to unregistered endpoint 'discord'`` while markdown
      # delivered fine. See cutover-agent-playbook bug #6.
      discord_endpoint_name: "REPLACE_WITH_DISCORD_ENDPOINT_NAME"
  - type: markdown_file
    config:
      path: ${agent_root}/Memory/daily/briefs/{{when.date}}-morning.md
```

## Colors

Discord embed colors (decimal). Names are referenced by the
section blocks; static colors look up the palette directly,
dynamic colors pick a name based on a simpleeval expression
evaluated against the gathered context.

```yaml
colors:
  MORNING_GREETING: 5814783
  CALENDAR: 3447003
  EMAIL_OK: 5763719
  EMAIL_URGENT: 15548997
  PRIORITIES: 16776960
  PROJECTS: 10181046
  RECAP: 9807270
  OPEN_LOOPS: 16753920
  WATCH: 3066993
  WEEKLY: 8359053
  WAR: 15105570
```

## Sections

### greeting

```yaml
section_id: greeting
title: "🌅 Morning, Jeff"
color: MORNING_GREETING
required: true
required_context:
  - now
allow_compression: false
fields:
  - name: "Today"
    required: true
    max_chars: 256
    guidance: >
      One-line frame for the day. Day-of-week + a hint of weather
      or what's notable about today. Set the tone before the
      schedule.
```

### calendar_today

```yaml
section_id: calendar_today
title: "📅 Today's calendar"
color: CALENDAR
required: true
required_context:
  - calendar
fields:
  - name: "Schedule"
    required: true
    guidance: >
      Bulleted list of today's events with start times. If no
      events, say so explicitly — silence reads as missing data.
```

### email_status

```yaml
section_id: email_status
title: "📧 Email"
color:
  dynamic: true
  expr: "len(email.urgent) > 0"
  if_true: EMAIL_URGENT
  if_false: EMAIL_OK
required: true
required_context:
  - email
fields:
  - name: "Inbox"
    required: true
    guidance: >
      One-line summary: total unread + count of urgent. List up to
      three urgent senders by name if any.
```

### priorities_today

```yaml
section_id: priorities_today
title: "🎯 Priorities"
color: PRIORITIES
required: true
required_context:
  - projects
fields:
  - name: "Top 3"
    required: true
    max_chars: 512
    guidance: >
      Three concrete things Jeff should tackle today, in priority
      order. Pull from active projects' blockers and next-action
      lists. Each entry one line.
```

### project_status

```yaml
section_id: project_status
title: "🛠 Projects"
color: PROJECTS
required: true
required_context:
  - projects
allow_compression: true
fields:
  - name: "Active"
    required: true
    guidance: >
      One line per active project: name, current state, next move.
      Compress aggressively if many active.
```

### yesterday_recap

```yaml
section_id: yesterday_recap
title: "📜 Yesterday"
color: RECAP
required: true
allow_compression: true
fields:
  - name: "Highlights"
    required: true
    guidance: >
      Two or three sentences on what shipped or moved yesterday.
      Pulled from yesterday's bus log + handoff. Skip filler;
      include one specific reference if possible.
```

### open_loops

```yaml
section_id: open_loops
title: "🔁 Open loops"
color: OPEN_LOOPS
required: true
fields:
  - name: "Awaiting"
    required: true
    guidance: >
      Things waiting on Jeff or on someone else. Format as
      "Waiting on X for Y" — explicit who + why.
```

### watch_list

```yaml
section_id: watch_list
title: "👀 Watch list"
color: WATCH
required: true
allow_compression: true
fields:
  - name: "Watching"
    required: true
    guidance: >
      Things Pepper is keeping an eye on but not yet asking Jeff
      to act on. Surface only if state changed since yesterday;
      otherwise say "no changes" and keep moving.
```

## Conditional sections

### weekly_digest

```yaml
section_id: weekly_digest
title: "📊 Week ahead"
color: WEEKLY
when:
  expr: "now.is_weekly_digest_day"
required_when_active: true
fields:
  - name: "This week"
    required: true
    guidance: >
      Mondays only — preview the week's commitments and any
      single-shot deadlines. Two or three lines.
```

### war_pointer

```yaml
section_id: war_pointer
title: "🗓 Friday review"
color: WAR
when:
  expr: "now.is_friday"
required_when_active: true
fields:
  - name: "Pointer"
    required: true
    guidance: >
      Fridays only — short pointer to Pepper's weekly review
      ritual. One line; no full digest here.
```
