"""Jinja2-based template rendering.

Substitution variables come from HatchConfig. StrictUndefined is enabled
so missing variables raise loudly rather than silently rendering empty.
Path-typed variables (vault_root, vault_memory_path) are stringified for
YAML safety.
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined, UndefinedError

from agent_core_hatchery.config import HatchConfig


class LeftoverBraceError(Exception):
    """Raised when a rendered template still contains {{ }} markers."""


class Renderer:
    def __init__(self, config: HatchConfig) -> None:
        self._config = config
        self._env = Environment(
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    def _substitution_dict(self) -> dict[str, str]:
        """All Jinja2 variables documented in the spec, as strings."""
        cfg = self._config
        return {
            "being_name": cfg.being_name,
            "being_name_lower": cfg.being_name_lower,
            "being_name_upper": cfg.being_name_upper,
            "being_emoji": cfg.being_emoji,
            "being_role_placeholder": cfg.being_role_placeholder
            or "(role to be defined by your primary human and you)",
            "primary_human_name": cfg.primary_human_name,
            "hatched_date": cfg.hatched_date,
            "endpoint_name": cfg.endpoint_name or cfg.being_name_lower,
            "vault_root": cfg.resolved_vault_root().as_posix(),
            "vault_memory_path": cfg.resolved_vault_memory_path().as_posix(),
            "daemon_handoff_url": "http://127.0.0.1:8789/internal/handoff-jobs",
            "discord_token_env": cfg.discord_token_env,
        }

    def render_string(self, template_source: str) -> str:
        try:
            tmpl = self._env.from_string(template_source)
            rendered = tmpl.render(**self._substitution_dict())
        except UndefinedError as exc:
            raise LeftoverBraceError(str(exc)) from exc
        if "{{" in rendered or "}}" in rendered:
            raise LeftoverBraceError(
                f"Rendered output still contains Jinja braces: {rendered[:200]!r}"
            )
        return rendered
