"""ARES adapter: payload mapping, missing halves, throttling, retries and failure modes.

Every test drives an ``httpx.MockTransport``; nothing here touches the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from config.settings import Settings
from core.sources.ares import PATH_RES, PATH_SEARCH, PATH_VR, AresSource
from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    SourceResponseError,
    SourceUnavailableError,
)
from tests.sources.conftest import (
    RES_PAYLOAD,
    RES_PAYLOAD_MISMATCH,
    VR_PAYLOAD,
    make_client,
    payload,
    routed_client,
)


class TestResMapping:
    def test_maps_the_live_payload(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD)))
        record = source.fetch_by_ico("49240901")

        assert record is not None
        res = record.res
        assert res.name == "Raiffeisenbank a.s."
        assert res.founded_on == date(1993, 6, 25)
        assert res.legal_form == "121"
        assert res.provenance.source == "ARES_LIVE"
        assert res.provenance.detail == "ares:res"

    def test_esa_sector_is_canonicalized(self, settings: Settings) -> None:
        """ARES stores bare digits; a row must carry the S.xxxxx display form."""
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD)))
        assert source.fetch_by_ico("49240901").res.esa_sector == "S.12203"

    def test_unparseable_esa_sector_is_dropped(self, settings: Settings) -> None:
        document = payload(RES_PAYLOAD)
        document["zaznamy"][0]["statistickeUdaje"]["institucionalniSektor2010"] = "nonsense"
        source = AresSource(settings, client=routed_client(res=document))
        assert source.fetch_by_ico("49240901").res.esa_sector is None

    def test_snapshot_comes_from_datum_aktualizace(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD)))
        record = source.fetch_by_ico("49240901")
        assert record.res.provenance.snapshot_at == datetime(2023, 6, 29, tzinfo=UTC)
        assert record.timestamp == datetime(2023, 6, 29, tzinfo=UTC)

    def test_both_revisions_are_read_from_their_own_fields(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD_MISMATCH)))
        res = source.fetch_by_ico("00177041").res

        assert res.main(NACE_REV_2).code == "29100"
        assert res.main(NACE_REV_21).code == "29200"
        assert set(res.codes(NACE_REV_2)) == {"29100", "25500", "25610", "45200"}
        assert set(res.codes(NACE_REV_21)) == {"29200", "25400", "25510"}
        assert res.nace_mismatch is True

    def test_codes_are_not_truncated_to_a_division(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD)))
        assert source.fetch_by_ico("49240901").res.codes(NACE_REV_2) == ("64190",)

    def test_prevailing_code_missing_from_the_list_is_still_included(
        self, settings: Settings
    ) -> None:
        document = payload(RES_PAYLOAD)
        document["zaznamy"][0]["czNace2008"] = []
        source = AresSource(settings, client=routed_client(res=document))
        res = source.fetch_by_ico("49240901").res
        assert res.main(NACE_REV_2).code == "64190"

    def test_primary_record_wins_over_the_first(self, settings: Settings) -> None:
        document = payload(RES_PAYLOAD)
        historical = payload(RES_PAYLOAD)["zaznamy"][0]
        historical["obchodniJmeno"] = "Agrobanka Praha, a.s."
        historical["primarniZaznam"] = False
        document["zaznamy"] = [historical, document["zaznamy"][0]]
        source = AresSource(settings, client=routed_client(res=document))
        assert source.fetch_by_ico("49240901").res.name == "Raiffeisenbank a.s."


class TestVrMapping:
    def test_maps_the_live_payload(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(vr=payload(VR_PAYLOAD)))
        or_record = source.fetch_by_ico("49240901").or_record

        assert or_record.obchodni_firma == "Raiffeisenbank a.s."
        assert or_record.datum_zapisu == date(1993, 6, 25)
        assert or_record.spisova_znacka == "B 2051/MSPH"
        assert or_record.provenance.detail == "ares:vr"

    def test_deleted_entries_are_dropped(self, settings: Settings) -> None:
        """An entry carrying datumVymazu is historical: it must not reach a review row."""
        source = AresSource(settings, client=routed_client(vr=payload(VR_PAYLOAD)))
        or_record = source.fetch_by_ico("49240901").or_record

        assert or_record.predmet_podnikani == ("bankovní obchody",)
        assert "směnárenská činnost" not in or_record.predmet_podnikani
        assert or_record.obchodni_firma != "Agrobanka Praha, a.s."

    def test_predmet_cinnosti_is_kept_apart_from_podnikani(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(vr=payload(VR_PAYLOAD)))
        or_record = source.fetch_by_ico("49240901").or_record
        assert or_record.predmet_cinnosti == ("pronájem nemovitostí",)

    def test_snapshot_falls_back_to_the_envelope(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(vr=payload(VR_PAYLOAD)))
        or_record = source.fetch_by_ico("49240901").or_record
        assert or_record.provenance.snapshot_at == datetime(2026, 9, 5, tzinfo=UTC)


class TestMissingHalves:
    def test_both_registers_missing_returns_none(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client())
        assert source.fetch_by_ico("49240901") is None

    def test_res_only_subject_is_noted(self, settings: Settings) -> None:
        """A sole trader is in RES but not in the veřejný rejstřík; that is data, not an error."""
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD)))
        record = source.fetch_by_ico("49240901")
        assert record.or_record is None
        assert any("no OR record" in note for note in record.notes)

    def test_vr_only_subject_is_noted(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(vr=payload(VR_PAYLOAD)))
        record = source.fetch_by_ico("49240901")
        assert record.res is None
        assert any("no RES record" in note for note in record.notes)


class TestFailures:
    def test_server_error_is_unavailable_not_missing(self, settings: Settings) -> None:
        """A 5xx must never be read as "the subject does not exist"."""
        client = make_client(lambda request: httpx.Response(503, text="down"))
        with pytest.raises(SourceUnavailableError):
            AresSource(settings, client=client).fetch_by_ico("49240901")

    def test_timeout_is_unavailable(self, settings: Settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with pytest.raises(SourceUnavailableError, match="timed out"):
            AresSource(settings, client=make_client(handler)).fetch_by_ico("49240901")

    def test_client_error_is_a_response_error(self, settings: Settings) -> None:
        client = make_client(lambda request: httpx.Response(400, json={"kod": "CHYBA"}))
        with pytest.raises(SourceResponseError):
            AresSource(settings, client=client).fetch_by_ico("49240901")

    def test_non_json_body_is_a_response_error(self, settings: Settings) -> None:
        client = make_client(lambda request: httpx.Response(200, text="<html>maintenance"))
        with pytest.raises(SourceResponseError):
            AresSource(settings, client=client).fetch_by_ico("49240901")


class TestRequestShape:
    def test_uses_the_verified_endpoints(self, settings: Settings) -> None:
        calls: list[str] = []
        source = AresSource(settings, client=routed_client(res=payload(RES_PAYLOAD), calls=calls))
        source.fetch_by_ico("49240901")
        assert calls == [PATH_RES.format(ico="49240901"), PATH_VR.format(ico="49240901")]

    def test_search_returns_candidates(self, settings: Settings) -> None:
        body = {
            "pocetCelkem": 2,
            "ekonomickeSubjekty": [
                {"ico": "49240901", "obchodniJmeno": "Raiffeisenbank a.s."},
                {"ico": "26400276", "obchodniJmeno": "Raiffeisen stavební spořitelna a.s."},
            ],
        }
        calls: list[str] = []
        source = AresSource(settings, client=routed_client(search=body, calls=calls))
        candidates = source.search_by_name("Raiffeisen")

        assert calls == [PATH_SEARCH]
        assert [item.ico for item in candidates] == ["49240901", "26400276"]
        assert all(item.source == "ARES_LIVE" for item in candidates)

    def test_search_respects_the_limit(self, settings: Settings) -> None:
        body = {"ekonomickeSubjekty": [{"ico": f"0000000{n}"} for n in range(5)]}
        source = AresSource(settings, client=routed_client(search=body))
        assert len(source.search_by_name("x", limit=2)) == 2

    def test_search_tolerates_an_unexpected_body(self, settings: Settings) -> None:
        source = AresSource(settings, client=routed_client(search={"neco": "jineho"}))
        assert source.search_by_name("x") == ()


class TestThrottleAndRetry:
    def test_requests_are_spaced_out(self) -> None:
        """A batch must pace itself: ARES times out on back-to-back calls."""
        settings = Settings(ares_min_interval_seconds=0.25, ares_max_attempts=1)
        slept: list[float] = []
        clock = iter([0.0, 0.10, 0.25])  # mark, elapsed-at-second-call, mark
        source = AresSource(
            settings,
            client=routed_client(res=payload(RES_PAYLOAD), vr=payload(VR_PAYLOAD)),
            sleep=slept.append,
            monotonic=lambda: next(clock),
        )
        source.fetch_by_ico("49240901")
        assert slept and slept[0] == pytest.approx(0.15)

    def test_a_timeout_is_retried_then_succeeds(self) -> None:
        settings = Settings(ares_min_interval_seconds=0.0, ares_max_attempts=3)
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("timed out", request=request)
            if "ekonomicke-subjekty-res" in request.url.path:
                return httpx.Response(200, json=payload(RES_PAYLOAD))
            return httpx.Response(404, json={})

        source = AresSource(settings, client=make_client(handler), sleep=lambda _: None)
        record = source.fetch_by_ico("49240901")
        assert record is not None and record.res is not None
        assert attempts["n"] == 3  # one failure, one retry for RES, one call for VR

    def test_a_client_error_is_not_retried(self) -> None:
        settings = Settings(ares_min_interval_seconds=0.0, ares_max_attempts=3)
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, json={})

        source = AresSource(settings, client=make_client(handler), sleep=lambda _: None)
        with pytest.raises(SourceResponseError):
            source.fetch_by_ico("49240901")
        assert attempts["n"] == 1

    def test_retries_are_exhausted_then_reported(self) -> None:
        settings = Settings(ares_min_interval_seconds=0.0, ares_max_attempts=2)
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(500, text="boom")

        source = AresSource(settings, client=make_client(handler), sleep=lambda _: None)
        with pytest.raises(SourceUnavailableError):
            source.fetch_by_ico("49240901")
        assert attempts["n"] == 2


def test_close_leaves_an_injected_client_alone(settings: Settings) -> None:
    client = routed_client(res=payload(RES_PAYLOAD))
    source = AresSource(settings, client=client)
    source.close()
    assert not client.is_closed
