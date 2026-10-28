"""Local-only MW240 configuration; flows never open or probe a serial bus."""
from __future__ import annotations

import os
import re

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .config_schema import BUS_SCHEMA, DOMAIN, SETTINGS_SCHEMA


def _settings_form_schema(values=None, *, options=False):
    """Use serializable UI types; submitted values still use shared validation."""
    fields = {}
    for key, validator in SETTINGS_SCHEMA.schema.items():
        if options and key.schema == "name":
            continue
        marker = vol.Required(key.schema, default=values[key.schema]) if options else key
        fields[marker] = str if key.schema in ("name", "port") else validator
    return vol.Schema(fields)


def _port_conflict(port: str, ports: list[str]) -> bool:
    """Resolve serial aliases on an executor thread, never the HA event loop."""
    target = os.path.realpath(port)
    return any(os.path.realpath(other) == target for other in ports)


async def _async_conflict(hass, data, exclude_entry_id=None):
    ports = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry_id:
            continue
        effective = {**entry.data, **entry.options}
        if entry.unique_id == data["name"] or effective.get("name") == data["name"]:
            return "already_configured"
        if port := effective.get("port"):
            ports.append(port)
    if ports and await hass.async_add_executor_job(_port_conflict, data["port"], ports):
        return "serial_port_in_use"
    return None


def _modules_schema(text: str = "") -> vol.Schema:
    return vol.Schema({
        vol.Required("modules", default=text): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
    })


def _parse_modules(text: str) -> list[dict]:
    devices = []
    for line in text.splitlines():
        if not line.strip():
            continue
        unit, name = line.split(":", 1)
        if not re.fullmatch(r"[1-9][0-9]{0,2}", unit.strip()) or not name.strip():
            raise ValueError("Use an integer address and a nonempty name")
        devices.append({"id": int(unit.strip()), "name": name.strip()})
    return devices


class DomatMW240ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one config entry per named serial bus."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DomatMW240OptionsFlow()

    async def async_step_import(self, user_input):
        """Import a complete YAML bus once; never update existing UI options."""
        try:
            data = BUS_SCHEMA(user_input)
        except vol.Invalid:
            return self.async_abort(reason="invalid_config")
        await self.async_set_unique_id(data["name"])
        if conflict := await _async_conflict(self.hass, data):
            return self.async_abort(reason=conflict)
        return self.async_create_entry(title=data["name"], data=data)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._settings = SETTINGS_SCHEMA(user_input)
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_config"
            else:
                await self.async_set_unique_id(self._settings["name"])
                conflict = await _async_conflict(self.hass, self._settings)
                if conflict == "already_configured":
                    return self.async_abort(reason=conflict)
                if conflict:
                    errors["port"] = conflict
                else:
                    return await self.async_step_modules()
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=self.add_suggested_values_to_schema(_settings_form_schema(), user_input),
        )

    async def async_step_modules(self, user_input=None):
        errors = {}
        text = ""
        if user_input is not None:
            text = user_input["modules"]
            try:
                data = BUS_SCHEMA({**self._settings, "devices": _parse_modules(text)})
            except (vol.Invalid, ValueError):
                errors["modules"] = "invalid_modules"
            else:
                if conflict := await _async_conflict(self.hass, data):
                    return self.async_abort(reason=conflict)
                return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(step_id="modules", data_schema=_modules_schema(text), errors=errors)


class DomatMW240OptionsFlow(config_entries.OptionsFlow):
    """Edit HA connection and permissions without reprogramming module settings."""

    async def async_step_init(self, user_input=None):
        current = BUS_SCHEMA({**self.config_entry.data, **self.config_entry.options})
        self._settings = {key: value for key, value in current.items() if key != "devices"}
        errors = {}
        if user_input is not None:
            try:
                if "name" in user_input:
                    raise vol.Invalid("Bus name is immutable")
                self._settings = SETTINGS_SCHEMA({**self._settings, **user_input})
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_config"
            else:
                conflict = await _async_conflict(self.hass, self._settings, self.config_entry.entry_id)
                if conflict:
                    errors["port" if conflict == "serial_port_in_use" else "base"] = conflict
                else:
                    return await self.async_step_modules()
        schema = _settings_form_schema(current, options=True)
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
        )

    async def async_step_modules(self, user_input=None):
        current = BUS_SCHEMA({**self.config_entry.data, **self.config_entry.options})
        text = "\n".join(f"{device['id']}: {device['name']}" for device in current["devices"])
        errors = {}
        if user_input is not None:
            text = user_input["modules"]
            try:
                data = BUS_SCHEMA({**self._settings, "devices": _parse_modules(text)})
            except (vol.Invalid, ValueError):
                errors["modules"] = "invalid_modules"
            else:
                if conflict := await _async_conflict(self.hass, data, self.config_entry.entry_id):
                    return self.async_abort(reason=conflict)
                data.pop("name")
                return self.async_create_entry(title="", data=data)
        return self.async_show_form(step_id="modules", data_schema=_modules_schema(text), errors=errors)
