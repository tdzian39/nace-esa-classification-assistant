"""Keyword hints and the ESA family structure: the deterministic half of the pre-filter.

Two jobs.

**Keyword hints.** A small, reviewable table of trigger words in Czech *and* English that
force an obvious code onto the shortlist regardless of what the lexical scorer thought.
It is the safety net against a bad narrowing run, and it is the part MO can read and argue
with. Kept small on purpose: every entry is a claim someone has to stand behind.

**ESA families.** The BA0036 rest-of-world block is not a flat list of 56 codes - it is a
grid of *entity family* x *control type*:

    Banky                      veřejné | soukromé národní | pod zahraniční kontrolou
    Pojišťovací společnosti    veřejné | soukromé národní | pod zahraniční kontrolou
    Kaptivní finanční inst.    veřejné | soukromé národní | pod zahraniční kontrolou

That structure is not hardcoded here: it is *derived* from the codebook names by stripping
the control suffix, so a codebook update reshapes it automatically. It matters because the
two axes are answered by different evidence - the family from what the entity does, the
control from who owns it - and a filter that knows this can offer the whole family and let
the classifier settle the control digit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from core.classify.text import contains_phrase, fold

#: Who controls the entity. The last axis of the BA0036 grid.
Control = Literal["public", "private_national", "foreign_controlled", "unknown"]

#: Control suffixes as the codebook spells them, longest first so the more specific
#: "soukromé národní" is stripped before the bare "soukromé".
_CONTROL_SUFFIXES: Final[tuple[tuple[str, Control], ...]] = (
    # "Nefinanční podniky soukromé pod zahraniční kontrolou" spells the control as
    # "soukromé pod zahraniční kontrolou". Strip that whole phrase, or the sector splits
    # off into a family of its own and stops being a control variant of "nefinanční
    # podniky" - which is the most common sector for a foreign corporate issuer.
    ("soukrome pod zahranicni kontrolou", "foreign_controlled"),
    ("soukromi pod zahranicni kontrolou", "foreign_controlled"),
    ("soukroma pod zahranicni kontrolou", "foreign_controlled"),
    ("pod zahranicni kontrolou", "foreign_controlled"),
    ("pod zahr. kontr.", "foreign_controlled"),
    ("pod zahranicni kontr.", "foreign_controlled"),
    ("soukrome narodni", "private_national"),
    ("soukromi narodni", "private_national"),
    ("soukroma narodni", "private_national"),
    ("soukr. nar.", "private_national"),
    ("verejne", "public"),
    ("verejni", "public"),
    ("verejna", "public"),
)

#: Trailing punctuation left behind once a suffix is removed ("Specializované inst. - ").
_TRAILING_JUNK: Final[re.Pattern[str]] = re.compile(r"[\s,;:\-–—]+$")


def split_control(name: str) -> tuple[str, Control]:
    """Split a BA0036 name into its family and its control type.

    ``"Banky pod zahraniční kontrolou"`` -> ``("banky", "foreign_controlled")``.
    A name with no recognised suffix (``"Centrální banka"``) keeps its whole name as the
    family and reports ``"unknown"`` - correct, because those sectors have no control axis.
    """
    folded = fold(name).strip()
    for suffix, control in _CONTROL_SUFFIXES:
        if folded.endswith(suffix):
            family = _TRAILING_JUNK.sub("", folded[: -len(suffix)])
            return (family or folded), control
    return _TRAILING_JUNK.sub("", folded), "unknown"


@dataclass(frozen=True, slots=True)
class EsaFamily:
    """One row of the BA0036 grid: an entity type and its control variants.

    Attributes:
        key: Folded family name, e.g. ``"banky"``.
        display: The family name as the codebook spells it, for prompts and logs.
        members: ``{control: ESA key}`` for every variant present in the codebook.
    """

    key: str
    display: str
    members: Mapping[Control, str]

    @property
    def codes(self) -> tuple[str, ...]:
        """Every ESA key in this family, most-likely control first.

        Foreign control leads because Tool 1 classifies foreign issuers; the ordering only
        breaks ties when the shortlist is truncated.
        """
        order: tuple[Control, ...] = ("foreign_controlled", "private_national", "public", "unknown")
        return tuple(self.members[c] for c in order if c in self.members)


def build_families(sectors: Iterable[tuple[str, str]]) -> dict[str, EsaFamily]:
    """Group ``(esa_key, name)`` pairs into families by their control suffix.

    Pass **one residency block at a time**. The resident and rest-of-world blocks use the
    same names ("Banky pod zahraniční kontrolou" is both 1221300 and 2002213), so a family
    holds one code per control slot and a mixed list would silently keep only the first.
    :class:`~core.classify.candidates.EsaCandidateFilter` filters by prefix before calling
    this, which is the only supported use.

    The display name is the first spelling of the family without its control suffix
    ("Banky"), so a candidate's reason "family: Banky" does not name one control variant
    while another is proposed.
    """
    grouped: dict[str, dict[Control, str]] = {}
    display: dict[str, str] = {}
    for key, name in sectors:
        family, control = split_control(name)
        if not family:
            continue
        grouped.setdefault(family, {})
        # Keep the first code seen for a given control slot; codebooks do not repeat them.
        grouped[family].setdefault(control, key)
        display.setdefault(family, _family_display(name, family))
    return {
        family: EsaFamily(key=family, display=display[family], members=members)
        for family, members in grouped.items()
    }


def _family_display(name: str, family: str) -> str:
    """``name`` cut to its family part, accents and case kept: "Banky veřejné" -> "Banky".

    Folding strips accents without changing the length of precomposed text, so the folded
    family is a prefix of the same length; anything else keeps the whole name.
    """
    stripped = name.strip()
    if len(fold(stripped)) != len(stripped) or not fold(stripped).startswith(family):
        return stripped
    return _TRAILING_JUNK.sub("", stripped[: len(family)]) or stripped


# ---------------------------------------------------------------------------------------
# Keyword hints. Each entry: trigger words (cs + en) -> what they imply.
# Reviewable by MO. Codes are validated against the loaded codebook at startup, so a hint
# naming a code that no longer exists fails loudly instead of silently doing nothing.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hint:
    """A keyword rule.

    Attributes:
        triggers: Words that fire it, matched on word boundaries with a short suffix
            tolerance, so ``"bank"`` catches ``banks``, ``banky``, ``banking``.
        nace: NACE divisions to force onto the shortlist.
        esa_families: ESA family keys to force onto the shortlist (all control variants).
        note: Why, in one phrase, recorded on the candidate as its reason.
    """

    triggers: tuple[str, ...]
    nace: tuple[str, ...] = ()
    esa_families: tuple[str, ...] = ()
    note: str = ""

    def matches(self, text: str) -> bool:
        return any(contains_phrase(text, trigger) for trigger in self.triggers)


#: The hint table. Small and blunt by design.
HINTS: Final[tuple[Hint, ...]] = (
    Hint(
        triggers=("bank", "banka", "banking", "credit institution", "úvěrová instituce"),
        nace=("64",),
        esa_families=("banky",),
        note="banking",
    ),
    Hint(
        triggers=("insurance", "insurer", "pojišťovna", "pojištění", "reinsurance", "zajišťovna"),
        nace=("65",),
        esa_families=("pojistovaci spolecnosti (ic)",),
        note="insurance",
    ),
    Hint(
        triggers=("pension", "penzijní", "penzijni fond", "retirement"),
        nace=("65",),
        esa_families=("penzijni fondy (pf)",),
        note="pensions",
    ),
    Hint(
        triggers=("investment fund", "mutual fund", "investiční fond", "ucits", "sicav"),
        nace=("64",),
        esa_families=("investicni fondy jine nez fondy penezniho trhu",),
        note="investment fund",
    ),
    Hint(
        # "Money market sub-fund of the ... SICAV" is how a Luxembourg umbrella's MMF reads.
        triggers=(
            "money market fund",
            "money-market fund",
            "money market sub-fund",
            "fond peněžního trhu",
        ),
        nace=("64",),
        esa_families=("fondy penezniho trhu",),
        note="money market fund",
    ),
    Hint(
        triggers=(
            "securitisation",
            "securitization",
            "sekuritizace",
            "covered bond",
            # Real prospectuses rarely use the word "securitisation"; they name the
            # instrument. Added after the golden case "securitisation-vehicle", whose
            # description says "asset-backed notes" and matched nothing.
            "asset-backed",
            "asset backed",
            "mortgage-backed",
            "mortgage backed",
            "rmbs",
            "cmbs",
            "abs notes",
            "receivables",
        ),
        nace=("64",),
        esa_families=("ucelove financni instituce pro sekuritizaci aktiv",),
        note="securitisation vehicle",
    ),
    Hint(
        triggers=(
            "captive",
            "kaptivní",
            "funding vehicle",
            "financing vehicle",
            "spv",
            "special purpose",
            "účelová finanční",
            "intra-group",
            "treasury company",
            # "Dutch financing company of Shell plc, with no staff" - how the golden
            # descriptions of Eurobond vehicles read, whose ISINs GLEIF does not map.
            "financing company",
            "finance company",
        ),
        nace=("64",),
        esa_families=("kaptivni financni instituce a pujcovatele penez",),
        note="captive finance vehicle",
    ),
    Hint(
        # The captive's *name*, when its description is missing. Group finance vehicles
        # are registered as '<Group> Finance N.V.', '<Group> International Finance B.V.',
        # '<Group> Funding B.V.' or '<Group> Motor Credit Corporation' (GLEIF, 2026-09-22:
        # BMW Finance N.V., Volkswagen International Finance N.V., Deutsche Telekom
        # International Finance B.V., Toyota Motor Credit Corporation - all category
        # GENERAL, so the register does not say 'captive'; the name does). The phrase
        # includes the legal-form suffix on purpose: bare 'finance' fires on half of
        # section K, but 'Finance N.V.' is how a Dutch funding vehicle is spelled.
        triggers=(
            "finance n.v.",
            "finance b.v.",
            "finance nv",
            "finance bv",
            "finance s.a.",
            "funding n.v.",
            "funding b.v.",
            "funding corporation",
            "international finance",
            "finance corporation",
            "credit corporation",
            "capital corporation",
            "treasury b.v.",
            # Dutch for "financing company": Siemens Financieringsmaatschappij N.V.
            "financieringsmaatschappij",
        ),
        nace=("64",),
        esa_families=("kaptivni financni instituce a pujcovatele penez",),
        note="finance-vehicle name",
    ),
    Hint(
        triggers=("holding", "holdingová"),
        nace=("64", "70"),
        esa_families=("kaptivni financni instituce a pujcovatele penez",),
        note="holding company",
    ),
    Hint(
        triggers=("broker", "dealer", "obchodník s cennými papíry", "securities trading"),
        nace=("66",),
        esa_families=("obchodnici s cennymi papiry a derivaty",),
        note="securities dealer",
    ),
    Hint(
        # The lender, dealer, securitisation and specialised-institution families have no
        # Popis in BA0036_2024_jen_validni.xlsx (nor at the CNB), so the lexical scorer has
        # only their names to go on; these triggers are the rest. Factoring and hire
        # purchase are ESA 2010's own examples of lending corporations (S.125.3).
        triggers=(
            "leasing",
            "lease",
            "consumer credit",
            "spotřebitelský úvěr",
            "lending",
            "factoring",
            "faktoring",
            "hire purchase",
            "splátkový prodej",
        ),
        nace=("64",),
        esa_families=("financni instituce poskytujici uvery",),
        note="lending / leasing",
    ),
    Hint(
        # ESA 2010's specialised financial corporations (S.125.4): venture and development
        # capital companies, export/import financing companies. The one family without a
        # Popis that no other hint reached.
        triggers=(
            "venture capital",
            "development capital",
            "rizikový kapitál",
            "rizikového kapitálu",
            "export credit",
            "export finance",
            "export financing",
            "exportní úvěr",
            "vývozní úvěr",
            "financování vývozu",
        ),
        nace=("64",),
        esa_families=("specializovane financni instituce",),
        note="specialised financial institution",
    ),
    Hint(
        # NACE 84 because a government borrower is public administration whatever it
        # finances. The GLEIF category RESIDENT_GOVERNMENT_ENTITY and the OpenFIGI sector
        # Govt reach this through the fact sheet's glosses ("government", "sovereign").
        triggers=("government", "sovereign", "ministry", "vláda", "ministerstvo", "státní"),
        nace=("84",),
        esa_families=("ustredni vladni instituce", "narodni vladni instituce"),
        note="government",
    ),
    Hint(
        triggers=("municipality", "city of", "region of", "obec", "kraj", "město"),
        nace=("84",),
        esa_families=("mistni vladni instituce",),
        note="local government",
    ),
    Hint(
        # NACE 99 (extraterritorial organisations) - the division for the EU, the World
        # Bank group and the other international bodies, development banks included. The
        # GLEIF category INTERNATIONAL_ORGANIZATION reaches it through its gloss.
        triggers=(
            "development bank",
            "rozvojová banka",
            "supranational",
            "nadnárodní",
            "international organisation",
            "international organization",
            "mezinárodní organizace",
            "intergovernmental",
            "european investment bank",
            "world bank",
        ),
        nace=("99",),
        esa_families=("mezinarodni rozvojove banky", "ostatni mezinarodni instituce"),
        note="supranational institution",
    ),
    # --- non-financial issuers: NACE only, ESA follows from "nefinanční podniky" ---------
    Hint(
        triggers=("automotive", "motor vehicle", "automobil", "vozidel", "carmaker", "automaker"),
        nace=("29",),
        note="automotive",
    ),
    Hint(triggers=("pharmaceutical", "farmaceutick", "léčiv"), nace=("21",), note="pharma"),
    Hint(
        triggers=("software", "it services", "informační technolog"), nace=("62",), note="software"
    ),
    Hint(
        triggers=("telecommunication", "telekomunikac", "mobile operator"),
        nace=("61",),
        note="telecoms",
    ),
    Hint(
        triggers=("electricity", "power generation", "elektřin", "energetick"),
        nace=("35",),
        note="energy",
    ),
    Hint(
        triggers=("oil", "gas", "petroleum", "ropa", "zemního plynu"),
        nace=("06", "19"),
        note="oil & gas",
    ),
    Hint(
        triggers=("real estate", "nemovitost", "property investment"),
        nace=("68",),
        note="real estate",
    ),
    Hint(triggers=("retail", "maloobchod", "supermarket"), nace=("47",), note="retail"),
    Hint(triggers=("wholesale", "velkoobchod"), nace=("46",), note="wholesale"),
    Hint(
        triggers=("airline", "letecká doprava", "air transport"), nace=("51",), note="air transport"
    ),
    Hint(triggers=("steel", "ocel", "metallurg", "hutnictv"), nace=("24",), note="metals"),
    Hint(triggers=("chemical", "chemick"), nace=("20",), note="chemicals"),
    Hint(
        triggers=("construction", "stavebnictv", "stavební"), nace=("41", "42"), note="construction"
    ),
)


def matching_hints(text: str) -> tuple[Hint, ...]:
    """Every hint whose triggers appear in ``text``."""
    return tuple(hint for hint in HINTS if hint.matches(text))


def hinted_nace(text: str) -> dict[str, str]:
    """``{NACE division: reason}`` implied by the keyword table."""
    out: dict[str, str] = {}
    for hint in matching_hints(text):
        for code in hint.nace:
            out.setdefault(code, f"keyword: {hint.note}")
    return out


def hinted_esa_families(text: str) -> dict[str, str]:
    """``{ESA family key: reason}`` implied by the keyword table."""
    out: dict[str, str] = {}
    for hint in matching_hints(text):
        for family in hint.esa_families:
            out.setdefault(family, f"keyword: {hint.note}")
    return out


# ---------------------------------------------------------------------------------------
# Register rules. A register fact outranks any keyword: the European Investment Bank's
# name says "bank", but GLEIF files it as an international organisation, and the NACE
# division follows what the entity is, not what it is called. Matched on the bracketed
# category the fact sheet carries ("... [INTERNATIONAL_ORGANIZATION]"), so only the
# register can fire them, never prose. OpenFIGI's Govt sector is deliberately not one:
# Kommuninvest, a Swedish bank, issues bonds OpenFIGI files as Govt.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterRule:
    """A GLEIF entity category that settles the NACE division.

    Attributes:
        category: The GLEIF value as the fact sheet brackets it.
        nace: The NACE division it settles.
        note: Why, recorded on the candidate as its reason.
    """

    category: str
    nace: str
    note: str

    def matches(self, text: str) -> bool:
        return f"[{self.category}]" in text


REGISTER_RULES: Final[tuple[RegisterRule, ...]] = (
    RegisterRule("INTERNATIONAL_ORGANIZATION", "99", "GLEIF: international organisation"),
    RegisterRule("RESIDENT_GOVERNMENT_ENTITY", "84", "GLEIF: government entity"),
)


def register_nace(text: str) -> dict[str, str]:
    """``{NACE division: reason}`` settled by a GLEIF category in the fact sheet."""
    return {rule.nace: f"register: {rule.note}" for rule in REGISTER_RULES if rule.matches(text)}
