"""Record semantics: NACE as a list, both revisions, nace_mismatch, provenance, merging."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    OrRecord,
    Provenance,
    ResRecord,
    SubjectRecord,
)

RETRIEVED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SNAPSHOT = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)


def provenance(source: str = "DWS", snapshot: datetime | None = SNAPSHOT) -> Provenance:
    return Provenance(source=source, retrieved_at=RETRIEVED, snapshot_at=snapshot)


def res(
    *codes: NaceAssignment,
    ico: str = "49240901",
    source: str = "DWS",
    snapshot: datetime | None = SNAPSHOT,
) -> ResRecord:
    return ResRecord(ico=ico, provenance=provenance(source, snapshot), name="Test a.s.", nace=codes)


class TestNaceAssignment:
    def test_digits_ignores_spelling(self) -> None:
        assert NaceAssignment("64.19", NACE_REV_2).digits == "6419"
        assert NaceAssignment("6419", NACE_REV_2).digits == "6419"

    def test_leading_zero_is_preserved(self) -> None:
        """Agriculture must not silently become division 11 later on."""
        assert NaceAssignment("01110", NACE_REV_2).code == "01110"
        assert NaceAssignment("01110", NACE_REV_2).digits == "01110"


class TestResRecordNace:
    def test_main_and_others_are_split_per_revision(self) -> None:
        record = res(
            NaceAssignment("29100", NACE_REV_2, is_main=True),
            NaceAssignment("25500", NACE_REV_2),
            NaceAssignment("29200", NACE_REV_21, is_main=True),
            NaceAssignment("25400", NACE_REV_21),
        )
        assert record.main(NACE_REV_2).code == "29100"
        assert [item.code for item in record.others(NACE_REV_2)] == ["25500"]
        assert record.main(NACE_REV_21).code == "29200"
        assert [item.code for item in record.others(NACE_REV_21)] == ["25400"]

    def test_main_comes_first_in_codes(self) -> None:
        record = res(
            NaceAssignment("25500", NACE_REV_2),
            NaceAssignment("29100", NACE_REV_2, is_main=True),
        )
        assert record.codes(NACE_REV_2) == ("29100", "25500")

    def test_duplicate_codes_are_collapsed_per_revision(self) -> None:
        record = res(
            NaceAssignment("64.19", NACE_REV_2, is_main=True),
            NaceAssignment("6419", NACE_REV_2),
            NaceAssignment("6419", NACE_REV_21),
        )
        assert len(record.nace) == 2
        assert record.main(NACE_REV_2).code == "64.19"

    def test_missing_main_is_none_not_a_guess(self) -> None:
        record = res(NaceAssignment("64190", NACE_REV_2))
        assert record.main(NACE_REV_2) is None


class TestNaceMismatch:
    def test_false_when_the_revisions_agree(self) -> None:
        record = res(
            NaceAssignment("64190", NACE_REV_2, is_main=True),
            NaceAssignment("64190", NACE_REV_21, is_main=True),
        )
        assert record.nace_mismatch is False

    def test_true_when_the_prevailing_codes_differ(self) -> None:
        record = res(
            NaceAssignment("29100", NACE_REV_2, is_main=True),
            NaceAssignment("29200", NACE_REV_21, is_main=True),
        )
        assert record.nace_mismatch is True

    def test_spelling_alone_is_not_a_mismatch(self) -> None:
        record = res(
            NaceAssignment("64.19", NACE_REV_2, is_main=True),
            NaceAssignment("6419", NACE_REV_21, is_main=True),
        )
        assert record.nace_mismatch is False

    @pytest.mark.parametrize(
        "codes",
        [
            (NaceAssignment("64190", NACE_REV_2, is_main=True),),
            (NaceAssignment("64190", NACE_REV_21, is_main=True),),
            (),
        ],
    )
    def test_none_when_a_revision_is_missing(self, codes: tuple[NaceAssignment, ...]) -> None:
        """ "Cannot tell" must not be reported as "they agree"."""
        assert res(*codes).nace_mismatch is None


class TestSubjectRecord:
    def test_source_label_lists_both_halves(self) -> None:
        record = SubjectRecord(
            ico="49240901",
            res=res(source="DWS"),
            or_record=OrRecord(ico="49240901", provenance=provenance("ARES_LIVE")),
        )
        assert record.sources == ("DWS", "ARES_LIVE")
        assert record.source_label == "DWS+ARES_LIVE"

    def test_timestamp_prefers_the_snapshot(self) -> None:
        record = SubjectRecord(ico="49240901", res=res())
        assert record.timestamp == SNAPSHOT

    def test_timestamp_falls_back_to_retrieval(self) -> None:
        record = SubjectRecord(ico="49240901", res=res(snapshot=None))
        assert record.timestamp == RETRIEVED

    def test_snapshot_is_the_oldest_across_halves(self) -> None:
        older = datetime(2025, 1, 1, tzinfo=UTC)
        record = SubjectRecord(
            ico="49240901",
            res=res(),
            or_record=OrRecord(
                ico="49240901",
                provenance=Provenance("DWS", retrieved_at=RETRIEVED, snapshot_at=older),
            ),
        )
        assert record.snapshot_at == older

    def test_name_falls_back_to_obchodni_firma(self) -> None:
        record = SubjectRecord(
            ico="49240901",
            or_record=OrRecord(
                ico="49240901", provenance=provenance(), obchodni_firma="Raiffeisenbank a.s."
            ),
        )
        assert record.name == "Raiffeisenbank a.s."

    def test_codebook_version_is_stamped_without_mutating(self) -> None:
        record = SubjectRecord(ico="49240901", res=res())
        stamped = record.with_codebook_version("cb-0123456789abcdef")
        assert record.codebook_version is None
        assert stamped.codebook_version == "cb-0123456789abcdef"


class TestMerge:
    def test_fallback_fills_only_the_missing_half(self) -> None:
        primary = SubjectRecord(ico="49240901", res=res(source="DWS"), notes=("DWS: no OR row",))
        fallback = SubjectRecord(
            ico="49240901",
            res=res(source="ARES_LIVE"),
            or_record=OrRecord(
                ico="49240901", provenance=provenance("ARES_LIVE"), obchodni_firma="Raiffeisenbank"
            ),
        )
        merged = primary.merge(fallback)
        assert merged.res.provenance.source == "DWS"
        assert merged.or_record.provenance.source == "ARES_LIVE"
        assert merged.source_label == "DWS+ARES_LIVE"

    def test_notes_from_both_are_kept_once(self) -> None:
        primary = SubjectRecord(ico="49240901", res=res(), notes=("a",))
        other = SubjectRecord(ico="49240901", res=res(), notes=("a", "b"))
        assert primary.merge(other).notes == ("a", "b")

    def test_refuses_a_different_subject(self) -> None:
        with pytest.raises(ValueError, match="different subjects"):
            SubjectRecord(ico="49240901").merge(SubjectRecord(ico="00177041"))


def test_or_record_keeps_activity_order() -> None:
    record = OrRecord(
        ico="49240901",
        provenance=provenance(),
        predmet_podnikani=("bankovní obchody", "směnárenská činnost"),
        datum_zapisu=date(1993, 6, 25),
    )
    assert record.predmet_podnikani[0] == "bankovní obchody"
    assert record.datum_zapisu == date(1993, 6, 25)
