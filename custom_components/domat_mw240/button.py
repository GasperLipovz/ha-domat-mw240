"""Explicit single-step tilt controls; no invented angle on older firmware."""
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from . import DOMAIN
from .entity import MW240Entity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    hub = hass.data[DOMAIN][discovery_info["bus"]]
    async_add_entities([MW240TiltStep(hub, device, direction)
                        for device in hub.config["devices"] for direction in ("up", "down")]
                       + [MW240ApplyTravelTime(hub, device) for device in hub.config["devices"]])


async def async_setup_entry(hass, entry, async_add_entities):
    """Load explicit actions under their module devices."""
    hub = entry.runtime_data
    async_add_entities([MW240TiltStep(hub, device, direction)
                        for device in hub.config["devices"] for direction in ("up", "down")]
                       + [MW240ApplyTravelTime(hub, device) for device in hub.config["devices"]]
                       + [MW240EEPROM(hub, device, prepare=prepare)
                          for device in hub.config["devices"] for prepare in (True, False)])


class MW240TiltStep(MW240Entity, ButtonEntity):
    def __init__(self, hub, device, direction):
        super().__init__(hub, device, f"tilt_{direction}_step", f" Tilt {direction} step")
        self._direction = direction
        self._attr_icon = "mdi:blinds-horizontal"

    async def async_press(self):
        await self.coordinator.async_command(self.unit, f"tilt_{self._direction}")


class MW240ApplyTravelTime(MW240Entity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-check-outline"

    def __init__(self, hub, device):
        super().__init__(hub, device, "apply_travel_times", " Apply travel times (temporary)")

    async def async_press(self):
        await self.coordinator.async_apply_travel_times(self.unit)


class MW240EEPROM(MW240Entity, ButtonEntity):
    """Explicit read-only preparation followed by one guarded permanent save."""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub, device, *, prepare):
        super().__init__(hub, device,
                         "prepare_eeprom_save" if prepare else "save_settings_to_eeprom",
                         " Prepare EEPROM save" if prepare else " Save settings to EEPROM")
        self._prepare = prepare
        self._attr_icon = "mdi:clipboard-check-outline" if prepare else "mdi:content-save-alert"

    @property
    def available(self):
        return bool(super().available and self.snapshot.firmware == 17
                    and self.coordinator.config.get("allow_eeprom_save", False)
                    and (self._prepare or self.coordinator.eeprom_prepared(self.unit)))

    @property
    def extra_state_attributes(self):
        from .engine import EEPROM_CONFIG_OFFSETS

        prepared = self.coordinator._eeprom_confirmations.get(self.unit)
        return {
            **super().extra_state_attributes,
            "eeprom_save_enabled": self.coordinator.config.get("allow_eeprom_save", False),
            "eeprom_commits_all_RAM_settings": True,
            "eeprom_prepared": self.coordinator.eeprom_prepared(self.unit),
            "eeprom_configuration_words": {
                str(offset + 1): f"0x{word:04x}"
                for offset, word in zip(EEPROM_CONFIG_OFFSETS, prepared[0])
            } if prepared else None,
            "eeprom_last_result": self.coordinator.eeprom_results.get(self.unit, "not_saved_this_session"),
        }

    async def async_press(self):
        if self._prepare:
            await self.coordinator.async_prepare_eeprom(self.unit)
        else:
            await self.coordinator.async_save_eeprom(self.unit)
