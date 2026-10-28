"""HA-layer EEPROM permissions, two-action confirmation and one-shot intent."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class EEPROMHATests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from test_engine import FakeTransport
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.hass = HomeAssistant(self.temp.name)
        integration = importlib.import_module("custom_components.domat_mw240")
        self.runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        self.transport = FakeTransport()
        self.transport.words[2] = 0x8000
        self.config = integration.BUS_SCHEMA({"name": "test", "port": "/dev/test", "devices": [{"id": 23, "name": "Test blind"}]})
        self.hub = self.runtime.Hub(self.hass, self.config, transport=self.transport)
        self.hub.bus.start()
        await self.hub.async_refresh()

    async def asyncTearDown(self):
        await self.hub.async_close()
        await self.hass.async_stop()
        self.temp.cleanup()

    async def test_prepare_and_save_require_separate_permission(self):
        from homeassistant.exceptions import HomeAssistantError
        prepare = getattr(self.hub, "async_prepare_eeprom", None)
        self.assertIsNotNone(prepare, "Guarded EEPROM preparation is missing")
        self.assertFalse(self.config.get("allow_eeprom_save", False))
        for action in [prepare, self.hub.async_save_eeprom]:
            with self.assertRaises(HomeAssistantError):
                await action(23)
        self.assertEqual(self.transport.writes, [])

    async def test_preparation_is_read_only_and_save_intent_is_consumed_once(self):
        self.assertTrue(hasattr(self.hub, "async_prepare_eeprom"), "EEPROM preparation missing")
        self.config["allow_eeprom_save"] = True
        await self.hub.async_prepare_eeprom(23)
        self.assertEqual(self.transport.writes, [])
        self.assertTrue(self.hub.eeprom_prepared(23))
        state = self.hub.records[23].snapshot
        with patch.object(self.hub.bus, "submit", return_value=state) as submit:
            await self.hub.async_save_eeprom(23)
            submit.assert_awaited_once()
            self.assertEqual(submit.call_args.args[:2], (23, "save_eeprom"))
            self.assertIsInstance(submit.call_args.args[2], tuple)
            from homeassistant.exceptions import HomeAssistantError
            with self.assertRaises(HomeAssistantError):
                await self.hub.async_save_eeprom(23)
            self.assertEqual(submit.await_count, 1)
        self.assertFalse(self.hub.eeprom_prepared(23))

    async def test_failed_commit_cannot_reuse_preparation(self):
        from homeassistant.exceptions import HomeAssistantError
        self.assertTrue(hasattr(self.hub, "async_prepare_eeprom"))
        self.config["allow_eeprom_save"] = True
        await self.hub.async_prepare_eeprom(23)
        with patch.object(self.hub.bus, "submit", side_effect=self.runtime.DeviceError("Lost response")) as submit:
            with self.assertRaises(HomeAssistantError):
                await self.hub.async_save_eeprom(23)
            with self.assertRaises(HomeAssistantError):
                await self.hub.async_save_eeprom(23)
            self.assertEqual(submit.await_count, 1)
        self.assertIn("uncertain", self.hub.eeprom_results[23])

    async def test_pending_draft_revokes_preparation(self):
        from homeassistant.exceptions import HomeAssistantError
        self.assertTrue(hasattr(self.hub, "async_prepare_eeprom"))
        self.config["allow_eeprom_save"] = True
        await self.hub.async_prepare_eeprom(23)
        self.hub.stage_travel_time(23, "down", 45)
        self.assertFalse(self.hub.eeprom_prepared(23))
        with self.assertRaises(HomeAssistantError):
            await self.hub.async_save_eeprom(23)
        with self.assertRaises(HomeAssistantError):
            await self.hub.async_prepare_eeprom(23)
        self.assertEqual(self.transport.writes, [])

    async def test_config_entry_has_locked_prepare_and_save_buttons(self):
        from types import SimpleNamespace
        button = importlib.import_module("custom_components.domat_mw240.button")
        entities = []
        await button.async_setup_entry(self.hass, SimpleNamespace(runtime_data=self.hub), entities.extend)
        self.assertEqual(len(entities), 5, "Device needs separate prepare and permanent-save actions")
        prepare = next(e for e in entities if e.unique_id.endswith("prepare_eeprom_save"))
        save = next(e for e in entities if e.unique_id.endswith("save_settings_to_eeprom"))
        self.assertFalse(prepare.available)
        self.assertFalse(save.available)
        self.config["allow_eeprom_save"] = True
        self.assertTrue(prepare.available)
        self.assertFalse(save.available)
        await prepare.async_press()
        self.assertTrue(save.available)
        attributes = save.extra_state_attributes
        self.assertTrue(attributes["eeprom_prepared"])
        self.assertTrue(attributes["eeprom_commits_all_RAM_settings"])
        self.assertEqual(attributes["eeprom_configuration_words"]["19"], "0x051e")
        self.assertEqual(self.transport.writes, [])

    async def test_expired_preparation_cannot_write(self):
        from homeassistant.exceptions import HomeAssistantError
        self.assertTrue(hasattr(self.hub, "async_prepare_eeprom"))
        self.config["allow_eeprom_save"] = True
        await self.hub.async_prepare_eeprom(23)
        with patch.object(self.runtime.time, "monotonic", return_value=10**12):
            with self.assertRaises(HomeAssistantError):
                await self.hub.async_save_eeprom(23)
        self.assertEqual(self.transport.writes, [])


if __name__ == "__main__":
    unittest.main()
