"""Offline standard Modbus TCP configuration and conditional UI tests."""
from __future__ import annotations

from copy import deepcopy
import importlib
import unittest

import homeassistant  # Install Home Assistant's voluptuous compatibility layer.

from tests import test_config_flow as fixtures
from tests.test_config_flow import bus


def tcp_bus(**changes):
    return {"name": "office", "connection_type": "tcp", "host": "R035.example", "devices": [{"id": 23, "name": "Window"}], **changes}


class TCPConfigSchemaTests(unittest.TestCase):
    def schema(self):
        return importlib.import_module("custom_components.domat_mw240.config_schema")

    def test_tcp_defaults_and_legacy_serial_defaults(self):
        schema = self.schema()
        tcp = schema.BUS_SCHEMA(tcp_bus())
        self.assertEqual(tcp["host"], "R035.example")
        self.assertEqual(tcp["tcp_port"], 502)
        self.assertEqual(tcp["timeout"], 2)
        self.assertNotIn("port", tcp)
        for permission in ("allow_commands", "allow_timing_changes", "allow_eeprom_save"):
            self.assertFalse(tcp[permission])
        serial = schema.BUS_SCHEMA(bus())
        self.assertEqual(serial["connection_type"], "serial")
        self.assertEqual(serial["timeout"], 0.4)
        self.assertEqual(serial["baudrate"], 19200)

    def test_active_tcp_endpoint_validation_preserves_valid_literals(self):
        import voluptuous as vol
        for host in ("192.0.2.8", "2001:0db8::01", "R035.example.", "r035", "xn--r035-9za.example"):
            with self.subTest(host=host):
                self.assertEqual(self.schema().BUS_SCHEMA(tcp_bus(host=host))["host"], host)
        for host in ("", " ", " host", "host ", "tcp://r035", "http://host", "host:502", "host/path", "host?x=1", "host#x", "[::1]", "bad_host", "-bad.example", "bad-.example", "one..two", "999.1.1.1", "fe80::1%eth0/path", "fe80::1%eth0?query", "fe80::1%bad scope", True, 123):
            with self.subTest(host=host), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(tcp_bus(host=host))
        for port in (0, 65536, True, False, 502.0, "502", None):
            with self.subTest(port=port), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(tcp_bus(tcp_port=port))
        for port in (1, 502, 65535):
            self.assertEqual(self.schema().BUS_SCHEMA(tcp_bus(tcp_port=port))["tcp_port"], port)
        for timeout in (0.09, 10.01, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(tcp_bus(timeout=timeout))
        self.assertEqual(self.schema().BUS_SCHEMA(tcp_bus(timeout=10))["timeout"], 10)

    def test_only_active_endpoint_is_required_and_inactive_settings_are_ignored(self):
        import voluptuous as vol
        tcp = self.schema().BUS_SCHEMA(tcp_bus(port="/dev/old", baudrate=12345, parity="obsolete"))
        self.assertEqual(tcp["host"], "R035.example")
        serial = self.schema().BUS_SCHEMA(bus(host="stale://host", tcp_port=True))
        self.assertEqual(serial["port"], "/dev/example")
        for data in (tcp_bus(host=None), {k: v for k, v in tcp_bus().items() if k != "host"}, {k: v for k, v in bus().items() if k != "port"}, bus(connection_type="rtuovertcp"), bus(connection_type=True)):
            with self.subTest(data=data), self.assertRaises(vol.Invalid):
                self.schema().BUS_SCHEMA(data)

    def test_connection_keys_normalize_without_dns_and_preserve_serial_string_keys(self):
        from unittest.mock import patch
        schema = self.schema()
        self.assertTrue(hasattr(schema, "connection_key"), "Shared transport ownership key is missing")
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS ownership lookup")):
            self.assertEqual(schema.connection_key(tcp_bus()), ("tcp", "r035.example", 502))
            self.assertEqual(schema.connection_key(tcp_bus(host="R035.EXAMPLE.")), ("tcp", "r035.example", 502))
            self.assertEqual(schema.connection_key(tcp_bus(host="2001:0db8:0::1", tcp_port=1502)), ("tcp", "2001:db8::1", 1502))
            self.assertEqual(schema.connection_key(bus(port="/dev/../dev/example")), "/dev/example")
            with patch("os.path.realpath", return_value="/dev/resolved") as resolve:
                self.assertEqual(schema.connection_key(bus(), resolve_serial=True), "/dev/resolved")
                resolve.assert_called_once_with("/dev/example")

    def test_foreign_quoted_port_is_accepted_without_changing_modbus_config(self):
        from homeassistant.helpers import config_validation as cv
        for mode in ("tcp", "rtuovertcp"):
            for domat in (None, [bus()]):
                with self.subTest(mode=mode, domat=domat):
                    hub = {"name": "unrelated", "type": mode, "host": "192.0.2.9", "port": "502"}
                    self.assertEqual(cv.port(hub["port"]), 502)
                    config = {"modbus": [hub]}
                    if domat is not None:
                        config["domat_mw240"] = domat
                    original = deepcopy(config)
                    result = self.schema().CONFIG_SCHEMA(config)
                    self.assertEqual(config, original)
                    self.assertEqual(result["modbus"], original["modbus"])

    def test_foreign_quoted_port_still_rejects_conflicting_endpoint(self):
        import voluptuous as vol
        for mode in ("tcp", "rtuovertcp"):
            with self.subTest(mode=mode):
                config = {"domat_mw240": [tcp_bus()], "modbus": [
                    {"name": "existing", "type": mode, "host": "r035.EXAMPLE.", "port": "502"},
                ]}
                original = deepcopy(config)
                with self.assertRaisesRegex(vol.Invalid, "already assigned to Home Assistant Modbus"):
                    self.schema().CONFIG_SCHEMA(config)
                self.assertEqual(config, original)

    def test_foreign_hosts_use_builtin_string_validation_not_native_host_rules(self):
        from homeassistant.helpers import config_validation as cv
        for host in ("gateway_local", 123, 0, ""):
            with self.subTest(host=host):
                hub = {"name": "unrelated", "type": "tcp", "host": host, "port": 502}
                self.assertIsInstance(cv.string(host), str)
                config = {"domat_mw240": [bus()], "modbus": [hub]}
                original = deepcopy(config)
                result = self.schema().CONFIG_SCHEMA(config)
                self.assertEqual(config, original)
                self.assertEqual(result["modbus"], original["modbus"])

    def test_yaml_tcp_ownership_includes_builtin_tcp_and_rtu_over_tcp(self):
        import voluptuous as vol
        schema = self.schema()
        for config in (
            {"domat_mw240": [tcp_bus(), tcp_bus(name="other", host="r035.EXAMPLE.")]},
            {"domat_mw240": [tcp_bus(), bus(port="/dev/other")]},
            *({"domat_mw240": [tcp_bus()], "modbus": [{"type": mode, "host": "r035.EXAMPLE.", "port": 502}]} for mode in ("tcp", "rtuovertcp")),
        ):
            with self.subTest(config=config), self.assertRaises(vol.Invalid):
                schema.CONFIG_SCHEMA(config)
        for config in (
            {"domat_mw240": [tcp_bus(), tcp_bus(name="other", tcp_port=1502)]},
            {"domat_mw240": [tcp_bus(), bus(name="serial")]},
            {"domat_mw240": [tcp_bus()], "modbus": [{"type": "tcp", "host": "elsewhere", "port": 502}]},
        ):
            schema.CONFIG_SCHEMA(config)


class TCPConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ConfigFlowTests.asyncSetUp
    flow = fixtures.ConfigFlowTests.flow
    entry = fixtures.ConfigFlowTests.entry
    options_flow = fixtures.ConfigFlowTests.options_flow

    async def test_endpoint_steps_cannot_override_bus_identity_or_selected_transport(self):
        for field, value in (("name", "renamed"), ("connection_type", "serial")):
            with self.subTest(field=field):
                flow = self.flow()
                await flow.async_step_user({"name": "office", "connection_type": "tcp"})
                result = await flow.async_step_tcp({"host": "r035.example", field: value})
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"], {"base": "invalid_config"})
                entry = self.entry()
                flow = self.options_flow(entry)
                await flow.async_step_init({"connection_type": "tcp"})
                result = await flow.async_step_tcp({"host": "r035.example", field: value})
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"], {"base": "invalid_config"})
                del self.hass.config_entries._entries[entry.entry_id]

    async def test_tcp_errors_are_correctable_without_changing_permissions(self):
        flow = self.flow()
        await flow.async_step_user({"name": "office", "connection_type": "tcp"})
        for changes, field in (({"host": "tcp://host"}, "host"), ({"tcp_port": True}, "tcp_port"), ({"tcp_port": "502"}, "tcp_port"), ({"timeout": 10.1}, "timeout")):
            with self.subTest(changes=changes):
                result = await flow.async_step_tcp({"host": "r035.example", **changes})
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["step_id"], "tcp")
                self.assertEqual(result["errors"], {field: "invalid_config"})
        result = await flow.async_step_tcp({"host": "r035.example", "timeout": 10})
        self.assertEqual(result["step_id"], "modules")
        result = await flow.async_step_modules({"modules": "23: Window"})
        self.assertFalse(result["data"]["allow_commands"])

    async def test_tcp_duplicate_endpoint_uses_effective_options_without_dns(self):
        from unittest.mock import patch
        self.entry(data=tcp_bus(host="old.example"), options={"host": "R035.example."})
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")):
            flow = self.flow()
            await flow.async_step_user({"name": "other", "connection_type": "tcp"})
            result = await flow.async_step_tcp({"host": "r035.EXAMPLE"})
            self.assertEqual(result["errors"], {"host": "tcp_endpoint_in_use"})
            result = await flow.async_step_tcp({"host": "old.example"})
            self.assertEqual(result["step_id"], "modules")
            # A late claim is checked again before creating the entry.
            self.entry(data=tcp_bus(name="late", host="old.example"), unique_id="late")
            result = await flow.async_step_modules({"modules": "23: Window"})
            self.assertEqual(result["reason"], "tcp_endpoint_in_use")

    async def test_options_switch_existing_bus_both_directions_preserving_identity_and_permissions(self):
        schema = importlib.import_module("custom_components.domat_mw240.config_schema")
        for data, mode, settings in ((bus(), "tcp", {"host": "R035.example", "tcp_port": 1502}), (tcp_bus(), "serial", {"port": "/dev/replacement"})):
            with self.subTest(mode=mode):
                entry = self.entry(data=data, options={"allow_commands": True, "allow_timing_changes": True, "allow_eeprom_save": True, "timeout": 0.8})
                flow = self.options_flow(entry)
                initial = await flow.async_step_init()
                self.assertEqual({key.schema for key in initial["data_schema"].schema}, {"connection_type"})
                self.assertEqual(initial["data_schema"]({})["connection_type"], data.get("connection_type", "serial"))
                form = await flow.async_step_init({"connection_type": mode})
                self.assertEqual(form["step_id"], mode)
                fields = {key.schema for key in form["data_schema"].schema}
                self.assertNotIn("name", fields)
                self.assertNotIn("port" if mode == "tcp" else "host", fields)
                self.assertEqual(form["data_schema"](settings)["timeout"], 0.8)
                modules = await getattr(flow, f"async_step_{mode}")(settings)
                self.assertEqual(modules["step_id"], "modules")
                result = await flow.async_step_modules({"modules": "23: Window"})
                self.assertEqual(result["type"], "create_entry")
                self.assertNotIn("name", result["data"])
                effective = schema.BUS_SCHEMA({**entry.data, **result["data"]})
                self.assertEqual(effective["name"], "office")
                self.assertEqual(entry.unique_id, "office")
                self.assertEqual(effective["devices"], data["devices"])
                self.assertEqual(effective["connection_type"], mode)
                for permission in ("allow_commands", "allow_timing_changes", "allow_eeprom_save"):
                    self.assertTrue(effective[permission])
                # Remove only the offline fixture, never run hardware lifecycle.
                del self.hass.config_entries._entries[entry.entry_id]

    async def test_options_reject_tcp_collision_but_not_own_endpoint(self):
        entry = self.entry(data=tcp_bus())
        self.entry(data=tcp_bus(name="other", host="other.example"), unique_id="other")
        flow = self.options_flow(entry)
        await flow.async_step_init({"connection_type": "tcp"})
        result = await flow.async_step_tcp({"host": "OTHER.example."})
        self.assertEqual(result["errors"], {"host": "tcp_endpoint_in_use"})
        result = await flow.async_step_tcp({"host": "r035.example."})
        self.assertEqual(result["step_id"], "modules")

    async def test_tcp_import_is_offline_and_rejects_duplicate_endpoint(self):
        result = await self.flow("import").async_step_import(tcp_bus())
        self.assertEqual(result["type"], "create_entry")
        self.entry(data=tcp_bus())
        result = await self.flow("import").async_step_import(tcp_bus(name="other", host="r035.EXAMPLE."))
        self.assertEqual(result["reason"], "tcp_endpoint_in_use")

    async def test_choose_transport_then_show_only_active_fields_and_create_without_io(self):
        from unittest.mock import patch
        from probatio import to_field_list
        from homeassistant.helpers import config_validation as cv
        flow = self.flow()
        with patch("serial.Serial", side_effect=AssertionError("No serial I/O")), patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")), patch("socket.create_connection", side_effect=AssertionError("No TCP probe")):
            initial = await flow.async_step_user()
            self.assertEqual({k.schema for k in initial["data_schema"].schema}, {"name", "connection_type"})
            form = await flow.async_step_user({"name": "office", "connection_type": "tcp"})
            self.assertEqual(form["step_id"], "tcp")
            fields = {field["name"]: field for field in to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)}
            self.assertEqual(set(fields), {"host", "tcp_port", "poll_interval", "timeout", "command_ttl", "allow_commands", "allow_timing_changes", "allow_eeprom_save"})
            self.assertEqual(fields["host"]["type"], "string")
            self.assertEqual(fields["tcp_port"]["type"], "integer")
            self.assertEqual(form["data_schema"]({"host": "R035.EXAMPLE."})["timeout"], 2)
            modules = await flow.async_step_tcp({"host": "R035.EXAMPLE."})
            self.assertEqual(modules["step_id"], "modules")
            result = await flow.async_step_modules({"modules": "23: Window"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["connection_type"], "tcp")
        self.assertEqual(result["data"]["host"], "R035.EXAMPLE.")
        self.assertEqual(result["data"]["tcp_port"], 502)
        self.assertFalse(result["data"]["allow_eeprom_save"])
        self.assertEqual(flow.context["unique_id"], "office")
        self.assertEqual(flow.MINOR_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
