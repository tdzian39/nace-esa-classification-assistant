"""Prompt construction and the response schema. Versioned, because the cache depends on it.

The model is never asked to *produce* a code. It is given a shortlist and asked to choose,
and the JSON schema pins ``code`` to an enum of exactly those codes, so the provider itself
refuses anything else. The validation in :mod:`core.classify.llm` then re-checks it - the
guarantee is worth having twice, because it is the difference between a suggestion a
reviewer can trust and one they have to verify by hand.

Two things in the schema earn their place:

* ``sufficient_evidence`` - the model must be able to decline. An issuer the web could not
  describe has to come back as "I don't know", which sends MO to research it. A confident
  guess on no evidence is the failure mode that would cost the tool its credibility.
* ``justification`` in Czech, one sentence, tied to the evidence - so a reviewer can
  overrule the tool knowingly rather than accepting or rejecting a bare code.

:data:`PROMPT_VERSION` is part of the cache key. Change the wording, change the version, or
yesterday's answers keep being served for today's prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from core.classify.models import ESA, NACE, Candidate, CandidateSet, Kind
from core.classify.text import overlap_score, stems

#: Bump on ANY change to the wording or the schema below. It is part of the cache key.
# v2: candidate definitions are trimmed to a character budget (see MAX_DEFINITION_CHARS),
# which changes what the model is shown, so cached v1 answers must not be reused.
PROMPT_VERSION: Final[str] = "nace-esa/2"

#: Rough tokens per character for Czech/English mixed text in a BPE tokenizer. Used only for
#: budgeting and reporting, never for truncation.
_CHARS_PER_TOKEN: Final[int] = 4

#: Character budget for the definitions of ONE candidate.
#:
#: NACE_STAT carries every sub-activity of a division: 56 rows for oddíl 46 (Velkoobchod),
#: 47 for oddíl 47 - about 3,000 characters each, most of it about activities the issuer
#: plainly does not perform. A budget rather than a fixed count, because the cost is in the
#: handful of giant divisions; a typical division fits whole and is sent whole.
MAX_DEFINITION_CHARS: Final[int] = 1100


_KIND_SUBJECT: Final[dict[Kind, str]] = {
    NACE: (
        "dvoumístný oddíl klasifikace NACE (CZ-NACE), který nejlépe vystihuje "
        "ČINNOST emitenta - čím se zabývá"
    ),
    ESA: (
        "elementární sektor klasifikace ESA 2010 podle číselníku BA0036, který nejlépe "
        "vystihuje TYP INSTITUCE - čím emitent je a kdo jej ovládá, nikoli čím se zabývá"
    ),
}

_KIND_HINT: Final[dict[Kind, str]] = {
    NACE: (
        "Rozhoduj podle převažující činnosti. Definice oddílů obsahují i jejich podpoložky - "
        "shoda s podpoložkou je silný důvod pro daný oddíl."
    ),
    ESA: (
        "Nabídnuté kódy jsou varianty téhož typu instituce lišící se kontrolou "
        "(veřejné / soukromé národní / pod zahraniční kontrolou). Nejprve zvol správný typ "
        "instituce, teprve potom kontrolu podle toho, kdo emitenta vlastní. "
        "Pozor: subjekt, který financuje výhradně vlastní skupinu a nemá bankovní licenci, "
        "NENÍ banka."
    ),
}

SYSTEM_PROMPT: Final[str] = (
    "Jsi asistent Middle Office treasury české banky. Zařazuješ zahraniční emitenty "
    "cenných papírů do číselníků, které banka používá v systému CTS.\n\n"
    "Závazná pravidla:\n"
    "1. Vybírej VÝHRADNĚ z nabídnutého seznamu kandidátů. Kód, který v seznamu není, "
    "je nepřípustný.\n"
    "2. Pokud podklady nestačí k rozhodnutí, nastav sufficient_evidence na false a vrať "
    "prázdný seznam. Nehádej. Nepodložený odhad je horší než přiznaná nejistota.\n"
    "3. Vrať nejvýše tři kandidáty seřazené od nejpravděpodobnějšího.\n"
    "4. Ke každému uveď jednu větu česky, která se opírá o konkrétní údaj z podkladů.\n"
    "5. confidence 'high' jen tehdy, když podklady přímo podporují zařazení; "
    "'low', pokud jde spíše o dohad mezi několika možnostmi."
)


@dataclass(frozen=True, slots=True)
class Prompt:
    """One ready-to-send request: the two messages, the schema, and what it will cost."""

    kind: Kind
    system: str
    user: str
    schema: dict[str, Any]
    version: str = PROMPT_VERSION
    candidate_codes: tuple[str, ...] = ()

    @property
    def estimated_tokens(self) -> int:
        """Rough input size, for the cost line in reports. Never used to truncate."""
        return (len(self.system) + len(self.user)) // _CHARS_PER_TOKEN

    def describe(self) -> str:
        return (
            f"{self.kind} prompt {self.version}: {len(self.candidate_codes)} candidate(s), "
            f"~{self.estimated_tokens} input tokens"
        )


def response_schema(codes: tuple[str, ...], *, max_suggestions: int = 3) -> dict[str, Any]:
    """The JSON schema the provider must enforce.

    ``code`` is an enum of the supplied codes, so a code outside the shortlist is not
    something to detect afterwards - the provider cannot emit it. ``additionalProperties``
    is false and every property is required, which OpenAI's strict mode demands.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["sufficient_evidence", "picks"],
        "properties": {
            "sufficient_evidence": {
                "type": "boolean",
                "description": "False when the evidence does not support any choice.",
            },
            "picks": {
                "type": "array",
                "maxItems": max_suggestions,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "confidence", "justification"],
                    "properties": {
                        "code": {
                            "type": "string",
                            "enum": list(codes),
                            "description": "Must be one of the offered codes.",
                        },
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "justification": {
                            "type": "string",
                            "description": "Jedna věta česky, opřená o podklady.",
                        },
                    },
                },
            },
        },
    }


def select_definitions(
    candidate: Candidate, description: str, *, budget: int = MAX_DEFINITION_CHARS
) -> tuple[str, ...]:
    """The definitions worth sending for this candidate, within a character budget.

    The first is always kept: it is the division's own name, the canonical statement of what
    the code covers. Beyond that, the ordering depends on whether scoring can tell anything
    apart - and often it cannot, because the description is English and the codebook Czech,
    so every overlap is zero.

    * **Scores discriminate** - take the best-matching definitions first.
    * **Nothing scores** - keep codebook order and simply stop at the budget. Picking "the
      first six" by score in that case silently dropped the decisive sub-activity for a
      captive vehicle, which is the one line that justifies its division.

    Either way the kept set is restored to codebook order, so the model reads a definition
    rather than a ranking.
    """
    texts = [item.strip() for item in candidate.definitions if item.strip()]
    if not texts:
        return ()
    if sum(len(item) for item in texts) <= budget:
        return tuple(texts)

    head, tail = texts[0], texts[1:]
    query = stems(description)
    scores = [overlap_score(query, stems(item)) for item in tail]
    discriminating = any(score > 0 for score in scores)
    order = (
        sorted(range(len(tail)), key=lambda index: (-scores[index], index))
        if discriminating
        else list(range(len(tail)))
    )

    remaining = budget - len(head)
    keep: list[int] = []
    for index in order:
        cost = len(tail[index])
        if cost > remaining:
            continue
        remaining -= cost
        keep.append(index)
    return (head, *(tail[index] for index in sorted(keep)))


def _render_candidates(candidates: CandidateSet, description: str) -> str:
    """The shortlist as the model sees it: code, label, and the definitions that matter."""
    blocks: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        lines = [f"{index}. kód {candidate.code} — {candidate.label}"]
        seen: set[str] = set()
        for item in select_definitions(candidate, description):
            if item and item != candidate.label and item not in seen:
                seen.add(item)
                lines.append(f"   • {item}")
        blocks.append(chr(10).join(lines))
    return chr(10).join(blocks)


def build_prompt(
    candidates: CandidateSet,
    *,
    issuer_name: str | None,
    description: str,
    max_suggestions: int = 3,
) -> Prompt:
    """Build the prompt for one codebook.

    Only public information goes in: the issuer name, the web-derived or user-typed
    description, and codebook labels. Nothing from DWS may ever reach this function.
    """
    kind = candidates.kind
    parts = [
        f"Úkol: vyber {_KIND_SUBJECT[kind]}.",
        "",
        f"Emitent: {issuer_name or '(název neuveden)'}",
        "",
        "Podklady o emitentovi:",
        description.strip() or "(žádné podklady)",
        "",
        _KIND_HINT[kind],
        "",
        f"Kandidáti ({len(candidates)}) — vybírej pouze z nich:",
        _render_candidates(candidates, description),
    ]
    return Prompt(
        kind=kind,
        system=SYSTEM_PROMPT,
        user="\n".join(parts),
        schema=response_schema(candidates.codes, max_suggestions=max_suggestions),
        candidate_codes=candidates.codes,
    )
