from __future__ import annotations

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


def get_ranking_version(explicit: str | None) -> RankingVersion:
    """
    Determine ranking version from query param or environment.

    Environment variable:
      - HA_SEARCH_RANKING_VERSION: "v1" (default) or "v2"
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


def get_ranking_config(*, mode: QueryMode) -> RankingConfig:
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
    if cfg.archived_penalty == 0:
        return literal_zero()
    return case(
        (title_expr.ilike("archived%"), float(cfg.archived_penalty)),
        else_=0.0,
    )


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
    "build_depth_penalty_expr",
    "build_authority_boost_expr_postgres",
    "build_authority_tier_expr",
]
