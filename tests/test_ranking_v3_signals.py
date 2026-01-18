"""Tests for v3 ranking signal improvements."""

from __future__ import annotations

from ha_backend.search_ranking import (
    QueryMode,
    RankingConfig,
    RankingVersion,
    get_ranking_config,
    get_ts_rank_weights_array,
)


class TestRankingConfigV3:
    """Tests for v3 ranking configuration."""

    def test_v3_config_has_recency_coef_for_broad(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        assert cfg.recency_coef > 0
        assert cfg.recency_coef == 0.15

    def test_v3_config_has_recency_coef_for_mixed(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.mixed, version=RankingVersion.v3)
        assert cfg.recency_coef > 0
        assert cfg.recency_coef == 0.08

    def test_v3_config_no_recency_for_specific(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.specific, version=RankingVersion.v3)
        assert cfg.recency_coef == 0.0

    def test_v3_config_has_title_exact_match_boost(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        assert cfg.title_exact_match_boost > 0
        assert cfg.title_exact_match_boost == 0.35

    def test_v3_config_has_tuned_ts_rank_weights(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        # v3 uses stronger title weight (A), weaker URL weight (D).
        d, c, b, a = cfg.ts_rank_weights
        assert a > 1.0  # Title boosted above default.
        assert d < 0.1  # URL weight reduced.

    def test_v1_v2_config_uses_default_ts_rank_weights(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v2)
        # Should have default values.
        assert cfg.ts_rank_weights == (0.1, 0.2, 0.4, 1.0)
        assert cfg.title_exact_match_boost == 0.0
        assert cfg.recency_coef == 0.0


class TestTsRankWeightsArray:
    """Tests for ts_rank weights formatting."""

    def test_formats_weights_as_postgres_array(self) -> None:
        cfg = RankingConfig(
            authority_coef=0.0,
            hubness_coef=0.0,
            pagerank_coef=0.0,
            depth_coef=0.0,
            archived_penalty=0.0,
            title_all_tokens_boost=0.0,
            title_any_token_boost=0.0,
            ts_rank_weights=(0.1, 0.2, 0.4, 1.0),
        )
        result = get_ts_rank_weights_array(cfg)
        assert result == "'{0.1, 0.2, 0.4, 1.0}'"

    def test_formats_v3_broad_weights(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        result = get_ts_rank_weights_array(cfg)
        assert result == "'{0.05, 0.15, 0.35, 1.2}'"


class TestTitleExactMatchBoost:
    """Tests for title exact-match boost behavior."""

    def test_exact_match_boost_enabled_in_v3(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        assert cfg.title_exact_match_boost > cfg.title_all_tokens_boost

    def test_exact_match_boost_disabled_in_v2(self) -> None:
        cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v2)
        assert cfg.title_exact_match_boost == 0.0


class TestRecencyBoost:
    """Tests for recency boost behavior expectations."""

    def test_recency_stronger_for_broad_than_mixed(self) -> None:
        broad_cfg = get_ranking_config(mode=QueryMode.broad, version=RankingVersion.v3)
        mixed_cfg = get_ranking_config(mode=QueryMode.mixed, version=RankingVersion.v3)
        assert broad_cfg.recency_coef > mixed_cfg.recency_coef

    def test_recency_disabled_for_specific(self) -> None:
        specific_cfg = get_ranking_config(mode=QueryMode.specific, version=RankingVersion.v3)
        assert specific_cfg.recency_coef == 0.0
