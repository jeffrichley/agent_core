# Morning Brief Orchestrator Test Playbook

A minimal playbook used by the orchestrator tests. The ``gather_config``
points at a sibling YAML file under ``tests/fixtures/gather/``.

## Metadata
```yaml
brief_type: morning_brief
voice: "Pepper, warm"
gather_config: ${gather_config_path}
```

## Destinations
```yaml
destinations:
  - type: noop
    config: {}
```

## Colors
```yaml
colors:
  RED: 15548997
  GREEN: 5763719
```

## Sections

### greeting
```yaml
section_id: greeting
title: "Morning"
color: RED
required: true
fields:
  - name: "Today"
    required: true
```

### calendar_today
```yaml
section_id: calendar_today
title: "Today's calendar"
color: RED
required: true
fields:
  - name: "Schedule"
```

### footer_optional
```yaml
section_id: footer_optional
title: "Footer"
color: GREEN
fields:
  - name: "Note"
```

## Conditional sections

### weekly_digest
```yaml
section_id: weekly_digest
title: "Week ahead"
color: GREEN
when:
  expr: "calendar.key == 'value'"
required_when_active: true
fields:
  - name: "This week"
    required: true
```

### never_active
```yaml
section_id: never_active
title: "Never"
color: GREEN
when:
  expr: "False"
fields:
  - name: "x"
```
