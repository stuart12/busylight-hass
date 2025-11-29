#!/usr/bin/python3
# Link busylight and homeassistant with mqtt
# Copyright 2025 Stuart Pook http://www.pook.it/
# https://github.com/stuart12/busylight-hass

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# https://github.com/JnyJny/busylight-core/
# https://github.com/empicano/aiomqtt/
# https://www.home-assistant.io/integrations/light.mqtt/
# https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery

import sys
import argparse
import time
import json
import logging
import re
import syslog
import functools
import collections
import asyncio
import socket
import aiomqtt
import busylight_core
import busylight_core.hid
import busylight_core.hardware


def get_options():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Link busylight and homeassistant with mqtt")
    parser.set_defaults(loglevel='warn')
    parser.set_defaults(initially_on=True)
    parser.add_argument('-o', "--off", '--no-on', dest='initially_on', action="store_false", help="start with light off")
    parser.add_argument('--on', '--no-off', dest='initially_on', action="store_true", help="start with light on (default)")
    parser.add_argument("-v", "--verbose", dest='loglevel', action="store_const", const='debug', help="debug loglevel")
    parser.add_argument("-l", "--loglevel", metavar="LEVEL", help="set logging level")
    parser.add_argument("--reconnect", type=float, metavar="SECONDS", default=10, help="delay reconnection attempts")
    parser.add_argument("--red", type=int, metavar="INTEGER", default=255, help="red level")
    parser.add_argument("--green", type=int, metavar="INTEGER", default=0, help="green level")
    parser.add_argument("--blue", type=int, metavar="INTEGER", default=0, help="blue level")
    parser.add_argument("--brightness", type=int, metavar="INTEGER", default=255, help="initial and default brightness")
    parser.add_argument("--mqttbroker", help="mqtt broker")
    parser.add_argument('--mqtt_tag', '--tag', default='busylight_hass', help='tag for mqtt broker and topics')
    parser.add_argument("--mqttuser", help="mqtt user")
    parser.add_argument("--mqttpassword", help="mqtt password")
    parser.add_argument("--mqttpasswordfile","--mqttpasswdfile", metavar="FILENAME", help="file containing mqtt password")
    parser.add_argument('path', nargs=1, help="light's USB path")
    options = parser.parse_args()
    return options


def get_password(password: str, password_file: str) -> str:
    if password:
        return password
    if not password_file:
        return None
    with open(password_file) as f:
        logging.debug("reading password from %s", password_file)
        return f.readline().strip('\n')


Outgoing = collections.namedtuple('Outgoing', ['topic', 'payload'])


def get_int(current: int, fields: list[int], position: int) -> int:
    if len(fields) <= position:
        return current
    try:
        return int(fields[position])
    except ValueError:
        return current


class Colour:
    def __init__(self, red: int, green: int, blue:int, brightness: int):
        self.red = red
        self.green = green
        self.blue = blue
        self.brightness = brightness

    def get_rgb(self) -> tuple[int, int, int]:
        if  self.brightness >= 255:
            r = [self.red, self.green, self.blue]
        elif self.brightness <= 0:
            r = [0, 0, 0]
        else:
            scale = self.brightness / 255.0
            r = [int(self.red * scale), int(self.green * scale), int(self.blue * scale)]
        return tuple(r)

    def update(self, fields: list[str], offset: int) -> None:
        self.red = get_int(self.red, fields, offset)
        self.green = get_int(self.green, fields, offset + 1)
        self.blue = get_int(self.blue, fields, offset + 2)
        self.brightness = get_int(self.brightness, fields, offset + 3)

    def state(self) -> str:
        return f"{self.red},{self.green},{self.blue},{self.brightness}"


def queue_current_state(on: bool, colour: Colour, outgoing: asyncio.Queue, discovery: dict) -> None:
    payload = f"{"on" if on else "off"},{colour.state()}"
    outgoing.put_nowait(Outgoing(topic=discovery['state_topic'], payload=payload))


async def transition(light: busylight_core.Light, fields: list[str], offset: int, rgb: tuple[int, int, int]) -> None:
    if light.nleds > 0 and len(fields) > offset and fields[offset]:
        try:
            transition = float(fields[offset])
        except ValueError:
            logging.info("bad transition from client: %s", fields)
        else:
            delay = transition / (light.nleds - 1.0)
            logging.debug("transition to %s leds=%d %s %f %f %d", rgb, light.nleds, fields, transition, delay, offset)
            first = True
            for led in range(1, light.nleds + 1):
                if not first:
                    await asyncio.sleep(delay)
                else:
                    first = False
                light.on(color=rgb, led=led)
            return
    logging.debug("set %s leds=%d %s %d", rgb, light.nleds, fields, offset)
    light.on(color=rgb)


async def listener(client: aiomqtt.Client, light: busylight_core.Light, discovery: dict, outgoing: asyncio.Queue, colour: Colour, on: bool) -> None:
    async for message in client.messages:
        logging.debug("got a message from client.messages: %s %s", message.topic, message.payload)
        topic = str(message.topic)
        payload = message.payload.decode().lower()
        if topic == discovery['command_topic']:
            fields = payload.split(",")
            if fields[0] == "off":
                await transition(light, fields, offset=1, rgb=(0, 0, 0))
                on = False
                logging.debug("after off colours are %s", light.color)
            elif fields[0] == "on":
                on = True
                colour.update(fields, 1)
                rgb = colour.get_rgb()
                await transition(light, fields, offset=5, rgb=rgb)
                logging.debug("after setting colours to %s colours are %s", rgb, light.color)
            else:
                logging.info("bad message from client: %s %s", message.topic, payload)
        else:
            logging.info("message on unexpected topic from client: %s %s", message.topic, message.payload)

        queue_current_state(on=on, colour=colour, outgoing=outgoing, discovery=discovery)

    logging.error("end of listener")


async def publisher(client: aiomqtt.Client, outgoing: asyncio.Queue) -> None:
    while True:
        message = await outgoing.get()
        logging.debug("sending %s (%d remaining)", message, outgoing.qsize())
        await client.publish(topic=message.topic, payload=message.payload, retain=True)
        outgoing.task_done()
    logging.error("end of publisher")


def make_topic(hardware: busylight_core.Hardware, topic: str, mqtt_tag: str) -> str:
    serial = re.sub(r'[ /+#]', '-', hardware.serial_number)
    return "/".join([mqtt_tag, "%#0x" % hardware.vendor_id, "%#0x" % hardware.product_id, serial, topic])


def make_discovery(hardware: busylight_core.Hardware, mqtt_tag: str) -> dict:
    identifier = "%0xdx%0xdx%s" % (hardware.vendor_id, hardware.product_id, re.sub(r'[^a-z0-9]', 'y', hardware.serial_number.lower()))
    discovery_topic = f"homeassistant/light/{mqtt_tag}/{identifier}/config"

    payload = {
        "device": {
            "identifiers": [identifier],
            "name": f"{hardware.product_string} f{hardware.serial_number}",
            "manufacturer": hardware.manufacturer_string,
            "model": hardware.product_string,
            "serial_number": hardware.serial_number,
            "sw_version": "%#0x" % hardware.release_number,
        },
        "origin": {
            "name": identifier,
            "sw_version": "1.0",
            "url": "https://github.com/stuart12/busylight_hass",
        },
        "unique_id": identifier,
        "object_id": identifier,
        "schema": "template",
        "availability_topic": make_topic(hardware, 'availability', mqtt_tag=mqtt_tag),
        "state_topic": make_topic(hardware, 'state', mqtt_tag=mqtt_tag),
        "command_topic": make_topic(hardware, 'command', mqtt_tag=mqtt_tag),
        "command_on_template": "on,{{ red | d }},{{ green | d }},{{ blue  | d }},{{ brightness | d }},{{ transition | d }}",
        "command_off_template": "off,{{ transition | d }}",
        "state_template": "{{ value.split(',')[0] }}",  # must return `on` or `off`
        "brightness_template": "{{ value.split(',')[4] }}",
        "red_template": "{{ value.split(',')[1] }}",
        "green_template": "{{ value.split(',')[2] }}",
        "blue_template": "{{ value.split(',')[3] }}",
        "qos": 2,
    }
    return payload


async def send_mqtt_configuration(client: aiomqtt.Client, payload: dict, mqtt_tag: str) -> None:
    # https://stevessmarthomeguide.com/adding-an-mqtt-device-to-home-assistant/
    # https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
    discovery_topic = f"homeassistant/light/{mqtt_tag}/{payload['unique_id']}/config"
    availability_topic = payload['availability_topic']
    logging.info("publishing homeassistance discovery on %s for %s", discovery_topic, availability_topic)
    await client.publish(discovery_topic, json.dumps(payload), retain=True)
    await client.publish(availability_topic, "online", retain=True)


async def mqtt(light: busylight_core.Light,
        broker: str, user:str, password:str, clientid:str,
        reconnect_delay: float,
        colour: Colour,
        on: bool,
        mqtt_tag: str,
    ) -> None:
    logging.info("aiomqtt.Client(hostname=%s, username=%s, password=password, identifier=%s)", broker, user, clientid)
    discovery = make_discovery(light.hardware, mqtt_tag)
    will = aiomqtt.Will(topic=discovery['availability_topic'], payload="offline", qos=2, retain=True) # https://github.com/empicano/aiomqtt/issues/28
    client = aiomqtt.Client(hostname=broker, username=user, password=password, identifier=clientid, will=will)
    outgoing = asyncio.Queue()
    first = True
    while True:
        try:
            logging.debug("starting main loop with %d tasks", len(asyncio.all_tasks()))
            async with client:
                await client.subscribe(discovery['command_topic'])
                if first:
                    await send_mqtt_configuration(client, discovery, mqtt_tag=mqtt_tag)
                    first = False
                    rgb = colour.get_rgb() if on else (0, 0, 0)
                    light.on(color=rgb)
                    logging.debug("initial colours %s read back as %s", rgb, light.color)
                    queue_current_state(on=on, colour=colour, outgoing=outgoing, discovery=discovery)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(listener(client=client, light=light, discovery=discovery, outgoing=outgoing, colour=colour, on=on))
                    tg.create_task(publisher(client=client, outgoing=outgoing))
            logging.error("after async")
        except aiomqtt.MqttError:
            logging.warning("Connection lost to %s; Reconnecting in %0.2f seconds ...", broker, reconnect_delay)
            await asyncio.sleep(reconnect_delay)


def make_mqtt_clientid(hostname: str, device: str) -> str:
    # consists of alphanumeric characters only, with a length restriction often between 1 and 23 characters
    new_device = re.sub(r'[^a-z0-9]', 'z', device.lower().translate(str.maketrans('-:.', 'dcf')))
    new_hostname = re.sub(r'[^a-z0-9]', 'y', hostname.lower())
    return f"{new_hostname}busylighthass"[0:23 - len(new_device)] + new_device


async def flash_light(light: busylight_core.Light, colour: Colour):
    colours = colour.get_rgb()
    for led in range(1, light.nleds + 1):
        light.on(color=colours, led=led)
        await asyncio.sleep(0.05)
    logging.debug("flashed %s colour=%s", light, colours)


def get_light(path: str) -> busylight_core.Light:
    try:
        light = busylight_core.Light.at_path(path)
    except busylight_core.NoLightsFoundError as ex:
        logging.fatal("No light found at path %s: %s", path, ex)
        sys.exit(2)
    logging.info("using light %s with %d LEDs", light, light.nleds)
    return light
    

async def main():
    options = get_options()
    numeric_level = getattr(logging, options.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        sys.exit('Invalid log level: %s' % options.loglevel)
    logging.basicConfig(level=numeric_level)
    
    device = options.path[0]
    colour = Colour(red=options.red, green=options.green, blue=options.blue, brightness=options.brightness)

    light = get_light(path=device)
    if options.initially_on:
        await flash_light(light=light, colour=colour)
    
    hostname = socket.gethostname()
    
    async with asyncio.TaskGroup() as tg: # https://aiomqtt.bo3hm.com/subscribing-to-a-topic.html
        if options.mqttbroker:
            tg.create_task(mqtt(
                light=light,
                broker=options.mqttbroker, user=options.mqttuser,
                password=get_password(options.mqttpassword, options.mqttpasswordfile),
                clientid=make_mqtt_clientid(hostname=hostname, device=device),
                reconnect_delay=options.reconnect,
                colour=colour,
                on=options.initially_on,
                mqtt_tag=options.mqtt_tag,
            ))


if __name__ == "__main__":
    asyncio.run(main())
