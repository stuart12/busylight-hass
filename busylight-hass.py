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
import errno
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
    parser.add_argument('-o', "--off", dest='initially_on', action="store_false", help="start with light off")
    parser.add_argument('--on', dest='initially_on', action="store_true", help="start with light on (default)")
    parser.add_argument("-v", "--verbose", dest='loglevel', action="store_const", const='debug', help="debug loglevel")
    parser.add_argument("-l", "--loglevel", metavar="LEVEL", help="set logging level")
    parser.add_argument("--reconnect", type=float, metavar="SECONDS", default=60, help="delay reconnection attempts")
    parser.add_argument("--red", type=int, metavar="INTEGER", default=255, help="red level")
    parser.add_argument("--green", type=int, metavar="INTEGER", default=0, help="green level")
    parser.add_argument("--blue", type=int, metavar="INTEGER", default=0, help="blue level")
    parser.add_argument("--mqttbroker", help="mqtt broker")
    parser.add_argument("--id", default="busylight-hass", help="tag for mqtt broker and topics")
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


def queue_current_state(light: busylight_core.Light, outgoing: asyncio.Queue) -> None:
    outgoing.put_nowait("ON" if light.is_lit else "OFF")


async def listener(client: aiomqtt.Client, light: busylight_core.Light, discovery: dict, outgoing: asyncio.Queue, colour: tuple[int, int, int]) -> None:
    async for message in client.messages:
        logging.debug("got a message from client.messages: %s %s to set %s", message.topic, message.payload, colour)
        topic = str(message.topic)
        payload = message.payload.decode().lower()
        if topic == discovery['command_topic']:
            if payload == "on":
                light.on(colour)
            elif payload == "off":
                light.off()
            else:
                logging.info("unexpected message from clients: %s %s", message.topic, payload)
            queue_current_state(light, outgoing)
        else:
            logging.info("message on unexpected topic from client: %s %s", message.topic, message.payload)
    logging.error("end of listener")


async def publisher(client: aiomqtt.Client, topic:str, outgoing: asyncio.Queue) -> None:
    while True:
        status = await outgoing.get()
        logging.debug("retrieved a status %s to send in %s, %d remaining", status, topic, outgoing.qsize())
        await client.publish(topic=topic, payload=status, retain=True)
        outgoing.task_done()
    logging.error("end of publisher")


def make_topic(hardware: busylight_core.Hardware, topic: str) -> str:
    serial = re.sub(r'[ /+#]', '-', hardware.serial_number)
    return "/".join(["busylight_hass", "%#0x" % hardware.vendor_id, "%#0x" % hardware.product_id, serial, topic])


def make_discovery(hardware: busylight_core.Hardware) -> dict:
    identifier = "%0xdx%0xdx%s" % (hardware.vendor_id, hardware.product_id, re.sub(r'[^a-z0-9]', 'y', hardware.serial_number.lower()))
    discovery_topic = f"homeassistant/light/busylight_hass/{identifier}/config"

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
        "availability_topic": make_topic(hardware, 'availability'),
        "command_topic": make_topic(hardware, 'command'),
        "state_topic": make_topic(hardware, 'state'),
        "qos": 2,
    }
    return payload


async def send_mqtt_configuration(client: aiomqtt.Client, hardware: busylight_core.Hardware, payload: dict) -> None:
    # https://stevessmarthomeguide.com/adding-an-mqtt-device-to-home-assistant/
    # https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
    discovery_topic = f"homeassistant/light/busylight_hass/{payload['unique_id']}/config"
    availability_topic = payload['availability_topic']
    logging.info("publishing homeassistance discovery on %s for %s", discovery_topic, availability_topic)
    await client.publish(discovery_topic, json.dumps(payload), retain=True)
    await client.publish(availability_topic, "online", retain=True)


async def mqtt(light: busylight_core.Light,
        broker: str, user:str, password:str, clientid:str,
        reconnect_delay: float,
        colour: tuple[int, int, int],
        initially_on: bool,
    ) -> None:
    logging.info("aiomqtt.Client(hostname=%s, username=%s, password=password, identifier=%s)", broker, user, clientid)
    discovery = make_discovery(light.hardware)
    will = aiomqtt.Will(topic=discovery['availability_topic'], payload="offline", qos=2, retain=True) # https://github.com/empicano/aiomqtt/issues/28
    client = aiomqtt.Client(hostname=broker, username=user, password=password, identifier=clientid, will=will)
    outgoing = asyncio.Queue()
    first = True
    while True:
        try: # https://aiomqtt.bo3hm.com/subscribing-to-a-topic.html
            async with client:
                await client.subscribe(discovery['command_topic'])
                await send_mqtt_configuration(client, light.hardware, discovery)
                if first:
                    first = False
                    if initially_on:
                        light.on(color=colour)
                    else:
                        light.off()
                    queue_current_state(light, outgoing)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(listener(client=client, light=light, discovery=discovery, outgoing=outgoing, colour=colour))
                    tg.create_task(publisher(client, discovery['state_topic'], outgoing))
            logging.error("after async")
        except aiomqtt.MqttError:
            logging.warning("Connection lost to %s; Reconnecting in %f seconds ...", broker, reconnect_delay)
            await asyncio.sleep(reconnect_delay)


def make_mqtt_clientid(hostname: str, device: str) -> str:
    # consists of alphanumeric characters only, with a length restriction often between 1 and 23 characters
    new_device = re.sub(r'[^a-z0-9]', 'z', device.lower().translate(str.maketrans('-:.', 'dcf')))
    new_hostname = re.sub(r'[^a-z0-9]', 'y', hostname.lower())
    return f"{new_hostname}busylighthass"[0:23 - len(new_device)] + new_device


async def flash_light(light: busylight_core.Light, colour: tuple[int, int, int]):
    for led in [1, 3, 5, 7]:
        light.on(color=colour, led=led)
        await asyncio.sleep(0.05)
    logging.debug("flashed %s colour=%s", light, colour)


def get_light(path: str) -> busylight_core.Light:
    as_bytes = str.encode(path)
    # TODO: how can I open a USB light with the given path without having to enumerate all HID devices?
    hardware = busylight_core.Light.available_hardware()
    logging.debug("busylight_core.available_hardware %d %s", len(hardware), hardware)
    for subclass, devices in hardware.items():
        for device in devices:
            if device.path == as_bytes:
                if not device.serial_number:
                    logging.warn("no serial number in %s manufacturer_string=%s", device, device.manufacturer_string)
                light = subclass(device)
                logging.info("opened light %s serial=%s release_number=%s", light, light.hardware.serial_number, light.hardware.release_number)
                return light

    logging.fatal("path %s not found in list of %d hardware", as_bytes, len(hardware))
    sys.exit(1)
    return None
    

async def main():
    options = get_options()
    numeric_level = getattr(logging, options.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        sys.exit('Invalid log level: %s' % options.loglevel)
    logging.basicConfig(level=numeric_level)
    
    device = options.path[0]
    colour = (options.red, options.green, options.blue)

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
                initially_on=options.initially_on,
            ))


if __name__ == "__main__":
    asyncio.run(main())
