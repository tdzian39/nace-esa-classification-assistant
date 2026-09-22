"""Pure normalization functions for Excel-derived cells, ESA sector codes and NACE codes.

All functions are side-effect free and accept ``object`` because values coming out of
openpyxl (or later out of DWS) may be ``str``, ``int``, ``float``, ``bool``, ``Decimal``,
``datetime`` or ``None`` depending on how the exporting tool typed the cell.

Terminology:

* **ESA key** - the canonical lookup form of an ESA 2010 sector code: only the digits,
  e.g. ``"11001"`` for ``S.11001``. Codebooks and inputs spell the same code in many ways
  (``S.11001``, ``s11001``, ``11001``, the float artefact ``11001.0``); the key makes them
  equal.
* **NACE division** - the 2-digit NACE Rev. 2 level (``"01"`` .. ``"99"``). The CTS codebook
  OKEC_NACE2 is keyed by division. Full NACE codes stay untouched in the data layer;
  :func:`nace_to_division` is the ONLY place where a full code is truncated.
"""

from __future__ import annotations

import re
from datetime import date, time
from decimal import Decimal

from core.codebooks.errors import MalformedCodeError

#: Unicode spaces that Excel exports use instead of a plain space.
_NON_BREAKING_SPACES = ("\N{NO-BREAK SPACE}", "\N{NARROW NO-BREAK SPACE}")
#: Invisible characters that sometimes survive copy/paste into Excel cells.
_INVISIBLE_CHARS = ("\N{ZERO WIDTH NO-BREAK SPACE}", "\N{ZERO WIDTH SPACE}")

# The real CTS BA0036 codebook uses a 7-digit CNB code (``1221300``), not the 5-digit ESA
# form (``S.12213``); RES/ARES report the ESA form. Both must normalize, so the key holds
# 1-7 digits. See the ESA-code note in the module docstring.
_ESA_KEY_MAX_DIGITS = 7

_WHITESPACE_RE = re.compile(r"\s+")
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")
#: ``S.11001`` / ``S11001`` / ``11001``: an optional ``S`` with an optional dot directly
#: after it, then 1-5 digits. A dot anywhere else (``"S.1.5"``, ``"1.5"``) is rejected rather
#: than collapsed into a different, existing sector.
_ESA_CODE_RE = re.compile(rf"(?:S\.?)?([0-9]{{1,{_ESA_KEY_MAX_DIGITS}}})")
#: A pure number with a zero fraction, e.g. ``"11001.0"`` produced by a float-to-string
#: conversion upstream (``","`` covers the Czech decimal separator).
_INTEGRAL_FLOAT_TEXT_RE = re.compile(r"([0-9]+)[.,]0+")
_NACE_DIVISION_TEXT_RE = re.compile(r"([0-9]{1,2})(?:[.,]0+)?")
#: ISO date (``2024-11-01``) or clock time (``12:30``) at the start of a cell: Excel in the
#: Czech locale turns NACE text such as ``01.11`` into a date, and the leading-digit rule would
#: otherwise read the year as division ``20``.
_TEMPORAL_TEXT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}:[0-9]{2}")


def normalize_cell(value: object) -> str:
    """Convert any cell value to clean text.

    Rules:

    * ``None`` -> ``""``
    * ``str`` -> ends stripped; non-breaking spaces become plain spaces; zero-width and
      byte-order marks are removed; Windows line endings become ``"\\n"``
    * ``bool`` -> ``"TRUE"`` / ``"FALSE"`` (checked before ``int`` because ``bool`` is an ``int``)
    * ``int`` -> decimal text
    * ``float`` or float subclass (e.g. ``numpy.float64``) -> ``str(int(v))`` when integral
      (Excel stores every number as a float, so ``1001.0`` is the ID ``1001``), otherwise
      ``repr(float(v))``; ``nan``/``inf`` fall into the non-integral branch and come back as
      their text form, which no code parser accepts
    * ``Decimal`` -> same as ``float``
    * ``datetime`` / ``date`` -> ISO 8601 text
    * anything else -> ``str(value).strip()``
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
        for space in _NON_BREAKING_SPACES:
            text = text.replace(space, " ")
        for invisible in _INVISIBLE_CHARS:
            text = text.replace(invisible, "")
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # ``float(value)`` so a float subclass (numpy.float64) gets the plain float repr
        # instead of ``np.float64(62.01)``.
        return str(int(value)) if value.is_integer() else repr(float(value))
    if isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            return str(int(value))
        return str(value)
    if isinstance(value, date):  # ``datetime`` is a ``date`` subclass
        return value.isoformat()
    return str(value).strip()


def _strip_whitespace(value: object) -> str:
    """``normalize_cell`` plus removal of every whitespace character (including inner ones)."""
    return _WHITESPACE_RE.sub("", normalize_cell(value))


def normalize_cts_id(value: object) -> str:
    """Return the CTS ID exactly as written, only cleaned by :func:`normalize_cell`.

    CTS IDs are opaque identifiers: leading zeros are kept and nothing is re-formatted.
    An integral float (``1001.0`` from Excel) becomes ``"1001"`` because that is the text
    the cell displayed.

    Raises:
        MalformedCodeError: if the value is empty.
    """
    text = normalize_cell(value)
    if not text:
        raise MalformedCodeError("CTS ID is empty")
    return text


def normalize_esa_key(value: object) -> str:
    """Return the canonical ESA key (1 to 5 ASCII digits) of any spelling of a sector code.

    Steps: :func:`normalize_cell`, drop all whitespace, upper-case, drop a ``.0`` float
    fraction, then accept ``S.<digits>``, ``S<digits>`` or ``<digits>`` (1 to 5 digits) and
    return the digits.

    Examples: ``"S.11001"`` -> ``"11001"``; ``"s11001"`` -> ``"11001"``; ``11001`` ->
    ``"11001"``; ``"11001.0"`` (or the Czech ``"11001,0"``) -> ``"11001"``; ``"S.2"`` -> ``"2"``;
    ``"S.13"`` -> ``"13"``.

    A dot anywhere but directly after the ``S`` is rejected on purpose: collapsing ``"S.1.5"``
    into ``"15"`` would silently turn a typo into the real sector S.15.

    Raises:
        MalformedCodeError: for ``""``, ``"S.1A"``, ``"SS.11"``, ``"S.1.5"``, more than five
            digits, etc.
    """
    text = _strip_whitespace(value).upper()
    if not text:
        raise MalformedCodeError("ESA code is empty")
    integral = _INTEGRAL_FLOAT_TEXT_RE.fullmatch(text)
    if integral is not None:
        text = integral.group(1)
    match = _ESA_CODE_RE.fullmatch(text)
    if match is None:
        raise MalformedCodeError(
            f"{normalize_cell(value)!r} is not an ESA 2010 sector code "
            f"(expected 'S.' followed by 1-{_ESA_KEY_MAX_DIGITS} digits)"
        )
    return match.group(1)


def format_esa_code(key: str) -> str:
    """Display form of an ESA key: ``"11001"`` -> ``"S.11001"``.

    The input is normalized first, so an already formatted code passes through unchanged.

    Raises:
        MalformedCodeError: if ``key`` is not an ESA code in any spelling.
    """
    return f"S.{normalize_esa_key(key)}"


def normalize_nace_division(value: object) -> str:
    """Normalize a cell of a 2-digit codebook column (CTS_OKEC_NACE2 VALUE, NACE_STAT NACE).

    Accepted: one digit (zero-padded: ``"1"`` / ``1`` -> ``"01"``), exactly two digits, and
    the Excel float artefact ``"1.0"`` / ``"1,0"`` / ``1.0`` -> ``"01"``. Anything else - three or more
    digits, letters, a full code such as ``"01.11"`` or the non-division ``"00"`` - is
    rejected because a codebook row must be a division, never truncated silently.

    Date/time guard: a ``date``/``datetime``/``time`` instance or text that looks like an ISO
    date or a clock time (an Excel auto-conversion of ``01.11``-style text) is rejected
    explicitly instead of yielding a bogus division.

    Raises:
        MalformedCodeError: if the value is not a 2-digit NACE division.
    """
    if isinstance(value, (date, time)):
        raise MalformedCodeError(
            f"{value!r} is a date/time, not a NACE division (Excel probably auto-converted the cell)"
        )
    text = _strip_whitespace(value)
    if _TEMPORAL_TEXT_RE.match(text):
        raise MalformedCodeError(
            f"{normalize_cell(value)!r} looks like a date or time, not a NACE division"
        )
    match = _NACE_DIVISION_TEXT_RE.fullmatch(text)
    if match is None:
        raise MalformedCodeError(f"{normalize_cell(value)!r} is not a 2-digit NACE division")
    division = match.group(1).zfill(2)
    if division == "00":
        raise MalformedCodeError("'00' is not a NACE division (valid range is 01-99)")
    return division


def nace_to_division(value: object) -> str:
    """Truncate a full NACE code in any common spelling to its 2-digit division.

    This is the ONLY truncation point used by the CTS-ID mapping; data records keep the
    full code. Rules: :func:`normalize_cell`, drop whitespace, take the leading run of
    digits (the part before the first ``.`` or other non-digit); two or more digits -> the
    first two (``"62.01"`` / ``"6201"`` / ``"62"`` -> ``"62"``, ``"01.11"`` / ``"0111"`` ->
    ``"01"``, ``"62.01.1"`` -> ``"62"``); a single digit is zero-padded (``"1"``, ``1``,
    ``1.0`` -> ``"01"``).

    Ambiguity warning: an *integer* such as ``111`` cannot be interpreted safely - it may
    be ``01.11`` whose leading zero was lost by Excel, or ``11.1``. This function does not
    guess: it applies the text rule and returns ``"11"``. It therefore assumes that string
    input from RES/DWS keeps its leading zeros; callers that receive NACE codes as numbers
    must restore the leading zero before calling.

    Date/time guard: a ``date``/``datetime``/``time`` instance (``pandas.Timestamp`` is a
    ``datetime`` subclass, so it is covered too) or text that looks like an ISO date or a clock
    time is rejected. Excel in the Czech locale converts NACE text such as ``01.11`` or
    ``10.12`` into a date; without the guard the year ``2024`` would be truncated to the real
    division ``20`` and a wrong CTS ID would be emitted.

    Raises:
        MalformedCodeError: for an empty value or one without a leading digit (``"J"``), for
            a date/time value, or for the non-division ``"00"``.
    """
    if isinstance(value, (date, time)):
        raise MalformedCodeError(
            f"{value!r} is a date/time, not a NACE code (Excel probably auto-converted the cell)"
        )
    text = _strip_whitespace(value)
    if _TEMPORAL_TEXT_RE.match(text):
        raise MalformedCodeError(
            f"{normalize_cell(value)!r} looks like a date or time, not a NACE code"
        )
    match = _ASCII_DIGITS_RE.match(text)
    if match is None:
        raise MalformedCodeError(f"{normalize_cell(value)!r} is not a NACE code")
    digits = match.group(0)
    division = digits.zfill(2) if len(digits) == 1 else digits[:2]
    if division == "00":
        raise MalformedCodeError(
            f"{normalize_cell(value)!r} does not start with a NACE division (valid range is 01-99)"
        )
    return division


def is_nace_division(value: str) -> bool:
    """True when ``value`` is exactly two ASCII digits in the range ``"01"`` .. ``"99"``."""
    return (
        isinstance(value, str)
        and len(value) == 2
        and value.isascii()
        and value.isdigit()
        and value != "00"
    )
