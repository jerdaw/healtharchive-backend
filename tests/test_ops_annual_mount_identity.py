from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COLD_ROOT = Path("/test/cold")
COLD_DIR = COLD_ROOT / "jobs/hc/edition"
HOT_DIR = Path("/test/hot/hc/edition")


@pytest.fixture
def tiering() -> ModuleType:
    name = "ha_test_annual_mount_identity"
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts/vps-annual-output-tiering.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def mount_record(
    target: str,
    *,
    mount_id: str = "20",
    root: str = "/",
    device: str = "0:64",
    fstype: str = "fuse.sshfs",
    source: str = "archive.test:/data",
) -> dict[str, str]:
    return {
        "id": mount_id,
        "parent": "1",
        "major_minor": device,
        "root": root,
        "target": target,
        "options": "rw,relatime",
        "fstype": fstype,
        "source": source,
        "super_options": "rw",
    }


def expected_table(*, base_root: str = "/") -> list[dict[str, str]]:
    return [
        mount_record("/", mount_id="1", device="8:1", fstype="ext4", source="/dev/test"),
        mount_record(str(COLD_ROOT), mount_id="20", root=base_root),
        mount_record(
            str(HOT_DIR),
            mount_id="21",
            root=base_root.rstrip("/") + "/jobs/hc/edition",
        ),
    ]


def is_expected(tiering: ModuleType) -> bool:
    return tiering._is_expected_cold_archive_bind_mount(
        output_dir=HOT_DIR, cold_dir=COLD_DIR, cold_archive_root=COLD_ROOT
    )


@pytest.mark.parametrize("base_root", ["/", "/exports/archive"])
def test_correct_sshfs_bind_uses_one_table_without_bind_option(
    tiering, monkeypatch, base_root
) -> None:
    calls = []

    def read_mountinfo():
        calls.append(True)
        return expected_table(base_root=base_root)

    monkeypatch.setattr(tiering, "_read_mountinfo", read_mountinfo)
    assert is_expected(tiering)
    assert calls == [True]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("major_minor", "0:65"),
        ("fstype", "nfs4"),
        ("source", "other.test:/data"),
        ("root", "/jobs/phac/edition"),
        ("root", "/"),
    ],
)
def test_wrong_hot_mount_identity_fails_closed(tiering, monkeypatch, field, value) -> None:
    table = expected_table()
    table[-1][field] = value
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


@pytest.mark.parametrize(
    "target",
    [
        "/test/cold/jobs",
        "/test/cold/jobs/hc",
        "/test/cold/jobs/hc/edition",
        "/test/cold/jobs/hc/edition/warcs",
        "/test/hot/hc/edition/warcs",
    ],
)
def test_source_or_target_overmount_fails_closed(tiering, monkeypatch, target) -> None:
    table = expected_table()
    table.append(mount_record(target, mount_id="22", device="0:65"))
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


@pytest.mark.parametrize("target", [str(COLD_ROOT), str(HOT_DIR)])
def test_stacked_relevant_mounts_fail_even_with_same_filesystem(
    tiering, monkeypatch, target
) -> None:
    table = expected_table()
    duplicate = next(record.copy() for record in table if record["target"] == target)
    duplicate["id"] = "22"
    table.append(duplicate)
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


@pytest.mark.parametrize("missing", [str(COLD_ROOT), str(HOT_DIR)])
def test_missing_required_mount_fails_closed(tiering, monkeypatch, missing) -> None:
    table = [record for record in expected_table() if record["target"] != missing]
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


def test_unreadable_mount_table_is_not_expected(tiering, monkeypatch) -> None:
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: None)
    assert not is_expected(tiering)


@pytest.mark.parametrize(
    ("argument", "path"),
    [
        ("output_dir", Path("/test/hot/../hot/hc/edition")),
        ("cold_dir", Path("/test/cold/jobs/../jobs/hc/edition")),
        ("cold_archive_root", Path("//test/cold")),
        ("cold_dir", Path("/test/cold-other/jobs/hc/edition")),
        ("cold_dir", COLD_ROOT),
    ],
)
def test_noncanonical_or_outside_source_path_fails_closed(
    tiering, monkeypatch, argument, path
) -> None:
    monkeypatch.setattr(tiering, "_read_mountinfo", expected_table)
    arguments = {
        "output_dir": HOT_DIR,
        "cold_dir": COLD_DIR,
        "cold_archive_root": COLD_ROOT,
    }
    arguments[argument] = path
    assert not tiering._is_expected_cold_archive_bind_mount(**arguments)


def test_unrelated_mount_does_not_invalidate_exact_binding(tiering, monkeypatch) -> None:
    table = expected_table()
    table.append(mount_record("/test/unrelated", mount_id="22", device="0:99"))
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert is_expected(tiering)


def test_parser_preserves_identity_fields_and_optional_propagation(tiering) -> None:
    text = (
        "20 1 0:64 / /test/cold rw,relatime shared:7 - fuse.sshfs archive.test:/data rw\n"
        "21 1 0:64 /jobs/hc/edition /test/hot/hc/edition rw master:7 - "
        "fuse.sshfs archive.test:/data rw\n"
    )
    records = tiering._parse_mountinfo(text)
    assert records is not None and len(records) == 2
    for actual, expected in zip(records, expected_table()[1:], strict=True):
        for field in ("id", "major_minor", "root", "target", "fstype", "source"):
            assert actual[field] == expected[field]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not mountinfo\n",
        "20 1 0:64 / /test/cold rw\n",
        "20 1 0:64 / /test/cold rw - fuse.sshfs archive.test:/data\n",
        "20 1 bad / /test/cold rw - fuse.sshfs archive.test:/data rw\n",
        "20 1 0:64 / /test/cold/../other rw - fuse.sshfs archive.test:/data rw\n",
        "20 1 0:64 / //test/cold rw - fuse.sshfs archive.test:/data rw\n",
        "20 1 0:64 / /test/cold\x00 rw - fuse.sshfs archive.test:/data rw\n",
    ],
)
def test_malformed_mountinfo_fails_closed(tiering, text) -> None:
    assert tiering._parse_mountinfo(text) is None


def test_parser_rejects_duplicate_mount_ids(tiering) -> None:
    line = "20 1 0:64 / /test/cold rw - fuse.sshfs archive.test:/data rw\n"
    assert tiering._parse_mountinfo(line + line) is None


def test_parser_rejects_oversized_input(tiering, monkeypatch) -> None:
    line = "20 1 0:64 / /test/cold rw - fuse.sshfs archive.test:/data rw\n"
    monkeypatch.setattr(tiering, "_MOUNTINFO_MAX_BYTES", len(line.encode("utf-8")) - 1)
    assert tiering._parse_mountinfo(line) is None


def test_read_mountinfo_uses_a_bounded_binary_read(tiering, monkeypatch) -> None:
    line = b"20 1 0:64 / /test/cold rw - fuse.sshfs archive.test:/data rw\n"
    read_sizes = []
    open_modes = []

    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    def open_stream(mode):
        open_modes.append(mode)
        return BoundedStream(line)

    monkeypatch.setattr(tiering, "_MOUNTINFO_PATH", SimpleNamespace(open=open_stream))
    records = tiering._read_mountinfo()
    assert records is not None and len(records) == 1
    assert open_modes == ["rb"]
    assert read_sizes == [tiering._MOUNTINFO_MAX_BYTES + 1]


@pytest.mark.parametrize("data", [b"x" * 33, b"\xff", b"not mountinfo\n"])
def test_read_mountinfo_rejects_oversize_decode_or_parse_failure(
    tiering, monkeypatch, data
) -> None:
    monkeypatch.setattr(tiering, "_MOUNTINFO_MAX_BYTES", 32)
    monkeypatch.setattr(
        tiering, "_MOUNTINFO_PATH", SimpleNamespace(open=lambda _mode: io.BytesIO(data))
    )
    assert tiering._read_mountinfo() is None


def test_read_mountinfo_rejects_io_failure(tiering, monkeypatch) -> None:
    def failed_open(_mode):
        raise OSError("synthetic read failure")

    monkeypatch.setattr(tiering, "_MOUNTINFO_PATH", SimpleNamespace(open=failed_open))
    assert tiering._read_mountinfo() is None


def test_dry_run_readable_bind_label_cannot_bypass_identity(tiering, monkeypatch, capsys) -> None:
    item = tiering.TierPlanItem(
        job_id=101,
        source_code="hc",
        job_name="synthetic-edition",
        job_status="indexed",
        output_dir=HOT_DIR,
        cold_dir=COLD_DIR,
        mount_present=True,
        mount_source="other.test:/data",
        mount_fstype="fuse.sshfs",
        mount_options="rw,bind",
        output_dir_ok=1,
        output_dir_errno=0,
    )
    identity_calls = []

    def identity(**_kwargs):
        identity_calls.append(True)
        return False

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("dry-run must not invoke a subprocess or a mount mutation")

    monkeypatch.setattr(tiering, "_require_cold_archive_mounted", lambda _root: None)
    monkeypatch.setattr(tiering, "_plan", lambda **_kwargs: [item])
    monkeypatch.setattr(tiering, "_is_expected_cold_archive_bind_mount", identity)
    monkeypatch.setattr(tiering, "_mount_bind", unexpected_call)
    monkeypatch.setattr(tiering.subprocess, "run", unexpected_call)
    assert (
        tiering.main(
            [
                "--year",
                "2026",
                "--archive-root",
                "/test/hot",
                "--campaign-archive-root",
                "/test/cold/jobs",
                "--cold-archive-root",
                "/test/cold",
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "OK   job=" not in output.out
    assert "mounted_now=0" in output.out
    assert identity_calls == [True]


@pytest.mark.parametrize("failure", ["unreadable", "stacked", "descendant", "hidden-parent"])
def test_apply_repair_flags_cannot_authorize_unmount_on_unproven_identity(
    tiering, monkeypatch, capsys, failure
) -> None:
    table = expected_table()
    if failure == "stacked":
        duplicate = table[-1].copy()
        duplicate["id"] = "22"
        table.append(duplicate)
    elif failure == "descendant":
        table.append(mount_record(str(HOT_DIR / "warcs"), mount_id="22", device="0:65"))
    elif failure == "hidden-parent":
        table.append(mount_record("/test", mount_id="30", device="0:65"))
    item = tiering.TierPlanItem(
        job_id=101,
        source_code="hc",
        job_name="synthetic-edition",
        job_status="running",
        output_dir=HOT_DIR,
        cold_dir=COLD_DIR,
        mount_present=True,
        mount_source="archive.test:/data",
        mount_fstype="fuse.sshfs",
        mount_options="rw,bind",
        output_dir_ok=1,
        output_dir_errno=0,
    )
    mutation_calls = []

    def forbidden_mutation(*args, **_kwargs):
        mutation_calls.append(args)
        pytest.fail("an unproven mount identity must never authorize a mutation")

    monkeypatch.setattr(
        tiering, "_read_mountinfo", lambda: None if failure == "unreadable" else table
    )
    monkeypatch.setattr(tiering, "_require_cold_archive_mounted", lambda _root: None)
    monkeypatch.setattr(tiering, "_plan", lambda **_kwargs: [item])
    monkeypatch.setattr(tiering.os, "geteuid", lambda: 0)
    monkeypatch.setattr(tiering, "_mount_bind", forbidden_mutation)
    monkeypatch.setattr(tiering.subprocess, "run", forbidden_mutation)
    assert (
        tiering.main(
            [
                "--year",
                "2026",
                "--archive-root",
                "/test/hot",
                "--campaign-archive-root",
                "/test/cold/jobs",
                "--cold-archive-root",
                "/test/cold",
                "--apply",
                "--repair-unexpected-mounts",
                "--repair-stale-mounts",
                "--allow-repair-running-jobs",
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "expected_cold_archive_identity_unproven" in output.out
    assert "OK   job=" not in output.out
    assert "mounted_now=0" in output.out
    assert mutation_calls == []


@pytest.mark.parametrize("annual_present", [0, 1, 2, 3])
def test_legacy_coverage_does_not_substitute_for_each_annual_bind(
    tiering, monkeypatch, annual_present
) -> None:
    # Simulate reboot recovery restoring two legacy mounts, then zero to three
    # annual mounts. A global mount count cannot stand in for per-root identity.
    table = expected_table()[:-1]
    for index in range(2):
        table.append(
            mount_record(
                f"/test/hot/legacy-{index}",
                mount_id=str(30 + index),
                root=f"/jobs/legacy-{index}",
            )
        )
    sources = ("hc", "phac", "cihr")
    for index, source in enumerate(sources[:annual_present]):
        table.append(
            mount_record(
                f"/test/hot/{source}/edition",
                mount_id=str(40 + index),
                root=f"/jobs/{source}/edition",
            )
        )
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    annual_ready = [
        tiering._is_expected_cold_archive_bind_mount(
            output_dir=Path(f"/test/hot/{source}/edition"),
            cold_dir=COLD_ROOT / "jobs" / source / "edition",
            cold_archive_root=COLD_ROOT,
        )
        for source in sources
    ]
    assert annual_ready == [index < annual_present for index in range(3)]
    assert all(annual_ready) is (annual_present == 3)
    for index in range(2):
        assert tiering._is_expected_cold_archive_bind_mount(
            output_dir=Path(f"/test/hot/legacy-{index}"),
            cold_dir=COLD_ROOT / "jobs" / f"legacy-{index}",
            cold_archive_root=COLD_ROOT,
        )


def test_host_success_cannot_be_reused_for_missing_container_namespace_bind(
    tiering, monkeypatch
) -> None:
    # Feed independent synthetic namespace snapshots to the same identity
    # matcher. Host success must not be cached or inferred into the second view.
    host_table = expected_table()
    container_table = expected_table()[:-1]
    snapshots = iter([host_table, container_table])
    calls = []

    def read_namespace():
        calls.append(True)
        return next(snapshots)

    monkeypatch.setattr(tiering, "_read_mountinfo", read_namespace)
    assert is_expected(tiering)
    assert not is_expected(tiering)
    assert calls == [True, True]


@pytest.mark.parametrize("parent_id", ["1", "30"])
def test_covering_ancestor_requires_both_mounts_to_belong_to_visible_parent(
    tiering, monkeypatch, parent_id
) -> None:
    table = expected_table()
    table.append(mount_record("/test", mount_id="30", device="0:65"))
    table[1]["parent"] = parent_id
    table[2]["parent"] = parent_id
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert is_expected(tiering) is (parent_id == "30")


@pytest.mark.parametrize("parent_id", ["1", "30"])
def test_cold_only_covering_ancestor_requires_visible_parent(
    tiering, monkeypatch, parent_id
) -> None:
    cold_root = Path("/test/cold-parent/archive")
    table = expected_table()
    table[1]["target"] = str(cold_root)
    table[1]["parent"] = parent_id
    table.append(mount_record("/test/cold-parent", mount_id="30", device="0:65"))
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert tiering._is_expected_cold_archive_bind_mount(
        output_dir=HOT_DIR,
        cold_dir=cold_root / "jobs/hc/edition",
        cold_archive_root=cold_root,
    ) is (parent_id == "30")


@pytest.mark.parametrize("parent_id", ["1", "30"])
def test_hot_only_covering_ancestor_requires_visible_parent(
    tiering, monkeypatch, parent_id
) -> None:
    table = expected_table()
    table[2]["parent"] = parent_id
    table.append(mount_record("/test/hot", mount_id="30", device="0:65"))
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert is_expected(tiering) is (parent_id == "30")


@pytest.mark.parametrize("record_index", [1, 2])
def test_missing_parent_identity_fails_closed(tiering, monkeypatch, record_index) -> None:
    table = expected_table()
    table[record_index]["parent"] = "999"
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


@pytest.mark.parametrize("cycle", ["cold-self", "hot-self", "root-to-child"])
def test_cyclic_mount_parentage_fails_closed(tiering, monkeypatch, cycle) -> None:
    table = expected_table()
    if cycle == "cold-self":
        table[1]["parent"] = table[1]["id"]
    elif cycle == "hot-self":
        table[2]["parent"] = table[2]["id"]
    else:
        table[0]["parent"] = table[1]["id"]
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


def test_stacked_covering_ancestor_fails_closed(tiering, monkeypatch) -> None:
    table = expected_table()
    table[1]["parent"] = "31"
    table[2]["parent"] = "31"
    table.append(mount_record("/test", mount_id="30", device="0:65"))
    table.append(mount_record("/test", mount_id="31", device="0:66"))
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)


@pytest.mark.parametrize("root_parent", ["1", "999"])
def test_namespace_root_parent_can_be_self_or_outside_namespace(
    tiering, monkeypatch, root_parent
) -> None:
    table = expected_table()
    table[0]["parent"] = root_parent
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert is_expected(tiering)


def test_missing_namespace_root_fails_closed(tiering, monkeypatch) -> None:
    table = expected_table()[1:]
    monkeypatch.setattr(tiering, "_read_mountinfo", lambda: table)
    assert not is_expected(tiering)
