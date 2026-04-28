"""agent_core.hooks.tools — Built-in hook tools shipped with agent_core.

Each tool in this package implements the HookTool protocol and can be
registered in agent_core.yaml to run at lifecycle events.

Available tools:
    TimeInjector: Injects the current date and time into session context.
    FileInjector: Reads a list of files and injects their contents into session context.
    IdentityInjector: FileInjector preset for agent identity/personality files.
    HandoffWriter: Writes LLM-powered continuity notes before context is lost.
"""
