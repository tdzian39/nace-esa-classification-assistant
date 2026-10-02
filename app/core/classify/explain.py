"""How a code was decided, in Czech: the page's "Jak rozhodl" line and the rules' reasons.

The pre-filter records its reasons in English ("keyword: banking", "register: ECB MFI list:
credit institution") - they are for logs, the CLI and the tests, and they stay that way. What
MO reads is rendered here: the same reasons in Czech, and one sentence saying who decided -
the model, a register, or the rules because the model did not. For a Czech issuer whose code
RES settled (2 Oct 2026) that sentence is "převzato z RES (ARES), stav k <datumAktualizace>".
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # proposal.py imports this module; the types are for the checker only
    from core.classify.models import CandidateSet, Classification
    from core.classify.proposal import Proposal

#: The hint table's notes (:data:`core.classify.hints.HINTS`) in Czech. A note missing here is
#: shown as it is, so a new hint can never make a reason disappear.
_NOTES_CS: Final[dict[str, str]] = {
    "banking": "bankovnictví",
    "insurance": "pojišťovnictví",
    "pensions": "penzijní fondy",
    "investment fund": "investiční fond",
    "money market fund": "fond peněžního trhu",
    "securitisation vehicle": "sekuritizační jednotka",
    "captive finance vehicle": "kaptivní finanční jednotka skupiny",
    "finance-vehicle name": "název finanční jednotky skupiny",
    "securities dealer": "obchodník s cennými papíry",
    "market infrastructure": "tržní infrastruktura (burza, clearing)",
    "asset manager": "správa aktiv",
    "lending / leasing": "úvěry a leasing",
    "specialised financial institution": "specializovaná finanční instituce",
    "government": "vláda, stát",
    "local government": "místní samospráva",
    "supranational institution": "nadnárodní instituce",
    "automotive": "automobilový průmysl",
    "pharma": "farmacie",
    "software": "software",
    "telecoms": "telekomunikace",
    "energy": "energetika",
    "oil & gas": "ropa a plyn",
    "real estate": "nemovitosti",
    "retail": "maloobchod",
    "wholesale": "velkoobchod",
    "air transport": "letecká doprava",
    "shipping": "lodní doprava",
    "transport support": "přístavy, sklady a logistika",
    "metals": "hutnictví",
    "chemicals": "chemie",
    "construction": "stavebnictví",
}

#: The register rules' notes (:data:`core.classify.hints.REGISTER_RULES`,
#: ``ESA_REGISTER_RULES``) in Czech; each already names its register.
_REGISTERS_CS: Final[dict[str, str]] = {
    "GLEIF: international organisation": "GLEIF: mezinárodní organizace",
    "GLEIF: government entity": "GLEIF: vládní instituce",
    "ISIN country code EU: an EU institution": "kód země EU v ISIN: instituce EU",
    "ECB MFI list: central bank": "seznam MFI ECB: centrální banka",
    "ECB MFI list: credit institution": "seznam MFI ECB: úvěrová instituce",
    "ECB MFI list: money market fund": "seznam MFI ECB: fond peněžního trhu",
    "ECB MFI list: other deposit-taking corporation": (
        "seznam MFI ECB: jiná instituce přijímající vklady"
    ),
    "ECB list of investment funds": "seznam investičních fondů ECB",
    "ECB list of FVCs": "seznam sekuritizačních jednotek (FVC) ECB",
    "ECB list of insurance corporations": "seznam pojišťoven ECB",
    "ECB list of pension funds": "seznam penzijních fondů ECB",
    "on no ECB list, no financial keyword": (
        "na žádném seznamu ECB a bez finančního klíčového slova"
    ),
    "not a credit institution on the ECB MFI list": "na seznamu MFI ECB není úvěrovou institucí",
    "RES legal form 205": "RES: právní forma 205 družstvo – spořitelní a úvěrní družstvo",
}

#: The RES reasons of :mod:`core.classify.residents`, which carry codes and RES's date.
_RES_SECTOR: Final[re.Pattern[str]] = re.compile(r"RES sector (\d+)(?:, as of ([\d-]+))?$")
_RES_NACE: Final[re.Pattern[str]] = re.compile(
    r"RES CZ-NACE (2025|2008) (\d+)(?:, as of ([\d-]+))?$"
)
_RES_LENDER: Final[re.Pattern[str]] = re.compile(r"RES CZ-NACE (\d+): lending$")
_AS_OF: Final[re.Pattern[str]] = re.compile(r"^register: RES .*?, as of ([\d-]+)$")


def _res_cs(rest: str) -> str | None:
    """A RES reason in Czech, or ``None`` when ``rest`` is not one."""
    if found := _RES_SECTOR.fullmatch(rest):
        when = f", stav k {found.group(2)}" if found.group(2) else ""
        return f"RES (ARES): institucionální sektor {found.group(1)}{when}"
    if found := _RES_NACE.fullmatch(rest):
        when = f", stav k {found.group(3)}" if found.group(3) else ""
        return f"RES (ARES): převažující činnost CZ-NACE {found.group(1)} {found.group(2)}{when}"
    if found := _RES_LENDER.fullmatch(rest):
        return (
            f"RES: převažující činnost CZ-NACE {found.group(1)} – finanční leasing nebo jiné "
            "poskytování úvěrů (64.91, 64.92)"
        )
    return None


def from_res(reasons: Sequence[str]) -> bool:
    """Whether a candidate's first reason is RES's (a resident issuer's register code)."""
    return bool(reasons) and reasons[0].startswith("register: RES ")


def reason_cs(reason: str) -> str:
    """One pre-filter reason in Czech: ``"keyword: banking"`` -> ``"klíčové slovo: bankovnictví"``."""
    head, _, rest = reason.partition(": ")
    if head == "keyword":
        return f"klíčové slovo: {_NOTES_CS.get(rest, rest)}"
    if head == "register":
        return _REGISTERS_CS.get(rest) or _res_cs(rest) or f"registr: {rest}"
    if head == "family":
        return f"skupina: {rest}"
    if reason == "baseline: residual sector":
        return "zbytkový sektor (nefinanční podniky)"
    if reason.startswith("text match "):
        return "shoda textu " + reason.removeprefix("text match ").replace(".", ",")
    return reason


def reasons_cs(reasons: Sequence[str]) -> str:
    """The reasons in Czech, joined as the page lists them."""
    return "; ".join(reason_cs(reason) for reason in reasons)


def decision_cs(
    proposal: Proposal | None, classification: Classification, candidates: CandidateSet
) -> str:
    """Who decided, the text after "Jak rozhodl:" - the model, a register, or the rules."""
    reason = classification.abstain_reason
    if proposal is None:
        if len(candidates) > 1 and all(from_res(item.reasons) for item in candidates):
            # A RES tie: the sector and its control digit are RES's, the institution type is not.
            return (
                "nerozhodnuto – RES (ARES) určuje sektor i typ kontroly, ne druh instituce; "
                "vyberte jeden z nabízených kódů" + (f" ({reason})" if reason else "") + "."
            )
        return f"nerozhodnuto – {reason or 'podklady k rozhodnutí nestačily'}."
    if proposal.basis == "model":
        return (
            f"model {classification.model or ''} – vybral z nabídky {len(candidates)} kódů "
            "zúženého číselníku podle popisu činnosti a údajů z registrů."
        )
    if proposal.overridden:
        return (
            "registr – určuje jediný kód, proto má přednost před modelem "
            f"(ten navrhl {proposal.overridden})."
        )
    if from_res(proposal.top.reasons):
        found = _AS_OF.match(proposal.top.reasons[0])
        when = f", stav k {found.group(1)}" if found else ""
        return f"převzato z RES (ARES){when} – kód určuje registr, model se neptal."
    if reason:
        return f"pravidla, protože model kód nevybral ({reason})."
    return "pravidla."


__all__ = ["decision_cs", "from_res", "reason_cs", "reasons_cs"]
