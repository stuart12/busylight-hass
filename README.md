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
1. open the USB light with a given USB path (`1-2:1.0` for example) without enumerating all HID devices. How?
2. handle colour changes
3. use brighness to control the number of on leds?

## Architecture

USB lights can be inserted and removed at any moment.
When a light is inserted HomeAssistant needs to be informed via
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery).
Similarly when an USB light is removed the HomeAssistant UI must be updated to show the light as unavailable.
During a quick look, I could not find any callbacks in busylight-core that could be used
to emit mqtt messages when USB lights are inserted and removed.

It is possible to leverage udev and systemd to start a systemd unit when
an USB device is inserted and have that unit stopped when the USB device is removed.
See the included [busylight.rules.j2](./busylight.rules.j2) and
[busylight_hass@.service.j2](./busylight_hass@.service.j2) for how this can be done.
I thus decided to have each busylight-hass server only manage one USB light
and that a computer will run multiple busylight-hass servers if it has
multiple lights at a given moment.
This also has the desirable property that if no lights are present no resources are used.

The systemd unit passes the path of the USB light that it is managing
to the busylight-hass script.
busylight-hass will manage the light at that path and no other.
busylight-core does not have a mechanism to open a light at a given path
so busylight-hass has to enumerate all lights and look in the list of lights
for the light at the correct path.
This is inelegant,
might cause problems at boot when multiple devices are added at the same time,
and might be what caused failures when `PrivateDevices = true`
was set in [busylight_hass@.service.j2](./busylight_hass@.service.j2) for extra
security.
It would be good, as discussed in [issue 3](https://github.com/JnyJny/busylight/issues/3#issuecomment-3563923604)
if `busylight_core.Light.available_hardware()` could take an argument to only return the light at a given USB path.
