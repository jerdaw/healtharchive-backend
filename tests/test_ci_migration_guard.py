from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest


def _load_script_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "ci_migration_guard.py"
    spec = importlib.util.spec_from_file_location("ha_test_ci_migration_guard", script_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _minimal_models_source(extra_snapshot_lines: str = "") -> str:
    return f"""
from sqlalchemy.orm import mapped_column


class Snapshot:
    id = mapped_column()
{extra_snapshot_lines}
"""


def test_guard_passes_for_non_schema_changes() -> None:
    mod = _load_script_module()
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    decision = evaluate_changed_content(
        ["docs/README.md"],
        base_models_source=None,
        head_models_source=None,
        schema_ddl_hits=[],
    )

    assert decision.ok is True
    assert decision.schema_signals == ()


def test_guard_fails_when_model_columns_change_without_migration() -> None:
    mod = _load_script_module()
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    decision = evaluate_changed_content(
        ["src/ha_backend/models.py"],
        base_models_source=_minimal_models_source(),
        head_models_source=_minimal_models_source("    is_archived = mapped_column()\n"),
        schema_ddl_hits=[],
    )

    assert decision.ok is False
    assert any("Snapshot" in signal for signal in decision.schema_signals)


def test_guard_passes_when_model_columns_change_with_migration() -> None:
    mod = _load_script_module()
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    migration_path = "alembic/versions/0015_snapshot_guardrail.py"
    decision = evaluate_changed_content(
        ["src/ha_backend/models.py", migration_path],
        base_models_source=_minimal_models_source(),
        head_models_source=_minimal_models_source("    is_archived = mapped_column()\n"),
        schema_ddl_hits=[],
    )

    assert decision.ok is True
    assert decision.migration_files == (migration_path,)


def test_guard_ignores_models_file_non_column_edits() -> None:
    mod = _load_script_module()
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    before = _minimal_models_source()
    after = _minimal_models_source() + "\n# trailing comment change\n"
    decision = evaluate_changed_content(
        ["src/ha_backend/models.py"],
        base_models_source=before,
        head_models_source=after,
        schema_ddl_hits=[],
    )

    assert decision.ok is True
    assert decision.schema_signals == ()


def test_guard_fails_on_direct_schema_ddl_without_migration() -> None:
    mod = _load_script_module()
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    decision = evaluate_changed_content(
        ["src/ha_backend/search.py"],
        base_models_source=None,
        head_models_source=None,
        schema_ddl_hits=["ALTER TABLE snapshots ADD COLUMN is_archived BOOLEAN"],
    )

    assert decision.ok is False
    assert any("app DDL detected" in signal for signal in decision.schema_signals)


def test_guard_passes_when_signal_matches_active_exception_rule() -> None:
    mod = _load_script_module()
    ExceptionRule = getattr(mod, "ExceptionRule")
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    rule = ExceptionRule(
        path_glob="src/ha_backend/search.py",
        signal_pattern_raw=r"app DDL detected: .*CREATE TEMP TABLE.*",
        signal_pattern=re.compile(r"app DDL detected: .*CREATE TEMP TABLE.*"),
        expires_on=date(2026, 2, 20),
        reason="Temp table for query planning only.",
        source=".github/migration-guard-exceptions.txt:12",
    )

    decision = evaluate_changed_content(
        ["src/ha_backend/search.py"],
        base_models_source=None,
        head_models_source=None,
        schema_ddl_hits=["CREATE TEMP TABLE temp_rank AS SELECT 1"],
        exception_rules=[rule],
    )

    assert decision.ok is True
    assert decision.migration_files == ()
    assert decision.matched_exception_rules


def test_guard_fails_when_exception_rule_does_not_match_changed_path() -> None:
    mod = _load_script_module()
    ExceptionRule = getattr(mod, "ExceptionRule")
    evaluate_changed_content = getattr(mod, "evaluate_changed_content")

    rule = ExceptionRule(
        path_glob="src/ha_backend/other.py",
        signal_pattern_raw=r"app DDL detected: .*CREATE TEMP TABLE.*",
        signal_pattern=re.compile(r"app DDL detected: .*CREATE TEMP TABLE.*"),
        expires_on=date(2026, 2, 20),
        reason="Temp table for query planning only.",
        source=".github/migration-guard-exceptions.txt:12",
    )

    decision = evaluate_changed_content(
        ["src/ha_backend/search.py"],
        base_models_source=None,
        head_models_source=None,
        schema_ddl_hits=["CREATE TEMP TABLE temp_rank AS SELECT 1"],
        exception_rules=[rule],
    )

    assert decision.ok is False
    assert decision.matched_exception_rules == ()


def test_load_exception_rules_rejects_far_future_expiry(tmp_path: Path) -> None:
    mod = _load_script_module()
    load_exception_rules = getattr(mod, "_load_exception_rules")

    repo_root = tmp_path
    github_dir = repo_root / ".github"
    github_dir.mkdir(parents=True)
    (github_dir / "migration-guard-exceptions.txt").write_text(
        ("src/ha_backend/search.py|app DDL detected: .*|2026-06-30|Too long-lived override\n"),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="too far in the future"):
        _ = load_exception_rules(
            repo_root,
            exceptions_file=".github/migration-guard-exceptions.txt",
            today=date(2026, 2, 6),
        )
