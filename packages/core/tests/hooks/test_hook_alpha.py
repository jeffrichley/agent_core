"""Test hook that returns a known pattern of ~4K chars."""
import json
import sys

content = "ALPHA_START " + ("A" * 8000) + " ALPHA_END"

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": content,
    }
}
print(json.dumps(output))
