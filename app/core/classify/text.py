"""Text primitives for the candidate pre-filter: folding, stemming and overlap scoring.

Deliberately crude. The pre-filter only has to get the right code *onto* a shortlist of
ten or so; deciding between them is the classifier's job. Optimising this for precision
would be effort spent on the wrong stage.

Two problems it does have to survive:

* **Czech morphology.** ``finanční``, ``finančních`` and ``finance`` must match. A full
  stemmer is overkill, so a token is truncated to its first :data:`STEM_LENGTH` characters
  after accents are stripped - enough for declension, short enough to stay cheap.
* **Two languages.** The codebooks are Czech; a description found on the web for a foreign
  issuer will usually be English. Lexical overlap across that gap is near zero, which is
  why this scorer is the *safety net* and the model-based narrowing is the primary path.
  The keyword table in :mod:`core.classify.hints` bridges the common cases in both languages.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Final

#: A token is truncated to this many characters to stand in for stemming.
STEM_LENGTH: Final[int] = 6

#: Tokens shorter than this carry no signal ("a", "je", "of").
MIN_TOKEN_LENGTH: Final[int] = 3

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-z]+")

#: Czech and English function words, plus the filler that appears in almost every codebook
#: label ("činnost", "ostatní", "jiné") and would otherwise match everything.
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        # Czech
        "a",
        "an",
        "aby",
        "ale",
        "ani",
        "az",
        "bez",
        "by",
        "byl",
        "byla",
        "bylo",
        "byt",
        "ci",
        "co",
        "coz",
        "do",
        "dle",
        "gg",
        "i",
        "jak",
        "jako",
        "je",
        "jeho",
        "jej",
        "jeji",
        "jejich",
        "jen",
        "jina",
        "jine",
        "jineho",
        "jinych",
        "jsou",
        "jsme",
        "k",
        "kde",
        "kdy",
        "ke",
        "kolem",
        "krome",
        "ktera",
        "ktere",
        "kteri",
        "ktery",
        "kterych",
        "kterym",
        "na",
        "nad",
        "nebo",
        "nejsou",
        "nez",
        "ni",
        "nikoli",
        "o",
        "od",
        "pod",
        "podle",
        "pouze",
        "pro",
        "proto",
        "pri",
        "s",
        "se",
        "si",
        "tak",
        "take",
        "tato",
        "te",
        "tedy",
        "ten",
        "tento",
        "to",
        "toho",
        "tohoto",
        "tom",
        "tomto",
        "u",
        "v",
        "vcetne",
        "ve",
        "vsak",
        "vsech",
        "vsechny",
        "z",
        "za",
        "ze",
        "zejmena",
        # very common codebook filler
        "cinnost",
        "cinnosti",
        "ostatni",
        "jednotky",
        "jednotka",
        "subsektor",
        "sektor",
        "zahrnuje",
        "patri",
        "uvedene",
        "souvisejici",
        "souvis",
        "vyroba",
        # English
        "the",
        "and",
        "or",
        "of",
        "for",
        "in",
        "on",
        "at",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "as",
        "that",
        "this",
        "these",
        "those",
        "its",
        "it",
        "from",
        "which",
        "other",
        "such",
        "than",
        "into",
        "also",
        "any",
        "all",
        "company",
        "companies",
        "group",
        "limited",
        "ltd",
        "plc",
        "inc",
    }
)


def fold(text: str) -> str:
    """Lower-case, accent-stripped form: ``"Finanční"`` -> ``"financni"``."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def tokenize(text: str) -> list[str]:
    """Folded word tokens, stopwords and very short words dropped."""
    return [
        token
        for token in _WORD_RE.findall(fold(text))
        if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS
    ]


def stem(token: str) -> str:
    """Crude stem: the first :data:`STEM_LENGTH` characters."""
    return token[:STEM_LENGTH]


def stems(text: str) -> set[str]:
    """The distinct stems of ``text``."""
    return {stem(token) for token in tokenize(text)}


def inverse_document_frequency(documents: Sequence[Iterable[str]]) -> dict[str, float]:
    """IDF over pre-tokenised documents.

    Weights down stems that appear in most codebook entries (``financ`` across the whole
    of section K) and up the ones that actually discriminate (``pojist``, ``leasin``).
    """
    total = max(1, len(documents))
    counts: dict[str, int] = {}
    for document in documents:
        for token in set(document):
            counts[token] = counts.get(token, 0) + 1
    return {token: math.log(1 + total / count) for token, count in counts.items()}


def overlap_score(
    query: set[str], document: set[str], weights: dict[str, float] | None = None
) -> float:
    """Weighted overlap of two stem sets, normalised by the query's own weight.

    Returns 0.0 when nothing matches and 1.0 when every weighted stem of the query appears
    in the document. Normalising by the query (not the document) keeps a long codebook
    definition from being penalised for being thorough.
    """
    if not query:
        return 0.0
    shared = query & document
    if not shared:
        return 0.0
    if weights is None:
        return len(shared) / len(query)
    total = sum(weights.get(token, 1.0) for token in query)
    if total <= 0:
        return 0.0
    return sum(weights.get(token, 1.0) for token in shared) / total


def contains_phrase(text: str, phrase: str) -> bool:
    """Whether ``phrase`` occurs in ``text``, both folded, on word boundaries.

    Word boundaries matter: without them ``"bank"`` would fire on ``"Banka"`` (wanted) but
    also on ``"embankment"`` (not), and Czech ``"fond"`` would match inside ``"fondue"``.
    A trailing-suffix match is allowed, so ``"bank"`` still fires on ``"banky"`` and
    ``"banking"``.
    """
    folded_text = fold(text)
    folded_phrase = fold(phrase)
    if not folded_phrase:
        return False
    pattern = r"(?<![0-9a-z])" + re.escape(folded_phrase) + r"[a-z]{0,4}(?![0-9a-z])"
    return re.search(pattern, folded_text) is not None
