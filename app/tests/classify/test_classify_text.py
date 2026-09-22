"""Text primitives: folding, stemming, phrase boundaries and overlap scoring."""

from __future__ import annotations

import pytest

from core.classify.text import (
    STEM_LENGTH,
    contains_phrase,
    fold,
    inverse_document_frequency,
    overlap_score,
    stem,
    stems,
    tokenize,
)


class TestFolding:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Finanční", "financni"),
            ("POJIŠŤOVNA", "pojistovna"),
            ("Škoda", "skoda"),
            ("ABC", "abc"),
        ],
    )
    def test_accents_and_case_are_removed(self, text: str, expected: str) -> None:
        assert fold(text) == expected

    def test_tokenize_drops_stopwords_and_short_words(self) -> None:
        tokens = tokenize("Je to banka a její činnost v EU")
        assert "banka" in tokens
        assert "je" not in tokens and "to" not in tokens
        assert "cinnosti" not in tokens and "cinnost" not in tokens

    def test_punctuation_is_not_a_token(self) -> None:
        assert tokenize("leasing, úvěry; pojištění.") == ["leasing", "uvery", "pojisteni"]


class TestStemming:
    def test_czech_declension_collapses(self) -> None:
        """The reason stemming exists at all: these are the same word to a reader."""
        assert stem("financni") == stem("financnich") == stem("finance"[:STEM_LENGTH])

    def test_distinct_words_stay_distinct(self) -> None:
        assert stem("pojistovna") != stem("penzijni")

    def test_stems_of_a_phrase(self) -> None:
        assert "leasin" in stems("finanční leasing vozidel")


class TestContainsPhrase:
    @pytest.mark.parametrize("text", ["a bank", "Banka", "banky", "banking group", "BANKS"])
    def test_matches_word_and_short_suffixes(self, text: str) -> None:
        assert contains_phrase(text, "bank")

    @pytest.mark.parametrize("text", ["embankment", "mountainbank", "riverbanks are wide"])
    def test_does_not_match_inside_another_word(self, text: str) -> None:
        """Without word boundaries "bank" would fire on "embankment" and poison the hints."""
        assert not contains_phrase(text, "bank")

    def test_accents_are_ignored_on_both_sides(self) -> None:
        assert contains_phrase("POJIŠŤOVNA Praha", "pojišťovna")

    def test_multiword_phrase(self) -> None:
        assert contains_phrase("a special purpose entity", "special purpose")
        assert not contains_phrase("a general purpose entity", "special purpose")

    def test_empty_phrase_never_matches(self) -> None:
        assert not contains_phrase("anything", "")


class TestScoring:
    def test_no_overlap_scores_zero(self) -> None:
        assert overlap_score({"banka"}, {"vozidl"}) == 0.0

    def test_full_overlap_scores_one(self) -> None:
        assert overlap_score({"banka", "uvery"}, {"banka", "uvery", "jine"}) == pytest.approx(1.0)

    def test_empty_query_scores_zero_without_dividing(self) -> None:
        assert overlap_score(set(), {"banka"}) == 0.0

    def test_long_documents_are_not_penalised(self) -> None:
        """Normalising by the query keeps a thorough codebook definition from losing out."""
        short = overlap_score({"banka"}, {"banka"})
        long = overlap_score({"banka"}, {"banka", *(f"x{i}" for i in range(50))})
        assert short == long

    def test_idf_weights_down_ubiquitous_stems(self) -> None:
        documents = [{"financ", "banka"}, {"financ", "pojist"}, {"financ", "leasin"}]
        weights = inverse_document_frequency(documents)
        assert weights["financ"] < weights["banka"]

    def test_idf_weighting_changes_the_ranking(self) -> None:
        documents = [{"financ", "banka"}, {"financ", "pojist"}, {"financ", "leasin"}]
        weights = inverse_document_frequency(documents)
        query = {"financ", "pojist"}
        generic = overlap_score(query, {"financ"}, weights)
        specific = overlap_score(query, {"pojist"}, weights)
        assert specific > generic
