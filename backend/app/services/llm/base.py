"""Abstract LLM provider interface. All providers must implement `generate`."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate a text completion for `prompt`, optionally guided by a system prompt."""
        raise NotImplementedError
