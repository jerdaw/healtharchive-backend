from __future__ import annotations

from pathlib import Path


def _read(relative_path: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / relative_path).read_text(encoding="utf-8")


def test_systemd_public_surface_verifier_uses_apex_frontend() -> None:
    text = _read("docs/deployment/systemd/healtharchive-public-surface-verify.service")
    assert "--frontend-base https://healtharchive.ca" in text


def test_active_docs_do_not_treat_vercel_as_current_healtharchive_path() -> None:
    production_rollout = _read("docs/deployment/production-rollout-checklist.md")
    staging_rollout = _read("docs/deployment/staging-rollout-checklist.md")
    architecture = _read("docs/architecture.md")

    assert "https://healtharchive.vercel.app" not in production_rollout
    assert "https://healtharchive.vercel.app" not in architecture
    assert "There is no active standalone staging backend/frontend path" in staging_rollout


def test_active_docs_reflect_apex_canonical_frontend() -> None:
    production_rollout = _read("docs/deployment/production-rollout-checklist.md")
    production_runbook = _read("docs/deployment/production-single-vps.md")

    assert "https://healtharchive.ca" in production_rollout
    assert "https://www.healtharchive.ca" in production_rollout
    assert "https://api.healtharchive.ca" in production_rollout
    assert "`healtharchive.ca` (canonical)" in production_runbook
    assert "`www.healtharchive.ca` (redirect alias)" in production_runbook


def test_active_entrypoints_point_shared_vps_facts_to_platform_ops() -> None:
    for relative_path in (
        "README.md",
        "AGENTS.md",
        "ENVIRONMENTS.md",
        "docs/README.md",
        "docs/deployment/production-rollout-checklist.md",
        "docs/deployment/staging-rollout-checklist.md",
    ):
        assert (
            "/home/jer/repos/platform-ops/PLAT-009-shared-vps-documentation-boundary.md"
            in _read(relative_path)
        )

    production_runbook = _read("docs/deployment/production-single-vps.md")
    env_contract = _read("docs/deployment/environments-and-configuration.md")

    assert "/home/jer/repos/platform-ops" in production_runbook
    assert "canonical" in production_runbook
    assert "/home/jer/repos/platform-ops" in env_contract
    assert "canonical" in env_contract


if __name__ == "__main__":
    test_systemd_public_surface_verifier_uses_apex_frontend()
    test_active_docs_do_not_treat_vercel_as_current_healtharchive_path()
    test_active_docs_reflect_apex_canonical_frontend()
    test_active_entrypoints_point_shared_vps_facts_to_platform_ops()
