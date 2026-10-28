"""Draft travel times; changing a number never writes to the bus."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime

from . import DOMAIN
from .entity import MW240Entity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    hub = hass.data[DOMAIN][discovery_info["bus"]]
    async_add_entities([MW240TravelTime(hub, device, direction)
                        for device in hub.config["devices"] for direction in ("down", "up")])


async def async_setup_entry(hass, entry, async_add_entities):
    """Load drafts; setup never writes them to a module."""
    hub = entry.runtime_data
    async_add_entities([MW240TravelTime(hub, device, direction)
                        for device in hub.config["devices"] for direction in ("down", "up")])


class MW240TravelTime(MW240Entity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(self, hub, device, direction):
        state = hub.records[device["id"]].snapshot
        modern = bool(state and state.firmware >= 21)
        label = (" Pending up travel time" if direction == "up"
                 else " Pending down travel time" if modern else " Pending travel time")
        super().__init__(hub, device, f"travel_time_{direction}", label)
        self.direction = direction
        # FW17 has only one common travel time; do not offer an unsupported setting.
        self._attr_entity_registry_enabled_default = direction != "up" or modern

    @property
    def available(self):
        return super().available and (self.direction != "up" or self.snapshot.firmware >= 21)

    @property
    def applied_seconds(self):
        if not self.snapshot:
            return None
        return (self.snapshot.travel_up_seconds if self.direction == "up"
                else self.snapshot.travel_seconds)

    @property
    def native_value(self):
        return self.coordinator.pending_timings.get(self.unit, {}).get(self.direction, self.applied_seconds)

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            "applied_seconds": self.applied_seconds,
            "pending_change": self.direction in self.coordinator.pending_timings.get(self.unit, {}),
            "apply_required": True,
            "persistence": "working_RAM_only; lost_on_module_power_cycle",
            "timing_writes_enabled": self.coordinator.config["allow_timing_changes"],
        }

    async def async_set_native_value(self, value):
        self.coordinator.stage_travel_time(self.unit, self.direction, value)
