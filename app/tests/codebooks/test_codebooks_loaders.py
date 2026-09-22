"""Tests of the four loaders, ``load_codebooks`` and the ``CodebookSet`` lookups."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.codebooks.consistency import check_consistency
from core.codebooks.errors import (
    CodebookFileError,
    CodebookSchemaError,
    InvalidEsaCodeError,
    MalformedCodeError,
    UnknownCodeError,
    UnknownEsaCodeError,
    UnknownNaceCodeError,
)
from core.codebooks.loaders import (
    CTS_COLUMNS,
    NACE_STAT_COLUMNS,
    VALID_ESA_COLUMNS,
    load_ba0036_valid,
    load_codebooks,
    load_cts_ba0036,
    load_cts_okec_nace2,
    load_nace_stat,
)
from core.codebooks.models import (
    CodebookSet,
    CtsEntry,
    EsaSector,
    NaceDivision,
    NaceStatRow,
)

from .conftest import (
    CTS_BA0036_ROWS,
    CTS_OKEC_NACE2_ROWS,
    HEADERS,
    NACE_STAT_ROWS,
    VALID_ESA_ROWS,
    CodebookRows,
    MakeCodebookDir,
    make_settings,
    write_xlsx,
)

# --- individual loaders ------------------------------------------------------------------


def test_load_cts_ba0036_numeric_ids_and_values(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "cts.xlsx",
        ("ID", "VALUE", "DESCRIPTION"),
        [(1001.0, 11001, "Veřejné"), (1002, "s.11002", "Národní"), ("1003", "S.121", None)],
    )
    book = load_cts_ba0036(path)
    assert book.kind == "BA0036"
    assert len(book) == 3
    assert book.by_id["1001"] == CtsEntry("1001", "11001", "11001", "Veřejné")
    assert book.by_key["11002"].cts_id == "1002"
    assert book.by_key["121"].description == ""
    assert book.file.name == "cts_ba0036"
    assert book.file.row_count == 3
    assert book.file.path == path
    assert not book.malformed and book.skipped_rows == 0


def test_load_cts_okec_nace2_int_one_becomes_01(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "okec.xlsx",
        ("ID", "VALUE", "DESCRIPTION"),
        [(2001, 1, "Zemědělství"), (2002, 1.0, "dup of 01 written as float"), (2004, "62", "IT")],
    )
    book = load_cts_okec_nace2(path)
    assert book.kind == "OKEC_NACE2"
    assert book.by_id["2001"].key == "01"
    assert book.by_id["2001"].value == "1"
    assert book.by_id["2004"].key == "62"
    assert book.duplicate_keys == ("01",)
    assert book.by_key["01"].cts_id == "2001"  # first occurrence wins


def test_load_ba0036_valid_canonical_codes(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "valid.xlsx",
        ("KOD", "Nazev", "Popis"),
        [
            ("S.11001", "Veřejné nefinanční podniky", "popis"),
            (11002, "Národní", ""),
            ("s.14", "Domácnosti", None),
        ],
    )
    valid = load_ba0036_valid(path)
    assert [s.code for s in valid.sectors] == ["S.11001", "S.11002", "S.14"]
    assert valid.by_key["11002"] == EsaSector("S.11002", "11002", "Národní", "")
    assert valid.file.name == "ba0036_valid"
    assert valid.duplicate_keys == ()


def test_load_nace_stat_groups_rows_in_file_order(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "stat.xlsx",
        ("NACE", "Zkrtext", "Text"),
        [
            (62, "Programování a poradenství", "Činnosti v oblasti informačních technologií"),
            ("62", "", "Poradenství v oblasti informačních technologií"),
            ("62", "Programování a poradenství", "Činnosti v oblasti informačních technologií"),
            (1, "Rostlinná výroba", "Pěstování plodin"),
            ("01", "Rostlinná výroba", ""),
        ],
    )
    stat = load_nace_stat(path)
    assert stat.codes == ("01", "62")
    assert len(stat) == 2
    division = stat.divisions["62"]
    assert isinstance(division, NaceDivision)
    assert len(division.rows) == 3
    assert division.short_text == "Programování a poradenství"
    assert division.texts == (
        "Činnosti v oblasti informačních technologií",
        "Poradenství v oblasti informačních technologií",
    )
    assert division.labels == (
        "Programování a poradenství",
        "Činnosti v oblasti informačních technologií",
        "Poradenství v oblasti informačních technologií",
    )
    assert stat.divisions["01"].labels == ("Rostlinná výroba", "Pěstování plodin")
    assert stat.file.name == "nace_stat"
    assert stat.file.row_count == 5


def test_empty_id_or_value_rows_are_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_xlsx(
        tmp_path / "cts.xlsx",
        ("ID", "VALUE", "DESCRIPTION"),
        [
            (1001, "S.11001", "ok"),
            (None, "S.11002", "no id"),
            (1003, None, "no value"),
            (None, None, "only text"),
        ],
    )
    with caplog.at_level(logging.WARNING, logger="core.codebooks.loaders"):
        book = load_cts_ba0036(path)
    assert len(book) == 1
    assert book.skipped_rows == 3
    assert book.file.row_count == 4
    messages = [record.getMessage() for record in caplog.records]
    assert any("row 3" in m and "ID" in m for m in messages)
    assert any("row 4" in m and "VALUE" in m for m in messages)


def test_malformed_rows_are_recorded_not_raised(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "cts.xlsx",
        ("ID", "VALUE", "DESCRIPTION"),
        [(1001, "S.11001", "ok"), (1002, "S.1A", "bad"), (1003, "S.11003", "ok")],
    )
    book = load_cts_ba0036(path)
    assert len(book) == 2
    assert len(book.malformed) == 1
    row = book.malformed[0]
    assert row.row_number == 3
    assert row.raw == {"ID": "1002", "VALUE": "S.1A", "DESCRIPTION": "bad"}
    assert "S.1A" in row.reason


def test_malformed_rows_in_valid_list_and_nace_stat(tmp_path: Path) -> None:
    valid = load_ba0036_valid(
        write_xlsx(
            tmp_path / "v.xlsx", ("Kód", "Název", "Popis"), [("S.X", "bad", ""), ("S.14", "ok", "")]
        )
    )
    assert [r.row_number for r in valid.malformed] == [2]
    assert [s.key for s in valid.sectors] == ["14"]
    stat = load_nace_stat(
        write_xlsx(
            tmp_path / "s.xlsx",
            ("NACE", "Zkrtext", "Text"),
            [("62.01", "full code", ""), ("62", "ok", "")],
        )
    )
    assert [r.row_number for r in stat.malformed] == [2]
    assert stat.codes == ("62",)


def test_duplicates_are_recorded_first_wins(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "cts.xlsx",
        ("ID", "VALUE", "DESCRIPTION"),
        [
            (1001, "S.11001", "a"),
            (1001, "S.11002", "same id"),
            (1003, "S.11001", "same value"),
            (1001, "S.14", "again"),
        ],
    )
    book = load_cts_ba0036(path)
    assert len(book.entries) == 4
    assert book.duplicate_ids == ("1001",)
    assert book.duplicate_keys == ("11001",)
    assert book.by_id["1001"].description == "a"
    assert book.by_key["11001"].cts_id == "1001"


def test_loader_errors_propagate(tmp_path: Path) -> None:
    with pytest.raises(CodebookFileError):
        load_nace_stat(tmp_path / "missing.xlsx")
    path = write_xlsx(tmp_path / "wrong.xlsx", ("A", "B", "C"), [(1, 2, 3)])
    with pytest.raises(CodebookSchemaError):
        load_ba0036_valid(path)


# --- load_codebooks and CodebookSet -------------------------------------------------------


def test_load_codebooks_from_directory(codebook_dir: Path, defaults: CodebookRows) -> None:
    codebooks = load_codebooks(make_settings(codebook_dir))
    assert isinstance(codebooks, CodebookSet)
    assert len(codebooks.cts_ba0036) == len(defaults.cts_ba0036)
    assert len(codebooks.ba0036_valid) == len(defaults.ba0036_valid)
    assert len(codebooks.cts_okec_nace2) == len(defaults.cts_okec_nace2)
    assert codebooks.nace_stat.codes == defaults.nace_divisions
    assert codebooks.version.id.startswith("cb-")
    assert {f.name for f in codebooks.version.files} == set(defaults.file_names)


def test_codebook_dir_argument_overrides_settings(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    other = make_codebook_dir(tmp_path / "other")
    settings = make_settings(tmp_path / "does-not-exist")
    codebooks = load_codebooks(settings, codebook_dir=other)
    assert all(f.path.parent == other for f in codebooks.version.files)
    with pytest.raises(CodebookFileError):
        load_codebooks(settings)


def test_load_codebooks_uses_get_settings_by_default(
    codebook_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.codebooks.loaders as loaders

    monkeypatch.setattr(loaders, "get_settings", lambda: make_settings(codebook_dir))
    assert load_codebooks().version.id.startswith("cb-")


@pytest.mark.parametrize(
    "spelling",
    ["S.11001", "s.11001", "S11001", "s11001", "11001", 11001, 11001.0, "11001.0", " S. 11001 "],
)
def test_cts_id_for_esa_accepts_every_spelling(codebooks: CodebookSet, spelling: object) -> None:
    entry = codebooks.cts_id_for_esa(spelling)
    assert entry.cts_id == "1001"
    assert entry.key == "11001"
    assert entry.value == "S.11001"
    assert codebooks.is_valid_esa(spelling) is True


def test_cts_id_for_esa_rejects_parent_codes(
    codebooks: CodebookSet, defaults: CodebookRows
) -> None:
    for parent in defaults.parent_codes:
        assert parent in {row[1] for row in defaults.cts_ba0036}
        assert codebooks.is_valid_esa(parent) is False
        with pytest.raises(InvalidEsaCodeError, match="not a valid leaf") as info:
            codebooks.cts_id_for_esa(parent)
        assert isinstance(info.value, UnknownCodeError)
        assert isinstance(info.value, LookupError)


def test_cts_id_for_esa_unknown_and_malformed(codebooks: CodebookSet) -> None:
    with pytest.raises(UnknownEsaCodeError, match="not present"):
        codebooks.cts_id_for_esa("S.99999")
    assert codebooks.is_valid_esa("S.99999") is False
    with pytest.raises(MalformedCodeError):
        codebooks.cts_id_for_esa("S.1A")
    with pytest.raises(MalformedCodeError):
        codebooks.cts_id_for_esa("")
    assert codebooks.is_valid_esa("S.1A") is False
    assert codebooks.is_valid_esa(None) is False


def test_valid_leaf_without_cts_entry_fails_safely(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] != "S.1314"]
    directory = make_codebook_dir(tmp_path / "cb", cts_ba0036=rows)
    codebooks = load_codebooks(make_settings(directory))
    assert codebooks.is_valid_esa("S.1314") is True
    with pytest.raises(UnknownEsaCodeError, match="valid leaf but has no entry"):
        codebooks.cts_id_for_esa("S.1314")


@pytest.mark.parametrize("spelling", ["62.01", 62, "6201", "62", "62.01.1", 6201, " 62 "])
def test_cts_id_for_nace_truncates_here(codebooks: CodebookSet, spelling: object) -> None:
    entry = codebooks.cts_id_for_nace(spelling)
    assert entry.cts_id == "2004"
    assert entry.key == "62"


def test_cts_id_for_nace_equalities(codebooks: CodebookSet) -> None:
    assert codebooks.cts_id_for_nace("62.01") == codebooks.cts_id_for_nace(62)
    assert codebooks.cts_id_for_nace(62) == codebooks.cts_id_for_nace("6201")
    assert codebooks.cts_id_for_nace("01.11").cts_id == "2001"
    assert codebooks.cts_id_for_nace(1).cts_id == "2001"


def test_cts_id_for_nace_unknown_and_malformed(codebooks: CodebookSet) -> None:
    with pytest.raises(UnknownNaceCodeError, match="99") as info:
        codebooks.cts_id_for_nace("99.10")
    assert isinstance(info.value, LookupError)
    with pytest.raises(MalformedCodeError):
        codebooks.cts_id_for_nace("J")
    with pytest.raises(MalformedCodeError):
        codebooks.cts_id_for_nace(None)


def test_esa_leaves_and_nace_divisions(codebooks: CodebookSet, defaults: CodebookRows) -> None:
    leaves = codebooks.esa_leaves()
    assert [leaf.code for leaf in leaves] == [row[0] for row in defaults.ba0036_valid]
    assert all(isinstance(leaf, EsaSector) for leaf in leaves)
    assert leaves[0].name == "Veřejné nefinanční podniky"
    divisions = codebooks.nace_divisions()
    assert [d.code for d in divisions] == list(defaults.nace_divisions)
    assert divisions[3].short_text == "Programování a poradenství"


def test_describe_is_one_line_with_version_and_counts(codebooks: CodebookSet) -> None:
    text = codebooks.describe()
    assert "\n" not in text
    assert codebooks.version.id in text
    assert "cts_ba0036=18" in text
    assert "ba0036_valid=13" in text
    assert "cts_okec_nace2=7" in text
    assert "nace_stat=7 divisions (15 rows)" in text


# --- review regressions -------------------------------------------------------------------


def test_cts_id_for_nace_accepts_numpy_float(codebooks: CodebookSet) -> None:
    numpy = pytest.importorskip("numpy")
    assert codebooks.cts_id_for_nace(numpy.float64(62.01)).cts_id == "2004"


def test_cts_id_for_nace_refuses_date_cells(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_okec_nace2, (2020, "20", "Výroba chemických látek")]
    codebooks = load_codebooks(
        make_settings(make_codebook_dir(tmp_path / "cb", cts_okec_nace2=rows))
    )
    assert codebooks.cts_id_for_nace("20.11").cts_id == "2020"
    for value in (datetime(2024, 11, 1), "2024-11-01"):
        with pytest.raises(MalformedCodeError):
            codebooks.cts_id_for_nace(value)


def test_loader_alias_tables_match_contract() -> None:
    assert CTS_COLUMNS == {
        "ID": ("ID", ".ID", "CTS_ID"),
        "VALUE": ("VALUE", "HODNOTA"),
        "DESCRIPTION": ("DESCRIPTION", "POPIS"),
    }
    assert VALID_ESA_COLUMNS == {
        "Kód": ("Kód", "Kod", "Code"),
        "Název": ("Název", "Nazev", "Name"),
        "Popis": ("Popis", "Description"),
    }
    assert NACE_STAT_COLUMNS == {
        "NACE": ("NACE", "Kód NACE", "NACE2"),
        "Zkrtext": ("Zkrtext", "Zkrácený text"),
        "Text": ("Text",),
    }


@pytest.mark.parametrize(
    "headers", [("CTS_ID", "HODNOTA", "POPIS"), ("cts_id", "hodnota", "popis")]
)
def test_cts_loaders_accept_czech_aliases(tmp_path: Path, headers: tuple[str, ...]) -> None:
    esa = load_cts_ba0036(write_xlsx(tmp_path / "a.xlsx", headers, [(1001, "S.11001", "x")]))
    assert esa.by_id["1001"] == CtsEntry("1001", "S.11001", "11001", "x")
    nace = load_cts_okec_nace2(write_xlsx(tmp_path / "b.xlsx", headers, [(2001, 1, "y")]))
    assert nace.by_id["2001"].key == "01"


@pytest.mark.parametrize(
    "headers",
    [
        ("Kód NACE", "Zkrácený text", "Text"),
        ("NACE2", "ZKRTEXT", "text"),
        ("nace", "Zkrácený\N{NO-BREAK SPACE}text", "TEXT"),
    ],
)
def test_nace_stat_loader_accepts_aliases(tmp_path: Path, headers: tuple[str, ...]) -> None:
    stat = load_nace_stat(write_xlsx(tmp_path / "s.xlsx", headers, [(62, "IT", "x")]))
    assert stat.divisions["62"].labels == ("IT", "x")


def test_load_codebooks_uses_file_names_from_settings(tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    names = {
        "cts_ba0036": "cts_esa.xlsx",
        "ba0036_valid": "valid.xlsx",
        "cts_okec_nace2": "cts_nace.xlsx",
        "nace_stat": "stat.xlsx",
    }
    rows = {
        "cts_ba0036": CTS_BA0036_ROWS,
        "ba0036_valid": VALID_ESA_ROWS,
        "cts_okec_nace2": CTS_OKEC_NACE2_ROWS,
        "nace_stat": NACE_STAT_ROWS,
    }
    for key, name in names.items():
        write_xlsx(directory / name, HEADERS[key], rows[key])
    settings = Settings(
        _env_file=None,
        codebook_dir=directory,
        codebook_cts_ba0036_file=names["cts_ba0036"],
        codebook_ba0036_valid_file=names["ba0036_valid"],
        codebook_cts_okec_nace2_file=names["cts_okec_nace2"],
        codebook_nace_stat_file=names["nace_stat"],
    )
    codebooks = load_codebooks(settings)
    assert {f.name: f.path.name for f in codebooks.version.files} == names
    assert check_consistency(codebooks).ok


def test_nace_stat_empty_code_rows_are_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_xlsx(
        tmp_path / "stat.xlsx",
        ("NACE", "Zkrtext", "Text"),
        [("62", "IT", "x"), (None, "bez kódu", "y"), ("", "", "z")],
    )
    with caplog.at_level(logging.WARNING, logger="core.codebooks.loaders"):
        stat = load_nace_stat(path)
    assert stat.skipped_rows == 2
    assert stat.codes == ("62",)
    assert stat.malformed == ()
    messages = [r.getMessage() for r in caplog.records]
    assert any("row 3" in m and "NACE" in m for m in messages)
    assert any("row 4" in m for m in messages)


def test_nace_division_short_text_is_first_non_empty_and_labels_deduplicate() -> None:
    division = NaceDivision(
        "62",
        (
            NaceStatRow("62", "", "Text A"),
            NaceStatRow("62", "IT", "Text B"),
            NaceStatRow("62", "Jiné", "IT"),
        ),
    )
    assert division.short_text == "IT"
    assert division.texts == ("Text A", "Text B", "IT")
    assert division.labels == ("IT", "Text A", "Text B")
    assert NaceDivision("01", (NaceStatRow("01", "", ""),)).labels == ()


def test_cts_id_leading_zeros_survive_the_loader(tmp_path: Path) -> None:
    book = load_cts_ba0036(
        write_xlsx(tmp_path / "c.xlsx", ("ID", "VALUE", "DESCRIPTION"), [("00123", "S.14", "x")])
    )
    assert "00123" in book.by_id and "123" not in book.by_id
    assert book.by_key["14"].cts_id == "00123"
