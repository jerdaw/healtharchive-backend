from __future__ import annotations

import os
import sys
from pathlib import Path

from ha_backend.config import (
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_ARCHIVE_TOOL_CMD,
    DEFAULT_DATABASE_URL,
    ArchiveToolConfig,
    DatabaseConfig,
    get_archive_tool_config,
    get_database_config,
    get_exports_default_limit,
    get_exports_enabled,
    get_exports_max_limit,
)


def test_archive_tool_config_defaults(monkeypatch) -> None:
    """
    With no environment overrides, get_archive_tool_config should use defaults.
    """
    monkeypatch.delenv("HEALTHARCHIVE_ARCHIVE_ROOT", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_TOOL_CMD", raising=False)

    candidate = Path(sys.executable).resolve().parent / "archive-tool"
    expected_cmd = (
        str(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK)
        else DEFAULT_ARCHIVE_TOOL_CMD
    )

    cfg = get_archive_tool_config()
    assert isinstance(cfg, ArchiveToolConfig)
    assert cfg.archive_tool_cmd == expected_cmd
    assert isinstance(cfg.archive_root, Path)
    assert cfg.archive_root == DEFAULT_ARCHIVE_ROOT


def test_archive_tool_config_env_overrides(monkeypatch, tmp_path) -> None:
    """
    Environment variables should override archive root and tool command.
    """
    custom_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(custom_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "custom-archive-tool")

    cfg = get_archive_tool_config()
    assert cfg.archive_root == custom_root
    assert cfg.archive_tool_cmd == "custom-archive-tool"


def test_database_config_default(monkeypatch) -> None:
    """
    Default database URL should match the constant when no env override is set.
    """
    monkeypatch.delenv("HEALTHARCHIVE_DATABASE_URL", raising=False)
    cfg = get_database_config()
    assert isinstance(cfg, DatabaseConfig)
    assert cfg.database_url == DEFAULT_DATABASE_URL


def test_database_config_env_override(monkeypatch) -> None:
    """
    HEALTHARCHIVE_DATABASE_URL should override the default URL.
    """
    custom_url = "sqlite:///custom_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", custom_url)
    cfg = get_database_config()
    assert cfg.database_url == custom_url


def test_exports_config_defaults(monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_EXPORTS_ENABLED", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_EXPORTS_DEFAULT_LIMIT", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_EXPORTS_MAX_LIMIT", raising=False)

    assert get_exports_enabled() is True
    assert get_exports_default_limit() > 0
    assert get_exports_max_limit() >= get_exports_default_limit()


def test_exports_config_overrides(monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_EXPORTS_ENABLED", "0")
    monkeypatch.setenv("HEALTHARCHIVE_EXPORTS_DEFAULT_LIMIT", "50")
    monkeypatch.setenv("HEALTHARCHIVE_EXPORTS_MAX_LIMIT", "75")

    assert get_exports_enabled() is False
    assert get_exports_default_limit() == 50
    assert get_exports_max_limit() == 75
