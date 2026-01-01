from __future__ import annotations

import os
import time
from pathlib import Path

from archive_tool.utils import find_latest_config_yaml


def _write_yaml(path: Path, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test: true\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_find_latest_config_yaml_prefers_crawl_named_yaml(tmp_path: Path) -> None:
    now = time.time()
    temp_dir = tmp_path / ".tmpA"
    temp_dir.mkdir()

    older = temp_dir / "collections/crawl-123/crawls/crawl-0001.yaml"
    newer = temp_dir / "collections/crawl-123/crawls/crawl-0002.yaml"
    _write_yaml(older, mtime=now - 20)
    _write_yaml(newer, mtime=now - 10)

    # Even if other YAML files exist, the function should prefer crawl-*.yaml
    # under crawls/.
    other = temp_dir / "collections/crawl-123/crawls/other.yaml"
    _write_yaml(other, mtime=now)

    found = find_latest_config_yaml(temp_dir)
    assert found == newer.resolve()


def test_find_latest_config_yaml_falls_back_to_any_yaml_in_crawls(tmp_path: Path) -> None:
    now = time.time()
    temp_dir = tmp_path / ".tmpA"
    temp_dir.mkdir()

    # No crawl-*.yaml present, but a YAML exists under crawls/.
    config = temp_dir / "collections/crawl-123/crawls/config.yaml"
    _write_yaml(config, mtime=now - 10)

    found = find_latest_config_yaml(temp_dir)
    assert found == config.resolve()


def test_find_latest_config_yaml_falls_back_to_collection_root(tmp_path: Path) -> None:
    now = time.time()
    temp_dir = tmp_path / ".tmpA"
    temp_dir.mkdir()

    # No crawls/ directory, but crawl config stored directly under collection.
    config = temp_dir / "collections/crawl-123/crawl-0001.yml"
    _write_yaml(config, mtime=now - 10)

    found = find_latest_config_yaml(temp_dir)
    assert found == config.resolve()
