"""Config-entry lifecycle and migration tests using real HA registries."""
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
