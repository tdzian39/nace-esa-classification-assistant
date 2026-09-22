"""The API on Vercel (roadmap E1): lazy codebooks, 503 instead of a dead instance, /probe.

On Vercel a startup that raises takes the whole instance down, ``/health`` included, so the
codebooks load once per process - at startup or on first use - and a failure becomes a 503
with the reason while ``/health``, the form and ``/probe`` keep answering.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from api import main as api
from config.settings import Settings
from core.classify.provider import StubLlmProvider
from tests.api.test_api_suggest import answer, make_service
from tests.classify.conftest import build_codebooks
from tests.codebooks.conftest import make_codebook_dir
from tests.test_probe import healthy

TOKEN = "vercel_blob_rw_Store42_s3cr3t"


def offline_settings(**overrides: object) -> Settings:
    """Settings that never touch the network: no registers, no web, no model, no cache."""
    values: dict[str, object] = {
        "_env_file": None,
        "gleif_enabled": False,
        "openfigi_enabled": False,
        "web_enabled": False,
        "llm_api_key": None,
        "llm_cache_path": None,
        "llm_usage_path": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def fresh_state() -> Iterator[None]:
    api._state.clear()
    yield
    api._state.clear()


def wired(settings: Settings) -> TestClient:
    """A client whose app has settings but no service yet: the first lookup loads it."""
    api._state["settings"] = settings
    return TestClient(api.app)


def describe(client: TestClient) -> httpx.Response:
    return client.post("/api/suggest", json={"description": "Pěstování obilí a zeleniny."})


class TestLazyCodebooks:
    def test_health_never_loads_the_codebooks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def must_not_run(*args: object, **kwargs: object) -> None:
            raise AssertionError("/health loaded the codebooks")

        monkeypatch.setattr(api, "load_and_check", must_not_run)
        response = wired(offline_settings(codebook_dir=tmp_path)).get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "starting"
        assert body["codebooks"]["state"] == "not_loaded"
        assert body["codebook_version"] is None

    def test_the_first_lookup_loads_them_and_health_then_reports_the_version(
        self, tmp_path: Path
    ) -> None:
        client = wired(offline_settings(codebook_dir=make_codebook_dir(tmp_path)))
        assert describe(client).status_code == 200
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["codebooks"]["state"] == "loaded"
        assert body["codebook_version"].startswith("cb-")
        assert isinstance(body["codebooks"]["loaded_ms"], int)

    def test_concurrent_first_requests_load_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fluid compute runs several requests in one instance; they must share one load."""
        real = api.load_and_check
        calls: list[int] = []

        def slow_load(*args: object, **kwargs: object):
            calls.append(1)
            time.sleep(0.2)
            return real(*args, **kwargs)

        monkeypatch.setattr(api, "load_and_check", slow_load)
        api._state["settings"] = offline_settings(codebook_dir=make_codebook_dir(tmp_path))
        services: list[object] = []
        threads = [
            threading.Thread(target=lambda: services.append(api.get_service())) for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(calls) == 1
        assert len(services) == 4 and len({id(service) for service in services}) == 1


class TestUnavailableCodebooks:
    def test_the_api_answers_503_with_the_reason(self, tmp_path: Path) -> None:
        response = describe(wired(offline_settings(codebook_dir=tmp_path)))
        assert response.status_code == 503
        body = response.json()
        assert body["detail"] == "codebooks unavailable"
        assert "CTS_BA0036_NEW.xlsx" in body["reason"]

    def test_health_turns_503_and_says_why(self, tmp_path: Path) -> None:
        client = wired(offline_settings(codebook_dir=tmp_path))
        describe(client)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "error"
        assert "CTS_BA0036_NEW.xlsx" in response.json()["codebooks"]["error"]

    def test_a_failure_is_not_retried_on_every_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = api.load_and_check
        calls: list[int] = []

        def counting(*args: object, **kwargs: object):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(api, "load_and_check", counting)
        client = wired(offline_settings(codebook_dir=tmp_path))
        for _ in range(3):
            assert describe(client).status_code == 503
        assert len(calls) == 1

    def test_after_the_pause_it_tries_again_and_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "RETRY_AFTER_SECONDS", 0.0)
        settings = offline_settings(codebook_dir=tmp_path / "codebooks")
        client = wired(settings)
        assert describe(client).status_code == 503
        make_codebook_dir(tmp_path / "codebooks")  # the files arrive
        assert describe(client).status_code == 200
        assert client.get("/health").json()["codebooks"]["error"] is None

    def test_an_inconsistent_set_never_serves_a_suggestion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Report:
            ok = False

            @staticmethod
            def summary() -> str:
                return "1 error(s): E_CTS_ID_UNKNOWN"

        monkeypatch.setattr(api, "load_and_check", lambda *a, **k: (build_codebooks(), Report()))
        response = describe(wired(offline_settings()))
        assert response.status_code == 503
        assert "inconsistent" in response.json()["reason"]
        assert "E_CTS_ID_UNKNOWN" in response.json()["reason"]

    def test_the_page_shows_the_reason_and_keeps_what_was_typed(self, tmp_path: Path) -> None:
        client = wired(offline_settings(codebook_dir=tmp_path))
        response = client.post("/suggest", data={"description": "Výroba osobních automobilů"})
        assert response.status_code == 503
        assert "Číselníky nejsou k dispozici" in response.text
        assert "Výroba osobních automobilů" in response.text

    def test_the_empty_form_still_opens(self, tmp_path: Path) -> None:
        client = wired(offline_settings(codebook_dir=tmp_path))
        describe(client)
        assert client.get("/").status_code == 200

    def test_the_download_answers_503_in_plain_text(self, tmp_path: Path) -> None:
        response = wired(offline_settings(codebook_dir=tmp_path)).get(
            "/suggest.xlsx", params={"name": "Nordkap"}
        )
        assert response.status_code == 503
        assert response.text.startswith("Číselníky nejsou k dispozici")


class TestStartup:
    def test_a_failed_load_at_startup_does_not_stop_the_app(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Vercel an exception in the lifespan would take the instance down."""
        monkeypatch.setattr(api, "get_settings", lambda: offline_settings(codebook_dir=tmp_path))
        with TestClient(api.app) as client:
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["codebooks"]["state"] == "error"

    def test_a_good_set_is_loaded_at_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = offline_settings(codebook_dir=make_codebook_dir(tmp_path))
        monkeypatch.setattr(api, "get_settings", lambda: settings)
        with TestClient(api.app) as client:
            body = client.get("/health").json()
        assert body["codebooks"]["state"] == "loaded"


class TestBlobSource:
    def test_the_codebooks_come_from_the_private_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = make_codebook_dir(tmp_path / "master")
        seen: list[httpx.Request] = []

        def store(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=(source / name).read_bytes())

        real_client = httpx.Client
        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(store))
        )
        settings = offline_settings(
            codebook_source="blob",
            blob_read_write_token=TOKEN,
            codebook_download_dir=tmp_path / "downloaded",
        )
        client = wired(settings)
        assert describe(client).status_code == 200
        assert {request.url.host for request in seen} == {"store42.private.blob.vercel-storage.com"}
        assert client.get("/health").json()["codebooks"]["source"] == "blob"

    def test_a_missing_token_is_the_reason_given(self, tmp_path: Path) -> None:
        settings = offline_settings(codebook_source="blob", codebook_download_dir=tmp_path)
        response = describe(wired(settings))
        assert response.status_code == 503
        assert "BLOB_READ_WRITE_TOKEN is not set" in response.json()["reason"]


class TestProbeRoute:
    @pytest.fixture
    def registers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_client = httpx.Client
        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(healthy))
        )

    def test_the_page_renders(self, registers: None) -> None:
        response = wired(offline_settings()).get("/probe")
        assert response.status_code == 200
        assert "Diagnostika nasazení" in response.text
        assert "GLEIF LEI API" in response.text

    def test_json_carries_the_codebook_state_without_loading_it(self, registers: None) -> None:
        body = wired(offline_settings()).get("/probe", params={"format": "json"}).json()
        assert body["ok"] is True
        assert body["codebooks"]["state"] == "not_loaded"
        assert [host["key"] for host in body["hosts"]] == ["gleif", "openfigi"]

    def test_set_all_adds_the_e5_candidates(self, registers: None) -> None:
        body = (
            wired(offline_settings()).get("/probe", params={"format": "json", "set": "all"}).json()
        )
        assert body["set"] == "all"
        assert len(body["hosts"]) == 6

    def test_it_can_be_switched_off(self) -> None:
        response = wired(offline_settings(probe_enabled=False)).get("/probe")
        assert response.status_code == 404


class TestDownloadName:
    def test_a_czech_issuer_name_does_not_break_the_download(self) -> None:
        """HTTP headers are latin-1; a raw Č in Content-Disposition used to give a 500."""
        api._state["settings"] = offline_settings()
        api._state["service"] = make_service(
            build_codebooks(),
            provider=StubLlmProvider({"NACE": answer("64"), "ESA": answer("2002703")}),
        )
        response = TestClient(api.app).get("/suggest.xlsx", params={"name": "Česká spořitelna"})
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert 'filename="_esk__spo_itelna.xlsx"' in disposition
        assert f"filename*=UTF-8''{quote('Česká_spořitelna')}.xlsx" in disposition
