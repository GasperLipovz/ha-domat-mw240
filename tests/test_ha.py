"""Tests against real Home Assistant 2026.9.4, never a live HA instance."""
import importlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class HomeAssistantTests(unittest.TestCase):
    def test_configuration_defaults_to_read_only_and_rejects_duplicate_bus_or_ids(self):
        self.assertTrue((ROOT / "custom_components/domat_mw240/__init__.py").exists(), "Missing HA integration")
        integration = importlib.import_module("custom_components.domat_mw240")
        import voluptuous as vol
        configuration = {"domat_mw240": [{"name": "office", "port": "/dev/example", "devices": [{"id": 23, "name": "Test blind"}]}]}
        checked = integration.CONFIG_SCHEMA(configuration)
        self.assertFalse(checked["domat_mw240"][0]["allow_commands"])
        self.assertEqual(checked["domat_mw240"][0]["baudrate"], 19200)
        duplicate = {"domat_mw240": [configuration["domat_mw240"][0]] * 2}
        with self.assertRaises(vol.Invalid):
            integration.CONFIG_SCHEMA(duplicate)
        configuration["domat_mw240"][0]["devices"] *= 2
        with self.assertRaises(vol.Invalid):
            integration.CONFIG_SCHEMA(configuration)


class EntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_covers_forty_tilt_buttons_and_read_only_startup(self):
        import asyncio
        import os
        import tempfile
        from homeassistant.core import HomeAssistant
        from homeassistant.exceptions import HomeAssistantError
        integration = importlib.import_module("custom_components.domat_mw240")
        self.assertTrue((ROOT / "custom_components/domat_mw240/runtime.py").exists(), "Missing HA bus runtime")
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        cover = importlib.import_module("custom_components.domat_mw240.cover")
        button = importlib.import_module("custom_components.domat_mw240.button")
        from test_engine import FakeTransport
        transport = FakeTransport()
        ids = list(range(1, 20)) + [23]
        config = integration.CONFIG_SCHEMA({"domat_mw240": [{"name": "test", "port": "/dev/example", "devices": [{"id": unit, "name": f"Blind {unit}"} for unit in ids]}]})["domat_mw240"][0]
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            hub = runtime.Hub(hass, config, transport=transport)
            hass.data[integration.DOMAIN] = {"test": hub}
            hub.bus.start()
            try:
                await hub.async_refresh()
                covers, buttons = [], []
                await cover.async_setup_platform(hass, {}, covers.extend, {"bus": "test"})
                await button.async_setup_platform(hass, {}, buttons.extend, {"bus": "test"})
                self.assertEqual(len(covers), 20)
                self.assertEqual(len(buttons), 60)
                self.assertEqual(len({item.unique_id for item in covers + buttons}), 80)
                self.assertTrue(all(item.available for item in covers))
                self.assertEqual(transport.writes, [], "Startup must not move hardware")
                with self.assertRaises(HomeAssistantError):
                    await covers[-1].async_open_cover()
                self.assertEqual(transport.writes, [])
                config["allow_commands"] = True
                await covers[-1].async_close_cover()
                self.assertTrue(all(unit == 23 for unit, _, _ in transport.writes))
                self.assertEqual(transport.words[16] & 1, 0)
                self.assertEqual(covers[-1].supported_features,
                                 15 | cover.CoverEntityFeature.OPEN_TILT
                                 | cover.CoverEntityFeature.CLOSE_TILT | cover.CoverEntityFeature.STOP_TILT)
                self.assertIsNone(covers[-1].current_cover_tilt_position)
            finally:
                await hub.async_close()
                await hass.async_stop()


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_yaml_setup_imports_entry_without_opening_another_bus(self):
        import os
        import tempfile
        from unittest.mock import patch, AsyncMock
        from homeassistant.core import HomeAssistant
        integration = importlib.import_module("custom_components.domat_mw240")
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        from test_engine import FakeTransport
        self.assertTrue(hasattr(integration, "async_setup"), "YAML integration lacks setup entry point")
        config = integration.CONFIG_SCHEMA({"domat_mw240": [{"name": "setup_test", "port": "/dev/example", "devices": [{"id": 23, "name": "Test blind"}]}]})
        transport = FakeTransport()
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            from types import SimpleNamespace
            start = AsyncMock(return_value={"type": "abort"})
            hass.config_entries = SimpleNamespace(flow=SimpleNamespace(async_init=start))
            with patch.object(runtime, "SerialTransport") as transport_factory:
                self.assertTrue(await integration.async_setup(hass, config))
                await hass.async_block_till_done()
                start.assert_awaited_once_with(integration.DOMAIN, context={"source": "import"}, data=config[integration.DOMAIN][0])
                transport_factory.assert_not_called()
                self.assertEqual(transport.writes, [])
            await hass.async_stop()


class TimingEntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_timing_numbers_stage_locally_and_apply_requires_separate_permission(self):
        import os
        import tempfile
        from homeassistant.core import HomeAssistant
        from homeassistant.exceptions import HomeAssistantError
        integration = importlib.import_module("custom_components.domat_mw240")
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        self.assertTrue((ROOT / "custom_components/domat_mw240/number.py").exists(), "Missing travel-time number platform")
        number = importlib.import_module("custom_components.domat_mw240.number")
        button = importlib.import_module("custom_components.domat_mw240.button")
        from test_engine import FakeTransport
        transport = FakeTransport()
        config = integration.CONFIG_SCHEMA({"domat_mw240": [{"name": "timing", "port": "/dev/example", "devices": [{"id": 23, "name": "Test blind"}]}]})["domat_mw240"][0]
        self.assertFalse(config["allow_timing_changes"])
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            hub = runtime.Hub(hass, config, transport=transport)
            hass.data[integration.DOMAIN] = {"timing": hub}
            hub.bus.start()
            try:
                await hub.async_refresh()
                numbers, buttons = [], []
                await number.async_setup_platform(hass, {}, numbers.extend, {"bus": "timing"})
                await button.async_setup_platform(hass, {}, buttons.extend, {"bus": "timing"})
                self.assertEqual(len(numbers), 2)
                self.assertEqual(len(buttons), 3)
                shared, up = numbers
                self.assertEqual(shared.native_value, 30)
                self.assertFalse(up.available)
                await shared.async_set_native_value(45.0)
                self.assertEqual(shared.native_value, 45)
                self.assertEqual(shared.extra_state_attributes["applied_seconds"], 30)
                self.assertTrue(shared.extra_state_attributes["pending_change"])
                self.assertEqual(transport.writes, [])
                with self.assertRaises(HomeAssistantError):
                    await buttons[-1].async_press()
                config["allow_commands"] = True
                with self.assertRaises(HomeAssistantError):
                    await hub.async_command(23, "travel_times", {"down": 60})
                self.assertEqual(transport.writes, [])
                config["allow_commands"] = False
                config["allow_timing_changes"] = True
                await buttons[-1].async_press()
                self.assertEqual(transport.writes, [(23, 18, [0x052D])])
                self.assertEqual(shared.native_value, 45)
                self.assertFalse(shared.extra_state_attributes["pending_change"])
                with self.assertRaises(HomeAssistantError):
                    await shared.async_set_native_value(45.5)
                await shared.async_set_native_value(50)
                transport.words[12] = 2
                with self.assertRaises(HomeAssistantError):
                    await buttons[-1].async_press()
                self.assertEqual(len(transport.writes), 1)
                self.assertEqual(hub.pending_timings[23], {"down": 50})
            finally:
                await hub.async_close()
                await hass.async_stop()


class NativeTiltTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_cover_tilt_arrows_use_steps_without_exposing_percentage(self):
        import os
        import tempfile
        from homeassistant.core import HomeAssistant
        from homeassistant.components.cover import CoverEntityFeature
        from homeassistant.exceptions import HomeAssistantError
        integration = importlib.import_module("custom_components.domat_mw240")
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        cover = importlib.import_module("custom_components.domat_mw240.cover")
        from test_engine import FakeTransport
        config = integration.CONFIG_SCHEMA({"domat_mw240": [{"name": "native_tilt", "port": "/dev/example", "devices": [{"id": 23, "name": "Test blind"}]}]})["domat_mw240"][0]
        transport = FakeTransport()
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            hub = runtime.Hub(hass, config, transport=transport)
            hub.bus.start()
            try:
                await hub.async_refresh()
                entity = cover.MW240Cover(hub, config["devices"][0])
                tilt_features = CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT | CoverEntityFeature.STOP_TILT
                self.assertEqual(entity.supported_features & tilt_features, tilt_features)
                self.assertFalse(entity.supported_features & CoverEntityFeature.SET_TILT_POSITION)
                self.assertIsNone(entity.current_cover_tilt_position)
                self.assertEqual(entity.extra_state_attributes["tilt_control_mode"], "single_step")
                for action in [entity.async_open_cover_tilt, entity.async_close_cover_tilt, entity.async_stop_cover_tilt]:
                    with self.assertRaises(HomeAssistantError):
                        await action()
                self.assertEqual(transport.writes, [])
                config["allow_commands"] = True
                await entity.async_open_cover_tilt()
                await entity.async_close_cover_tilt()
                await entity.async_stop_cover_tilt()
                self.assertEqual(transport.writes, [(23, 16, [v]) for v in [3, 5, 0, 3, 9, 0, 3, 0]])
                self.assertEqual(transport.words[12], 0)
                self.assertEqual(transport.words[16] & 1, 0)
            finally:
                await hub.async_close()
                await hass.async_stop()


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_twenty_module_cycle_does_not_mark_healthy_nodes_stale(self):
        import os
        import tempfile
        import time
        from homeassistant.core import HomeAssistant
        from custom_components.domat_mw240 import BUS_SCHEMA
        from custom_components.domat_mw240.runtime import Hub, Record
        from custom_components.domat_mw240.cover import MW240Cover
        from custom_components.domat_mw240.binary_sensor import MW240Online
        from test_engine import FakeTransport
        config = BUS_SCHEMA({"name": "slow", "port": "/dev/test", "timeout": 2,
                             "devices": [{"id": unit, "name": f"Blind {unit}"} for unit in range(1, 21)]})
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            hub = Hub(hass, config, transport=FakeTransport())
            hub.bus.start()
            try:
                await hub.async_refresh()
                snapshot = hub.records[1].snapshot
                hub.records[1] = Record(snapshot, True, None, time.monotonic() - 15)
                hub.async_set_updated_data(dict(hub.records))
                self.assertTrue(MW240Cover(hub, config["devices"][0]).available)
                self.assertTrue(MW240Online(hub, config["devices"][0]).is_on)
                hub.records[1] = Record(snapshot, False, "offline", time.monotonic())
                hub.async_set_updated_data(dict(hub.records))
                self.assertFalse(MW240Cover(hub, config["devices"][0]).available)
            finally:
                await hub.async_close()
                await hass.async_stop()


if __name__ == "__main__":
    unittest.main()
