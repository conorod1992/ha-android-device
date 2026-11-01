"""Real release APK -> local push -> Android UI -> Home Assistant event tests.

Run only through the emulator workflow (or with a disposable local emulator).
No transport, device resolver, event, or Companion receiver is mocked.
"""

import json
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests
import uiautomator2 as u2
import websocket

PACKAGE = "io.homeassistant.companion.android.minimal"
URL = "http://127.0.0.1:8123"
ARTIFACTS = Path("e2e-artifacts")
DOMAIN = "android_device_control"


def adb(*args):
    return subprocess.check_output([shutil.which("adb"), *args], text=True, timeout=30)  # noqa: S603 - fixed adb arguments


def eventually(check, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {check}")


class Server:
    def __init__(self, token):
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.ws = websocket.create_connection(
            "ws://127.0.0.1:8123/api/websocket", timeout=15
        )
        assert json.loads(self.ws.recv())["type"] == "auth_required"
        self.ws.send(json.dumps({"type": "auth", "access_token": token}))
        assert json.loads(self.ws.recv())["type"] == "auth_ok"
        self.counter = 0
        self.events = []

    def api(self, path, data=None):
        response = self.http.request(
            "GET" if data is None else "POST", URL + path, json=data, timeout=60
        )
        response.raise_for_status()
        return response.json()

    def command(self, kind, **data):
        self.counter += 1
        self.ws.send(json.dumps({"id": self.counter, "type": kind, **data}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("type") == "event":
                self.events.append(message)
            if message.get("id") == self.counter:
                assert message.get("success"), message
                return message.get("result")

    def call(self, service, **data):
        return self.command(
            "call_service",
            domain=DOMAIN,
            service=service,
            service_data={"device_id": self.device, **data},
            return_response=service.startswith("ask_")
            or service in {"notify", "notify_until_acknowledged"},
        )

    def event(self, event_type, session_id):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            message = self.events.pop(0) if self.events else json.loads(self.ws.recv())
            event = message.get("event", {})
            if (
                event.get("event_type") == event_type
                and event["data"].get("session_id") == session_id
            ):
                return event["data"]
        raise AssertionError("Companion did not return the expected session event")


def onboard_phone(phone, password):
    """Drive the pinned APK's ordinary onboarding, including its real login UI."""
    phone.app_start(PACKAGE)
    end = time.monotonic() + 180
    previous = ""
    screens = 0
    while time.monotonic() < end:
        xml = phone.dump_hierarchy()
        if xml != previous:
            (ARTIFACTS / "onboarding.xml").write_text(
                xml.replace(password, "[redacted]")
            )
            if screens < 30:
                phone.screenshot(str(ARTIFACTS / f"onboarding-{screens:02}.png"))
                screens += 1
            previous = xml
        if "Pixel Launcher isn" in xml and "responding" in xml:
            # The emulator launcher can ANR during first boot. It is not the
            # application under test; dismiss only this specific system dialog.
            phone(text="Close app").click(timeout=5)
            phone.app_start(PACKAGE)
            continue
        if "Home Assistant isn" in xml and "responding" in xml:
            raise AssertionError("Companion stopped responding during onboarding")
        edits = phone(className="android.widget.EditText")
        if edits.count >= 2:
            edits[0].set_text("emulator")
            edits[1].set_text(password)
            phone(textMatches="(?i)log in|sign in").click_exists(timeout=2)
        elif "name this device" in xml:
            edits[0].set_text("CI Android")
            phone(text="Save").click_exists(timeout=2)
        elif "address?" in xml and edits.count:
            edits[0].set_text("http://10.0.2.2:8123")
            phone(text="Connect").click_exists(timeout=2)
        else:
            for label in (
                "Connect to my",
                "Enter address manually",
                "Less secure:",
                "Got it",
                "Allow notifications",
                "Allow",
                "Next",
                "Continue",
                "Finish",
            ):
                if phone(textStartsWith=label).click_exists(timeout=0.2):
                    break
        # Registration is only half of onboarding: finish the security choice
        # before moving the app into the background.
        if "io.homeassistant.companion.android.webview" in xml or "Overview" in xml:
            return
        time.sleep(1)
    raise AssertionError("Companion onboarding did not complete; see onboarding.xml")


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    ARTIFACTS.mkdir(exist_ok=True)
    config = tmp_path_factory.mktemp("homeassistant")
    shutil.copytree("custom_components", config / "custom_components")
    (config / "configuration.yaml").write_text(
        "homeassistant:\n  name: Emulator CI\n  time_zone: UTC\n"
        "http:\napi:\nfrontend:\nconfig:\nmobile_app:\nlovelace:\nperson:\nonboarding:\n"
    )
    log = (ARTIFACTS / "home-assistant.log").open("w")
    process = subprocess.Popen(  # noqa: S603 - disposable local HA process
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
    phone = None
    server = None
    try:

        def ready():
            try:
                return (
                    requests.get(URL + "/api/onboarding", timeout=3).status_code == 200
                )
            except requests.ConnectionError:
                return False

        eventually(ready, timeout=240)
        password = secrets.token_urlsafe(24)
        print(f"::add-mask::{password}", flush=True)
        response = requests.post(
            URL + "/api/onboarding/users",
            json={
                "name": "Emulator",
                "username": "emulator",
                "password": password,
                "client_id": URL + "/",
                "language": "en",
            },
            timeout=60,
        )
        response.raise_for_status()
        response = requests.post(
            URL + "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": response.json()["auth_code"],
                "client_id": URL + "/",
            },
            timeout=30,
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        print(f"::add-mask::{token}", flush=True)
        server = Server(token)
        for step in ("core_config", "analytics"):
            server.api("/api/onboarding/" + step, {})
        server.api(
            "/api/onboarding/integration",
            {"client_id": URL + "/", "redirect_uri": URL + "/"},
        )
        adb("install", "-r", "companion.apk")
        adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
        phone = u2.connect()
        onboard_phone(phone, password)
        entries = eventually(
            lambda: [
                e
                for e in server.api("/api/config/config_entries/entry")
                if e["domain"] == "mobile_app"
            ]
        )
        devices = server.command("config/device_registry/list")
        server.device = next(
            d["id"] for d in devices if entries[0]["entry_id"] in d["config_entries"]
        )
        flow = server.api(
            "/api/config/config_entries/flow",
            {"handler": DOMAIN, "show_advanced_options": False},
        )
        result = server.api("/api/config/config_entries/flow/" + flow["flow_id"], {})
        assert result["type"] == "create_entry", result
        eventually(
            lambda: any(s["domain"] == DOMAIN for s in server.api("/api/services"))
        )
        server.command("subscribe_events")
        phone.press("home")
        yield server, phone
    finally:
        if phone:
            phone.screenshot(str(ARTIFACTS / "final.png"))
            (ARTIFACTS / "final.xml").write_text(phone.dump_hierarchy())
        (ARTIFACTS / "logcat.txt").write_text(adb("logcat", "-d", "-t", "3000"))
        (ARTIFACTS / "notifications.txt").write_text(
            adb("shell", "dumpsys", "notification", "--noredact")
        )
        if server:
            server.ws.close()
        subprocess.run(  # noqa: S603 - fixed disposable container name
            [shutil.which("docker"), "stop", "--time", "15", "ha-android-e2e"],
            check=False,
            timeout=30,
            capture_output=True,
        )
        process.wait(timeout=30)
        log.close()


def notifications():
    dump = adb("shell", "dumpsys", "notification", "--noredact")
    active = dump.split("Notification List:", 1)[1]
    return active.split("Ranking Config:", 1)[0].split("Snoozed Notification List:", 1)[
        0
    ]


def visible(title):
    return title in notifications()


def click_action(phone, label):
    phone.open_notification()
    phone(text=label).click(timeout=15)


def test_progress_and_clear(environment):
    server, phone = environment
    server.call(
        "notify_progress",
        title="E2E progress",
        message="Copying",
        tag="e2e-progress",
        current=3,
        maximum=10,
    )
    eventually(lambda: visible("E2E progress"))
    dump = notifications()
    assert re.search(r"android.progress=(?:Integer \()?3(?:\)|\s)", dump)
    assert re.search(r"android.progressMax=(?:Integer \()?10(?:\)|\s)", dump)
    server.call(
        "notify_progress",
        title="E2E progress",
        message="Waiting",
        tag="e2e-progress",
        indeterminate=True,
    )
    eventually(
        lambda: re.search(
            r"android.progressIndeterminate=(?:Boolean \()?true", notifications()
        )
    )
    phone.open_notification()
    phone.screenshot(str(ARTIFACTS / "progress.png"))
    server.call("clear_notification", tag="e2e-progress")
    eventually(lambda: not visible("E2E progress"))
    phone.press("home")


def test_action_returns_real_session_event(environment):
    server, phone = environment
    result = server.call(
        "ask_yes_no",
        title="E2E question",
        message="Choose an answer",
        tag="e2e-question",
        yes_label="E2E Yes",
        no_label="E2E No",
    )
    session = result["response"]["devices"][0]["session_id"]
    eventually(lambda: visible("E2E question"))
    click_action(phone, "E2E Yes")
    event = server.event("android_device_control_notification_action", session)
    assert event["action_id"] == "yes"
    assert event["device_id"] == server.device


def test_acknowledgement_replacement_and_stop(environment):
    server, phone = environment
    server.call(
        "notify_until_acknowledged",
        title="E2E old",
        message="Old request",
        tag="e2e-ack",
        repeat_interval={"seconds": 60},
        max_attempts=2,
    )
    eventually(lambda: visible("E2E old"))
    result = server.call(
        "notify_until_acknowledged",
        title="E2E replacement",
        message="New request",
        tag="e2e-ack",
        acknowledgement_label="E2E Done",
        repeat_interval={"seconds": 60},
        max_attempts=5,
    )
    session = result["response"]["devices"][0]["session_id"]
    eventually(lambda: visible("E2E replacement") and not visible("E2E old"))
    click_action(phone, "E2E Done")
    event = server.event("android_device_control_notification_acknowledged", session)
    assert event["device_id"] == server.device
    eventually(lambda: not visible("E2E replacement"))
    time.sleep(65)
    assert not visible("E2E replacement")


def test_find_phone_stop_button(environment):
    server, phone = environment
    server.call(
        "find_phone",
        wake_screen=False,
        stop_when_unlocked=False,
        max_attempts=5,
        repeat_interval={"seconds": 3},
    )
    eventually(lambda: visible("Finding phone"))
    click_action(phone, "Stop ringing")
    eventually(lambda: not visible("Finding phone"))
    time.sleep(7)
    assert not visible("Finding phone")


def test_text_reply_returns_actual_input(environment):
    server, phone = environment
    result = server.call(
        "ask_text",
        title="E2E text",
        message="Reply here",
        tag="e2e-text",
        reply_label="E2E Reply",
    )
    session = result["response"]["devices"][0]["session_id"]
    eventually(lambda: visible("E2E text"))
    click_action(phone, "E2E Reply")
    phone(className="android.widget.EditText").set_text("Reply from Android")
    phone(resourceId="com.android.systemui:id/remote_input_send").click(timeout=10)
    event = server.event("android_device_control_notification_action", session)
    assert event["action_id"] == "reply"
    assert event["response_text"] == "Reply from Android"


def test_delivery_receipt_comes_from_companion(environment):
    server, _phone = environment
    result = server.call(
        "notify",
        title="E2E receipt",
        message="Confirm local receipt",
        tag="e2e-receipt",
        confirm_delivery=True,
    )
    session = result["response"]["devices"][0]["session_id"]
    event = server.event("android_device_control_notification_received", session)
    assert event["device_id"] == server.device
    eventually(lambda: visible("E2E receipt"))
    server.call("clear_notification", tag="e2e-receipt")
