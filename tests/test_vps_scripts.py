import sys
from pathlib import Path
from unittest.mock import patch

# Import the script module.
# Since it's a script in scripts/, we might need to add it to sys.path or import by path.
# For simplicity in this test, we can use runpy or just import it if we make it importable.
# But scripts/ isn't a package. Let's use SourceFileLoader or just mock the logic?
# Better: Make the script importable by adding scripts/ to sys.path temporarily.

repo_root = Path(__file__).parent.parent
script_path = repo_root / "scripts" / "vps-tiering-metrics-textfile.py"

# Add scripts dir to path to allow import
sys.path.append(str(repo_root / "scripts"))

# We import the module name. Since it has dashes, we must use importlib.
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("vps_tiering_metrics", script_path)
assert spec and spec.loader
vps_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vps_script)


class TestVpsTieringMetrics:
    def test_dt_to_epoch_seconds(self):
        dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts = vps_script._dt_to_epoch_seconds(dt)
        assert ts == 1735732800

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_is_mountpoint_true(self, mock_exists, mock_run):
        mock_exists.return_value = True
        mock_run.return_value.returncode = 0
        assert vps_script._is_mountpoint(Path("/mnt/test")) is True

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_is_mountpoint_false(self, mock_exists, mock_run):
        mock_exists.return_value = True
        mock_run.return_value.returncode = 1
        # Fallback to 'mount' command parsing
        mock_run.return_value.stdout = "something on /other typ x"
        assert vps_script._is_mountpoint(Path("/mnt/test")) is False

    @patch("pathlib.Path.stat")
    def test_probe_readable_dir_ok(self, mock_stat):
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o755
        with patch("os.listdir", return_value=[]):
            ok, errno = vps_script._probe_readable_dir(Path("/tmp"))
            assert ok == 1
            assert errno == -1

    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.is_file")
    def test_read_manifest_hot_paths(self, mock_is_file, mock_read):
        mock_is_file.return_value = True
        mock_read.return_value = """
        # Comment
        /source /target
        /source2 /target2
        """
        ok, paths = vps_script._read_manifest_hot_paths(Path("/etc/manifest"))
        assert ok == 1
        assert len(paths) == 2
        assert paths[0] == Path("/target")

    def test_main_runs(self, tmp_path):
        # Create a dummy manifest
        manifest = tmp_path / "manifest"
        manifest.write_text("/src /dst\n")

        # Output file
        out_file = "test.prom"

        # Mocking external calls to avoid actual system interactions
        with (
            patch.object(vps_script, "_is_mountpoint", return_value=True),
            patch.object(vps_script, "_probe_readable_dir", return_value=(1, -1)),
            patch.object(vps_script, "_unit_ok", return_value=1),
            patch.object(vps_script, "_unit_failed", return_value=0),
        ):
            ret = vps_script.main(
                [
                    "--out-dir",
                    str(tmp_path),
                    "--out-file",
                    out_file,
                    "--manifest",
                    str(manifest),
                    "--storagebox-mount",
                    str(tmp_path / "storagebox"),
                ]
            )

            assert ret == 0
            assert (tmp_path / out_file).exists()
            content = (tmp_path / out_file).read_text()
            assert "healtharchive_storagebox_mount_ok 1" in content


import stat  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
