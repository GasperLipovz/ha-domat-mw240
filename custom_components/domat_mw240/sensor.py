"""Read-only commissioned settings, separate from editable timing drafts."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime

from .entity import MW240Entity

# Suffix, label, snapshot field. Settings remain read-only here.
SETTINGS = (
    ("firmware", " Firmware", "firmware"),
    ("applied_travel_time", " Applied travel time", "travel_seconds"),
    ("tilt_step_time", " Tilt step time", "step_seconds"),
    ("slat_rotation_time", " Slat rotation time", "rotation_seconds"),
    ("reversal_delay", " Reversal delay", "reversal_seconds"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    async_add_entities([MW240Setting(hub, device, suffix, label, field)
                        for device in hub.config["devices"] for suffix, label, field in SETTINGS])


class MW240Setting(MW240Entity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, device, suffix, label, field):
        super().__init__(hub, device, suffix, label)
        self._field = field
        if field != "firmware":
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_icon = "mdi:timer-outline"
        else:
            self._attr_icon = "mdi:chip"

    @property
    def native_value(self):
        return getattr(self.snapshot, self._field) if self.snapshot else None
