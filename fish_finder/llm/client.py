from __future__ import annotations

import logging
import os

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


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

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        log.debug("LLM request: system=%d chars, user=%d chars", len(system), len(user))
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = response.content[0].text
        log.debug("LLM response: %d chars, stop=%s", len(text), response.stop_reason)
        return text
