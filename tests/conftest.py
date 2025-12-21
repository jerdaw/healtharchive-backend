from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make tests deterministic even when run in a production-like shell.

    Some deployments export env vars (e.g. HEALTHARCHIVE_ENV=production,
    HEALTHARCHIVE_ADMIN_TOKEN, HEALTHARCHIVE_REPLAY_BASE_URL) that legitimately
    change API/CLI behavior. If those leak into pytest runs, tests can fail
    depending on the host environment.
    """
    # Force a non-production env so admin endpoints aren't "fail closed" when
    # HEALTHARCHIVE_ADMIN_TOKEN is intentionally unset in tests.
    monkeypatch.setenv("HEALTHARCHIVE_ENV", "test")

    # Clear deployment-sensitive toggles unless an individual test sets them.
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_REPLAY_BASE_URL", raising=False)
    monkeypatch.delenv("HA_SEARCH_RANKING_VERSION", raising=False)

