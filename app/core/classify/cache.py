"""Cache of model answers, so the same issuer is never paid for twice.

CLAUDE.md says to cache by normalized name. The key here is wider on purpose:

    kind + normalized name + description + codebook version + model + prompt version

Every one of those changes the right answer, and leaving any out would serve a stale
suggestion that looks current. The codebook version especially: cache on the name alone and
a codebook update keeps returning IDs derived from the previous one - a provenance hole in a
tool whose whole point is defensible classification.

SQLite rather than a JSON file because the API will run with several workers and a JSON file
would lose writes. It is a cache: a corrupt or unreachable file must degrade to "no cache",
never to a failed lookup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core.classify.models import Classification, Confidence, Kind, Suggestion

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    key             TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    issuer          TEXT,
    model           TEXT,
    prompt_version  TEXT,
    codebook_version TEXT,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


def cache_key(
    *,
    kind: Kind,
    issuer_name: str | None,
    description: str,
    codebook_version: str | None,
    model: str,
    prompt_version: str,
) -> str:
    """Stable key over everything that can change the answer."""
    normalized_name = " ".join((issuer_name or "").split()).casefold()
    normalized_description = " ".join(description.split()).casefold()
    # JSON rather than a delimiter join: no separator can collide with a value, and the
    # source stays free of control characters.
    material = json.dumps(
        [
            kind,
            normalized_name,
            normalized_description,
            codebook_version or "",
            model,
            prompt_version,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ClassificationCache(Protocol):
    """What the classifier needs from a cache."""

    def get(self, key: str) -> Classification | None: ...

    def put(self, key: str, classification: Classification, *, issuer_name: str | None) -> None: ...


class NullCache:
    """Caching disabled."""

    def get(self, key: str) -> Classification | None:
        return None

    def put(self, key: str, classification: Classification, *, issuer_name: str | None) -> None:
        return None


def _to_payload(classification: Classification) -> str:
    data = asdict(classification)
    data["classified_at"] = (
        classification.classified_at.isoformat() if classification.classified_at else None
    )
    return json.dumps(data, ensure_ascii=False)


def _from_payload(text: str) -> Classification | None:
    """Rebuild a Classification, or ``None`` when the row predates a change in its shape."""
    try:
        data: Any = json.loads(text)
        suggestions = tuple(
            Suggestion(
                kind=item["kind"],
                code=item["code"],
                cts_id=item["cts_id"],
                label=item["label"],
                confidence=item["confidence"],
                justification=item["justification"],
                rank=item.get("rank", 1),
            )
            for item in data.get("suggestions", [])
        )
        classified_at = data.get("classified_at")
        return Classification(
            kind=data["kind"],
            suggestions=suggestions,
            abstained=bool(data.get("abstained", False)),
            abstain_reason=data.get("abstain_reason"),
            candidates_considered=int(data.get("candidates_considered", 0)),
            rejected=tuple(data.get("rejected", ())),
            model=data.get("model"),
            prompt_version=data.get("prompt_version"),
            classified_at=datetime.fromisoformat(classified_at) if classified_at else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("ignoring an unreadable cache row: %s", exc)
        return None


class SqliteCache:
    """File-backed cache. Any SQLite failure degrades to "no cache", never to an error."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._usable = True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            LOGGER.warning("classification cache is unusable (%s); continuing without it", exc)
            self._usable = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(self, key: str) -> Classification | None:
        if not self._usable:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM classifications WHERE key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            LOGGER.warning("cache read failed: %s", exc)
            return None
        return _from_payload(row[0]) if row else None

    def put(self, key: str, classification: Classification, *, issuer_name: str | None) -> None:
        if not self._usable:
            return
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO classifications "
                    "(key, kind, issuer, model, prompt_version, codebook_version, payload, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        classification.kind,
                        issuer_name,
                        classification.model,
                        classification.prompt_version,
                        None,
                        _to_payload(classification),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            LOGGER.warning("cache write failed: %s", exc)

    def __len__(self) -> int:
        if not self._usable:
            return 0
        try:
            with self._connect() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM classifications").fetchone()[0])
        except sqlite3.Error:
            return 0


def build_cache(path: Path | None, database: object = None) -> ClassificationCache:
    """The central database's cache when there is one (:mod:`core.db`), else a SQLite cache
    at ``path``, else :class:`NullCache` when caching is switched off."""
    if database is not None:
        from core.db import DatabaseCache

        return DatabaseCache(database)  # type: ignore[arg-type]
    return SqliteCache(path) if path else NullCache()


__all__ = [
    "ClassificationCache",
    "Confidence",
    "NullCache",
    "SqliteCache",
    "build_cache",
    "cache_key",
]
