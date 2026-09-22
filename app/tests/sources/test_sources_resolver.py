"""Resolution policy: DWS first, ARES only as a fallback, and what each kind of miss means."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.sources.base import (
    OrRecord,
    Provenance,
    ResRecord,
    Source,
    SourceResponseError,
    SourceUnavailableError,
    SubjectCandidate,
    SubjectRecord,
)
from core.sources.resolver import SubjectResolver

RETRIEVED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
ICO = "49240901"


def record(
    source: Source, *, res: bool = True, or_half: bool = True, ico: str = ICO
) -> SubjectRecord:
    provenance = Provenance(source=source, retrieved_at=RETRIEVED)
    return SubjectRecord(
        ico=ico,
        res=ResRecord(ico=ico, provenance=provenance, name=f"from {source}") if res else None,
        or_record=OrRecord(ico=ico, provenance=provenance) if or_half else None,
    )


class FakeSource:
    """A scripted :class:`~core.sources.base.SubjectSource`."""

    def __init__(
        self,
        source: Source,
        *,
        result: SubjectRecord | None = None,
        error: Exception | None = None,
        candidates: tuple[SubjectCandidate, ...] = (),
    ) -> None:
        self.source = source
        self._result = result
        self._error = error
        self._candidates = candidates
        self.ico_calls: list[str] = []
        self.name_calls: list[str] = []
        self.closed = False

    def fetch_by_ico(self, ico: str) -> SubjectRecord | None:
        self.ico_calls.append(ico)
        if self._error is not None:
            raise self._error
        return self._result

    def search_by_name(self, name: str, *, limit: int = 10) -> tuple[SubjectCandidate, ...]:
        self.name_calls.append(name)
        if self._error is not None:
            raise self._error
        return self._candidates

    def close(self) -> None:
        self.closed = True


class TestPriority:
    def test_dws_answers_and_ares_is_not_called(self) -> None:
        dws = FakeSource("DWS", result=record("DWS"))
        ares = FakeSource("ARES_LIVE", result=record("ARES_LIVE"))
        result = SubjectResolver([dws, ares], user="tester").resolve(ICO)

        assert result.status == "found"
        assert result.record.res.provenance.source == "DWS"
        assert ares.ico_calls == []

    def test_ares_fills_in_when_dws_does_not_have_the_subject(self) -> None:
        dws = FakeSource("DWS", result=None)
        ares = FakeSource("ARES_LIVE", result=record("ARES_LIVE"))
        result = SubjectResolver([dws, ares], user="tester").resolve(ICO)

        assert result.status == "found"
        assert result.record.source_label == "ARES_LIVE"
        assert ares.ico_calls == [ICO]

    def test_a_partial_dws_hit_is_completed_from_ares(self) -> None:
        dws = FakeSource("DWS", result=record("DWS", or_half=False))
        ares = FakeSource("ARES_LIVE", result=record("ARES_LIVE"))
        result = SubjectResolver([dws, ares], user="tester").resolve(ICO)

        assert result.record.res.provenance.source == "DWS"
        assert result.record.or_record.provenance.source == "ARES_LIVE"
        assert result.record.source_label == "DWS+ARES_LIVE"


class TestFailureSemantics:
    def test_an_unavailable_dws_does_not_become_not_found(self) -> None:
        """The central distinction: "could not ask" must never be reported as "not registered"."""
        dws = FakeSource("DWS", error=SourceUnavailableError("warehouse down"))
        ares = FakeSource("ARES_LIVE", result=record("ARES_LIVE"))
        result = SubjectResolver([dws, ares], user="tester").resolve(ICO)

        assert result.status == "found"
        assert any("unavailable" in message for message in result.messages)

    def test_every_source_failing_is_an_error_not_a_miss(self) -> None:
        dws = FakeSource("DWS", error=SourceUnavailableError("down"))
        ares = FakeSource("ARES_LIVE", error=SourceUnavailableError("also down"))
        result = SubjectResolver([dws, ares], user="tester").resolve(ICO)

        assert result.status == "error"
        assert result.record is None
        assert result.sources_tried == ("DWS", "ARES_LIVE")

    def test_genuinely_absent_everywhere_is_not_found(self) -> None:
        resolver = SubjectResolver(
            [FakeSource("DWS", result=None), FakeSource("ARES_LIVE", result=None)], user="tester"
        )
        result = resolver.resolve(ICO)
        assert result.status == "not_found"

    def test_a_response_error_still_allows_the_fallback(self) -> None:
        dws = FakeSource("DWS", error=SourceResponseError("garbage"))
        ares = FakeSource("ARES_LIVE", result=record("ARES_LIVE"))
        assert SubjectResolver([dws, ares], user="tester").resolve(ICO).status == "found"


class TestInputParsing:
    def test_messy_ico_is_normalized(self) -> None:
        dws = FakeSource("DWS", result=record("DWS"))
        result = SubjectResolver([dws], user="tester").resolve(" 49 240 901 ")
        assert result.ico == ICO
        assert dws.ico_calls == [ICO]

    def test_a_bad_check_digit_is_rejected_not_searched_as_a_name(self) -> None:
        """Searching for "11111111" as a company name would only hide the typo."""
        dws = FakeSource("DWS", result=record("DWS"))
        result = SubjectResolver([dws], user="tester").resolve("11111111")

        assert result.status == "invalid_input"
        assert dws.ico_calls == [] and dws.name_calls == []
        assert "checksum" in result.messages[0]

    def test_empty_input_is_rejected(self) -> None:
        assert SubjectResolver([], user="tester").resolve("   ").status == "invalid_input"

    def test_text_goes_to_name_search(self) -> None:
        dws = FakeSource("DWS", candidates=())
        SubjectResolver([dws], user="tester").resolve("Raiffeisenbank a.s.")
        assert dws.name_calls == ["Raiffeisenbank a.s."]


class TestNameSearch:
    def test_a_single_match_is_resolved_by_ico(self) -> None:
        dws = FakeSource(
            "DWS",
            result=record("DWS"),
            candidates=(SubjectCandidate(ICO, "Raiffeisenbank a.s.", "DWS"),),
        )
        result = SubjectResolver([dws], user="tester").resolve("Raiffeisenbank")

        assert result.status == "found"
        assert result.ico == ICO

    def test_several_matches_are_ambiguous_and_nothing_is_guessed(self) -> None:
        dws = FakeSource(
            "DWS",
            candidates=(
                SubjectCandidate(ICO, "Raiffeisenbank a.s.", "DWS"),
                SubjectCandidate("26400276", "Raiffeisen stavební spořitelna a.s.", "DWS"),
            ),
        )
        result = SubjectResolver([dws], user="tester").resolve("Raiffeisen")

        assert result.status == "ambiguous"
        assert result.record is None
        assert len(result.candidates) == 2

    def test_no_match_anywhere_is_not_found(self) -> None:
        resolver = SubjectResolver(
            [FakeSource("DWS", candidates=()), FakeSource("ARES_LIVE", candidates=())],
            user="tester",
        )
        assert resolver.resolve("Neexistující s.r.o.").status == "not_found"


class TestBookkeeping:
    def test_the_codebook_version_is_stamped_on_the_record(self) -> None:
        resolver = SubjectResolver(
            [FakeSource("DWS", result=record("DWS"))],
            codebook_version="cb-0123456789abcdef",
            user="tester",
        )
        assert resolver.resolve(ICO).record.codebook_version == "cb-0123456789abcdef"

    def test_every_lookup_is_audited(self, caplog: pytest.LogCaptureFixture) -> None:
        resolver = SubjectResolver([FakeSource("DWS", result=record("DWS"))], user="tester")
        with caplog.at_level("INFO", logger="core.audit"):
            result = resolver.resolve(ICO)

        assert result.event is not None
        assert result.event.user == "tester"
        assert result.event.ico == ICO
        assert any("outcome=found" in entry.message for entry in caplog.records)

    def test_a_failed_lookup_is_audited_too(self, caplog: pytest.LogCaptureFixture) -> None:
        resolver = SubjectResolver(
            [FakeSource("DWS", error=SourceUnavailableError("down"))], user="tester"
        )
        with caplog.at_level("INFO", logger="core.audit"):
            resolver.resolve(ICO)
        assert any("outcome=error" in entry.message for entry in caplog.records)

    def test_resolve_many_preserves_order(self) -> None:
        resolver = SubjectResolver([FakeSource("DWS", result=None)], user="tester")
        results = resolver.resolve_many([ICO, "00177041"])
        assert [item.query for item in results] == [ICO, "00177041"]

    def test_close_closes_every_source(self) -> None:
        sources = [FakeSource("DWS"), FakeSource("ARES_LIVE")]
        SubjectResolver(sources, user="tester").close()
        assert all(source.closed for source in sources)
