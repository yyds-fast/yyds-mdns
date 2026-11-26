# -*- coding:utf-8 -*-

import asyncio
import socket
import time
import unittest
from unittest.mock import MagicMock, patch

from zeroconf import ServiceInfo

from yyds_mdns.core.exceptions import MDNSResolutionTimeoutError
from yyds_mdns.core.resolver import (
    MDNSResolver,
    _DiscoveryListener,
    _build_full_service_name,
    _format_service_info,
)


def _make_service_info(
    name: str = "demo",
    stype: str = "_http._tcp.local.",
    ip: str = "192.168.1.20",
    port: int = 8080,
    props: dict = None,
) -> ServiceInfo:
    return ServiceInfo(
        type_=stype,
        name=f"{name}.{stype}",
        addresses=[socket.inet_aton(ip)] if ip else [],
        port=port,
        properties=props or {"ver": "1"},
        server=f"{name}.local.",
    )


class TestResolver(unittest.TestCase):
    def test_build_full_service_name(self):
        stype = "_http._tcp.local."
        self.assertEqual(
            _build_full_service_name("my-api", stype),
            ("my-api._http._tcp.local.", "my-api"),
        )
        self.assertEqual(
            _build_full_service_name("my-api.local", stype),
            ("my-api._http._tcp.local.", "my-api"),
        )
        # Already-qualified name from discover() should NOT double the service type
        self.assertEqual(
            _build_full_service_name("my-api._http._tcp.local.", stype),
            ("my-api._http._tcp.local.", "my-api"),
        )

    def test_format_service_info_empty_addresses_no_fake_localhost(self):
        info = _make_service_info(name="no-ip", ip="", port=8000)
        formatted = _format_service_info(info)
        self.assertIsNone(formatted["ip"])
        self.assertEqual(formatted["addresses"], [])
        self.assertEqual(formatted["url"], "http://no-ip.local:8000")

    def test_listener_dedup_and_goodbye_removal(self):
        listener = _DiscoveryListener()
        zc = MagicMock()
        listener.add_service(zc, "_http._tcp.local.", "a._http._tcp.local.")
        listener.add_service(zc, "_http._tcp.local.", "a._http._tcp.local.")
        listener.add_service(zc, "_http._tcp.local.", "b._http._tcp.local.")
        listener.remove_service(zc, "_http._tcp.local.", "a._http._tcp.local.")
        self.assertEqual(
            listener.snapshot(),
            [("_http._tcp.local.", "b._http._tcp.local.")],
        )

    def test_sync_resolve_and_timeout_exception(self):
        mock_zc = MagicMock()
        mock_zc.get_service_info.return_value = _make_service_info(
            name="my-api", ip="192.168.1.50", port=9000
        )
        with patch("yyds_mdns.core.resolver._create_zeroconf", return_value=mock_zc):
            res = MDNSResolver.resolve("my-api._http._tcp.local.", timeout=0.2)
            self.assertIsNotNone(res)
            self.assertEqual(res["ip"], "192.168.1.50")
            self.assertEqual(res["port"], 9000)
            self.assertEqual(res["url"], "http://my-api.local:9000")
            mock_zc.get_service_info.assert_called_once_with(
                "_http._tcp.local.", "my-api._http._tcp.local.", timeout=200
            )

        mock_zc_none = MagicMock()
        mock_zc_none.get_service_info.return_value = None
        with patch(
            "yyds_mdns.core.resolver._create_zeroconf", return_value=mock_zc_none
        ):
            self.assertIsNone(MDNSResolver.resolve("missing", timeout=0.1))
            with self.assertRaises(MDNSResolutionTimeoutError):
                MDNSResolver.resolve("missing", timeout=0.1, raise_on_timeout=True)

    def test_async_resolve_and_parallel_async_discover(self):
        async def run_test():
            mock_azc = MagicMock()
            mock_azc.zeroconf = MagicMock()

            async def fake_close():
                return None

            mock_azc.async_close = fake_close

            def fake_browser(zc, stype, listener):
                listener.add_service(zc, stype, "s1._http._tcp.local.")
                listener.add_service(zc, stype, "s2._http._tcp.local.")
                listener.add_service(zc, stype, "s3._http._tcp.local.")
                b = MagicMock()

                async def fake_cancel():
                    return None

                b.async_cancel = fake_cancel
                return b

            class FakeAsyncServiceInfo:
                def __init__(self, type_, name):
                    self.type = type_
                    self.name = name
                    self.server = f"{name.split('.')[0]}.local."
                    self.port = 8080
                    self.properties = {b"k": b"v"}

                def load_from_cache(self, zc):
                    return False

                def parsed_addresses(self, version=None):
                    return ["192.168.1.10"]

                async def async_request(self, zc, timeout=1000):
                    await asyncio.sleep(0.08)
                    return True

            with patch(
                "yyds_mdns.core.resolver._create_async_zeroconf",
                return_value=mock_azc,
            ), patch(
                "yyds_mdns.core.resolver.AsyncServiceBrowser",
                side_effect=fake_browser,
            ), patch(
                "yyds_mdns.core.resolver.AsyncServiceInfo",
                FakeAsyncServiceInfo,
            ):
                t0 = time.monotonic()
                services = await MDNSResolver.async_discover(timeout=0.05)
                elapsed = time.monotonic() - t0
                self.assertEqual(len(services), 3)
                # Parallel resolution: ~0.05 + 0.08 = ~0.13s, well below serial 0.05 + 3*0.08 = 0.29s
                self.assertLess(elapsed, 0.24)

                single = await MDNSResolver.async_resolve("s1", timeout=0.1)
                self.assertIsNotNone(single)
                self.assertEqual(single["ip"], "192.168.1.10")

        asyncio.run(run_test())

    def test_resolver_context_manager_reuses_zeroconf_instance(self):
        mock_zc = MagicMock()
        mock_zc.get_service_info.return_value = _make_service_info(
            name="cached-api", ip="192.168.1.77", port=8000
        )
        with patch(
            "yyds_mdns.core.resolver._create_zeroconf", return_value=mock_zc
        ) as mock_factory:
            with MDNSResolver() as resolver:
                r1 = resolver.resolve("cached-api", timeout=0.2)
                r2 = resolver.resolve("cached-api", timeout=0.2)
                self.assertEqual(r1["ip"], "192.168.1.77")
                self.assertEqual(r2["ip"], "192.168.1.77")
                # Must instantiate Zeroconf only ONCE across multiple calls!
                mock_factory.assert_called_once()
                mock_zc.close.assert_not_called()
            mock_zc.close.assert_called_once()

        # Calling instance WITHOUT `with` must still close temporary Zeroconf (no leak!)
        standalone_zc = MagicMock()
        standalone_zc.get_service_info.return_value = None
        with patch(
            "yyds_mdns.core.resolver._create_zeroconf", return_value=standalone_zc
        ):
            r = MDNSResolver()
            self.assertIsNone(r.resolve("missing", timeout=0.1))
            standalone_zc.close.assert_called_once()

        # Invalid timeout values (negative, NaN, Inf, bool) must raise ValueError
        for bad_timeout in (-1.0, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                MDNSResolver.resolve("x", timeout=bad_timeout)  # type: ignore[arg-type]

    def test_resolve_accepts_full_url_and_host_port(self):
        mock_zc = MagicMock()
        mock_zc.get_service_info.return_value = _make_service_info(
            name="secure-api",
            stype="_https._tcp.local.",
            ip="192.168.1.99",
            port=8443,
        )
        with patch("yyds_mdns.core.resolver._create_zeroconf", return_value=mock_zc):
            res = MDNSResolver.resolve(
                "https://secure-api.local:8443/v1/health", timeout=0.2
            )
            self.assertIsNotNone(res)
            self.assertEqual(res["url"], "https://secure-api.local:8443")
            mock_zc.get_service_info.assert_called_once_with(
                "_https._tcp.local.", "secure-api._https._tcp.local.", timeout=200
            )

        # Simulate Python 3.8 zeroconf <= 0.132.x where AddressResolverIPv4 is None
        with patch("yyds_mdns.core.resolver.AddressResolverIPv4", None), patch(
            "yyds_mdns.core.resolver._create_zeroconf", return_value=mock_zc
        ):
            res_py38 = MDNSResolver.resolve(
                "secure-api", service_type="https", timeout=0.2
            )
            self.assertIsNotNone(res_py38)
            self.assertEqual(res_py38["port"], 8443)


if __name__ == "__main__":
    unittest.main()
