"""Identifier normalization and validation (IČO, ISIN).

Pure functions only: no I/O, no logging. The algorithms and the policy for messy
Excel-derived input are documented in :mod:`core.identifiers.ico` and
:mod:`core.identifiers.isin`; this package re-exports their public names.
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
