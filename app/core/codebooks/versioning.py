"""Content-derived versioning of the codebook set.

Every output row of the application carries the codebook version, so the version must be
deterministic: the same file contents always yield the same id, regardless of path,
modification time or machine, and any changed cell yields a different id.

The fingerprint is the sha256 of the file bytes. Note that an xlsx workbook also stores its
own save timestamp, so re-exporting identical cells produces a new (equally valid) version id;
a loader that reads rows from another source should fingerprint the normalized rows instead.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from core.codebooks.errors import CodebookFileError
from core.codebooks.models import CodebookFile, CodebookVersion

_CHUNK_SIZE = 64 * 1024
_VERSION_PREFIX = "cb-"
_VERSION_DIGEST_CHARS = 16


def fingerprint_file(path: Path, name: str, row_count: int) -> CodebookFile:
    """Hash ``path`` (streaming sha256) and capture its size and mtime (UTC).

    Raises:
        CodebookFileError: the file cannot be read.
    """
    path = Path(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
        stat = path.stat()
    except OSError as exc:
        raise CodebookFileError(f"Cannot read codebook file {path}: {exc}") from exc
    return CodebookFile(
        name=name,
        path=path,
        sha256=digest.hexdigest(),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        row_count=row_count,
    )


def build_version(
    files: Sequence[CodebookFile],
    label: str | None = None,
    *,
    loaded_at: datetime | None = None,
) -> CodebookVersion:
    """Combine the file fingerprints into one :class:`CodebookVersion`.

    The id is ``"cb-"`` plus the first 16 hex characters of the sha256 over
    ``"\\n".join(f"{name}={sha256}")`` for the files sorted by logical name, followed by
    ``"+<label>"`` when a non-blank label is given. ``loaded_at`` defaults to now (UTC) and
    must be timezone-aware when supplied.
    """
    if loaded_at is None:
        loaded_at = datetime.now(UTC)
    elif loaded_at.tzinfo is None or loaded_at.utcoffset() is None:
        raise ValueError("loaded_at must be a timezone-aware datetime")
    ordered = tuple(sorted(files, key=lambda item: item.name))
    material = "\n".join(f"{item.name}={item.sha256}" for item in ordered).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:_VERSION_DIGEST_CHARS]
    clean_label = label.strip() if label is not None else None
    if not clean_label:
        clean_label = None
    version_id = f"{_VERSION_PREFIX}{digest}"
    if clean_label:
        version_id = f"{version_id}+{clean_label}"
    return CodebookVersion(id=version_id, label=clean_label, loaded_at=loaded_at, files=ordered)
