# qwen-tts source

Vendored. The URL listed in Task 1 (`https://github.com/Qwen/Qwen3-TTS`) returns
404 — the actual upstream lives at `https://github.com/QwenLM/Qwen3-TTS`. Rather
than depend on the misnamed URL or pin against a moving upstream branch, we
vendor a known-good snapshot. The local working copy at
`E:\workspaces\ai\voices2\finetune\upstream\Qwen3-TTS\` is checked out at commit
`022e286b98fbec7e1e916cb940cdf532cd9f488e` ("fix finetuning bug") of
`QwenLM/Qwen3-TTS`, and was copied verbatim into `vendor/Qwen3-TTS/` (full
project root including `LICENSE`, `pyproject.toml`, and the `qwen_tts/`
package; `.git/`, `qwen_tts.egg-info/`, and stray `.DS_Store` files were
stripped). The package is wired
in via `qwen-tts = { path = "vendor/Qwen3-TTS", editable = false }` in this
package's `pyproject.toml`. To refresh: re-copy from the upstream working tree
and update the commit SHA above.
