"""Transport construction and serial-regression checks; no real hardware."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_tcp_client_uses_socket_framing_and_no_automatic_retry(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        transport_class = getattr(runtime, "TcpTransport", None)
        self.assertIsNotNone(transport_class, "TCP transport is missing")
        assert transport_class is not None
        config = {"connection_type": "tcp", "host": "gateway.example", "tcp_port": 1502, "timeout": 2}
        with patch.object(runtime, "AsyncModbusTcpClient") as client_class:
            transport = transport_class(config)
            kwargs = client_class.call_args.kwargs
            self.assertEqual(client_class.call_args.args, ("gateway.example",))
            self.assertEqual(kwargs["port"], 1502)
            self.assertEqual(kwargs["timeout"], 2)
            self.assertEqual(kwargs["retries"], 0)
            self.assertEqual(kwargs["reconnect_delay"], 0)
            transport.close()
            client_class.return_value.close.assert_called_once()

    async def test_factory_preserves_legacy_serial_and_selects_tcp_explicitly(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        factory = getattr(runtime, "create_transport", None)
        self.assertIsNotNone(factory, "Connection factory is missing")
        assert factory is not None
        with patch.object(runtime, "SerialTransport") as serial, patch.object(runtime, "TcpTransport") as tcp:
            config = {"port": "/dev/test"}
            self.assertIs(factory(config), serial.return_value)
            self.assertIs(factory({"connection_type": "tcp", "host": "gateway.example", "tcp_port": 502}), tcp.return_value)
            self.assertEqual(serial.call_count, 1)
            self.assertEqual(tcp.call_count, 1)
            with self.assertRaises(runtime.DeviceError):
                factory({"connection_type": "rtu_over_tcp"})

    async def test_tcp_transactions_preserve_unit_and_fc16_without_reconnecting(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        transport_class = getattr(runtime, "TcpTransport", None)
        self.assertIsNotNone(transport_class)
        assert transport_class is not None
        client = MagicMock()
        client.connected = True
        read = MagicMock(registers=[0x0A24, 17])
        read.isError.return_value = False
        ack = MagicMock(address=18, count=1)
        ack.isError.return_value = False
        client.read_holding_registers = AsyncMock(return_value=read)
        client.write_registers = AsyncMock(return_value=ack)
        client.connect = AsyncMock(return_value=True)
        with patch.object(runtime, "AsyncModbusTcpClient", return_value=client):
            transport = transport_class({"host": "example", "tcp_port": 502, "timeout": 1})
            self.assertEqual(await transport.read(23, 0, 2), [0x0A24, 17])
            await transport.write(23, 18, [0x0514])
            client.read_holding_registers.assert_awaited_once_with(0, count=2, device_id=23)
            client.write_registers.assert_awaited_once_with(18, [0x0514], device_id=23)
            client.connect.assert_not_awaited()
            client.connected = False
            with self.assertRaisesRegex(runtime.DeviceError, "disconnected"):
                await transport.write(23, 18, [0x0514])
            self.assertEqual(client.write_registers.await_count, 1)
            client.connect.assert_not_awaited()

    async def test_unreachable_gateway_is_not_reconnected_once_per_module_in_a_sweep(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        client = MagicMock(connected=False)
        client.connect = AsyncMock(return_value=False)
        with patch.object(runtime, "AsyncModbusTcpClient", return_value=client), patch.object(runtime.time, "monotonic", return_value=100) as clock:
            transport = runtime.TcpTransport({"host": "example", "tcp_port": 502, "timeout": 2})
            for _ in range(20):
                with self.assertRaises(runtime.DeviceError):
                    await transport.connect()
            self.assertEqual(client.connect.await_count, 1, "One failed gateway must not cause twenty connect timeouts")
            clock.return_value = 103
            client.connect.return_value = True
            await transport.connect()
            self.assertEqual(client.connect.await_count, 2)

    async def test_failed_tcp_connect_is_reported_without_sending_requests(self):
        runtime = importlib.import_module("custom_components.domat_mw240.runtime")
        transport_class = getattr(runtime, "TcpTransport", None)
        self.assertIsNotNone(transport_class)
        assert transport_class is not None
        client = MagicMock(connected=False)
        client.connect = AsyncMock(return_value=False)
        with patch.object(runtime, "AsyncModbusTcpClient", return_value=client):
            transport = transport_class({"host": "example", "tcp_port": 502, "timeout": 1})
            with self.assertRaises(runtime.DeviceError):
                await transport.connect()
            client.connect.assert_awaited_once()
            client.read_holding_registers.assert_not_called()
            client.write_registers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
