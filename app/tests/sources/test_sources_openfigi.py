"""OpenFIGI: ISIN -> instrument, the key header, the rate limit, and the ways it can fail."""

from __future__ import annotations

import json

import httpx
import pytest

from config.settings import Settings
from core.classify.hints import hinted_esa_families
from core.sources.base import SourceResponseError, SourceUnavailableError
from core.sources.openfigi import API_KEY_HEADER, FigiInstrument, OpenFigiSource
from tests.sources.conftest import (
    FIGI_DEUTSCHE_BANK,
    FIGI_FUND,
    FIGI_INVALID,
    FIGI_NOT_FOUND,
    figi_client,
    make_client,
)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "openfigi_min_interval_seconds": 0.0,
        "openfigi_max_attempts": 1,
        "openfigi_api_key": None,
    }
    base.update(overrides)
    return Settings(**base)


def source(body: object, **kwargs: object) -> OpenFigiSource:
    return OpenFigiSource(settings(), client=figi_client(body, **kwargs), sleep=lambda _: None)  # type: ignore[arg-type]


class TestMapping:
    def test_maps_the_first_listing(self) -> None:
        instrument = source(FIGI_DEUTSCHE_BANK).map_isin("DE0005140008")
        assert instrument is not None
        assert instrument.name == "DEUTSCHE BANK AG-REGISTERED"
        assert instrument.figi == "BBG000BBZTH2"
        assert instrument.security_type == "Common Stock"
        assert instrument.market_sector == "Equity"
        assert instrument.exchange_code == "GR"
        assert instrument.listings == 2
        assert instrument.provenance.source == "OPENFIGI"

    def test_a_fund_known_only_to_openfigi(self) -> None:
        """IE00B4L5Y983 is missing from GLEIF's ISIN mapping; OpenFIGI still names it."""
        instrument = source(FIGI_FUND).map_isin("IE00B4L5Y983")
        assert instrument is not None
        assert instrument.name == "ISHARES CORE MSCI WORLD"
        assert instrument.security_type2 == "Mutual Fund"
        assert "ETP / Mutual Fund" in instrument.fact_sheet()

    def test_an_unknown_isin_is_none(self) -> None:
        assert source(FIGI_NOT_FOUND).map_isin("FR0129895324") is None

    def test_a_rejected_identifier_is_none_not_an_error(self) -> None:
        """'Invalid idValue format.' is about our input, not an outage - a plain miss."""
        assert source(FIGI_INVALID).map_isin("XS0000000001") is None

    def test_the_request_shape_is_the_verified_one(self) -> None:
        calls: list[httpx.Request] = []
        source(FIGI_DEUTSCHE_BANK, calls=calls).map_isin("DE0005140008")
        request = calls[0]
        assert request.method == "POST"
        assert request.url.path.endswith("/mapping")
        assert json.loads(request.content) == [{"idType": "ID_ISIN", "idValue": "DE0005140008"}]
        assert API_KEY_HEADER not in request.headers

    def test_the_key_is_sent_only_when_configured(self) -> None:
        calls: list[httpx.Request] = []
        client = figi_client(FIGI_DEUTSCHE_BANK, calls=calls)
        OpenFigiSource(settings(openfigi_api_key="secret-key"), client=client).map_isin(
            "DE0005140008"
        )
        assert calls[0].headers[API_KEY_HEADER] == "secret-key"

    def test_a_blank_key_in_the_environment_means_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENFIGI_API_KEY", "   ")
        assert Settings(_env_file=None).openfigi_api_key is None


class TestFacts:
    def test_the_sheet_names_the_register_the_type_and_the_sector(self) -> None:
        instrument = source(FIGI_DEUTSCHE_BANK).map_isin("DE0005140008")
        assert instrument is not None
        sheet = instrument.fact_sheet()
        assert sheet.startswith("OpenFIGI (ISIN DE0005140008): DEUTSCHE BANK AG-REGISTERED.")
        assert "Typ nástroje: Common Stock." in sheet
        assert "[Equity]" in sheet

    def test_a_government_sector_is_glossed_so_the_hints_fire(self) -> None:
        instrument = FigiInstrument(
            isin="DE000A351PG2",
            figi=None,
            name="LAND BERLIN",
            ticker=None,
            security_type="DOMESTIC",
            security_type2="Bond",
            market_sector="Govt",
            exchange_code=None,
            listings=1,
            provenance=source(FIGI_FUND).map_isin("IE00B4L5Y983").provenance,  # type: ignore[union-attr]
        )
        families = hinted_esa_families(instrument.fact_sheet())
        assert "ustredni vladni instituce" in families or "narodni vladni instituce" in families

    def test_the_search_page_is_the_citable_url(self) -> None:
        instrument = source(FIGI_DEUTSCHE_BANK).map_isin("DE0005140008")
        assert instrument is not None
        assert instrument.url.endswith("q=DE0005140008")


class TestFailures:
    def test_server_error_is_unavailable(self) -> None:
        with pytest.raises(SourceUnavailableError):
            source(FIGI_DEUTSCHE_BANK, status=503).map_isin("DE0005140008")

    def test_rate_limiting_is_retried_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, text="Too Many Requests")
            return httpx.Response(200, json=FIGI_DEUTSCHE_BANK)

        client = make_client(handler, base_url="https://api.openfigi.com/v3")
        instrument = OpenFigiSource(
            settings(openfigi_max_attempts=3), client=client, sleep=lambda _: None
        ).map_isin("DE0005140008")
        assert instrument is not None and attempts["n"] == 2

    def test_client_error_is_a_response_error_and_not_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(401, json={"message": "invalid key"})

        client = make_client(handler, base_url="https://api.openfigi.com/v3")
        with pytest.raises(SourceResponseError):
            OpenFigiSource(
                settings(openfigi_max_attempts=3), client=client, sleep=lambda _: None
            ).map_isin("DE0005140008")
        assert attempts["n"] == 1

    def test_an_unexpected_body_shape_is_a_response_error(self) -> None:
        with pytest.raises(SourceResponseError):
            source({"data": []}).map_isin("DE0005140008")

    def test_timeout_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no route", request=request)

        client = make_client(handler, base_url="https://api.openfigi.com/v3")
        with pytest.raises(SourceUnavailableError):
            OpenFigiSource(settings(), client=client, sleep=lambda _: None).map_isin("DE0005140008")


class TestThrottle:
    def test_requests_are_spaced_to_the_keyless_limit(self) -> None:
        """25 a minute without a key (ratelimit-policy 25;w=60, seen live): 2.5 s apart."""
        slept: list[float] = []
        clock = iter([0.0, 0.5, 2.5])
        figi = OpenFigiSource(
            settings(openfigi_min_interval_seconds=2.5),
            client=figi_client(FIGI_DEUTSCHE_BANK),
            sleep=slept.append,
            monotonic=lambda: next(clock),
        )
        figi.map_isin("DE0005140008")
        figi.map_isin("DE0005140008")
        assert slept == [pytest.approx(2.0)]


def test_close_leaves_an_injected_client_alone() -> None:
    client = figi_client(FIGI_DEUTSCHE_BANK)
    figi = OpenFigiSource(settings(), client=client)
    figi.close()
    assert not client.is_closed
