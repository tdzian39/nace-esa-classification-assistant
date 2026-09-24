"""Wikidata/Wikipedia: LEI -> item -> article summary, and how the gatherer uses it.

Payloads are trimmed live responses (see ``conftest.py``); nothing here touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from config.settings import Settings
from core.sources.base import SourceResponseError, SourceUnavailableError
from core.sources.web import SearchHit, StaticSearchProvider, WebEvidenceGatherer
from core.sources.wikimedia import WikimediaSource, _clean
from tests.sources.conftest import (
    WIKIDATA_BMW_NAME_SEARCH,
    WIKIDATA_DB_LEI,
    WIKIDATA_EIB_NAME_SEARCH,
    WIKIDATA_EMPTY_SEARCH,
    WIKIDATA_GERMANY_NAME_SEARCH,
    WIKIPEDIA_DB_EN,
    WIKIPEDIA_DISAMBIGUATION,
    wikimedia_client,
)

LEI = WIKIDATA_DB_LEI


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "wikimedia_enabled": True,
        "wikimedia_min_interval_seconds": 0.0,
        "wikimedia_max_attempts": 1,
        "wikipedia_languages": "cs,en",
        "web_enabled": True,
        "web_min_interval_seconds": 0.0,
        "web_search_url": None,
    }
    base.update(overrides)
    return Settings(**base)


def source(client: httpx.Client | None = None, **overrides: object) -> WikimediaSource:
    return WikimediaSource(
        settings(**overrides), client=client or wikimedia_client(), sleep=lambda _: None
    )


class TestItemByLei:
    def test_maps_the_live_payload(self) -> None:
        item = source().find_by_lei(LEI)
        assert item is not None
        assert item.qid == "Q66048"
        assert item.label == "Deutsche Bank"
        assert item.description_cs == "německá banka"
        assert item.description_en == "German global banking and financial services company"
        assert item.url == "https://www.wikidata.org/wiki/Q66048"
        assert [link.lang for link in item.sitelinks] == ["cs", "en"]
        assert item.provenance.source == "WEB"
        assert item.provenance.detail == "web:wikidata"

    def test_industries_put_preferred_first_and_drop_deprecated(self) -> None:
        item = source().find_by_lei(LEI)
        assert item is not None
        assert [industry.qid for industry in item.industries] == [
            "Q29585689",
            "Q837171",
            "Q29584334",
        ]
        assert item.industries[0].text == (
            "ostatní peněžní zprostředkování (other monetary intermediation)"
        )
        # Only an English label: shown as it is.
        assert item.industries[2].text.startswith("financial service activities")

    def test_facts_are_czech_with_the_english_in_parentheses(self) -> None:
        item = source().find_by_lei(LEI)
        assert item is not None
        facts = item.facts()
        assert facts[0] == (
            "Wikidata: německá banka (German global banking and financial services company)."
        )
        assert facts[1].startswith("Odvětví podle Wikidat: ostatní peněžní zprostředkování")

    def test_the_search_asks_for_the_lei_property(self) -> None:
        calls: list[httpx.Request] = []
        source(wikimedia_client(calls=calls)).find_by_lei(LEI)
        assert calls[0].url.params["srsearch"] == f"haswbstatement:P1278={LEI}"
        # The whole item (443 KB for Deutsche Bank) is never requested.
        assert all("claims" not in (call.url.params.get("props") or "") for call in calls)

    def test_the_sitelink_filter_follows_the_configured_languages(self) -> None:
        calls: list[httpx.Request] = []
        item = source(wikimedia_client(calls=calls), wikipedia_languages="en").find_by_lei(LEI)
        assert item is not None
        assert [link.lang for link in item.sitelinks] == ["en"]
        entity_call = next(
            call for call in calls if call.url.params.get("props", "").startswith("labels|desc")
        )
        assert entity_call.url.params["sitefilter"] == "enwiki"

    def test_no_item_with_the_lei_is_none(self) -> None:
        assert source(wikimedia_client(search=WIKIDATA_EMPTY_SEARCH)).find_by_lei(LEI) is None

    @pytest.mark.parametrize("lei", ["", "SHORT", "7ltwfzyicnsx8d621k8!", f"{LEI} OR x"])
    def test_a_malformed_lei_is_never_sent(self, lei: str) -> None:
        calls: list[httpx.Request] = []
        assert source(wikimedia_client(calls=calls)).find_by_lei(lei) is None
        assert calls == []

    def test_an_item_without_industries_asks_nothing_more(self) -> None:
        calls: list[httpx.Request] = []
        item = source(wikimedia_client(claims={"claims": {}}, calls=calls)).find_by_lei(LEI)
        assert item is not None
        assert item.industries == ()
        assert len(calls) == 3  # search, entity, claims - no industry labels


class TestItemByName:
    """The name path: only an exact, unique, un-owned match - the entity, not the item."""

    EIB = {"European Investment Bank": WIKIDATA_EIB_NAME_SEARCH}

    def test_the_one_item_bearing_the_name_is_taken(self) -> None:
        item = source(
            wikimedia_client(search=WIKIDATA_EMPTY_SEARCH, name_search=self.EIB)
        ).describe("5493006YXS1U5GIHE750", name="European Investment Bank")
        assert item is not None
        assert item.item.qid == "Q192247"
        assert item.item.matched_by == "name"

    def test_the_lei_is_tried_first_and_the_name_only_after_a_miss(self) -> None:
        calls: list[httpx.Request] = []
        item = source(wikimedia_client(calls=calls, name_search=self.EIB)).describe(
            LEI, name="European Investment Bank"
        )
        assert item is not None
        assert item.item.matched_by == "lei"
        assert all(c.url.params.get("action") != "wbsearchentities" for c in calls)

    def test_a_name_alone_is_enough(self) -> None:
        item = source(wikimedia_client(name_search=self.EIB)).describe(
            None, name="European Investment Bank"
        )
        assert item is not None
        assert item.item.qid == "Q192247"

    def test_case_diacritics_and_punctuation_do_not_matter(self) -> None:
        item = source(wikimedia_client(name_search=self.EIB)).find_by_name(
            "EUROPEAN  INVESTMENT-BANK"
        )
        assert item is not None

    def test_hits_that_merely_contain_the_name_do_not_count(self) -> None:
        # "European Investment Bank building" and "... project" are in the answer, not taken.
        item = source(wikimedia_client(name_search=self.EIB)).find_by_name(
            "European Investment Bank"
        )
        assert item is not None
        assert item.qid == "Q192247"

    def test_several_items_bearing_the_name_means_none(self) -> None:
        client = wikimedia_client(
            name_search={"Bundesrepublik Deutschland": WIKIDATA_GERMANY_NAME_SEARCH}
        )
        assert source(client).find_by_name("Bundesrepublik Deutschland") is None

    def test_the_legal_form_is_stripped_for_a_second_try(self) -> None:
        calls: list[httpx.Request] = []
        source(wikimedia_client(calls=calls, name_search=self.EIB)).find_by_name(
            "European Investment Bank AG"
        )
        searched = [
            c.url.params.get("search")
            for c in calls
            if c.url.params.get("action") == "wbsearchentities"
        ]
        assert searched[:2] == ["European Investment Bank AG"] * 2  # en, cs
        assert "European Investment Bank" in searched

    def test_a_brand_match_carrying_a_lei_is_not_the_vehicle(self) -> None:
        # "BMW Finance N.V." -> stripped "BMW Finance" -> the group's item, which has BMW AG's
        # LEI. The vehicle has no LEI here: the item is somebody, not evidence it is this one.
        client = wikimedia_client(
            name_search={"BMW Finance": WIKIDATA_BMW_NAME_SEARCH},
            item_lei={"Q26678": "5299000FUKEMR5ZJ0K48"},
        )
        assert source(client).find_by_name("BMW Finance N.V.") is None

    def test_an_item_with_another_entitys_lei_is_rejected_even_on_an_exact_match(self) -> None:
        client = wikimedia_client(
            name_search={"European Investment Bank": WIKIDATA_EIB_NAME_SEARCH},
            item_lei={"Q192247": "OTHER00000000000000X"},
        )
        assert source(client).find_by_name("European Investment Bank", lei=LEI) is None

    def test_an_exact_match_with_a_lei_is_accepted_when_ours_is_unknown(self) -> None:
        # GLEIF had no ISIN mapping, so we have no LEI; the full name matches one item that
        # states a LEI. The name is the whole legal name, so it is taken.
        client = wikimedia_client(
            name_search={"European Investment Bank": WIKIDATA_EIB_NAME_SEARCH},
            item_lei={"Q192247": "5493006YXS1U5GIHE750"},
        )
        assert source(client).find_by_name("European Investment Bank") is not None

    def test_the_first_edition_with_a_match_decides(self) -> None:
        # Live quirk: in the Czech edition the EIB *building* is labelled "European Investment
        # Bank" too. English answered first with one item, so Czech is not asked at all.
        calls: list[httpx.Request] = []
        item = source(wikimedia_client(calls=calls, name_search=self.EIB)).find_by_name(
            "European Investment Bank"
        )
        assert item is not None
        languages = [
            c.url.params.get("language")
            for c in calls
            if c.url.params.get("action") == "wbsearchentities"
        ]
        assert languages == ["en"]

    def test_a_family_name_never_counts(self) -> None:
        hits = {
            "search": [
                WIKIDATA_EIB_NAME_SEARCH["search"][0],
                {
                    **WIKIDATA_EIB_NAME_SEARCH["search"][0],
                    "id": "Q37507309",
                    "description": "family name",
                },
            ],
            "success": 1,
        }
        item = source(
            wikimedia_client(name_search={"European Investment Bank": hits})
        ).find_by_name("European Investment Bank")
        assert item is not None
        assert item.qid == "Q192247"

    def test_a_disambiguation_hit_never_counts(self) -> None:
        hit = {
            **WIKIDATA_EIB_NAME_SEARCH["search"][0],
            "description": "Wikimedia disambiguation page",
        }
        client = wikimedia_client(name_search={"Mercury": {"search": [hit], "success": 1}})
        assert source(client).find_by_name("Mercury") is None

    def test_too_short_a_name_is_never_searched(self) -> None:
        calls: list[httpx.Request] = []
        assert source(wikimedia_client(calls=calls)).find_by_name("AB") is None
        assert calls == []

    def test_the_switch_turns_the_name_path_off(self) -> None:
        calls: list[httpx.Request] = []
        client = wikimedia_client(calls=calls, search=WIKIDATA_EMPTY_SEARCH, name_search=self.EIB)
        assert (
            source(client, wikimedia_name_match=False).describe(
                LEI, name="European Investment Bank"
            )
            is None
        )
        assert all(c.url.params.get("action") != "wbsearchentities" for c in calls)


class TestSummary:
    def test_czech_comes_first(self) -> None:
        wiki = source().describe(LEI)
        assert wiki is not None and wiki.summary is not None
        assert wiki.summary.lang == "cs"
        assert wiki.summary.url == "https://cs.wikipedia.org/wiki/Deutsche_Bank"
        assert wiki.summary.provenance.detail == "web:wikipedia:cs"
        assert wiki.summary.revised_at is not None

    def test_editorial_marks_are_dropped(self) -> None:
        wiki = source().describe(LEI)
        assert wiki is not None and wiki.summary is not None
        assert "[kdy?]" not in wiki.summary.extract
        assert "\n" in wiki.summary.extract  # paragraphs kept

    def test_english_when_there_is_no_czech_article(self) -> None:
        wiki = source(wikimedia_client(summaries={"en": WIKIPEDIA_DB_EN})).describe(LEI)
        assert wiki is not None and wiki.summary is not None
        assert wiki.summary.lang == "en"

    def test_a_disambiguation_page_is_not_a_description(self) -> None:
        client = wikimedia_client(summaries={"cs": WIKIPEDIA_DISAMBIGUATION, "en": WIKIPEDIA_DB_EN})
        wiki = source(client).describe(LEI)
        assert wiki is not None and wiki.summary is not None
        assert wiki.summary.lang == "en"

    def test_a_wikipedia_outage_leaves_the_item_and_a_note(self) -> None:
        wiki = source(wikimedia_client(summaries={"cs": 503, "en": 503})).describe(LEI)
        assert wiki is not None
        assert wiki.summary is None
        assert len(wiki.notes) == 2
        assert wiki.notes[0].startswith("Wikipedie (cs): článek se nepodařilo načíst")
        # The item alone still describes the issuer.
        assert wiki.paragraphs and wiki.paragraphs[0].startswith("Wikidata: německá banka")

    def test_no_article_at_all_is_noted(self) -> None:
        entity = {
            "entities": {
                "Q66048": {"id": "Q66048", "labels": {}, "descriptions": {}, "sitelinks": {}}
            }
        }
        wiki = source(wikimedia_client(entity=entity)).describe(LEI)
        assert wiki is not None
        assert wiki.summary is None
        assert "nemá článek na Wikipedii" in wiki.notes[0]

    def test_no_item_means_no_description(self) -> None:
        assert source(wikimedia_client(search=WIKIDATA_EMPTY_SEARCH)).describe(LEI) is None


class TestFailures:
    def test_a_wikidata_outage_is_unavailable_not_a_miss(self) -> None:
        with pytest.raises(SourceUnavailableError):
            source(wikimedia_client(wikidata_status=503)).find_by_lei(LEI)

    def test_an_api_error_inside_a_200_is_a_response_error(self) -> None:
        client = wikimedia_client(search={"error": {"code": "badvalue", "info": "x"}})
        with pytest.raises(SourceResponseError, match="badvalue"):
            source(client).find_by_lei(LEI)

    def test_429_is_retried(self) -> None:
        answers = iter([httpx.Response(429), httpx.Response(200, json=WIKIDATA_EMPTY_SEARCH)])
        client = httpx.Client(transport=httpx.MockTransport(lambda request: next(answers)))
        assert source(client, wikimedia_max_attempts=2).find_by_lei(LEI) is None

    def test_a_4xx_is_not_retried(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(403, text="Please respect our robot policy")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(SourceResponseError):
            source(client, wikimedia_max_attempts=3).find_by_lei(LEI)
        assert len(calls) == 1

    def test_no_request_is_started_that_could_outlive_the_deadline(self) -> None:
        calls: list[httpx.Request] = []
        wiki = WikimediaSource(
            settings(wikimedia_timeout_seconds=5.0),
            client=wikimedia_client(calls=calls),
            sleep=lambda _: None,
            monotonic=lambda: 100.0,
        )
        with pytest.raises(SourceUnavailableError, match="nezbyl čas"):
            wiki.find_by_lei(LEI, deadline=104.0)
        assert calls == []

    def test_requests_are_spaced_out(self) -> None:
        now = {"t": 0.0}
        slept: list[float] = []
        wiki = WikimediaSource(
            settings(wikimedia_min_interval_seconds=0.3),
            client=wikimedia_client(),
            sleep=slept.append,
            monotonic=lambda: now["t"],
        )
        wiki.find_by_lei(LEI)
        assert slept and all(wait == pytest.approx(0.3) for wait in slept)


def test_clean_keeps_text_and_drops_marks() -> None:
    assert _clean("A  bank.[citation needed]\n\n\nIt lends.") == "A bank.\nIt lends."


# -- the gatherer ------------------------------------------------------------------------


class RecordingProvider(StaticSearchProvider):
    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        super().__init__(hits or [])
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int) -> tuple[SearchHit, ...]:
        self.queries.append(query)
        return super().search(query, limit=limit)


def gatherer(
    client: httpx.Client | None = None,
    provider: RecordingProvider | None = None,
    **overrides: object,
) -> WebEvidenceGatherer:
    config = settings(**overrides)
    return WebEvidenceGatherer(
        config,
        provider=provider or RecordingProvider(),
        wikimedia=WikimediaSource(
            config, client=client or wikimedia_client(), sleep=lambda _: None
        ),
        sleep=lambda _: None,
    )


class TestGatherer:
    def test_a_lei_without_a_typed_description_is_described_from_wikipedia(self) -> None:
        provider = RecordingProvider()
        evidence = gatherer(provider=provider).gather(name="DEUTSCHE BANK AG", lei=LEI)
        assert evidence.has_description
        assert evidence.description.startswith("Deutsche Bank AG je největší německá banka")
        assert "Odvětví podle Wikidat" in evidence.description
        assert evidence.provenance is not None
        assert evidence.provenance.source == "WEB"
        assert evidence.provenance.detail == "web:wikipedia:cs"
        assert provider.queries == []  # the paid search is not needed

    def test_both_pages_are_cited(self) -> None:
        evidence = gatherer().gather(name="DEUTSCHE BANK AG", lei=LEI)
        assert [source.url for source in evidence.sources] == [
            "https://www.wikidata.org/wiki/Q66048",
            "https://cs.wikipedia.org/wiki/Deutsche_Bank",
        ]
        assert all(source.fetched for source in evidence.sources)

    def test_the_reviewer_is_told_to_check_the_match(self) -> None:
        evidence = gatherer().gather(name="DEUTSCHE BANK AG", lei=LEI)
        assert any("ověřte, že popisuje právě tohoto emitenta" in n for n in evidence.notes)

    def test_the_typed_name_stays_the_name(self) -> None:
        assert gatherer().gather(name="Deutsche Bank AG", lei=LEI).issuer_name == (
            "Deutsche Bank AG"
        )
        assert gatherer().gather(lei=LEI).issuer_name == "Deutsche Bank"

    def test_a_typed_description_still_wins_and_nothing_is_asked(self) -> None:
        calls: list[httpx.Request] = []
        evidence = gatherer(wikimedia_client(calls=calls)).gather(
            name="Deutsche Bank AG", lei=LEI, description="Univerzální banka."
        )
        assert evidence.description == "Univerzální banka."
        assert calls == []

    def test_no_item_falls_through_to_the_search_provider(self) -> None:
        provider = RecordingProvider()
        evidence = gatherer(wikimedia_client(search=WIKIDATA_EMPTY_SEARCH), provider).gather(
            name="BMW Finance N.V.", lei=LEI
        )
        assert provider.queries == ["BMW Finance N.V."]
        assert not evidence.has_description
        assert "Wikidata nemá položku s tímto LEI ani jedinou položku s tímto názvem" in (
            evidence.notes
        )

    def test_a_name_match_is_flagged_as_such(self) -> None:
        client = wikimedia_client(
            search=WIKIDATA_EMPTY_SEARCH,
            name_search={"European Investment Bank": WIKIDATA_EIB_NAME_SEARCH},
        )
        evidence = gatherer(client).gather(name="European Investment Bank", lei=LEI)
        assert evidence.has_description
        assert any("podle shody názvu, ne identifikátoru" in n for n in evidence.notes)
        assert not any("podle LEI" in n for n in evidence.notes)

    def test_the_name_alone_can_bring_a_description(self) -> None:
        provider = RecordingProvider()
        client = wikimedia_client(
            name_search={"European Investment Bank": WIKIDATA_EIB_NAME_SEARCH}
        )
        evidence = gatherer(client, provider).gather(name="European Investment Bank")
        assert evidence.has_description
        assert provider.queries == []

    def test_an_outage_is_a_note_and_the_search_still_runs(self) -> None:
        provider = RecordingProvider()
        evidence = gatherer(wikimedia_client(wikidata_status=503), provider).gather(
            name="Deutsche Bank AG", lei=LEI
        )
        assert provider.queries == ["Deutsche Bank AG"]
        assert any(
            note.startswith("Wikidata: zdroj se nepodařilo dotázat") for note in evidence.notes
        )

    @pytest.mark.parametrize("switch", ["wikimedia_enabled", "web_enabled"])
    def test_either_switch_turns_it_off(self, switch: str) -> None:
        calls: list[httpx.Request] = []
        gatherer(wikimedia_client(calls=calls), **{switch: False}).gather(
            name="Deutsche Bank AG", lei=LEI
        )
        assert calls == []

    def test_without_a_lei_or_a_name_wikimedia_is_never_asked(self) -> None:
        calls: list[httpx.Request] = []
        gatherer(wikimedia_client(calls=calls)).gather(isin="DE0005140008")
        assert calls == []

    def test_without_a_lei_only_the_name_is_asked(self) -> None:
        calls: list[httpx.Request] = []
        evidence = gatherer(wikimedia_client(calls=calls)).gather(name="Deutsche Bank AG")
        assert {c.url.params.get("action") for c in calls} == {"wbsearchentities"}
        assert "Wikidata nemá jedinou položku s tímto názvem" in evidence.notes

    def test_the_description_is_capped_like_any_web_description(self) -> None:
        evidence = gatherer(web_max_description_chars=200).gather(lei=LEI)
        assert evidence.description is not None
        assert len(evidence.description) <= 201
