"""RES through ARES: the record of a Czech IČO, the fact line, and every way ARES can fail.

Payloads are trimmed live answers of 2 Oct 2026 (see ``conftest.py``); nothing here touches
the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from config.settings import Settings
from core.sources.ares import PATH_RES, AresSource
from core.sources.base import SourceQueryError, SourceResponseError, SourceUnavailableError
from tests.sources.conftest import (
    RES_CEZ,
    RES_KB,
    RES_MF,
    ares_client,
    make_client,
    payload,
    res_payload,
)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"ares_min_interval_seconds": 0.0, "ares_max_attempts": 1}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def source(answers: dict, **overrides: object) -> AresSource:
    return AresSource(settings(**overrides), client=ares_client(answers), sleep=lambda _: None)


class TestRecord:
    def test_maps_the_live_payload(self) -> None:
        record = source({"45274649": RES_CEZ}).fetch_res("45274649")
        assert record is not None
        assert record.ico == "45274649"
        assert record.name == "ČEZ, a. s."
        assert record.legal_form == "121"
        assert record.sector == "11001"
        assert record.nace == "35110" and record.nace_2008 == "35110"
        assert record.updated_on == date(2026, 9, 4)
        assert record.as_of == "2026-09-04"

    def test_provenance_is_res_with_the_register_update_as_snapshot(self) -> None:
        record = source({"45274649": RES_CEZ}).fetch_res("45274649")
        assert record is not None
        assert record.provenance.source == "RES"
        assert record.provenance.detail == "ares:res"
        assert record.provenance.snapshot_at == datetime(2026, 9, 4, tzinfo=UTC)

    def test_the_citable_page_is_the_ares_web_application(self) -> None:
        record = source({"45274649": RES_CEZ}).fetch_res("45274649")
        assert record is not None
        assert record.url == "https://ares.gov.cz/ekonomicke-subjekty?ico=45274649"

    def test_codes_are_kept_as_res_spells_them(self) -> None:
        """Full NACE codes stay in the data; only the CTS-ID mapping truncates (hard rule)."""
        kooperativa = res_payload(
            "47116617",
            "Kooperativa pojišťovna, a.s., Vienna Insurance Group",
            sector="12803",
            nace="651",
            nace_2008="651",
        )
        record = source({"47116617": kooperativa}).fetch_res("47116617")
        assert record is not None and record.nace == "651"

    def test_the_primary_record_wins_over_the_first(self) -> None:
        document = payload(RES_KB)
        historical = {
            **document["zaznamy"][0],
            "obchodniJmeno": "Old name",
            "primarniZaznam": False,
        }
        document["zaznamy"] = [historical, document["zaznamy"][0]]
        record = source({"45317054": document}).fetch_res("45317054")
        assert record is not None and record.name == "Komerční banka, a.s."

    def test_missing_codes_are_none(self) -> None:
        bare = res_payload("12345678", "Nový subjekt, s.r.o.", legal_form="112")
        record = source({"12345678": bare}).fetch_res("12345678")
        assert record is not None
        assert record.sector is None and record.nace is None and record.nace_2008 is None

    @pytest.mark.parametrize("field", ["institucionalniSektor2010", "czNacePrevazujici"])
    def test_a_malformed_code_is_dropped_not_used(self, field: str) -> None:
        document = payload(RES_CEZ)
        if field == "institucionalniSektor2010":
            document["zaznamy"][0]["statistickeUdaje"][field] = "S.11001"
        else:
            document["zaznamy"][0][field] = "35.11"
        record = source({"45274649": document}).fetch_res("45274649")
        assert record is not None
        assert (record.sector if field == "institucionalniSektor2010" else record.nace) is None


class TestFactLine:
    def test_the_line_carries_the_codes_the_names_and_the_date(self) -> None:
        record = source({"45274649": RES_CEZ}).fetch_res("45274649")
        assert record is not None
        (line,) = record.facts()
        assert line.startswith("RES (ČSÚ, přes ARES, stav k 2026-09-04): ČEZ, a. s., IČO 45274649")
        assert "právní forma 121 akciová společnost" in line
        assert "institucionální sektor 11001 Veřejné podniky nefinanční" in line
        assert "převažující činnost CZ-NACE 2025 35110" in line
        assert line.endswith("[RES].")

    def test_a_differing_2008_code_is_shown_beside_the_2025_one(self) -> None:
        skoda = res_payload(
            "00177041", "Škoda Auto a.s.", sector="11003", nace="29200", nace_2008="29100"
        )
        record = source({"00177041": skoda}).fetch_res("00177041")
        assert record is not None
        assert "CZ-NACE 2025 29200 (CZ-NACE 2008 29100)" in record.facts()[0]

    def test_a_2008_code_alone_is_said_so(self) -> None:
        old = res_payload("12345678", "Starý subjekt, a.s.", sector="11002", nace_2008="45200")
        record = source({"12345678": old}).fetch_res("12345678")
        assert record is not None
        assert "CZ-NACE 2008 45200, CZ-NACE 2025 neuvedena" in record.facts()[0]

    def test_an_unknown_legal_form_is_shown_bare(self) -> None:
        odd = res_payload("12345678", "Subjekt", legal_form="999", sector="11002", nace="70100")
        record = source({"12345678": odd}).fetch_res("12345678")
        assert record is not None
        assert "právní forma 999;" in record.facts()[0]


class TestRequest:
    def test_uses_the_verified_endpoint(self) -> None:
        calls: list[str] = []
        client = ares_client({"00006947": RES_MF}, calls=calls)
        AresSource(settings(), client=client).fetch_res("00006947")
        assert calls == ["https://ares.gov.cz" + PATH_RES.format(ico="00006947")]

    @pytest.mark.parametrize("ico", ["4527464", "452746490", "4527464a", ""])
    def test_a_malformed_ico_is_never_sent(self, ico: str) -> None:
        calls: list[str] = []
        client = ares_client({}, calls=calls)
        with pytest.raises(SourceQueryError):
            AresSource(settings(), client=client).fetch_res(ico)
        assert calls == []


class TestNotFoundIsNotAnOutage:
    def test_an_ico_res_does_not_hold_is_none(self) -> None:
        """404 NENALEZENO: RES has no such subject (new ones reach ARES weeks late)."""
        assert source({}).fetch_res("12345679") is None

    def test_a_server_error_is_unavailable(self) -> None:
        with pytest.raises(SourceUnavailableError):
            source({"45274649": 503}).fetch_res("45274649")

    def test_a_timeout_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = make_client(handler, base_url="https://ares.gov.cz")
        with pytest.raises(SourceUnavailableError, match="timed out"):
            AresSource(settings(), client=client).fetch_res("45274649")

    def test_a_refused_request_is_a_response_error(self) -> None:
        with pytest.raises(SourceResponseError):
            source({"45274649": 400}).fetch_res("45274649")

    def test_a_non_json_body_is_a_response_error(self) -> None:
        client = make_client(
            lambda request: httpx.Response(200, text="<html>údržba"),
            base_url="https://ares.gov.cz",
        )
        with pytest.raises(SourceResponseError):
            AresSource(settings(), client=client).fetch_res("45274649")

    def test_an_answer_without_a_record_is_a_response_error(self) -> None:
        with pytest.raises(SourceResponseError):
            source({"45274649": {"icoId": "45274649", "zaznamy": []}}).fetch_res("45274649")


class TestRetries:
    def test_rate_limiting_is_retried_then_succeeds(self) -> None:
        answers = iter([httpx.Response(429), httpx.Response(200, json=payload(RES_CEZ))])
        client = make_client(lambda request: next(answers), base_url="https://ares.gov.cz")
        slept: list[float] = []
        ares = AresSource(settings(ares_max_attempts=2), client=client, sleep=slept.append)
        record = ares.fetch_res("45274649")
        assert record is not None and record.sector == "11001"
        assert slept, "a retry waits first"

    def test_a_client_error_is_not_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, json={"kod": "CHYBA_VSTUPU"})

        client = make_client(handler, base_url="https://ares.gov.cz")
        with pytest.raises(SourceResponseError):
            AresSource(
                settings(ares_max_attempts=3), client=client, sleep=lambda _: None
            ).fetch_res("45274649")
        assert attempts["n"] == 1

    def test_retries_are_exhausted_then_reported(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(500, text="boom")

        client = make_client(handler, base_url="https://ares.gov.cz")
        with pytest.raises(SourceUnavailableError):
            AresSource(
                settings(ares_max_attempts=2), client=client, sleep=lambda _: None
            ).fetch_res("45274649")
        assert attempts["n"] == 2


def test_requests_are_spaced_out() -> None:
    slept: list[float] = []
    clock = iter([0.0, 0.10, 0.25])
    ares = AresSource(
        settings(ares_min_interval_seconds=0.25),
        client=ares_client({"45274649": RES_CEZ, "45317054": RES_KB}),
        sleep=slept.append,
        monotonic=lambda: next(clock),
    )
    ares.fetch_res("45274649")
    ares.fetch_res("45317054")
    assert slept == [pytest.approx(0.15)]


def test_the_defaults_fit_the_lookup_deadline() -> None:
    """No Vercel variable is set for ARES, so the defaults must be safe there: about 11 s."""
    defaults = Settings(_env_file=None)
    worst = defaults.ares_max_attempts * defaults.ares_timeout_seconds + 0.5
    assert worst <= 11.0
    assert defaults.ares_base_url == "https://ares.gov.cz"


def test_close_leaves_an_injected_client_alone() -> None:
    client = ares_client({})
    AresSource(settings(), client=client).close()
    assert not client.is_closed
