"""Offline protocol tests. Run: python3 -m unittest discover -s tests -v."""
import importlib.util
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).parents[1] / "custom_components/domat_mw240/engine.py"
if PATH.exists():
    spec = importlib.util.spec_from_file_location("mw240_engine", PATH)
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
else:
    engine = None


def registers(firmware=17):
    values = [0] * 20
    values[0] = 0x0A24
    values[1] = firmware
    values[3] = 0x0E17
    values[4] = 0x8300
    values[17] = 0x0C00
    values[18] = 0x051E
    values[19] = 0x0702
    return values


class DecodeTests(unittest.TestCase):
    def test_live_fw17_snapshot_is_decoded_without_claiming_tilt_feedback(self):
        self.assertIsNotNone(engine, "Missing bus engine")
        state = engine.decode(registers())
        self.assertEqual(state.firmware, 17)
        self.assertTrue(state.blind_mode)
        self.assertEqual(state.position, 100)  # HA convention: open
        self.assertIsNone(state.tilt_position)
        self.assertEqual(state.rotation_seconds, 1.2)
        self.assertEqual(state.step_seconds, 0.2)
        self.assertEqual(state.travel_seconds, 30)
        self.assertEqual(state.reversal_seconds, 0.7)


class FakeTransport:
    def __init__(self, firmware=17):
        self.words = registers(firmware)
        self.writes = []
        self.reads = []

    async def read(self, unit, address, count):
        self.reads.append((unit, address, count))
        return self.words[address:address + count]

    async def write(self, unit, address, values):
        self.writes.append((unit, address, list(values)))
        old = self.words[address]
        self.words[address:address + len(values)] = values
        if address == 16:
            value = values[0]
            # Model only documented command edges, not physical blind movement.
            if value & 2:
                self.words[12] = 0
            elif value & 12:
                self.words[12] = 1 if value & 4 else 2
            elif value & 1 and (not old & 1 or old >> 8 != value >> 8):
                self.words[12] = 1 if value >> 8 == 0 else 2
            self.words[16] &= ~14  # self-clearing action bits


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_position_target_is_retriggered_and_local_override_released(self):
        transport = FakeTransport()
        transport.words[15] = 50
        transport.words[16] = 0  # local movement interrupted an earlier OPEN
        self.assertTrue(hasattr(engine, "Controller"), "Missing command controller")
        controller = engine.Controller(transport, settle_seconds=0)
        state = await controller.command(23, "position", 100)
        self.assertEqual(transport.writes, [
            (23, 16, [3]),  # STOP, no spurious endpoint synchronization
            (23, 16, [0]),  # stage target with bus override disabled
            (23, 16, [1]),  # rising edge: repeated OPEN still works
            (23, 16, [0]),  # restore local control, movement continues
        ])
        self.assertEqual(state.outputs, 1)
        self.assertEqual(state.manual & 1, 0)

    async def test_tilt_steps_stop_and_return_control_without_changing_position_target(self):
        for kind, action in [("tilt_up", 5), ("tilt_down", 9), ("stop", None)]:
            with self.subTest(kind=kind):
                transport = FakeTransport()
                transport.words[16] = 0x3230  # preserve target and reserved low bits
                controller = engine.Controller(transport, settle_seconds=0)
                await controller.command(23, kind)
                expected = [(23, 16, [0x3233])]
                if action is not None:
                    expected.append((23, 16, [0x3230 | action]))
                expected.append((23, 16, [0x3230]))
                self.assertEqual(transport.writes, expected)

    async def test_invalid_uncommissioned_or_locally_active_requests_cannot_write(self):
        cases = [(0, "position", 50, None), (248, "stop", None, None),
                 (23, "position", True, None), (23, "position", 101, None),
                 (23, "unknown", None, None), (23, "position", 50, (4, 0x0300)),
                 (23, "position", 50, (1, 13)), (23, "position", 50, (0, 0x0A25)),
                 (23, "position", 50, (11, 1)), (23, "position", 50, (12, 3))]
        for unit, kind, value, change in cases:
            with self.subTest(unit=unit, kind=kind, value=value, change=change):
                transport = FakeTransport()
                if change:
                    transport.words[change[0]] = change[1]
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport, settle_seconds=0).command(unit, kind, value)
                self.assertEqual(transport.writes, [])

    async def test_lost_write_ack_is_not_replayed_and_best_effort_stop_is_sent(self):
        class LostAck(FakeTransport):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if values == [1] and not hasattr(self, "failed"):
                    self.failed = True
                    raise engine.DeviceError("Simulated lost enable acknowledgement")
        transport = LostAck()
        with self.assertRaises(engine.DeviceError):
            await engine.Controller(transport, settle_seconds=0).command(23, "position", 100)
        self.assertEqual(transport.writes.count((23, 16, [1])), 1)
        self.assertEqual(transport.words[12] & 3, 0, "Ambiguous write needs best-effort STOP")
        self.assertEqual(transport.words[16] & 1, 0)

    async def test_acknowledged_but_ignored_release_is_reported_as_failure(self):
        class IgnoreRelease(FakeTransport):
            async def write(self, unit, address, values):
                if self.writes and values[0] & 1 == 0:
                    self.writes.append((unit, address, list(values)))
                    return
                await super().write(unit, address, values)
        with self.assertRaises(engine.DeviceError):
            await engine.Controller(IgnoreRelease(), settle_seconds=0).command(23, "tilt_down")

    async def test_idle_stop_bit_need_not_self_clear_on_firmware17(self):
        class StickyIdleStop(FakeTransport):
            async def write(self, unit, address, values):
                idle = self.words[12] & 3 == 0
                await super().write(unit, address, values)
                if idle and address == 16 and values[0] & 2:
                    self.words[16] |= 2  # observed on real FW17, even after 1.8 seconds
        transport = StickyIdleStop()
        state = await engine.Controller(transport, settle_seconds=0).command(23, "position", 90)
        self.assertEqual(state.outputs, 2)
        self.assertEqual(state.manual & 1, 0)

    async def test_poll_never_writes(self):
        transport = FakeTransport()
        await engine.Controller(transport).poll(23)
        self.assertEqual(transport.writes, [])


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fw17_travel_time_update_preserves_button_threshold_and_uses_ram_only(self):
        transport = FakeTransport()
        state = await engine.Controller(transport).command(23, "travel_times", {"down": 45})
        self.assertEqual(transport.writes, [(23, 18, [0x052D])])
        self.assertEqual(state.travel_seconds, 45)
        self.assertEqual(transport.words[2], 0, "Never enable EEPROM or reset")
        self.assertEqual(transport.words[16], 0, "Do not take motor override")

    async def test_fw21_separate_up_time_preserves_post_rotation_byte(self):
        transport = FakeTransport(firmware=21)
        transport.words.extend([0] * 7)
        transport.words[26] = 0x1E23  # post rotation 30%, up time 35 seconds
        controller = engine.Controller(transport)
        before = await controller.poll(23)
        self.assertEqual(getattr(before, "travel_up_seconds", None), 35)
        state = await controller.command(23, "travel_times", {"down": 40, "up": 50})
        self.assertEqual(transport.writes, [(23, 18, [0x0528]), (23, 26, [0x1E32])])
        self.assertEqual(state.travel_seconds, 40)
        self.assertEqual(state.travel_up_seconds, 50)

    async def test_late_local_movement_or_eeprom_arming_blocks_the_write(self):
        for index, value in [(12, 2), (11, 1), (2, 0x0200)]:
            with self.subTest(index=index):
                class ChangedAfterPreflight(FakeTransport):
                    async def read(self, unit, address, count):
                        result = await super().read(unit, address, count)
                        if address == 18 and count == 1:
                            self.words[index] = value
                        return result
                transport = ChangedAfterPreflight()
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport).command(23, "travel_times", {"down": 45})
                self.assertEqual(transport.writes, [])

    async def test_unsafe_timing_changes_never_write(self):
        for change in [(12, 1), (12, 2), (11, 1), (16, 1), (16, 2), (2, 1), (2, 0x0200)]:
            with self.subTest(change=change):
                transport = FakeTransport()
                transport.words[change[0]] = change[1]
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport).command(23, "travel_times", {"down": 45})
                self.assertEqual(transport.writes, [])
        for value in [{}, {"down": 0}, {"down": 256}, {"down": True}, {"down": 1.5}, {"up": 35}, {"address": 1}, None]:
            with self.subTest(value=value):
                transport = FakeTransport()
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport).command(23, "travel_times", value)
                self.assertEqual(transport.writes, [])


class BusTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_that_expires_during_connect_is_not_transmitted(self):
        import asyncio
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowConnect(FakeTransport):
            async def connect(self):
                entered.set()
                await release.wait()
        transport = SlowConnect()
        bus = engine.Bus(engine.Controller(transport, settle_seconds=0), command_ttl=0.02)
        bus.start()
        job = asyncio.create_task(bus.submit(23, "position", 0))
        await entered.wait()
        await asyncio.sleep(0.03)
        release.set()
        try:
            with self.assertRaisesRegex(engine.DeviceError, "expired"):
                await job
            self.assertEqual(transport.reads, [])
            self.assertEqual(transport.writes, [])
        finally:
            await bus.close()

    async def test_cancel_or_shutdown_during_connect_cannot_dispatch_a_new_motor_action(self):
        import asyncio
        assert engine is not None
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                entered, release = asyncio.Event(), asyncio.Event()
                class PausedConnect(FakeTransport):
                    async def connect(self):
                        entered.set()
                        await release.wait()
                transport = PausedConnect()
                bus = engine.Bus(engine.Controller(transport, settle_seconds=0))
                bus.start()
                job = asyncio.create_task(bus.submit(23, "position", 0))
                await entered.wait()
                if cancel:
                    job.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await job
                    release.set()
                    try:
                        await bus.submit(23, "poll")
                        self.assertEqual(transport.writes, [])
                    finally:
                        await bus.close()
                else:
                    closing = asyncio.create_task(bus.close())
                    await asyncio.sleep(0)
                    release.set()
                    try:
                        with self.assertRaises(engine.DeviceError):
                            await job
                        self.assertEqual(transport.writes, [])
                    finally:
                        await closing

    async def test_twenty_devices_share_a_worker_and_stop_cancels_queued_movement(self):
        import asyncio
        self.assertTrue(hasattr(engine, "Bus"), "Missing serialized bus worker")
        gate = asyncio.Event()
        entered = asyncio.Event()
        transport = FakeTransport()
        original_read = transport.read
        order = []

        async def read(unit, address, count):
            order.append(unit)
            if not entered.is_set():
                entered.set()
                await gate.wait()
            return await original_read(unit, address, count)

        transport.read = read
        bus = engine.Bus(engine.Controller(transport, settle_seconds=0))
        bus.start()
        first = asyncio.create_task(bus.submit(1, "poll"))
        await entered.wait()
        polls = [asyncio.create_task(bus.submit(unit, "poll")) for unit in range(2, 21)]
        movement = asyncio.create_task(bus.submit(23, "position", 0))
        await asyncio.sleep(0)
        stop = asyncio.create_task(bus.submit(23, "stop"))
        await asyncio.sleep(0)
        gate.set()
        await first
        await stop
        with self.assertRaises(engine.DeviceError):
            await movement
        states = await asyncio.gather(*polls)
        self.assertEqual(len(states) + 1, 20)
        self.assertEqual(order[:2], [1, 23])
        self.assertTrue(all(values[0] & 3 != 1 for _, _, values in transport.writes))
        await bus.close()
        with self.assertRaises(engine.DeviceError):
            await bus.submit(23, "position", 100)


if __name__ == "__main__":
    unittest.main()
