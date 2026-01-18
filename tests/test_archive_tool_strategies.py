"""
Tests for archive_tool.strategies module.

Verifies:
- Adaptive worker reduction logic
- Container restart strategies (self-healing)
- VPN rotation frequency and limits
- Handling of external command failures
"""

import threading
import time
from unittest.mock import patch

import pytest

from archive_tool.strategies import (
    attempt_container_restart,
    attempt_vpn_rotation,
    attempt_worker_reduction,
)

# Mock the import inside strategies.py
# properly mocking conditional imports or imports inside functions can be tricky.
# The strategies module imports `stop_docker_container` and `current_container_id` inside the functions.
# We can patch them where they are imported FROM, e.g. `archive_tool.docker_runner`.


@pytest.fixture
def mock_docker_runner():
    with patch("archive_tool.docker_runner.stop_docker_container") as mock_stop:
        with patch("archive_tool.docker_runner.current_container_id", "test-container-id"):
            yield mock_stop


@pytest.fixture
def mock_execute_command():
    with patch("archive_tool.strategies.execute_external_command") as mock_exec:
        yield mock_exec


@pytest.fixture
def mock_shutil_which():
    with patch("archive_tool.strategies.shutil.which") as mock_which:
        yield mock_which


@pytest.fixture
def mock_time_sleep():
    with patch("archive_tool.strategies.time.sleep") as mock_sleep:
        yield mock_sleep


def test_attempt_worker_reduction_disabled(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory(initial_workers=5)
    args = mock_args_factory(enable_adaptive_workers=False)

    assert attempt_worker_reduction(state, args) is False
    assert state.current_workers == 5


def test_attempt_worker_reduction_max_reached(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory(initial_workers=5)
    state.worker_reductions_done = 2
    args = mock_args_factory(enable_adaptive_workers=True, max_worker_reductions=2, min_workers=1)

    assert attempt_worker_reduction(state, args) is False
    assert state.current_workers == 5


def test_attempt_worker_reduction_min_workers_reached(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory(initial_workers=2)
    args = mock_args_factory(enable_adaptive_workers=True, max_worker_reductions=5, min_workers=2)

    assert attempt_worker_reduction(state, args) is False
    assert state.current_workers == 2


def test_attempt_worker_reduction_success(
    crawl_state_factory, mock_args_factory, mock_docker_runner, mock_time_sleep
):
    state = crawl_state_factory(initial_workers=5)
    args = mock_args_factory(enable_adaptive_workers=True, max_worker_reductions=5, min_workers=1)

    assert attempt_worker_reduction(state, args) is True

    # State updated
    assert state.current_workers == 4
    assert state.worker_reductions_done == 1

    # Docker stopped
    mock_docker_runner.assert_called_once_with("test-container-id")
    # Sleep called
    mock_time_sleep.assert_called()


def test_attempt_container_restart_disabled(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    args = mock_args_factory(enable_adaptive_restart=False)
    assert attempt_container_restart(state, args) is False


def test_attempt_container_restart_max_reached(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    state.container_restarts_done = 3
    args = mock_args_factory(enable_adaptive_restart=True, max_container_restarts=3)
    assert attempt_container_restart(state, args) is False


def test_attempt_container_restart_success(
    crawl_state_factory, mock_args_factory, mock_docker_runner, mock_time_sleep
):
    state = crawl_state_factory()
    args = mock_args_factory(enable_adaptive_restart=True, max_container_restarts=3)

    assert attempt_container_restart(state, args) is True

    assert state.container_restarts_done == 1
    mock_docker_runner.assert_called_once()
    mock_time_sleep.assert_called()


def test_attempt_vpn_rotation_disabled(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    args = mock_args_factory(enable_vpn_rotation=False)
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False


def test_attempt_vpn_rotation_no_command(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="",
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
    )
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False


def test_attempt_vpn_rotation_command_missing(
    crawl_state_factory, mock_args_factory, mock_shutil_which
):
    state = crawl_state_factory()
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="fake-vpn connect",
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
    )
    mock_shutil_which.return_value = None
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False
    mock_shutil_which.assert_called_with("fake-vpn")


def test_attempt_vpn_rotation_frequency_limit(
    crawl_state_factory, mock_args_factory, mock_shutil_which
):
    state = crawl_state_factory()
    state.last_vpn_rotation_timestamp = time.monotonic()  # Just happened

    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="real-vpn",
        vpn_disconnect_command=None,
        vpn_rotation_frequency_minutes=10,
        max_vpn_rotations=5,
    )
    mock_shutil_which.return_value = "/usr/bin/real-vpn"
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False


def test_attempt_vpn_rotation_success(
    crawl_state_factory, mock_args_factory, mock_shutil_which, mock_execute_command
):
    state = crawl_state_factory()
    # Ensure frequency check passes (never rotated)
    state.last_vpn_rotation_timestamp = None

    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="real-vpn",
        vpn_disconnect_command=None,
        vpn_rotation_frequency_minutes=10,
        max_vpn_rotations=5,
    )
    mock_shutil_which.return_value = "/usr/bin/real-vpn"
    mock_execute_command.return_value = True
    stop_evt = threading.Event()

    with patch.object(stop_evt, "wait", return_value=False):
        assert attempt_vpn_rotation(state, args, stop_evt) is True

    assert state.vpn_rotations_done == 1
    assert state.last_vpn_rotation_timestamp is not None
    mock_execute_command.assert_called_with("real-vpn", "VPN Connect/Rotate")


def test_attempt_vpn_rotation_command_failed(
    crawl_state_factory, mock_args_factory, mock_shutil_which, mock_execute_command
):
    state = crawl_state_factory()
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="real-vpn",
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
        vpn_rotation_frequency_minutes=10,
    )
    mock_shutil_which.return_value = "/usr/bin/real-vpn"
    mock_execute_command.return_value = False
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False
    assert state.vpn_rotations_done == 0


def test_attempt_vpn_rotation_stop_event_set(
    crawl_state_factory, mock_args_factory, mock_shutil_which, mock_execute_command
):
    state = crawl_state_factory()
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="real-vpn",
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
        vpn_rotation_frequency_minutes=10,
    )
    mock_shutil_which.return_value = "/usr/bin/real-vpn"
    mock_execute_command.return_value = True
    stop_evt = threading.Event()

    # Simulate stop_event being set during the 15s wait
    with patch.object(stop_evt, "wait", side_effect=lambda timeout: stop_evt.set()):
        assert attempt_vpn_rotation(state, args, stop_evt) is False

    assert state.vpn_rotations_done == 0


def test_attempt_vpn_rotation_parsing_error(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    # shlex.split might fail or return empty if command is weirdly quoted
    # But usually it just returns empty list for empty string or handles spaces.
    # The code specifically checks for "if not connect_cmd_parts".
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command="   ",  # Empty after split
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
        vpn_rotation_frequency_minutes=10,
    )
    stop_evt = threading.Event()

    assert attempt_vpn_rotation(state, args, stop_evt) is False


def test_attempt_vpn_rotation_shlex_error(crawl_state_factory, mock_args_factory):
    state = crawl_state_factory()
    # Invalid quoting to trigger shlex error
    args = mock_args_factory(
        enable_vpn_rotation=True,
        vpn_connect_command='vpn "unclosed quote',
        vpn_disconnect_command=None,
        max_vpn_rotations=5,
        vpn_rotation_frequency_minutes=10,
    )
    stop_evt = threading.Event()

    # shlex.split on "unclosed quote" raises ValueError
    assert attempt_vpn_rotation(state, args, stop_evt) is False


def test_strategy_selection_logic_placeholder():
    # These are placeholders for the logic triggers in controller or main loop
    # but the roadmap asked for them in strategies.py context.
    pass


def test_stall_detection_heuristics():
    # Placeholder for stall detection log parsing if implemented in strategies
    pass
