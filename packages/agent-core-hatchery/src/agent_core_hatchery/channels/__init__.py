"""Channel scaffolding (Discord, webcam, GitHub backup).

Each module exports a `scaffold_<name>(config)` function that:
- Returns None if the channel is disabled.
- Performs side effects (write env file, write hook script) and returns
  a dict (the endpoint or job block) to be appended to daemon fragments.
"""
