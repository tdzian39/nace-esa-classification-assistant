"""Tests for :mod:`core.identifiers.ico` (IČO normalization and mod-11 checksum)."""

from __future__ import annotations

import decimal
import pickle
from fractions import Fraction

import numpy as np
import pytest

from core.identifiers import (
    ICO_LENGTH,
    ICO_REASONS,
    IcoError,
    InvalidIcoError,
    ico_checksum_ok,
    is_valid_ico,
    normalize_ico,
    try_normalize_ico,
)


class _Opaque:
    """Stand-in for an arbitrary unsupported object with a run-independent repr."""

    def __repr__(self) -> str:
        return "<opaque object>"


#: Real, checksum-valid IČOs, each verified by hand against the mod-11 algorithm.
VALID_ICOS: dict[str, str] = {
    "49240901": "Raiffeisenbank a.s. (remainder 0 -> check digit 1)",
    "45274649": "ČEZ",
    "00001350": "ČSOB (remainder 1 -> check digit 0)",
    "45317054": "Komerční banka",
    "45244782": "Česká spořitelna",
    "00006947": "Ministerstvo financí",
    "47114983": "Česká pošta",
}

#: ``(input, expected)`` pairs exercising the messy-input policy.
MESSY_INPUTS: list[tuple[object, str]] = [
    (1350, "00001350"),
    (1350.0, "00001350"),
    ("1350.0", "00001350"),
    ("1350.00", "00001350"),
    ("1350", "00001350"),
    ("00001350", "00001350"),
    (" 492 409 01 ", "49240901"),
    ("49240901\u00a0", "49240901"),
    ("\u202f49240901", "49240901"),
    ("4924\t0901", "49240901"),
    ("49240901\r\n", "49240901"),
    ("\ufeff49240901", "49240901"),
    ("4924\u200b0901", "49240901"),
    ("4.9240901E7", "49240901"),
    ("4.9240901e+07", "49240901"),
    ("49240901.0", "49240901"),
    ("49240901E0", "49240901"),
    (np.int64(49240901), "49240901"),
    (np.int64(1350), "00001350"),
    (np.float64(1350.0), "00001350"),
    (np.str_("49240901"), "49240901"),
    (decimal.Decimal("49240901"), "49240901"),
    (decimal.Decimal("1350.0"), "00001350"),
    (decimal.Decimal("4.9240901E7"), "49240901"),
    (Fraction(1350), "00001350"),
]

#: ``(input, reason)`` pairs; together they must cover every code in ``ICO_REASONS``.
REASON_CASES: list[tuple[object, str]] = [
    # empty
    (None, "empty"),
    ("", "empty"),
    ("   ", "empty"),
    ("\u00a0\t", "empty"),
    ("\r\n", "empty"),
    ("\ufeff", "empty"),
    # unsupported_type
    (True, "unsupported_type"),
    (False, "unsupported_type"),
    (np.bool_(True), "unsupported_type"),
    (b"49240901", "unsupported_type"),
    (_Opaque(), "unsupported_type"),
    ([], "unsupported_type"),
    (1 + 2j, "unsupported_type"),
    # nan
    (float("nan"), "nan"),
    (float("inf"), "nan"),
    (float("-inf"), "nan"),
    (np.float64("nan"), "nan"),
    (np.float64("inf"), "nan"),
    (decimal.Decimal("NaN"), "nan"),
    (decimal.Decimal("sNaN"), "nan"),
    (decimal.Decimal("Infinity"), "nan"),
    # not_digits
    ("12a45678", "not_digits"),
    ("49-240-901", "not_digits"),
    ("1350.5", "not_digits"),
    ("1350.", "not_digits"),
    ("+1350", "not_digits"),
    ("-1350", "not_digits"),
    ("1E-5", "not_digits"),
    ("1E-10000000000000000000000000", "not_digits"),
    ("１３５０", "not_digits"),
    ("CZ", "not_digits"),
    ("CZX1", "not_digits"),
    (1350.5, "not_digits"),
    (-1350, "not_digits"),
    (-1350.0, "not_digits"),
    (Fraction(3, 2), "not_digits"),
    (decimal.Decimal("-1350"), "not_digits"),
    (decimal.Decimal("1350.5"), "not_digits"),
    # too_long
    ("123456789", "too_long"),
    ("049240901", "too_long"),
    ("0000000049240901", "too_long"),
    ("1.23456789E8", "too_long"),
    ("1E+999999999", "too_long"),
    ("1E+10000000000000000000000000", "too_long"),
    (123456789, "too_long"),
    (10**5000, "too_long"),
    (123456789.0, "too_long"),
    (1e300, "too_long"),
    (decimal.Decimal("1E+999999999"), "too_long"),
    # checksum
    ("49240902", "checksum"),
    (49240902, "checksum"),
    ("00000000", "checksum"),
    # looks_like_dic
    ("CZ49240901", "looks_like_dic"),
    ("cz49240901", "looks_like_dic"),
    (" CZ 492 409 01 ", "looks_like_dic"),
]


def _safe_repr(value: object) -> str:
    """Test id helper: ``repr`` bounded in length; huge ints exceed the int->str limit."""
    try:
        return repr(value)[:50]
    except ValueError:
        return f"<{type(value).__name__}>"


def _reason_ids(cases: list[tuple[object, str]]) -> list[str]:
    return [f"{reason}-{_safe_repr(value)}" for value, reason in cases]


@pytest.mark.parametrize("ico", list(VALID_ICOS), ids=list(VALID_ICOS.values()))
def test_valid_ico_round_trips(ico: str) -> None:
    assert len(ico) == ICO_LENGTH
    assert normalize_ico(ico) == ico
    assert try_normalize_ico(ico) == ico
    assert is_valid_ico(ico) is True
    assert ico_checksum_ok(ico) is True


@pytest.mark.parametrize("ico", list(VALID_ICOS), ids=list(VALID_ICOS.values()))
def test_valid_ico_survives_numeric_storage(ico: str) -> None:
    """Excel stores IČOs as numbers and drops leading zeros; they must come back padded."""
    assert normalize_ico(int(ico)) == ico
    assert normalize_ico(float(ico)) == ico
    assert normalize_ico(str(int(ico))) == ico
    assert normalize_ico(f"{int(ico)}.0") == ico


@pytest.mark.parametrize(
    ("value", "expected"), MESSY_INPUTS, ids=[repr(v) for v, _ in MESSY_INPUTS]
)
def test_normalize_messy_input(value: object, expected: str) -> None:
    assert normalize_ico(value) == expected
    assert try_normalize_ico(value) == expected
    assert is_valid_ico(value) is True


@pytest.mark.parametrize(
    "value",
    ["49240901.0000000000000000001", "4.92409010000000000001E7", "1350.000000000000000000001"],
)
def test_decimal_strings_are_converted_exactly_not_via_float(value: str) -> None:
    """float() would round these to an integer and accept them; Decimal must refuse them."""
    assert float(value).is_integer()  # documents why the case is discriminating
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    assert info.value.reason == "not_digits"
    assert try_normalize_ico(value, check=False) is None


@pytest.mark.parametrize(("value", "reason"), REASON_CASES, ids=_reason_ids(REASON_CASES))
def test_invalid_input_reason(value: object, reason: str) -> None:
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    assert info.value.reason == reason
    assert info.value.value is value
    assert reason in str(info.value)
    assert try_normalize_ico(value) is None
    assert is_valid_ico(value) is False


def test_reason_cases_cover_every_reason_code() -> None:
    assert {reason for _, reason in REASON_CASES} == ICO_REASONS
    assert {
        "empty",
        "nan",
        "not_digits",
        "too_long",
        "checksum",
        "looks_like_dic",
        "unsupported_type",
    } == ICO_REASONS


@pytest.mark.parametrize("value", ["CZ49240901", "cz49240901", " CZ 492 409 01 ", "Cz49240901"])
def test_dic_prefix(value: object) -> None:
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    assert info.value.reason == "looks_like_dic"
    assert normalize_ico(value, allow_dic_prefix=True) == "49240901"
    assert try_normalize_ico(value, allow_dic_prefix=True) == "49240901"


def test_dic_prefix_with_wrong_checksum_still_fails_checksum() -> None:
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("CZ49240902", allow_dic_prefix=True)
    assert info.value.reason == "checksum"


def test_dic_prefix_with_personal_vat_id_is_too_long() -> None:
    """A DIČ of a natural person carries a 9- or 10-digit birth number, never an IČO."""
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("CZ8501011234", allow_dic_prefix=True)
    assert info.value.reason == "too_long"


@pytest.mark.parametrize("value", ["049240901", "0000000049240901", "00000000000000001350"])
def test_surplus_leading_zeros_are_too_long(value: str) -> None:
    """The digit count after cleaning is what the length rule sees; zeros are not dropped first."""
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    assert info.value.reason == "too_long"
    assert try_normalize_ico(value, check=False) is None


def test_exactly_eight_digits_with_leading_zeros_still_normalize() -> None:
    assert normalize_ico("00001350") == "00001350"
    assert normalize_ico("CZ00001350", allow_dic_prefix=True) == "00001350"
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("CZ000001350", allow_dic_prefix=True)
    assert info.value.reason == "too_long"


def test_check_false_skips_checksum_only() -> None:
    assert normalize_ico("49240902", check=False) == "49240902"
    assert normalize_ico(1351, check=False) == "00001351"
    assert try_normalize_ico(" 4924 0902 ", check=False) == "49240902"
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("12a45678", check=False)
    assert info.value.reason == "not_digits"
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("123456789", check=False)
    assert info.value.reason == "too_long"


def test_check_false_keeps_dic_detection() -> None:
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("CZ49240901", check=False)
    assert info.value.reason == "looks_like_dic"
    assert normalize_ico("CZ49240902", check=False, allow_dic_prefix=True) == "49240902"


def test_allow_dic_prefix_is_noop_without_prefix() -> None:
    assert normalize_ico("49240901", allow_dic_prefix=True) == "49240901"
    assert normalize_ico(" 492 409 01 ", allow_dic_prefix=True) == "49240901"
    assert normalize_ico(1350, allow_dic_prefix=True) == "00001350"
    assert normalize_ico("00001350", allow_dic_prefix=True) == "00001350"


@pytest.mark.parametrize(
    "value",
    ["4924090", "4924090a", "", "49240902", "492409010", " 49240901", "49240901\n", 49240901, None],
    ids=repr,
)
def test_checksum_rejects_non_canonical_or_wrong_input(value: object) -> None:
    assert ico_checksum_ok(value) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["４９２４０９０１", "٤٩٢٤٠٩٠١", "4924090¹"],
    ids=["fullwidth", "arabic-indic", "superscript"],
)
def test_checksum_rejects_non_ascii_digits(value: str) -> None:
    """str.isdigit()/int() accept these; the contract demands ASCII 0-9 only."""
    assert value.isdigit()
    assert ico_checksum_ok(value) is False
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    assert info.value.reason == "not_digits"


def test_checksum_formula_matches_documented_algorithm() -> None:
    """Every 7-digit prefix has exactly one valid check digit and it follows (11 - r) mod 10."""
    weights = (8, 7, 6, 5, 4, 3, 2)
    for prefix_number in range(0, 10**7, 123_457):
        prefix = f"{prefix_number:07d}"
        remainder = sum(w * int(d) for w, d in zip(weights, prefix, strict=True)) % 11
        expected = (11 - remainder) % 10
        accepted = [d for d in "0123456789" if ico_checksum_ok(prefix + d)]
        assert accepted == [str(expected)], prefix


def test_error_hierarchy_and_attributes() -> None:
    with pytest.raises(IcoError) as info:
        normalize_ico("49240902")
    exc = info.value
    assert isinstance(exc, InvalidIcoError)
    assert isinstance(exc, ValueError)
    assert exc.reason == "checksum"
    assert exc.value == "49240902"
    assert str(exc) == "invalid IČO '49240902': checksum"


def test_error_message_bounds_long_values() -> None:
    value = "9" * 500
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(value)
    message = str(info.value)
    assert len(message) < 200
    assert message.endswith(": too_long")
    assert info.value.value is value


def test_error_message_survives_broken_repr() -> None:
    class Broken:
        def __repr__(self) -> str:
            raise RuntimeError("no repr for you")

    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(Broken())
    assert info.value.reason == "unsupported_type"
    assert "unrepresentable Broken" in str(info.value)


def test_error_rejects_unknown_reason_code() -> None:
    with pytest.raises(ValueError, match="unknown IČO reason code"):
        InvalidIcoError("bogus", "x")  # type: ignore[arg-type]


def test_error_is_picklable() -> None:
    original = InvalidIcoError("too_long", "123456789")
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, InvalidIcoError)
    assert restored.reason == "too_long"
    assert restored.value == "123456789"
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
    10**5000,
    "1E+999999999",
]


@pytest.mark.parametrize("value", SAFE_HELPER_INPUTS, ids=_safe_repr)
def test_safe_helpers_never_raise(value: object) -> None:
    assert try_normalize_ico(value) is None
    assert try_normalize_ico(value, check=False, allow_dic_prefix=True) is None
    assert is_valid_ico(value) is False


def test_parametrize_ids_are_run_independent() -> None:
    """Node ids must not embed memory addresses (breaks --lf, -k and xdist)."""
    for value, _ in REASON_CASES:
        assert " at 0x" not in _safe_repr(value), value
    for value, _ in MESSY_INPUTS:
        assert " at 0x" not in repr(value), value
    for value in SAFE_HELPER_INPUTS:
        assert " at 0x" not in _safe_repr(value), value


def test_large_exponent_strings_are_rejected_quickly() -> None:
    """Absurd exponents must be classified without materialising the integer."""
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico("1E+999999999")
    assert info.value.reason == "too_long"
    with pytest.raises(InvalidIcoError) as info:
        normalize_ico(decimal.Decimal("1E+999999999"))
    assert info.value.reason == "too_long"
