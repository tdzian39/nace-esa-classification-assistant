"""Fetch the four codebooks from a private Vercel Blob store into a local directory.

Why this exists: on Vercel the function's filesystem is read-only except ``/tmp``, the
repository stays public (roadmap D2) and the codebooks are bank-internal, so they can travel
neither in git nor in environment variables (64 KB in total per deployment). They live in a
private Blob store (roadmap D3) and are downloaded once per instance, before the first lookup,
into a temporary directory. The loaders, the consistency check and the version id then work on
those files exactly as they do on a laptop - nothing downstream knows where the files came from.

Verified against the Vercel docs and the ``@vercel/blob`` / ``vercel`` (PyPI) sources on
22 Sept 2026:

* A private blob lives at ``https://<store-id>.private.blob.vercel-storage.com/<pathname>``
  and is read with a plain ``GET`` carrying ``Authorization: Bearer <BLOB_READ_WRITE_TOKEN>``.
  No list or head call is needed, and none is made: both are metered operations, and a Hobby
  team that exceeds its Blob quota loses Blob for 30 days.
* The store id is ``BLOB_STORE_ID`` with any ``store_`` prefix removed, or else the fourth
  ``_``-separated segment of the token (``vercel_blob_rw_<storeId>_<secret>``) - the way both
  official SDKs derive it.
* Reads go through Vercel's CDN cache (default max-age one month). An overwritten file can be
  served stale for up to 60 s, so a codebook update is: upload, wait a minute, redeploy.
* OIDC is not used. In Python the OIDC token belongs to a request, so it is not available when
  an instance starts, and the Python SDK does not support it. The read-write token can also
  overwrite and delete the store: keep it a Sensitive environment variable, keep the store for
  the codebooks alone, and keep the master copies of the files elsewhere.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx

from core.codebooks.errors import CodebookFetchError

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from config.settings import Settings

logger = logging.getLogger(__name__)

#: Where a private blob is served from; ``pathname`` is ``<prefix><file name>``.
PRIVATE_BLOB_URL: Final[str] = "https://{store_id}.private.blob.vercel-storage.com/{pathname}"

#: Every xlsx file is a zip archive; anything else is an error page or a wrong upload.
_XLSX_MAGIC: Final[bytes] = b"PK\x03\x04"

#: Pause between attempts after a timeout or a 5xx, in seconds (multiplied by the attempt).
_RETRY_PAUSE_SECONDS: Final[float] = 0.5


def blob_store_id(token: str | None, store_id: str | None = None) -> str:
    """The id of the store the codebooks live in.

    ``BLOB_STORE_ID`` wins when set (``store_`` prefix optional); otherwise the id embedded in
    the read-write token is used.

    Raises:
        CodebookFetchError: neither gives an id.
    """
    if store_id and store_id.strip():
        return store_id.strip().removeprefix("store_")
    parts = (token or "").split("_")
    if len(parts) >= 5 and parts[:3] == ["vercel", "blob", "rw"] and parts[3]:
        return parts[3]
    raise CodebookFetchError(
        "cannot tell which Blob store holds the codebooks: set BLOB_STORE_ID, or use a "
        "read-write token of the form vercel_blob_rw_<storeId>_<secret>"
    )


def codebook_file_names(settings: Settings) -> tuple[str, ...]:
    """The four configured codebook file names, in loading order."""
    return (
        settings.codebook_cts_ba0036_file,
        settings.codebook_ba0036_valid_file,
        settings.codebook_cts_okec_nace2_file,
        settings.codebook_nace_stat_file,
    )


def default_download_dir() -> Path:
    """``/tmp/nace-esa-codebooks`` on Vercel (and the system temp directory elsewhere)."""
    return Path(tempfile.gettempdir()) / "nace-esa-codebooks"


def fetch_codebooks(
    settings: Settings,
    *,
    target_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Download the four codebooks into ``target_dir`` and return that directory.

    Each file is written atomically (a temporary file renamed into place), so a crash halfway
    never leaves a truncated workbook for the next attempt to load.

    Raises:
        CodebookFetchError: a token or store id is missing, the store refused the token, a
            file is not uploaded, is not an xlsx workbook, or the store could not be reached
            after ``codebook_blob_max_attempts`` attempts.
    """
    token = (
        settings.blob_read_write_token.get_secret_value()
        if settings.blob_read_write_token
        else None
    )
    if not token:
        raise CodebookFetchError(
            "CODEBOOK_SOURCE=blob but BLOB_READ_WRITE_TOKEN is not set; add it to the "
            "project's environment variables (Production and Preview) and redeploy"
        )
    store = blob_store_id(token, settings.blob_store_id)
    directory = target_dir if target_dir is not None else default_download_dir()
    directory.mkdir(parents=True, exist_ok=True)

    owned = client is None
    http = client or httpx.Client(timeout=settings.codebook_blob_timeout_seconds)
    try:
        started = time.perf_counter()
        for name in codebook_file_names(settings):
            pathname = f"{settings.codebook_blob_prefix}{name}"
            url = PRIVATE_BLOB_URL.format(store_id=store, pathname=pathname)
            content = _download(http, url, pathname, token, settings.codebook_blob_max_attempts)
            _write_atomically(directory / name, content)
        logger.info(
            "fetched %d codebooks from Blob store %s in %d ms",
            len(codebook_file_names(settings)),
            store,
            round((time.perf_counter() - started) * 1000),
        )
    finally:
        if owned:
            http.close()
    return directory


def _download(client: httpx.Client, url: str, pathname: str, token: str, attempts: int) -> bytes:
    """GET one private blob; retry timeouts and 5xx, never 4xx."""
    headers = {"Authorization": f"Bearer {token}"}
    last_problem = "no attempt was made"
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            last_problem = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                return _checked(response.content, pathname)
            if response.status_code in (401, 403):
                raise CodebookFetchError(
                    f"the Blob store refused the token for {pathname} (HTTP "
                    f"{response.status_code}); check BLOB_READ_WRITE_TOKEN and BLOB_STORE_ID"
                )
            if response.status_code == 404:
                raise CodebookFetchError(
                    f"{pathname} is not in the Blob store; upload it with "
                    f"'vercel blob put <file> --pathname {pathname} --access private'"
                )
            if response.status_code < 500:
                raise CodebookFetchError(
                    f"the Blob store answered HTTP {response.status_code} for {pathname}"
                )
            last_problem = f"HTTP {response.status_code}"
        if attempt < attempts:
            time.sleep(_RETRY_PAUSE_SECONDS * attempt)
    raise CodebookFetchError(
        f"could not download {pathname} from the Blob store after {attempts} attempt(s): "
        f"{last_problem}"
    )


def _checked(content: bytes, pathname: str) -> bytes:
    if not content.startswith(_XLSX_MAGIC):
        raise CodebookFetchError(
            f"{pathname} in the Blob store is not an xlsx workbook ({len(content)} bytes)"
        )
    return content


def _write_atomically(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
