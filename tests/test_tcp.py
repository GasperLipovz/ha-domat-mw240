"""Real loopback Modbus TCP tests; synthetic devices, never physical hardware."""
import asyncio
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class TcpTransportContractTests(unittest.TestCase):
    def test_tcp_transport_exists(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        self.assertIsNotNone(getattr(runtime, "TcpTransport", None),
                             "TCP transport is missing; tests must use the real pymodbus TCP client")


def synthetic_registers(unit):
    """FW17 protocol fixture, not a claim about any connected R035/MW240."""
    words = [0] * 27
    words[0:5] = [0x0A24, 17, 0x8000, 0x0E17, 0x8300]
    words[5] = 0x0A00
    words[15] = unit  # distinguish independently routed unit maps
    words[17:20] = [0x0C00, 0x051E, 0x0702]
    words[26] = 35
    return words


@dataclass(frozen=True)
class Request:
    connection: int
    transaction: int
    unit: int
    function: int
    address: int
    count: int
    values: tuple = ()


class SyntheticTcpGateway:
    """Minimal actual TCP/MBAP endpoint, FC03/FC16 only.

    Requests are decoded independently of pymodbus, so tests catch actual wire
    addressing/framing/function-code mistakes. Writes take effect *before* a
    deliberately lost/corrupt ACK: their outcome is genuinely ambiguous to the
    client. This simulates register logic, never mechanics or flash persistence.
    """

    def __init__(self, units=(7, 23), *, fragmented=False):
        self.words = {unit: synthetic_registers(unit) for unit in units}
        self.fragmented = fragmented
        self.server = None
        self.connections = 0
        self.writers = set()
        self.tasks = set()
        self.requests = []
        self.errors = []
        self.actions = []
        self.faults = []
        self.fragments = 0
        self.hold_next_read = False
        self.read_held = asyncio.Event()
        self.release_read = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def fault(self, behavior, *, function=16, address=None, values=None, unit=23):
        self.faults.append((behavior, function, address,
                            None if values is None else tuple(values), unit))

    def take_fault(self, request):
        for index, (behavior, function, address, values, unit) in enumerate(self.faults):
            if (request.function == function and request.unit == unit
                    and (address is None or request.address == address)
                    and (values is None or request.values == values)):
                self.faults.pop(index)
                return behavior
        return None

    @property
    def writes(self):
        return [r for r in self.requests if r.function == 16]

    def apply(self, request):
        words = self.words[request.unit]
        old = words[request.address]
        words[request.address:request.address + request.count] = request.values
        if request.address == 16:
            value = request.values[0]
            if value & 2:
                words[12] = 0
                self.actions.append((request.unit, "stop"))
            elif value & 12:
                self.actions.append((request.unit, "tilt_up" if value & 4 else "tilt_down"))
                words[12] = 1 if value & 4 else 2
                words[16] &= ~14  # action consumed, bus override remains until release
            elif value & 1 and (not old & 1 or old >> 8 != value >> 8):
                self.actions.append((request.unit, "move"))
                words[12] = 1 if value >> 8 == 0 else 2
                words[15] = (words[15] & 0xFF00) | (value >> 8)
            # STOP may stay latched when already idle on FW17.
        elif request.address == 2 and request.values == (0x8001,):
            words[2] = 0x8200
        elif request.address == 18 and words[2] == 0x8200:
            self.actions.append((request.unit, "eeprom_commit"))
            words[2] = 0x8000

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        self.connections += 1
        connection = self.connections
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                assert protocol == 0, "Modbus TCP protocol identifier must be zero"
                assert 2 <= length <= 254, "Invalid MBAP length"
                pdu = await reader.readexactly(length - 1)
                function = pdu[0]
                assert function in (3, 16), f"Forbidden FC{function}; writes must be FC16, not FC06"
                assert unit in self.words, f"Unexpected routed unit {unit}"
                address, count = struct.unpack(">HH", pdu[1:5])
                assert 1 <= count <= 125 and address + count <= len(self.words[unit])
                values = ()
                if function == 3:
                    assert len(pdu) == 5
                else:
                    assert len(pdu) >= 6 and pdu[5] == count * 2
                    assert len(pdu) == 6 + count * 2
                    values = struct.unpack(f">{count}H", pdu[6:])
                request = Request(connection, transaction, unit, function, address, count, values)
                self.requests.append(request)
                fault = self.take_fault(request)
                if function == 3:
                    if self.hold_next_read:
                        self.hold_next_read = False
                        self.read_held.set()
                        await self.release_read.wait()
                    registers = self.words[unit][address:address + count]
                    if fault == "short_read":
                        registers = registers[:-1]
                    response = bytes((3, len(registers) * 2)) + struct.pack(
                        f">{len(registers)}H", *registers)
                else:
                    self.apply(request)
                    response = struct.pack(">BHH", 16, address + (fault == "bad_address"),
                                           count + (fault == "bad_count"))
                if fault == "drop":
                    writer.close()
                    return
                if fault == "silent":
                    continue
                if fault == "exception":
                    response = bytes((function | 0x80, 4))
                response_unit = unit + 1 if fault == "wrong_unit" else unit
                response_transaction = (transaction + 1) & 0xFFFF if fault == "wrong_transaction" else transaction
                frame = struct.pack(">HHHB", response_transaction, 0, len(response) + 1,
                                    response_unit) + response
                if self.fragmented:
                    # Split the MBAP header itself, and then the register payload.
                    for chunk in (frame[:2], frame[2:6], frame[6:9], frame[9:]):
                        if chunk:
                            writer.write(chunk)
                            await writer.drain()
                            self.fragments += 1
                            await asyncio.sleep(0.003)
                else:
                    writer.write(frame)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as err:
            self.errors.append(err)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.writers.discard(writer)
            self.tasks.discard(task)

    async def close(self):
        self.release_read.set()
        if self.server is not None:
            self.server.close()
        # Python 3.14 Server.wait_closed() also waits for accepted connections;
        # close them first, including factory-created clients not owned by Bus.
        for writer in tuple(self.writers):
            writer.close()
        if self.server is not None:
            await self.server.wait_closed()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class RealSocketTcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        self.engine = importlib.import_module("custom_components.domat_mw240.engine")
        self.gateway = await SyntheticTcpGateway().start()
        self.addAsyncCleanup(self.gateway.close)
        transport_class = getattr(self.runtime, "TcpTransport", None)
        self.assertIsNotNone(transport_class, "TcpTransport must exist before TCP can pass")
        self.config = {"connection_type": "tcp", "host": "127.0.0.1",
                       "tcp_port": self.gateway.port, "timeout": 0.15}
        self.transport = transport_class(self.config)
        self.addCleanup(self.transport.close)
        self.assertIsInstance(self.transport.client, AsyncModbusTcpClient)
        self.bus = self.engine.Bus(self.engine.Controller(self.transport), command_ttl=2)
        self.bus.start()
        self.addAsyncCleanup(self.bus.close)

    async def asyncTearDown(self):
        await self.bus.close()
        self.transport.close()
        await self.gateway.close()
        self.assertEqual(self.gateway.errors, [], "Synthetic server rejected actual wire data")
        self.assertEqual(self.gateway.faults, [], "Every fault must have reached its intended wire request")
        self.assertEqual(self.gateway.tasks, set(), "Server tasks must be joined, not orphaned")

    async def submit(self, unit, kind, value=None):
        return await asyncio.wait_for(self.bus.submit(unit, kind, value), 3)

    async def test_fragmented_mbap_and_payload_route_distinct_unit_register_maps(self):
        self.gateway.fragmented = True
        first = await self.submit(23, "poll")
        second = await self.submit(7, "poll")
        self.assertEqual((first.position, second.position), (77, 93))
        self.assertEqual((first.firmware, first.blind_mode, first.travel_seconds), (17, True, 30))
        self.assertEqual((first.rotation_seconds, first.step_seconds), (1.2, 0.2))
        self.assertEqual(first.raw[2], 0x8000)
        self.assertIsNone(first.tilt_position)
        self.assertEqual(self.gateway.connections, 1)
        self.assertEqual([(r.unit, r.function, r.address, r.count) for r in self.gateway.requests],
                         [(23, 3, 0, 20), (7, 3, 0, 20)])
        self.assertEqual(len({r.transaction for r in self.gateway.requests}), 2)
        self.assertGreaterEqual(self.gateway.fragments, 8)
        self.assertEqual(self.gateway.writes, [])

    async def expect_failure(self, awaitable):
        # An outer deadline must fail the test, not masquerade as the expected
        # transport failure. All socket timeouts are at least 150 ms.
        async with asyncio.timeout(2):
            with self.assertRaises((self.engine.DeviceError, ModbusException, OSError)) as caught:
                await awaitable
        return caught.exception

    async def test_factory_uses_tcp_without_serial_keys_and_fc16_multiple_words(self):
        transport = self.runtime.create_transport(self.config)
        self.addCleanup(transport.close)
        self.assertIsInstance(transport, self.runtime.TcpTransport)
        self.assertIsInstance(transport.client, AsyncModbusTcpClient)
        await transport.connect()
        await transport.write(23, 6, [0x1234, 0xABCD])
        self.assertEqual(await transport.read(23, 6, 2), [0x1234, 0xABCD])
        self.assertEqual(await transport.read(7, 6, 2), [0, 0])
        self.assertEqual([(r.function, r.address, r.count, r.values) for r in self.gateway.writes],
                         [(16, 6, 2, (0x1234, 0xABCD))])

    async def test_twenty_units_share_one_socket_and_one_read_only_bus_worker(self):
        units = [*range(1, 20), 23]
        self.gateway.words = {unit: synthetic_registers(unit) for unit in units}
        worker = self.bus._task
        self.gateway.hold_next_read = True
        tasks = [asyncio.create_task(self.submit(unit, "poll")) for unit in units]
        try:
            await asyncio.wait_for(self.gateway.read_held.wait(), 1)
            await asyncio.sleep(0.05)
            self.assertEqual(len(self.gateway.requests), 1, "Whole jobs must be serialized")
            self.assertIs(self.bus._task, worker)
            self.gateway.release_read.set()
            states = await asyncio.gather(*tasks)
        finally:
            self.gateway.release_read.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual([s.position for s in states], [100 - unit for unit in units])
        self.assertEqual(self.gateway.connections, 1)
        self.assertEqual(len(self.gateway.requests), 20)
        self.assertEqual({r.unit for r in self.gateway.requests}, set(units))
        self.assertEqual({r.connection for r in self.gateway.requests}, {1})
        self.assertTrue(all(r.function == 3 for r in self.gateway.requests))
        await self.bus.close()
        self.transport.close()
        await asyncio.sleep(0.05)
        self.assertEqual(self.gateway.writes, [], "Neither startup nor unload may initialize devices")

    async def test_repeated_position_target_uses_new_edge_and_releases_override(self):
        self.gateway.fragmented = True
        self.gateway.words[23][16] = 0x0030  # reserved bits must survive
        for _ in range(2):
            state = await self.submit(23, "position", 100)
            self.assertEqual(state.manual & 1, 0)
            self.assertEqual(state.position, 100)
        self.assertEqual([r.values for r in self.gateway.writes],
                         [(0x33,), (0x30,), (0x31,), (0x30,)] * 2)
        self.assertTrue(all(r.unit == 23 and r.address == 16 and r.count == 1
                            for r in self.gateway.writes))
        self.assertEqual(self.gateway.actions.count((23, "move")), 2)
        self.assertEqual(self.gateway.words[7], synthetic_registers(7))
        self.assertEqual(self.gateway.connections, 1)

    async def test_tilt_steps_stop_and_release_preserve_existing_target(self):
        self.gateway.words[23][16] = 0x3230
        for kind, action in (("tilt_up", 0x3235), ("tilt_down", 0x3239), ("stop", None)):
            with self.subTest(kind=kind):
                start = len(self.gateway.writes)
                state = await self.submit(23, kind)
                expected = [(0x3233,)]
                if action is not None:
                    expected.append((action,))
                expected.append((0x3230,))
                self.assertEqual([r.values for r in self.gateway.writes[start:]], expected)
                self.assertEqual(state.manual, 0x3230)
        self.assertEqual(state.outputs, 0)
        self.assertEqual(self.gateway.actions.count((23, "tilt_up")), 1)
        self.assertEqual(self.gateway.actions.count((23, "tilt_down")), 1)

    async def test_dropped_move_ack_fails_without_reconnect_or_original_move_replay(self):
        self.gateway.fault("drop", address=16, values=[1])
        await self.expect_failure(self.submit(23, "position", 100))
        self.assertEqual([r.values for r in self.gateway.writes], [(3,), (0,), (1,)])
        self.assertEqual(self.gateway.actions.count((23, "move")), 1)
        self.assertEqual(self.gateway.connections, 1)
        before = list(self.gateway.requests)
        await self.expect_failure(self.transport.write(23, 16, [1]))
        await self.expect_failure(self.transport.read(23, 0, 20))
        await asyncio.sleep(0.2)
        self.assertEqual(self.gateway.requests, before, "No command-internal or background reconnect")
        self.assertEqual(self.gateway.connections, 1)
        state = await self.submit(23, "poll")
        self.assertEqual(state.firmware, 17)
        self.assertEqual(self.gateway.connections, 2)
        self.assertEqual(self.gateway.requests[-1].function, 3)
        self.assertEqual(self.gateway.requests[-1].connection, 2)
        self.assertEqual([r.values for r in self.gateway.writes], [(3,), (0,), (1,)])
        # A lost ACK can leave real override/motion uncertain: do not claim it stopped.
        self.assertEqual(state.manual & 1, 1)

    async def test_silent_move_ack_is_not_retried_but_explicit_stop_cleanup_is_allowed(self):
        self.gateway.fault("silent", address=16, values=[1])
        await self.expect_failure(self.submit(23, "position", 100))
        writes = self.gateway.writes
        self.assertEqual(sum(r.values == (1,) for r in writes), 1)
        original = next(i for i, r in enumerate(writes) if r.values == (1,))
        self.assertTrue(all(r.address == 16 and r.values in ((3,), (0,))
                            for r in writes[original + 1:]), "Cleanup is not MOVE replay")
        self.assertEqual(self.gateway.actions.count((23, "move")), 1)
        before = list(self.gateway.writes)
        await asyncio.sleep(0.2)
        await self.submit(23, "poll")
        self.assertEqual(self.gateway.writes, before)

    async def test_bad_fc16_ack_address_or_count_fails_and_never_replays_move(self):
        for fault in ("bad_address", "bad_count", "exception"):
            with self.subTest(fault=fault):
                start = len(self.gateway.writes)
                self.gateway.fault(fault, address=16, values=[1])
                error = await self.expect_failure(self.submit(23, "position", 100))
                self.assertIsInstance(error, self.engine.DeviceError)
                writes = self.gateway.writes[start:]
                self.assertEqual(sum(r.values == (1,) for r in writes), 1)
                original = next(i for i, r in enumerate(writes) if r.values == (1,))
                self.assertTrue(all(r.values in ((3,), (0,)) for r in writes[original + 1:]))
                self.assertEqual(self.gateway.words[23][12], 0, "Best-effort STOP is expected on a healthy link")
                self.assertEqual(self.gateway.words[23][16] & 1, 0)
                self.assertFalse(self.gateway.faults)

    async def test_wrong_unit_and_transaction_read_replies_are_not_accepted(self):
        for fault in ("wrong_unit", "wrong_transaction"):
            with self.subTest(fault=fault):
                start = len(self.gateway.requests)
                self.gateway.fault(fault, function=3, address=0)
                await self.expect_failure(self.submit(23, "poll"))
                self.assertEqual(len(self.gateway.requests) - start, 1, "Read must not be retried")
                self.assertFalse(self.gateway.faults)
                self.assertEqual((await self.submit(7, "poll")).position, 93)
        self.assertEqual(self.gateway.writes, [])

    async def test_wrong_unit_write_ack_cannot_confirm_success(self):
        await self.transport.connect()
        self.gateway.fault("wrong_unit", address=6, values=[0x1234])
        await self.expect_failure(self.transport.write(23, 6, [0x1234]))
        self.assertEqual(self.gateway.words[23][6], 0x1234, "Write may execute despite rejected ACK")
        self.assertEqual(len(self.gateway.writes), 1)
        self.assertFalse(self.gateway.faults)
        await self.submit(23, "poll")
        self.assertEqual(len(self.gateway.writes), 1)

    async def test_short_read_and_modbus_exception_are_not_valid_snapshots(self):
        for fault in ("short_read", "exception"):
            with self.subTest(fault=fault):
                self.gateway.fault(fault, function=3, address=0)
                error = await self.expect_failure(self.submit(23, "poll"))
                self.assertIsInstance(error, self.engine.DeviceError)
                self.assertFalse(self.gateway.faults)
        self.assertEqual(self.gateway.writes, [])

    async def test_eof_read_reconnects_only_for_next_read_only_job(self):
        self.gateway.fault("drop", function=3, address=0)
        await self.expect_failure(self.submit(23, "poll"))
        await asyncio.sleep(0.2)
        self.assertEqual(self.gateway.connections, 1)
        self.assertEqual(len(self.gateway.requests), 1)
        self.assertEqual((await self.submit(7, "poll")).position, 93)
        self.assertEqual(self.gateway.connections, 2)
        self.assertEqual(self.gateway.writes, [])

    async def test_offline_preflight_never_queues_a_move_for_later_reconnection(self):
        self.gateway.fault("silent", function=3, address=0)
        await self.expect_failure(self.submit(23, "position", 40))
        self.assertEqual(len(self.gateway.requests), 1)
        self.assertEqual(self.gateway.writes, [])
        await self.submit(23, "poll")
        await asyncio.sleep(0.2)
        self.assertEqual(self.gateway.writes, [], "Recovering polling must not replay failed commands")
        self.assertEqual(self.bus._pending, {})

    async def test_command_expired_behind_slow_poll_never_reaches_tcp_wire(self):
        self.bus.command_ttl = 0.05
        self.gateway.hold_next_read = True
        poll = asyncio.create_task(self.submit(7, "poll"))
        command = None
        try:
            await asyncio.wait_for(self.gateway.read_held.wait(), 1)
            command = asyncio.create_task(self.submit(23, "position", 40))
            await asyncio.sleep(0.08)
            self.gateway.release_read.set()
            await poll
            with self.assertRaisesRegex(self.engine.DeviceError, "expired"):
                await command
        finally:
            self.gateway.release_read.set()
            await asyncio.gather(*(task for task in (poll, command) if task), return_exceptions=True)
        await self.submit(23, "poll")
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual([(r.unit, r.function) for r in self.gateway.requests], [(7, 3), (23, 3)])

    async def test_tcp_hub_twenty_unit_first_refresh_and_unload_are_read_only(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.exceptions import HomeAssistantError

        units = [*range(1, 20), 23]
        self.gateway.words = {unit: synthetic_registers(unit) for unit in units}
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            hass = HomeAssistant(directory)
            hub = None
            try:
                config = {**self.config, "name": "synthetic_tcp_only", "poll_interval": 30,
                          "command_ttl": 2, "allow_commands": False,
                          "allow_timing_changes": False, "allow_eeprom_save": False,
                          "devices": [{"id": unit, "name": f"Synthetic {unit}"} for unit in units]}
                # No injected transport: exercises Hub -> factory -> actual client.
                hub = self.runtime.Hub(hass, config)
                self.assertIsInstance(hub.transport, self.runtime.TcpTransport)
                hub.bus.start()
                worker = hub.bus._task
                await asyncio.wait_for(hub.async_refresh(), 3)
                self.assertEqual(set(hub.data), set(units))
                self.assertTrue(all(record.available for record in hub.data.values()))
                self.assertEqual([hub.data[u].snapshot.position for u in units],
                                 [100 - u for u in units])
                self.assertIs(hub.bus._task, worker)
                with self.assertRaises(HomeAssistantError):
                    await hub.async_command(23, "position", 100)
                self.assertEqual(self.gateway.connections, 1)
                self.assertEqual(len(self.gateway.requests), 20)
                self.assertEqual(self.gateway.writes, [])
            finally:
                if hub is not None:
                    await hub.async_close()
                await hass.async_stop()
        self.assertEqual(self.gateway.writes, [])

    async def test_full_ha_tcp_entry_loads_twenty_modules_and_registries_on_one_socket(self):
        import shutil
        from homeassistant import loader
        from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import device_registry as dr, entity_registry as er, frame

        units = [*range(1, 20), 23]
        self.gateway.words = {unit: synthetic_registers(unit) for unit in units}
        integration = importlib.import_module("custom_components.domat_mw240")
        config = integration.BUS_SCHEMA({
            **self.config, "name": "synthetic_native_tcp", "poll_interval": 60,
            "devices": [{"id": unit, "name": f"Synthetic TCP {unit}"} for unit in units],
        })
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            target = Path(directory) / "custom_components" / "domat_mw240"
            shutil.copytree(ROOT / "custom_components" / "domat_mw240", target,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            hass = HomeAssistant(directory)
            hass.config_entries = ConfigEntries(hass, {})
            entry = None
            try:
                await hass.config_entries.async_initialize()
                dr.async_setup(hass)
                await dr.async_load(hass, load_empty=True)
                await er.async_load(hass, load_empty=True)
                loader.async_setup(hass)
                frame.async_setup(hass)
                entry = ConfigEntry(
                    domain="domat_mw240", title=config["name"], data=config,
                    options={}, unique_id=config["name"], version=1, minor_version=2,
                    source="user", discovery_keys={}, subentries_data=[],
                )
                # async_add performs the real config-entry setup; no platform,
                # transport, registry, connect, read or write functions are mocked.
                await asyncio.wait_for(hass.config_entries.async_add(entry), 15)
                await asyncio.wait_for(hass.async_block_till_done(), 5)
                self.assertEqual(entry.state, ConfigEntryState.LOADED)
                hub = entry.runtime_data
                self.assertIsInstance(hub.transport.client, AsyncModbusTcpClient)
                self.assertTrue(all(record.available for record in hub.records.values()))
                self.assertEqual(set(hub.records), set(units))
                devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
                self.assertEqual(len(devices), 21)
                bus_device = next(device for device in devices if device.id == hub.bus_device_id)
                self.assertEqual(bus_device.identifiers, {("domat_mw240", f"{config['name']}_bus")})
                modules = [device for device in devices if device.model == "MW240-B"]
                self.assertEqual(len(modules), 20)
                self.assertEqual({device.via_device_id for device in modules}, {bus_device.id})
                self.assertEqual({device.sw_version for device in modules}, {"17"})
                entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                self.assertEqual(len(entities), 280)
                self.assertEqual(len({entity.unique_id for entity in entities}), 280)
                self.assertTrue(all(entity.device_id in {d.id for d in modules} for entity in entities))
                covers = [entity for entity in entities if entity.domain == "cover"]
                self.assertEqual(len(covers), 20)
                for entity in covers:
                    state = hass.states.get(entity.entity_id)
                    self.assertIsNotNone(state)
                    self.assertNotIn(state.state, ("unavailable", "unknown"))
                self.assertEqual(self.gateway.connections, 1)
                self.assertEqual({request.unit for request in self.gateway.requests}, set(units))
                self.assertTrue(all(request.function == 3 for request in self.gateway.requests))
                self.assertEqual(hass.data["domat_mw240"]["ports"],
                                 {("tcp", "127.0.0.1", self.gateway.port): entry.entry_id})
                self.assertEqual(self.gateway.writes, [])
                self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
                self.assertTrue(hub.bus._task.done())
                self.assertFalse(hub.transport.client.connected)
                self.assertEqual(hass.data["domat_mw240"]["ports"], {})
            finally:
                if entry is not None and entry.state == ConfigEntryState.LOADED:
                    await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_stop()
        self.assertEqual(self.gateway.writes, [], "Native startup/unload must never initialize hardware")

    async def test_concurrent_modules_cannot_interleave_whole_motor_sequences(self):
        first, second = await asyncio.gather(self.submit(23, "position", 100),
                                             self.submit(7, "position", 0))
        self.assertEqual((first.position, second.position), (100, 0))
        runs = []
        for request in self.gateway.requests:
            if not runs or runs[-1] != request.unit:
                runs.append(request.unit)
        self.assertEqual(runs, [23, 7], "The bus lock covers preflight through final release/readback")
        self.assertEqual([(r.unit, r.values) for r in self.gateway.writes],
                         [(23, (3,)), (23, (0,)), (23, (1,)), (23, (0,)),
                          (7, (3,)), (7, (0x6400,)), (7, (0x6401,)), (7, (0x6400,))])
        self.assertEqual(self.gateway.connections, 1)

    async def test_read_and_write_do_not_open_an_unconnected_transport(self):
        await self.expect_failure(self.transport.read(23, 0, 20))
        await self.expect_failure(self.transport.write(23, 16, [1]))
        await asyncio.sleep(0.05)
        self.assertEqual(self.gateway.connections, 0)
        self.assertEqual(self.gateway.requests, [])
        self.assertEqual((await self.submit(23, "poll")).firmware, 17)
        self.assertEqual(self.gateway.connections, 1)
        self.assertEqual(self.gateway.writes, [])

    async def test_identity_and_commissioning_guards_remain_in_force_over_tcp(self):
        for address, value in ((0, 0x0A25), (1, 13), (4, 0x0300), (11, 1), (12, 3)):
            with self.subTest(address=address, value=value):
                self.gateway.words[23] = synthetic_registers(23)
                self.gateway.words[23][address] = value
                error = await self.expect_failure(self.submit(23, "position", 100))
                self.assertIsInstance(error, self.engine.DeviceError)
                self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.gateway.connections, 1)

    async def test_eeprom_commit_lost_ack_is_never_replayed_after_reconnect(self):
        prepared = self.engine.eeprom_fingerprint(await self.submit(23, "poll"))
        self.gateway.fault("drop", address=18, values=[0x051E])
        error = await self.expect_failure(self.submit(23, "save_eeprom", prepared))
        self.assertRegex(str(error), "uncertain.*manual recovery")
        self.assertEqual([(r.address, r.values) for r in self.gateway.writes],
                         [(2, (0x8001,)), (18, (0x051E,))])
        self.assertEqual(self.gateway.actions.count((23, "eeprom_commit")), 1)
        await asyncio.sleep(0.2)
        self.assertEqual(self.gateway.connections, 1)
        await self.submit(23, "poll")
        self.assertEqual(self.gateway.connections, 2)
        self.assertEqual(len(self.gateway.writes), 2)
        with self.assertRaisesRegex(self.engine.DeviceError, "manual recovery"):
            await self.submit(23, "save_eeprom", prepared)
        self.assertEqual(len(self.gateway.writes), 2)


if __name__ == "__main__":
    unittest.main()
