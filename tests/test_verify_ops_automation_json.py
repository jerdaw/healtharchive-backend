from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_verify_ops_automation_json_only_emits_single_json_line() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "verify_ops_automation.sh"

    env = os.environ.copy()
    # Force the "systemctl not found" path so this test is deterministic and
    # doesn't depend on systemd being PID 1 on the CI runner.
    env["PATH"] = "/nonexistent"

    result = subprocess.run(
        ["/usr/bin/bash", str(script_path), "--json-only"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["schema_version"] == 1
    assert payload["skipped"] is True
    assert payload["ok"] is True
    assert payload["failures"] == 0
    assert isinstance(payload["warnings"], int)
