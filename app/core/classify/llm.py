"""The classifier: shortlist in, ranked suggestions out, with every answer re-checked.

The safety property is structural rather than behavioural. The model is handed a list and
the JSON schema pins ``code`` to an enum of exactly those codes, so the provider itself
cannot emit anything else; then :meth:`LlmClassifier._accept` re-checks every returned code
against the same list. A hallucinated or unmappable code is therefore not a risk to monitor -
it cannot reach an output row.

Everything that can go wrong becomes an **abstention**, never a guess and never a lost row:

* no candidates, no description, no model configured, model unreachable, malformed answer,
  or a model that itself declined - all come back as :class:`~core.classify.models.Classification`
  with ``abstained=True`` and a reason a reviewer can read.

That is the point. MO's fallback is to research the issuer themselves, which they can do; a
confident wrong code entered into CTS is the outcome nobody catches.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.classify.cache import ClassificationCache, NullCache, cache_key
from core.classify.errors import LlmError, LlmNotConfiguredError
from core.classify.models import (
    CONFIDENCE_ORDER,
    Candidate,
    CandidateSet,
    Classification,
    Confidence,
    Kind,
    Suggestion,
)
from core.classify.prompts import PROMPT_VERSION, Prompt, build_prompt
from core.classify.provider import LlmProvider, LlmResponse, NullLlmProvider

if TYPE_CHECKING:  # the settings import stays lazy at runtime (see build_classifier)
    from config.settings import Settings

LOGGER = logging.getLogger(__name__)

#: Longest justification kept. A model asked for one sentence occasionally writes five; the
#: UI has room for one, and the full text adds nothing a reviewer needs.
MAX_JUSTIFICATION_CHARS = 400

_VALID_CONFIDENCE: frozenset[str] = frozenset(CONFIDENCE_ORDER)


class LlmClassifier:
    """Turns a shortlist plus evidence into up to three ranked, checked suggestions.

    Args:
        provider: The model adapter. :class:`~core.classify.provider.NullLlmProvider` makes
            every call abstain, which is how the tool behaves with nothing configured.
        cache: Answers are reused across runs; see :mod:`core.classify.cache` for the key.
        codebook_version: Part of the cache key, so a codebook update invalidates answers.
        max_suggestions: Ranked suggestions to keep.
        call_seconds: The longest one model call can take, retries included
            (:func:`~core.classify.provider.worst_case_call_seconds`). With a ``deadline``,
            a call that could end after it is not started. 0 (the default, and what the
            null provider gets) means there is no network call to bound.
        clock: Monotonic seconds; injected by the tests.
    """

    def __init__(
        self,
        provider: LlmProvider | None = None,
        *,
        cache: ClassificationCache | None = None,
        codebook_version: str | None = None,
        max_suggestions: int = 3,
        call_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Explicit None checks, not `or`: SqliteCache defines __len__, so an EMPTY cache is
        # falsy and `cache or NullCache()` would silently discard it - disabling caching
        # exactly until the cache had something in it, which it never would.
        self._provider = NullLlmProvider() if provider is None else provider
        self._cache = NullCache() if cache is None else cache
        self._codebook_version = codebook_version
        self._max_suggestions = max(1, max_suggestions)
        self._call_seconds = max(0.0, call_seconds)
        self._clock = clock

    @property
    def provider(self) -> LlmProvider:
        return self._provider

    # -- public API --------------------------------------------------------------------

    def classify(
        self,
        candidates: CandidateSet,
        *,
        issuer_name: str | None,
        description: str | None,
        deadline: float | None = None,
    ) -> Classification:
        """Choose from ``candidates``, or abstain with a reason.

        ``deadline`` is a :func:`time.monotonic` value: a model call that could end after it
        is not started (a cached answer is still served, it costs no time).
        """
        kind = candidates.kind
        text = (description or "").strip()

        if not len(candidates):
            return self._abstain(kind, "no candidate codes to choose from", 0)
        if not text:
            return self._abstain(
                kind,
                "no description available; the issuer could not be researched",
                len(candidates),
            )

        key = cache_key(
            kind=kind,
            issuer_name=issuer_name,
            description=text,
            codebook_version=self._codebook_version,
            model=self._provider.model,
            prompt_version=PROMPT_VERSION,
        )
        cached = self._cache.get(key)
        if cached is not None:
            LOGGER.debug("%s classification served from cache", kind)
            return cached

        if deadline is not None and self._call_seconds > 0:
            left = deadline - self._clock()
            if left < self._call_seconds:
                return self._abstain(
                    kind,
                    f"no time left for the model: a call can take up to "
                    f"{self._call_seconds:.0f} s and the lookup has {max(left, 0.0):.0f} s "
                    "of its limit left (LOOKUP_DEADLINE_SECONDS)",
                    len(candidates),
                )

        prompt = build_prompt(
            candidates,
            issuer_name=issuer_name,
            description=text,
            max_suggestions=self._max_suggestions,
        )
        try:
            response = self._provider.complete(prompt)
        except LlmNotConfiguredError as exc:
            return self._abstain(kind, f"no model configured: {exc}", len(candidates))
        except LlmError as exc:
            LOGGER.warning("%s classification failed: %s", kind, exc)
            return self._abstain(kind, f"model call failed: {exc}", len(candidates))

        classification = self._interpret(response, prompt, candidates)
        self._cache.put(key, classification, issuer_name=issuer_name)
        return classification

    def classify_both(
        self,
        nace: CandidateSet,
        esa: CandidateSet,
        *,
        issuer_name: str | None,
        description: str | None,
        deadline: float | None = None,
    ) -> tuple[Classification, Classification]:
        """Classify both codebooks.

        Two separate calls on purpose: the reasoning differs, each is cached and measured on
        its own, and a bad NACE answer cannot drag the ESA one with it. They run one after
        the other, so the ``deadline`` check before the second sees what the first took.
        """
        return (
            self.classify(
                nace, issuer_name=issuer_name, description=description, deadline=deadline
            ),
            self.classify(esa, issuer_name=issuer_name, description=description, deadline=deadline),
        )

    # -- internals ---------------------------------------------------------------------

    def _abstain(self, kind: Kind, reason: str, considered: int) -> Classification:
        return Classification(
            kind=kind,
            abstained=True,
            abstain_reason=reason,
            candidates_considered=considered,
            model=self._provider.model,
            prompt_version=PROMPT_VERSION,
            classified_at=datetime.now(UTC),
        )

    def _interpret(
        self, response: LlmResponse, prompt: Prompt, candidates: CandidateSet
    ) -> Classification:
        """Validate the answer and build the result."""
        try:
            payload = response.parsed()
        except LlmError as exc:
            return self._abstain(prompt.kind, f"unreadable model answer: {exc}", len(candidates))

        if payload.get("sufficient_evidence") is False:
            return Classification(
                kind=prompt.kind,
                abstained=True,
                abstain_reason="the model judged the evidence insufficient",
                candidates_considered=len(candidates),
                model=response.model,
                prompt_version=prompt.version,
                classified_at=datetime.now(UTC),
            )

        picks = payload.get("picks")
        if not isinstance(picks, Sequence) or isinstance(picks, (str, bytes)):
            return self._abstain(prompt.kind, "model answer had no picks", len(candidates))

        suggestions: list[Suggestion] = []
        rejected: list[str] = []
        for item in picks:
            if len(suggestions) >= self._max_suggestions:
                break
            accepted = self._accept(item, candidates, rank=len(suggestions) + 1)
            if accepted is None:
                code = item.get("code") if isinstance(item, Mapping) else None
                rejected.append(str(code) if code is not None else "(unreadable pick)")
                continue
            suggestions.append(accepted)

        if rejected:
            # The schema enum should make this impossible; if it ever fires, the provider is
            # not enforcing the schema and that is worth knowing loudly.
            LOGGER.warning(
                "%s: dropped %d code(s) the model was not offered: %s",
                prompt.kind,
                len(rejected),
                ", ".join(rejected),
            )

        if not suggestions:
            return Classification(
                kind=prompt.kind,
                abstained=True,
                abstain_reason="the model returned no usable code",
                candidates_considered=len(candidates),
                rejected=tuple(rejected),
                model=response.model,
                prompt_version=prompt.version,
                classified_at=datetime.now(UTC),
            )

        return Classification(
            kind=prompt.kind,
            suggestions=tuple(suggestions),
            candidates_considered=len(candidates),
            rejected=tuple(rejected),
            model=response.model,
            prompt_version=prompt.version,
            classified_at=datetime.now(UTC),
        )

    def _accept(self, item: Any, candidates: CandidateSet, *, rank: int) -> Suggestion | None:
        """Turn one pick into a suggestion, or reject it.

        The gate: the code must be one that was offered. Building the suggestion from the
        candidate rather than from the model's answer means the CTS ID and the label come
        from the codebook and cannot be invented.
        """
        if not isinstance(item, Mapping):
            return None
        code = str(item.get("code") or "").strip()
        if not code:
            return None
        candidate: Candidate | None = candidates.by_code(code)
        if candidate is None:
            return None

        confidence = str(item.get("confidence") or "").strip().lower()
        if confidence not in _VALID_CONFIDENCE:
            # An unreadable confidence is treated as the weakest, not discarded: the code
            # itself was validated and is still worth showing.
            confidence = "low"

        justification = " ".join(str(item.get("justification") or "").split())
        if len(justification) > MAX_JUSTIFICATION_CHARS:
            justification = justification[:MAX_JUSTIFICATION_CHARS].rstrip() + "…"

        return Suggestion.from_candidate(
            candidate,
            confidence=confidence,  # type: ignore[arg-type]
            justification=justification,
            rank=rank,
        )


def can_start_a_call(settings: Settings, call_seconds: float) -> bool:
    """Whether a lookup can start both of its model calls inside the deadline.

    A lookup classifies TWO codebooks, so the deadline has to hold two worst-case calls, not
    one. Returns True when no deadline is set (``lookup_deadline_seconds`` 0) or no call is
    made at all.
    """
    if settings.lookup_deadline_seconds <= 0 or call_seconds <= 0:
        return True
    return 2 * call_seconds <= settings.lookup_deadline_seconds


def warn_if_unstartable(settings: Settings, call_seconds: float) -> None:
    """Say loudly when the configured timeouts make a model call impossible to start.

    This is the failure that hides: the classifier abstains, the rules propose a code, and the
    page looks exactly like a working deterministic run. The only clue is one sentence inside
    an abstention reason. Anyone raising LLM_TIMEOUT_SECONDS or LLM_MAX_ATTEMPTS to be
    "safer" silently switches the model off, so say so at build time instead.
    """
    if can_start_a_call(settings, call_seconds):
        return
    LOGGER.warning(
        "the model will NEVER be called: one call can take up to %.0f s and a lookup makes two, "
        "but LOOKUP_DEADLINE_SECONDS is %.0f. Lower LLM_TIMEOUT_SECONDS (%.0f) or "
        "LLM_MAX_ATTEMPTS (%d), or raise the deadline off Vercel.",
        call_seconds,
        settings.lookup_deadline_seconds,
        settings.llm_timeout_seconds,
        settings.llm_max_attempts,
    )


def build_classifier(
    settings: Any = None,
    *,
    provider: LlmProvider | None = None,
    codebook_version: str | None = None,
) -> LlmClassifier:
    """The standard classifier from settings: configured provider plus the SQLite cache."""
    from config.settings import Settings, get_settings
    from core.classify.budget import BudgetedProvider, build_budget, build_ledger
    from core.classify.cache import build_cache
    from core.classify.provider import build_provider, worst_case_call_seconds

    resolved: Settings = settings if isinstance(settings, Settings) else get_settings()
    # Every caller gets the limits, because they are applied here rather than at each call
    # site: a new entry point cannot forget them.
    inner = provider if provider is not None else build_provider(resolved)
    guarded = BudgetedProvider(
        inner,
        budget=build_budget(resolved),
        ledger=build_ledger(resolved.llm_usage_path),
    )
    # The null provider makes no call, so there is nothing to fit into the deadline - and
    # "no model configured" stays the reason the page shows.
    call_seconds = 0.0 if isinstance(inner, NullLlmProvider) else worst_case_call_seconds(resolved)
    warn_if_unstartable(resolved, call_seconds)
    return LlmClassifier(
        guarded,
        cache=build_cache(resolved.llm_cache_path),
        codebook_version=codebook_version,
        max_suggestions=resolved.llm_max_suggestions,
        call_seconds=call_seconds,
    )


__all__ = [
    "can_start_a_call",
    "warn_if_unstartable",
    "MAX_JUSTIFICATION_CHARS",
    "Confidence",
    "LlmClassifier",
    "build_classifier",
]
