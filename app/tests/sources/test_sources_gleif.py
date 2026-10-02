"""GLEIF: ISIN -> LEI record, parents, the fact sheet, and every way the register can fail.

Payloads are trimmed live responses (see ``conftest.py``); nothing here touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from config.settings import Settings
from core.classify.hints import hinted_esa_families, hinted_nace
from core.sources.base import SourceResponseError, SourceUnavailableError
from core.sources.gleif import GleifSource, LeiRecord
from tests.sources.conftest import (
    GLEIF_ADIDAS_AG,
    GLEIF_ADIDAS_SEARCH,
    GLEIF_BMW_AG,
    GLEIF_BMW_FINANCE,
    GLEIF_CEZ,
    GLEIF_CZ_FUND,
    GLEIF_DEUTSCHE_BANK,
    GLEIF_EIB,
    GLEIF_FUND,
    GLEIF_KB,
    GLEIF_LAND_BERLIN,
    GLEIF_MF,
    _gleif_item,
    gleif_client,
    make_client,
)

DB_ISIN = "DE0005140008"
DB_LEI = "7LTWFZYICNSX8D621K86"
BMW_LEI = "5299006ZHG3IXU0PNJ56"


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gleif_min_interval_seconds": 0.0,
        "gleif_max_attempts": 1,
        "gleif_fetch_parents": True,
    }
    base.update(overrides)
    return Settings(**base)


def deutsche_bank(**client_kwargs: object) -> GleifSource:
    client = gleif_client(
        by_isin={DB_ISIN: GLEIF_DEUTSCHE_BANK},
        records={DB_LEI: GLEIF_DEUTSCHE_BANK},
        exceptions={DB_LEI: "NO_KNOWN_PERSON"},
        **client_kwargs,  # type: ignore[arg-type]
    )
    return GleifSource(settings(), client=client, sleep=lambda _: None)


def bmw_finance(**settings_overrides: object) -> GleifSource:
    client = gleif_client(
        by_isin={"FR0129895324": GLEIF_BMW_FINANCE},
        parents={
            f"{BMW_LEI}/direct-parent": GLEIF_BMW_AG,
            f"{BMW_LEI}/ultimate-parent": GLEIF_BMW_AG,
        },
    )
    return GleifSource(settings(**settings_overrides), client=client, sleep=lambda _: None)


class TestIsinLookup:
    def test_maps_the_live_payload(self) -> None:
        record = deutsche_bank().find_by_isin(DB_ISIN)
        assert record is not None
        assert record.lei == DB_LEI
        assert record.legal_name == "DEUTSCHE BANK AKTIENGESELLSCHAFT"
        assert record.country == "DE"
        assert record.jurisdiction == "DE"
        assert record.category == "GENERAL"
        assert record.sub_category is None
        assert record.legal_form_id == "6QQB"
        assert record.entity_status == "ACTIVE"
        assert record.registration_status == "ISSUED"

    def test_provenance_is_gleif_with_the_register_update_as_snapshot(self) -> None:
        record = deutsche_bank().find_by_isin(DB_ISIN)
        assert record is not None
        assert record.provenance.source == "GLEIF"
        assert record.provenance.snapshot_at is not None
        assert record.provenance.snapshot_at.year == 2026
        assert record.provenance.retrieved_at.tzinfo is not None

    def test_an_isin_gleif_does_not_map_is_none(self) -> None:
        """``data: []`` is a fact about coverage (the ETF IE00B4L5Y983), not an outage."""
        assert deutsche_bank().find_by_isin("IE00B4L5Y983") is None

    def test_uses_the_verified_endpoint_and_media_type(self) -> None:
        calls: list[str] = []
        deutsche_bank(calls=calls).find_by_isin(DB_ISIN)
        assert calls[0].startswith("https://api.gleif.org/api/v1/lei-records?")
        assert "filter%5Bisin%5D=DE0005140008" in calls[0]
        assert "page%5Bsize%5D=1" in calls[0]

    def test_the_record_page_is_the_citable_url(self) -> None:
        record = deutsche_bank().find_by_isin(DB_ISIN)
        assert record is not None
        assert record.url == f"https://search.gleif.org/#/record/{DB_LEI}"

    def test_fetch_by_lei(self) -> None:
        record = deutsche_bank().fetch(DB_LEI.lower())
        assert record is not None and record.legal_name == "DEUTSCHE BANK AKTIENGESELLSCHAFT"

    def test_fetch_of_an_unknown_lei_is_none(self) -> None:
        assert deutsche_bank().fetch("0000000000000000XX00") is None


class TestParents:
    def test_direct_and_ultimate_parent_are_read(self) -> None:
        record = bmw_finance().find_by_isin("FR0129895324")
        assert record is not None
        assert record.direct_parent is not None
        assert record.direct_parent.legal_name == "Bayerische Motoren Werke Aktiengesellschaft"
        assert record.direct_parent.country == "DE"
        assert (
            record.ultimate_parent is not None
            and record.ultimate_parent.lei == record.direct_parent.lei
        )

    def test_a_parent_abroad_is_stated_as_a_fact(self) -> None:
        """NL issuer, DE parent: the fact the ESA control axis turns on, stated, not decided."""
        record = bmw_finance().find_by_isin("FR0129895324")
        assert record is not None
        assert record.parent_abroad is True
        assert any("jiné zemi (DE)" in line and "(NL)" in line for line in record.facts())

    def test_no_parent_is_explained_by_the_reporting_exception(self) -> None:
        record = deutsche_bank().find_by_isin(DB_ISIN)
        assert record is not None
        assert record.direct_parent is None and record.ultimate_parent is None
        assert record.parent_exception == "NO_KNOWN_PERSON"
        assert record.parent_abroad is None
        assert any("není v GLEIF uvedena" in line for line in record.facts())

    def test_parents_are_not_fetched_when_switched_off(self) -> None:
        calls: list[str] = []
        client = gleif_client(by_isin={DB_ISIN: GLEIF_DEUTSCHE_BANK}, calls=calls)
        source = GleifSource(
            settings(gleif_fetch_parents=False), client=client, sleep=lambda _: None
        )
        record = source.find_by_isin(DB_ISIN)
        assert record is not None and record.parent_exception is None
        assert len(calls) == 1

    def test_three_requests_when_no_parent_is_reported(self) -> None:
        """Record, direct parent (404), ultimate parent (404), then the reason - four in all."""
        calls: list[str] = []
        deutsche_bank(calls=calls).find_by_isin(DB_ISIN)
        assert len(calls) == 4
        assert calls[-1].endswith("/direct-parent-reporting-exception")

    def test_a_failing_parent_lookup_does_not_lose_the_record(self) -> None:
        """An outage on Level 2 must not turn a found issuer into nothing."""
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": [GLEIF_DEUTSCHE_BANK]})
            return httpx.Response(500, text="boom")

        source = GleifSource(
            settings(),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        record = source.find_by_isin(DB_ISIN)
        assert record is not None and record.legal_name == "DEUTSCHE BANK AKTIENGESELLSCHAFT"
        assert record.direct_parent is None and record.parent_exception is None


def record_from(item: dict) -> LeiRecord:
    client = gleif_client(by_isin={"XS0000000000": item})
    source = GleifSource(settings(gleif_fetch_parents=False), client=client, sleep=lambda _: None)
    record = source.find_by_isin("XS0000000000")
    assert record is not None
    return record


class TestFactSheet:
    """The facts are worded so the keyword table (hints.py) reacts to the register's category."""

    def test_starts_with_the_register_and_the_name(self) -> None:
        sheet = record_from(GLEIF_DEUTSCHE_BANK).fact_sheet()
        assert sheet.startswith(f"GLEIF (LEI {DB_LEI}): DEUTSCHE BANK AKTIENGESELLSCHAFT.")
        assert "Země sídla: DE." in sheet
        assert "ELF 6QQB" in sheet

    def test_a_bank_name_reaches_the_bank_family_and_division_64(self) -> None:
        sheet = record_from(GLEIF_DEUTSCHE_BANK).fact_sheet()
        assert "64" in hinted_nace(sheet)
        assert "banky" in hinted_esa_families(sheet)

    def test_a_fund_is_glossed_as_an_investment_fund(self) -> None:
        record = record_from(GLEIF_FUND)
        assert record.category_text is not None and "investment fund" in record.category_text
        assert "investicni fondy jine nez fondy penezniho trhu" in hinted_esa_families(
            record.fact_sheet()
        )
        assert "registrace LEI LAPSED" in record.fact_sheet()

    def test_a_state_government_is_glossed_as_government(self) -> None:
        record = record_from(GLEIF_LAND_BERLIN)
        sheet = record.fact_sheet()
        assert "STATE_GOVERNMENT" in sheet and "state government" in sheet
        assert "Legal Entity of Public Law" in sheet
        families = hinted_esa_families(sheet)
        assert "ustredni vladni instituce" in families or "narodni vladni instituce" in families

    def test_an_international_organisation_is_glossed_as_supranational(self) -> None:
        record = record_from(GLEIF_EIB)
        sheet = record.fact_sheet()
        assert "supranational" in sheet
        assert "jurisdikce EU" in sheet
        assert "mezinarodni rozvojove banky" in hinted_esa_families(sheet)

    def test_an_unknown_category_is_shown_as_is(self) -> None:
        item = {**GLEIF_DEUTSCHE_BANK, "attributes": {**GLEIF_DEUTSCHE_BANK["attributes"]}}
        item["attributes"]["entity"] = {**item["attributes"]["entity"], "category": "SOMETHING_NEW"}
        assert record_from(item).category_text == "SOMETHING_NEW"


class TestFailures:
    def test_server_error_is_unavailable_not_missing(self) -> None:
        source = GleifSource(settings(), client=gleif_client(status=500), sleep=lambda _: None)
        with pytest.raises(SourceUnavailableError):
            source.find_by_isin(DB_ISIN)

    def test_rate_limiting_is_retried_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, json={"data": [GLEIF_DEUTSCHE_BANK]})

        source = GleifSource(
            settings(gleif_max_attempts=3, gleif_fetch_parents=False),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        assert source.find_by_isin(DB_ISIN) is not None
        assert attempts["n"] == 2

    def test_timeout_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        source = GleifSource(
            settings(),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        with pytest.raises(SourceUnavailableError):
            source.find_by_isin(DB_ISIN)

    def test_client_error_is_a_response_error_and_not_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, json={"errors": [{"status": "400"}]})

        source = GleifSource(
            settings(gleif_max_attempts=3),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        with pytest.raises(SourceResponseError):
            source.find_by_isin(DB_ISIN)
        assert attempts["n"] == 1

    def test_non_json_body_is_a_response_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>maintenance</html>")

        source = GleifSource(
            settings(),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        with pytest.raises(SourceResponseError):
            source.find_by_isin(DB_ISIN)

    def test_retries_are_exhausted_then_reported(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(503, text="down")

        source = GleifSource(
            settings(gleif_max_attempts=2),
            client=make_client(handler, base_url="https://api.gleif.org/api/v1"),
            sleep=lambda _: None,
        )
        with pytest.raises(SourceUnavailableError):
            source.find_by_isin(DB_ISIN)
        assert attempts["n"] == 2


class TestThrottle:
    def test_requests_are_spaced_out(self) -> None:
        """60 requests a minute is the published limit; a batch must stay under it."""
        slept: list[float] = []
        clock = iter([0.0, 0.3, 1.0, 1.4, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5])
        client = gleif_client(
            by_isin={DB_ISIN: GLEIF_DEUTSCHE_BANK}, exceptions={DB_LEI: "NO_KNOWN_PERSON"}
        )
        source = GleifSource(
            settings(gleif_min_interval_seconds=1.0),
            client=client,
            sleep=slept.append,
            monotonic=lambda: next(clock),
        )
        source.find_by_isin(DB_ISIN)
        assert slept and slept[0] == pytest.approx(0.7)


def test_close_leaves_an_injected_client_alone() -> None:
    client = gleif_client()
    source = GleifSource(settings(), client=client)
    source.close()
    assert not client.is_closed


class TestNameLookup:
    """A typed name -> the one active entity so named; a namesake is worse than nothing."""

    @staticmethod
    def source(by_name: dict[str, list[dict[str, object]]], calls: list[str] | None = None):
        client = gleif_client(by_name=by_name, calls=calls)  # type: ignore[arg-type]
        return GleifSource(settings(gleif_fetch_parents=False), client=client, sleep=lambda _: None)

    def test_the_brand_finds_the_parent_not_the_subsidiaries(self) -> None:
        record = self.source({"adidas": GLEIF_ADIDAS_SEARCH}).find_by_name("adidas")
        assert record is not None
        assert record.lei == "549300JSX0Z4CW0V5023"
        assert record.legal_name == "adidas AG"

    def test_the_full_name_matches_exactly(self) -> None:
        record = self.source({"Adidas AG": GLEIF_ADIDAS_SEARCH}).find_by_name("ADIDAS  AG")
        assert record is not None and record.legal_name == "adidas AG"

    def test_two_entities_bearing_the_name_means_none(self) -> None:
        twin = {**GLEIF_ADIDAS_AG, "id": "X" * 20}
        twin["attributes"] = {**GLEIF_ADIDAS_AG["attributes"], "lei": "X" * 20}  # type: ignore[dict-item]
        assert self.source({"adidas": [GLEIF_ADIDAS_AG, twin]}).find_by_name("adidas") is None

    def test_an_inactive_entity_is_never_taken(self) -> None:
        gone = {**GLEIF_ADIDAS_AG}
        attributes = dict(gone["attributes"])  # type: ignore[arg-type]
        attributes["entity"] = {**attributes["entity"], "status": "INACTIVE"}
        gone["attributes"] = attributes
        assert self.source({"adidas": [gone]}).find_by_name("adidas") is None

    def test_a_name_that_only_contains_the_query_is_not_a_match(self) -> None:
        assert (
            self.source({"adidas Foundation": GLEIF_ADIDAS_SEARCH}).find_by_name(
                "adidas Foundation"
            )
            is None
        )

    def test_the_search_asks_the_legal_name_filter(self) -> None:
        calls: list[str] = []
        self.source({"adidas": GLEIF_ADIDAS_SEARCH}, calls).find_by_name("adidas")
        assert "filter%5Bentity.legalName%5D=adidas" in calls[0]


class TestLegalFormsAgree:
    """'OMV' names three entities; 'OMV AG' only the Austrian one (live, 2026-09-29)."""

    OMV_AG = _gleif_item(
        "549300V62YJ9HTLRI486",
        "OMV AKTIENGESELLSCHAFT",
        country="AT",
        jurisdiction="AT",
        category="GENERAL",
    )
    NAMESAKES = [
        OMV_AG,
        _gleif_item(
            "549300TS4WX683KMWT10", "ÖMV AB", country="SE", jurisdiction="SE", category="GENERAL"
        ),
        _gleif_item(
            "815600B16123A5D64366",
            "OMV - S.P.A.",
            country="IT",
            jurisdiction="IT",
            category="GENERAL",
        ),
    ]
    TRADING = _gleif_item(
        "549300HUJ5CJUX02RS52",
        "OMV SUPPLY & TRADING AG",
        country="CH",
        jurisdiction="CH",
        category="GENERAL",
    )

    def source(self, calls: list[str] | None = None) -> GleifSource:
        client = gleif_client(
            by_name={"OMV": self.NAMESAKES, "OMV AG": [self.TRADING]}, calls=calls
        )
        return GleifSource(settings(gleif_fetch_parents=False), client=client, sleep=lambda _: None)

    def test_the_brand_alone_is_a_tie(self) -> None:
        assert self.source().find_by_name("OMV") is None

    def test_the_short_legal_form_picks_the_long_one(self) -> None:
        record = self.source().find_by_name("OMV AG")
        assert record is not None and record.legal_name == "OMV AKTIENGESELLSCHAFT"

    def test_the_search_is_widened_only_after_the_exact_level_missed(self) -> None:
        calls: list[str] = []
        self.source(calls).find_by_name("OMV AG")
        assert len(calls) == 2
        assert "filter%5Bentity.legalName%5D=OMV&" in calls[1]


def test_match_name_reports_how_many_tied() -> None:
    source = TestLegalFormsAgree().source()
    assert source.match_name("OMV") == (None, 3)
    assert source.match_name("Nobody at all") == (None, 0)


class TestBrandsAndBranches:
    """Batch 3, 29 Sept 2026: 'Allianz' took a Belgian entity named just ALLIANZ; the full
    name of Deutsche Bank tied with its own French branch."""

    @staticmethod
    def item(lei: str, name: str, category: str = "GENERAL") -> dict[str, object]:
        return _gleif_item(lei, name, country="DE", jurisdiction="DE", category=category)

    def find(self, query: str, items: list[dict[str, object]]):
        client = gleif_client(by_name={query: items})  # type: ignore[arg-type]
        source = GleifSource(
            settings(gleif_fetch_parents=False), client=client, sleep=lambda _: None
        )
        return source.match_name(query)

    def test_a_bare_brand_ties_an_entity_named_just_so_with_the_group(self) -> None:
        items = [self.item("B" * 20, "ALLIANZ"), self.item("S" * 20, "Allianz SE")]
        assert self.find("Allianz", items) == (None, 2)

    def test_a_branch_never_ties_its_head_office(self) -> None:
        items = [
            self.item("F" * 20, "Deutsche Bank Aktiengesellschaft", "BRANCH"),
            self.item("7LTWFZYICNSX8D621K86", "DEUTSCHE BANK AKTIENGESELLSCHAFT"),
        ]
        record, _ = self.find("Deutsche Bank Aktiengesellschaft", items)
        assert record is not None and record.lei == "7LTWFZYICNSX8D621K86"


class TestCzechRegistration:
    """``registeredAs`` is the IČO of a Czech entity (2026-10-02: 598 of 600 sampled CZ LEIs)."""

    def test_registered_as_and_registered_at_are_read(self) -> None:
        record = record_from(GLEIF_CEZ)
        assert record.registered_as == "45274649"
        assert record.registered_at == "RA000163"
        assert record.ico == "45274649"

    def test_the_ico_does_not_depend_on_the_registration_authority(self) -> None:
        """The Ministry of Finance is filed under RA000168, not RA000163; it is still the IČO."""
        record = record_from(GLEIF_MF)
        assert record.registered_at == "RA000168"
        assert record.ico == "00006947"

    def test_a_fund_without_a_registered_number_has_no_ico(self) -> None:
        record = record_from(GLEIF_CZ_FUND)
        assert record.registered_at == "RA999999"
        assert record.registered_as is None and record.ico is None

    def test_a_foreign_registration_number_is_not_an_ico(self) -> None:
        item = _gleif_item(
            DB_LEI,
            "DEUTSCHE BANK AKTIENGESELLSCHAFT",
            country="DE",
            jurisdiction="DE",
            category="GENERAL",
            registered_as="12345678",
            registered_at="RA000242",
        )
        assert record_from(item).ico is None

    @pytest.mark.parametrize("number", ["4527464", "452746490", "CZ45274649", "4527 4649"])
    def test_only_eight_digits_are_an_ico(self, number: str) -> None:
        item = _gleif_item(
            "529900S5R9YHJHYKKG94",
            "ČEZ, a. s.",
            country="CZ",
            jurisdiction="CZ",
            category="GENERAL",
            registered_as=number,
        )
        assert record_from(item).ico is None


class TestIcoLookup:
    def test_the_entity_registered_under_the_ico_is_found(self) -> None:
        calls: list[str] = []
        client = gleif_client(by_ico={"45317054": [GLEIF_KB]}, calls=calls)
        record = GleifSource(settings(gleif_fetch_parents=False), client=client).find_by_ico(
            "45317054"
        )
        assert record is not None and record.lei == "IYKCAVNFR8QGF00HV840"
        assert "filter%5Bentity.registeredAs%5D=45317054" in calls[0]
        assert "filter%5Bentity.legalAddress.country%5D=CZ" in calls[0]

    def test_an_ico_gleif_does_not_know_is_none(self) -> None:
        """The Prague branch of ING Bank N.V. (49279866) has no LEI of its own."""
        client = gleif_client(by_ico={})
        assert GleifSource(settings(), client=client).find_by_ico("49279866") is None

    def test_a_record_whose_own_ico_differs_is_not_taken(self) -> None:
        client = gleif_client(by_ico={"45317054": [GLEIF_CEZ]})
        assert GleifSource(settings(), client=client).find_by_ico("45317054") is None

    def test_two_active_records_for_one_ico_mean_none(self) -> None:
        twin = {**GLEIF_KB, "id": "529900AAAAAAAAAAAA00"}
        twin = {**twin, "attributes": {**twin["attributes"], "lei": "529900AAAAAAAAAAAA00"}}
        client = gleif_client(by_ico={"45317054": [GLEIF_KB, twin]})
        assert GleifSource(settings(), client=client).find_by_ico("45317054") is None

    def test_an_outage_is_unavailable_not_missing(self) -> None:
        client = gleif_client(status=503)
        source = GleifSource(settings(), client=client, sleep=lambda _: None)
        with pytest.raises(SourceUnavailableError):
            source.find_by_ico("45317054")
