"""Simulated offline FW17 EEPROM protocol tests; not hardware evidence."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).parents[1] / "custom_components/domat_mw240/engine.py"
spec = importlib.util.spec_from_file_location("mw240_eeprom_engine", PATH)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)


def registers():
    """Synthetic idle fixture matching the recorded FW17 configuration shape."""
    return [0x0A24, 17, 0x8000, 0x0E17, 0x8300, 0x0A00,
            0, 0, 0, 0, 0, 0, 0, 7, 19, 7, 0, 0x0C00, 0x0514, 0x0702]


class FingerprintTests(unittest.TestCase):
    def test_read_only_fingerprint_covers_all_nine_persistent_words(self):
        words = registers()
        self.assertTrue(callable(getattr(engine, "eeprom_fingerprint", None)),
                        "Missing read-only EEPROM preparation API")
        expected = (0x0E17, 0x8300, 0x0A00, 0, 0, 0, 0x0C00, 0x0514, 0x0702)
        self.assertEqual(engine.eeprom_fingerprint(engine.decode(words)), expected)
        self.assertEqual(engine.Controller.eeprom_fingerprint(engine.decode(words)), expected)
        self.assertEqual(words, registers())

    def test_fingerprint_refuses_outside_proven_idle_fw17_scope(self):
        for index, value in [(1, 16), (1, 18), (1, 21), (4, 0x0300),
                             (11, 1), (11, 2), (12, 1), (12, 2), (12, 3),
                             (16, 1), (16, 2), (16, 4), (16, 8),
                             (2, 0), (2, 0x8001), (2, 0x8200), (2, 0x8010),
                             (2, 0x8002), (2, 0x8004), (2, 0xC000),
                             (18, 0x0500), (17, 0x0C01)]:
            with self.subTest(index=index, value=value):
                words = registers()
                words[index] = value
                with self.assertRaises(engine.DeviceError):
                    engine.eeprom_fingerprint(engine.decode(words))


class SimulatedEEPROM:
    """Protocol-only state machine; does not model flash persistence or motors."""

    def __init__(self):
        self.words = registers()
        self.writes = []
        self.events = []
        self.pending_reads = 0
        self.committed = False

    async def read(self, unit, address, count):
        self.events.append(("read", unit, address, count))
        if address == 2 and count == 1 and self.committed:
            if self.pending_reads:
                self.pending_reads -= 1
            else:
                self.words[2] = 0x8000
        return self.words[address:address + count]

    async def write(self, unit, address, values):
        values = list(values)
        self.events.append(("write", unit, address, values))
        self.writes.append((unit, address, values))
        if address == 2 and values == [0x8001]:
            self.words[2] = 0x8200  # enable self-clears; save-pending set
        elif address == 2 and values == [0x8000]:
            self.words[2] = 0x8000
        elif address == 18:
            self.words[18] = values[0]
            self.committed = True
        else:
            raise AssertionError("Forbidden write in simulated EEPROM protocol")


class SaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_serialized_save_triggers_once_with_unchanged_word_then_disarms(self):
        transport = SimulatedEEPROM()
        transport.pending_reads = 1
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        bus = engine.Bus(engine.Controller(transport))
        bus.start()
        try:
            result = await bus.submit(23, "save_eeprom", expected)
        finally:
            await bus.close()
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])
        self.assertEqual(engine.eeprom_fingerprint(result), expected)
        arm = transport.events.index(("write", 23, 2, [0x8001]))
        commit = transport.events.index(("write", 23, 18, [0x0514]))
        self.assertIn(("read", 23, 2, 1), transport.events[arm + 1:commit])
        self.assertIn(("read", 23, 0, 20), transport.events[arm + 1:commit])
        self.assertEqual(transport.events[-1], ("read", 23, 0, 20))

    async def test_lost_arm_ack_disarms_without_trigger_and_reports_uncertainty(self):
        class LostArmAck(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if address == 2 and values == [0x8001]:
                    raise OSError("simulated lost arm ACK")
        transport = LostArmAck()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "uncertain.*disarm verified"):
            await engine.Controller(transport).command(23, "save_eeprom", expected)
        self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_lost_commit_ack_disarms_without_replay_and_reports_uncertainty(self):
        class LostCommitAck(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if address == 18:
                    raise OSError("simulated lost commit ACK")
        transport = LostCommitAck()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "uncertain.*disarm verified"):
            await engine.Controller(transport).command(23, "save_eeprom", expected)
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])

    async def test_changed_configuration_after_arm_disarms_without_trigger(self):
        for index in (3, 4, 5, 6, 7, 8, 17, 18, 19):
            with self.subTest(index=index):
                class ChangedAfterArm(SimulatedEEPROM):
                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        if address == 2 and values == [0x8001]:
                            self.words[index] ^= 0x0100
                transport = ChangedAfterArm()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "disarm verified"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_transport_cancellation_after_arm_becomes_command_failure(self):
        class CancelledArm(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if address == 2 and values == [0x8001]:
                    raise asyncio.CancelledError("simulated transport cancellation")
        transport = CancelledArm()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "uncertain.*disarm verified"):
            await engine.Controller(transport).command(23, "save_eeprom", expected)
        self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_unverified_disarm_latches_configuration_block_without_replay(self):
        for failure in ("pending", "lost_disarm_ack", "unsafe_status", "unreadable_status"):
            with self.subTest(failure=failure):
                class RecoveryFailure(SimulatedEEPROM):
                    async def read(self, unit, address, count):
                        if failure == "unreadable_status" and self.writes and address == 2:
                            raise OSError("simulated broken read after arm")
                        return await super().read(unit, address, count)

                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        if values == [0x8001] and failure == "unsafe_status":
                            self.words[2] = 0x8210  # unknown/INIT function bit: never echo
                        if address == 2 and values == [0x8000]:
                            if failure == "pending":
                                self.committed = False
                                self.words[2] = 0x8200  # disarm cannot clear pending in this simulation
                            elif failure == "lost_disarm_ack":
                                raise OSError("simulated lost disarm ACK")
                transport = RecoveryFailure()
                controller = engine.Controller(transport)
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "manual recovery required"):
                    await controller.command(23, "save_eeprom", expected)
                original_writes = list(transport.writes)
                self.assertLessEqual(transport.writes.count((23, 18, [0x0514])), 1)
                self.assertLessEqual(transport.writes.count((23, 2, [0x8000])), 1)
                if failure in {"unsafe_status", "unreadable_status"}:
                    self.assertEqual(transport.writes, [(23, 2, [0x8001])])
                # Even a later normal poll cannot prove an ambiguous disarm ACK.
                transport.words[2] = 0x8000
                transport.committed = False
                for kind, value in [("travel_times", {"down": 21}), ("save_eeprom", expected)]:
                    with self.assertRaisesRegex(engine.DeviceError, "manual recovery"):
                        await controller.command(23, kind, value)
                self.assertEqual(transport.writes, original_writes)
                await controller.poll(23)  # Read-only inspection remains possible.

    async def test_cancelled_eeprom_preflight_does_not_kill_serialized_worker(self):
        class CancelledPreflight(SimulatedEEPROM):
            cancelled = False

            async def read(self, unit, address, count):
                if not self.cancelled:
                    self.cancelled = True
                    raise asyncio.CancelledError("simulated cancelled initial EEPROM read")
                return await super().read(unit, address, count)
        transport = CancelledPreflight()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        bus = engine.Bus(engine.Controller(transport))
        bus.start()
        try:
            with self.assertRaises(engine.DeviceError):
                await asyncio.wait_for(bus.submit(23, "save_eeprom", expected), 0.2)
            await asyncio.wait_for(bus.submit(23, "poll"), 0.2)
            self.assertFalse(bus._task.done())
            self.assertEqual(transport.writes, [])
        finally:
            await bus.close()

    async def test_pending_reappearing_in_final_snapshot_requires_manual_recovery(self):
        class PendingAtFinalRead(SimulatedEEPROM):
            async def read(self, unit, address, count):
                if self.committed and address == 0:
                    self.words[2] = 0x8200
                return await super().read(unit, address, count)
        transport = PendingAtFinalRead()
        controller = engine.Controller(transport)
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "manual recovery required"):
            await controller.command(23, "save_eeprom", expected)
        writes = list(transport.writes)
        transport.committed = False
        transport.words[2] = 0x8000
        with self.assertRaisesRegex(engine.DeviceError, "manual recovery required"):
            await controller.command(23, "travel_times", {"down": 21})
        self.assertEqual(transport.writes, writes)

    async def test_changed_configuration_before_arm_never_writes(self):
        for index in (3, 4, 5, 6, 7, 8, 17, 18, 19):
            with self.subTest(index=index):
                transport = SimulatedEEPROM()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                transport.words[index] ^= 0x0100
                with self.assertRaisesRegex(engine.DeviceError, "changed since preparation"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [])

    async def test_unsafe_save_preflight_has_no_writes(self):
        cases = [(1, fw) for fw in (0, 14, 16, 18, 20, 21)]
        cases += [(4, 0x0300), (11, 1), (11, 2), (12, 1), (12, 2), (12, 3),
                  (16, 1), (16, 2), (16, 4), (16, 8), (2, 0), (2, 0x8001),
                  (2, 0x8200), (2, 0x8010), (2, 0x8002), (2, 0x8004),
                  (2, 0xC000), (18, 0x0500), (17, 0x0C01)]
        for index, value in cases:
            with self.subTest(index=index, value=value):
                transport = SimulatedEEPROM()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                transport.words.extend([0] * 7)  # FW21 poll includes its documented extra word.
                transport.words[index] = value
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [])

    async def test_missing_or_invalid_prepared_tuple_never_writes(self):
        baseline = engine.eeprom_fingerprint(engine.decode(registers()))
        for expected in (None, {}, 20, (), list(baseline), baseline[:-1],
                         (True,) + baseline[1:], (-1,) + baseline[1:],
                         (65536,) + baseline[1:]):
            with self.subTest(expected=expected):
                transport = SimulatedEEPROM()
                with self.assertRaises(engine.DeviceError):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [])

    async def test_activity_after_arm_refuses_trigger_and_only_disarms(self):
        for index, value in [(11, 1), (12, 2), (16, 1), (16, 2), (16, 4),
                             (16, 8), (1, 18), (4, 0x0300), (2, 0x8000)]:
            with self.subTest(index=index, value=value):
                class BusyAfterArm(SimulatedEEPROM):
                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        if values == [0x8001]:
                            self.words[index] = value
                transport = BusyAfterArm()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "disarm verified"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_arm_must_be_exact_pending_status_with_enable_self_cleared(self):
        for arm_status in (0x8000, 0x8001, 0x8201):
            with self.subTest(arm_status=arm_status):
                class WrongArmStatus(SimulatedEEPROM):
                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        if values == [0x8001]:
                            self.words[2] = arm_status
                transport = WrongArmStatus()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "arm not confirmed"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_invalid_scalar_status_is_rejected_before_trigger(self):
        for invalid in ([], [True], [-1], [65536], [0x8200, 0]):
            with self.subTest(invalid=invalid):
                class InvalidStatus(SimulatedEEPROM):
                    supplied = False

                    async def read(self, unit, address, count):
                        if address == 2 and count == 1 and not self.supplied:
                            self.supplied = True
                            return invalid
                        return await super().read(unit, address, count)
                transport = InvalidStatus()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "Invalid EEPROM status"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 2, [0x8000])])

    async def test_changed_final_configuration_is_not_reported_as_success(self):
        for index in (3, 4, 5, 6, 7, 8, 17, 18, 19):
            with self.subTest(index=index):
                class ChangedOnCommit(SimulatedEEPROM):
                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        if address == 18:
                            self.words[index] ^= 0x0100
                transport = ChangedOnCommit()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "uncertain.*disarm verified"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                                   (23, 18, [0x0514]), (23, 2, [0x8000])])

    async def test_unexpected_completion_status_never_replays_or_echoes_functions(self):
        for completion_status in (0, 0x8001, 0x8210, 0x8204):
            with self.subTest(completion_status=completion_status):
                class BadCompletion(SimulatedEEPROM):
                    async def read(self, unit, address, count):
                        if self.committed and address == 2 and count == 1:
                            self.words[2] = completion_status
                            self.committed = False
                        return await super().read(unit, address, count)
                transport = BadCompletion()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                with self.assertRaisesRegex(engine.DeviceError, "Unexpected EEPROM completion"):
                    await engine.Controller(transport).command(23, "save_eeprom", expected)
                self.assertEqual(transport.writes.count((23, 18, [0x0514])), 1)
                self.assertTrue(all(address in {2, 18} for _, address, _ in transport.writes))
                if completion_status != 0x8001:
                    self.assertEqual(transport.writes, [(23, 2, [0x8001]), (23, 18, [0x0514])])

    async def test_completion_poll_timeout_does_not_retry_trigger(self):
        transport = SimulatedEEPROM()
        transport.pending_reads = 100
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        started = asyncio.get_running_loop().time()
        with self.assertRaisesRegex(engine.DeviceError, "waiting for completion.*uncertain.*TimeoutError"):
            await engine.Controller(transport).command(23, "save_eeprom", expected)
        self.assertLess(asyncio.get_running_loop().time() - started, 4.0)
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])

    async def test_stalled_status_read_is_also_bounded_and_disarmed(self):
        class StalledRead(SimulatedEEPROM):
            stalled = False

            async def read(self, unit, address, count):
                if self.committed and address == 2 and not self.stalled:
                    self.stalled = True
                    await asyncio.Event().wait()
                return await super().read(unit, address, count)
        transport = StalledRead()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "uncertain.*disarm verified.*TimeoutError"):
            await asyncio.wait_for(engine.Controller(transport).command(23, "save_eeprom", expected), 4)
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])

    async def test_failed_armed_cancellation_leaves_worker_alive_for_other_units(self):
        for phase in ("arm", "commit", "completion", "disarm"):
            with self.subTest(phase=phase):
                class CancelledPhase(SimulatedEEPROM):
                    cancelled = False

                    async def write(self, unit, address, values):
                        await super().write(unit, address, values)
                        match = ((phase == "arm" and values == [0x8001]) or
                                 (phase == "commit" and address == 18) or
                                 (phase == "disarm" and values == [0x8000]))
                        if match and not self.cancelled:
                            self.cancelled = True
                            raise asyncio.CancelledError("simulated phase cancellation")

                    async def read(self, unit, address, count):
                        if phase == "completion" and self.committed and not self.cancelled:
                            self.cancelled = True
                            raise asyncio.CancelledError("simulated completion cancellation")
                        return await super().read(unit, address, count)
                transport = CancelledPhase()
                expected = engine.eeprom_fingerprint(engine.decode(transport.words))
                bus = engine.Bus(engine.Controller(transport))
                bus.start()
                try:
                    with self.assertRaisesRegex(engine.DeviceError, "uncertain"):
                        await asyncio.wait_for(bus.submit(23, "save_eeprom", expected), 1)
                    await asyncio.wait_for(bus.submit(24, "poll"), 1)
                    self.assertFalse(bus._task.done())
                    self.assertLessEqual(transport.writes.count((23, 18, [0x0514])), 1)
                finally:
                    await bus.close()

    async def test_other_bus_jobs_cannot_interleave_with_armed_sequence(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class PausedArm(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if values == [0x8001]:
                    entered.set()
                    await release.wait()
        transport = PausedArm()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        bus = engine.Bus(engine.Controller(transport))
        bus.start()
        saving = asyncio.create_task(bus.submit(23, "save_eeprom", expected))
        await entered.wait()
        polling = asyncio.create_task(bus.submit(24, "poll"))
        await asyncio.sleep(0)
        self.assertFalse(any(event[1] == 24 for event in transport.events))
        release.set()
        try:
            await saving
            await polling
        finally:
            await bus.close()
        last_save = max(i for i, event in enumerate(transport.events) if event[1] == 23)
        first_other = min(i for i, event in enumerate(transport.events) if event[1] == 24)
        self.assertGreater(first_other, last_save)

    async def test_disarm_timeout_requires_recovery_and_is_not_retried(self):
        class StalledDisarm(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if address == 2 and values == [0x8000]:
                    await asyncio.Event().wait()  # ambiguous sent disarm; ACK never arrives
        transport = StalledDisarm()
        controller = engine.Controller(transport)
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        with self.assertRaisesRegex(engine.DeviceError, "manual recovery required.*TimeoutError"):
            await asyncio.wait_for(controller.command(23, "save_eeprom", expected), 4)
        with self.assertRaisesRegex(engine.DeviceError, "manual recovery"):
            await controller.command(23, "travel_times", {"down": 21})
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])

    async def test_cancelled_submitter_cannot_interrupt_or_replay_inflight_save(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class PausedArm(SimulatedEEPROM):
            async def write(self, unit, address, values):
                await super().write(unit, address, values)
                if values == [0x8001]:
                    entered.set()
                    await release.wait()
        transport = PausedArm()
        expected = engine.eeprom_fingerprint(engine.decode(transport.words))
        bus = engine.Bus(engine.Controller(transport))
        bus.start()
        caller = asyncio.create_task(bus.submit(23, "save_eeprom", expected))
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        release.set()
        try:
            await asyncio.wait_for(bus.submit(24, "poll"), 1)
        finally:
            await bus.close()
        self.assertEqual(transport.writes, [(23, 2, [0x8001]),
                                           (23, 18, [0x0514]), (23, 2, [0x8000])])
        self.assertEqual(engine.eeprom_fingerprint(engine.decode(transport.words)), expected)


if __name__ == "__main__":
    unittest.main()
