"""Shared YAML, config-entry and options validation for an MW240 bus."""
from __future__ import annotations

import os

from homeassistant.helpers import config_validation as cv
import voluptuous as vol

DOMAIN = "domat_mw240"
BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]


def _unit_id(value):
    if type(value) is not int or not 1 <= value <= 247:
        raise vol.Invalid("Device ID must be an integer 1..247 (never broadcast)")
    return value


DEVICE_SCHEMA = vol.Schema({
    vol.Required("id"): _unit_id,
    vol.Required("name"): cv.string,
})
def _unique_devices(devices):
    if len({device["id"] for device in devices}) != len(devices):
        raise vol.Invalid("Module addresses must be unique within each bus")
    return devices


SETTINGS_SCHEMA = vol.Schema({
    vol.Required("name"): vol.All(cv.slug, vol.Length(min=1)),
    vol.Required("port"): vol.All(cv.string, vol.Match(r".*\S.*")),
    vol.Optional("baudrate", default=19200): vol.In(BAUDRATES),
    vol.Optional("parity", default="N"): vol.In(["N", "E", "O"]),
    vol.Optional("stopbits", default=1): vol.In([1, 2]),
    vol.Optional("poll_interval", default=2): vol.All(vol.Coerce(float), vol.Range(min=1, max=60)),
    vol.Optional("timeout", default=0.4): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2)),
    vol.Optional("command_ttl", default=5): vol.All(vol.Coerce(float), vol.Range(min=1, max=15)),
    vol.Optional("allow_commands", default=False): cv.boolean,
    vol.Optional("allow_timing_changes", default=False): cv.boolean,
    vol.Optional("allow_eeprom_save", default=False): cv.boolean,
})
BUS_SCHEMA = vol.Schema({
    **SETTINGS_SCHEMA.schema,
    vol.Required("devices"): vol.All(cv.ensure_list, [DEVICE_SCHEMA], vol.Length(min=1, max=247), _unique_devices),
})


def _unique(config):
    """Validate lexical ownership only; resolve symlinks off the HA loop at setup."""
    names, ports = set(), set()
    for bus in config.get(DOMAIN, []):
        port = os.path.normpath(bus["port"])
        if bus["name"] in names or port in ports:
            raise vol.Invalid("Bus names and serial ports must be unique")
        names.add(bus["name"])
        ports.add(port)
    for hub in config.get("modbus", []):
        if isinstance(hub, dict) and hub.get("type") == "serial" and os.path.normpath(hub.get("port", "")) in ports:
            raise vol.Invalid("Serial port is already assigned to Home Assistant Modbus")
    return config


CONFIG_SCHEMA = vol.All(vol.Schema({
    vol.Optional(DOMAIN): vol.All(cv.ensure_list, [BUS_SCHEMA]),
}, extra=vol.ALLOW_EXTRA), _unique)
