from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataSource(ABC):
    """Interface that all data sources implement."""

    @abstractmethod
    def fetch(self, **kwargs: Any) -> Any: ...
