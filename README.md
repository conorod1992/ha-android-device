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

Setup discovers existing official Android Companion App registrations and shows their
friendly names and readiness. No device or `notify.mobile_app_*` mapping is required.
This is an action-only integration: it does not create normal control entities. After
setup, open an Automation or Script and choose an **Android Device Control** action.
If setup finds no Android device, it explains how to register one but still allows the
single integration entry to be created.

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
| `set_screen_brightness` | `command_screen_brightness_level` | `brightness`: 0–100%, converted to 0–255 |
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
| `set_alarm` | `command_activity` | time, repeat days, label, vibration, ringtone, skip UI, optional clock package |
| `dismiss_alarm` | `command_activity` | next alarm, time, or label search |
| `snooze_alarm` | `command_activity` | optional whole-minute duration |
| `show_alarms` | `command_activity` | optional clock package |
| `set_timer` | `command_activity` | 1 second to 24 hours, label, skip UI, optional clock package |
| `dismiss_expired_timers` | `command_activity` | dismisses all expired timers |
| `show_timers` | `command_activity` | optional clock package; Android 8.0+ |
| `launch_app` | `command_launch_app` | common app preset or custom package ID |
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
| `notify` / `notify_urgent` | Companion notification | curated normal or immediate/high-priority delivery |
| `prompt` / `ask_yes_no` / `ask_choice` | actionable notification | one to three validated buttons and an integration event |
| `notify_until_acknowledged` / `stop_notify_until_acknowledged` | managed notification | bounded, per-device acknowledgement sessions |
| `speak` | Companion TTS notification | text; normal, alarm, or maximum alarm playback |
| `check_device` | no command sent | structured registration and compatibility response |

Friendly standard-intent actions form a second layer on `command_activity`:

| Action | Public Android/provider contract |
| --- | --- |
| `open_url` | `ACTION_VIEW` with an absolute URI |
| `share_text` / `share_url` | `ACTION_SEND` with `text/plain` and structured extras |
| `show_map` / `navigate_to` | `geo:`, Google Maps URLs, or Waze Deep Links |
| `dial_number` | `ACTION_DIAL` (never calls automatically) |
| `compose_sms` / `compose_email` | `ACTION_SENDTO` with `smsto:` / `mailto:` |
| `create_calendar_event` | `ACTION_INSERT` with Calendar event extras |
| `web_search` | `ACTION_WEB_SEARCH` |
| `open_settings` / `open_app_settings` | curated public `Settings` actions |
| `open_camera` / `open_video_camera` | standard still/video camera actions |
| `open_entity` | existing Companion `entityId:` webview support |
| `find_phone` / `stop_find_phone` | bounded, stoppable ringtone (default) or maximum-volume TTS session |

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

Set screen brightness using a percentage. The integration converts it to Android's
0–255 scale with deterministic half-up rounding (`0% → 0`, `50% → 128`,
`100% → 255`):

```yaml
action: android_device_control.set_screen_brightness
data:
  device_id: 12ab34cd56ef
  brightness: 50
```

For backwards compatibility, existing YAML using raw `level: 0..255` continues to
work. The raw field is hidden from the friendly action editor and is deprecated for
new automations. `set_volume` remains an absolute Android stream level; it is not a
percentage. Kiosk brightness and volume remain validated 0–100% values.

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

Set a weekday alarm with Android's default clock app:

```yaml
action: android_device_control.set_alarm
data:
  device_id: 12ab34cd56ef
  alarm_time: "07:30:00"
  label: Work
  repeat: [monday, tuesday, wednesday, thursday, friday]
  skip_ui: true
```

Launch Spotify from the common-app selector:

```yaml
action: android_device_control.launch_app
data:
  device_id: 12ab34cd56ef
  app: com.spotify.music
```

Launch an arbitrary package (the legacy package-only form remains supported):

```yaml
action: android_device_control.launch_app
data:
  device_id: 12ab34cd56ef
  package_name: com.example.app
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

## Notifications

`notify` sends a deliberately small Android notification payload: message, optional
title/tag/channel/importance, sticky behavior, and a bounded timeout. `notify_urgent`
adds Companion's documented immediate delivery request (`ttl: 0`, `priority: high`) and
defaults to an **Urgent** channel with high requested importance. It does not change
volume or bypass Android/OEM/user channel settings.

```yaml
action: android_device_control.notify_urgent
data:
  device_id: 12ab34cd56ef
  title: Water leak
  message: Check the utility room.
  tag: water-leak
```

`prompt`, `ask_yes_no`, and `ask_choice` accept friendly logical IDs and visible labels.
Companion receives collision-resistant action tokens. A real button press emits
`android_device_control_notification_action` with `device_id`, `session_id`, logical
`action_id`, and `tag`; unrelated, stale, or malformed action events are ignored.
Android currently displays at most three notification actions, so prompts and choices
are validated to one through three unique actions.

```yaml
action: android_device_control.ask_choice
data:
  device_id: 12ab34cd56ef
  title: Destination
  message: Where should I navigate?
  choices:
    - {id: home, title: Home}
    - {id: work, title: Work}
```

`notify_until_acknowledged` dispatches immediately, then repeats every five minutes by
default for at most five attempts. Each device has an independent in-memory session.
A new call with the same device and tag stops and clears the old session before
replacing it. The acknowledgement button stops future attempts, requests tagged
notification clearing, and emits
`android_device_control_notification_acknowledged`. Explicitly stop and clear with
`stop_notify_until_acknowledged`. Restart/unload cancels future attempts; sessions are
not persisted.

```yaml
action: android_device_control.notify_until_acknowledged
data:
  device_id: 12ab34cd56ef
  title: Freezer door
  message: Please check and acknowledge.
  tag: freezer-door
  repeat_interval: {minutes: 5}
  max_attempts: 5
```

Home Assistant action responses report dispatch facts and session IDs where applicable.
They do not claim Android displayed a notification. Only returned Companion button
events provide evidence of a prompt choice or acknowledgement.

## Send to phone

These actions are grouped by what UI they ask Android or Companion to open:

- **Open:** `open_url`, `open_entity`, `show_map`
- **Navigate:** `navigate_to`
- **Share:** `share_text`, `share_url`
- **Compose:** `dial_number`, `compose_sms`, `compose_email`
- **Launch/search:** `launch_app`, `web_search`

The existing names remain canonical; no redundant `send_search`, `send_entity`,
`send_navigation`, phone-number, SMS, email, or location aliases are added. **Open**
shows a destination, **navigate** requests directions, **compose** pre-fills an editor,
and **share** invokes Android's `ACTION_SEND` handler. Successful dispatch never means
the user completed the resulting Android action.

```yaml
action: android_device_control.share_text
data:
  device_id: 12ab34cd56ef
  text: "The alarm is armed."
  subject: Home status
```

```yaml
action: android_device_control.share_url
data:
  device_id: 12ab34cd56ef
  url: "https://www.home-assistant.io/"
  text: Worth reading
```

## Friendly intent examples

Open a URL and navigate to either an address or coordinates:

```yaml
action: android_device_control.open_url
data: {device_id: 12ab34cd56ef, url: "https://www.home-assistant.io/"}
```

```yaml
action: android_device_control.navigate_to
data:
  device_id: 12ab34cd56ef
  location: Dublin Airport
  provider: google_maps
  travel_mode: driving
```

```yaml
action: android_device_control.navigate_to
data:
  device_id: 12ab34cd56ef
  latitude: 53.4264
  longitude: -6.2499
  provider: waze
```

Dial, compose SMS, or compose email. These actions only open composition UI:

```yaml
action: android_device_control.dial_number
data: {device_id: 12ab34cd56ef, phone_number: "+353 1 234 5678"}
```

```yaml
action: android_device_control.compose_sms
data: {device_id: 12ab34cd56ef, recipient: "+3531234567", message: "On my way"}
```

```yaml
action: android_device_control.compose_email
data:
  device_id: 12ab34cd56ef
  to: [person@example.com]
  subject: Meeting notes
  body: "Hello from Home Assistant"
```

Open an event editor. Dispatch does not mean the user saved the event:

```yaml
action: android_device_control.create_calendar_event
data:
  device_id: 12ab34cd56ef
  title: Home Assistant meetup
  start: "2026-08-01 18:00:00"
  end: "2026-08-01 19:00:00"
  location: Dublin
  attendees: [person@example.com]
```

Search, open Bluetooth settings, or open Spotify's application details:

```yaml
action: android_device_control.web_search
data: {device_id: 12ab34cd56ef, query: Home Assistant Android}
```

```yaml
action: android_device_control.open_settings
data: {device_id: 12ab34cd56ef, page: bluetooth}
```

```yaml
action: android_device_control.open_app_settings
data: {device_id: 12ab34cd56ef, app: com.spotify.music, page: details}
```

Open either camera mode or an entity More Info panel:

```yaml
action: android_device_control.open_camera
data: {device_id: 12ab34cd56ef}
```

```yaml
action: android_device_control.open_video_camera
data: {device_id: 12ab34cd56ef}
```

```yaml
action: android_device_control.open_entity
data: {device_id: 12ab34cd56ef, entity_id: light.kitchen}
```

Control a common media app without knowing its package ID:

```yaml
action: android_device_control.media_control
data: {device_id: 12ab34cd56ef, media_command: pause, app: com.spotify.music}
```

Use structured extras with either advanced intent action. Supported types are
`string`, `boolean`, `integer`, `long`, `float`, `double`, and `integer_list`.
Strings are encoded centrally so commas, colons, spaces, and Unicode
survive the Companion wire format. Raw `extras` remain accepted, but the two forms
cannot be combined.

```yaml
action: android_device_control.launch_activity
data:
  device_id: 12ab34cd56ef
  intent_action: android.intent.action.SEND
  mime_type: text/plain
  structured_extras:
    - {name: android.intent.extra.TEXT, type: string, value: "Hello, café"}
    - {name: enabled, type: boolean, value: true}
```

`find_phone` starts a bounded, per-device session. The first audible attempt is sent
immediately; by default it then repeats every 15 seconds for at most 10 attempts. A new
call for the same device replaces its existing session. Multiple selected devices run
independently. By default, an active session also stops when the target phone is
unlocked. Set `stop_when_unlocked: false` to retain manual-only stopping.

Ringtone is the default and:

- sends one immediately delivered, high-priority notification on Companion's
  `alarm_stream` channel;
- plays the sound configured for that Android notification channel at the current
  alarm-stream volume; and
- uses the stable `find_phone` tag, so repeated attempts update one notification while
  sounding again.

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
```

Choose a repeat interval that suits the length of the alarm-channel sound configured on
the phone. Maximum attempts prevent indefinite ringing:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  max_attempts: 20
  repeat_interval:
    seconds: 12
```

For the former one-shot behavior:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  repeat: false
```

Text to speech is the alternative for maximum audibility. Companion temporarily saves
the current alarm volume, raises the alarm stream to maximum, speaks the message, and
restores the saved volume:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  sound_mode: tts
  message: Finding phone
```

When enabled (the default), the notification includes a **Stop ringing** button. It and
the explicit action below interrupt the waiting interval, prevent later attempts, stop
Companion TTS, and clear the tagged notification. If the active session turned on the
flashlight, Stop also turns it off. The button normally identifies its active session
from a unique action token, without depending on custom fields being echoed in the
Companion event. As a compatibility fallback, a prefixed but unmatched Stop action can
stop the sole active Find Phone session; it is ignored when multiple sessions make the
target ambiguous.

For the quickest unlock detection, configure Android's `USER_PRESENT` broadcast once
on each target phone:

1. Open **Home Assistant Companion** on the phone.
2. Go to **Manage Sensors → Last Update Trigger → Intent**.
3. Add `android.intent.action.USER_PRESENT`.
4. Force-stop and restart the Companion app.

Companion then sends a device-identified `android.intent_received` event immediately
after Android reports that phone as user-present. If this intent is not configured,
Find Phone still works: the enabled Companion **Keyguard Locked** binary sensor is used
as a fallback when available, and the **Stop ringing** button remains available. The
integration never treats screen-on or the Interactive sensor as proof of unlock,
because Find Phone can wake the screen itself. Broadcast delivery and keyguard update
timing can vary by Android version and device manufacturer.

```yaml
action: android_device_control.stop_find_phone
data:
  device_id: YOUR_DEVICE_ID
```

Sessions are in memory only. A Home Assistant restart cancels future attempts and does
not resume them. `stop_find_phone` remains useful after a restart: it still sends Stop
TTS and clears the notification. It does not turn off the flashlight without a known
session unless `turn_off_flashlight: true` is explicitly supplied, which protects an
unrelated manually enabled flashlight. A notification Stop pressed after restart also
performs best-effort cleanup when the Companion event includes enough device and
session information to identify the intended phone safely.

The optional Companion ringer, volume, and interactive sensors are mostly disabled by
default and are not prerequisites. Interactive state is not used as an automatic stop
signal because Find Phone itself wakes the screen. Ringtone mode does not manually
change or restore alarm volume. Screen wake and flashlight-on are sent only on the first
attempt; later attempts send sound only. A failure does not prevent future attempts.

Android may not immediately terminate notification-channel audio that is already
playing when its notification is cleared. TTS can generally be stopped more explicitly
through Companion's `command_stop_tts` support.

### Speak on an Android device

`speak` wraps the official Companion TTS payload. Normal playback uses the music stream;
`alarm` uses the alarm stream; and `alarm_max` temporarily raises the alarm stream to
maximum and lets Companion restore it after playback.

```yaml
action: android_device_control.speak
data:
  device_id: 12ab34cd56ef
  message: The washing machine has finished.
  playback_mode: normal
```

Dispatch only confirms that Home Assistant handed the request to Mobile App push. TTS
locale/engine health, audio state, Android restrictions, Companion settings, and device
settings can still prevent playback. `find_phone` reuses maximum-alarm TTS internally.

### Check device compatibility

`check_device` is read-only and returns Home Assistant action response data. It reports
the selected device and registration, OS/app metadata, push and notify-target
availability, a readiness status, and metadata-based observations such as the Android
13+ Bluetooth restriction. It does not contact the phone.

```yaml
action: android_device_control.check_device
data:
  device_id: 12ab34cd56ef
response_variable: android_check
```

The response separates verified Home Assistant facts from compatibility observations
and explicitly marks on-device execution as unverified. It never claims Android
permissions are granted because Mobile App registration data does not prove that.

## Intent and provider behavior

The default map behavior uses Android's standard `geo:` resolution. Android has no
generic public "start turn-by-turn navigation" intent, so the default provider opens
the destination in a capable handler. Choose Google Maps or Waze to explicitly request
navigation through documented public URLs. Google Maps supports driving, walking,
bicycling, and transit; Waze exposes driving behavior. Provider URLs can fall back to
a web experience when the app is absent.

`open_settings` contains only public Android constants. Each mapping records its
minimum API level; older Android versions and OEM builds may not resolve every page.
App permissions are reached through Application details because Android does not
publish a stable package-scoped permissions activity contract.

Camera actions only open the receiving camera. The transport cannot receive an Android
activity result, capture remotely, or retrieve media.

## Alarms and timers

These actions use Android's public
[`android.provider.AlarmClock`](https://developer.android.com/reference/android/provider/AlarmClock)
contract, not private Google Clock actions. By default no package is sent, so Android
resolves a compatible clock application. Choose Google Clock to target
`com.google.android.deskclock`, or choose Custom package for another compatible clock.
An installed clock app must support the relevant standard intent; successful dispatch
does not guarantee that the receiving app completed it.

`set_alarm` accepts a friendly time selector and translates repeat days to Android
`Calendar` weekday values. Omitting Vibrate uses Android's documented default of
enabled. Omitting Ringtone uses the platform default alarm sound; Silent sends the
standard `silent` value; Custom ringtone accepts a content URI. Skip confirmation UI
asks the clock app to bypass intermediate UI, but the receiver controls final behavior.

`dismiss_alarm` supports Android's next, time, and label search modes. A 24-hour time
is translated to Android's 12-hour search value plus an explicit AM/PM flag. If several
alarms match, the clock app may ask the user to choose. For a repeating alarm,
dismissal skips/dismisses the upcoming occurrence rather than permanently deleting the
series. The broad "all alarms" search is deliberately not exposed.

`snooze_alarm` applies only to the currently ringing alarm and is a no-op when none is
ringing. Its duration is optional; when omitted, the clock app chooses its default.
`show_alarms` opens the alarm page.

`set_timer` accepts 1 through 86,400 seconds using a duration selector. With Skip
confirmation UI enabled, Android specifies that the started timer is normally removed
after it is dismissed. `dismiss_expired_timers` implements Android's useful no-URI
behavior: it dismisses all expired timers. Arbitrary running timer dismissal is not
offered because the standard requires a timer-specific deep-link URI and Home Assistant
has no supported way to obtain one. `show_timers` requires Android 8.0 (API 26) or newer.
Set/show alarms and set timers require Android 4.4 (API 19) for the full field set;
dismiss/snooze actions require Android 6.0 (API 23).

## App launcher presets

The App selector stores canonical package IDs rather than integration-specific preset
keys. Existing automations containing only `package_name` remain valid. Choose Custom
package and enter Package ID in the editor, or continue using package-only YAML. A
common preset and `package_name` cannot be supplied together, avoiding ambiguous saved
automations.

| App | Package ID |
| --- | --- |
| Home Assistant | `io.homeassistant.companion.android` |
| Google Chrome | `com.android.chrome` |
| Google Maps | `com.google.android.apps.maps` |
| Google Clock | `com.google.android.deskclock` |
| Gmail | `com.google.android.gm` |
| Google Calendar | `com.google.android.calendar` |
| Google Photos | `com.google.android.apps.photos` |
| YouTube | `com.google.android.youtube` |
| YouTube Music | `com.google.android.apps.youtube.music` |
| Spotify | `com.spotify.music` |
| Netflix | `com.netflix.mediaclient` |
| Plex | `com.plexapp.android` |
| WhatsApp | `com.whatsapp` |
| Facebook | `com.facebook.katana` |
| Facebook Messenger | `com.facebook.orca` |
| Instagram | `com.instagram.android` |
| Reddit | `com.reddit.frontpage` |
| Discord | `com.discord` |
| Telegram | `org.telegram.messenger` |
| Microsoft Teams | `com.microsoft.teams` |
| Microsoft Outlook | `com.microsoft.office.outlook` |
| Microsoft Edge | `com.microsoft.emmx` |
| Waze | `com.waze` |
| Amazon Prime Video | `com.amazon.avod.thirdpartyclient` |
| Disney+ | `com.disney.disneyplus` |
| Firefox | `org.mozilla.firefox` |
| VLC | `org.videolan.vlc` |
| Google Drive | `com.google.android.apps.docs` |
| Google Keep | `com.google.android.keep` |
| Google Messages | `com.google.android.apps.messaging` |
| Google Phone | `com.google.android.dialer` |

The current Companion registration and documented sensors do not expose a complete,
reliable installed-app inventory. The Last used app and Frontmost app sensors report
individual observed packages, not all installed applications, so the integration uses
offline curated presets plus a custom package field. It does not query Google Play at
runtime. If the selected package is unavailable, the existing Companion App behavior
is preserved.

## Permissions and platform caveats

| Commands | Requirement or caveat |
| --- | --- |
| Ringer, DND, volume | Notification Policy access may be required. Ringer and volume can affect DND. On Android 15+, the app can only disable DND that it previously enabled. |
| Bluetooth | Direct toggling is documented only for Android 12 or older. Android 12 may require Nearby Devices/Bluetooth permission. |
| Flashlight | Camera permission. |
| Brightness and screen timeout | Modify system settings permission. Android applies device-specific screen-timeout limits. |
| Webview, app launch, activity | Display over other apps. Calling activities also need phone permission. |
| Alarm and timer activities | Display over other apps; a compatible clock app; action support varies by Android API level and OEM clock implementation. |
| Media | Notification access and an active media session owned by the supplied package. |
| Wake word | Home Assistant set as default digital assistant plus microphone permission; may use significant battery. |
| Location and sensors | Appropriate location/sensor permissions and background execution. Location requests are best effort and should not be polled frequently. |
| BLE and beacon monitoring | Companion sensors/settings and platform Bluetooth permissions must be enabled. Manufacturer behavior varies. |
| App lock | A supported biometric or device credential must be configured. |
| Notification channels | Channels exist on Android 8+. Removing a channel does not erase its Android system settings. |
| Kiosk commands | Companion App open in the foreground, kiosk features configured, and **Accept kiosk remote commands** enabled. |
| Assistant volume | Android 17+ and Home Assistant set as the default assistant. |
| Friendly activity intents | Display over other apps; a receiving activity must be installed and support the public contract. |
| Find phone | Ringtone uses the current alarm volume and configured `alarm_stream` channel sound. TTS temporarily maximises and restores the alarm stream. Clearing a notification may not immediately end audio already playing. Flashlight requires camera permission. |
| Speak | Normal TTS uses the music stream; alarm modes use the alarm stream. Dispatch cannot guarantee playback. |

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

The repository also runs HACS validation and hassfest in GitHub Actions. Protocol
research is based on the current [official notification documentation](https://companion.home-assistant.io/docs/notifications/notifications-basic/),
[actionable-notification documentation](https://companion.home-assistant.io/docs/notifications/actionable-notifications/),
[critical-notification documentation](https://companion.home-assistant.io/docs/notifications/critical-notifications/),
[Android sharing guidance](https://developer.android.com/training/sharing/send),
[Home Assistant action development guidance](https://developers.home-assistant.io/docs/dev_101_services/),
and the current Mobile App implementation in Home Assistant Core.

## License

MIT — see [LICENSE](LICENSE).
