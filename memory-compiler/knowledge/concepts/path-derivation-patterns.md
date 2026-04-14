---
title: "Path Derivation Patterns"
aliases: [path-resolution, file-path-patterns]
tags: [python, patterns, packaging]
sources:
  - "daily/2026-04-13.md"
created: 2026-04-13
updated: 2026-04-13
---

# Path Derivation Patterns

Path derivation in Python projects should use configuration-based or parameter-based resolution rather than `__file__` parent traversal. Walking up N `.parent` levels from `__file__` is fragile and breaks when code is installed as a package via pip.

## Key Points

- `Path(__file__).parent.parent...` breaks when package install location differs from development layout
- Derive paths from config params or runtime inputs instead (e.g., `output_path` from YAML config)
- State file paths should be siblings of their associated output files, not relative to source code location
- When nesting a project as a subdirectory, all `parent` traversal counts change — a common source of bugs
- The agent_core.yaml params system provides a natural place to declare path configuration

## Details

The HandoffWriter tool originally derived its state file path by traversing up from `__file__` to find the project root. This worked during development but would break when the package was installed via pip, since the installed location bears no relationship to the project directory structure. The fix was to derive the state file path from the `output_path` parameter already available in the tool's config, making the path resolution independent of where the source code lives.

A related issue surfaced during the memory-compiler integration. The original claude-memory-compiler used two levels of `.parent` traversal to reach the project root. When nested as a subdirectory of agent_core, all seven files with path traversal needed an additional `.parent` call. This kind of fragility is inherent to the `__file__` traversal pattern — any restructuring of the directory hierarchy silently breaks all path references.

The recommended pattern is to accept paths as explicit parameters (via YAML config, CLI arguments, or environment variables) and resolve everything relative to those declared roots. This makes the code relocatable and testable with arbitrary directory structures.

## Related Concepts

- [[concepts/agent-core-yaml-config]] - The config system that provides path parameters to tools
- [[concepts/handoff-writer]] - The tool where this pattern was corrected
- [[concepts/memory-compiler-integration]] - Where directory nesting exposed __file__ traversal fragility

## Sources

- [[daily/2026-04-13.md]] - HandoffWriter state file path fix and memory-compiler path adjustments
