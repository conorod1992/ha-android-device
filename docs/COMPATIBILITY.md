# Companion command compatibility matrix

Inventory reviewed against the official Android command list, notification
documentation, and Android public intent documentation on 2026-08-12.

## Companion notification-command wrappers

| Documented Android command | Status | Friendly action / reason |
| --- | --- | --- |
| `clear_notification` | Implemented | `clear_notification` |
| `command_activity` | Implemented | `launch_activity`; standard AlarmClock actions `set_alarm`, `dismiss_alarm`, `snooze_alarm`, `show_alarms`, `set_timer`, `dismiss_expired_timers`, and `show_timers` |
| `command_app_lock` | Implemented | `set_app_lock` |
| `command_auto_screen_brightness` | Implemented | `set_auto_brightness` |
| `command_bluetooth` | Implemented | `set_bluetooth`; Android 12 or older only |
| `command_ble_transmitter` | Implemented | `set_ble_transmitter`, `configure_ble_transmitter`; includes every documented setting |
| `command_beacon_monitor` | Implemented | `set_beacon_monitor` |
| `command_broadcast_intent` | Implemented | `send_broadcast_intent` |
| `command_dnd` | Implemented | `set_do_not_disturb` |
| `command_flashlight` | Implemented | `set_flashlight` |
| `command_high_accuracy_mode` | Implemented | `set_high_accuracy_mode`, `set_high_accuracy_interval` |
| `command_launch_app` | Implemented | `launch_app` |
| `command_media` | Implemented | `media_control`; all eight documented media operations |
| `command_ringer_mode` | Implemented | `set_ringer_mode` |
| `command_screen_brightness_level` | Implemented | `set_screen_brightness`; friendly 0–100% converted to raw 0–255, with legacy raw YAML retained |
| `command_screen_off_timeout` | Implemented | `set_screen_timeout` |
| `command_screen_on` | Implemented | `turn_screen_on` |
| `command_stop_tts` | Implemented | `stop_tts` |
| `command_persistent_connection` | Implemented | `set_persistent_connection`; all four modes |
| `command_update_sensors` | Implemented | `update_sensors` |
| `command_volume_level` | Implemented | `set_volume`; all current streams including Android 17+ assistant |
| `command_wake_word_detection` | Implemented | `set_wake_word_detection` |
| `command_webview` | Implemented | `open_webview`; paths and `entityId:` forms |
| `remove_channel` | Implemented | `remove_notification_channel` |
| `request_location_update` | Implemented | `request_location_update` |
| `kiosk_show_screensaver` | Implemented | `kiosk_show_screensaver` |
| `kiosk_hide_screensaver` | Implemented | `kiosk_hide_screensaver` |
| `kiosk_show_camera` | Implemented | `kiosk_show_camera` |
| `kiosk_hide_camera` | Implemented | `kiosk_hide_camera` |
| `kiosk_set_brightness` | Implemented | `kiosk_set_brightness` |
| `kiosk_set_volume` | Implemented | `kiosk_set_volume` |
| `kiosk_reload` | Implemented | `kiosk_reload` |
| `kiosk_default` | Implemented | `kiosk_default` |
| `clear_badge` | Not applicable | Documented for Apple platforms, not Android |
| `update_complications` | Not applicable | Apple Watch only |
| `update_widgets` | Not applicable | Listed in the cross-platform table but not the Android command table |

No currently documented Android command is deliberately deferred. `send_command` provides a guarded path for future `command_*` messages until a typed action is released.

## Friendly standard Android intents

These actions use `command_activity` as transport but expose public Android or provider
contracts instead of raw fields.

| Friendly action | Contract | Compatibility notes |
| --- | --- | --- |
| `open_url` | `ACTION_VIEW` | Any absolute URI Android can resolve |
| `share_text` | `ACTION_SEND` + `text/plain` | Uses encoded `EXTRA_TEXT` and optional `EXTRA_SUBJECT`; opens a handler only |
| `share_url` | `ACTION_SEND` + `text/plain` | Validated HTTP(S) URL with optional accompanying text and subject |
| `show_map` | `geo:` / Waze URL | Generic Android, optional Google Maps targeting, Waze web fallback |
| `navigate_to` | `geo:` / Google Maps URLs / Waze Deep Links | Generic handling cannot guarantee turn-by-turn; provider modes are validated |
| `dial_number` | `ACTION_DIAL` | UI only; no direct-call action or CALL_PHONE requirement |
| `compose_sms` | `ACTION_SENDTO` + `smsto:` | Composition UI only |
| `compose_email` | `ACTION_SENDTO` + `mailto:` | Email-capable handlers; composition UI only |
| `create_calendar_event` | `ACTION_INSERT` + Calendar URI/extras | Opens editor; naive datetimes use Home Assistant's timezone |
| `web_search` | `ACTION_WEB_SEARCH` | Generic Android handler; no Google hardcoding |
| `open_settings` | curated `Settings` constants | API levels are recorded in code; OEM resolution can differ |
| `open_app_settings` | details, notifications, overlay, write settings | Permissions are reached through Application details |
| `open_camera` / `open_video_camera` | standard media capture actions | No activity result, remote capture, or retrieval |
| `open_entity` | Companion `entityId:` webview | Reuses `command_webview` |
| `find_phone` / `stop_find_phone` | bounded alarm-channel notification or TTS session | Immediate first attempt, configurable interval and maximum, per-device restart semantics, actionable Stop button |

The settings list deliberately omits candidates without a stable public contract,
including a package-scoped permissions deep link and Android Auto. App-specific battery
optimisation exemption requests are also omitted because they carry policy and
permission requirements inappropriate for a generic remote action.

## Structured intent extras

`launch_activity` and `send_broadcast_intent` accept either the legacy raw string or a
typed `structured_extras` list. The exposed, safely coercible subset of the Companion
parser is: strings, booleans, integers, longs, floats, doubles, and integer lists.
Strings use `String.urlencoded`; integer lists use Companion's semicolon format.
Raw input remains untouched for backwards compatibility.

The current Companion source has a defect in its URL-decoded string-array branches:
each element decodes the full joined value. Structured string arrays are deliberately
omitted rather than serialized unreliably. Advanced users can retain raw extras for
receiver-specific cases.

Android's arbitrary running-timer dismissal is deliberately not wrapped. The standard
`ACTION_DISMISS_TIMER` contract requires a timer-specific data URI, and neither the
Mobile App registration nor a documented Companion sensor exposes those URIs. The
implemented `dismiss_expired_timers` action uses the standard no-URI behavior instead.

## Friendly notifications

The curated `notify` action exposes only documented Android notification fields:
title, message, tag, channel, importance, sticky, and timeout. `notify_urgent` adds
documented immediate delivery (`ttl: 0`, `priority: high`) and defaults to an Urgent
channel. Importance is a channel request: existing channel configuration and Android,
OEM, Do Not Disturb, and user settings remain authoritative. Neither action forces
audio volume or claims device-side display.

`prompt`, `ask_yes_no`, `ask_choice`, and `ask_text` share one listener and session lookup. Each
button receives a collision-resistant token; a matching Companion action event is
translated to `android_device_control_notification_action` with the target device,
prompt session, logical action ID, and optional tag. Android's documented three-action
limit is enforced, along with unique safe IDs and non-empty bounded labels. Tokens from
different devices or prompts cannot cross-trigger, and stale/malformed events are
ignored. Minimal token-to-action mappings are stored for 24 hours so buttons already
on a phone remain usable across a Home Assistant restart; notification content is not
stored.

`ask_text` uses Companion's `behavior: textInput` action and translates `reply_text`
to additive `response_text` on the existing integration event. Actionable services can
set `authenticationRequired`; Android 12+ enforces the unlock UI, but this is not
described as a stronger security guarantee. Friendly choice fields normalize to the
same validated action list as legacy `choices` YAML.

Progress (`progress`, `progress_max`, `progress_indeterminate`), image attachments
(`image`), Android Auto (`car_ui`), and receipt confirmation (`confirmation`) use the
documented Companion fields. Receipt events are translated to
`android_device_control_notification_received`; they mean receipt by Companion only.
Each target gets a unique opaque correlation value. A bounded 24-hour mapping stores
only that value, the canonical HA device target, and optional tag, so multi-device and
post-restart receipts never depend on the raw Mobile App event device identifier.

`notify_live_update` isolates the version-sensitive `live_update: true` contract and
requires a stable 1–64 character `[A-Za-z0-9_-]` tag and Android title. Android
rendering requires Android 16+ and a compatible current Companion version; integration
setup and dispatch remain available on older devices and do not claim rendering
success.

`notify_until_acknowledged` uses the same listener and unique-token architecture. It
dispatches immediately and defaults to five total attempts at five-minute intervals.
Sessions are keyed by device and tag. A same-key replacement is dispatched before the
old repeating task is retired, so a failed replacement leaves the old session working.
A matching
acknowledgement stops repeats, requests `clear_notification`, and emits
`android_device_control_notification_acknowledged`. The explicit stop action also
performs best-effort tagged clearing after restart. On startup, minimal persisted
metadata is used to clear notifications whose repeating tasks cannot safely resume;
late acknowledgement actions remain recognizable for up to 24 hours. Tasks, storage,
and listeners are bounded, and every repeat loop is finite.

## Find Phone notification behavior

Each ringtone attempt sends a notification with `ttl: 0`, `priority: high`,
`channel: alarm_stream`, and the namespaced integration tag. Companion categorises it as an alarm and
uses the configured alarm-channel sound on the alarm audio stream at its current volume.
It does not force maximum volume. TTS mode uses `alarm_stream_max`; Companion saves the
current alarm-stream volume, maximises it for playback, then restores the saved value.
When requested, TTS mode also posts a low-importance control notification so the same
actionable Stop button is available without adding a second audible alert.

Sessions repeat by default, are bounded to 10 attempts at 15-second intervals, and are
keyed by Home Assistant device ID. Screen wake and flashlight-on are first-attempt-only.
Later attempts send only ringtone or TTS. A new session replaces the old session for the
same device; other devices are independent. Users should choose an interval appropriate
to their configured alarm-channel sound, which can be long or ramp gradually.

Stop sends `command_stop_tts` only for a known TTS-mode session and always clears the
integration notification. A ringtone session therefore cannot interrupt unrelated TTS.
Clearing a notification cannot guarantee Android immediately terminates channel audio
already playing. Companion does not expose the flashlight's prior state, so Find Phone
does not turn it off automatically; the explicit post-restart cleanup option remains
available when the caller knowingly wants that state change.
The Stop button normally finds an active session from its unique action token, without
requiring Companion to return arbitrary custom event fields. A prefixed but unmatched
action stops the sole active session as a compatibility fallback; ambiguous actions are
ignored when multiple sessions are active. Returned device and session metadata still
allows best-effort conservative cleanup after Home Assistant restarts.

Session state is deliberately ephemeral: it is neither stored nor restored, and no
attempts resume after restart. Unload and Home Assistant shutdown cancel tasks and
remove listeners without broadcasting cleanup to every device. Companion sensors are
not required; interactive state is not treated as a stop signal because waking the
screen would make it true.

The friendly `speak` action is the other deliberately supported notification wrapper.
It uses Companion's documented `message: TTS` contract and exposes only default music
playback, `alarm_stream`, and `alarm_stream_max`. Dispatch cannot prove playback; the
device TTS engine, locale, audio state, Android restrictions, and Companion settings
remain authoritative. `find_phone` reuses the same TTS payload builder without changing
its behavior.

`check_device` sends no command. Its action response reports facts available from Home
Assistant's device registry and current Mobile App registration: registration presence,
reported OS/app metadata, push support, and exact notify-target resolution. Compatibility
observations are metadata-based, while Android permissions and actual command execution
are explicitly left unknown.
