"""Regression coverage for optional WFTNP server diagnostics."""

import unittest
from uuid import UUID

from wftnp import Endpoint, WftnpClient
from wftnp.server import ServiceSpec, WftnpServer


class TestServerDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_and_raw_frames(self):
        events = []
        service_uuid = UUID("00001826-0000-1000-8000-00805f9b34fb")
        server = WftnpServer(
            host="127.0.0.1", port=0,
            services=[ServiceSpec(service_uuid, ())],
            on_diagnostic=lambda message, raw: events.append((message, raw)),
        )
        await server.start()
        try:
            async with WftnpClient(Endpoint("127.0.0.1", server.port)) as client:
                await client.discover_services()
        finally:
            await server.stop()
        self.assertTrue(any("TCP listening" in msg and not raw for msg, raw in events))
        self.assertTrue(any("client connected" in msg and not raw for msg, raw in events))
        self.assertTrue(any(msg.startswith("RX ") and raw for msg, raw in events))
        self.assertTrue(any(msg.startswith("TX ") and raw for msg, raw in events))

    async def test_callback_failure_does_not_break_server(self):
        def broken_callback(message, raw):
            raise RuntimeError("diagnostic sink unavailable")

        server = WftnpServer(
            host="127.0.0.1", port=0,
            on_diagnostic=broken_callback,
        )
        await server.start()
        try:
            async with WftnpClient(Endpoint("127.0.0.1", server.port)) as client:
                self.assertEqual(len(await client.discover_services()), 0)
        finally:
            await server.stop()


if __name__ == "__main__":
    unittest.main()
