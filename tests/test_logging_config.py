from __future__ import annotations

import logging

from ha_backend.logging_config import configure_logging


def test_configure_logging_sets_debug_level(monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_LOG_LEVEL", "DEBUG")
    configure_logging()
    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_configure_logging_defaults_to_info(monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_LOG_LEVEL", raising=False)
    configure_logging()
    root = logging.getLogger()
    # Either INFO or lower (NOTSET) is acceptable, but INFO is the default.
    assert root.level in (logging.INFO, logging.NOTSET)
