"""Job source adapter interface + shared DTO."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class JobPostingDTO:
    source: str
    external_id: str
    title: str
    company: str
    location: str = ""
    remote: bool = False
    description: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)


class JobSourceAdapter(ABC):
    @abstractmethod
    def search(self, query: str = "", location: str = "") -> list[JobPostingDTO]:
        """Return job postings matching `query`/`location` (adapter decides how to filter)."""
        raise NotImplementedError
