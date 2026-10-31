# Companion end-to-end tests

The Android end-to-end workflow runs for pull requests and can be run manually.
The Release workflow also requires it to pass before publishing.

It boots an Android 35 x86_64 emulator and installs the official **2026.10.1
minimal Companion APK**, verified against its published SHA-256 digest. A fresh
Home Assistant instance loads this checkout's custom integration. The tests create
a disposable owner account, onboard Companion through its UI, and use its real
Mobile App registration and persistent WebSocket connection for local push.
No personal credentials, Firebase project, or externally reachable server is needed.

The tests verify:

- Determinate and indeterminate progress extras on Android, with notification cleanup.
- A notification button returning the correct device, session, and logical action.
- A typed reply returning the actual text through Companion to Home Assistant.
- A receipt event originating from Companion.
- Same-tag acknowledgement replacement and a real acknowledgement button stopping repeats.
- Find Phone's Stop button clearing its notification and preventing later attempts.

Android's active notification records provide the rendering assertions; action and
reply tests click System UI, then assert the resulting Home Assistant events. They
do not inject simulated broadcasts or call Companion's internal receiver directly.
The workflow uploads JUnit results, notification records, logcat, a final screenshot,
and UI hierarchy. The progress test also captures its notification shade.

To reproduce locally on Linux with a disposable emulator already running:

```sh
pip install -r requirements_test.txt uiautomator2==3.5.0 websocket-client==1.9.0
# Download and verify companion.apk using the workflow's pinned URL and digest.
python -m pytest e2e/android -v --junitxml=e2e-artifacts/results.xml
```

Port 8123 must be free; the emulator reaches its host through `10.0.2.2`. These tests
are separate from the normal `pytest` suite. APK upgrades should update both the URL
and digest and be validated through this workflow.

This covers local push on stock emulated Android. It does not measure Firebase
delivery, audible output, physical flashlight behavior, or manufacturer power policies.
