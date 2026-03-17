from __future__ import annotations

import logging
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from ..disk_cache import PersistentTTLCache

load_dotenv()

log = logging.getLogger(__name__)
_LLM_CACHE = PersistentTTLCache[str]("llm_text", ttl_seconds=2 * 60 * 60, max_entries=256)


class LLMClient:
    """Thin wrapper around the Anthropic API."""

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. "
                "Copy .env.example to .env and add your key."
            )
        self.client = Anthropic(api_key=api_key)
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        log.debug("LLM client initialised with model %s", self.model)

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        model: str | None = None,
        cache_key: str | None = None,
    ) -> str:
        selected_model = model or self.model
        resolved_cache_key = cache_key or f"{selected_model}:{max_tokens}:{system}:{user}"
        cached = _LLM_CACHE.get(resolved_cache_key)
        if cached is not None:
            log.debug("LLM cache hit")
            return cached

        log.debug("LLM request: system=%d chars, user=%d chars", len(system), len(user))
        response = self.client.messages.create(
            model=selected_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = response.content[0].text
        log.debug("LLM response: %d chars, stop=%s", len(text), response.stop_reason)
        _LLM_CACHE.set(resolved_cache_key, text)
        return text
