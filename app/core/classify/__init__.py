"""Classification of foreign issuers: shortlist, then a checked choice from that list.

* :mod:`core.classify.candidates` (+ ``hints``, ``text``) - the pre-filter that turns a
  codebook into the ten or so codes worth deciding between, each already carrying its CTS ID.
* :mod:`core.classify.prompts` - the versioned prompt and the JSON schema that pins the
  answer to the offered codes.
* :mod:`core.classify.provider` - model adapters (OpenAI over HTTP, a stub, a null one).
* :mod:`core.classify.llm` - the classifier: validates every answer and abstains rather
  than guessing.
* :mod:`core.classify.cache` - answers reused across runs, keyed on everything that can
  change them.
* :mod:`core.classify.golden` - verified cases and recall scoring.

A rule table over the register facts (``rules.py``, roadmap E4) is not built yet; until
then the GLEIF/OpenFIGI facts reach the pre-filter as words in the identity fact sheet.
"""

from core.classify.budget import (
    Budget,
    BudgetedProvider,
    BudgetExceededError,
    SqliteLedger,
    UsageTotals,
    build_budget,
    build_ledger,
)
from core.classify.cache import NullCache, SqliteCache, build_cache, cache_key
from core.classify.candidates import (
    DEFAULT_LIMIT,
    CandidateFilter,
    EsaCandidateFilter,
    NaceCandidateFilter,
    shortlist_both,
)
from core.classify.errors import (
    ClassifyError,
    LlmError,
    LlmNotConfiguredError,
    LlmResponseError,
    LlmUnavailableError,
)
from core.classify.golden import GoldenCase, GoldenError, load_golden, score_recall
from core.classify.llm import LlmClassifier, build_classifier
from core.classify.models import (
    ESA,
    NACE,
    Candidate,
    CandidateSet,
    Classification,
    Confidence,
    Kind,
    Suggestion,
)
from core.classify.prompts import PROMPT_VERSION, Prompt, build_prompt
from core.classify.provider import (
    LlmProvider,
    LlmResponse,
    NullLlmProvider,
    OpenAiProvider,
    StubLlmProvider,
    build_provider,
)

__all__ = [
    "DEFAULT_LIMIT",
    "ESA",
    "NACE",
    "PROMPT_VERSION",
    "Budget",
    "BudgetExceededError",
    "BudgetedProvider",
    "Candidate",
    "CandidateFilter",
    "CandidateSet",
    "Classification",
    "ClassifyError",
    "Confidence",
    "EsaCandidateFilter",
    "GoldenCase",
    "GoldenError",
    "Kind",
    "LlmClassifier",
    "LlmError",
    "LlmNotConfiguredError",
    "LlmProvider",
    "LlmResponse",
    "LlmResponseError",
    "LlmUnavailableError",
    "NaceCandidateFilter",
    "NullCache",
    "NullLlmProvider",
    "OpenAiProvider",
    "Prompt",
    "SqliteCache",
    "SqliteLedger",
    "StubLlmProvider",
    "Suggestion",
    "UsageTotals",
    "build_budget",
    "build_cache",
    "build_classifier",
    "build_prompt",
    "build_ledger",
    "build_provider",
    "cache_key",
    "load_golden",
    "score_recall",
    "shortlist_both",
]
