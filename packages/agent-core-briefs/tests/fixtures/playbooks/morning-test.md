# Morning Brief Test Playbook

## Metadata
```yaml
brief_type: morning_brief
voice: test
schedule:
  cron: "0 7 * * *"
gather_config: ${agent_root}/Memory/gather/morning.yaml
```

## Destinations
```yaml
destinations:
  - type: discord_embed
    config:
      channel_id: "12345"
  - type: markdown_file
    config:
      path: ${agent_root}/Memory/daily/briefs/{{when.date}}-morning.md
```

## Colors
```yaml
colors:
  TEST_RED: 15548997
  TEST_GREEN: 5763719
  TEST_BLUE: 3447003
```

## Sections

### greeting
```yaml
section_id: greeting
title: "🌅 Morning"
color: TEST_RED
required: true
fields:
  - name: "Today"
    required: true
    guidance: "One-line frame for the day."
```

### calendar_today
```yaml
section_id: calendar_today
title: "📅 Today's calendar"
color: TEST_BLUE
required: true
fields:
  - name: "Schedule"
    required: true
```

### priorities_today
```yaml
section_id: priorities_today
title: "🎯 Priorities"
color:
  dynamic: true
  expr: "any(p.blockers for p in projects.active)"
  if_true: TEST_RED
  if_false: TEST_GREEN
required: true
fields:
  - name: "Top 3"
    required: true
```

## Conditional sections

### weekly_digest
```yaml
section_id: weekly_digest
title: "📊 Week ahead"
color: TEST_GREEN
when:
  expr: "now.day_of_week == 'Monday'"
required_when_active: true
fields:
  - name: "This week"
    required: true
```
