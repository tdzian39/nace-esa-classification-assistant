"""Czech company identifier (IČO): normalization and mod-11 checksum validation.

An IČO ("identifikační číslo osoby") is exactly :data:`ICO_LENGTH` (8) decimal digits,
leading zeros included. The last digit is a check digit computed from the first seven:

    weights 8, 7, 6, 5, 4, 3, 2 are applied to digits 1..7,
    r = weighted sum mod 11,
    expected check digit = (11 - r) mod 10   (r == 0 -> 1, r == 1 -> 0, else 11 - r).

Messy-input policy
------------------
The public functions accept whatever an openpyxl or dataframe column may deliver and turn it
into a canonical 8-digit string or a precise :class:`InvalidIcoError`:

* ``None``, empty and whitespace-only strings are ``"empty"``.
* ``bool`` is ``"unsupported_type"`` even though ``bool`` is an ``int`` subclass.
* Integers (``int`` and any :class:`numbers.Integral`, e.g. numpy integer scalars)
  become their decimal digits; negative numbers are ``"not_digits"``.
* Real numbers (``float``, numpy floats, ...) and :class:`decimal.Decimal` are accepted
  only when integral (``1350.0`` -> ``"00001350"``); NaN and infinities are ``"nan"``,
  fractional values are ``"not_digits"``. A 64-bit float represents every 8-digit
  integer exactly, so nothing is lost on that path; a narrower float (numpy ``float32``)
  may already have rounded the value upstream, which no normalization can undo.
* Strings lose every whitespace character anywhere in the string (spaces, tabs,
  CR/LF, NBSP U+00A0, narrow NBSP U+202F, ...) plus the zero-width and BOM characters
  that Excel/CSV round-trips leave behind. Excel float artefacts (``"1350.0"``) and
  scientific notation (``"4.9240901E7"``) are converted exactly with :mod:`decimal`,
  never through ``float``. A value starting with ``CZ`` followed by digits is a DIČ
  (VAT id): it is ``"looks_like_dic"`` unless ``allow_dic_prefix=True`` strips it.
  Only ASCII digits ``0-9`` count as digits; anything else is ``"not_digits"``.
* Fewer than eight digits are zero-padded on the left, which recovers IČOs stored as
  numbers that lost their leading zeros. More than eight digits, leading zeros included,
  is ``"too_long"``.
* Any other type is ``"unsupported_type"``.

Everything in this module is pure: no I/O, no logging, no global state.
"""

from __future__ import annotations

import decimal
import numbers
import re
from typing import Final, Literal, get_args

__all__ = [
    "ICO_LENGTH",
    "ICO_REASONS",
    "IcoError",
    "IcoReason",
    "InvalidIcoError",
    "ico_checksum_ok",
    "is_valid_ico",
    "normalize_ico",
    "try_normalize_ico",
]

#: Canonical length of an IČO in digits, leading zeros included.
ICO_LENGTH: Final[int] = 8

#: Machine-readable reason codes carried by :class:`InvalidIcoError`.
IcoReason = Literal[
    "empty",
    "nan",
    "not_digits",
    "too_long",
    "checksum",
    "looks_like_dic",
    "unsupported_type",
]

#: The fixed set of reason codes, for validation and for callers that map them to text.
ICO_REASONS: Final[frozenset[str]] = frozenset(get_args(IcoReason))

#: Mod-11 weights applied to digits 1..7 (the eighth digit is the check digit).
_CHECKSUM_WEIGHTS: Final[tuple[int, ...]] = (8, 7, 6, 5, 4, 3, 2)

#: Unicode whitespace (``\\s`` covers NBSP, narrow NBSP, tabs, CR/LF, ...) plus the
#: zero-width characters and BOM that survive Excel/CSV round-trips.
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")
_ASCII_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]+")
_ICO_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
#: Non-negative decimal literal as spreadsheets render numbers: ``1350.0``, ``4.9240901E7``.
_DECIMAL_LIKE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
#: DIČ (Czech VAT id) prefix: ``CZ`` directly followed by a digit, any letter case.
_DIC_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"CZ(?=[0-9])", re.IGNORECASE)
#: Longest ``repr`` of the offending value embedded in error messages.
_REPR_LIMIT: Final[int] = 80


class IcoError(ValueError):
    """Base class of every IČO error raised by this module."""


class InvalidIcoError(IcoError):
    """The input cannot be interpreted as a valid IČO.

    Attributes:
        reason: One of :data:`ICO_REASONS`; stable, intended for programmatic handling.
        value: The original, unmodified input. Kept for reporting; embed it with ``repr``.
    """

    reason: IcoReason
    value: object

    def __init__(self, reason: IcoReason, value: object = None) -> None:
        if reason not in ICO_REASONS:
            raise ValueError(
                f"unknown IČO reason code {reason!r}; expected one of {sorted(ICO_REASONS)}"
            )
        self.reason = reason
        self.value = value
        super().__init__(f"invalid IČO {_short_repr(value)}: {reason}")

    def __reduce__(self) -> tuple[type[InvalidIcoError], tuple[IcoReason, object]]:
        """Keep the exception picklable despite the custom constructor signature."""
        return type(self), (self.reason, self.value)


def normalize_ico(value: object, *, check: bool = True, allow_dic_prefix: bool = False) -> str:
    """Return the canonical 8-digit IČO for a messy cell value.

    Args:
        value: Anything an xlsx or dataframe column may contain; see the module docstring
            for the accepted shapes.
        check: Validate the mod-11 check digit (default). With ``check=False`` the
            padded digit string is returned even when the checksum fails.
        allow_dic_prefix: Accept a DIČ (``CZ`` + digits) by stripping the prefix instead
            of raising ``"looks_like_dic"``.

    Raises:
        InvalidIcoError: With a ``reason`` from :data:`ICO_REASONS` describing the problem.
    """
    if value is None:
        raise InvalidIcoError("empty", value)
    if isinstance(value, bool):
        raise InvalidIcoError("unsupported_type", value)
    if isinstance(value, str):
        digits = _digits_from_str(value, allow_dic_prefix=allow_dic_prefix)
    elif isinstance(value, numbers.Integral):
        digits = _digits_from_int(int(value), value)
    elif isinstance(value, decimal.Decimal):
        digits = _digits_from_decimal(value, value)
    elif isinstance(value, numbers.Real):
        digits = _digits_from_real(value)
    else:
        raise InvalidIcoError("unsupported_type", value)

    if len(digits) > ICO_LENGTH:
        raise InvalidIcoError("too_long", value)
    ico = digits.zfill(ICO_LENGTH)
    if check and not ico_checksum_ok(ico):
        raise InvalidIcoError("checksum", value)
    return ico


def ico_checksum_ok(ico: str) -> bool:
    """Return ``True`` when ``ico`` is exactly 8 ASCII digits with a correct check digit.

    Anything else (wrong length, non-digits, non-string) is ``False``; nothing is raised.
    """
    if not isinstance(ico, str) or _ICO_DIGITS_RE.fullmatch(ico) is None:
        return False
    digits = [ord(ch) - ord("0") for ch in ico]
    weighted = zip(_CHECKSUM_WEIGHTS, digits[:-1], strict=True)
    remainder = sum(weight * digit for weight, digit in weighted) % 11
    expected = (11 - remainder) % 10
    return expected == digits[-1]


def is_valid_ico(value: object) -> bool:
    """Return ``True`` when :func:`normalize_ico` accepts ``value`` (checksum included). Never raises."""
    return try_normalize_ico(value) is not None


def try_normalize_ico(
    value: object, *, check: bool = True, allow_dic_prefix: bool = False
) -> str | None:
    """Like :func:`normalize_ico` but return ``None`` instead of raising. Never raises."""
    try:
        return normalize_ico(value, check=check, allow_dic_prefix=allow_dic_prefix)
    except IcoError:
        return None


def _digits_from_str(text: str, *, allow_dic_prefix: bool) -> str:
    """Digit string from a cell text: whitespace, DIČ prefix and Excel number formats handled."""
    cleaned = _WHITESPACE_RE.sub("", text)
    if not cleaned:
        raise InvalidIcoError("empty", text)
    if _DIC_PREFIX_RE.match(cleaned) is not None:
        if not allow_dic_prefix:
            raise InvalidIcoError("looks_like_dic", text)
        cleaned = cleaned[2:]
    if _ASCII_DIGITS_RE.fullmatch(cleaned) is not None:
        return cleaned
    if _DECIMAL_LIKE_RE.fullmatch(cleaned) is not None:
        try:
            number = decimal.Decimal(cleaned)
        except decimal.InvalidOperation:
            # The exponent exceeds what the decimal module can represent at all; the only
            # place a "-" can occur is the exponent sign, so a negative exponent denotes a
            # minuscule fraction and a positive one an enormous integer.
            reason: IcoReason = "not_digits" if "-" in cleaned else "too_long"
            raise InvalidIcoError(reason, text) from None
        return _digits_from_decimal(number, text)
    raise InvalidIcoError("not_digits", text)


def _digits_from_int(number: int, value: object) -> str:
    """Digit string of a non-negative integer; the size guard keeps ``str()`` cheap."""
    if number < 0:
        raise InvalidIcoError("not_digits", value)
    if number >= 10**ICO_LENGTH:
        raise InvalidIcoError("too_long", value)
    return str(number)


def _digits_from_real(value: numbers.Real) -> str:
    """Digit string of an integral real number (``1350.0`` -> ``"1350"``).

    Truncating with ``int()`` and comparing back is exact for every finite float, so no
    precision is lost on the way; NaN and infinities cannot be truncated and are ``"nan"``.
    """
    try:
        number = int(value)
    except (OverflowError, ValueError):  # infinity / NaN
        raise InvalidIcoError("nan", value) from None
    except TypeError:  # a Real without an integer conversion
        raise InvalidIcoError("unsupported_type", value) from None
    if number != value:
        raise InvalidIcoError("not_digits", value)
    return _digits_from_int(number, value)


def _digits_from_decimal(number: decimal.Decimal, value: object) -> str:
    """Digit string of an integral :class:`decimal.Decimal`, without materialising huge values."""
    if number.is_nan() or number.is_infinite():
        raise InvalidIcoError("nan", value)
    if number < 0:
        raise InvalidIcoError("not_digits", value)
    if number != number.to_integral_value():
        raise InvalidIcoError("not_digits", value)
    # ``adjusted()`` is the exponent of the most significant digit, i.e. the number of
    # integer digits minus one: checking it first avoids expanding ``1E+999999999``.
    if number and number.adjusted() >= ICO_LENGTH:
        raise InvalidIcoError("too_long", value)
    return str(int(number))


def _short_repr(value: object) -> str:
    """``repr`` of a value bounded in length and guaranteed not to raise."""
    try:
        text = repr(value)
    except Exception:  # a broken __repr__ (or a huge int) must not mask the real error
        text = f"<unrepresentable {type(value).__name__}>"
    if len(text) > _REPR_LIMIT:
        text = text[: _REPR_LIMIT - 3] + "..."
    return text
