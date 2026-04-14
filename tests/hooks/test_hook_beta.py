"""Test hook that returns a known pattern of ~4K chars."""
import json
import sys

content = "BETA_START " + ("B" * 8000) + " BETA_END"

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": content,
    }
}
print(json.dumps(output))
