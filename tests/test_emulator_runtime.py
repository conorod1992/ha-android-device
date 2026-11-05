"""Exercise emulator harness failure paths without needing an emulator."""

import json
from unittest.mock import MagicMock, Mock

import pytest

from e2e.android import runtime


@pytest.fixture
def docker(monkeypatch):
    process = Mock()
    run = Mock()
    monkeypatch.setattr(runtime, "assert_port_free", lambda: None)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: name)
    monkeypatch.setattr(runtime.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    return process, run


def test_runtime_stops_and_closes_log_after_diagnostic_failure(tmp_path, docker):
    process, run = docker
    with (
        pytest.raises(RuntimeError, match="screenshot failed"),
        runtime.home_assistant_runtime(tmp_path, tmp_path / "ha.log"),
    ):
        raise RuntimeError("screenshot failed")
    assert run.call_args.args[0] == ["docker", "stop", "--time", "15", "ha-android-e2e"]
    process.wait.assert_called_once_with(timeout=30)
    assert runtime.subprocess.Popen.call_args.kwargs["stdout"].closed


def test_runtime_waits_and_closes_log_even_if_docker_stop_times_out(tmp_path, docker):
    process, run = docker
    run.side_effect = TimeoutError("docker stop timed out")
    with (
        pytest.raises(TimeoutError, match="docker stop"),
        runtime.home_assistant_runtime(tmp_path, tmp_path / "ha.log"),
    ):
        pass
    process.wait.assert_called_once_with(timeout=30)
    assert runtime.subprocess.Popen.call_args.kwargs["stdout"].closed


def test_runtime_refuses_to_start_against_an_occupied_port(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.socket, "create_connection", MagicMock())
    popen = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    with (
        pytest.raises(RuntimeError, match="already in use"),
        runtime.home_assistant_runtime(tmp_path, tmp_path / "ha.log"),
    ):
        pytest.fail("must not start")
    popen.assert_not_called()


def test_websocket_command_preserves_events_even_with_matching_request_id():
    ws = Mock()
    event = {"id": 7, "type": "event", "event": {"data": {"session_id": "early"}}}
    ws.recv.side_effect = [
        json.dumps(event),
        json.dumps({"id": 7, "type": "result", "success": True, "result": "ok"}),
    ]
    events = []
    assert (
        runtime.websocket_command(ws, {"id": 7, "type": "subscribe_events"}, events)
        == "ok"
    )
    assert events == [event]


def test_websocket_command_has_a_deadline_despite_continuous_events(monkeypatch):
    monkeypatch.setattr(runtime.time, "monotonic", Mock(side_effect=[0, 1, 2, 3]))
    ws = Mock()
    ws.recv.return_value = json.dumps({"id": 1, "type": "event"})
    with pytest.raises(TimeoutError, match="Timed out"):
        runtime.websocket_command(ws, {"id": 2}, [], timeout=3)
    assert ws.recv.call_count == 2


def test_event_wait_uses_its_full_deadline_and_retains_session_correlation(monkeypatch):
    monkeypatch.setattr(runtime.time, "monotonic", Mock(side_effect=[0, 1, 2]))
    ws = Mock()
    ws.recv.return_value = json.dumps(
        {
            "type": "event",
            "event": {
                "event_type": "receipt",
                "data": {"session_id": "right", "device_id": "phone"},
            },
        }
    )
    events = [{"event": {"event_type": "receipt", "data": {"session_id": "wrong"}}}]
    assert runtime.session_event(ws, events, "receipt", "right")["device_id"] == "phone"
    assert ws.settimeout.call_args.args[0] > 15
