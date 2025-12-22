from __future__ import annotations

from archive_tool.constants import CONTAINER_OUTPUT_DIR
from archive_tool.docker_runner import build_zimit_args


def test_build_zimit_args_multiple_seeds_uses_single_csv_seeds_flag() -> None:
    args = build_zimit_args(
        base_zimit_args=[],
        required_args={
            "seeds": ["https://example.org/en", "https://example.org/fr"],
            "name": "test-job",
        },
        current_workers=3,
        is_final_build=False,
        extra_args=[],
    )

    assert args[0] == "zimit"

    assert args.count("--seeds") == 1
    seeds_idx = args.index("--seeds")
    assert args[seeds_idx + 1] == "https://example.org/en,https://example.org/fr"

    assert args.count("--workers") == 1
    workers_idx = args.index("--workers")
    assert args[workers_idx + 1] == "3"

    assert args[-2:] == ["--output", str(CONTAINER_OUTPUT_DIR)]
