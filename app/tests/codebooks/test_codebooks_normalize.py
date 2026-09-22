"""Tests of the pure normalization functions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from core.codebooks.errors import MalformedCodeError
from core.codebooks.normalize import (
    format_esa_code,
    is_nace_division,
    nace_to_division,
    normalize_cell,
    normalize_cts_id,
    normalize_esa_key,
    normalize_nace_division,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("  S.11001 ", "S.11001"),
        ("\N{NO-BREAK SPACE}Kód\N{NARROW NO-BREAK SPACE}", "Kód"),
        ("a\N{NO-BREAK SPACE}b", "a b"),
        ("\N{ZERO WIDTH NO-BREAK SPACE}ID", "ID"),
        ("line1\r\nline2", "line1\nline2"),
        (True, "TRUE"),
        (False, "FALSE"),
        (1001, "1001"),
        (0, "0"),
        (1001.0, "1001"),
        (1001.5, "1001.5"),
        (1e20, "100000000000000000000"),
        (float("nan"), "nan"),
        (Decimal("11001.0"), "11001"),
        (Decimal("1.25"), "1.25"),
        (Decimal("NaN"), "NaN"),
        (datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC), "2024-01-02T03:04:05+00:00"),
        (date(2024, 1, 2), "2024-01-02"),
        (b"raw", "b'raw'"),
    ],
)
def test_normalize_cell(value: object, expected: str) -> None:
    assert normalize_cell(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1001", "1001"), (1001, "1001"), (1001.0, "1001"), (" 00123 ", "00123"), ("AB-7", "AB-7")],
)
def test_normalize_cts_id_keeps_text(value: object, expected: str) -> None:
    assert normalize_cts_id(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "\N{NO-BREAK SPACE}"])
def test_normalize_cts_id_rejects_empty(value: object) -> None:
    with pytest.raises(MalformedCodeError):
        normalize_cts_id(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("S.11001", "11001"),
        ("s11001", "11001"),
        (11001, "11001"),
        (11001.0, "11001"),
        ("11001.0", "11001"),
        ("11001,0", "11001"),
        ("11001.000", "11001"),
        ("S.2", "2"),
        ("S.13", "13"),
        (" S. 12 201 ", "12201"),
        ("S.1\N{NO-BREAK SPACE}1", "11"),
        (Decimal("121"), "121"),
    ],
)
def test_normalize_esa_key(value: object, expected: str) -> None:
    assert normalize_esa_key(value) == expected


@pytest.mark.parametrize(
    "value",
    # 8 digits is over the limit; 6 and 7 are real - the CTS BA0036 codebook uses a 7-digit
    # CNB code (1221300) and trimmed forms such as 125011 occur in it.
    ["", None, "S.1A", "S.", "SS.11", "S.11001100", "S.1.5", "1.5", ".11001", "S..11", "S.11001.0"],
)
def test_normalize_esa_key_rejects(value: object) -> None:
    with pytest.raises(MalformedCodeError):
        normalize_esa_key(value)


@pytest.mark.parametrize(
    ("key", "expected"), [("11001", "S.11001"), ("2", "S.2"), ("S.121", "S.121"), (14, "S.14")]
)
def test_format_esa_code(key: object, expected: str) -> None:
    assert format_esa_code(key) == expected  # type: ignore[arg-type]


def test_format_esa_code_rejects_garbage() -> None:
    with pytest.raises(MalformedCodeError):
        format_esa_code("S.1A")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "01"),
        (1, "01"),
        ("62", "62"),
        ("1.0", "01"),
        ("1,0", "01"),
        ("62.00", "62"),
        (1.0, "01"),
        (62, "62"),
        (" 6 2 ", "62"),
    ],
)
def test_normalize_nace_division(value: object, expected: str) -> None:
    assert normalize_nace_division(value) == expected


@pytest.mark.parametrize("value", ["", None, "621", "A", "01.11", "6A", "00", "0", "62.1"])
def test_normalize_nace_division_rejects(value: object) -> None:
    with pytest.raises(MalformedCodeError):
        normalize_nace_division(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("62.01", "62"),
        ("6201", "62"),
        ("62", "62"),
        ("01.11", "01"),
        ("0111", "01"),
        ("62.01.1", "62"),
        ("1", "01"),
        (1, "01"),
        (1.0, "01"),
        (5, "05"),
        (62, "62"),
        (6201, "62"),
        (" 62 . 01 ", "62"),
        ("62A", "62"),
        ("1.11", "01"),
        ("62,01", "62"),
    ],
)
def test_nace_to_division(value: object, expected: str) -> None:
    assert nace_to_division(value) == expected


@pytest.mark.parametrize("value", ["J", "", None, "NACE 62", ".62", "00.1"])
def test_nace_to_division_rejects(value: object) -> None:
    with pytest.raises(MalformedCodeError):
        nace_to_division(value)


def test_nace_to_division_integer_ambiguity_is_documented() -> None:
    """An integer 111 (leading zero lost by Excel) is read as 11.1, never guessed as 01.11."""
    assert nace_to_division(111) == "11"
    assert nace_to_division("0111") == "01"
    doc = nace_to_division.__doc__ or ""
    assert "111" in doc
    assert "leading zero" in doc


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01", True),
        ("99", True),
        ("62", True),
        ("00", False),
        ("1", False),
        ("621", False),
        ("6a", False),
        ("٠١", False),
        ("", False),
    ],
)
def test_is_nace_division(value: str, expected: bool) -> None:
    assert is_nace_division(value) is expected


@pytest.mark.parametrize(
    "value",
    [
        datetime(2024, 11, 1),
        datetime(2024, 11, 1, tzinfo=UTC),
        date(2024, 11, 1),
        time(12, 30),
        "2024-11-01",
        "2024-11-01T00:00:00",
        "12:30",
    ],
)
def test_nace_to_division_rejects_temporal_values(value: object) -> None:
    with pytest.raises(MalformedCodeError):
        nace_to_division(value)
    with pytest.raises(MalformedCodeError):
        normalize_nace_division(value)


def test_nace_to_division_rejects_a_datetime_subclass() -> None:
    """``pandas.Timestamp`` is such a subclass; a spreadsheet library may hand one over."""

    class Timestamp(datetime):
        pass

    with pytest.raises(MalformedCodeError):
        nace_to_division(Timestamp(2024, 12, 10))
    with pytest.raises(MalformedCodeError):
        normalize_nace_division(Timestamp(2024, 12, 10))


def test_temporal_guard_is_documented() -> None:
    for func in (nace_to_division, normalize_nace_division):
        doc = func.__doc__ or ""
        assert "date" in doc and "Excel" in doc


numpy = pytest.importorskip("numpy")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (numpy.float64(62.01), "62.01"),
        (numpy.float64(1001.0), "1001"),
        (numpy.float32(62.5), "62.5"),
        (numpy.int64(62), "62"),
    ],
)
def test_normalize_cell_numpy_scalars(value: object, expected: str) -> None:
    assert normalize_cell(value) == expected


def test_nace_to_division_accepts_numpy_float() -> None:
    assert nace_to_division(numpy.float64(62.01)) == "62"
    assert normalize_esa_key(numpy.float64(11001.0)) == "11001"
