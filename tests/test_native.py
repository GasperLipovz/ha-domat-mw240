"""Config-entry lifecycle and migration tests using real HA registries."""
from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class NativeEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.integration = importlib.import_module("custom_components.domat_mw240")
        self.runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        self.config = self.integration.BUS_SCHEMA({
            "name": "blinds_bus_1", "port": "/dev/test",
            "devices": [{"id": 23, "name": "Test blind"}],
        })
        self.entry = ConfigEntry(
            domain="domat_mw240", title="blinds_bus_1", data=self.config,
            options={}, unique_id="blinds_bus_1", version=1, minor_version=1,
            source="import", discovery_keys={}, subentries_data=[],
        )
        with patch.object(self.hass.config_entries, "async_setup", AsyncMock(return_value=True)):
            await self.hass.config_entries.async_add(self.entry)
        self.hub = None

    async def asyncTearDown(self):
        if self.hub:
            await self.hub.async_close()
        await self.hass.async_stop()
        self.temp.cleanup()

    async def test_version_030_migration_defaults_to_serial_without_changing_options(self):
        migrate = getattr(self.integration, "async_migrate_entry", None)
        self.assertIsNotNone(migrate, "Stored 0.3 serial entries need an explicit minor migration")
        assert migrate is not None
        options = {"allow_commands": True, "allow_timing_changes": False, "timeout": 0.8}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        original = dict(self.entry.data)
        self.assertTrue(await migrate(self.hass, self.entry))
        self.assertEqual(self.entry.data, {**original, "connection_type": "serial"})
        self.assertEqual(dict(self.entry.options), options)
        self.assertEqual(self.entry.unique_id, "blinds_bus_1")
        self.assertEqual(self.entry.minor_version, 2)
        self.assertTrue(await migrate(self.hass, self.entry))
        self.assertEqual(self.entry.data, {**original, "connection_type": "serial"})

    async def test_config_entry_registers_bus_and_module_devices_without_writes(self):
        from homeassistant.helpers import device_registry as dr
        from test_engine import FakeTransport
        transport = FakeTransport()
        setup_entry = getattr(self.integration, "async_setup_entry", None)
        self.assertIsNotNone(setup_entry, "Native config-entry setup is missing")
        with patch.object(self.runtime, "SerialTransport", return_value=transport), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ) as forward:
            self.assertTrue(await setup_entry(self.hass, self.entry))
            self.hub = self.entry.runtime_data
            self.assertTrue(self.hub.data[23].available)
            forward.assert_awaited_once()
            self.assertEqual(set(map(str, forward.call_args.args[1])), {"cover", "button", "number", "sensor", "binary_sensor"})
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)
        self.assertEqual(len(devices), 2)
        module = next(d for d in devices if d.model == "MW240-B")
        bus = next(d for d in devices if d.model == "Modbus RTU bus")
        self.assertEqual(module.manufacturer, "Domat")
        self.assertEqual(module.sw_version, "17")
        self.assertEqual(module.via_device_id, bus.id)
        self.assertEqual(module.area_id, None)
        self.assertEqual(transport.writes, [])

    async def test_legacy_entity_id_moves_to_entry_without_renaming(self):
        from homeassistant.helpers import entity_registry as er
        from test_engine import FakeTransport
        registry = er.async_get(self.hass)
        legacy = registry.async_get_or_create(
            "cover", "domat_mw240", "domat_mw240_blinds_bus_1_23_cover",
            suggested_object_id="existing_blind",
        )
        registry.async_update_entity(legacy.entity_id, name="My existing name")
        setup_entry = getattr(self.integration, "async_setup_entry", None)
        self.assertIsNotNone(setup_entry, "Native migration is missing")
        with patch.object(self.runtime, "SerialTransport", return_value=FakeTransport()), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ):
            self.assertTrue(await setup_entry(self.hass, self.entry))
            self.hub = self.entry.runtime_data
        migrated = registry.async_get(legacy.entity_id)
        self.assertEqual(migrated.entity_id, "cover.existing_blind")
        self.assertEqual(migrated.name, "My existing name")
        self.assertEqual(migrated.config_entry_id, self.entry.entry_id)
        self.assertEqual(migrated.unique_id, legacy.unique_id)

    async def test_platform_entry_entities_share_module_identity(self):
        from test_engine import FakeTransport
        setup_entry = getattr(self.integration, "async_setup_entry", None)
        self.assertIsNotNone(setup_entry)
        with patch.object(self.runtime, "SerialTransport", return_value=FakeTransport()), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ):
            await setup_entry(self.hass, self.entry)
            self.hub = self.entry.runtime_data
        cover = importlib.import_module("custom_components.domat_mw240.cover")
        entry_setup = getattr(cover, "async_setup_entry", None)
        self.assertIsNotNone(entry_setup, "Platforms must load via config entries")
        entities = []
        await entry_setup(self.hass, self.entry, entities.extend)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].unique_id, "domat_mw240_blinds_bus_1_23_cover")
        self.assertEqual(entities[0].device_info["identifiers"], {("domat_mw240", "blinds_bus_1_23")})
        self.assertEqual(entities[0].device_info["via_device_id"], self.hub.bus_device_id)
        sensor = importlib.import_module("custom_components.domat_mw240.sensor")
        sensors = []
        await sensor.async_setup_entry(self.hass, self.entry, sensors.extend)
        self.assertEqual(next(e.native_value for e in sensors if e.unique_id.endswith("applied_travel_time")), 30)
        binary = importlib.import_module("custom_components.domat_mw240.binary_sensor")
        statuses = []
        await binary.async_setup_entry(self.hass, self.entry, statuses.extend)
        self.assertTrue(next(e.is_on for e in statuses if e.unique_id.endswith("online")))

    async def test_full_home_assistant_entry_loads_platforms_and_device_entities(self):
        import shutil
        from homeassistant import loader
        from homeassistant.helpers import frame, entity_registry as er
        from test_engine import FakeTransport
        target = Path(self.temp.name) / "custom_components" / "domat_mw240"
        shutil.copytree(ROOT / "custom_components" / "domat_mw240", target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        loader.async_setup(self.hass)
        frame.async_setup(self.hass)
        transport = FakeTransport()
        with patch.object(self.runtime, "SerialTransport", return_value=transport):
            self.assertTrue(await self.hass.config_entries.async_setup(self.entry.entry_id))
            self.hub = self.entry.runtime_data
            await self.hass.async_block_till_done()
            registry = er.async_get(self.hass)
            entries = er.async_entries_for_config_entry(registry, self.entry.entry_id)
            self.assertEqual(len(entries), 14)
            self.assertTrue(all(e.device_id is not None for e in entries))
            cover = self.hass.states.get("cover.test_blind")
            self.assertIsNotNone(cover)
            self.assertNotEqual(cover.state, "unavailable")
            self.assertEqual(cover.attributes["supported_features"], 127)
            self.assertEqual(transport.writes, [])
            self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))

    async def test_full_twenty_module_bus_loads_one_master_and_twenty_devices(self):
        import shutil
        from homeassistant import loader
        from homeassistant.helpers import frame, device_registry as dr, entity_registry as er
        from test_engine import FakeTransport
        config = {**self.config, "devices": [{"id": unit, "name": f"Blind {unit}"} for unit in range(1, 21)]}
        self.hass.config_entries.async_update_entry(self.entry, data=config)
        target = Path(self.temp.name) / "custom_components" / "domat_mw240"
        shutil.copytree(ROOT / "custom_components" / "domat_mw240", target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        loader.async_setup(self.hass)
        frame.async_setup(self.hass)
        transport = FakeTransport()
        with patch.object(self.runtime, "SerialTransport", return_value=transport) as factory:
            self.assertTrue(await self.hass.config_entries.async_setup(self.entry.entry_id))
            self.hub = self.entry.runtime_data
            await self.hass.async_block_till_done()
            devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)
            self.assertEqual(len(devices), 21)
            entries = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id)
            self.assertEqual(len(entries), 280)
            self.assertEqual(len({e.unique_id for e in entries}), 280)
            self.assertEqual(factory.call_count, 1)
            self.assertTrue(all(record.available for record in self.hub.records.values()))
            self.assertEqual(transport.writes, [])
            self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))

    async def test_same_entry_can_switch_to_tcp_without_new_entity_or_device_identity(self):
        from homeassistant.helpers import device_registry as dr
        from test_engine import FakeTransport
        serial_transport = FakeTransport()
        with patch.object(self.integration, "_async_options_updated", AsyncMock()), patch.object(self.runtime, "SerialTransport", return_value=serial_transport), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ):
            await self.integration.async_setup_entry(self.hass, self.entry)
            self.hub = self.entry.runtime_data
        before = {d.model: d.id for d in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)}
        with patch.object(self.hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
            self.assertTrue(await self.integration.async_unload_entry(self.hass, self.entry))
        self.assertTrue(self.hub._closed)
        self.hass.config_entries.async_update_entry(self.entry, options={
            "connection_type": "tcp", "host": "gateway.example", "tcp_port": 502, "timeout": 2,
        })
        tcp_transport = FakeTransport()
        with patch.object(self.runtime, "SerialTransport") as serial, patch.object(
            self.runtime, "TcpTransport", return_value=tcp_transport
        ) as tcp, patch.object(self.hass.config_entries, "async_forward_entry_setups", AsyncMock()):
            self.assertTrue(await self.integration.async_setup_entry(self.hass, self.entry))
            self.hub = self.entry.runtime_data
            serial.assert_not_called()
            tcp.assert_called_once()
        after = {d.model: d.id for d in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)}
        self.assertEqual(before, after)
        self.assertEqual(self.hub.config["name"], "blinds_bus_1")
        cover = importlib.import_module("custom_components.domat_mw240.cover").MW240Cover(self.hub, self.config["devices"][0])
        self.assertEqual(cover.unique_id, "domat_mw240_blinds_bus_1_23_cover")
        self.assertEqual(self.hass.data["domat_mw240"]["ports"], {("tcp", "gateway.example", 502): self.entry.entry_id})
        self.assertEqual(serial_transport.writes, [])
        self.assertEqual(tcp_transport.writes, [])

    async def test_tcp_endpoint_owned_elsewhere_is_refused_before_connecting(self):
        from homeassistant.exceptions import ConfigEntryError
        self.hass.config_entries.async_update_entry(self.entry, options={
            "connection_type": "tcp", "host": "Gateway.EXAMPLE.", "tcp_port": 502,
        })
        self.hass.data["domat_mw240"] = {
            "ports": {("tcp", "gateway.example", 502): "other_entry"}, "modbus_ports": set(),
        }
        with patch.object(self.runtime, "TcpTransport") as factory:
            with self.assertRaises(ConfigEntryError):
                await self.integration.async_setup_entry(self.hass, self.entry)
            factory.assert_not_called()

    async def test_foreign_quoted_port_setup_preserves_config_and_legacy_import(self):
        for mode in ("tcp", "rtuovertcp"):
            for domat in (None, [self.config]):
                with self.subTest(mode=mode, domat=domat):
                    config = {"modbus": [
                        {"name": "unrelated", "type": mode, "host": "192.0.2.9", "port": "502"},
                    ]}
                    if domat is not None:
                        config["domat_mw240"] = domat
                    original = deepcopy(config)
                    with patch.object(self.hass.config_entries.flow, "async_init", AsyncMock(return_value={"type": "abort"})) as start, patch.object(
                        self.runtime, "SerialTransport"
                    ) as serial, patch.object(self.runtime, "TcpTransport") as tcp:
                        self.assertTrue(await self.integration.async_setup(self.hass, config))
                        await self.hass.async_block_till_done()
                        if domat is None:
                            start.assert_not_awaited()
                        else:
                            start.assert_awaited_once_with("domat_mw240", context={"source": "import"}, data=self.config)
                        serial.assert_not_called()
                        tcp.assert_not_called()
                    self.assertEqual(config, original)
                    self.assertEqual(self.hass.data["domat_mw240"]["modbus_ports"], {("tcp", "192.0.2.9", 502)})

    async def test_foreign_quoted_port_setup_reserves_conflicting_endpoint(self):
        from homeassistant.exceptions import ConfigEntryError
        self.hass.config_entries.async_update_entry(self.entry, options={
            "connection_type": "tcp", "host": "Gateway.EXAMPLE.", "tcp_port": 502,
        })
        for mode in ("tcp", "rtuovertcp"):
            with self.subTest(mode=mode):
                config = {"modbus": [
                    {"name": "existing", "type": mode, "host": "gateway.example", "port": "502"},
                ]}
                original = deepcopy(config)
                self.assertTrue(await self.integration.async_setup(self.hass, config))
                self.assertEqual(config, original)
                self.assertEqual(self.hass.data["domat_mw240"]["modbus_ports"], {("tcp", "gateway.example", 502)})
                with patch.object(self.runtime, "TcpTransport") as factory:
                    with self.assertRaisesRegex(ConfigEntryError, "already has a Modbus owner"):
                        await self.integration.async_setup_entry(self.hass, self.entry)
                    factory.assert_not_called()

    async def test_foreign_host_setup_uses_builtin_string_validation(self):
        from homeassistant.helpers import config_validation as cv
        for host in ("gateway_local", 123, 0, ""):
            with self.subTest(host=host):
                config = {"modbus": [{"name": "unrelated", "type": "tcp", "host": host, "port": 502}]}
                original = deepcopy(config)
                self.assertTrue(await self.integration.async_setup(self.hass, config))
                self.assertEqual(config, original)
                self.assertEqual(self.hass.data["domat_mw240"]["modbus_ports"], {("tcp", cv.string(host), 502)})

    async def test_builtin_tcp_gateway_owner_is_reserved_before_native_setup(self):
        from homeassistant.exceptions import ConfigEntryError
        self.hass.config_entries.async_update_entry(self.entry, options={
            "connection_type": "tcp", "host": "Gateway.EXAMPLE.", "tcp_port": 502,
        })
        await self.integration.async_setup(self.hass, {"modbus": [
            {"type": "tcp", "host": "gateway.example", "port": 502},
        ]})
        with patch.object(self.runtime, "TcpTransport") as factory:
            with self.assertRaises(ConfigEntryError):
                await self.integration.async_setup_entry(self.hass, self.entry)
            factory.assert_not_called()

    async def test_tcp_setup_failure_closes_worker_and_releases_endpoint(self):
        from test_engine import FakeTransport
        self.hass.config_entries.async_update_entry(self.entry, options={
            "connection_type": "tcp", "host": "gateway.example", "tcp_port": 502,
        })
        transport = FakeTransport()
        with patch.object(self.runtime, "TcpTransport", return_value=transport), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock(side_effect=RuntimeError("Platform failed"))
        ):
            with self.assertRaisesRegex(RuntimeError, "Platform failed"):
                await self.integration.async_setup_entry(self.hass, self.entry)
        self.hub = self.entry.runtime_data
        self.assertTrue(self.hub._closed)
        self.assertTrue(self.hub.bus._task.done())
        self.assertEqual(self.hass.data["domat_mw240"]["ports"], {})
        self.assertEqual(transport.writes, [])

    async def test_duplicate_serial_master_is_refused_before_opening_port(self):
        from homeassistant.exceptions import ConfigEntryError
        self.hass.data["domat_mw240"] = {"ports": {"/dev/test": "another_entry"}, "modbus_ports": set()}
        with patch.object(self.runtime, "SerialTransport") as transport:
            with self.assertRaises(ConfigEntryError):
                await self.integration.async_setup_entry(self.hass, self.entry)
            transport.assert_not_called()

    async def test_unload_releases_owner_and_closes_worker_without_writes(self):
        from test_engine import FakeTransport
        transport = FakeTransport()
        with patch.object(self.runtime, "SerialTransport", return_value=transport), patch.object(
            self.hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ):
            await self.integration.async_setup_entry(self.hass, self.entry)
            self.hub = self.entry.runtime_data
        unload = getattr(self.integration, "async_unload_entry", None)
        self.assertIsNotNone(unload, "UI unload is missing")
        with patch.object(self.hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
            self.assertTrue(await unload(self.hass, self.entry))
        self.assertTrue(self.hub._closed)
        self.assertEqual(self.hass.data["domat_mw240"]["ports"], {})
        self.assertEqual(transport.writes, [])

    async def test_yaml_only_starts_import_never_a_second_serial_master(self):
        with patch.object(self.hass.config_entries.flow, "async_init", AsyncMock(return_value={"type": "abort"})) as start, patch.object(
            self.runtime, "SerialTransport"
        ) as transport:
            self.assertTrue(await self.integration.async_setup(self.hass, {"domat_mw240": [self.config]}))
            await self.hass.async_block_till_done()
            start.assert_awaited_once_with("domat_mw240", context={"source": "import"}, data=self.config)
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
