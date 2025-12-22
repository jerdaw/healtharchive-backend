from __future__ import annotations

from ha_backend.archive_contract import ArchiveJobConfig, ArchiveToolOptions, validate_tool_options


def test_archive_tool_options_round_trip_defaults() -> None:
    """
    Defaults should round-trip via to_dict/from_dict and preserve values.
    """
    opts = ArchiveToolOptions()
    data = opts.to_dict()

    # Core defaults should be present.
    assert data["cleanup"] is False
    assert data["overwrite"] is False
    assert data["enable_monitoring"] is False
    assert data["enable_adaptive_workers"] is False
    assert data["enable_vpn_rotation"] is False
    assert data["initial_workers"] == 1
    assert data["log_level"] == "INFO"
    assert data["relax_perms"] is False

    # Optional fields should be omitted when None.
    assert "monitor_interval_seconds" not in data
    assert "stall_timeout_minutes" not in data
    assert "error_threshold_timeout" not in data
    assert "error_threshold_http" not in data
    assert "min_workers" not in data
    assert "max_worker_reductions" not in data
    assert "vpn_connect_command" not in data
    assert "max_vpn_rotations" not in data
    assert "vpn_rotation_frequency_minutes" not in data
    assert "backoff_delay_minutes" not in data

    loaded = ArchiveToolOptions.from_dict(data)
    assert loaded == opts


def test_archive_tool_options_round_trip_with_optional_fields() -> None:
    """
    When optional fields are set, they should be included in the dict and
    restored by from_dict.
    """
    opts = ArchiveToolOptions(
        cleanup=True,
        overwrite=True,
        enable_monitoring=True,
        enable_adaptive_workers=True,
        enable_vpn_rotation=True,
        initial_workers=3,
        log_level="DEBUG",
        monitor_interval_seconds=10,
        stall_timeout_minutes=5,
        error_threshold_timeout=3,
        error_threshold_http=2,
        min_workers=1,
        max_worker_reductions=4,
        vpn_connect_command="vpn connect",
        max_vpn_rotations=6,
        vpn_rotation_frequency_minutes=30,
        backoff_delay_minutes=20,
        relax_perms=True,
    )

    data = opts.to_dict()
    # Ensure optional fields are serialized.
    assert data["monitor_interval_seconds"] == 10
    assert data["stall_timeout_minutes"] == 5
    assert data["error_threshold_timeout"] == 3
    assert data["error_threshold_http"] == 2
    assert data["min_workers"] == 1
    assert data["max_worker_reductions"] == 4
    assert data["vpn_connect_command"] == "vpn connect"
    assert data["max_vpn_rotations"] == 6
    assert data["vpn_rotation_frequency_minutes"] == 30
    assert data["backoff_delay_minutes"] == 20

    loaded = ArchiveToolOptions.from_dict(data)
    assert loaded == opts


def test_archive_job_config_round_trip() -> None:
    """
    ArchiveJobConfig should round-trip between object and dict forms.
    """
    tool_opts = ArchiveToolOptions(
        enable_monitoring=True,
        initial_workers=2,
        log_level="WARNING",
    )
    cfg = ArchiveJobConfig(
        seeds=["https://example.org", "https://example.com"],
        zimit_passthrough_args=["--pageLimit", "10"],
        tool_options=tool_opts,
    )

    data = cfg.to_dict()
    assert data["seeds"] == ["https://example.org", "https://example.com"]
    assert data["zimit_passthrough_args"] == ["--pageLimit", "10"]
    assert data["tool_options"]["enable_monitoring"] is True
    assert data["tool_options"]["initial_workers"] == 2

    loaded = ArchiveJobConfig.from_dict(data)
    assert loaded.seeds == cfg.seeds
    assert loaded.zimit_passthrough_args == cfg.zimit_passthrough_args
    assert loaded.tool_options == tool_opts


def test_validate_tool_options_enforces_invariants() -> None:
    """
    validate_tool_options should enforce the same invariants as build_job_config.
    """
    # enable_adaptive_workers requires enable_monitoring.
    opts = ArchiveToolOptions(enable_adaptive_workers=True, enable_monitoring=False)
    try:
        validate_tool_options(opts)
    except ValueError as exc:
        assert "enable_adaptive_workers" in str(exc)
    else:
        assert False, "Expected ValueError for adaptive without monitoring"

    # enable_vpn_rotation requires enable_monitoring.
    opts = ArchiveToolOptions(enable_vpn_rotation=True, enable_monitoring=False)
    try:
        validate_tool_options(opts)
    except ValueError as exc:
        assert "enable_vpn_rotation" in str(exc)
    else:
        assert False, "Expected ValueError for VPN rotation without monitoring"

    # enable_vpn_rotation requires vpn_connect_command.
    opts = ArchiveToolOptions(enable_vpn_rotation=True, enable_monitoring=True)
    try:
        validate_tool_options(opts)
    except ValueError as exc:
        assert "vpn_connect_command" in str(exc)
    else:
        assert False, "Expected ValueError for VPN rotation without vpn_connect_command"

    # A valid combination should pass.
    opts = ArchiveToolOptions(
        enable_vpn_rotation=True,
        enable_monitoring=True,
        vpn_connect_command="vpn connect",
    )
    validate_tool_options(opts)
