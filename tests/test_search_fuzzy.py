from __future__ import annotations

from ha_backend.search_fuzzy import (
    pick_word_similarity_threshold,
    should_use_url_similarity,
    token_variants,
)


def test_pick_word_similarity_threshold_scales_with_token_length() -> None:
    assert pick_word_similarity_threshold(["covd"]) == 0.35
    assert pick_word_similarity_threshold(["influen"]) == 0.30
    assert pick_word_similarity_threshold(["coronovirus"]) == 0.25


def test_token_variants_adds_title_case_variant() -> None:
    assert token_variants("coronovirus") == ["Coronovirus", "coronovirus"]


def test_token_variants_adds_upper_for_acronyms() -> None:
    assert token_variants("h1n1") == ["H1N1", "H1n1", "h1n1"]


def test_should_use_url_similarity_for_long_or_urlish_tokens() -> None:
    assert should_use_url_similarity("coronovirus") is True  # length >= 10
    assert should_use_url_similarity("influensa") is False
    assert should_use_url_similarity("https") is False
    assert should_use_url_similarity("canada.ca") is True
