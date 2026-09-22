"""Smoke test against the real bank codebooks when they are present on this machine."""

from __future__ import annotations

import pytest

from config.settings import get_settings
from core.codebooks.loaders import load_and_check, load_ba0036_valid, load_cts_ba0036


def test_real_codebooks_are_consistent() -> None:
    settings = get_settings()
    paths = [
        settings.codebook_path(name)
        for name in (
            settings.codebook_cts_ba0036_file,
            settings.codebook_ba0036_valid_file,
            settings.codebook_cts_okec_nace2_file,
            settings.codebook_nace_stat_file,
        )
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        pytest.skip(f"real codebooks not available: {missing}")

    codebooks, report = load_and_check(settings, strict=False)
    assert report.ok, report.summary()
    assert codebooks.esa_leaves()
    assert codebooks.nace_divisions()
    assert codebooks.version.id.startswith("cb-")


def test_real_esa_pair_loads_and_agrees() -> None:
    """The ESA half alone, so this runs before the NACE codebooks arrive.

    Pins what the real files turned out to look like (verified 2026-09-22): the CTS export
    writes the header ``.ID``, and both files key on a 7-digit CNB code such as ``1221300``,
    not the 5-digit ESA form ``S.12213`` that the written spec implied.
    """
    settings = get_settings()
    cts_path = settings.codebook_path(settings.codebook_cts_ba0036_file)
    valid_path = settings.codebook_path(settings.codebook_ba0036_valid_file)
    if not (cts_path.is_file() and valid_path.is_file()):
        pytest.skip("the ESA codebooks are not available on this machine")

    cts = load_cts_ba0036(cts_path)
    valid = load_ba0036_valid(valid_path)

    assert not cts.malformed, cts.malformed[:3]
    assert not cts.duplicate_ids and not cts.duplicate_keys
    # Every emittable leaf must have a CTS ID, or cts_id_for_esa could never return it.
    assert not set(valid.by_key) - set(cts.by_key)
    assert all(len(key) == 7 and key.isdigit() for key in valid.by_key)
