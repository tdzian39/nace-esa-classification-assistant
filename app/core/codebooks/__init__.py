"""Codebooks: load the four xlsx codebooks, version them and check them at startup.

Only :meth:`CodebookSet.cts_id_for_esa` and :meth:`CodebookSet.cts_id_for_nace` may ever emit
a CTS ID. :func:`load_and_check` is the startup entry point; it guarantees that every ID those
two lookups can emit exists in the loaded codebook.
"""

from core.codebooks.consistency import (
    ConsistencyReport,
    Finding,
    Severity,
    assert_consistent,
    check_consistency,
)
from core.codebooks.errors import (
    CodebookConsistencyError,
    CodebookError,
    CodebookFileError,
    CodebookSchemaError,
    InvalidEsaCodeError,
    MalformedCodeError,
    UnknownCodeError,
    UnknownEsaCodeError,
    UnknownNaceCodeError,
)
from core.codebooks.loaders import (
    load_and_check,
    load_ba0036_valid,
    load_codebooks,
    load_cts_ba0036,
    load_cts_okec_nace2,
    load_nace_stat,
)
from core.codebooks.models import (
    CodebookFile,
    CodebookSet,
    CodebookVersion,
    CtsCodebook,
    CtsEntry,
    EsaSector,
    EsaValidList,
    MalformedRow,
    NaceDivision,
    NaceStat,
    NaceStatRow,
)
from core.codebooks.normalize import (
    format_esa_code,
    is_nace_division,
    nace_to_division,
    normalize_cell,
    normalize_cts_id,
    normalize_esa_key,
    normalize_nace_division,
)
from core.codebooks.versioning import build_version, fingerprint_file

__all__ = [
    "CodebookConsistencyError",
    "CodebookError",
    "CodebookFile",
    "CodebookFileError",
    "CodebookSchemaError",
    "CodebookSet",
    "CodebookVersion",
    "ConsistencyReport",
    "CtsCodebook",
    "CtsEntry",
    "EsaSector",
    "EsaValidList",
    "Finding",
    "InvalidEsaCodeError",
    "MalformedCodeError",
    "MalformedRow",
    "NaceDivision",
    "NaceStat",
    "NaceStatRow",
    "Severity",
    "UnknownCodeError",
    "UnknownEsaCodeError",
    "UnknownNaceCodeError",
    "assert_consistent",
    "build_version",
    "check_consistency",
    "fingerprint_file",
    "format_esa_code",
    "is_nace_division",
    "load_and_check",
    "load_ba0036_valid",
    "load_codebooks",
    "load_cts_ba0036",
    "load_cts_okec_nace2",
    "load_nace_stat",
    "nace_to_division",
    "normalize_cell",
    "normalize_cts_id",
    "normalize_esa_key",
    "normalize_nace_division",
]
