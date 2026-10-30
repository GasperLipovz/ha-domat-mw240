"""Local-only MW240 configuration; flows never open or probe bus endpoints."""
from __future__ import annotations

import re

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .config_schema import BUS_SCHEMA, DOMAIN, SETTINGS_SCHEMA, connection_key


def _connection_form_schema(values=None, *, options=False):
    values = values or {}
    fields = {vol.Required("connection_type", default=values.get("connection_type", "serial")): vol.In(["serial", "tcp"])}
    if not options:
        fields[vol.Required("name")] = str
    return vol.Schema(fields)


def _settings_form_schema(values=None, *, connection_type="serial"):
    """Only active transport settings appear; use frontend-serializable types."""
    values = values or {}
    inactive = {"host", "tcp_port"} if connection_type == "serial" else {"port", "baudrate", "parity", "stopbits"}
    fields = {}
    for key, validator in SETTINGS_SCHEMA.schema.items():
        name = key.schema
        if name in inactive | {"name", "connection_type"}:
            continue
        if name == "timeout" and connection_type == "tcp":
            key = vol.Optional(name, default=2)
            validator = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10))
        marker = vol.Required(name, default=values[name]) if name in values else key
        if name in {"port", "host"}:
            marker = vol.Required(name, default=values[name]) if name in values else vol.Required(name)
            validator = str
        elif name == "tcp_port":
            validator = vol.All(int, vol.Range(min=1, max=65535))
        fields[marker] = validator
    return vol.Schema(fields)


def _endpoint_conflict(data, configs):
    """Resolve serial aliases on an executor thread, never the HA event loop."""
    target = connection_key(data, resolve_serial=True)
    return any(connection_key(other, resolve_serial=True) == target for other in configs)


async def _async_conflict(hass, data, exclude_entry_id=None):
    configs = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry_id:
            continue
        effective = {**entry.data, **entry.options}
        if entry.unique_id == data["name"] or effective.get("name") == data["name"]:
            return "already_configured"
        configs.append(effective)
    if configs and await hass.async_add_executor_job(_endpoint_conflict, data, configs):
        return "tcp_endpoint_in_use" if data.get("connection_type", "serial") == "tcp" else "serial_port_in_use"
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
    """Create one config entry per named physical bus, serial or standard TCP."""

    VERSION = 1
    MINOR_VERSION = 2

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
                choice = _connection_form_schema()(user_input)
                identity_schema = vol.Schema({key: validator for key, validator in SETTINGS_SCHEMA.schema.items() if key.schema in {"name", "connection_type"}})
                self._settings = identity_schema(choice)
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_config"
            else:
                await self.async_set_unique_id(self._settings["name"])
                if any(entry.unique_id == self._settings["name"] or entry.data.get("name") == self._settings["name"] for entry in self.hass.config_entries.async_entries(DOMAIN)):
                    return self.async_abort(reason="already_configured")
                return await self._async_settings_step()
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=self.add_suggested_values_to_schema(_connection_form_schema(), user_input),
        )

    async def async_step_serial(self, user_input=None):
        return await self._async_settings_step(user_input)

    async def async_step_tcp(self, user_input=None):
        return await self._async_settings_step(user_input)

    async def _async_settings_step(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if {"name", "connection_type"} & user_input.keys():
                    raise vol.Invalid("Identity and transport are selected in the previous step")
                self._settings = SETTINGS_SCHEMA({**self._settings, **user_input})
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_config"
            else:
                conflict = await _async_conflict(self.hass, self._settings)
                if conflict:
                    errors["host" if self._settings["connection_type"] == "tcp" else "port"] = conflict
                else:
                    return await self.async_step_modules()
        mode = self._settings["connection_type"]
        return self.async_show_form(
            step_id=mode, errors=errors,
            data_schema=self.add_suggested_values_to_schema(_settings_form_schema(self._settings, connection_type=mode), user_input),
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
                choice = _connection_form_schema(current, options=True)(user_input)
            except vol.Invalid as err:
                field = str(err.path[0]) if err.path and err.path[0] != "name" else "base"
                errors[field] = "invalid_config"
            else:
                self._settings.update(choice)
                return await self._async_settings_step()
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=self.add_suggested_values_to_schema(_connection_form_schema(current, options=True), user_input),
        )

    async def async_step_serial(self, user_input=None):
        return await self._async_settings_step(user_input)

    async def async_step_tcp(self, user_input=None):
        return await self._async_settings_step(user_input)

    async def _async_settings_step(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if "name" in user_input:
                    raise vol.Invalid("Bus name is immutable")
                if {"name", "connection_type"} & user_input.keys():
                    raise vol.Invalid("Identity and transport are selected in the previous step")
                self._settings = SETTINGS_SCHEMA({**self._settings, **user_input})
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_config"
            else:
                conflict = await _async_conflict(self.hass, self._settings, self.config_entry.entry_id)
                if conflict:
                    field = "host" if self._settings["connection_type"] == "tcp" else "port"
                    errors[field if conflict != "already_configured" else "base"] = conflict
                else:
                    return await self.async_step_modules()
        mode = self._settings["connection_type"]
        return self.async_show_form(
            step_id=mode, errors=errors,
            data_schema=self.add_suggested_values_to_schema(_settings_form_schema(self._settings, connection_type=mode), user_input),
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
