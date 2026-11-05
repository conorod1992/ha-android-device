# Complete notification examples

Create the receiving automation first, then run the sending script. Both examples use
an Android device already registered with Home Assistant. Replace every
`YOUR_DEVICE_ID` with that device's Home Assistant device ID. The device selector can
fill this in when editing the action through the UI.

## Ask a question and inspect the result

Paste this into a script's YAML editor. Its response variable contains one result per
device. Each successful target gets its own opaque `session_id`.

```yaml
alias: Ask my Android phone
mode: parallel
max: 5
sequence:
  - action: android_device_control.ask_yes_no
    data:
      device_id: YOUR_DEVICE_ID
      title: Task reminder
      message: Have you finished the task?
      tag: task-choice
      yes_label: Finished
      no_label: Later
    response_variable: prompt_result
  - action: persistent_notification.create
    data:
      title: Android question sent
      message: >-
        {% for device in prompt_result.devices %}
          {{ device.device_name }}:
          {% if device.dispatched %}
            session {{ device.session_id }}
          {% else %}
            {{ device.error }}
          {% endif %}
        {% endfor %}
```

## Receive button and text replies

Paste this into an automation's YAML editor. The automation is always listening, so
it also catches a fast reply while the sending script is finishing.

```yaml
alias: Handle my Android task replies
mode: parallel
max: 10
triggers:
  - trigger: event
    event_type: android_device_control_notification_action
    event_data:
      device_id: YOUR_DEVICE_ID
      tag: task-choice
actions:
  - action: persistent_notification.create
    data:
      notification_id: "{{ 'android-answer-' ~ trigger.event.data.session_id }}"
      title: Android task answer
      message: >-
        Device: {{ trigger.event.data.device_id }}
        Session: {{ trigger.event.data.session_id }}
        Answer: {{ trigger.event.data.action_id }}
        Text: {{ trigger.event.data.response_text | default('(no text reply)') }}
```

The logical answer is `yes` or `no`, regardless of the visible button labels.
Match the event's `session_id` with the successful device's returned `session_id`
to identify a particular question. The notification ID above keeps answers from
different sessions distinct. A tag controls which phone notification is replaced;
it does not identify one unique session.

For free text, replace the sending action with:

```yaml
action: android_device_control.ask_text
data:
  device_id: YOUR_DEVICE_ID
  title: Task reminder
  message: What should I remind you about?
  tag: task-choice
  reply_label: Reply
  require_unlock: true
response_variable: prompt_result
```

The same receiver then gets `action_id: reply` and `response_text`. Prompt mappings
survive a Home Assistant restart and expire after 24 hours. No script waits for a
button indefinitely.

## Check receipt separately

Add `confirm_delivery: true` to a supported notification action. Listen for
`android_device_control_notification_received`; its `device_id`, `tag`, and
`session_id` identify the requested receipt. This confirms Companion receipt,
not visible display, command execution, or a user response.

`dispatched: true` only means that Home Assistant's notify action returned. Some
transport errors are logged internally by Mobile App instead of being raised.
