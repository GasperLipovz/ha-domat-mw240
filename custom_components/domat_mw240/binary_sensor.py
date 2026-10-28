"""Connection status remains visible when an individual module is offline."""
import time

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import MW240Entity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    async_add_entities([MW240Online(hub, device) for device in hub.config["devices"]])


class MW240Online(MW240Entity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, hub, device):
        super().__init__(hub, device, "online", " Online")

    @property
    def available(self):
        return not self.coordinator._closed

    @property
    def is_on(self):
        record = self.record
        return bool(record and record.available and record.snapshot
                    and time.monotonic() - record.last_seen < self.coordinator.availability_timeout)
