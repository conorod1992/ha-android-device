# Android Device Control for Home Assistant

Control your Android phone or tablet from Home Assistant using simple, selectable actions.

**Android Device Control** is a custom Home Assistant integration that makes the official Android commands built into the Home Assistant Companion App easier to use in automations and scripts.

For example, you can:

- change ringer mode, volume, brightness, or screen timeout;
- turn the flashlight or Bluetooth on or off;
- request a location or sensor update;
- create alarms and timers;
- open apps, URLs, maps, settings, or Home Assistant entities;
- send normal, urgent, actionable, or repeating notifications;
- make a phone ring so you can find it;
- speak text on the device;
- control supported kiosk features; and
- use advanced Android intents when you need them.

You normally **do not need to know your `notify.mobile_app_*` action name, Android intent syntax, webhook ID, or device ID**. Select your Android device in the Home Assistant action editor and the integration handles the mapping for you.

> [!NOTE]
> This integration does not install anything extra on your Android device. It uses the official Home Assistant Companion App and its existing notification-command support.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Getting started](#getting-started)
- [Available actions](#available-actions)
- [Permissions and Android limitations](#permissions-and-android-limitations)
- [Notifications](#notifications)
- [Find phone](#find-phone)
- [Open, share and send things to your phone](#open-share-and-send-things-to-your-phone)
- [Alarms and timers](#alarms-and-timers)
- [Speaking on a device](#speaking-on-a-device)
- [Checking device compatibility](#checking-device-compatibility)
- [Advanced Android actions](#advanced-android-actions)
- [Troubleshooting](#troubleshooting)
- [Technical details](#technical-details)
- [Development](#development)

## Requirements

You need:

- **Home Assistant 2026.7 or newer**
- the official **Home Assistant Companion App for Android**
- the Android device registered with the same Home Assistant server
- push notifications enabled for that device

Most device-control commands must also be enabled in the Companion App:

1. Open the **Home Assistant Companion App** on the Android device.
2. Open **Settings → Companion app**.
3. Find **Notification commands** and make sure the commands you want to use are enabled.

Some actions require extra Android permissions. For example, changing brightness requires permission to modify system settings. See [Permissions and Android limitations](#permissions-and-android-limitations).

> [!IMPORTANT]
> Home Assistant can confirm that it sent a request to the Companion App, but it cannot guarantee that Android carried it out. Android permissions, battery restrictions, device-manufacturer changes, or the receiving app can still prevent or alter the result.

## Installation

### HACS

If you already use HACS:

1. Open **HACS** in Home Assistant.
2. Add this repository as a **custom repository**:
   `https://github.com/conorod1992/ha-android-device`
3. Choose **Integration** as the repository type.
4. Install **Android Device Control**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services**.
7. Select **Add integration**.
8. Search for **Android Device Control** and complete setup.

If this is your first custom HACS repository, adding the repository only makes it available in HACS. You still need to install it, restart Home Assistant, and then add the integration under **Devices & services**.

### Manual installation

If you are not using HACS:

1. Download or clone this repository.
2. Copy the folder:

   ```text
   custom_components/android_device_control
   ```

   into the `custom_components` folder inside your Home Assistant configuration directory.

3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**.
5. Search for **Android Device Control** and complete setup.

After setup, the integration checks the Android Companion App devices already registered with Home Assistant.

No manual pairing or `notify.mobile_app_*` mapping is required.

## Getting started

Android Device Control is **action-only**. It does not create switches, buttons, or other normal Home Assistant entities.

You use it inside an **automation** or **script**.

### Example using the Home Assistant UI

To make a phone vibrate:

1. Open an automation or script.
2. Add an **action**.
3. Search for **Android Device Control**.
4. Choose **Set ringer mode**.
5. Select the Android device.
6. Choose **Vibrate**.
7. Save and run the automation or script.

That is all that is required for normal use.

The YAML equivalent is:

```yaml
action: android_device_control.set_ringer_mode
data:
  device_id: YOUR_DEVICE_ID
  mode: vibrate
```

The examples below use YAML because it is compact and easy to copy, but the normal actions also provide Home Assistant UI fields and selectors.

## Available actions

The integration includes friendly actions for most supported tasks.

### Sound and media

| Action | What it does |
| --- | --- |
| `set_ringer_mode` | Set normal, vibrate, or silent mode |
| `set_volume` | Set an Android audio-stream volume |
| `set_do_not_disturb` | Change Do Not Disturb mode |
| `media_control` | Play, pause, skip, rewind, and more |
| `speak` | Speak text using Companion App TTS |
| `stop_tts` | Stop current Companion App TTS |
| `find_phone` | Repeatedly sound a phone until stopped |
| `stop_find_phone` | Stop an active Find Phone session |

### Screen and device controls

| Action | What it does |
| --- | --- |
| `turn_screen_on` | Wake the screen |
| `set_screen_brightness` | Set brightness from 0–100% |
| `set_auto_brightness` | Turn automatic brightness on or off |
| `set_screen_timeout` | Change how long the screen stays on |
| `set_flashlight` | Turn the flashlight on or off |
| `set_bluetooth` | Turn Bluetooth on or off where Android permits it |
| `set_app_lock` | Configure Companion App lock behaviour |
| `set_persistent_connection` | Change Companion App persistent-connection mode |
| `set_wake_word_detection` | Turn Assist wake-word detection on or off |

### Location, sensors and Bluetooth features

| Action | What it does |
| --- | --- |
| `request_location_update` | Ask the Companion App for a fresh location |
| `update_sensors` | Ask enabled Companion sensors to update |
| `set_high_accuracy_mode` | Change high-accuracy location mode |
| `set_high_accuracy_interval` | Change its update interval |
| `set_ble_transmitter` | Turn the Companion BLE transmitter on or off |
| `configure_ble_transmitter` | Change BLE transmitter settings |
| `set_beacon_monitor` | Turn beacon monitoring on or off |

### Alarms and timers

| Action | What it does |
| --- | --- |
| `set_alarm` | Create an alarm |
| `dismiss_alarm` | Dismiss an alarm |
| `snooze_alarm` | Snooze the currently ringing alarm |
| `show_alarms` | Open the alarm screen |
| `set_timer` | Start a timer |
| `dismiss_expired_timers` | Dismiss expired timers |
| `show_timers` | Open the timer screen |

### Notifications

| Action | What it does |
| --- | --- |
| `notify` | Send a normal Android notification |
| `notify_urgent` | Request immediate/high-priority delivery |
| `prompt` | Send a notification with custom buttons |
| `ask_yes_no` | Send a Yes/No notification |
| `ask_choice` | Send a notification with up to three choices |
| `notify_until_acknowledged` | Repeat a notification until acknowledged |
| `stop_notify_until_acknowledged` | Stop a repeating notification |
| `clear_notification` | Clear a tagged notification |
| `remove_notification_channel` | Remove an Android notification channel |

### Open, launch and share

| Action | What it does |
| --- | --- |
| `open_url` | Open a web address |
| `open_entity` | Open a Home Assistant entity |
| `open_webview` | Open a Companion App page or entity |
| `show_map` | Show a location on a map |
| `navigate_to` | Request directions using a supported provider |
| `share_text` | Open Android's share UI with text |
| `share_url` | Open Android's share UI with a URL |
| `dial_number` | Open the dialler with a number filled in |
| `compose_sms` | Open an SMS editor |
| `compose_email` | Open an email editor |
| `create_calendar_event` | Open a new calendar event |
| `web_search` | Start a web search |
| `launch_app` | Open an Android app |
| `open_settings` | Open a selected Android settings page |
| `open_app_settings` | Open settings for a selected app |
| `open_camera` | Open the still camera |
| `open_video_camera` | Open the video camera |

Actions such as `dial_number`, `compose_sms`, `compose_email`, and `create_calendar_event` **open the appropriate Android screen**. They do not automatically place a call, send the message, send the email, or save the calendar event.

### Kiosk controls

When Companion App kiosk mode is configured, the integration also provides:

- `kiosk_show_screensaver`
- `kiosk_hide_screensaver`
- `kiosk_show_camera`
- `kiosk_hide_camera`
- `kiosk_set_brightness`
- `kiosk_set_volume`
- `kiosk_reload`
- `kiosk_default`

### Advanced actions

For use cases that need Android intent details:

- `launch_activity`
- `send_broadcast_intent`
- `send_command`

You can also use `check_device` to inspect whether Home Assistant can correctly resolve a selected Android device.

For the complete command-by-command implementation list, see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Permissions and Android limitations

Not every Android command is allowed automatically. Some require a one-time permission on the phone, and some behave differently depending on Android version or manufacturer.

A common pattern is:

1. Run the action once.
2. The Companion App opens or shows a notification asking for permission.
3. Grant the requested Android permission.
4. Run the Home Assistant action again.

| Feature | Requirement or limitation |
| --- | --- |
| Ringer, DND and some volume controls | Notification Policy access may be required. Ringer changes can also affect DND. |
| Bluetooth | Direct Bluetooth toggling is only supported on Android versions where the Companion App and Android permit it. |
| Flashlight | Camera permission |
| Brightness and screen timeout | Modify system settings permission |
| Opening webviews, apps or activities | Display over other apps may be required |
| Alarms and timers | A compatible clock app must support the requested Android action |
| Media control | Notification access and an active media session for the selected app |
| Wake word detection | Home Assistant must be the default digital assistant and microphone access is required |
| Location | Appropriate location/background permissions; updates are best-effort |
| BLE and beacon monitoring | Relevant Companion sensors/settings and Android Bluetooth permissions |
| App lock | A supported biometric or device credential |
| Notification channels | Android 8 or newer |
| Kiosk commands | Companion App must be in the foreground with kiosk features configured and **Accept kiosk remote commands** enabled |
| Speak/TTS | Playback depends on Android audio state, TTS engine and Companion settings |

Android 15 and newer place additional restrictions on Do Not Disturb changes. Android also increasingly restricts direct control of system features such as Bluetooth.

Device manufacturers can add their own battery or background-execution restrictions.

## Notifications

### Normal notification

```yaml
action: android_device_control.notify
data:
  device_id: YOUR_DEVICE_ID
  title: Washing machine
  message: The washing machine has finished.
```

### Urgent notification

`notify_urgent` asks the Companion App for immediate/high-priority delivery.

```yaml
action: android_device_control.notify_urgent
data:
  device_id: YOUR_DEVICE_ID
  title: Water leak
  message: Check the utility room.
  tag: water-leak
```

This does **not** override Android's notification-channel settings, force maximum volume, or bypass manufacturer restrictions.

### Yes/No or choice notifications

```yaml
action: android_device_control.ask_yes_no
data:
  device_id: YOUR_DEVICE_ID
  title: Garage
  message: Close the garage door?
```

For a custom choice:

```yaml
action: android_device_control.ask_choice
data:
  device_id: YOUR_DEVICE_ID
  title: Destination
  message: Where should I navigate?
  choices:
    - id: home
      title: Home
    - id: work
      title: Work
```

Android currently shows at most three notification action buttons, so choice actions are limited to three options.

When a user presses a button, the integration fires:

```text
android_device_control_notification_action
```

The event includes the selected logical action ID along with device/session information.

### Repeat until acknowledged

`notify_until_acknowledged` sends a notification immediately and then repeats it until the user acknowledges it or the configured attempt limit is reached.

By default it repeats every five minutes for up to five attempts.

```yaml
action: android_device_control.notify_until_acknowledged
data:
  device_id: YOUR_DEVICE_ID
  title: Freezer door
  message: Please check and acknowledge.
  tag: freezer-door
  repeat_interval:
    minutes: 5
  max_attempts: 5
```

To stop it explicitly:

```yaml
action: android_device_control.stop_notify_until_acknowledged
data:
  device_id: YOUR_DEVICE_ID
  tag: freezer-door
```

Repeating-notification sessions are stored in memory only. Restarting Home Assistant or unloading the integration stops future repeats.

## Find phone

`find_phone` is designed for the familiar "where did I leave my phone?" use case.

By default it:

- sends an audible notification immediately;
- repeats every 15 seconds;
- stops after 10 attempts if not stopped earlier;
- adds a **Stop ringing** button; and
- can stop automatically when the phone is unlocked.

Basic use:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
```

You can change the repeat behaviour:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  max_attempts: 20
  repeat_interval:
    seconds: 12
```

Or send only one attempt:

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  repeat: false
```

### Maximum-volume TTS

As an alternative to the ringtone notification, Find Phone can use text to speech. Companion temporarily raises the alarm stream to maximum, speaks the message, and restores the previous volume.

```yaml
action: android_device_control.find_phone
data:
  device_id: YOUR_DEVICE_ID
  sound_mode: tts
  message: Finding phone
```

To stop an active session:

```yaml
action: android_device_control.stop_find_phone
data:
  device_id: YOUR_DEVICE_ID
```

### Optional: faster automatic stop when unlocked

For the quickest unlock detection, configure Android's `USER_PRESENT` broadcast on each target phone:

1. Open the **Home Assistant Companion App**.
2. Go to **Manage Sensors → Last Update Trigger → Intent**.
3. Add:

   ```text
   android.intent.action.USER_PRESENT
   ```

4. Force-stop and reopen the Companion App.

If you do not configure this, Find Phone still works. The integration can use the Companion App's **Keyguard Locked** sensor as a fallback when available, and the **Stop ringing** button is always available.

Find Phone sessions are held in memory. A Home Assistant restart stops future attempts.

## Open, share and send things to your phone

These actions are useful when you want an automation to put something directly in front of you on the phone.

### Open a URL

```yaml
action: android_device_control.open_url
data:
  device_id: YOUR_DEVICE_ID
  url: "https://www.home-assistant.io/"
```

### Open a Home Assistant entity

```yaml
action: android_device_control.open_entity
data:
  device_id: YOUR_DEVICE_ID
  entity_id: light.kitchen
```

### Navigate somewhere

```yaml
action: android_device_control.navigate_to
data:
  device_id: YOUR_DEVICE_ID
  location: Dublin Airport
  provider: google_maps
  travel_mode: driving
```

Coordinates can also be used:

```yaml
action: android_device_control.navigate_to
data:
  device_id: YOUR_DEVICE_ID
  latitude: 53.4264
  longitude: -6.2499
  provider: waze
```

The default provider lets Android choose a suitable map app. Google Maps and Waze can be selected explicitly.

### Share text

```yaml
action: android_device_control.share_text
data:
  device_id: YOUR_DEVICE_ID
  text: "The alarm is armed."
  subject: Home status
```

### Compose an SMS

```yaml
action: android_device_control.compose_sms
data:
  device_id: YOUR_DEVICE_ID
  recipient: "+3531234567"
  message: "On my way"
```

This opens the message for the user to review and send.

### Compose an email

```yaml
action: android_device_control.compose_email
data:
  device_id: YOUR_DEVICE_ID
  to:
    - person@example.com
  subject: Meeting notes
  body: "Hello from Home Assistant"
```

### Open an app

Common apps can be selected in the Home Assistant action editor, so you do not normally need to know package IDs.

```yaml
action: android_device_control.launch_app
data:
  device_id: YOUR_DEVICE_ID
  app: com.spotify.music
```

For an app not listed in the selector, choose a custom package and enter its Android package ID.

<details>
<summary>Built-in app package presets</summary>

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

</details>

The Companion App does not expose a complete installed-app list to Home Assistant, so the integration cannot automatically build the selector from every app installed on the phone.

## Alarms and timers

Alarm and timer actions use Android's standard alarm-clock interface rather than private Google Clock commands.

This means Android can normally choose a compatible clock app. You can also explicitly target Google Clock or provide another compatible package.

### Set a weekday alarm

```yaml
action: android_device_control.set_alarm
data:
  device_id: YOUR_DEVICE_ID
  alarm_time: "07:30:00"
  label: Work
  repeat:
    - monday
    - tuesday
    - wednesday
    - thursday
    - friday
  skip_ui: true
```

`skip_ui: true` asks the clock app to create the alarm without showing a confirmation screen. The receiving clock app controls whether that request is honoured.

### Set a timer

```yaml
action: android_device_control.set_timer
data:
  device_id: YOUR_DEVICE_ID
  duration:
    minutes: 10
  label: Pasta
```

The integration supports timers from 1 second to 24 hours.

Compatibility varies between Android versions and clock apps. See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for detailed support information.

## Speaking on a device

`speak` uses the official Companion App text-to-speech notification support.

```yaml
action: android_device_control.speak
data:
  device_id: YOUR_DEVICE_ID
  message: The washing machine has finished.
  playback_mode: normal
```

Playback modes include:

- **normal** — use the normal media/music stream;
- **alarm** — use the alarm stream;
- **alarm_max** — temporarily raise the alarm stream to maximum and let Companion restore it after playback.

Home Assistant can confirm dispatch, but Android audio state, TTS-engine availability, Companion settings, and device restrictions can still prevent speech.

## Checking device compatibility

If an action is not working, `check_device` can confirm whether Home Assistant can correctly resolve the selected Companion App registration.

```yaml
action: android_device_control.check_device
data:
  device_id: YOUR_DEVICE_ID
response_variable: android_check
```

It reports information such as:

- the matched Mobile App registration;
- Android/app metadata available to Home Assistant;
- push availability;
- notification-target availability; and
- known compatibility observations.

It does **not** contact the phone or prove that a particular Android permission has been granted.

## Advanced Android actions

Most users can stop reading here.

The actions in this section are for Android features that are not covered by the friendly actions above.

### Launch an Android activity

`launch_activity` exposes Android's activity-intent system.

Example:

```yaml
action: android_device_control.launch_activity
data:
  device_id: YOUR_DEVICE_ID
  intent_action: android.intent.action.SEND
  mime_type: text/plain
  structured_extras:
    - name: android.intent.extra.TEXT
      type: string
      value: "Hello, café"
```

Structured extras support:

- `string`
- `boolean`
- `integer`
- `long`
- `float`
- `double`
- `integer_list`

Raw extras remain available for compatibility, but raw and structured extras cannot be used together.

### Send a broadcast intent

```yaml
action: android_device_control.send_broadcast_intent
data:
  device_id: YOUR_DEVICE_ID
  package_name: com.urbandroid.sleep
  intent_action: com.urbandroid.sleep.alarmclock.ALARM_STATE_CHANGE
  extras: alarm_label:work,alarm_enabled:false
```

You need to know the broadcast contract supported by the receiving Android app.

### Raw Companion command

`send_command` is an escape hatch for supported Companion App commands that do not yet have a dedicated friendly action.

```yaml
action: android_device_control.send_command
data:
  device_id: YOUR_DEVICE_ID
  command: command_future_feature
  data:
    command: turn_on
```

Prefer a dedicated Android Device Control action whenever one exists. Dedicated actions provide Home Assistant selectors, validation, and clearer errors.

## Troubleshooting

### I cannot see Android Device Control when adding an integration

Make sure you:

1. installed the repository in HACS rather than only adding it as a custom repository;
2. restarted Home Assistant after installation; and
3. are using Home Assistant 2026.7 or newer.

### Setup says it cannot find an Android device

Open the official Home Assistant Companion App on the Android device and make sure it is connected to the same Home Assistant server.

The integration only targets Android devices registered through Home Assistant's **Mobile App** integration.

### My device is listed, but an action does nothing

Check:

1. push notifications are enabled for the Home Assistant Companion App;
2. notification commands are enabled in Companion App settings;
3. the action's required Android permission has been granted;
4. the Android version supports the requested feature; and
5. battery/background restrictions from the device manufacturer are not blocking the Companion App.

Try running the action manually from a simple script while testing.

### The command appears as an ordinary notification

This usually means the Companion App could not process the command. Common causes are:

- notification commands are disabled;
- a required value is invalid or missing; or
- the Android/Companion version does not support the requested command.

Using the dedicated Android Device Control action instead of `send_command` is recommended because it validates known fields before sending.

### Home Assistant says there is no Mobile App notification target

Enable notifications in the Companion App and restart Home Assistant so the Mobile App notification action is registered.

### One phone failed in an action targeting several devices

Each selected device is validated before sending. Once sending begins, devices are handled independently.

If one fails after another has already succeeded, the successful phone may already have received the request. The error identifies the failed device or devices.

### Find Phone does not stop immediately

A notification sound that Android has already started may continue briefly after its notification is cleared.

TTS mode can usually be stopped more directly through the Companion App's TTS support.

### Debug logging

For deeper troubleshooting, enable debug logging for:

```text
custom_components.android_device_control
```

Debug logging records device resolution and command names, but does not log arbitrary intent extras or raw payload contents.

## Technical details

Android Device Control does not add a new protocol or install a second Android app.

Under the hood it uses the Android notification-command support already provided by the official Home Assistant Companion App:

```text
Automation or script
        ↓
Android Device Control action
        ↓
Validate selected Home Assistant device
        ↓
Existing Mobile App notification target
        ↓
Official Home Assistant Companion App
        ↓
Android
```

The integration resolves the selected Home Assistant device through the Mobile App registration rather than constructing a `notify.mobile_app_*` name from the device's friendly name.

This avoids relying on name matching and helps prevent accidentally targeting a different device with a similar name.

For multi-device actions, targets are validated before commands are sent. After validation, sends run independently, so one later failure does not undo commands already delivered to another target.

### Brightness

The friendly `set_screen_brightness` action accepts **0–100%** and converts it to Android's 0–255 brightness scale.

Existing YAML using the older raw `level: 0..255` form remains supported for backwards compatibility, but new automations should use `brightness`.

`set_volume` is different: Android audio streams use their own absolute volume levels rather than a universal percentage.

### Intent/provider behaviour

`navigate_to` can use Android's standard map handling, Google Maps, or Waze.

The generic/default option opens the destination using a suitable Android handler. Google Maps and Waze can explicitly request navigation using their public interfaces.

Camera actions open a camera app. They cannot remotely capture a photograph or return media to Home Assistant.

### Delivery versus execution

A successful Home Assistant action means the request was handed to Home Assistant's Mobile App notification system.

It does **not** prove that:

- push reached the phone immediately;
- Android accepted the command;
- a required permission was granted;
- an opened activity completed its task; or
- a notification was visibly displayed.

For actionable notifications, a returned button event is evidence that the user actually pressed that button.

## Development

Install test requirements and run the local checks:

```bash
python -m pip install -r requirements_test.txt
ruff format --check .
ruff check .
pytest
```

The repository also runs HACS validation and hassfest in GitHub Actions.

Implementation and protocol behaviour are based on:

- [Home Assistant Companion App notification commands](https://companion.home-assistant.io/docs/notifications/notification-commands/)
- [Home Assistant actionable notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/)
- [Home Assistant critical notifications](https://companion.home-assistant.io/docs/notifications/critical-notifications/)
- [Android sharing guidance](https://developer.android.com/training/sharing/send)
- [Android AlarmClock API](https://developer.android.com/reference/android/provider/AlarmClock)
- [Home Assistant action development guidance](https://developers.home-assistant.io/docs/dev_101_services/)

For the detailed implementation and compatibility inventory, see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## License

MIT — see [LICENSE](LICENSE).
