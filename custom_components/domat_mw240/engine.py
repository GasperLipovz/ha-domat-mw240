"""Device protocol, independent of Home Assistant. Addresses are zero-based."""
from __future__ import annotations

from dataclasses import dataclass, replace


class DeviceError(Exception):
    """Invalid device, unsafe configuration or failed transaction."""


@dataclass(frozen=True)
class Snapshot:
    firmware: int
    blind_mode: bool
    position: int | None
    tilt_position: int | None
    outputs: int
    inputs: int
    manual: int
    rotation_seconds: float
    step_seconds: float
    travel_seconds: int
    reversal_seconds: float
    raw: tuple[int, ...]
    travel_up_seconds: int | None = None


def decode(words: list[int], *, tilt_inverted: bool = True) -> Snapshot:
    """Decode manufacturer registers 1..20; device estimates, not encoders."""
    if len(words) != 20 or any(type(v) is not int or not 0 <= v <= 65535 for v in words):
        raise DeviceError("Expected twenty uint16 registers")
    if words[0] != 0x0A24:
        raise DeviceError(f"Not an MW240: identity 0x{words[0]:04x}")
    firmware = words[1] & 255
    raw_position = words[15] & 255
    raw_tilt = words[15] >> 8
    return Snapshot(
        firmware=firmware,
        blind_mode=bool(words[4] & 0x8000),
        position=100 - raw_position if raw_position <= 100 else None,
        tilt_position=(100 - raw_tilt if tilt_inverted else raw_tilt)
        if firmware >= 21 and raw_tilt <= 100 else None,
        outputs=words[12] & 3,
        inputs=words[11] & 3,
        manual=words[16],
        rotation_seconds=(words[17] >> 8) / 10,
        step_seconds=(words[19] & 255) / 10,
        travel_seconds=words[18] & 255,
        reversal_seconds=(words[19] >> 8) / 10,
        raw=tuple(words),
    )


# Manufacturer registers 4..9 and 18..20; never include live command registers.
EEPROM_CONFIG_OFFSETS = (3, 4, 5, 6, 7, 8, 17, 18, 19)


def _eeprom_fingerprint(state: Snapshot, *, status: int) -> tuple[int, ...]:
    # Validate raw identity/shape as well as decoded fields. Snapshot is public.
    state = decode(list(state.raw))
    if state.firmware != 17 or not state.blind_mode:
        raise DeviceError("EEPROM save requires commissioned blind mode and exact firmware 17")
    if state.outputs or state.inputs or state.manual & 15:
        raise DeviceError("EEPROM save requires idle outputs, inputs and manual/action flags")
    if state.raw[2] != status:
        raise DeviceError(f"Unexpected EEPROM status 0x{state.raw[2]:04x}; expected 0x{status:04x}")
    if not 1 <= state.travel_seconds <= 255:
        raise DeviceError("EEPROM travel time must be 1..255 seconds")
    if state.raw[17] & 255:
        raise DeviceError("EEPROM save requires no configured startup action (tested FW17 scope)")
    return tuple(state.raw[index] for index in EEPROM_CONFIG_OFFSETS)


def eeprom_fingerprint(state: Snapshot) -> tuple[int, ...]:
    """Read-only FW17 preparation guard; fingerprint all nine persistent words.

    Requires normal status 0x8000 (no arm, pending, INIT or reset functions).
    This is not authorization: callers must explicitly permit saving, expire and
    consume the prepared tuple once, then submit save_eeprom through Bus.
    """
    return _eeprom_fingerprint(state, status=0x8000)


class Controller:
    """One command sequence. Caller must serialize the entire sequence per bus."""

    eeprom_fingerprint = staticmethod(eeprom_fingerprint)

    def __init__(self, transport, *, settle_seconds: float = 0.05):
        self.transport = transport
        self.settle_seconds = settle_seconds
        # Unverified disarm is not fixed by a later normal poll. No automatic
        # latch reset: inspect/recover the module manually before recreating the
        # controller. Existing live status guards still apply after recreation.
        self._eeprom_blocked: set[int] = set()

    async def poll(self, unit: int, *, tilt_inverted: bool = True) -> Snapshot:
        state = decode(await self.transport.read(unit, 0, 20), tilt_inverted=tilt_inverted)
        if state.firmware >= 21:
            words = await self.transport.read(unit, 26, 1)
            if len(words) != 1 or type(words[0]) is not int or not 0 <= words[0] <= 65535:
                raise DeviceError("Invalid upward travel-time response")
            state = replace(state, travel_up_seconds=words[0] & 255)
        return state

    async def _consumed(self, unit: int) -> int:
        import asyncio
        for _ in range(20):
            value = (await self.transport.read(unit, 16, 1))[0]
            if not value & 14:
                return value
            await asyncio.sleep(0.025)
        raise DeviceError("Device did not consume action bits; command not replayed")

    async def _stopped(self, unit: int):
        import asyncio
        # FW17 leaves STOP set indefinitely if the outputs were already idle.
        # Output readback, not self-clearing STOP, is the completion criterion.
        for _ in range(20):
            if (await self.transport.read(unit, 12, 1))[0] & 3 == 0:
                return
            await asyncio.sleep(0.025)
        raise DeviceError("Outputs did not stop; refusing the next motor command")

    async def _set_travel_times(self, unit: int, values: dict, state: Snapshot) -> Snapshot:
        """Update working RAM only. No EEPROM enable, reset, or motor commands."""
        if not isinstance(values, dict) or not values or set(values) - {"down", "up"}:
            raise DeviceError("Expected down/shared and/or up travel times")
        if "up" in values and state.firmware < 21:
            raise DeviceError("Separate upward travel time requires firmware 21+")
        if any(type(seconds) is not int or not 1 <= seconds <= 255 for seconds in values.values()):
            raise DeviceError("Travel time must be a whole number of seconds, 1..255")
        if state.outputs or state.inputs or state.manual & 15:
            raise DeviceError("Travel times can only change with idle outputs, inputs and bus override")
        if state.raw[2] & 0x0201:
            raise DeviceError("EEPROM save is armed; refusing a RAM-only timing write")
        for direction, address in (("down", 18), ("up", 26)):
            if direction not in values:
                continue
            current = (await self.transport.read(unit, address, 1))[0]
            new_word = (current & 0xFF00) | values[direction]
            if current != new_word:
                # Check again immediately before each field, not just when queued.
                guard = await self.transport.read(unit, 2, 15)
                if guard[0] & 0x0201 or guard[9] & 3 or guard[10] & 3 or guard[14] & 15:
                    raise DeviceError("Module no longer idle or EEPROM save armed; timing write refused")
                await self.transport.write(unit, address, [new_word])
                if (await self.transport.read(unit, address, 1))[0] != new_word:
                    raise DeviceError("Travel time write was not confirmed; earlier fields may have changed")
        return await self.poll(unit)

    async def _eeprom_status(self, unit: int) -> int:
        words = await self.transport.read(unit, 2, 1)
        if len(words) != 1 or type(words[0]) is not int or not 0 <= words[0] <= 65535:
            raise DeviceError("Invalid EEPROM status response")
        return words[0]

    async def _disarm_eeprom(self, unit: int):
        """One safe cleanup attempt. Never echo unknown status/function bits."""
        import asyncio
        async with asyncio.timeout(2.0):
            status = await self._eeprom_status(unit)
            if status not in {0x8000, 0x8001, 0x8200, 0x8201}:
                raise DeviceError(f"Unsafe status 0x{status:04x}; no disarm write sent")
            await self.transport.write(unit, 2, [0x8000])
            if await self._eeprom_status(unit) != 0x8000:
                raise DeviceError("EEPROM pending/enable remains set or status abnormal after disarm")

    async def _save_eeprom(self, unit: int, expected: tuple[int, ...], state: Snapshot) -> Snapshot:
        """Commit the prepared RAM configuration, not a draft. No trigger retries."""
        import asyncio
        if (type(expected) is not tuple or len(expected) != len(EEPROM_CONFIG_OFFSETS)
                or any(type(word) is not int or not 0 <= word <= 65535 for word in expected)):
            raise DeviceError("Expected a prepared nine-word EEPROM configuration tuple")
        if eeprom_fingerprint(state) != expected:
            raise DeviceError("Configuration changed since preparation; prepare again")
        disarm_attempted = False
        disarm_verified = False
        stage = "arming"
        try:
            # An exception can mean the write executed but its ACK was lost.
            await self.transport.write(unit, 2, [0x8001])
            if await self._eeprom_status(unit) != 0x8200:
                raise DeviceError("EEPROM arm not confirmed as 0x8200; trigger refused")
            guard = await self.poll(unit)
            if _eeprom_fingerprint(guard, status=0x8200) != expected:
                raise DeviceError("Configuration changed after arming; trigger refused")
            stage = "triggering"
            await self.transport.write(unit, 18, [expected[7]])
            stage = "waiting for completion"
            # 0.6s is observed, not a firmware deadline guarantee. Bound even I/O.
            async with asyncio.timeout(2.0):
                while True:
                    status = await self._eeprom_status(unit)
                    if status == 0x8000:
                        break
                    if status != 0x8200:
                        raise DeviceError(f"Unexpected EEPROM completion status 0x{status:04x}")
                    await asyncio.sleep(0.1)
            stage = "disarming"
            disarm_attempted = True
            await self._disarm_eeprom(unit)
            disarm_verified = True
            stage = "checking final configuration"
            result = await self.poll(unit)
            if result.raw[2] != 0x8000:
                # A later full snapshot supersedes the scalar disarm readback.
                # Do not attempt another disarm or configuration trigger.
                disarm_verified = False
            if eeprom_fingerprint(result) != expected:
                raise DeviceError("Configuration changed during EEPROM save")
            return result
        except (Exception, asyncio.CancelledError) as err:
            recovery = ""
            if not disarm_attempted:
                try:
                    disarm_attempted = True
                    await self._disarm_eeprom(unit)
                    disarm_verified = True
                except (Exception, asyncio.CancelledError) as cleanup_err:
                    recovery = f" ({type(cleanup_err).__name__}: {cleanup_err})"
            if not disarm_verified:
                self._eeprom_blocked.add(unit)
            detail = ("disarm verified" if disarm_verified else
                      "manual recovery required; configuration writes blocked" + recovery)
            # Convert transport/task cancellation into a failed command, not a
            # cancelled Bus worker. Never replay an ambiguous arm or trigger.
            raise DeviceError(
                f"EEPROM save failed while {stage}; persistence uncertain; {detail}; "
                f"{type(err).__name__}: {err}"
            ) from err

    async def command(self, unit: int, kind: str, value: int | dict | tuple[int, ...] | None = None) -> Snapshot:
        import asyncio
        if type(unit) is not int or not 1 <= unit <= 247:
            raise DeviceError("Unicast Modbus address must be 1..247")
        if kind in {"save_eeprom", "travel_times"} and unit in self._eeprom_blocked:
            raise DeviceError("EEPROM disarm unverified; manual recovery required before configuration writes")
        state = await self.poll(unit)
        if kind == "save_eeprom":
            return await self._save_eeprom(unit, value, state)
        if not state.blind_mode or state.firmware < 14:
            raise DeviceError("Commission blind mode and firmware >=14 before motor commands")
        if kind == "travel_times":
            return await self._set_travel_times(unit, value, state)
        if kind not in {"position", "stop", "tilt_up", "tilt_down"}:
            raise DeviceError("Unknown motor command")
        if kind == "position" and (type(value) is not int or not 0 <= value <= 100):
            raise DeviceError("Expected HA position 0..100")
        if kind != "stop" and (state.inputs or state.outputs == 3):
            raise DeviceError("Local button active or conflicting output feedback; only STOP permitted")
        reserved = state.manual & 0xF0
        release = (state.manual & 0xFF00) | reserved
        try:
            await self.transport.write(unit, 16, [release | 3])
            await self._stopped(unit)
            if kind == "position":
                release = ((100 - value) << 8) | reserved
                await self.transport.write(unit, 16, [release])
                await self.transport.write(unit, 16, [release | 1])
                await asyncio.sleep(self.settle_seconds)
            elif kind in {"tilt_up", "tilt_down"}:
                action = 5 if kind == "tilt_up" else 9
                await self.transport.write(unit, 16, [release | action])
                await self._consumed(unit)
        except BaseException:
            # A lost reply may mean the motor command executed. Do not replay it.
            # If the link still works, stop before handing control back locally.
            try:
                await self.transport.write(unit, 16, [release | 3])
                await self._stopped(unit)
            except Exception:
                pass  # A broken wire cannot be made safe by a software retry.
            raise
        finally:
            await self.transport.write(unit, 16, [release])
        result = await self.poll(unit)
        if result.manual & 1:
            raise DeviceError("Bus override release not confirmed; check local control")
        if kind == "stop" and result.outputs:
            raise DeviceError("STOP acknowledged but output remains active (check local buttons)")
        return result


class Bus:
    """A single transient, priority-ordered worker for the entire serial line.

    Commands are never persisted or retried. STOP cancels queued commands for
    its module and runs ahead of movement and polling, not in parallel with I/O.
    """

    def __init__(self, controller: Controller, *, command_ttl: float = 5.0, max_pending: int = 64):
        import asyncio
        self.controller = controller
        self.command_ttl = command_ttl
        self.max_pending = max_pending
        self._queue = asyncio.PriorityQueue()
        self._pending = {}
        self._sequence = 0
        self._closed = False
        self._task = None

    def start(self):
        import asyncio
        if self._closed or self._task is not None:
            raise DeviceError("Bus already started or closed")
        self._task = asyncio.create_task(self._run(), name="domat-mw240-bus")

    async def submit(self, unit: int, kind: str, value: int | dict | tuple[int, ...] | None = None) -> Snapshot:
        import asyncio
        loop = asyncio.get_running_loop()
        if self._closed or self._task is None:
            raise DeviceError("Bus is not running")
        if type(unit) is not int or not 1 <= unit <= 247:
            raise DeviceError("Unicast Modbus address must be 1..247")
        if kind == "stop":
            for old_unit, old_kind, old_future in tuple(self._pending.values()):
                if old_unit == unit and old_kind != "poll" and not old_future.done():
                    old_future.set_exception(DeviceError("Superseded by STOP"))
        elif len(self._pending) >= self.max_pending:
            raise DeviceError("Bus queue full; command rejected, not delayed")
        self._sequence += 1
        seq = self._sequence
        future = loop.create_future()
        self._pending[seq] = (unit, kind, future)
        priority = 0 if kind == "stop" else 10 if kind == "poll" else 1
        deadline = loop.time() + self.command_ttl if kind != "poll" else float("inf")
        self._queue.put_nowait((priority, seq, deadline, unit, kind, value, future))
        return await future

    async def _run(self):
        import asyncio
        while True:
            item = await self._queue.get()
            _, seq, deadline, unit, kind, value, future = item
            self._pending.pop(seq, None)
            if self._closed:
                self._queue.task_done()
                break
            try:
                if future.done():
                    continue
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError("Command expired in queue; not transmitted")
                connect = getattr(self.controller.transport, "connect", None)
                if connect is not None:
                    await connect()
                if kind == "poll":
                    result = await self.controller.poll(unit)
                else:
                    result = await self.controller.command(unit, kind, value)
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError as err:
                # EEPROM's armed path handles cleanup itself. A cancellation in
                # connect/preflight must likewise fail its future, not orphan it
                # and kill the only worker for every other module on the line.
                if kind != "save_eeprom":
                    raise
                if not future.done():
                    future.set_exception(DeviceError(
                        f"EEPROM operation cancelled before arming; no save replayed: {err}"
                    ))
            except Exception as err:
                if not future.done():
                    future.set_exception(err)
            finally:
                self._queue.task_done()

    async def close(self):
        """Finish an in-flight sequence, discard pending work, send no new motor action."""
        if self._closed:
            return
        self._closed = True
        for _, _, future in self._pending.values():
            if not future.done():
                future.set_exception(DeviceError("Bus stopped; queued work discarded"))
        self._pending.clear()
        # Wake an idle worker; shutdown is not a device command.
        self._queue.put_nowait((-1, -1, 0, 0, "shutdown", None, None))
        if self._task is not None:
            await self._task
