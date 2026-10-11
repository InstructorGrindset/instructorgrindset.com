"""Source registry and source_id reference validation."""
from __future__ import annotations

from typing import Iterable

from .schemas.common import Source


class SourceConflictError(ValueError):
    pass


class SourceRegistry:
    """Every Source known to a build, keyed by id.

    The same id may be declared in several research files as long as the URL
    matches; the most recent ``accessed_date`` wins. Two different URLs under
    one id is an error.
    """

    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}
        self._origin: dict[str, str] = {}

    def add(self, source: Source, origin: str) -> None:
        existing = self._sources.get(source.id)
        if existing is None:
            self._sources[source.id] = source
            self._origin[source.id] = origin
            return
        if existing.url != source.url:
            raise SourceConflictError(
                f"source id '{source.id}' has url {existing.url} in {self._origin[source.id]} "
                f"and {source.url} in {origin}"
            )
        if source.accessed_date > existing.accessed_date:
            self._sources[source.id] = source

    def add_all(self, sources: Iterable[Source], origin: str) -> None:
        for s in sources:
            self.add(s, origin)

    def ids(self) -> set[str]:
        return set(self._sources)

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._sources

    def __len__(self) -> int:
        return len(self._sources)

    def get(self, source_id: str) -> Source:
        return self._sources[source_id]

    def context(self) -> dict:
        """Pydantic validation context that enforces source_id references."""
        return {"source_ids": self.ids()}

    def sorted(self, only: Iterable[str] | None = None) -> list[Source]:
        keep = set(only) if only is not None else None
        return [self._sources[k] for k in sorted(self._sources) if keep is None or k in keep]
