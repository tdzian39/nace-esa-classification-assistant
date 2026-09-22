"""Fetching the codebooks from a private Vercel Blob store (roadmap E1, decision D3).

The store is driven through ``httpx.MockTransport``; the request shape (host built from the
store id, ``Authorization: Bearer``, no list or head call) is the one verified against the
Vercel docs and SDK sources on 22 Sept 2026.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from config.settings import Settings
from core.codebooks.blob import (
    PRIVATE_BLOB_URL,
    CodebookFetchError,
    blob_store_id,
    codebook_file_names,
    fetch_codebooks,
)
from core.codebooks.loaders import load_and_check
from tests.codebooks.conftest import make_codebook_dir

STORE = "AbCdEf123"
TOKEN = f"vercel_blob_rw_{STORE}_s3cr3tPart"
XLSX = b"PK\x03\x04 pretend workbook"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "codebook_source": "blob",
        "blob_read_write_token": TOKEN,
        "codebook_blob_max_attempts": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def serving(files: dict[str, bytes], seen: list[httpx.Request] | None = None) -> httpx.Client:
    """A client whose transport answers like a private store holding ``files`` by pathname."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(403, text="Forbidden")
        pathname = request.url.path.lstrip("/")
        if pathname in files:
            return httpx.Response(200, content=files[pathname])
        return httpx.Response(404, text="The requested blob does not exist")

    return httpx.Client(transport=httpx.MockTransport(handler))


def all_four(config: Settings, content: bytes = XLSX) -> dict[str, bytes]:
    return {f"codebooks/{name}": content for name in codebook_file_names(config)}


class TestStoreId:
    def test_the_id_is_read_from_the_token(self) -> None:
        assert blob_store_id(TOKEN) == STORE

    def test_an_explicit_store_id_wins_and_its_prefix_is_dropped(self) -> None:
        assert blob_store_id(TOKEN, "store_Zyx987") == "Zyx987"
        assert blob_store_id(None, "Zyx987") == "Zyx987"

    @pytest.mark.parametrize("token", [None, "", "not-a-blob-token", "vercel_blob_rw__secret"])
    def test_without_either_it_says_what_to_set(self, token: str | None) -> None:
        with pytest.raises(CodebookFetchError, match="BLOB_STORE_ID"):
            blob_store_id(token)


class TestFetch:
    def test_each_file_is_read_from_the_private_host_with_the_token(self, tmp_path: Path) -> None:
        config = settings()
        seen: list[httpx.Request] = []
        fetch_codebooks(config, target_dir=tmp_path, client=serving(all_four(config), seen))
        assert [request.url for request in seen] == [
            httpx.URL(PRIVATE_BLOB_URL.format(store_id=STORE, pathname=f"codebooks/{name}"))
            for name in codebook_file_names(config)
        ]
        assert {request.method for request in seen} == {"GET"}

    def test_no_list_or_head_call_is_made(self, tmp_path: Path) -> None:
        """Both are metered; a Hobby team past its quota loses Blob for 30 days."""
        config = settings()
        seen: list[httpx.Request] = []
        fetch_codebooks(config, target_dir=tmp_path, client=serving(all_four(config), seen))
        assert len(seen) == 4
        assert all(
            request.url.host.endswith(".private.blob.vercel-storage.com") for request in seen
        )

    def test_the_files_land_in_the_target_directory(self, tmp_path: Path) -> None:
        config = settings()
        directory = fetch_codebooks(config, target_dir=tmp_path, client=serving(all_four(config)))
        assert directory == tmp_path
        assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
            codebook_file_names(config)
        )
        assert (tmp_path / config.codebook_nace_stat_file).read_bytes() == XLSX

    def test_the_prefix_is_configurable(self, tmp_path: Path) -> None:
        config = settings(codebook_blob_prefix="codebooks/2026-09/")
        files = {f"codebooks/2026-09/{name}": XLSX for name in codebook_file_names(config)}
        fetch_codebooks(config, target_dir=tmp_path, client=serving(files))
        assert (tmp_path / config.codebook_cts_okec_nace2_file).exists()

    def test_the_real_loaders_read_what_was_fetched(self, tmp_path: Path) -> None:
        """End to end: the downloaded files are loaded and checked like local ones."""
        source = make_codebook_dir(tmp_path / "source")
        config = settings()
        files = {
            f"codebooks/{name}": (source / name).read_bytes()
            for name in codebook_file_names(config)
        }
        target = fetch_codebooks(config, target_dir=tmp_path / "fetched", client=serving(files))
        codebooks, report = load_and_check(config, codebook_dir=target, strict=False)
        assert report.ok
        assert codebooks.version.id.startswith("cb-")


class TestFailures:
    def test_a_missing_token_names_the_variable(self, tmp_path: Path) -> None:
        with pytest.raises(CodebookFetchError, match="BLOB_READ_WRITE_TOKEN is not set"):
            fetch_codebooks(settings(blob_read_write_token=None), target_dir=tmp_path)

    def test_a_refused_token_is_reported_as_such(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CodebookFetchError, match="refused the token"):
            fetch_codebooks(settings(), target_dir=tmp_path, client=client)

    def test_a_file_that_was_never_uploaded_says_how_to_upload_it(self, tmp_path: Path) -> None:
        config = settings()
        files = all_four(config)
        del files[f"codebooks/{config.codebook_nace_stat_file}"]
        with pytest.raises(CodebookFetchError, match="vercel blob put .*NACE_STAT.xlsx"):
            fetch_codebooks(config, target_dir=tmp_path, client=serving(files))

    def test_something_that_is_not_an_xlsx_is_refused(self, tmp_path: Path) -> None:
        config = settings()
        with pytest.raises(CodebookFetchError, match="not an xlsx workbook"):
            fetch_codebooks(
                config, target_dir=tmp_path, client=serving(all_four(config, b"<html>"))
            )

    def test_a_timeout_is_retried_and_then_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("core.codebooks.blob.time.sleep", lambda seconds: None)
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectTimeout("timed out", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CodebookFetchError, match="after 2 attempt"):
            fetch_codebooks(settings(), target_dir=tmp_path, client=client)
        assert len(calls) == 2

    def test_a_server_error_is_retried_and_a_second_answer_is_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("core.codebooks.blob.time.sleep", lambda seconds: None)
        answers = iter([httpx.Response(503)] + [httpx.Response(200, content=XLSX)] * 4)

        def handler(request: httpx.Request) -> httpx.Response:
            return next(answers)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_codebooks(settings(), target_dir=tmp_path, client=client)
        assert len(list(tmp_path.glob("*.xlsx"))) == 4

    def test_a_failed_download_leaves_no_partial_file(self, tmp_path: Path) -> None:
        config = settings()
        files = all_four(config)
        del files[f"codebooks/{config.codebook_cts_okec_nace2_file}"]
        with pytest.raises(CodebookFetchError):
            fetch_codebooks(config, target_dir=tmp_path, client=serving(files))
        assert not list(tmp_path.glob(".*"))
