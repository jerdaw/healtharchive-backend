from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import (
    HC_CANADA_CA_SCOPE_INCLUDE_RX,
    PHAC_CANADA_CA_SCOPE_INCLUDE_RX,
    SOURCE_JOB_CONFIGS,
    build_job_config,
    build_output_dir_for_job,
    create_job_for_source,
    generate_job_name,
    get_config_for_source,
)
from ha_backend.models import ArchiveJob
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "job_registry.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_get_config_for_source_known_sources() -> None:
    hc_cfg = get_config_for_source("hc")
    phac_cfg = get_config_for_source("PHAC")  # case-insensitive
    cihr_cfg = get_config_for_source("cihr")

    assert hc_cfg is not None
    assert phac_cfg is not None
    assert cihr_cfg is not None
    assert hc_cfg.source_code == "hc"
    assert phac_cfg.source_code == "phac"
    assert cihr_cfg.source_code == "cihr"
    assert hc_cfg.default_seeds
    assert phac_cfg.default_seeds
    assert cihr_cfg.default_seeds
    # Annual defaults are tuned for search-first throughput on the single VPS.
    assert hc_cfg.default_tool_options["skip_final_build"] is True
    assert hc_cfg.default_tool_options["docker_shm_size"] == "1g"
    assert hc_cfg.default_tool_options["initial_workers"] == 2
    assert hc_cfg.default_tool_options["stall_timeout_minutes"] == 60

    assert phac_cfg.default_tool_options["skip_final_build"] is True
    assert phac_cfg.default_tool_options["docker_shm_size"] == "1g"
    assert phac_cfg.default_tool_options["initial_workers"] == 2
    assert phac_cfg.default_tool_options["stall_timeout_minutes"] == 60

    assert cihr_cfg.default_tool_options["skip_final_build"] is True
    assert cihr_cfg.default_tool_options["docker_shm_size"] == "1g"
    assert cihr_cfg.default_tool_options["initial_workers"] == 2


def test_get_config_for_source_unknown() -> None:
    assert get_config_for_source("unknown-source") is None


def test_generate_job_name_uses_template_and_date() -> None:
    cfg = SOURCE_JOB_CONFIGS["hc"]
    fixed = datetime(2025, 12, 9, 12, 0, tzinfo=timezone.utc)
    name = generate_job_name(cfg, now=fixed)
    assert name.startswith("hc-")
    assert name.endswith("20251209")


def test_build_output_dir_for_job_layout(tmp_path) -> None:
    archive_root = tmp_path / "archive_root"
    source_code = "hc"
    job_name = "hc-20251209"
    fixed = datetime(2025, 12, 9, 12, 0, tzinfo=timezone.utc)

    output_dir = build_output_dir_for_job(
        source_code,
        job_name,
        archive_root=archive_root,
        now=fixed,
    )

    # Should be namespaced under <archive_root>/<source_code>/<ts>__<name>
    assert output_dir.parent.parent == archive_root
    assert output_dir.parent.name == source_code

    prefix, sep, suffix = output_dir.name.partition("__")
    assert sep == "__"
    assert suffix == job_name.replace(" ", "_")
    assert prefix.endswith("Z")


def test_build_job_config_merges_defaults_and_overrides() -> None:
    cfg = SOURCE_JOB_CONFIGS["hc"]
    overrides = {"cleanup": True, "initial_workers": 4}

    config = build_job_config(cfg, extra_seeds=["https://extra.example"], overrides=overrides)

    assert "seeds" in config
    assert "zimit_passthrough_args" in config
    assert "tool_options" in config

    seeds = config["seeds"]
    assert cfg.default_seeds[0] in seeds
    assert "https://extra.example" in seeds

    tool_options = config["tool_options"]
    assert tool_options["cleanup"] is True
    assert tool_options["initial_workers"] == 4
    # Unmodified options should still be present.
    assert tool_options["log_level"] == cfg.default_tool_options["log_level"]

    # Ensure default scope constraints are preserved.
    assert config["zimit_passthrough_args"] == cfg.default_zimit_passthrough_args


def test_canada_ca_scope_regexes_match_expected_urls() -> None:
    hc_rx = re.compile(HC_CANADA_CA_SCOPE_INCLUDE_RX)
    phac_rx = re.compile(PHAC_CANADA_CA_SCOPE_INCLUDE_RX)

    assert hc_rx.match("https://www.canada.ca/en/health-canada.html")
    assert hc_rx.match("https://www.canada.ca/fr/sante-canada.html")
    assert hc_rx.match("https://www.canada.ca/en/health-canada/services/drugs-health-products.html")
    assert not hc_rx.match(
        "https://www.canada.ca/en/health-canada/services/drugs-health-products.html?utm_source=x"
    )
    assert hc_rx.match("https://www.canada.ca/content/dam/hc-sc/images/corporate/example.jpg")
    assert hc_rx.match("https://www.canada.ca/etc/designs/canada/wet-boew/js/theme.min.js")
    assert hc_rx.match("https://www.canada.ca/etc/designs/canada/wet-boew/js/theme.min.js?v=1")
    assert not hc_rx.match("https://www.canada.ca/en/services/benefits.html")
    assert not hc_rx.match("https://www.canada.ca/en/public-health.html")
    assert not hc_rx.match("https://www.canada.ca/content/dam/phac-aspc/example.jpg")

    assert phac_rx.match("https://www.canada.ca/en/public-health.html")
    assert phac_rx.match("https://www.canada.ca/fr/sante-publique.html")
    assert phac_rx.match("https://www.canada.ca/en/public-health/services/diseases/measles.html")
    assert not phac_rx.match(
        "https://www.canada.ca/en/public-health/services/diseases/measles.html?utm_source=x"
    )
    assert phac_rx.match("https://www.canada.ca/content/dam/phac-aspc/images/corporate/example.jpg")
    assert phac_rx.match("https://www.canada.ca/etc/designs/canada/wet-boew/css/theme.min.css")
    assert phac_rx.match("https://www.canada.ca/etc/designs/canada/wet-boew/css/theme.min.css?v=1")
    assert not phac_rx.match("https://www.canada.ca/en/services/benefits.html")
    assert not phac_rx.match("https://www.canada.ca/en/health-canada.html")
    assert not phac_rx.match("https://www.canada.ca/content/dam/hc-sc/example.jpg")


def test_build_job_config_validates_adaptive_requires_monitoring() -> None:
    cfg = SOURCE_JOB_CONFIGS["hc"]
    overrides = {
        "enable_adaptive_workers": True,
        "enable_monitoring": False,
    }

    with pytest.raises(ValueError):
        build_job_config(cfg, overrides=overrides)


def test_build_job_config_validates_vpn_requires_monitoring_and_command() -> None:
    cfg = SOURCE_JOB_CONFIGS["hc"]

    # Missing monitoring
    overrides_no_monitor = {
        "enable_vpn_rotation": True,
        "vpn_connect_command": "nordvpn connect ca",
        "enable_monitoring": False,
    }
    with pytest.raises(ValueError):
        build_job_config(cfg, overrides=overrides_no_monitor)

    # Missing connect command
    overrides_no_command = {
        "enable_vpn_rotation": True,
        "enable_monitoring": True,
    }
    with pytest.raises(ValueError):
        build_job_config(cfg, overrides=overrides_no_command)

    # Valid combination should succeed
    overrides_valid = {
        "enable_vpn_rotation": True,
        "enable_monitoring": True,
        "vpn_connect_command": "nordvpn connect ca",
    }
    config = build_job_config(cfg, overrides=overrides_valid)
    tool_options = config["tool_options"]
    assert tool_options["enable_vpn_rotation"] is True
    assert tool_options["enable_monitoring"] is True
    assert tool_options["vpn_connect_command"] == "nordvpn connect ca"


@pytest.mark.parametrize(
    ("source_code", "expected_seeds"),
    [
        (
            "hc",
            [
                "https://www.canada.ca/en/health-canada.html",
                "https://www.canada.ca/fr/sante-canada.html",
            ],
        ),
        (
            "phac",
            [
                "https://www.canada.ca/en/public-health.html",
                "https://www.canada.ca/fr/sante-publique.html",
            ],
        ),
        (
            "cihr",
            [
                "https://cihr-irsc.gc.ca/e/193.html",
                "https://cihr-irsc.gc.ca/f/193.html",
            ],
        ),
    ],
)
def test_create_job_for_source_persists_archive_job(
    tmp_path, monkeypatch, source_code: str, expected_seeds: list[str]
) -> None:
    """
    create_job_for_source should create a queued ArchiveJob with a reasonable
    name, output_dir, and config.
    """
    _init_test_db(tmp_path, monkeypatch)

    # Use a temp archive root so we do not touch the real filesystem.
    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

    with get_session() as session:
        # Ensure Source rows exist.
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source(source_code, session=session)
        job_id = job_row.id
        output_dir = Path(job_row.output_dir)

    # Verify that the row was persisted correctly.
    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.source is not None
        assert stored.source.code == source_code

        cfg = stored.config or {}
        assert cfg.get("seeds")
        assert cfg["seeds"] == expected_seeds
        assert (
            cfg.get("zimit_passthrough_args")
            == SOURCE_JOB_CONFIGS[source_code].default_zimit_passthrough_args
        )
        assert cfg.get("tool_options")

    # Output dir should live under the configured archive root.
    assert str(output_dir).startswith(str(archive_root))
