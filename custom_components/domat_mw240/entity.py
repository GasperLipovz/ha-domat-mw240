"""Common entity metadata and per-module availability."""
from __future__ import annotations

import time
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class MW240Entity(CoordinatorEntity):
    _attr_has_entity_name = False

    def __init__(self, hub, device, suffix, label=""):
        super().__init__(hub, context=device["id"])
        self.unit = device["id"]
        self._attr_unique_id = f"domat_mw240_{hub.config['name']}_{self.unit}_{suffix}"
        self._attr_name = device["name"] + label
        self._module_name = device["name"]

    @property
    def device_info(self):
        """Group all controls/diagnostics under the physical MW240 module."""
        from homeassistant.helpers.device_registry import DeviceInfo

        state = self.snapshot
        info = DeviceInfo(
            identifiers={("domat_mw240", f"{self.coordinator.config['name']}_{self.unit}")},
            manufacturer="Domat", model="MW240-B", name=self._module_name,
            sw_version=str(state.firmware) if state else None,
        )
        if bus_device_id := getattr(self.coordinator, "bus_device_id", None):
            info["via_device_id"] = bus_device_id
        return info

    @property
    def record(self):
        return (self.coordinator.data or {}).get(self.unit)

    @property
    def snapshot(self):
        return self.record.snapshot if self.record else None

    @property
    def available(self):
        record = self.record
        return bool(record and record.available and record.snapshot
                    and record.snapshot.blind_mode and record.snapshot.firmware >= 14
                    and time.monotonic() - record.last_seen < self.coordinator.availability_timeout)

    @property
    def extra_state_attributes(self):
        state = self.snapshot
        if state is None:
            return {"modbus_address": self.unit, "last_error": self.record.error if self.record else None}
        return {
            "modbus_address": self.unit,
            "bus": self.coordinator.config["name"],
            "firmware": state.firmware,
            "blind_mode": state.blind_mode,
            "position_source": "module_time_estimate_not_encoder",
            "relay_up": bool(state.outputs & 1),
            "relay_down": bool(state.outputs & 2),
            "output_conflict": state.outputs == 3,
            "local_inputs": state.inputs,
            "bus_override": bool(state.manual & 1),
            "tilt_step_seconds": state.step_seconds,
            "travel_seconds": state.travel_seconds,
            "reversal_delay_seconds": state.reversal_seconds,
            "read_only": not self.coordinator.config["allow_commands"],
            "last_error": self.record.error,
        }
