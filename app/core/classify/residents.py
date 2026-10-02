"""Codes for a Czech (resident) issuer, taken from RES: the register rules of 2 Oct 2026.

For a resident issuer RES already records what this tool otherwise works out: the prevailing
activity (CZ-NACE) and the institutional sector with its control digit (ČSÚ's ESA 2010). MO
asked (through Reporting, 2 Oct 2026) that the codes then come from RES, so that they need
not care whether an issuer is resident; Jakub decided the same day. This module turns RES's
answer into the candidates that settle each axis, as ``register:`` reasons:

* **NACE** - the division of RES's prevailing CZ-NACE 2025 code, resolved through
  :meth:`~core.codebooks.models.CodebookSet.cts_id_for_nace` (the one truncation point; the
  full code stays in the reason). With no 2025 code the 2008 one, with a note - except where
  its division is not in the CTS list (45, which CZ-NACE 2025 dropped). No code, no rule.
* **ESA** - the BA0036 resident code of :mod:`core.codebooks.res_esa`. S.122 and S.125 also
  need the institution type, read from structured data only (never from the name): legal
  form 205 = savings and credit cooperative; the ECB MFI list's "credit institution" = bank,
  else (the lists read for the LEI) another deposit-taker; the ECB FVC list = securitisation;
  a RES NACE class 64.91 or 64.92 (financial leasing, other credit granting) = lender. When the
  data cannot tell, the possible codes **tie** at RES's control digit, and the model - or MO -
  picks among them alone.

RES wins over the model and over GLEIF categories for a resident: an axis RES settles is not
sent to the model at all (:mod:`core.suggest`). Where an ECB list files the issuer in another
sector than RES does (Česká exportní banka and Národní rozvojová banka are credit institutions
there, EGAP an insurer; RES has all three in 13110, central government), the code stays RES's
and a note says the registers disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from core.codebooks.errors import MalformedCodeError, UnknownCodeError
from core.codebooks.res_esa import (
    DEPOSIT_TAKERS,
    DIRECT,
    OTHER_INTERMEDIARIES,
    SECTOR_NAMES_CS,
)

if TYPE_CHECKING:  # pragma: no cover
    from core.codebooks.models import CodebookSet
    from core.sources.ares import ResRecord
    from core.sources.identity import IssuerIdentity

#: Legal form 205 (družstvo): a deposit-taking cooperative is a spořitelní a úvěrní družstvo.
COOPERATIVE: Final[str] = "205"

#: RES NACE classes of lenders (CZ-NACE 2025 = 2008 here): 64.91 financial leasing, 64.92 other
#: credit granting (64921-64929), both in NACE_STAT.xlsx. ESA 2010 2.86: S.125.3.
LENDER_CLASSES: Final[tuple[str, ...]] = ("6491", "6492")

#: The control digit (the RES code's last) in words.
CONTROL_CS: Final[Mapping[str, str]] = {
    "1": "veřejné",
    "2": "národní soukromé",
    "3": "pod zahraniční kontrolou",
}

#: Which RES sectors each ECB list belongs to (by prefix), for the disagreement note.
_ECB_SECTORS: Final[Mapping[str, tuple[str, str]]] = {
    "ECB_MFI:CENTRAL_BANK": ("12100", "jako centrální banku"),
    "ECB_MFI:CREDIT_INSTITUTION": ("1220", "jako úvěrovou instituci"),
    "ECB_MFI:OTHER_INSTITUTION": ("1220", "jako jinou instituci přijímající vklady"),
    "ECB_MFI:MONEY_MARKET_FUND": ("1230", "jako fond peněžního trhu"),
    "ECB_IF": ("1240", "jako investiční fond"),
    "ECB_FVC": ("1250", "jako sekuritizační jednotku"),
    "ECB_IC": ("1280", "jako pojišťovnu"),
    "ECB_PF": ("1290", "jako penzijní fond"),
}


@dataclass(frozen=True, slots=True)
class ResidentRules:
    """What RES settles for one resident issuer.

    Attributes:
        nace: ``{division: reasons}`` - empty, or the one division RES settles.
        esa: ``{BA0036 code: reasons}`` - empty, one settled code, or a tie of several.
        notes: Czech sentences for the page: what RES could not settle, and why.
    """

    nace: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    esa: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def nace_settled(self) -> bool:
        return len(self.nace) == 1

    @property
    def esa_settled(self) -> bool:
        return len(self.esa) == 1

    @property
    def esa_tied(self) -> bool:
        return len(self.esa) > 1


#: Nothing settled: no RES record (or not a resident).
NO_RULES: Final[ResidentRules] = ResidentRules()


def resident_rules(identity: IssuerIdentity, codebooks: CodebookSet) -> ResidentRules:
    """The NACE division and the BA0036 code(s) RES settles for ``identity``."""
    res = identity.res_record
    if res is None:
        return NO_RULES
    notes: list[str] = []
    nace = _nace_rule(res, codebooks, notes)
    esa = _esa_rule(res, identity, codebooks, notes)
    disagreement = _disagreement(res, identity)
    if disagreement:
        notes.append(disagreement)
    return ResidentRules(nace=nace, esa=esa, notes=tuple(notes))


def _as_of(res: ResRecord) -> str:
    return f", as of {res.as_of}" if res.as_of else ""


def _nace_rule(
    res: ResRecord, codebooks: CodebookSet, notes: list[str]
) -> dict[str, tuple[str, ...]]:
    """RES's prevailing activity -> its CTS division, keeping the full code in the reason."""
    if res.nace:
        code, edition = res.nace, "2025"
    elif res.nace_2008:
        code, edition = res.nace_2008, "2008"
    else:
        notes.append("NACE: RES neuvádí převažující činnost – kód vybírá klasifikátor.")
        return {}
    try:
        division = codebooks.cts_id_for_nace(code).key
    except (UnknownCodeError, MalformedCodeError):
        notes.append(
            f"NACE: oddíl kódu CZ-NACE {edition} {code} z RES v číselníku CTS (CZ-NACE 2025) "
            "není – kód vybírá klasifikátor."
        )
        return {}
    if edition == "2008":
        notes.append(
            f"NACE: RES neuvádí činnost podle CZ-NACE 2025, převzat oddíl kódu CZ-NACE 2008 "
            f"{code} – ověřte ho."
        )
    return {division: (f"register: RES CZ-NACE {edition} {code}{_as_of(res)}",)}


def _esa_rule(
    res: ResRecord, identity: IssuerIdentity, codebooks: CodebookSet, notes: list[str]
) -> dict[str, tuple[str, ...]]:
    """RES's sector -> the BA0036 resident code, or the codes that tie when the type is unknown."""
    sector = res.sector
    if sector is None:
        notes.append(
            "ESA: RES neuvádí institucionální sektor – kód vybírá klasifikátor z rezidentských "
            "kódů."
        )
        return {}
    base = f"register: RES sector {sector}{_as_of(res)}"
    if sector in DIRECT:
        codes: dict[str, tuple[str, ...]] = {DIRECT[sector]: (base,)}
    elif sector in DEPOSIT_TAKERS:
        codes = _deposit_taker(sector, res, identity, base, notes)
    elif sector in OTHER_INTERMEDIARIES:
        codes = _intermediary(sector, res, identity, base, notes)
    else:
        name = SECTOR_NAMES_CS.get(sector)
        notes.append(
            f"ESA: sektor {sector}" + (f" – {name} –" if name else "") + " z RES nemá v BA0036 "
            "rezidentský kód – kód vybírá klasifikátor z rezidentských kódů."
        )
        return {}
    return _emittable(codes, codebooks, notes)


def _deposit_taker(
    sector: str,
    res: ResRecord,
    identity: IssuerIdentity,
    base: str,
    notes: list[str],
) -> dict[str, tuple[str, ...]]:
    """S.122: a cooperative by legal form, a bank or not by the ECB MFI list, else a tie."""
    row = DEPOSIT_TAKERS[sector]
    if res.legal_form == COOPERATIVE and "cooperative" in row:
        return {row["cooperative"]: (base, "register: RES legal form 205")}
    if res.legal_form != COOPERATIVE and identity.ecb_known:
        entry = identity.ecb_entry
        if entry is not None and entry.code == "ECB_MFI:CREDIT_INSTITUTION":
            return {row["bank"]: (base, "register: ECB MFI list: credit institution")}
        if entry is not None and entry.code == "ECB_MFI:OTHER_INSTITUTION":
            return {
                row["other"]: (base, "register: ECB MFI list: other deposit-taking corporation")
            }
        return {row["other"]: (base, "register: not a credit institution on the ECB MFI list")}
    if res.legal_form == COOPERATIVE:
        why = "BA0036 nemá veřejné spořitelní a úvěrní družstvo"
    elif identity.lei is None:
        why = "banku od jiné instituce rozliší seznam MFI ECB podle LEI a emitent LEI nemá"
    else:
        why = "banku od jiné instituce rozliší seznam MFI ECB, který tu není načtený"
    tied = sorted((row["bank"], row["other"]))
    notes.append(
        f"ESA: RES uvádí sektor {sector} (instituce přijímající vklady, "
        f"{CONTROL_CS.get(sector[-1], sector[-1])}), ne druh instituce; {why} – vyberte "
        f"jeden z kódů {', '.join(tied)}."
    )
    return {code: (base,) for code in tied}


def _intermediary(
    sector: str,
    res: ResRecord,
    identity: IssuerIdentity,
    base: str,
    notes: list[str],
) -> dict[str, tuple[str, ...]]:
    """S.125: securitisation by the ECB FVC list, a lender by its NACE class, else a tie."""
    row = OTHER_INTERMEDIARIES[sector]
    entry = identity.ecb_entry
    if entry is not None and entry.code == "ECB_FVC":
        return {row["securitisation"]: (base, "register: ECB list of FVCs")}
    nace = res.nace or res.nace_2008
    if nace and nace.startswith(LENDER_CLASSES):
        return {row["lender"]: (base, f"register: RES CZ-NACE {nace}: lending")}
    tied = sorted(row.values())
    notes.append(
        f"ESA: RES uvádí sektor {sector} (ostatní finanční zprostředkovatelé, "
        f"{CONTROL_CS.get(sector[-1], sector[-1])}), ne jejich podtyp (sekuritizace, obchodníci "
        "s cennými papíry, poskytovatelé úvěrů, specializované instituce) a registry ho neurčí "
        f"– vyberte jeden z kódů {', '.join(tied)}."
    )
    return {code: (base,) for code in tied}


def _emittable(
    codes: dict[str, tuple[str, ...]], codebooks: CodebookSet, notes: list[str]
) -> dict[str, tuple[str, ...]]:
    """Only codes the loaded codebook can emit; a missing one is said (W_RES_ESA_TARGET_MISSING)."""
    kept: dict[str, tuple[str, ...]] = {}
    for code, reasons in codes.items():
        try:
            codebooks.cts_id_for_esa(code)
        except (UnknownCodeError, MalformedCodeError):
            notes.append(f"ESA: kód BA0036 {code} z převodu RES v číselníku CTS není.")
            continue
        kept[code] = reasons
    return kept


def _disagreement(res: ResRecord, identity: IssuerIdentity) -> str | None:
    """A note when an ECB list files the issuer in another sector than RES does."""
    entry = identity.ecb_entry
    if entry is None or not res.sector:
        return None
    expected = _ECB_SECTORS.get(entry.code)
    if expected is None or res.sector.startswith(expected[0]):
        return None
    name = SECTOR_NAMES_CS.get(res.sector)
    return (
        f"ESA: seznam ECB ({entry.as_of}) vede emitenta {expected[1]} [{entry.code}], RES ho "
        f"řadí do sektoru {res.sector}" + (f" {name}" if name else "") + " – kód je převzat "
        "z RES, rozpor registrů ověřte."
    )


__all__ = [
    "COOPERATIVE",
    "CONTROL_CS",
    "LENDER_CLASSES",
    "NO_RULES",
    "ResidentRules",
    "resident_rules",
]
