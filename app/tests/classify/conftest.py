"""A synthetic CodebookSet shaped like the real one, built in memory.

The real codebooks are bank-internal and git-ignored, so every test here builds its own. The
shape matters more than the size: 7-digit BA0036 codes in the CNB's grid of family x control,
and NACE divisions carrying several NACE_STAT texts each, because the filter's behaviour
depends on both.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.codebooks.models import (
    CodebookFile,
    CodebookSet,
    CodebookVersion,
    CtsCodebook,
    CtsEntry,
    EsaSector,
    EsaValidList,
    NaceDivision,
    NaceStat,
    NaceStatRow,
)
from core.codebooks.normalize import normalize_esa_key

#: (ESA code, name, popis) - the rest-of-world grid, plus one resident family and the
#: sectors with no control axis, mirroring how the real file is laid out.
ESA_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        "2001001",
        "Nefinanční podniky veřejné",
        "Nerezidentské nefinanční podniky pod veřejnou kontrolou.",
    ),
    ("2001002", "Nefinanční podniky soukromé národní", "Nerezidentské nefinanční podniky."),
    (
        "2001003",
        "Nefinanční podniky soukromé pod zahraniční kontrolou",
        "Nerezidentské nefinanční podniky ovládané zahraničními jednotkami.",
    ),
    ("2002211", "Banky veřejné", "Nerezidentské banky pod veřejnou kontrolou."),
    ("2002212", "Banky soukromé národní", "Nerezidentské banky, národní soukromé."),
    (
        "2002213",
        "Banky pod zahraniční kontrolou",
        "Nerezidentské banky ovládané zahraničními jednotkami.",
    ),
    (
        "2002703",
        "Kaptivní finanční instituce a půjčovatelé peněz pod zahraniční kontrolou",
        "Jednotky, které financují výhradně vlastní skupinu a nepřijímají vklady.",
    ),
    (
        "2002702",
        "Kaptivní finanční instituce a půjčovatelé peněz soukromé národní",
        "Kaptivní finanční instituce, národní soukromé.",
    ),
    (
        "2002803",
        "Pojišťovací společnosti (IC) pod zahraniční kontrolou",
        "Nerezidentské pojišťovny ovládané zahraničními jednotkami.",
    ),
    ("2009031", "Mezinárodní rozvojové banky", "Nadnárodní rozvojové banky."),
    # Resident block, so the residency restriction has something to exclude.
    (
        "1100300",
        "Nefinanční podniky soukromé pod zahraniční kontrolou",
        "Rezidentské nefinanční podniky.",
    ),
    ("1221300", "Banky pod zahraniční kontrolou", "Rezidentské banky pod zahraniční kontrolou."),
)

#: The whole resident block of BA0036 as the CTS list has it (53 leaves; the ČNB names are
#: public, SDAT v044), for the resident issuers (2 Oct 2026). The CTS IDs these get are
#: synthetic, like every other ID here.
_S125 = (
    ("1", "Účelové finanční instituce pro sekuritizaci aktiv"),
    ("2", "Obchodníci s cennými papíry a deriváty"),
    ("3", "Finanční instituce poskytující úvěry"),
    ("4", "Specializované finanční instituce"),
)
_CONTROL = (("1", "veřejné"), ("2", "soukromé národní"), ("3", "pod zahraniční kontrolou"))
RESIDENT_ESA_ROWS: tuple[tuple[str, str, str], ...] = (
    ("1100100", "Nefinanční podniky veřejné", "Veřejné nefinanční podniky (S.11001)."),
    ("1100200", "Nefinanční podniky soukromé národní", "Národní soukromé (S.11002)."),
    (
        "1100300",
        "Nefinanční podniky soukromé pod zahraniční kontrolou",
        "Nefinanční podniky pod zahraniční kontrolou (S.11003).",
    ),
    ("1210000", "Centrální banka", "Česká národní banka (S.121)."),
    ("1221100", "Banky veřejné", "Banky podle zákona o bankách, veřejné."),
    ("1221200", "Banky soukromé národní", "Banky podle zákona o bankách, národní."),
    ("1221300", "Banky pod zahraniční kontrolou", "Banky podle zákona o bankách, zahraniční."),
    ("1222100", "Jiné instituce přijímající vklady veřejné", "Jiné než banky, veřejné."),
    ("1222200", "Spořitelní a úvěrní družstva soukromé národní", "Podle zákona č. 87/1995 Sb."),
    ("1222300", "Spořitelní a úvěrní družstva pod zahraniční kontrolou", "Zákon č. 87/1995 Sb."),
    ("1224200", "Jiné instituce přijímající vklady soukromé národní", "Jiné než banky a družstva."),
    ("1224300", "Jiné instituce přijímající vklady pod zahraniční kontrolou", "Jiné, zahraniční."),
    *((f"1230{c}00", f"Fondy peněžního trhu {name}", "") for c, name in _CONTROL),
    *(
        (f"1240{c}00", f"Investiční fondy jiné než fondy peněžního trhu, {name}", "")
        for c, name in _CONTROL
    ),
    *((f"1250{c}{t}0", f"{family} - {name}", "") for c, name in _CONTROL for t, family in _S125),
    *((f"1260{c}00", f"Pomocné finanční instituce {name}", "") for c, name in _CONTROL),
    *(
        (f"1270{c}00", f"Kaptivní finanční instituce a půjčovatelé peněz {name}", "")
        for c, name in _CONTROL
    ),
    *((f"1280{c}00", f"Pojišťovací společnosti (IC) {name}", "") for c, name in _CONTROL),
    *((f"1290{c}00", f"Penzijní fondy (PF) {name}", "") for c, name in _CONTROL),
    ("1311000", "Ústřední vládní instituce", "Správní resorty státu (S.1311)."),
    ("1313000", "Místní vládní instituce", "Obce a kraje (S.1313)."),
    ("1314000", "Fondy sociálního zabezpečení", "Zdravotní pojišťovny (S.1314)."),
    ("1410000", "Zaměstnavatelé", ""),
    ("1420000", "Osoby samostatně výdělečně činné", ""),
    ("1430000", "Zaměstnanci", ""),
    ("1441000", "Příjemci důchodů z vlastnictví", ""),
    ("1442000", "Příjemci penzí", ""),
    ("1443000", "Příjemci ostatních transferů (obsahuje výhradně SVJ)", ""),
    ("1500200", "Neziskové instituce sloužící domácnostem soukromé národní", ""),
    ("1500300", "Neziskové instituce sloužící domácnostem pod zahraniční kontrolou", ""),
)

#: (division, short text, full texts) - several NACE_STAT rows per division, as in the real file.
NACE_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "64",
        "Finanční činnosti,ne pojišťování a penzijního financování",
        (
            "Finanční činnosti, kromě pojišťování a penzijního financování",
            "Peněžní zprostředkování",
            "Činnosti holdingových společností a účelových finančních společností",
            "Činnosti účelových finančních společností",
            "Finanční leasing",
            "Ostatní poskytování úvěrů",
        ),
    ),
    (
        "65",
        "Pojišťovnictví a penzijní financování",
        (
            "Pojištění, zajištění a penzijní financování",
            "Neživotní pojištění",
            "Penzijní financování",
        ),
    ),
    (
        "66",
        "Pomocné činnosti k finančním činnostem",
        ("Pomocné činnosti k finančním a pojišťovacím činnostem",),
    ),
    (
        "29",
        "Výroba motorových vozidel, přívěsů a návěsů",
        ("Výroba motorových vozidel", "Výroba dílů a příslušenství pro motorová vozidla"),
    ),
    (
        "47",
        "Maloobchod",
        ("Maloobchod, kromě motorových vozidel", "Maloobchod v nespecializovaných prodejnách"),
    ),
    (
        "62",
        "Činnosti v oblasti informačních technologií",
        ("Programování a poradenství v oblasti IT",),
    ),
    (
        "70",
        "Činnosti vedení podniků",
        ("Činnosti řízení podniků a poradenství v oblasti podnikání",),
    ),
)


def _file(name: str, rows: int) -> CodebookFile:
    return CodebookFile(
        name=name,
        path=Path(f"/synthetic/{name}.xlsx"),
        sha256="0" * 64,
        size_bytes=1,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        row_count=rows,
    )


def build_codebooks(
    esa_rows: Sequence[tuple[str, str, str]] = ESA_ROWS,
    nace_rows: Sequence[tuple[str, str, tuple[str, ...]]] = NACE_ROWS,
    *,
    esa_cts_start: int = 600,
    nace_cts_start: int = 500,
) -> CodebookSet:
    """Assemble a CodebookSet directly, without touching a spreadsheet."""
    esa_entries = tuple(
        CtsEntry(
            cts_id=str(esa_cts_start + index),
            value=code,
            key=normalize_esa_key(code),
            description=name,
        )
        for index, (code, name, _popis) in enumerate(esa_rows)
    )
    sectors = tuple(
        EsaSector(code=f"S.{code}", key=normalize_esa_key(code), name=name, description=popis)
        for code, name, popis in esa_rows
    )
    nace_entries = tuple(
        CtsEntry(cts_id=str(nace_cts_start + index), value=code, key=code, description=short)
        for index, (code, short, _texts) in enumerate(nace_rows)
    )
    divisions = {
        code: NaceDivision(
            code=code,
            rows=tuple(NaceStatRow(code=code, short_text=short, text=text) for text in texts),
        )
        for code, short, texts in nace_rows
    }
    files = (_file("cts_ba0036", len(esa_entries)), _file("nace_stat", len(divisions)))
    return CodebookSet(
        cts_ba0036=CtsCodebook(kind="BA0036", entries=esa_entries, file=files[0]),
        ba0036_valid=EsaValidList(sectors=sectors, file=files[0]),
        cts_okec_nace2=CtsCodebook(kind="OKEC_NACE2", entries=nace_entries, file=files[1]),
        nace_stat=NaceStat(divisions=divisions, file=files[1]),
        version=CodebookVersion(
            id="cb-test0000000000",
            label="synthetic",
            loaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            files=files,
        ),
    )


#: Divisions the resident cases need on top of NACE_ROWS; 45 stays out (CZ-NACE 2025 has
#: none, nor does the CTS list).
RESIDENT_NACE_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("35", "Výroba a rozvod elektřiny, plynu, tepla", ("Výroba a rozvod elektřiny",)),
    ("84", "Veřejná správa a obrana; povinné sociální zabezpečení", ("Veřejná správa",)),
)


def build_resident_codebooks(drop: str | None = None) -> CodebookSet:
    """The synthetic books with the whole resident block (less ``drop``) and divisions 35, 84."""
    resident = [row for row in RESIDENT_ESA_ROWS if row[0] != drop]
    return build_codebooks((*ESA_ROWS[:-2], *resident), (*NACE_ROWS, *RESIDENT_NACE_ROWS))


@pytest.fixture
def codebooks() -> CodebookSet:
    return build_codebooks()


#: Descriptions reused across the candidate tests.
CAPTIVE_EN = (
    "Special purpose funding vehicle of a banking group. Issues bonds and on-lends the "
    "proceeds exclusively to its parent bank. Does not take deposits and holds no licence."
)
CAPTIVE_CS = (
    "Účelová finanční společnost skupiny. Vydává dluhopisy a prostředky půjčuje výhradně "
    "mateřské bance. Nepřijímá vklady a nemá bankovní licenci."
)
CARMAKER_EN = (
    "German manufacturer of passenger cars and light commercial vehicles, majority owned "
    "by a foreign automotive group. Issues bonds to finance its own operations."
)
