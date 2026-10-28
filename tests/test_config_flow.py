"""Offline config-flow tests using real Home Assistant, never HA stubs."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

import homeassistant  # Install HA's voluptuous compatibility layer first.

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
DOMAIN = "domat_mw240"


def bus(**changes):
    return {"name": "office", "port": "/dev/example", "devices": [{"id": 23, "name": "Window"}], **changes}


class SchemaTests(unittest.TestCase):
    def schema(self):
        self.assertTrue((ROOT / "custom_components/domat_mw240/config_schema.py").exists(), "Shared schema is missing")
        return importlib.import_module("custom_components.domat_mw240.config_schema")

    def test_shared_bus_schema_preserves_defaults_with_all_write_permissions_off(self):
        schema = self.schema()
        checked = schema.BUS_SCHEMA(bus())
        self.assertEqual(checked, bus(baudrate=19200, parity="N", stopbits=1,
                                     poll_interval=2, timeout=0.4, command_ttl=5,
                                     allow_commands=False, allow_timing_changes=False,
                                     allow_eeprom_save=False))
        self.assertEqual(schema.CONFIG_SCHEMA({DOMAIN: [bus()]})[DOMAIN], [checked])

    def test_duplicate_module_addresses_rejected_in_bus_schema_itself(self):
        import voluptuous as vol
        with self.assertRaises(vol.Invalid):
            self.schema().BUS_SCHEMA(bus(devices=[{"id": 23, "name": "One"}, {"id": 23, "name": "Two"}]))

    def test_yaml_rejects_duplicate_bus_names_ports_and_builtin_modbus_ownership(self):
        import voluptuous as vol
        schema = self.schema()
        for config in (
            {DOMAIN: [bus(), bus(port="/dev/other")]},
            {DOMAIN: [bus(), bus(name="other", port="/dev/../dev/example")]},
            {DOMAIN: [bus()], "modbus": [{"type": "serial", "port": "/dev/../dev/example"}]},
        ):
            with self.subTest(config=config), self.assertRaises(vol.Invalid):
                schema.CONFIG_SCHEMA(config)
        unchanged = {DOMAIN: [bus()], "modbus": [{"type": "tcp", "host": "example"}], "other": {}}
        self.assertEqual(schema.CONFIG_SCHEMA(unchanged)["other"], {})

    def test_schema_keeps_strict_module_id_bounds_and_bus_name_slug(self):
        import voluptuous as vol
        for unit in (0, 248, -1, True, 23.0, "23"):
            with self.subTest(unit=unit), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(bus(devices=[{"id": unit, "name": "Window"}]))
        for name in ("Office bus", "UPPER", ""):
            with self.subTest(name=name), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(bus(name=name))
        for devices in ([], [{"id": 1, "name": "Window"}] * 248):
            with self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(bus(devices=devices))


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os
        import tempfile
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries
        self.directory = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.addAsyncCleanup(self.hass.async_stop)
        self.addCleanup(self.directory.cleanup)

    def flow(self, source="user"):
        self.assertTrue((ROOT / "custom_components/domat_mw240/config_flow.py").exists(), "UI flow is missing")
        module = importlib.import_module("custom_components.domat_mw240.config_flow")
        flow = module.DomatMW240ConfigFlow()
        flow.hass = self.hass
        flow.handler = DOMAIN
        flow.flow_id = "test_flow"
        flow.context = {"source": source}
        return flow

    def entry(self, data=None, options=None, unique_id="office"):
        from homeassistant.config_entries import ConfigEntry
        entry = ConfigEntry(data=data or bus(), options=options or {}, domain=DOMAIN,
                            unique_id=unique_id, title="Office", source="user", version=1,
                            minor_version=1, discovery_keys={}, subentries_data=None)
        # Register offline, without ConfigEntries.async_add (which starts hardware setup).
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    async def test_existing_bus_name_aborts_without_overwriting_permissions(self):
        entry = self.entry(options={"allow_commands": True, "port": "/dev/new"})
        original = dict(entry.options)
        result = await self.flow().async_step_user({"name": "office", "port": "/dev/unused", "allow_commands": False})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "already_configured")
        self.assertEqual(dict(entry.options), original)

    async def test_duplicate_serial_alias_checks_effective_options_off_the_event_loop(self):
        import os
        import threading
        from unittest.mock import patch
        original = os.path.realpath
        path = Path(self.directory.name) / "serial"
        path.touch()
        alias = Path(self.directory.name) / "alias"
        alias.symlink_to(path)
        self.entry(data=bus(port="/dev/old"), options={"port": str(path)})
        loop_thread = threading.get_ident()
        threads = []
        def realpath(value):
            threads.append(threading.get_ident())
            return original(value)
        with patch("os.path.realpath", side_effect=realpath):
            result = await self.flow().async_step_user({"name": "second", "port": str(alias)})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"port": "serial_port_in_use"})
        self.assertTrue(threads)
        self.assertNotIn(loop_thread, threads)
        valid = await self.flow().async_step_user({"name": "second", "port": "/dev/old"})
        self.assertEqual(valid["step_id"], "modules", "Old data port is released when options override it")

    async def test_port_claimed_between_user_and_modules_is_rechecked(self):
        flow = self.flow()
        await flow.async_step_user({"name": "office", "port": "/dev/example"})
        self.entry(data=bus(name="other"), unique_id="other")
        result = await flow.async_step_modules({"modules": "23: Window"})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "serial_port_in_use")

    async def test_yaml_import_creates_full_bus_without_probing_or_enabling_writes(self):
        from unittest.mock import patch
        flow = self.flow("import")
        self.assertTrue(hasattr(flow, "async_step_import"), "YAML entry import is missing")
        with patch("serial.Serial", side_effect=AssertionError("Import must never access serial")):
            result = await flow.async_step_import(bus())
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(flow.context["unique_id"], "office")
        self.assertFalse(result["data"]["allow_eeprom_save"])
        self.assertFalse(result["data"]["allow_timing_changes"])
        self.assertFalse(result["data"]["allow_commands"])

    async def test_yaml_reimport_aborts_without_overwriting_ui_settings_or_creating_an_entry(self):
        entry = self.entry(options={"port": "/dev/changed", "allow_commands": True, "allow_eeprom_save": True})
        original_data, original_options = dict(entry.data), dict(entry.options)
        for _ in range(2):
            flow = self.flow("import")
            self.assertTrue(hasattr(flow, "async_step_import"), "YAML entry import is missing")
            result = await flow.async_step_import(bus())
            self.assertEqual(result["type"], "abort")
            self.assertEqual(result["reason"], "already_configured")
        self.assertEqual(dict(entry.data), original_data)
        self.assertEqual(dict(entry.options), original_options)
        self.assertEqual(len(self.hass.config_entries.async_entries(DOMAIN)), 1)

    async def test_yaml_import_rejects_duplicate_port_and_invalid_configuration(self):
        self.entry()
        for data, reason in ((bus(name="another"), "serial_port_in_use"), (bus(devices=[{"id": 0, "name": "Invalid"}]), "invalid_config")):
            with self.subTest(data=data):
                flow = self.flow("import")
                self.assertTrue(hasattr(flow, "async_step_import"), "YAML entry import is missing")
                result = await flow.async_step_import(data)
                self.assertEqual(result["type"], "abort")
                self.assertEqual(result["reason"], reason)

    def options_flow(self, entry):
        flow_class = type(self.flow())
        from homeassistant.config_entries import OptionsFlow
        flow = flow_class.async_get_options_flow(entry)
        self.assertIsInstance(flow, OptionsFlow)
        flow.hass = self.hass
        flow.handler = entry.entry_id
        flow.flow_id = "test_options"
        flow.context = {"source": "init"}
        return flow

    async def test_options_edit_all_settings_and_modules_but_preserve_bus_identity(self):
        from unittest.mock import patch
        schema = importlib.import_module("custom_components.domat_mw240.config_schema")
        entry = self.entry(data=schema.BUS_SCHEMA(bus()), options={"allow_commands": True, "timeout": 0.8})
        flow = self.options_flow(entry)
        with patch("serial.Serial", side_effect=AssertionError("Options must never access serial")):
            initial = await flow.async_step_init()
            self.assertEqual(initial["step_id"], "init")
            defaults = initial["data_schema"]({})
            self.assertNotIn("name", defaults)
            self.assertTrue(defaults["allow_commands"])
            self.assertEqual(defaults["timeout"], 0.8)
            modules = await flow.async_step_init({"port": "/dev/updated", "baudrate": 9600, "parity": "E", "stopbits": 2, "poll_interval": 4, "command_ttl": 6, "allow_commands": False, "allow_timing_changes": True, "allow_eeprom_save": True})
            self.assertEqual(modules["step_id"], "modules")
            self.assertEqual(next(iter(modules["data_schema"].schema)).default(), "23: Window")
            result = await flow.async_step_modules({"modules": "24: Renamed\n25: New blind"})
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("name", result["data"])
        effective = schema.BUS_SCHEMA({**entry.data, **result["data"]})
        self.assertEqual(effective, schema.BUS_SCHEMA(bus(port="/dev/updated", baudrate=9600, parity="E", stopbits=2, poll_interval=4, timeout=0.8, command_ttl=6, allow_commands=False, allow_timing_changes=True, allow_eeprom_save=True, devices=[{"id": 24, "name": "Renamed"}, {"id": 25, "name": "New blind"}])))
        self.assertEqual(entry.unique_id, "office")
        self.assertEqual(entry.data["name"], "office")
        self.assertEqual(entry.options, {"allow_commands": True, "timeout": 0.8}, "Manager, not the flow, persists options")

    async def test_options_cannot_change_immutable_bus_name_or_accept_bad_settings(self):
        entry = self.entry()
        for changes, errors in (({"name": "renamed"}, {"base": "invalid_config"}), ({"timeout": 99}, {"timeout": "invalid_config"})):
            with self.subTest(changes=changes):
                result = await self.options_flow(entry).async_step_init(changes)
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["step_id"], "init")
                self.assertEqual(result["errors"], errors)
        self.assertEqual(entry.data["name"], "office")

    async def test_options_modules_errors_are_correctable_and_keep_existing_options(self):
        entry = self.entry(options={"allow_commands": True})
        flow = self.options_flow(entry)
        await flow.async_step_init({"baudrate": 9600})
        result = await flow.async_step_modules({"modules": "23: Duplicate\n23: Also duplicate"})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"modules": "invalid_modules"})
        result = await flow.async_step_modules({"modules": "23: Fixed"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["baudrate"], 9600)
        self.assertTrue(result["data"]["allow_commands"])

    async def test_options_reject_another_entry_port_but_allow_own_port(self):
        entry = self.entry()
        self.entry(data=bus(name="other", port="/dev/other"), unique_id="other")
        flow = self.options_flow(entry)
        result = await flow.async_step_init({"port": "/dev/../dev/other"})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"port": "serial_port_in_use"})
        await flow.async_step_init({"port": "/dev/example"})
        result = await flow.async_step_modules({"modules": "23: Window"})
        self.assertEqual(result["type"], "create_entry")
        # A conflicting entry appearing after the first step must still prevent saving.
        flow = self.options_flow(entry)
        await flow.async_step_init({"port": "/dev/new"})
        self.entry(data=bus(name="third", port="/dev/new"), unique_id="third")
        result = await flow.async_step_modules({"modules": "23: Window"})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "serial_port_in_use")

    async def test_forms_serialize_for_real_home_assistant_frontend(self):
        from probatio import to_field_list
        from homeassistant.helpers import config_validation as cv
        forms = [await self.flow().async_step_user(), await self.options_flow(self.entry()).async_step_init()]
        for form in forms:
            fields = to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
            by_name = {field["name"]: field for field in fields}
            self.assertEqual(by_name["port"]["type"], "string")
            self.assertEqual(by_name["allow_eeprom_save"]["type"], "boolean")
            self.assertEqual(by_name["baudrate"]["type"], "select")

    async def test_user_flow_creates_full_validated_bus_with_legacy_name_identity_without_serial_io(self):
        from unittest.mock import patch
        from homeassistant.data_entry_flow import FlowResultType
        flow = self.flow()
        with patch("serial.Serial", side_effect=AssertionError("Flow must never access serial")):
            initial = await flow.async_step_user()
            self.assertEqual(initial["type"], FlowResultType.FORM)
            self.assertEqual(initial["step_id"], "user")
            fields = {key.schema for key in initial["data_schema"].schema}
            self.assertEqual(fields, {"name", "port", "baudrate", "parity", "stopbits", "poll_interval", "timeout", "command_ttl", "allow_commands", "allow_timing_changes", "allow_eeprom_save"})
            modules = await flow.async_step_user({"name": "office", "port": "/dev/example"})
            self.assertEqual(modules["step_id"], "modules")
            selector = modules["data_schema"].schema["modules"]
            self.assertTrue(selector.config["multiline"])
            result = await flow.async_step_modules({"modules": "23: Window\n24: Another: west"})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        self.assertEqual(result["title"], "office")
        self.assertEqual(flow.context["unique_id"], "office")
        schema = importlib.import_module("custom_components.domat_mw240.config_schema")
        self.assertEqual(result["data"], schema.BUS_SCHEMA(bus(devices=[{"id": 23, "name": "Window"}, {"id": 24, "name": "Another: west"}])))

    async def test_module_errors_redisplay_form_and_preserve_text_without_creating_entry(self):
        from homeassistant.data_entry_flow import FlowResultType
        for text in ("", "23", "0: Broadcast", "248: Invalid", "-1: Invalid", "23.0: Invalid", "True: Invalid", "+23: Invalid", "01: Invalid", "23:", "23: One\n23: Two", "23: One\nbroken"):
            with self.subTest(text=text):
                flow = self.flow()
                await flow.async_step_user({"name": "office", "port": "/dev/example"})
                result = await flow.async_step_modules({"modules": text})
                self.assertEqual(result["type"], FlowResultType.FORM)
                self.assertEqual(result["errors"], {"modules": "invalid_modules"})
                self.assertEqual(result["data_schema"].schema[next(iter(result["data_schema"].schema))].config["multiline"], True)
                self.assertEqual(next(iter(result["data_schema"].schema)).default(), text)
        flow = self.flow()
        await flow.async_step_user({"name": "office", "port": "/dev/example"})
        valid = await flow.async_step_modules({"modules": "\n".join(f"{unit}: Module {unit}" for unit in range(1, 248))})
        self.assertEqual(len(valid["data"]["devices"]), 247)

    async def test_invalid_bus_settings_are_correctable_form_errors(self):
        from homeassistant.data_entry_flow import FlowResultType
        for changes, field in (({"name": "Office Bus"}, "name"), ({"name": ""}, "name"), ({"port": " "}, "port"), ({"baudrate": 12345}, "baudrate"), ({"parity": "X"}, "parity"), ({"poll_interval": 0}, "poll_interval"), ({"timeout": 3}, "timeout"), ({"command_ttl": 16}, "command_ttl")):
            with self.subTest(changes=changes):
                result = await self.flow().async_step_user({"name": "office", "port": "/dev/example", **changes})
                self.assertEqual(result["type"], FlowResultType.FORM)
                self.assertEqual(result["errors"], {field: "invalid_config"})


class TranslationTests(unittest.TestCase):
    def test_english_translations_cover_every_flow_field_error_abort_and_safety_warning(self):
        import json
        documents = []
        for relative in ("strings.json", "translations/en.json"):
            path = ROOT / "custom_components/domat_mw240" / relative
            self.assertTrue(path.exists(), f"Missing {relative}")
            documents.append(json.loads(path.read_text()))
        self.assertEqual(documents[0], documents[1])
        schema = importlib.import_module("custom_components.domat_mw240.config_schema")
        fields = {key.schema for key in schema.SETTINGS_SCHEMA.schema}
        for section, step, wanted in (("config", "user", fields), ("options", "init", fields - {"name"})):
            flow = documents[0][section]
            self.assertEqual(set(flow["step"][step]["data"]), wanted)
            self.assertEqual(set(flow["step"]["modules"]["data"]), {"modules"})
            self.assertTrue({"invalid_config", "invalid_modules", "serial_port_in_use", "already_configured"} <= set(flow["error"]))
            self.assertTrue({"already_configured", "serial_port_in_use", "invalid_config", "already_in_progress"} <= set(flow["abort"]))
            details = flow["step"][step]
            self.assertIn("do not reprogram", details["description"])
            warning = details["data_description"]["allow_eeprom_save"]
            self.assertIn("all RAM configuration", warning)
            self.assertIn("tested firmware 17", warning)
        self.assertIn("cannot be changed", documents[0]["config"]["step"]["user"]["data_description"]["name"])


if __name__ == "__main__":
    unittest.main()
