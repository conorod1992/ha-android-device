# Android Device Control for Home Assistant

Android Device Control is a HACS custom integration that turns the official Home Assistant Companion App's Android notification commands into friendly, validated Home Assistant actions.

It adds no Android protocol and does not use ADB, MQTT, Tasker, or a separate app. It resolves a selected Home Assistant device to its existing Mobile App registration and dispatches the official notification command through Home Assistant's `notify.mobile_app` implementation.

```text
Automation → Android Device Control action → validation and translation
           → Mobile App notify target → official Companion App → Android
```

## Requirements

- Home Assistant 2026.7 or newer
- The official Home Assistant Companion App for Android, registered with this Home Assistant server
- Push notifications enabled for each target device
- Notification commands enabled in Companion App settings, except commands the app documents as always available
- Command-specific Android permissions described below

Android Device Control only confirms that Home Assistant dispatched the request. Android, the device manufacturer, or the Companion App can still reject, defer, or modify it.

## Installation

### HACS

1. In HACS, add `https://github.com/conorod1992/ha-android-device` as a custom **Integration** repository.
2. Install **Android Device Control**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **Android Device Control**, and confirm setup.

### Manual

Copy `custom_components/android_device_control` into the `custom_components` directory in your Home Assistant configuration, restart Home Assistant, then add the integration in **Settings → Devices & services**.

No device mapping is required. Action device selectors show Mobile App devices; runtime validation rejects iOS and registrations without push support.

## Device targeting

Actions accept one or more Home Assistant `device_id` values. The integration:

1. resolves each ID through Mobile App's live device-to-webhook map;
2. verifies the registration reports `os_name: Android` and supports push;
3. looks up the notify target registered for that exact webhook ID; and
4. sends to that webhook target.

It never constructs `notify.mobile_app_*` from a device name. All targets are validated before any command is sent, preventing accidental delivery to a similarly named device. Once validation succeeds, multi-device sends run independently; an error names failed devices, while already successful devices may have received the command.

## Actions

| Friendly action | Companion command | Important parameters |
| --- | --- | --- |
| `set_ringer_mode` | `command_ringer_mode` | `mode`: normal, vibrate, silent |
| `set_volume` | `command_volume_level` | `stream`, absolute `level` |
| `set_do_not_disturb` | `command_dnd` | off, priority only, alarms only, total silence |
| `media_control` | `command_media` | media command, package name |
| `stop_tts` | `command_stop_tts` | — |
| `turn_screen_on` | `command_screen_on` | optional keep-screen-on behavior |
| `set_screen_brightness` | `command_screen_brightness_level` | 0–255 |
| `set_auto_brightness` | `command_auto_screen_brightness` | enabled |
| `set_screen_timeout` | `command_screen_off_timeout` | friendly duration, converted to milliseconds |
| `open_webview` | `command_webview` | path or `entityId:domain.entity` |
| `set_flashlight` | `command_flashlight` | enabled |
| `set_bluetooth` | `command_bluetooth` | enabled |
| `set_ble_transmitter` | `command_ble_transmitter` | enabled |
| `configure_ble_transmitter` | `command_ble_transmitter` | advertise mode, transmit power, UUID, major, minor, measured power |
| `set_beacon_monitor` | `command_beacon_monitor` | enabled |
| `request_location_update` | `request_location_update` | — |
| `update_sensors` | `command_update_sensors` | — |
| `set_high_accuracy_mode` | `command_high_accuracy_mode` | on/off/force on/force off |
| `set_high_accuracy_interval` | `command_high_accuracy_mode` | interval ≥ 5 seconds |
| `launch_app` | `command_launch_app` | package name |
| `launch_activity` | `command_activity` | action; optional package, class, URI, MIME type, extras |
| `set_app_lock` | `command_app_lock` | enabled, timeout, home-Wi-Fi bypass |
| `set_wake_word_detection` | `command_wake_word_detection` | enabled |
| `set_persistent_connection` | `command_persistent_connection` | always, home Wi-Fi, screen on, never |
| `send_broadcast_intent` | `command_broadcast_intent` | action, package; optional class, URI, MIME type, extras |
| `clear_notification` | `clear_notification` | tag |
| `remove_notification_channel` | `remove_channel` | channel |
| `kiosk_show_screensaver` / `kiosk_hide_screensaver` | same as action name | — |
| `kiosk_show_camera` / `kiosk_hide_camera` | same as action name | camera entity for show |
| `kiosk_set_brightness` | same as action name | 0–100% |
| `kiosk_set_volume` | same as action name | 0–100% |
| `kiosk_reload` / `kiosk_default` | same as action name | — |
| `send_command` | guarded raw command | command message and arbitrary nested data |

See [the compatibility matrix](docs/COMPATIBILITY.md) for the complete current command inventory and implementation status.

## Examples

Set two devices to vibrate without knowing either notify action:

```yaml
action: android_device_control.set_ringer_mode
data:
  device_id:
    - 12ab34cd56ef
    - 78ab90cd12ef
  mode: vibrate
```

Set a 30-second screen timeout:

```yaml
action: android_device_control.set_screen_timeout
data:
  device_id: 12ab34cd56ef
  duration:
    seconds: 30
```

Open an entity's More Info panel:

```yaml
action: android_device_control.open_webview
data:
  device_id: 12ab34cd56ef
  path: entityId:light.kitchen
```

Send a typed broadcast intent:

```yaml
action: android_device_control.send_broadcast_intent
data:
  device_id: 12ab34cd56ef
  package_name: com.urbandroid.sleep
  intent_action: com.urbandroid.sleep.alarmclock.ALARM_STATE_CHANGE
  extras: alarm_label:work,alarm_enabled:false
```

Use the guarded escape hatch for a newly introduced Companion command:

```yaml
action: android_device_control.send_command
data:
  device_id: 12ab34cd56ef
  command: command_future_feature
  data:
    command: turn_on
```

The escape hatch still requires a verified Android Mobile App target. It accepts messages beginning with `command_` and the documented non-prefixed command messages. Prefer typed actions, which provide selectors and validation.

## Permissions and platform caveats

| Commands | Requirement or caveat |
| --- | --- |
| Ringer, DND, volume | Notification Policy access may be required. Ringer and volume can affect DND. On Android 15+, the app can only disable DND that it previously enabled. |
| Bluetooth | Direct toggling is documented only for Android 12 or older. Android 12 may require Nearby Devices/Bluetooth permission. |
| Flashlight | Camera permission. |
| Brightness and screen timeout | Modify system settings permission. Android applies device-specific screen-timeout limits. |
| Webview, app launch, activity | Display over other apps. Calling activities also need phone permission. |
| Media | Notification access and an active media session owned by the supplied package. |
| Wake word | Home Assistant set as default digital assistant plus microphone permission; may use significant battery. |
| Location and sensors | Appropriate location/sensor permissions and background execution. Location requests are best effort and should not be polled frequently. |
| BLE and beacon monitoring | Companion sensors/settings and platform Bluetooth permissions must be enabled. Manufacturer behavior varies. |
| App lock | A supported biometric or device credential must be configured. |
| Notification channels | Channels exist on Android 8+. Removing a channel does not erase its Android system settings. |
| Kiosk commands | Companion App open in the foreground, kiosk features configured, and **Accept kiosk remote commands** enabled. |
| Assistant volume | Android 17+ and Home Assistant set as the default assistant. |

When permission is missing, the Companion App may post a notification asking the user to open it, then show the relevant Android settings screen after the command is retried.

## Troubleshooting

- **Device is not an Android Companion device:** confirm the selected Home Assistant device belongs to an Android Mobile App registration, not iOS or an unrelated integration.
- **No Mobile App notification target:** enable notifications in the Companion App and restart Home Assistant so its notify action is registered.
- **Command appears as a normal notification:** the Companion App rejected its values, notification commands are disabled, or a required field is missing. Use a typed action and check Companion App notification history/logs.
- **Command dispatched but nothing changed:** grant the command-specific permission, check Android-version restrictions, manufacturer battery controls, and relevant Companion settings.
- **One target failed in a multi-device action:** the error lists failed devices. Successful targets may already have received the request.

Debug logging can be enabled for `custom_components.android_device_control`. It records resolution and command names, but never arbitrary intent extras or raw payload contents.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff format --check .
ruff check .
pytest
```

The repository also runs HACS validation and hassfest in GitHub Actions. Protocol research is based on the current [official notification command documentation](https://companion.home-assistant.io/docs/notifications/notification-commands/), [Home Assistant action development guidance](https://developers.home-assistant.io/docs/dev_101_services/), and the current Mobile App implementation in Home Assistant Core.

## License

MIT — see [LICENSE](LICENSE).
