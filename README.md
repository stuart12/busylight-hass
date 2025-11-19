# busylight-hass
This script automatically creates a light managed by
[busylight-core](https://github.com/JnyJny/busylight-core/) in
[HomeAssistant](https://www.home-assistant.io/integrations/light.mqtt/) using
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) and [aiomqtt](https://github.com/empicano/aiomqtt/).
Once this script, systemd unit and udev rules are installed, plugging in
a [BlinkStickStrip](https://www.blinkstick.com/products/blinkstick-strip) (for example)
will create a new MQTT light in every HomeAssistant
instance connected to the same MQTT server.
This MQTT light entity in HomeAssistant can immediately be used to switch the light on and off.

This script is designed to only manage one light. Light can be combined in HomeAssistant.
Inserting and removing the USB light starts and stops the busylight-hass server.

This script is a work in progress so breaking changes may arrive.

The systemd unit and udev rules files are ninja templates.
Replace the `{{ .. }}` strings with your values and install them without the `.j2` suffix.

TODO: 
1. open the USB light with a given USB path (`1-2:1.0` for example) without enumerating all HID devices. How?
2. handle colour changes
3. use brighness to control the number of on leds?
