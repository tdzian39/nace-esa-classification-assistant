"""ISIN (ISO 6166) normalization, format check and Luhn checksum validation.

An ISIN is :data:`ISIN_LENGTH` (12) characters: a two-letter ISO 3166-1 country code,
a nine-character alphanumeric NSIN and one check digit (:data:`ISIN_PATTERN`).

Checksum
--------
Every character of the FULL 12-character ISIN is expanded to digits (a digit stays
itself, a letter becomes two digits with A=10 ... Z=35, i.e. ``ord(ch) - 55``), the
pieces are concatenated and the standard Luhn algorithm runs over the whole digit
string: starting from the rightmost digit, every second digit (the second from the
right first) is doubled and the digits of the products are summed together with the
untouched digits; the ISIN is valid iff the total is divisible by 10.

Messy-input policy
------------------
* ``None``, empty and whitespace-only strings are ``"empty"``.
* Any other non-string is ``"unsupported_type"``; a dataframe NaN standing for a blank cell
  must be converted to ``None`` by the caller before it reaches this module.
* Strings lose every whitespace character anywhere in the string (spaces, tabs, CR/LF,
  NBSP U+00A0, narrow NBSP U+202F, ...) plus zero-width and BOM characters, and are
  upper-cased. Only ASCII input can match; hyphens or other separators are not
  tolerated and yield ``"format"``, as does any other deviation from :data:`ISIN_PATTERN`.
* With ``check=True`` (default) a failing Luhn check is ``"checksum"``.

Everything in this module is pure: no I/O, no logging, no global state.
"""

from __future__ import annotations

import re
from typing import Final, Literal, get_args

__all__ = [
    "ISIN_LENGTH",
    "ISIN_PATTERN",
    "ISIN_REASONS",
    "InvalidIsinError",
    "IsinError",
    "IsinReason",
    "is_isin_format",
    "is_valid_isin",
    "isin_checksum_ok",
    "isin_country_code",
    "normalize_isin",
    "try_normalize_isin",
]

#: Length of an ISIN in characters.
ISIN_LENGTH: Final[int] = 12

#: Structure of a normalized ISIN: country code, alphanumeric NSIN, numeric check digit.
ISIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

#: Machine-readable reason codes carried by :class:`InvalidIsinError`.
IsinReason = Literal["empty", "format", "checksum", "unsupported_type"]

#: The fixed set of reason codes, for validation and for callers that map them to text.
ISIN_REASONS: Final[frozenset[str]] = frozenset(get_args(IsinReason))

#: Unicode whitespace (``\\s`` covers NBSP, narrow NBSP, tabs, CR/LF, ...) plus the
#: zero-width characters and BOM that survive Excel/CSV round-trips.
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")
#: ``ord("A") - 10``: maps ``A``..``Z`` onto 10..35 for the checksum expansion.
_LETTER_OFFSET: Final[int] = ord("A") - 10
#: Longest ``repr`` of the offending value embedded in error messages.
_REPR_LIMIT: Final[int] = 80


class IsinError(ValueError):
    """Base class of every ISIN error raised by this module."""


class InvalidIsinError(IsinError):
    """The input cannot be interpreted as a valid ISIN.

    Attributes:
        reason: One of :data:`ISIN_REASONS`; stable, intended for programmatic handling.
        value: The original, unmodified input. Kept for reporting; embed it with ``repr``.
    """

    reason: IsinReason
    value: object

    def __init__(self, reason: IsinReason, value: object = None) -> None:
        if reason not in ISIN_REASONS:
            raise ValueError(
                f"unknown ISIN reason code {reason!r}; expected one of {sorted(ISIN_REASONS)}"
            )
        self.reason = reason
        self.value = value
        super().__init__(f"invalid ISIN {_short_repr(value)}: {reason}")

    def __reduce__(self) -> tuple[type[InvalidIsinError], tuple[IsinReason, object]]:
        """Keep the exception picklable despite the custom constructor signature."""
        return type(self), (self.reason, self.value)


def normalize_isin(value: object, *, check: bool = True) -> str:
    """Return the canonical upper-case 12-character ISIN for a messy cell value.

    Args:
        value: Anything an xlsx or dataframe column may contain; see the module docstring.
        check: Validate the Luhn check digit (default). With ``check=False`` a string
            that merely matches :data:`ISIN_PATTERN` is returned.

    Raises:
        InvalidIsinError: With a ``reason`` from :data:`ISIN_REASONS` describing the problem.
    """
    if value is None:
        raise InvalidIsinError("empty", value)
    if not isinstance(value, str):
        raise InvalidIsinError("unsupported_type", value)
    cleaned = _WHITESPACE_RE.sub("", value)
    if not cleaned:
        raise InvalidIsinError("empty", value)
    # Upper-casing non-ASCII text can produce ASCII letters (e.g. "ſ" -> "S"); refuse it
    # up front so only genuine ASCII input can ever match the pattern.
    if not cleaned.isascii():
        raise InvalidIsinError("format", value)
    isin = cleaned.upper()
    if ISIN_PATTERN.fullmatch(isin) is None:
        raise InvalidIsinError("format", value)
    if check and not isin_checksum_ok(isin):
        raise InvalidIsinError("checksum", value)
    return isin


def is_isin_format(value: object) -> bool:
    """Return ``True`` when ``value`` normalizes to the ISIN shape; the checksum is ignored. Never raises."""
    return try_normalize_isin(value, check=False) is not None


def isin_checksum_ok(isin: str) -> bool:
    """Return ``True`` when the normalized 12-character ``isin`` passes the Luhn check.

    ``isin`` must already match :data:`ISIN_PATTERN` (upper case, no whitespace);
    anything else, including a non-string, is ``False``. Nothing is raised.
    """
    if not isinstance(isin, str) or ISIN_PATTERN.fullmatch(isin) is None:
        return False
    return _luhn_total(_expand_to_digits(isin)) % 10 == 0


def is_valid_isin(value: object) -> bool:
    """Return ``True`` when :func:`normalize_isin` accepts ``value`` (checksum included). Never raises."""
    return try_normalize_isin(value) is not None


def try_normalize_isin(value: object, *, check: bool = True) -> str | None:
    """Like :func:`normalize_isin` but return ``None`` instead of raising. Never raises."""
    try:
        return normalize_isin(value, check=check)
    except IsinError:
        return None


def isin_country_code(isin: str) -> str:
    """Return the two-letter country prefix of a valid ISIN (normalized first).

    Raises:
        InvalidIsinError: When ``isin`` is not a valid ISIN, checksum included.
    """
    return normalize_isin(isin)[:2]


def _expand_to_digits(isin: str) -> str:
    """Expand a pattern-matching ISIN to its digit string: digits stay, ``A``..``Z`` -> 10..35."""
    return "".join(str(ord(ch) - _LETTER_OFFSET) if ch.isalpha() else ch for ch in isin)


def _luhn_total(digits: str) -> int:
    """Luhn sum of an ASCII digit string: every second digit from the right is doubled."""
    total = 0
    for position, ch in enumerate(reversed(digits)):
        digit = ord(ch) - ord("0")
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9  # equivalent to summing the two digits of the product
        total += digit
    return total


def _short_repr(value: object) -> str:
    """``repr`` of a value bounded in length and guaranteed not to raise."""
    try:
        text = repr(value)
    except Exception:  # a broken __repr__ (or a huge int) must not mask the real error
        text = f"<unrepresentable {type(value).__name__}>"
    if len(text) > _REPR_LIMIT:
        text = text[: _REPR_LIMIT - 3] + "..."
    return text
