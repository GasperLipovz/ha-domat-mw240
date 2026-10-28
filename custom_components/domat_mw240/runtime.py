"""HA coordinator and pymodbus adapter; one worker and client per serial line."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pymodbus.client import AsyncModbusSerialClient

from .engine import Bus, Controller, DeviceError, Snapshot

_LOGGER = logging.getLogger(__name__)


class SerialTransport:
    def __init__(self, config):
        self.client = AsyncModbusSerialClient(
            config["port"], baudrate=config["baudrate"], bytesize=8,
            parity=config["parity"], stopbits=config["stopbits"],
            timeout=config["timeout"], retries=0, reconnect_delay=0,
        )

    async def connect(self):
        if not self.client.connected and not await self.client.connect():
            raise DeviceError("Cannot open serial adapter")

    async def read(self, unit, address, count):
        if not self.client.connected:
            raise DeviceError("Serial disconnected; sequence will not reconnect/replay")
        response = await self.client.read_holding_registers(address, count=count, device_id=unit)
        if response.isError() or len(response.registers) != count:
            raise DeviceError(f"FC03 failed for unit {unit}: {response}")
        return response.registers

    async def write(self, unit, address, values):
        if not self.client.connected:
            raise DeviceError("Serial disconnected; write not replayed")
        response = await self.client.write_registers(address, values, device_id=unit)
        if response.isError() or response.address != address or response.count != len(values):
            raise DeviceError(f"FC16 failed for unit {unit}: {response}")

    def close(self):
        self.client.close()


@dataclass(frozen=True)
class Record:
    snapshot: Snapshot | None = None
    available: bool = False
    error: str | None = None
    last_seen: float = 0


class Hub(DataUpdateCoordinator):
    def __init__(self, hass, config, *, transport=None, config_entry=None):
        super().__init__(hass, _LOGGER, config_entry=config_entry,
                         name=f"domat_mw240_{config['name']}",
                         update_interval=timedelta(seconds=config["poll_interval"]))
        self.config = config
        self.transport = transport if transport is not None else SerialTransport(config)
        self.bus = Bus(Controller(self.transport), command_ttl=config["command_ttl"])
        self.records = {device["id"]: Record() for device in config["devices"]}
        self.pending_timings = {}
        self._eeprom_confirmations = {}
        self.eeprom_results = {}
        self._failures = {}
        self._next_poll = {}
        self._closed = False

    @property
    def availability_timeout(self):
        """Allow a worst-case two-read polling sweep, not a fixed per-node age."""
        return max(10, self.config["poll_interval"] * 3,
                   len(self.records) * (2 * self.config["timeout"] + 0.1)
                   + 2 * self.config["poll_interval"])

    async def _async_update_data(self):
        for unit in self.records:
            if self._closed:
                break
            if time.monotonic() < self._next_poll.get(unit, 0):
                continue
            try:
                snapshot = await self.bus.submit(unit, "poll")
                self.records[unit] = Record(snapshot, True, None, time.monotonic())
                self._failures.pop(unit, None)
                self._next_poll.pop(unit, None)
            except Exception as err:
                failures = min(self._failures.get(unit, 0) + 1, 6)
                self._failures[unit] = failures
                self._next_poll[unit] = time.monotonic() + min(60, 2 ** failures)
                self.records[unit] = Record(self.records[unit].snapshot, False, str(err))
                if failures == 1:
                    _LOGGER.warning("Bus %s, module %s unavailable: %s", self.config["name"], unit, err)
        return dict(self.records)

    async def async_command(self, unit, kind, value=None):
        if kind not in {"position", "stop", "tilt_up", "tilt_down"}:
            raise HomeAssistantError("Motor commands cannot bypass the separate timing-apply permission")
        if self._closed or not self.config["allow_commands"]:
            raise HomeAssistantError("Domat bus is read-only; enable allow_commands only after commissioning")
        if unit not in self.records:
            raise HomeAssistantError("Module is not configured on this bus")
        self._eeprom_confirmations.pop(unit, None)
        await self._async_execute(unit, kind, value)

    def stage_travel_time(self, unit, direction, value):
        if self._closed or unit not in self.records:
            raise HomeAssistantError("Module is not configured or bus is closed")
        if direction not in {"down", "up"}:
            raise HomeAssistantError("Unknown travel-time direction")
        if (type(value) not in (int, float) or not 1 <= value <= 255 or value != int(value)):
            raise HomeAssistantError("Travel time must be a whole number of seconds, 1..255")
        state = self.records[unit].snapshot
        if direction == "up" and (state is None or state.firmware < 21):
            raise HomeAssistantError("Separate upward travel time requires firmware 21+")
        self._eeprom_confirmations.pop(unit, None)
        self.pending_timings.setdefault(unit, {})[direction] = int(value)
        self.async_set_updated_data(dict(self.records))

    async def async_apply_travel_times(self, unit):
        if self._closed or not self.config["allow_timing_changes"]:
            raise HomeAssistantError("Timing writes disabled; explicitly enable allow_timing_changes first")
        if unit not in self.records:
            raise HomeAssistantError("Module is not configured on this bus")
        values = dict(self.pending_timings.get(unit, {}))
        if not values:
            raise HomeAssistantError("No pending travel-time changes")
        self._eeprom_confirmations.pop(unit, None)
        await self._async_execute(unit, "travel_times", values)
        # Preserve any newer draft entered while this request was in flight.
        pending = self.pending_timings.get(unit, {})
        for direction, value in values.items():
            if pending.get(direction) == value:
                pending.pop(direction)
        self.async_set_updated_data(dict(self.records))

    def _check_eeprom_permission(self, unit):
        if self._closed or not self.config.get("allow_eeprom_save", False):
            raise HomeAssistantError("Permanent saving disabled; enable the separate EEPROM permission first")
        if unit not in self.records:
            raise HomeAssistantError("Module is not configured on this bus")
        if self.pending_timings.get(unit):
            raise HomeAssistantError("Apply or discard pending travel-time drafts before preparing an EEPROM save")

    def eeprom_prepared(self, unit):
        prepared = self._eeprom_confirmations.get(unit)
        return bool(prepared and time.monotonic() < prepared[1])

    async def async_prepare_eeprom(self, unit):
        """Read and capture the entire known configuration; no device writes."""
        from .engine import eeprom_fingerprint

        self._eeprom_confirmations.pop(unit, None)
        self._check_eeprom_permission(unit)
        try:
            snapshot = await self.bus.submit(unit, "poll")
            fingerprint = eeprom_fingerprint(snapshot)
        except Exception as err:
            raise HomeAssistantError(f"EEPROM preparation refused: {err}") from err
        # Drafts or permissions may have changed while waiting for the bus.
        self._check_eeprom_permission(unit)
        self.records[unit] = Record(snapshot, True, None, time.monotonic())
        self._eeprom_confirmations[unit] = (fingerprint, time.monotonic() + 60)
        self.eeprom_results[unit] = "prepared_all_RAM_settings_review_before_save"
        self.async_set_updated_data(dict(self.records))

    async def async_save_eeprom(self, unit):
        """Consume a short-lived preparation exactly once, including on failure."""
        prepared = self._eeprom_confirmations.pop(unit, None)
        self._check_eeprom_permission(unit)
        if not prepared or time.monotonic() >= prepared[1]:
            raise HomeAssistantError("Prepare this module's EEPROM save first; confirmation expires after 60 seconds")
        try:
            await self._async_execute(unit, "save_eeprom", prepared[0])
        except BaseException:
            self.eeprom_results[unit] = "failed_commit_may_be_uncertain_inspect_before_retry"
            self.async_set_updated_data(dict(self.records))
            raise
        self.eeprom_results[unit] = "save_status_confirmed_power_cycle_not_checked"
        self.async_set_updated_data(dict(self.records))

    async def _async_execute(self, unit, kind, value):
        try:
            snapshot = await self.bus.submit(unit, kind, value)
        except Exception as err:
            self.records[unit] = Record(self.records[unit].snapshot, False, f"Command failed: {err}")
            self.async_set_updated_data(dict(self.records))
            detail = ("EEPROM commit may be uncertain; do not retry automatically; inspect status and settings"
                      if kind == "save_eeprom" else
                      "some timing fields may have changed; refresh before retrying"
                      if kind == "travel_times" else "motion/override state may be uncertain")
            raise HomeAssistantError(f"MW240 module {unit}: {err}; {detail}") from err
        self.records[unit] = Record(snapshot, True, None, time.monotonic())
        self._failures.pop(unit, None)
        self._next_poll.pop(unit, None)
        self.async_set_updated_data(dict(self.records))

    async def async_close(self):
        if self._closed:
            return
        self._closed = True
        self._eeprom_confirmations.clear()
        await self.async_shutdown()
        await self.bus.close()
        close = getattr(self.transport, "close", None)
        if close:
            close()
