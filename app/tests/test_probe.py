"""The /probe diagnostics: fixed checks, a status per failure mode, nothing secret shown.

Every register is faked with ``httpx.MockTransport``; the answers are trimmed copies of the
live responses captured for the source tests (GLEIF, OpenFIGI) and on 17 Sept 2026 in the
design source (FIRDS, Wikidata, Wikipedia).
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from config.settings import Settings
from core.probe import ISIN, LEI, STATUS_CS, checks, configuration, report, run_check

GLEIF_OK = {"data": {"attributes": {"entity": {"legalName": {"name": "DEUTSCHE BANK AG"}}}}}
FIGI_OK = [{"data": [{"figi": "BBG000BBNVX1", "name": "DEUTSCHE BANK AG-REGISTERED"}]}]
FIRDS_OK = {"response": {"numFound": 1, "docs": [{"isin": ISIN}]}}
WIKIDATA_SEARCH_OK = {"query": {"search": [{"title": "Q66048"}]}}
WIKIDATA_ENTITY_OK = {"entities": {"Q66048": {"labels": {"en": {"value": "Deutsche Bank"}}}}}
WIKIPEDIA_OK = {"extract": "Deutsche Bank AG is a German multinational investment bank."}


def settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def healthy(request: httpx.Request) -> httpx.Response:
    """Every register answering the way it did when the probes were written."""
    host = request.url.host
    if host == "api.gleif.org":
        return httpx.Response(200, json=GLEIF_OK)
    if host == "api.openfigi.com":
        return httpx.Response(200, json=FIGI_OK)
    if host == "registers.esma.europa.eu":
        return httpx.Response(200, json=FIRDS_OK)
    if host == "www.wikidata.org" and request.url.params.get("action") == "query":
        return httpx.Response(200, json=WIKIDATA_SEARCH_OK)
    if host == "www.wikidata.org" and request.url.params.get("action") == "wbgetentities":
        return httpx.Response(200, json=WIKIDATA_ENTITY_OK)
    if host.endswith("wikipedia.org"):
        return httpx.Response(200, json=WIKIPEDIA_OK)
    return httpx.Response(404)


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestTheFixedChecks:
    def test_by_default_only_the_sources_in_use_are_checked(self) -> None:
        assert [check.key for check in checks(settings())] == [
            "gleif",
            "openfigi",
            "wikidata",
            "wikipedia_cs",
            "wikipedia_en",
        ]
        assert all(check.in_use for check in checks(settings()))

    def test_the_wikipedia_checks_follow_the_configured_languages(self) -> None:
        keys = [check.key for check in checks(settings(wikipedia_languages="en"))]
        assert keys == ["gleif", "openfigi", "wikidata", "wikipedia_en"]

    def test_the_extended_set_adds_the_e5_candidates(self) -> None:
        keys = [check.key for check in checks(settings(), extended=True)]
        assert keys == ["gleif", "openfigi", "wikidata", "wikipedia_cs", "wikipedia_en", "firds"]

    @pytest.mark.parametrize("switch", ["wikimedia_enabled", "web_enabled"])
    def test_wikimedia_switched_off_is_only_a_candidate(self, switch: str) -> None:
        off = settings(**{switch: False})
        assert [check.key for check in checks(off)] == ["gleif", "openfigi"]
        extended = checks(off, extended=True)
        assert [check.key for check in extended][2:] == [
            "firds",
            "wikidata",
            "wikipedia_cs",
            "wikipedia_en",
        ]
        assert not any(check.in_use for check in extended[2:])

    def test_the_requests_are_fixed_and_harmless(self) -> None:
        gleif, figi, *_ = checks(settings())
        assert gleif.method == "GET" and gleif.url.endswith(f"/lei-records/{LEI}")
        assert figi.method == "POST" and figi.json_body == [{"idType": "ID_ISIN", "idValue": ISIN}]

    def test_the_configured_base_urls_are_used(self) -> None:
        gleif, figi, *_ = checks(
            settings(
                gleif_base_url="https://gleif.test/api/v1", openfigi_base_url="https://figi.test/v3"
            )
        )
        assert gleif.host == "gleif.test" and gleif.url.startswith("https://gleif.test/api/v1/")
        assert figi.host == "figi.test"

    def test_the_openfigi_key_is_sent_only_when_configured(self) -> None:
        assert "X-OPENFIGI-APIKEY" not in checks(settings())[1].headers
        keyed = checks(settings(openfigi_api_key="k3y"))[1]
        assert keyed.headers["X-OPENFIGI-APIKEY"] == "k3y"

    def test_every_request_identifies_the_tool(self) -> None:
        agent = settings().web_user_agent
        assert all(
            check.headers["User-Agent"] == agent for check in checks(settings(), extended=True)
        )


class TestStatuses:
    def run(self, handler) -> str:
        return run_check(client(handler), checks(settings())[0]).status

    def test_a_good_answer_is_ok_and_says_what_it_confirmed(self) -> None:
        result = run_check(client(healthy), checks(settings())[0])
        assert result.status == "ok"
        assert result.detail == "legalName: DEUTSCHE BANK AG"
        assert result.http_status == 200

    def test_a_2xx_with_the_wrong_body_is_not_ok(self) -> None:
        """A captive portal or a block page answers 200 too."""
        assert self.run(lambda request: httpx.Response(200, text="<html>blocked</html>")) == (
            "unexpected_body"
        )

    def test_an_http_error_is_its_own_status(self) -> None:
        assert self.run(lambda request: httpx.Response(503, text="Service Unavailable")) == (
            "http_error"
        )

    def test_a_proxy_asking_for_credentials_is_recognised(self) -> None:
        assert self.run(lambda request: httpx.Response(407)) == "proxy_auth"

    def test_a_timeout_is_a_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        assert self.run(handler) == "timeout"

    def test_a_certificate_failure_is_tls(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            try:
                raise ssl.SSLCertVerificationError("certificate verify failed")
            except ssl.SSLError as exc:
                raise httpx.ConnectError("handshake failed", request=request) from exc

        assert self.run(handler) == "tls"

    def test_a_name_that_does_not_resolve_is_dns(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)

        assert self.run(handler) == "dns"

    def test_a_refused_connection_is_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

        assert self.run(handler) == "blocked"

    def test_an_unexpected_exception_never_escapes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("boom")

        assert self.run(handler) == "error"

    def test_every_status_has_a_czech_explanation(self) -> None:
        from typing import get_args

        from core.probe import Status

        assert set(STATUS_CS) == set(get_args(Status))


class TestReport:
    def test_all_registers_answering_is_ok(self) -> None:
        result = report(settings(), client=client(healthy))
        assert result["ok"] is True
        assert [host["status"] for host in result["hosts"]] == ["ok"] * 5

    def test_the_wikidata_check_follows_the_search_to_the_item(self) -> None:
        result = report(settings(), extended=True, client=client(healthy))
        wikidata = next(host for host in result["hosts"] if host["key"] == "wikidata")
        assert wikidata["status"] == "ok"
        assert [step["label"] for step in wikidata["steps"]] == [
            "Wikidata – hledání podle LEI",
            "Wikidata – položka",
        ]
        assert "Q66048: Deutsche Bank" in wikidata["detail"]

    def test_a_candidate_that_fails_does_not_fail_the_verdict(self) -> None:
        """Only the registers in use decide ``ok``; E5 candidates are informational."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "registers.esma.europa.eu":
                return httpx.Response(500)
            return healthy(request)

        assert report(settings(), extended=True, client=client(handler))["ok"] is True

    def test_a_register_in_use_that_fails_fails_the_verdict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.openfigi.com":
                return httpx.Response(429, text="Too Many Requests")
            return healthy(request)

        assert report(settings(), client=client(handler))["ok"] is False

    def test_the_codebook_state_is_passed_through(self) -> None:
        state = {"state": "loaded", "version": "cb-0123456789abcdef"}
        assert report(settings(), client=client(healthy), codebooks=state)["codebooks"] == state

    def test_the_runtime_names_the_interpreter_and_the_libraries(self) -> None:
        runtime = report(settings(), client=client(healthy))["runtime"]
        assert runtime["python"]
        assert set(runtime["libraries"]) >= {"fastapi", "httpx", "openpyxl"}
        assert str(runtime["settings_module"]).endswith("settings.py")


class TestNothingSecretIsShown:
    def rows(self, config: Settings) -> dict[str, dict[str, object]]:
        return {row["name"]: row for row in configuration(config)}

    def test_tokens_and_keys_are_reported_as_present_only(self) -> None:
        rows = self.rows(
            settings(
                blob_read_write_token="vercel_blob_rw_Store1_secret",
                openfigi_api_key="figi-secret",
                llm_api_key="sk-secret",
                web_search_url="https://search.test/?key=secret",
            )
        )
        for name in ("BLOB_READ_WRITE_TOKEN", "OPENFIGI_API_KEY", "LLM_API_KEY", "WEB_SEARCH_URL"):
            assert rows[name] == {"name": name, "present": True, "value": "***"}
        assert "secret" not in repr(rows)

    def test_a_proxy_is_shown_without_its_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://user:pa55@proxy.bank.test:8080")
        row = self.rows(settings())["HTTPS_PROXY"]
        assert row["value"] == "http://***@proxy.bank.test:8080"

    def test_plain_settings_are_shown_as_they_are(self) -> None:
        rows = self.rows(settings(codebook_source="blob", web_user_header=""))
        assert rows["CODEBOOK_SOURCE"]["value"] == "blob"
        assert rows["WEB_USER_HEADER"] == {
            "name": "WEB_USER_HEADER",
            "present": False,
            "value": None,
        }
