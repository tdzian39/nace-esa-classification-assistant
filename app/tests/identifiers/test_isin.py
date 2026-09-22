"""Tests for :mod:`core.identifiers.isin` (ISIN normalization, format and Luhn checksum)."""

from __future__ import annotations

import decimal
import pickle

import numpy as np
import pytest

from core.identifiers import (
    ISIN_LENGTH,
    ISIN_PATTERN,
    ISIN_REASONS,
    InvalidIsinError,
    IsinError,
    is_isin_format,
    is_valid_isin,
    isin_checksum_ok,
    isin_country_code,
    normalize_isin,
    try_normalize_isin,
)


class _Opaque:
    """Stand-in for an arbitrary unsupported object with a run-independent repr."""

    def __repr__(self) -> str:
        return "<opaque object>"


#: Documented, checksum-valid ISINs, each verified by hand against the Luhn algorithm.
VALID_ISINS: dict[str, str] = {
    "US0378331005": "Apple",
    "US5949181045": "Microsoft",
    "DE0007164600": "SAP",
    "GB0002634946": "BAE Systems",
    "CZ0008019106": "ČEZ",
    "AU0000XVGZA3": "Treasury Corp Victoria (letters inside the NSIN)",
    "US38259P5089": "Google (letter in the middle of the NSIN)",
}

#: ``(input, expected)`` pairs exercising the messy-input policy.
MESSY_INPUTS: list[tuple[object, str]] = [
    ("us0378331005", "US0378331005"),
    (" US 0378 3310 05 ", "US0378331005"),
    ("US0378331005\u00a0", "US0378331005"),
    ("\u202fUS0378331005", "US0378331005"),
    ("US0378\t331005", "US0378331005"),
    ("US0378331005\r\n", "US0378331005"),
    ("\ufeffUS0378331005", "US0378331005"),
    ("US0378\u200b331005", "US0378331005"),
    ("au0000xvgza3", "AU0000XVGZA3"),
    ("us38259p5089", "US38259P5089"),
    (np.str_("CZ0008019106"), "CZ0008019106"),
]

#: ``(input, reason)`` pairs; together they must cover every code in ``ISIN_REASONS``.
REASON_CASES: list[tuple[object, str]] = [
    # empty
    (None, "empty"),
    ("", "empty"),
    ("  \t", "empty"),
    ("\u00a0", "empty"),
    ("\r\n", "empty"),
    # unsupported_type
    (12345, "unsupported_type"),
    (12345.0, "unsupported_type"),
    (float("nan"), "unsupported_type"),
    (np.float64("nan"), "unsupported_type"),
    (True, "unsupported_type"),
    (b"US0378331005", "unsupported_type"),
    (_Opaque(), "unsupported_type"),
    ([], "unsupported_type"),
    # format
    ("US037833100", "format"),
    ("US03783310055", "format"),
    ("US-0378331005", "format"),
    ("U10378331005", "format"),
    ("US037833100A", "format"),
    ("us037833100a", "format"),
    ("0S0378331005", "format"),
    ("US0378331005X", "format"),
    ("ＵＳ0378331005", "format"),
    ("uſ0378331005", "format"),
    ("US 0378331005 US", "format"),
    # checksum
    ("US0378331006", "checksum"),
    ("AU0000XVGZB3", "checksum"),
    ("US38259P5088", "checksum"),
    ("CZ0008019107", "checksum"),
    ("us0378331006", "checksum"),
]


def _reason_ids(cases: list[tuple[object, str]]) -> list[str]:
    return [f"{reason}-{value!r}"[:60] for value, reason in cases]


@pytest.mark.parametrize("isin", list(VALID_ISINS), ids=list(VALID_ISINS.values()))
def test_valid_isin_round_trips(isin: str) -> None:
    assert len(isin) == ISIN_LENGTH
    assert ISIN_PATTERN.fullmatch(isin) is not None
    assert normalize_isin(isin) == isin
    assert try_normalize_isin(isin) == isin
    assert is_valid_isin(isin) is True
    assert is_isin_format(isin) is True
    assert isin_checksum_ok(isin) is True
    assert isin_country_code(isin) == isin[:2]


@pytest.mark.parametrize("isin", list(VALID_ISINS), ids=list(VALID_ISINS.values()))
def test_exactly_one_check_digit_is_valid(isin: str) -> None:
    """The Luhn check digit is unique; brute force must recover the documented one."""
    body = isin[:-1]
    accepted = [d for d in "0123456789" if isin_checksum_ok(body + d)]
    assert accepted == [isin[-1]]


@pytest.mark.parametrize(
    ("value", "expected"), MESSY_INPUTS, ids=[repr(v) for v, _ in MESSY_INPUTS]
)
def test_normalize_messy_input(value: object, expected: str) -> None:
    assert normalize_isin(value) == expected
    assert try_normalize_isin(value) == expected
    assert is_valid_isin(value) is True
    assert is_isin_format(value) is True


@pytest.mark.parametrize(("value", "reason"), REASON_CASES, ids=_reason_ids(REASON_CASES))
def test_invalid_input_reason(value: object, reason: str) -> None:
    with pytest.raises(InvalidIsinError) as info:
        normalize_isin(value)
    assert info.value.reason == reason
    assert info.value.value is value
    assert reason in str(info.value)
    assert try_normalize_isin(value) is None
    assert is_valid_isin(value) is False


def test_reason_cases_cover_every_reason_code() -> None:
    assert {reason for _, reason in REASON_CASES} == ISIN_REASONS
    assert {"empty", "format", "checksum", "unsupported_type"} == ISIN_REASONS


def test_check_false_skips_luhn_only() -> None:
    assert normalize_isin("US0378331006", check=False) == "US0378331006"
    assert normalize_isin(" us0378331006 ", check=False) == "US0378331006"
    assert try_normalize_isin("AU0000XVGZB3", check=False) == "AU0000XVGZB3"
    with pytest.raises(InvalidIsinError) as info:
        normalize_isin("US-0378331005", check=False)
    assert info.value.reason == "format"
    with pytest.raises(InvalidIsinError) as info:
        normalize_isin(None, check=False)
    assert info.value.reason == "empty"


#: ``(input, expected)`` pairs for the format-only predicate; the checksum must not matter.
FORMAT_ONLY_CASES: list[tuple[object, bool]] = [
    ("US0378331006", True),
    (" us0378331005 ", True),
    ("US037833100", False),
    ("US-0378331005", False),
    (None, False),
    (12345, False),
    (_Opaque(), False),
    ([], False),
    (float("nan"), False),
    (b"US0378331005", False),
]


@pytest.mark.parametrize(("value", "expected"), FORMAT_ONLY_CASES, ids=repr)
def test_is_isin_format_ignores_checksum_and_never_raises(value: object, expected: bool) -> None:
    assert is_isin_format(value) is expected


@pytest.mark.parametrize(
    "value",
    ["US0378331006", "us0378331005", " US0378331005", "US037833100", "US-0378331005", "", 12, None],
    ids=repr,
)
def test_checksum_rejects_non_canonical_or_wrong_input(value: object) -> None:
    assert isin_checksum_ok(value) is False  # type: ignore[arg-type]


def test_country_code() -> None:
    assert isin_country_code("US0378331005") == "US"
    assert isin_country_code(" cz0008019106 ") == "CZ"
    assert isin_country_code("au0000xvgza3") == "AU"
    with pytest.raises(InvalidIsinError) as info:
        isin_country_code("US0378331006")
    assert info.value.reason == "checksum"
    with pytest.raises(InvalidIsinError) as info:
        isin_country_code(None)  # type: ignore[arg-type]
    assert info.value.reason == "empty"


def test_error_hierarchy_and_attributes() -> None:
    with pytest.raises(IsinError) as info:
        normalize_isin("US0378331006")
    exc = info.value
    assert isinstance(exc, InvalidIsinError)
    assert isinstance(exc, ValueError)
    assert exc.reason == "checksum"
    assert exc.value == "US0378331006"
    assert str(exc) == "invalid ISIN 'US0378331006': checksum"


def test_error_message_bounds_long_values() -> None:
    value = "X" * 500
    with pytest.raises(InvalidIsinError) as info:
        normalize_isin(value)
    message = str(info.value)
    assert len(message) < 200
    assert message.endswith(": format")
    assert info.value.value is value


def test_error_message_survives_broken_repr() -> None:
    class Broken:
        def __repr__(self) -> str:
            raise RuntimeError("no repr for you")

    with pytest.raises(InvalidIsinError) as info:
        normalize_isin(Broken())
    assert info.value.reason == "unsupported_type"
    assert "unrepresentable Broken" in str(info.value)


def test_error_rejects_unknown_reason_code() -> None:
    with pytest.raises(ValueError, match="unknown ISIN reason code"):
        InvalidIsinError("bogus", "x")  # type: ignore[arg-type]


def test_error_is_picklable() -> None:
    original = InvalidIsinError("format", "US-0378331005")
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, InvalidIsinError)
    assert restored.reason == "format"
    assert restored.value == "US-0378331005"
    assert str(restored) == str(original)


#: Inputs the never-raising helpers must swallow, whatever the underlying reason.
SAFE_HELPER_INPUTS: list[object] = [
    None,
    _Opaque(),
    [],
    {},
    b"1",
    float("nan"),
    True,
    1 + 2j,
    "abc",
    12345,
]


@pytest.mark.parametrize("value", SAFE_HELPER_INPUTS, ids=repr)
def test_safe_helpers_never_raise(value: object) -> None:
    assert try_normalize_isin(value) is None
    assert try_normalize_isin(value, check=False) is None
    assert is_valid_isin(value) is False
    assert is_isin_format(value) is False


@pytest.mark.parametrize(
    "value",
    [float("nan"), np.float64("nan"), np.float32("nan"), decimal.Decimal("NaN"), 12345.0],
    ids=["float-nan", "np.float64-nan", "np.float32-nan", "Decimal-NaN", "float"],
)
def test_non_string_numbers_are_unsupported_type_even_when_nan(value: object) -> None:
    """Contract: only None/blank strings are 'empty'; every non-str is 'unsupported_type'."""
    with pytest.raises(InvalidIsinError) as info:
        normalize_isin(value)
    assert info.value.reason == "unsupported_type"
    assert is_isin_format(value) is False
    assert try_normalize_isin(value) is None


def test_parametrize_ids_are_run_independent() -> None:
    """Node ids must not embed memory addresses (breaks --lf, -k and xdist)."""
    for value, _ in REASON_CASES:
        assert " at 0x" not in repr(value)[:60], value
    for value, _ in MESSY_INPUTS:
        assert " at 0x" not in repr(value)[:60], value
    for value, _ in FORMAT_ONLY_CASES:
        assert " at 0x" not in repr(value)[:60], value
    for value in SAFE_HELPER_INPUTS:
        assert " at 0x" not in repr(value)[:60], value
