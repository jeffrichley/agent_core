---
title: "FileInjector + IdentityInjector"
aliases: [file-injector, identity-injector]
tags: [agent-core, hooks, tools]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# FileInjector + IdentityInjector

FileInjector is a generic hook tool that reads a configurable list of files and injects their concatenated contents into session context. IdentityInjector is a thin subclass with identity-appropriate defaults (heading "Identity", missing files silently skipped).

## Key Points

- FileInjector reads files listed in params relative to a base_path
- Each file gets a ## heading in the output, content is concatenated
- Three missing file behaviors: skip (silent), warn (note in output), error (raise)
- IdentityInjector overrides only DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR
- Uses utf-8-sig encoding to handle Windows BOM automatically
- base_path and files are required params — clear errors if missing
- Raises `ValueError` for unrecognized `missing_file_behavior` values instead of silently falling through

## Writing a Custom Injector

Subclass FileInjector and override the class attributes:

```python
class ProjectContextInjector(FileInjector):
    DEFAULT_HEADING = "Project Context"
    DEFAULT_MISSING_BEHAVIOR = "warn"
```

## Related Concepts

- [[concepts/hook-tool-protocol]] - The protocol FileInjector implements
- [[concepts/pipeline-system]] - The pipeline that runs FileInjector
- [[concepts/handoff-writer]] - HandoffWriter writes files that IdentityInjector reads

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
- [[daily/2026-04-13.md]] - Code review fix: ValueError for unrecognized missing_file_behavior
