"""What every source shares: the Source stamp, provenance timestamps and the error family."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import get_args

import pytest

from core.sources.base import (
    Provenance,
    Source,
    SourceError,
    SourceQueryError,
    SourceResponseError,
    SourceUnavailableError,
)

RETRIEVED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SNAPSHOT = datetime(2026, 4, 7, 8, 5, 10, tzinfo=UTC)


class TestProvenance:
    def test_timestamp_prefers_the_snapshot(self) -> None:
        """The source's own update time says more about freshness than when we asked."""
        provenance = Provenance(source="GLEIF", retrieved_at=RETRIEVED, snapshot_at=SNAPSHOT)
        assert provenance.timestamp == SNAPSHOT

    def test_timestamp_falls_back_to_retrieval(self) -> None:
        provenance = Provenance(source="OPENFIGI", retrieved_at=RETRIEVED)
        assert provenance.timestamp == RETRIEVED

    def test_snapshot_and_detail_are_optional(self) -> None:
        provenance = Provenance(source="WEB", retrieved_at=RETRIEVED)
        assert provenance.snapshot_at is None
        assert provenance.detail is None

    def test_a_provenance_cannot_be_rewritten_after_the_fact(self) -> None:
        provenance = Provenance(source="WEB", retrieved_at=RETRIEVED, detail="user-supplied")
        with pytest.raises(FrozenInstanceError):
            provenance.source = "GLEIF"  # type: ignore[misc]


def test_the_source_stamp_names_only_what_tool_1_consults() -> None:
    """A row stamped with a source this tool never asks would be an unattributable row."""
    assert set(get_args(Source)) == {"WEB", "GLEIF", "OPENFIGI"}


@pytest.mark.parametrize(
    "error",
    [SourceUnavailableError, SourceResponseError, SourceQueryError],
    ids=lambda e: e.__name__,
)
def test_every_source_error_is_caught_as_a_source_error(error: type[SourceError]) -> None:
    """The identity step catches ``SourceError``; a failure outside the family would crash it."""
    with pytest.raises(SourceError):
        raise error("register is down")


def test_unavailable_and_unreadable_stay_distinguishable() -> None:
    """A register that could not be asked and one that answered nonsense are different notes."""
    assert not issubclass(SourceUnavailableError, SourceResponseError)
    assert not issubclass(SourceResponseError, SourceUnavailableError)
