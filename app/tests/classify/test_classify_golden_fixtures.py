"""Recorded register answers for the golden set: capture once, replay without the network.

The "live" registers here are the trimmed GLEIF/OpenFIGI payloads of the source tests, served
through mock transports - so the round trip record -> trim -> replay is exercised end to end
and the fact sheet the golden run scores can be compared with the one the pipeline would see.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from config.settings import Settings
from core.classify.golden import GoldenCase, GoldenError
from core.classify.golden_fixtures import (
    MAX_LIST_ITEMS,
    capture,
    identifier_over,
    load_answers,
    replay_transport,
    replaying_identifier,
    request_key,
    trim,
)
from tests.sources.conftest import (
    FIGI_DEUTSCHE_BANK,
    GLEIF_BMW_AG,
    GLEIF_BMW_FINANCE,
    GLEIF_DEUTSCHE_BANK,
    figi_client,
    gleif_client,
)

DB_ISIN = "DE0005140008"
BMW_ISIN = "FR0129895324"


def settings() -> Settings:
    return Settings(
        _env_file=None, gleif_min_interval_seconds=0.0, openfigi_min_interval_seconds=0.0
    )


def live_registers() -> httpx.MockTransport:
    """Both registers as the source tests fake them: Deutsche Bank, BMW Finance -> BMW AG."""
    gleif = gleif_client(
        by_isin={DB_ISIN: GLEIF_DEUTSCHE_BANK, BMW_ISIN: GLEIF_BMW_FINANCE},
        parents={
            f"{GLEIF_BMW_FINANCE['id']}/direct-parent": GLEIF_BMW_AG,
            f"{GLEIF_BMW_FINANCE['id']}/ultimate-parent": GLEIF_BMW_AG,
        },
        exceptions={GLEIF_DEUTSCHE_BANK["id"]: "NO_KNOWN_PERSON"},
    )
    figi = figi_client(FIGI_DEUTSCHE_BANK)
    return httpx.MockTransport(
        lambda request: (gleif if request.url.host == "api.gleif.org" else figi).send(request)
    )


def case(case_id: str, isin: str | None) -> GoldenCase:
    return GoldenCase(id=case_id, issuer=case_id, description="Popis.", isin=isin)


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    path = tmp_path / "identity.json"
    capture(
        [case("db", DB_ISIN), case("bmw", BMW_ISIN), case("fictional", None)],
        settings(),
        captured_on=date(2026, 9, 22),
        path=path,
        transport=live_registers(),
    )
    return path


class TestTrim:
    def test_only_the_keys_the_parsers_read_survive(self) -> None:
        payload = {
            "data": {"id": "X", "links": {"self": "u"}, "attributes": {"lei": "X", "foo": 1}}
        }
        assert trim(payload) == {"data": {"id": "X", "attributes": {"lei": "X"}}}

    def test_long_lists_are_cut(self) -> None:
        assert len(trim([{"data": list(range(10))}])[0]["data"]) == MAX_LIST_ITEMS  # type: ignore[index]


class TestRequestKey:
    def test_a_get_is_filed_under_its_url(self) -> None:
        request = httpx.Request("GET", "https://api.gleif.org/api/v1/lei-records/X")
        assert request_key(request) == "GET https://api.gleif.org/api/v1/lei-records/X"

    def test_a_post_includes_its_body(self) -> None:
        request = httpx.Request("POST", "https://api.openfigi.com/v3/mapping", json=[{"a": 1}])
        assert request_key(request).startswith("POST https://api.openfigi.com/v3/mapping [")


class TestCapture:
    def test_every_isin_is_recorded_and_dated(self, recording: Path) -> None:
        document = json.loads(recording.read_text(encoding="utf-8"))
        assert document["captured_on"] == "2026-09-22"
        keys = list(document["answers"])
        assert any(DB_ISIN in key for key in keys)
        assert any(BMW_ISIN in key for key in keys)
        assert any("ultimate-parent" in key for key in keys)

    def test_a_case_without_an_isin_asks_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "identity.json"
        count = capture(
            [case("fictional", None)],
            settings(),
            captured_on=date(2026, 9, 22),
            path=path,
            transport=live_registers(),
        )
        assert count == 0

    def test_a_missing_file_means_nothing_recorded(self, tmp_path: Path) -> None:
        assert load_answers(tmp_path / "absent.json") == {}


class TestReplay:
    def test_the_replayed_identity_is_the_one_the_registers_gave(self, recording: Path) -> None:
        """Trimming must lose nothing the parsers need: same LEI, same facts."""
        direct = identifier_over(settings(), live_registers(), sleep=lambda seconds: None)
        replayed = replaying_identifier(settings(), load_answers(recording))
        for isin in (DB_ISIN, BMW_ISIN):
            expected = direct.identify(isin)
            actual = replayed.identify(isin)
            assert actual.lei == expected.lei
            assert actual.facts() == expected.facts()
            assert actual.fact_sheet() == expected.fact_sheet()

    def test_the_parent_reaches_the_fact_sheet(self, recording: Path) -> None:
        identity = replaying_identifier(settings(), load_answers(recording)).identify(BMW_ISIN)
        assert "DE" in identity.fact_sheet()

    def test_an_unrecorded_request_is_an_error_not_a_silent_miss(self) -> None:
        client = httpx.Client(transport=replay_transport({}))
        with pytest.raises(GoldenError, match="--golden-capture"):
            client.get("https://api.gleif.org/api/v1/lei-records/UNKNOWN")
