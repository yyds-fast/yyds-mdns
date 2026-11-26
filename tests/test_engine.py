# -*- coding:utf-8 -*-

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from zeroconf import NonUniqueNameException

from yyds_mdns.core.engine import (
    AsyncMDNSEngine,
    MDNSEngine,
    print_banner,
    print_goodbye,
)
from yyds_mdns.core.exceptions import MDNSServiceConflictError
from yyds_mdns.core.service import MDNSServiceConfig


class TestMDNSEngine(unittest.TestCase):
    def test_sync_engine_real_loopback_lifecycle(self):
        cfg = MDNSServiceConfig(
            name="test-sync-engine",
            port=18080,
            ip="127.0.0.1",
        )
        engine = MDNSEngine(cfg, use_worker_lock=False, verbose=False)
        self.assertFalse(engine.is_registered)
        self.assertFalse(engine._atexit_registered)

        res = engine.start()
        self.assertTrue(res)
        self.assertTrue(engine.is_registered)
        self.assertTrue(engine._atexit_registered)

        # Second start is idempotent
        self.assertTrue(engine.start())

        engine.stop()
        self.assertFalse(engine.is_registered)
        self.assertFalse(engine._atexit_registered)

    def test_async_engine_real_loopback_lifecycle(self):
        async def run_async():
            cfg = MDNSServiceConfig(
                name="test-async-engine",
                port=18082,
                ip="127.0.0.1",
            )
            engine = AsyncMDNSEngine(cfg, use_worker_lock=False, verbose=False)
            self.assertFalse(engine.is_registered)

            res = await engine.start()
            self.assertTrue(res)
            self.assertTrue(engine.is_registered)
            self.assertTrue(engine._atexit_registered)

            await engine.stop()
            self.assertFalse(engine.is_registered)
            self.assertFalse(engine._atexit_registered)

        asyncio.run(run_async())

    def test_sync_engine_conflict_strict_vs_non_strict(self):
        cfg = MDNSServiceConfig(name="conflict-svc", port=18083, ip="127.0.0.1")
        mock_zc = MagicMock()
        mock_zc.register_service.side_effect = NonUniqueNameException()

        with patch("yyds_mdns.core.engine._create_zeroconf", return_value=mock_zc):
            strict_engine = MDNSEngine(
                cfg, use_worker_lock=False, verbose=False, strict=True
            )
            with self.assertRaises(MDNSServiceConflictError):
                strict_engine.start()
            self.assertFalse(strict_engine.is_registered)

            lenient_engine = MDNSEngine(
                cfg, use_worker_lock=False, verbose=False, strict=False
            )
            self.assertFalse(lenient_engine.start())
            self.assertFalse(lenient_engine.is_registered)

    def test_sync_engine_fork_safety_skips_parent_zeroconf_calls(self):
        cfg = MDNSServiceConfig(name="fork-svc", port=18084, ip="127.0.0.1")
        mock_zc = MagicMock()
        with patch("yyds_mdns.core.engine._create_zeroconf", return_value=mock_zc):
            engine = MDNSEngine(cfg, use_worker_lock=False, verbose=False)
            self.assertTrue(engine.start())

            # Simulate forked worker process where PID differs from _owner_pid
            with patch(
                "yyds_mdns.core.engine.os.getpid", return_value=os.getpid() + 50000
            ):
                self.assertFalse(engine.is_registered)
                engine.stop()
                mock_zc.unregister_service.assert_not_called()
                mock_zc.close.assert_not_called()

    def test_allow_name_change_syncs_server_and_standby_failover(self):
        cfg = MDNSServiceConfig(name="dup-api", port=18085, ip="127.0.0.1")
        mock_zc = MagicMock()

        def rename_on_register(info, allow_name_change=False):
            if allow_name_change:
                info.name = "dup-api-2._http._tcp.local."

        mock_zc.register_service.side_effect = rename_on_register
        with patch("yyds_mdns.core.engine._create_zeroconf", return_value=mock_zc):
            engine = MDNSEngine(
                cfg, use_worker_lock=False, verbose=False, allow_name_change=True
            )
            self.assertTrue(engine.start())
            self.assertEqual(cfg.name, "dup-api-2")
            self.assertEqual(cfg.url, "http://dup-api-2.local:18085")
            mock_zc.update_service.assert_called_once()
            engine.stop()

        # Test multi-worker standby failover via watch_interval
        async def run_failover():
            from yyds_mdns.core.lock import WorkerLock

            cfg_fo = MDNSServiceConfig(name="failover-api", port=18086, ip="127.0.0.1")
            primary_lock = WorkerLock(cfg_fo.service_name)
            self.assertTrue(primary_lock.acquire())

            mock_azc = MagicMock()

            async def _noop(*a, **k):
                return None

            mock_azc.async_register_service = MagicMock(side_effect=_noop)
            mock_azc.async_unregister_service = MagicMock(side_effect=_noop)
            mock_azc.async_close = MagicMock(side_effect=_noop)

            with patch(
                "yyds_mdns.core.engine._create_async_zeroconf", return_value=mock_azc
            ):
                standby = AsyncMDNSEngine(
                    cfg_fo,
                    use_worker_lock=True,
                    verbose=False,
                    watch_interval=0.05,
                )
                try:
                    # Initially blocked by primary_lock
                    self.assertFalse(await standby.start())
                    self.assertFalse(standby.is_registered)

                    # Primary worker exits -> standby automatically takes over!
                    primary_lock.release()
                    await asyncio.sleep(0.12)
                    self.assertTrue(standby.is_registered)
                finally:
                    primary_lock.release()
                    await standby.stop()

        asyncio.run(run_failover())

    def test_print_banner_and_goodbye_limited_encoding(self):
        class LimitedEncodingStream:
            encoding = "cp1252"

            def __init__(self):
                self.output = []

            def write(self, s):
                s.encode("cp1252")
                self.output.append(s)

            def flush(self):
                pass

            def isatty(self):
                return False

        orig_stdout = sys.stdout
        s = LimitedEncodingStream()
        sys.stdout = s
        try:
            print_banner("http://test.local:8000", "127.0.0.1")
            print_goodbye("http://test.local:8000")
        finally:
            sys.stdout = orig_stdout

        output_str = "".join(s.output)
        self.assertIn("http://test.local:8000/", output_str)
        self.assertIn("127.0.0.1", output_str)


if __name__ == "__main__":
    unittest.main()
