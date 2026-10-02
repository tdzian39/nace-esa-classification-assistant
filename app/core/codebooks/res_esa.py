"""RES institutional sector (ČSÚ, 5 digits) -> BA0036 resident code (ČNB, 7 digits): the table.

RES records a Czech subject's sector in ČSÚ's ESA 2010 classification (``12203``); CTS keeps
the ČNB codelist BA0036, whose resident block (first digit 1) has 53 elementary codes in the
CTS file. The two mostly agree digit for digit, but not everywhere, so the conversion is this
reviewable table rather than a formula. It holds BA0036 codes only - **no CTS IDs**: the
repository is public, and every ID comes from the loaded codebook through
:meth:`~core.codebooks.models.CodebookSet.cts_id_for_esa`. At startup
:func:`check_targets` (called by the consistency check) looks every target up in that
codebook, and a miss is a finding, not a silent no-op.

Three kinds of row (Jakub's brief, 2 Oct 2026):

* **Direct** (:data:`DIRECT`): the sector alone decides; BA0036 = ``1`` + the RES code without
  its first digit + ``00`` (``11001`` -> ``1100100``, ``13110`` -> ``1311000``).
* **S.122 deposit-takers** (:data:`DEPOSIT_TAKERS`, RES ``1220c``): BA0036 splits them into
  banks (``1221c00``), savings and credit cooperatives (``1222c00``) and other deposit-takers
  (``1224200``, ``1224300``, and ``1222100`` for the public one - ČNB's code for it; there is
  no ``1224100`` and no public cooperative). Which one is not in the sector code.
* **S.125 other financial intermediaries** (:data:`OTHER_INTERMEDIARIES`, RES ``1250c``):
  BA0036 = ``1250`` + c + t + ``0``, t the subtype - 1 securitisation, 2 securities and
  derivatives dealers, 3 lenders, 4 specialised institutions. Not in the sector code either.

Which institution type or subtype applies is decided from structured data only (legal form,
the ECB lists, the RES NACE code) in :mod:`core.classify.residents` - never from words in the
name (the owner's rule, 29 Sept 2026). :data:`NO_COUNTERPART` lists the RES sectors with no
resident BA0036 code at all. The control digit (the last digit of the RES code) is RES's
answer to roadmap Q7 for a resident issuer.

The sector names are ČSÚ's, as the ARES codelist ``InstitucionalniSektor2010`` spells them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Literal

from core.codebooks.errors import MalformedCodeError, UnknownCodeError

if TYPE_CHECKING:  # pragma: no cover
    from core.codebooks.models import CodebookSet

#: The S.122 institution types BA0036 tells apart.
DepositTaker = Literal["bank", "cooperative", "other"]

#: The S.125 subtypes BA0036 tells apart (the t in ``1250ct0``).
Intermediary = Literal["securitisation", "dealer", "lender", "specialised"]

#: RES sectors whose BA0036 code follows from the sector alone.
DIRECT: Final[Mapping[str, str]] = {
    "11001": "1100100",
    "11002": "1100200",
    "11003": "1100300",
    "12100": "1210000",
    "12301": "1230100",
    "12302": "1230200",
    "12303": "1230300",
    "12401": "1240100",
    "12402": "1240200",
    "12403": "1240300",
    "12601": "1260100",
    "12602": "1260200",
    "12603": "1260300",
    "12701": "1270100",
    "12702": "1270200",
    "12703": "1270300",
    "12801": "1280100",
    "12802": "1280200",
    "12803": "1280300",
    "12901": "1290100",
    "12902": "1290200",
    "12903": "1290300",
    "13110": "1311000",
    "13130": "1313000",
    "13140": "1314000",
    "14100": "1410000",
    "14200": "1420000",
    "14300": "1430000",
    "14410": "1441000",
    "14420": "1442000",
    "14430": "1443000",
    "15002": "1500200",
    "15003": "1500300",
}

#: S.122 (RES ``1220c``): the BA0036 code per institution type. No public cooperative exists
#: in BA0036, so ``12201`` has no ``cooperative`` entry.
DEPOSIT_TAKERS: Final[Mapping[str, Mapping[DepositTaker, str]]] = {
    "12201": {"bank": "1221100", "other": "1222100"},
    "12202": {"bank": "1221200", "cooperative": "1222200", "other": "1224200"},
    "12203": {"bank": "1221300", "cooperative": "1222300", "other": "1224300"},
}

#: S.125 (RES ``1250c``): the BA0036 code per subtype, ``1250`` + c + t + ``0``.
OTHER_INTERMEDIARIES: Final[Mapping[str, Mapping[Intermediary, str]]] = {
    "12501": {
        "securitisation": "1250110",
        "dealer": "1250120",
        "lender": "1250130",
        "specialised": "1250140",
    },
    "12502": {
        "securitisation": "1250210",
        "dealer": "1250220",
        "lender": "1250230",
        "specialised": "1250240",
    },
    "12503": {
        "securitisation": "1250310",
        "dealer": "1250320",
        "lender": "1250330",
        "specialised": "1250340",
    },
}

#: RES sectors without a resident BA0036 code: "not found", the state government subsector
#: (S.1312, which has no Czech units and no code in the CTS list), and the rest-of-world codes.
NO_COUNTERPART: Final[frozenset[str]] = frozenset(
    {"0", "13120", "21110", "21120", "21210", "21220", "22000"}
)

#: ČSÚ's names of the RES sectors (ARES codelist ``InstitucionalniSektor2010``).
SECTOR_NAMES_CS: Final[Mapping[str, str]] = {
    "0": "Nezjištěno",
    "11001": "Veřejné podniky nefinanční",
    "11002": "Národní soukromé nefinanční podniky",
    "11003": "Nefinanční podniky pod zahraniční kontrolou",
    "12100": "Centrální banka (veřejná)",
    "12201": "Instituce přijímající vklady kromě centrální banky, veřejné",
    "12202": "Instituce přijímající vklady kromě centrální banky, národní soukromé",
    "12203": "Instituce přijímající vklady kromě centrální banky, pod zahraniční kontrolou",
    "12301": "Fondy peněžního trhu, veřejné",
    "12302": "Fondy peněžního trhu, národní soukromé",
    "12303": "Fondy peněžního trhu, pod zahraniční kontrolou",
    "12401": "Investiční fondy jiné než fondy peněžního trhu, veřejné",
    "12402": "Investiční fondy jiné než fondy peněžního trhu, národní soukromé",
    "12403": "Investiční fondy jiné než fondy peněžního trhu, pod zahraniční kontrolou",
    "12501": "Ostatní finanční zprostředkovatelé kromě pojišťovacích společností a penzijních "
    "fondů, veřejní",
    "12502": "Ostatní finanční zprostředkovatelé kromě pojišťovacích společností a penzijních "
    "fondů, národní soukromí",
    "12503": "Ostatní finanční zprostředkovatelé kromě pojišťovacích společností a penzijních "
    "fondů, pod zahraniční kontrolou",
    "12601": "Pomocné finanční instituce, veřejné",
    "12602": "Pomocné finanční instituce, národní soukromé",
    "12603": "Pomocné finanční instituce, pod zahraniční kontrolou",
    "12701": "Kaptivní finanční instituce a půjčovatelé peněz, veřejné",
    "12702": "Kaptivní finanční instituce a půjčovatelé peněz, národní soukromé",
    "12703": "Kaptivní finanční instituce a půjčovatelé peněz, pod zahraniční kontrolou",
    "12801": "Pojišťovací společnosti, veřejné",
    "12802": "Pojišťovací společnosti, národní soukromé",
    "12803": "Pojišťovací společnosti, pod zahraniční kontrolou",
    "12901": "Penzijní fondy, veřejné",
    "12902": "Penzijní fondy, národní soukromé",
    "12903": "Penzijní fondy, pod zahraniční kontrolou",
    "13110": "Ústřední vládní instituce (kromě fondů sociálního zabezpečení)",
    "13120": "Národní vládní instituce (kromě fondů sociálního zabezpečení)",
    "13130": "Místní vládní instituce (kromě fondů sociálního zabezpečení)",
    "13140": "Fondy sociálního zabezpečení",
    "14100": "Zaměstnavatelé",
    "14200": "Osoby samostatně výdělečně činné",
    "14300": "Zaměstnanci",
    "14410": "Příjemci důchodů z vlastnictví",
    "14420": "Příjemci penzí",
    "14430": "Příjemci ostatních transferů",
    "15002": "Neziskové instituce sloužící domácnostem, národní soukromé",
    "15003": "Neziskové instituce sloužící domácnostem, pod zahraniční kontrolou",
    "21110": "Členské státy eurozóny",
    "21120": "Členské státy mimo eurozónu",
    "21210": "Evropská centrální banka (ECB)",
    "21220": "Evropské orgány a instituce, kromě ECB",
    "22000": "Ostatní země a mezinárodní organizace - nerezidenti EU",
}


def targets() -> tuple[str, ...]:
    """Every BA0036 code the table can emit, sorted and without repeats."""
    codes = set(DIRECT.values())
    for row in (*DEPOSIT_TAKERS.values(), *OTHER_INTERMEDIARIES.values()):
        codes.update(row.values())
    return tuple(sorted(codes))


def missing_targets(codebooks: CodebookSet) -> tuple[str, ...]:
    """The table's targets the loaded codebook cannot emit (no CTS ID, or not a valid leaf)."""
    missing: list[str] = []
    for code in targets():
        try:
            codebooks.cts_id_for_esa(code)
        except (UnknownCodeError, MalformedCodeError):
            missing.append(code)
    return tuple(missing)


__all__ = [
    "DEPOSIT_TAKERS",
    "DIRECT",
    "NO_COUNTERPART",
    "OTHER_INTERMEDIARIES",
    "SECTOR_NAMES_CS",
    "DepositTaker",
    "Intermediary",
    "missing_targets",
    "targets",
]
