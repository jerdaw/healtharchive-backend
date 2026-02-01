from __future__ import annotations

"""
ha_backend.search_ranking - Search result ranking for HealthArchive queries

Implements a configurable scoring system that combines:
    - Authority (inbound link count)
    - Hubness (outbound link count for broad queries)
    - PageRank (graph-based authority)
    - URL depth penalty
    - Title match bonuses
    - Archived page penalty

Supports multiple ranking versions (v1, v2, v3) selectable via:
    - Query param: ranking=v3
    - Environment: HA_SEARCH_RANKING_VERSION=v3

See also:
    - docs/operations/search-quality.md for evaluation guidance
    - docs/operations/search-golden-queries.md for golden query tests
    - docs/decisions/2026-01-18-search-ranking-v3.md for v3 design
"""

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement


class RankingVersion(str, Enum):
    v1 = "v1"
    v2 = "v2"
    v3 = "v3"


class QueryMode(str, Enum):
    broad = "broad"
    mixed = "mixed"
    specific = "specific"


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class RankingConfig:
    """
    Coefficients for ranking heuristics.

    These are intentionally simple and tuned via a small set of repeatable
    evaluation queries (see docs/operations/search-golden-queries.md).
    """

    # Postgres authority coefficient (multiplies ln(inlink_count + 1)).
    authority_coef: float
    # Broad-query "hubness" coefficient (multiplies ln(outlink_count + 1) on Postgres).
    hubness_coef: float
    # Broad-query PageRank coefficient (multiplies ln(pagerank + 1) on Postgres).
    pagerank_coef: float
    # URL depth penalty per slash (negative).
    depth_coef: float
    # Penalty for titles that look like archive wrappers (negative).
    archived_penalty: float
    # Bonus for title matches (positive).
    title_all_tokens_boost: float
    title_any_token_boost: float

    # v3 additions:
    # Bonus for title exact-match (query appears as substring in title).
    title_exact_match_boost: float = 0.0
    # Recency boost coefficient (multiplies log of days-ago inverse for broad queries).
    recency_coef: float = 0.0
    # BM25 ts_rank weights: tuple of (D, C, B, A) weights for Postgres ts_rank.
    # Higher values = more importance. Default [0.1, 0.2, 0.4, 1.0].
    ts_rank_weights: tuple[float, float, float, float] = (0.1, 0.2, 0.4, 1.0)


def get_ranking_version(explicit: str | None) -> RankingVersion:
    """
    Determine ranking version from query param or environment.

    Environment variable:
      - HA_SEARCH_RANKING_VERSION: "v1" (default), "v2", or "v3"
    """
    if explicit:
        try:
            return RankingVersion(explicit)
        except ValueError:
            return RankingVersion.v1

    env_val = os.environ.get("HA_SEARCH_RANKING_VERSION", "v1").strip().lower()
    try:
        return RankingVersion(env_val)
    except ValueError:
        return RankingVersion.v1


def tokenize_query(q_clean: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(q_clean)]


def classify_query_mode(q_clean: str) -> QueryMode:
    """
    Classify the query into a coarse intent mode.

    Notes:
    - This does not try to be "smart"; it just ensures broad head queries (e.g.
      "covid") get a different blend than longer/specific queries.
    - We treat quoted/structured queries as specific.
    """
    q = q_clean.strip()
    if not q:
        return QueryMode.specific

    if any(ch in q for ch in ['"', ":", "(", ")", "[", "]", "{", "}", "|", "&"]):
        return QueryMode.specific
    if any(ch.isdigit() for ch in q):
        return QueryMode.specific

    tokens = tokenize_query(q)
    if len(tokens) <= 1:
        return QueryMode.broad
    if len(tokens) == 2:
        return QueryMode.mixed
    return QueryMode.specific


def get_ranking_config(
    *, mode: QueryMode, version: RankingVersion = RankingVersion.v2
) -> RankingConfig:
    """
    Get ranking coefficients based on query mode and ranking version.

    v3 uses stronger archived penalties and slightly tuned authority blending.
    """
    if version == RankingVersion.v3:
        return _get_ranking_config_v3(mode)
    # v1 and v2 use the same coefficients.
    return _get_ranking_config_v1_v2(mode)


def _get_ranking_config_v1_v2(mode: QueryMode) -> RankingConfig:
    """Ranking config for v1/v2 (original behavior)."""
    if mode == QueryMode.broad:
        return RankingConfig(
            authority_coef=0.30,
            hubness_coef=0.08,
            pagerank_coef=0.12,
            depth_coef=-0.03,
            archived_penalty=-0.8,
            title_all_tokens_boost=0.20,
            title_any_token_boost=0.10,
        )
    if mode == QueryMode.mixed:
        return RankingConfig(
            authority_coef=0.20,
            hubness_coef=0.00,
            pagerank_coef=0.00,
            depth_coef=-0.02,
            archived_penalty=-0.6,
            title_all_tokens_boost=0.20,
            title_any_token_boost=0.10,
        )
    return RankingConfig(
        authority_coef=0.05,
        hubness_coef=0.00,
        pagerank_coef=0.00,
        depth_coef=-0.01,
        archived_penalty=-0.0,
        title_all_tokens_boost=0.20,
        title_any_token_boost=0.0,
    )


def _get_ranking_config_v3(mode: QueryMode) -> RankingConfig:
    """
    Ranking config for v3.

    Changes from v2:
    - Stronger archived penalty for broad queries (better hub surfacing).
    - Slightly stronger depth penalty to prefer shallower pages.
    - Archived penalty now applies to mixed queries too.
    - NEW: Recency boost for broad/mixed queries (prefer recent content).
    - NEW: Title exact-match boost (query substring in title).
    - NEW: Tuned BM25 weights (stronger title, weaker URL).
    """
    if mode == QueryMode.broad:
        return RankingConfig(
            authority_coef=0.35,
            hubness_coef=0.10,
            pagerank_coef=0.15,
            depth_coef=-0.04,
            archived_penalty=-1.0,
            title_all_tokens_boost=0.25,
            title_any_token_boost=0.12,
            # v3 additions:
            title_exact_match_boost=0.35,  # Strong boost for exact title match.
            recency_coef=0.15,  # Moderate recency preference for broad queries.
            ts_rank_weights=(0.05, 0.15, 0.35, 1.2),  # Stronger title (A), weaker URL (D).
        )
    if mode == QueryMode.mixed:
        return RankingConfig(
            authority_coef=0.25,
            hubness_coef=0.00,
            pagerank_coef=0.00,
            depth_coef=-0.025,
            archived_penalty=-0.8,
            title_all_tokens_boost=0.22,
            title_any_token_boost=0.10,
            # v3 additions:
            title_exact_match_boost=0.30,
            recency_coef=0.08,  # Mild recency preference.
            ts_rank_weights=(0.08, 0.18, 0.38, 1.1),
        )
    return RankingConfig(
        authority_coef=0.08,
        hubness_coef=0.00,
        pagerank_coef=0.00,
        depth_coef=-0.015,
        archived_penalty=-0.3,
        title_all_tokens_boost=0.20,
        title_any_token_boost=0.05,
        # v3 additions:
        title_exact_match_boost=0.25,  # Still valuable for specific queries.
        recency_coef=0.0,  # No recency preference for specific queries.
        ts_rank_weights=(0.1, 0.2, 0.4, 1.0),  # Default weights.
    )


def build_title_token_match_expr(
    *,
    title_expr: ColumnElement,
    tokens: Iterable[str],
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    tokens_list = [t for t in tokens if t]
    if not tokens_list:
        return (literal_false(), literal_false())

    token_match_exprs = [title_expr.ilike(f"%{t}%") for t in tokens_list]
    any_match = or_(*token_match_exprs)
    all_match = and_(*token_match_exprs) if len(token_match_exprs) > 1 else any_match
    return (any_match, all_match)


def build_title_boost_expr(
    *,
    title_expr: ColumnElement,
    tokens: list[str],
    cfg: RankingConfig,
) -> ColumnElement[float]:
    any_match, all_match = build_title_token_match_expr(
        title_expr=title_expr,
        tokens=tokens,
    )

    return case(
        (all_match, float(cfg.title_all_tokens_boost)),
        (any_match, float(cfg.title_any_token_boost)),
        else_=0.0,
    )


def build_archived_penalty_expr(
    *,
    title_expr: ColumnElement,
    cfg: RankingConfig,
) -> ColumnElement[float]:
    """Build archived penalty using title heuristics (v1/v2 behavior)."""
    if cfg.archived_penalty == 0:
        return literal_zero()
    return case(
        (title_expr.ilike("archived%"), float(cfg.archived_penalty)),
        else_=0.0,
    )


def build_archived_penalty_expr_v3(
    *,
    is_archived_expr: ColumnElement | None,
    title_expr: ColumnElement,
    snippet_expr: ColumnElement | None,
    cfg: RankingConfig,
) -> ColumnElement[float]:
    """
    Build archived penalty for v3 ranking.

    Prefers the is_archived column when available and non-NULL.
    Falls back to title/snippet heuristics for legacy rows (is_archived IS NULL).
    """
    if cfg.archived_penalty == 0:
        return literal_zero()

    penalty = float(cfg.archived_penalty)

    # If is_archived column is available, use it with fallback.
    if is_archived_expr is not None:
        # Build fallback heuristic for NULL rows.
        title_heuristic = title_expr.ilike("archived%")

        if snippet_expr is not None:
            # Check for archived banners in snippet.
            snippet_archived = or_(
                snippet_expr.ilike("%we have archived this page%"),
                snippet_expr.ilike("%this page has been archived%"),
                snippet_expr.ilike("%cette page a été archivée%"),
                snippet_expr.ilike("%information archivée%"),
            )
            fallback_heuristic = or_(title_heuristic, snippet_archived)
        else:
            fallback_heuristic = title_heuristic

        return case(
            # is_archived = True -> apply penalty.
            (is_archived_expr == True, penalty),  # noqa: E712
            # is_archived = False -> no penalty.
            (is_archived_expr == False, 0.0),  # noqa: E712
            # is_archived IS NULL -> fall back to heuristics.
            (fallback_heuristic, penalty),
            else_=0.0,
        )

    # No is_archived column available, use heuristics only.
    return build_archived_penalty_expr(title_expr=title_expr, cfg=cfg)


def build_depth_penalty_expr(
    *,
    url_expr: ColumnElement,
    cfg: RankingConfig,
) -> ColumnElement[float]:
    # slash_count = length(url) - length(replace(url, '/', ''))
    slash_count = func.length(url_expr) - func.length(func.replace(url_expr, "/", ""))
    return float(cfg.depth_coef) * slash_count


def build_authority_boost_expr_postgres(
    *,
    inlink_count_expr: ColumnElement,
    cfg: RankingConfig,
) -> ColumnElement[float]:
    if cfg.authority_coef == 0:
        return literal_zero()
    return float(cfg.authority_coef) * func.ln(inlink_count_expr + 1)


def build_authority_tier_expr(
    *,
    inlink_count_expr: ColumnElement,
) -> ColumnElement[int]:
    return case(
        (inlink_count_expr >= 100, 3),
        (inlink_count_expr >= 20, 2),
        (inlink_count_expr >= 5, 1),
        else_=0,
    )


def build_title_exact_match_boost_expr(
    *,
    title_expr: ColumnElement,
    query: str,
    cfg: RankingConfig,
) -> ColumnElement[float]:
    """
    Build a boost expression for title exact-match (v3).

    Returns the configured boost value when the normalized query string
    appears as a substring in the title. This is stronger than token matching.
    """
    if cfg.title_exact_match_boost == 0:
        return literal_zero()

    # Normalize query for comparison (strip, lowercase via SQL).
    query_clean = query.strip()
    if not query_clean:
        return literal_zero()

    # Case-insensitive substring match.
    return case(
        (title_expr.ilike(f"%{query_clean}%"), float(cfg.title_exact_match_boost)),
        else_=0.0,
    )


def build_recency_boost_expr_postgres(
    *,
    archived_at_expr: ColumnElement,
    cfg: RankingConfig,
    reference_date: ColumnElement | None = None,
) -> ColumnElement[float]:
    """
    Build a recency boost expression for Postgres (v3).

    Computes a boost based on how recent the snapshot is. Uses logarithmic
    decay: boost = coef * ln(1 + 365 / days_ago). This gives:
    - Pages from today: ~6 * coef boost
    - Pages from 1 week ago: ~4 * coef boost
    - Pages from 1 year ago: ~1 * coef boost
    - Pages from 5 years ago: ~0.4 * coef boost
    """
    if cfg.recency_coef == 0:
        return literal_zero()

    # Use current date if no reference provided.
    if reference_date is None:
        reference_date = func.current_date()

    # Calculate days since archived_at (cast to date for comparison).
    days_ago = func.extract("epoch", reference_date - func.date(archived_at_expr)) / 86400.0

    # Clamp to minimum of 1 day to avoid log(0) issues.
    days_ago_clamped = case(
        (days_ago < 1, 1.0),
        else_=days_ago,
    )

    # Logarithmic decay: ln(1 + 365 / days_ago).
    # This gives ~6 for today, ~4 for 1 week, ~1 for 1 year.
    recency_score = func.ln(1.0 + 365.0 / days_ago_clamped)

    return float(cfg.recency_coef) * recency_score


def get_ts_rank_weights_array(cfg: RankingConfig) -> str:
    """
    Get the ts_rank weights array as a Postgres array literal string.

    Returns format suitable for use with ts_rank: '{D, C, B, A}'.
    Weights correspond to: D (default), C (URL), B (body), A (title).
    """
    d, c, b, a = cfg.ts_rank_weights
    return f"'{{{d}, {c}, {b}, {a}}}'"


def literal_zero() -> ColumnElement[float]:
    return literal(0.0)


def literal_false() -> ColumnElement[bool]:
    return literal(False)


__all__ = [
    "RankingVersion",
    "QueryMode",
    "RankingConfig",
    "get_ranking_version",
    "tokenize_query",
    "classify_query_mode",
    "get_ranking_config",
    "build_title_boost_expr",
    "build_archived_penalty_expr",
    "build_archived_penalty_expr_v3",
    "build_depth_penalty_expr",
    "build_authority_boost_expr_postgres",
    "build_authority_tier_expr",
    "build_title_exact_match_boost_expr",
    "build_recency_boost_expr_postgres",
    "get_ts_rank_weights_array",
]
