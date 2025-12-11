# archive_tool/cli.py
import argparse
from typing import List, Tuple

from .constants import DOCKER_IMAGE


def parse_arguments() -> Tuple[argparse.Namespace, List[str]]:
    """Sets up argparse and parses command line arguments."""
    parser = argparse.ArgumentParser(
        description="Enhanced automated website archiving tool using zimit via Docker.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    core_group = parser.add_argument_group("Core Arguments")
    core_group.add_argument("--seeds", nargs="+", required=True, help="Seed URLs.")
    core_group.add_argument("--name", required=True, help="Base name for ZIM file.")
    core_group.add_argument(
        "--output-dir", required=True, help="Host output directory path."
    )
    core_group.add_argument(
        "--initial-workers",
        type=int,
        default=1,
        help="Initial number of workers for zimit (can be overridden by passthrough --workers).",
    )

    tool_opts_group = parser.add_argument_group("Tool Options")
    tool_opts_group.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete temp dirs and state file on success.",
    )
    tool_opts_group.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing ZIM file in the output directory.",
    )  # <-- NEW
    tool_opts_group.add_argument(
        "--docker-image", default=DOCKER_IMAGE, help="Zimit Docker image."
    )
    tool_opts_group.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level.",
    )
    tool_opts_group.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate configuration and print a summary without running Docker "
            "containers."
        ),
    )

    monitor_group = parser.add_argument_group("Monitoring Configuration")
    # ... (monitor args remain the same) ...
    monitor_group.add_argument(
        "--enable-monitoring",
        action="store_true",
        help="Enable real-time monitoring and adaptive strategies.",
    )
    monitor_group.add_argument(
        "--monitor-interval-seconds",
        type=int,
        default=30,
        help="How often the monitor checks logs/conditions.",
    )
    monitor_group.add_argument(
        "--stall-timeout-minutes",
        type=int,
        default=30,
        help="Minutes without crawl progress increase to detect stall.",
    )
    monitor_group.add_argument(
        "--error-threshold-timeout",
        type=int,
        default=10,
        help="Number of consecutive timeout errors to trigger action.",
    )
    monitor_group.add_argument(
        "--error-threshold-http",
        type=int,
        default=10,
        help="Number of consecutive HTTP/network errors to trigger action.",
    )

    adapt_group = parser.add_argument_group(
        "Adaptive Strategies (require --enable-monitoring)"
    )
    # ... (adaptive worker args remain the same) ...
    adapt_group.add_argument(
        "--enable-adaptive-workers",
        action="store_true",
        help="Allow automatic worker reduction.",
    )
    adapt_group.add_argument(
        "--min-workers",
        type=int,
        default=1,
        help="Minimum workers for adaptive reduction.",
    )
    adapt_group.add_argument(
        "--max-worker-reductions",
        type=int,
        default=2,
        help="Max worker reduction attempts per run.",
    )
    # ... (VPN args remain the same) ...
    adapt_group.add_argument(
        "--enable-vpn-rotation",
        action="store_true",
        help="Allow IP rotation via VPN commands.",
    )
    adapt_group.add_argument(
        "--vpn-connect-command",
        type=str,
        default=None,
        help="Shell command to connect VPN (e.g., 'nordvpn connect us'). Use quotes if command contains spaces.",
    )
    adapt_group.add_argument(
        "--vpn-disconnect-command",
        type=str,
        default=None,
        help="Shell command to disconnect VPN (optional, e.g., 'nordvpn disconnect').",
    )
    adapt_group.add_argument(
        "--max-vpn-rotations",
        type=int,
        default=3,
        help="Max total VPN rotation attempts per run.",
    )  # Default reset to 3, user can override
    adapt_group.add_argument(
        "--vpn-rotation-frequency-minutes",
        type=int,
        default=60,
        help="Minimum time (minutes) between VPN rotation attempts.",
    )
    adapt_group.add_argument(
        "--relax-perms",
        action="store_true",
        default=False,
        help=(
            "After a crawl, relax permissions on temp output (chmod a+rX) so host users "
            "can read WARCs without sudo. Intended for dev."
        ),
    )
    # ... (backoff arg remains the same) ...
    adapt_group.add_argument(
        "--backoff-delay-minutes",
        type=int,
        default=15,
        help="How long to pause after certain errors/adaptations.",
    )

    script_args, zimit_passthrough_args = parser.parse_known_args()

    # Validation
    if (
        script_args.enable_adaptive_workers or script_args.enable_vpn_rotation
    ) and not script_args.enable_monitoring:
        parser.error(
            "--enable-adaptive-workers and --enable-vpn-rotation require --enable-monitoring."
        )
    if script_args.enable_vpn_rotation and not script_args.vpn_connect_command:
        parser.error("--enable-vpn-rotation requires --vpn-connect-command to be set.")
    if script_args.min_workers < 1:
        parser.error("--min-workers must be 1 or greater.")
    if script_args.vpn_rotation_frequency_minutes < 0:
        parser.error("--vpn-rotation-frequency-minutes cannot be negative.")

    return script_args, zimit_passthrough_args
