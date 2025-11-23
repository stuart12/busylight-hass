# busylight-hass
This script automatically creates light entities managed by
[busylight-core](https://github.com/JnyJny/busylight-core/) in
[HomeAssistant](https://www.home-assistant.io/integrations/light.mqtt/) for USB lights using
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) and [aiomqtt](https://github.com/empicano/aiomqtt/).
Once this script, systemd unit and udev rules are installed, plugging in
a [BlinkStickStrip](https://www.blinkstick.com/products/blinkstick-strip) (for example)
will create a new MQTT light in every HomeAssistant
instance connected to the same MQTT server.
This MQTT light entity in HomeAssistant can immediately be used to switch the light on and off.

This script is designed to only manage one light which must be USB.
Lights can be combined into groups in HomeAssistant.
Inserting and removing a USB light starts and stops a busylight-hass server.
The busylight-hass server informs HomeAssistant that the USB light has been
inserted or removed so that the updated state of the light can be
reflected in the HomeAssistant UI and used in automations.

This script is a work in progress so breaking changes may arrive.

I created this project because HomeAssistant's [BlinkStick integration](https://www.home-assistant.io/integrations/blinksticklight/)
was disabled by https://github.com/home-assistant/core/pull/121846/.

## Installation

The systemd unit and udev rules files are ninja templates.
Replace the `{{ .. }}` strings with your values
(including the location of the busylight-hass.ps script),
remove ninja comments `{# .. #}`,
and install the files without the `.j2` suffix.

Reload systemd, plug in your light, check that it is available in HomeAssistant.

## TODO
2. handle colour changes
3. use brighness to control the number of on leds?

## Architecture

USB lights can be inserted and removed at any moment.
When a light is inserted HomeAssistant needs to be informed via
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery).
Similarly when an USB light is removed the HomeAssistant UI must be updated to show the light as unavailable.

It is possible to leverage udev and systemd to start a systemd unit when
an USB device is inserted and have that unit stopped when the USB device is removed.
See the included [busylight.rules.j2](./busylight.rules.j2) and
[busylight_hass@.service.j2](./busylight_hass@.service.j2) for how this can be done.
I thus decided to have each busylight-hass server only manage one USB light
and that a computer will run multiple busylight-hass servers if it has
multiple lights at a given moment.
