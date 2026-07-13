from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldSpec:
    key: str
    label: str
    input_type: str
    required: bool
    options: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubmissionResult:
    submitted: bool
    confirmation_url: str | None = None
    confirmation_text: str | None = None
    screenshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class PlatformAdapter(ABC):
    name: str

    @abstractmethod
    async def open_application(self, url: str) -> None: ...

    @abstractmethod
    async def identify_fields(self) -> list[FieldSpec]: ...

    @abstractmethod
    async def fill_fields(self, answers: dict[str, Any]) -> None: ...

    @abstractmethod
    async def upload_documents(self, resume_path: str, cover_letter_path: str | None) -> None: ...

    @abstractmethod
    async def validate(self) -> ValidationResult: ...

    @abstractmethod
    async def submit(self, mode: str) -> SubmissionResult: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, type[PlatformAdapter]] = {}

    def register(self, adapter: type[PlatformAdapter]) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> type[PlatformAdapter]:
        if name not in self._adapters:
            raise KeyError(f"No adapter registered for {name}")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)
