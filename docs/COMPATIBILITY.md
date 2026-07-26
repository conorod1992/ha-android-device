# Companion command compatibility matrix

Inventory reviewed against the official Android command list and Companion App source on 2026-07-26.

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
| `command_screen_brightness_level` | Implemented | `set_screen_brightness` |
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

Android's arbitrary running-timer dismissal is deliberately not wrapped. The standard
`ACTION_DISMISS_TIMER` contract requires a timer-specific data URI, and neither the
Mobile App registration nor a documented Companion sensor exposes those URIs. The
implemented `dismiss_expired_timers` action uses the standard no-URI behavior instead.

The integration deliberately does not wrap ordinary notification features such as posting messages, TTS creation, attachments, or notification actions. Those are notification payload features rather than Android notification commands and remain available directly through Mobile App notify actions.
