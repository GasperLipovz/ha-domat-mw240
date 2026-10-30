"""Shared YAML, config-entry and options validation for an MW240 bus."""
from __future__ import annotations

import ipaddress
import os
import re

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


def _host(value):
    """Validate an IP literal or DNS name without DNS or literal rewriting."""
    if not isinstance(value, str) or not value or re.search(r"[\s/\[\]?#@]", value):
        raise vol.Invalid("Use an IP address or DNS hostname without a scheme or port")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        name = value.removesuffix(".")
        if (len(name) > 253 or re.fullmatch(r"[0-9.]+", name)
                or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                           for label in name.split("."))):
            raise vol.Invalid("Use an IP address or DNS hostname without a scheme or port") from None
    return value


def _tcp_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise vol.Invalid("TCP port must be an integer 1..65535")
    return value


class _ConnectionSchema(vol.Schema):
    """Require and default only fields belonging to the selected transport."""

    def __call__(self, data):
        if not isinstance(data, dict):
            raise vol.Invalid("Bus settings must be a mapping")
        mode = data.get("connection_type", "serial")
        fields = {}
        for key, validator in self.schema.items():
            name = key.schema
            if name in {"port", "baudrate", "parity", "stopbits"} and mode == "tcp":
                fields[vol.Optional(name)] = object
            elif name in {"host", "tcp_port"} and mode == "serial":
                fields[vol.Optional(name)] = object
            elif name == "host" and mode == "tcp":
                fields[vol.Required(name)] = validator
            elif name == "port" and mode == "serial":
                fields[vol.Required(name)] = validator
            elif name == "timeout" and mode == "tcp":
                fields[vol.Optional(name, default=2)] = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10))
            else:
                fields[key] = validator
        return vol.Schema(fields)(data)


SETTINGS_SCHEMA = _ConnectionSchema({
    vol.Optional("connection_type", default="serial"): vol.In(["serial", "tcp"]),
    vol.Optional("host"): _host,
    vol.Optional("tcp_port", default=502): _tcp_port,
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
BUS_SCHEMA = _ConnectionSchema({
    **SETTINGS_SCHEMA.schema,
    vol.Required("devices"): vol.All(cv.ensure_list, [DEVICE_SCHEMA], vol.Length(min=1, max=247), _unique_devices),
})


def _tcp_connection_key(host, port):
    """Canonicalize a validated endpoint locally, without DNS resolution."""
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = host.removesuffix(".").lower()
    return ("tcp", host, port)


def connection_key(config, resolve_serial=False):
    """Return a local-only ownership key; serial alias resolution may touch disk."""
    if config.get("connection_type", "serial") == "tcp":
        return _tcp_connection_key(_host(config["host"]), _tcp_port(config.get("tcp_port", 502)))
    path = config["port"]
    return os.path.realpath(path) if resolve_serial else os.path.normpath(path)


def modbus_connection_key(hub, resolve_serial=False):
    """Use built-in Modbus field validators without mutating its configuration."""
    if not isinstance(hub, dict):
        return None
    if hub.get("type") == "serial" and hub.get("port"):
        return connection_key({"port": cv.string(hub["port"])}, resolve_serial)
    if hub.get("type") in {"tcp", "rtuovertcp"} and "host" in hub:
        return _tcp_connection_key(cv.string(hub["host"]), cv.port(hub.get("port", 502)))
    return None


def _unique(config):
    """Validate lexical ownership only; resolve symlinks off the HA loop at setup."""
    names, endpoints = set(), set()
    for bus in config.get(DOMAIN, []):
        endpoint = connection_key(bus)
        if bus["name"] in names or endpoint in endpoints:
            raise vol.Invalid("Bus names and connection endpoints must be unique")
        names.add(bus["name"])
        endpoints.add(endpoint)
    for hub in config.get("modbus", []):
        endpoint = modbus_connection_key(hub)
        if endpoint in endpoints:
            raise vol.Invalid("Connection endpoint is already assigned to Home Assistant Modbus")
    return config


CONFIG_SCHEMA = vol.All(vol.Schema({
    vol.Optional(DOMAIN): vol.All(cv.ensure_list, [BUS_SCHEMA]),
}, extra=vol.ALLOW_EXTRA), _unique)
