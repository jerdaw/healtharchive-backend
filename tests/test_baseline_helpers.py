from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _add_scripts_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def test_split_host_port_parses_common_ss_formats() -> None:
    _add_scripts_to_path()
    import baseline_snapshot

    assert baseline_snapshot._split_host_port("127.0.0.1:3000") == ("127.0.0.1", 3000)
    assert baseline_snapshot._split_host_port("*:80") == ("*", 80)
    assert baseline_snapshot._split_host_port("[::1]:9090") == ("::1", 9090)
    assert baseline_snapshot._split_host_port("[::]:9090") == ("::", 9090)
    assert baseline_snapshot._split_host_port("") is None
    assert baseline_snapshot._split_host_port("not-a-host") is None
    assert baseline_snapshot._split_host_port("127.0.0.1:not-a-port") is None


def test_is_loopback_addr_covers_ipv4_and_ipv6() -> None:
    _add_scripts_to_path()
    import check_baseline_drift

    assert check_baseline_drift._is_loopback_addr("127.0.0.1") is True
    assert check_baseline_drift._is_loopback_addr("127.255.255.255") is True
    assert check_baseline_drift._is_loopback_addr("::1") is True
    assert check_baseline_drift._is_loopback_addr("0:0:0:0:0:0:0:1") is True

    assert check_baseline_drift._is_loopback_addr("") is False
    assert check_baseline_drift._is_loopback_addr("0.0.0.0") is False
    assert check_baseline_drift._is_loopback_addr("::") is False
    assert check_baseline_drift._is_loopback_addr("100.65.87.34") is False


def test_baseline_drift_warns_on_unexpected_non_loopback_ports() -> None:
    _add_scripts_to_path()
    import check_baseline_drift

    policy: dict[str, Any] = {
        "network": {
            "allowed_non_loopback_tcp_ports": [],
        }
    }
    observed: dict[str, Any] = {
        "inputs": {"mode": "local"},
        "network": {
            "tcp_listeners": [
                {"address": "127.0.0.1", "port": 3000, "local": "127.0.0.1:3000"},
                {"address": "100.65.87.34", "port": 46226, "local": "100.65.87.34:46226"},
            ]
        },
    }

    _required, warned = check_baseline_drift.evaluate(policy, observed)
    warn_keys = {f.key for f in warned}
    assert "network:tcp:unexpected_non_loopback_ports" in warn_keys


def test_baseline_drift_does_not_warn_when_only_loopback_listeners_exist() -> None:
    _add_scripts_to_path()
    import check_baseline_drift

    policy: dict[str, Any] = {
        "network": {
            "allowed_non_loopback_tcp_ports": [],
        }
    }
    observed: dict[str, Any] = {
        "inputs": {"mode": "local"},
        "network": {
            "tcp_listeners": [
                {"address": "127.0.0.1", "port": 3000, "local": "127.0.0.1:3000"},
                {"address": "::1", "port": 9090, "local": "[::1]:9090"},
            ]
        },
    }

    _required, warned = check_baseline_drift.evaluate(policy, observed)
    warn_keys = {f.key for f in warned}
    assert "network:tcp:unexpected_non_loopback_ports" not in warn_keys
