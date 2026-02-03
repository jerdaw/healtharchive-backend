from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("vps_run_db_job_detached", str(path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_retry_first_sources_env_file(monkeypatch, tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "vps-run-db-job-detached.py"
    mod = _load_script_module(script_path)

    env_file = tmp_path / "backend.env"
    env_file.write_text("HEALTHARCHIVE_DATABASE_URL=sqlite:///dev/null\n")

    calls: list[tuple[list[str], str]] = []

    class DummyCompletedProcess:
        returncode: int = 0
        stdout: str = ""
        stderr: str = ""

    def fake_run(
        argv: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        cwd: str,
    ) -> DummyCompletedProcess:
        calls.append((list(argv), str(cwd)))
        _ = (check, capture_output, text)
        return DummyCompletedProcess()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    run_with_env_file = getattr(mod, "_run_with_env_file")
    run_with_env_file(
        ["echo", "hello"],
        env_file=env_file,
        cwd=tmp_path,
    )

    assert calls, "expected subprocess.run to be called"
    argv, cwd = calls[0]
    assert argv[:2] == ["bash", "-lc"]
    assert cwd == str(tmp_path)
    script = argv[2]
    assert "source" in script
    assert str(env_file) in script
    assert "echo hello" in script
