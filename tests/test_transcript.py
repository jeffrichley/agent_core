"""Tests for the shared transcript reader utility."""

import json
from pathlib import Path
from agent_core.transcript import read_transcript


def write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    entries = [{"message": {"role": role, "content": content}} for role, content in turns]
    write_jsonl(path, entries)


class TestReadTranscript:
    def test_reads_simple_transcript(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi there")])
        context, count = read_transcript(transcript)
        assert count == 2
        assert "**User:** Hello" in context
        assert "**Assistant:** Hi there" in context

    def test_filters_non_conversation_roles(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "system", "content": "system prompt"}},
            {"message": {"role": "user", "content": "Hello"}},
            {"message": {"role": "assistant", "content": "Hi"}},
        ]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 2
        assert "system" not in context.lower()

    def test_handles_content_as_list_of_blocks(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [{"message": {"role": "assistant", "content": [
            {"type": "text", "text": "First block"},
            {"type": "text", "text": "Second block"},
        ]}}]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 1
        assert "First block" in context
        assert "Second block" in context

    def test_respects_max_turns(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        turns = [(f"{'user' if i % 2 == 0 else 'assistant'}", f"Turn {i}") for i in range(20)]
        make_transcript(transcript, turns)
        context, count = read_transcript(transcript, max_turns=5)
        assert count == 5
        assert "Turn 19" in context
        assert "Turn 0" not in context

    def test_empty_transcript(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        context, count = read_transcript(transcript)
        assert context == ""
        assert count == 0

    def test_missing_file_returns_empty(self, tmp_path: Path):
        context, count = read_transcript(tmp_path / "missing.jsonl")
        assert context == ""
        assert count == 0

    def test_handles_malformed_json_lines(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        with open(transcript, "w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps({"message": {"role": "user", "content": "Hello"}}) + "\n")
            f.write("{broken\n")
            f.write(json.dumps({"message": {"role": "assistant", "content": "Hi"}}) + "\n")
        context, count = read_transcript(transcript)
        assert count == 2
        assert "Hello" in context
        assert "Hi" in context

    def test_skips_empty_content(self, tmp_path: Path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "user", "content": ""}},
            {"message": {"role": "user", "content": "   "}},
            {"message": {"role": "user", "content": "Real content"}},
        ]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 1
        assert "Real content" in context
