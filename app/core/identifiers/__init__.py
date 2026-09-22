"""Identifier normalization and validation (ISIN, IČO).

Pure functions only: no I/O, no logging. The algorithms and the policy for messy
Excel-derived input are documented in :mod:`core.identifiers.isin` and
:mod:`core.identifiers.ico`; this package re-exports their public names. The ISIN is what
the suggester looks up; the IČO functions serve :mod:`core.batch.reader`, which keeps
recognising IČO columns until roadmap E6 generalises it to ISIN and name columns.
"""

from core.identifiers.ico import (
    ICO_LENGTH,
    ICO_REASONS,
    IcoError,
    IcoReason,
    InvalidIcoError,
    ico_checksum_ok,
    is_valid_ico,
    normalize_ico,
    try_normalize_ico,
)
from core.identifiers.isin import (
    ISIN_LENGTH,
    ISIN_PATTERN,
    ISIN_REASONS,
    InvalidIsinError,
    IsinError,
    IsinReason,
    is_isin_format,
    is_valid_isin,
    isin_checksum_ok,
    isin_country_code,
    normalize_isin,
    try_normalize_isin,
)

__all__ = [
    "ICO_LENGTH",
    "ICO_REASONS",
    "ISIN_LENGTH",
    "ISIN_PATTERN",
    "ISIN_REASONS",
    "IcoError",
    "IcoReason",
    "InvalidIcoError",
    "InvalidIsinError",
    "IsinError",
    "IsinReason",
    "ico_checksum_ok",
    "is_isin_format",
    "is_valid_ico",
    "is_valid_isin",
    "isin_checksum_ok",
    "isin_country_code",
    "normalize_ico",
    "normalize_isin",
    "try_normalize_ico",
    "try_normalize_isin",
]
