"""Resource and WebSocket helpers shared by emulator tests and fault tests."""

import json
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager


def assert_port_free(port=8123):
    """Never onboard a server that was already running on the test port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except ConnectionRefusedError:
        return
    raise RuntimeError(f"Port {port} is already in use; stop that server first")


@contextmanager
def home_assistant_runtime(config, log_path):
    """Always stop the disposable container, even if diagnostic capture fails."""
    assert_port_free()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603 - fixed disposable HA container
            [
                shutil.which("docker"),
                "run",
                "--rm",
                "--network",
                "host",
                "--name",
                "ha-android-e2e",
                "--volume",
                f"{config}:/config",
                "ghcr.io/home-assistant/home-assistant:2026.7.2",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            yield process
        finally:
            try:
                subprocess.run(  # noqa: S603 - fixed disposable container name
                    [shutil.which("docker"), "stop", "--time", "15", "ha-android-e2e"],
                    check=False,
                    timeout=30,
                    capture_output=True,
                )
            finally:
                process.wait(timeout=30)


def receive_json(ws, deadline):
    """Bound the whole operation even when unrelated messages keep arriving."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Timed out waiting for a Home Assistant WebSocket message")
    ws.settimeout(remaining)
    return json.loads(ws.recv())


def websocket_command(ws, payload, events, timeout=60):
    ws.settimeout(timeout)
    deadline = time.monotonic() + timeout
    ws.send(json.dumps(payload))
    while True:
        message = receive_json(ws, deadline)
        if message.get("type") == "event":
            events.append(message)
        elif message.get("type") == "result" and message.get("id") == payload["id"]:
            assert message.get("success"), message
            return message.get("result")


def session_event(ws, events, event_type, session_id, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        message = events.pop(0) if events else receive_json(ws, deadline)
        event = message.get("event", {})
        if (
            event.get("event_type") == event_type
            and event["data"].get("session_id") == session_id
        ):
            return event["data"]
