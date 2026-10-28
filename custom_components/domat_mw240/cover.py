"""Native HA covers with step-tilt arrows; no invented percentage feedback."""
from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature

from . import DOMAIN
from .entity import MW240Entity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    hub = hass.data[DOMAIN][discovery_info["bus"]]
    async_add_entities([MW240Cover(hub, device) for device in hub.config["devices"]])


async def async_setup_entry(hass, entry, async_add_entities):
    """Load covers under their native config entry and module device."""
    hub = entry.runtime_data
    async_add_entities([MW240Cover(hub, device) for device in hub.config["devices"]])


class MW240Cover(MW240Entity, CoverEntity):
    _attr_device_class = CoverDeviceClass.BLIND
    _attr_supported_features = (CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
                                | CoverEntityFeature.STOP | CoverEntityFeature.SET_POSITION
                                | CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
                                | CoverEntityFeature.STOP_TILT)

    def __init__(self, hub, device):
        super().__init__(hub, device, "cover")

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, "tilt_control_mode": "single_step"}

    @property
    def current_cover_position(self):
        return self.snapshot.position if self.snapshot else None

    @property
    def is_closed(self):
        position = self.current_cover_position
        return position == 0 if position is not None else None

    @property
    def is_opening(self):
        return self.snapshot.outputs == 1 if self.snapshot else None

    @property
    def is_closing(self):
        return self.snapshot.outputs == 2 if self.snapshot else None

    async def async_open_cover(self, **kwargs):
        await self.coordinator.async_command(self.unit, "position", 100)

    async def async_close_cover(self, **kwargs):
        await self.coordinator.async_command(self.unit, "position", 0)

    async def async_stop_cover(self, **kwargs):
        await self.coordinator.async_command(self.unit, "stop")

    async def async_set_cover_position(self, **kwargs):
        await self.coordinator.async_command(self.unit, "position", kwargs[ATTR_POSITION])

    async def async_open_cover_tilt(self, **kwargs):
        """One upward tilt step, not a full-open angle target."""
        await self.coordinator.async_command(self.unit, "tilt_up")

    async def async_close_cover_tilt(self, **kwargs):
        """One downward tilt step, not a full-close angle target."""
        await self.coordinator.async_command(self.unit, "tilt_down")

    async def async_stop_cover_tilt(self, **kwargs):
        await self.coordinator.async_command(self.unit, "stop")
