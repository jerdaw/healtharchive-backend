from __future__ import annotations

from collections.abc import Iterable


def pick_word_similarity_threshold(tokens: Iterable[str]) -> float:
    """
    Choose a pg_trgm.word_similarity_threshold for misspelling queries.

    Short tokens need a higher threshold to avoid huge candidate sets, while long
    misspellings typically need a lower threshold for reasonable recall.
    """
    tokens_list = [t for t in tokens if t]
    if not tokens_list:
        return 0.35

    min_len = min(len(t) for t in tokens_list)
    if min_len <= 5:
        return 0.35
    if min_len <= 7:
        return 0.30
    return 0.25


def token_variants(token: str) -> list[str]:
    """
    Generate case variants that help match typical title casing (and acronyms).
    """
    variants = {token}
    if token and token[0].isalpha():
        variants.add(token[0].upper() + token[1:])
    if any(ch.isalpha() for ch in token) and (
        any(ch.isdigit() for ch in token) or len(token) <= 6
    ):
        variants.add(token.upper())
    return sorted(variants)


def should_use_url_similarity(token: str) -> bool:
    """
    Heuristic for whether we should also consider URL similarity for a token.

    URLs can drastically increase candidate sets for short/broad terms; for long
    tokens or URL-ish patterns, URL similarity is usually helpful and still safe.
    """
    return any(ch in token for ch in ("/", ".", ":", "?", "&", "=")) or len(token) >= 10


__all__ = [
    "pick_word_similarity_threshold",
    "token_variants",
    "should_use_url_similarity",
]

