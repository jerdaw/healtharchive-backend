from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import get_archive_tool_config


def cmd_check_env(args: argparse.Namespace) -> int:
    """
    Print basic environment / config info.
    """
    cfg = get_archive_tool_config()
    print("HealthArchive Backend – Environment Check")
    print("-----------------------------------------")
    print(f"Archive root:     {cfg.archive_root}")
    print(f"Archive tool cmd: {cfg.archive_tool_cmd}")
    print()
    try:
        cfg.ensure_archive_root()
        print("Archive root exists and is (likely) writable.")
        return 0
    except Exception as e:
        print(f"ERROR: Failed to ensure archive root: {e}", file=sys.stderr)
        return 1


def cmd_check_archive_tool(args: argparse.Namespace) -> int:
    """
    Run 'archive-tool --help' using configured command, to verify wiring.
    """
    cfg = get_archive_tool_config()
    print(f"Running '{cfg.archive_tool_cmd} --help' to verify archive_tool...")
    try:
        completed = subprocess.run(
            [cfg.archive_tool_cmd, "--help"],
            check=False,  # don't crash Python on non-zero
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print(
            f"ERROR: Command '{cfg.archive_tool_cmd}' not found. Is the venv active and package installed?",
            file=sys.stderr,
        )
        return 1

    print("--- STDOUT ---")
    print(completed.stdout)
    print("--- STDERR ---")
    print(completed.stderr)
    print(f"Exit code: {completed.returncode}")
    return 0 if completed.returncode == 0 else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HealthArchive backend CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ha-backend check-env
    parser_check_env = subparsers.add_parser(
        "check-env", help="Check backend configuration and archive root."
    )
    parser_check_env.set_defaults(func=cmd_check_env)

    # ha-backend check-archive-tool
    parser_check_arch = subparsers.add_parser(
        "check-archive-tool", help="Verify archive_tool CLI wiring."
    )
    parser_check_arch.set_defaults(func=cmd_check_archive_tool)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())

