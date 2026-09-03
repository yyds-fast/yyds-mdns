# -*- coding:utf-8 -*-

import asyncio
import unittest
from yyds_mdns.core.engine import AsyncMDNSEngine, MDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


class TestMDNSEngine(unittest.TestCase):
    def test_sync_engine_lifecycle(self):
        cfg = MDNSServiceConfig(
            name="test-sync-engine",
            port=18080,
            ip="127.0.0.1",
        )
        engine = MDNSEngine(cfg, use_worker_lock=False)
        self.assertFalse(engine.is_registered)

        res = engine.start()
        self.assertTrue(res)
        self.assertTrue(engine.is_registered)

        # Second start is idempotent
        self.assertTrue(engine.start())

        engine.stop()
        self.assertFalse(engine.is_registered)

    def test_sync_engine_context_manager(self):
        cfg = MDNSServiceConfig(
            name="test-sync-cm",
            port=18081,
            ip="127.0.0.1",
        )
        with MDNSEngine(cfg, use_worker_lock=False) as engine:
            self.assertTrue(engine.is_registered)
        self.assertFalse(engine.is_registered)

    def test_async_engine_lifecycle(self):
        async def run_async():
            cfg = MDNSServiceConfig(
                name="test-async-engine",
                port=18082,
                ip="127.0.0.1",
            )
            engine = AsyncMDNSEngine(cfg, use_worker_lock=False)
            self.assertFalse(engine.is_registered)

            res = await engine.start()
            self.assertTrue(res)
            self.assertTrue(engine.is_registered)

            await engine.stop()
            self.assertFalse(engine.is_registered)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
