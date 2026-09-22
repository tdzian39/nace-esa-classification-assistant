"""The classifier: the candidate-list gate, abstention, and every way a model can misbehave.

No key and no network: every case drives a :class:`StubLlmProvider`.
"""

from __future__ import annotations

import json

import pytest

from core.classify.candidates import EsaCandidateFilter, NaceCandidateFilter
from core.classify.llm import MAX_JUSTIFICATION_CHARS, LlmClassifier
from core.classify.models import ESA, NACE, Candidate, CandidateSet
from core.classify.prompts import PROMPT_VERSION
from core.classify.provider import NullLlmProvider, StubLlmProvider
from core.codebooks.models import CodebookSet
from tests.classify.conftest import CAPTIVE_EN


def answer(*picks: tuple[str, str, str], sufficient: bool = True) -> str:
    return json.dumps(
        {
            "sufficient_evidence": sufficient,
            "picks": [
                {"code": code, "confidence": confidence, "justification": why}
                for code, confidence, why in picks
            ],
        }
    )


def nace_candidates(codebooks: CodebookSet, description: str = CAPTIVE_EN):
    return NaceCandidateFilter(codebooks).shortlist(description)


def esa_candidates(codebooks: CodebookSet, description: str = CAPTIVE_EN):
    return EsaCandidateFilter(codebooks).shortlist(description)


def ranked_set() -> CandidateSet:
    """A three-code NACE shortlist, built by hand for the ordering tests."""
    return CandidateSet(
        kind=NACE,
        candidates=(
            Candidate(kind=NACE, code="64", cts_id="512", label="Finanční činnosti"),
            Candidate(kind=NACE, code="65", cts_id="513", label="Pojišťovnictví"),
            Candidate(kind=NACE, code="66", cts_id="514", label="Pomocné činnosti"),
        ),
        considered=87,
        filter_name="handmade",
    )


class TestHappyPath:
    def test_a_pick_becomes_a_ranked_suggestion(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(("64", "high", "Financuje vlastní skupinu.")))
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="Nordkap", description=CAPTIVE_EN
        )

        assert not result.abstained
        assert result.top is not None
        assert result.top.code == "64"
        assert result.top.confidence == "high"
        assert result.top.rank == 1

    def test_cts_id_and_label_come_from_the_codebook_not_the_model(
        self, codebooks: CodebookSet
    ) -> None:
        """The model names a code; everything else is looked up, so it cannot be invented."""
        candidates = nace_candidates(codebooks)
        provider = StubLlmProvider(answer(("64", "high", "x")))
        result = LlmClassifier(provider).classify(
            candidates, issuer_name="X", description=CAPTIVE_EN
        )

        expected = candidates.by_code("64")
        assert expected is not None
        assert result.top.cts_id == expected.cts_id
        assert result.top.label == expected.label

    def test_three_picks_are_ranked_in_order(self) -> None:
        """Ranking only, so the shortlist is built explicitly rather than filtered."""
        candidates = ranked_set()
        provider = StubLlmProvider(
            answer(("64", "high", "a"), ("65", "medium", "b"), ("66", "low", "c"))
        )
        result = LlmClassifier(provider).classify(
            candidates, issuer_name="X", description=CAPTIVE_EN
        )
        assert [s.code for s in result.suggestions] == ["64", "65", "66"]
        assert [s.rank for s in result.suggestions] == [1, 2, 3]
        assert [a.code for a in result.alternatives] == ["65", "66"]

    def test_more_picks_than_allowed_are_truncated(self) -> None:
        provider = StubLlmProvider(
            answer(("64", "high", "a"), ("65", "medium", "b"), ("66", "low", "c"))
        )
        result = LlmClassifier(provider, max_suggestions=2).classify(
            ranked_set(), issuer_name="X", description=CAPTIVE_EN
        )
        assert len(result.suggestions) == 2

    def test_the_run_is_attributable(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(("64", "high", "x")), model="test-model-1")
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.model == "test-model-1"
        assert result.prompt_version == PROMPT_VERSION
        assert result.classified_at is not None

    def test_both_codebooks_are_classified_separately(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(
            {
                NACE: answer(("64", "high", "a")),
                ESA: answer(("2002703", "high", "b")),
            }
        )
        nace, esa = LlmClassifier(provider).classify_both(
            nace_candidates(codebooks),
            esa_candidates(codebooks),
            issuer_name="X",
            description=CAPTIVE_EN,
        )
        assert nace.top.code == "64" and nace.kind == NACE
        assert esa.top.code == "2002703" and esa.kind == ESA
        assert len(provider.calls) == 2


class TestTheGate:
    def test_a_code_that_was_not_offered_is_rejected(self, codebooks: CodebookSet) -> None:
        """The property the whole design rests on: an unoffered code cannot reach a row."""
        provider = StubLlmProvider(answer(("99", "high", "invented")))
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )

        assert result.abstained
        assert "99" in result.rejected
        assert result.suggestions == ()

    def test_good_picks_survive_alongside_a_rejected_one(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(("99", "high", "invented"), ("64", "high", "real")))
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )

        assert [s.code for s in result.suggestions] == ["64"]
        assert result.rejected == ("99",)

    def test_a_rejection_is_logged_loudly(
        self, codebooks: CodebookSet, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The schema enum should make this impossible; if it fires the provider is broken."""
        provider = StubLlmProvider(answer(("99", "high", "x")))
        with caplog.at_level("WARNING", logger="core.classify.llm"):
            LlmClassifier(provider).classify(
                nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
            )
        assert any("was not offered" in record.message for record in caplog.records)


class TestAbstention:
    def test_the_model_may_decline(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(sufficient=False))
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="X", description="something vague"
        )
        assert result.abstained
        assert "insufficient" in result.abstain_reason

    def test_no_description_means_no_call_at_all(self, codebooks: CodebookSet) -> None:
        """Researching the issuer failed; guessing from the name is exactly what to avoid."""
        provider = StubLlmProvider(answer(("64", "high", "x")))
        result = LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="Nordkap Funding B.V.", description=None
        )
        assert result.abstained
        assert provider.calls == []

    def test_no_candidates_means_no_call(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(("64", "high", "x")))
        empty = NaceCandidateFilter(codebooks).shortlist("")
        result = LlmClassifier(provider).classify(empty, issuer_name="X", description="text")
        assert result.abstained
        assert provider.calls == []

    def test_no_model_configured_abstains_rather_than_failing(self, codebooks: CodebookSet) -> None:
        result = LlmClassifier(NullLlmProvider()).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.abstained
        assert "no model configured" in result.abstain_reason

    def test_a_model_outage_abstains_rather_than_losing_the_row(
        self, codebooks: CodebookSet
    ) -> None:
        from core.classify.errors import LlmUnavailableError

        class Failing:
            name, model = "failing", "m"

            def complete(self, prompt):
                raise LlmUnavailableError("service unavailable")

        result = LlmClassifier(Failing()).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.abstained
        assert "model call failed" in result.abstain_reason


class TestMalformedAnswers:
    @pytest.mark.parametrize(
        "content",
        ["not json at all", "[]", '"a string"', "{}", '{"sufficient_evidence": true}'],
    )
    def test_unusable_bodies_abstain(self, codebooks: CodebookSet, content: str) -> None:
        result = LlmClassifier(StubLlmProvider(content)).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.abstained

    def test_a_pick_that_is_not_an_object_is_rejected(self, codebooks: CodebookSet) -> None:
        content = json.dumps({"sufficient_evidence": True, "picks": ["64"]})
        result = LlmClassifier(StubLlmProvider(content)).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.abstained

    def test_an_unreadable_confidence_becomes_low_rather_than_dropping_the_code(
        self, codebooks: CodebookSet
    ) -> None:
        """The code itself was validated; downgrading is better than discarding it."""
        content = json.dumps(
            {
                "sufficient_evidence": True,
                "picks": [{"code": "64", "confidence": "velmi vysoká", "justification": "x"}],
            }
        )
        result = LlmClassifier(StubLlmProvider(content)).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.top.code == "64"
        assert result.top.confidence == "low"

    def test_an_over_long_justification_is_trimmed(self, codebooks: CodebookSet) -> None:
        content = answer(("64", "high", "věta. " * 400))
        result = LlmClassifier(StubLlmProvider(content)).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert len(result.top.justification) <= MAX_JUSTIFICATION_CHARS + 1

    def test_whitespace_in_a_justification_is_collapsed(self, codebooks: CodebookSet) -> None:
        result = LlmClassifier(StubLlmProvider(answer(("64", "high", "a\n\n  b")))).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert result.top.justification == "a b"


class TestPromptContents:
    def test_the_prompt_carries_the_candidates_and_their_definitions(
        self, codebooks: CodebookSet
    ) -> None:
        provider = StubLlmProvider(answer(("64", "high", "x")))
        LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="Nordkap", description=CAPTIVE_EN
        )
        prompt = provider.calls[0]

        assert "Nordkap" in prompt.user
        assert "kód 64" in prompt.user
        assert "účelových finančních společností" in prompt.user

    def test_the_schema_pins_the_code_to_the_offered_list(self, codebooks: CodebookSet) -> None:
        provider = StubLlmProvider(answer(("64", "high", "x")))
        candidates = nace_candidates(codebooks)
        LlmClassifier(provider).classify(candidates, issuer_name="X", description=CAPTIVE_EN)

        enum = provider.calls[0].schema["properties"]["picks"]["items"]["properties"]["code"][
            "enum"
        ]
        assert set(enum) == set(candidates.codes)

    def test_the_prompt_is_small_enough_to_be_cheap(self, codebooks: CodebookSet) -> None:
        """The whole reason for the pre-filter: the deciding prompt stays a few thousand
        tokens instead of carrying the entire codebook."""
        provider = StubLlmProvider(answer(("64", "high", "x")))
        LlmClassifier(provider).classify(
            nace_candidates(codebooks), issuer_name="X", description=CAPTIVE_EN
        )
        assert provider.calls[0].estimated_tokens < 4000
