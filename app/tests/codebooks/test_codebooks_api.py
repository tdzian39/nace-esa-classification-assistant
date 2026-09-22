"""The package root re-exports the public API listed in the module contract."""

from __future__ import annotations

import core.codebooks as codebooks_pkg


def test_public_api_is_reexported() -> None:
    required = {
        "load_codebooks",
        "load_and_check",
        "check_consistency",
        "assert_consistent",
        "CodebookSet",
        "CtsEntry",
        "EsaSector",
        "NaceDivision",
        "NaceStatRow",
        "CodebookVersion",
        "CodebookFile",
        "ConsistencyReport",
        "Finding",
        "CodebookError",
        "CodebookFileError",
        "CodebookSchemaError",
        "CodebookConsistencyError",
        "UnknownCodeError",
        "UnknownEsaCodeError",
        "InvalidEsaCodeError",
        "UnknownNaceCodeError",
        "MalformedCodeError",
        "nace_to_division",
        "normalize_esa_key",
        "format_esa_code",
    }
    assert required <= set(codebooks_pkg.__all__)
    for name in codebooks_pkg.__all__:
        assert getattr(codebooks_pkg, name) is not None
