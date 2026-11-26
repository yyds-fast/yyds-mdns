# -*- coding:utf-8 -*-

import unittest
from yyds_mdns.core.exceptions import YYDSMDNSError
from yyds_mdns.core.service import (
    MDNSServiceConfig,
    normalize_host_name,
    normalize_service_type,
)


class TestService(unittest.TestCase):
    def test_normalize_host_name(self):
        self.assertEqual(normalize_host_name("my-app"), "my-app")
        self.assertEqual(normalize_host_name("my-app.local"), "my-app")
        self.assertEqual(normalize_host_name("my-app.local."), "my-app")
        self.assertEqual(normalize_host_name("my-app.LOCAL."), "my-app")
        self.assertEqual(normalize_host_name("test.host."), "test.host")

        with self.assertRaises(YYDSMDNSError):
            normalize_host_name("")

    def test_normalize_service_type(self):
        self.assertEqual(normalize_service_type("http"), "_http._tcp.local.")
        self.assertEqual(
            normalize_service_type("_http._tcp.local."), "_http._tcp.local."
        )
        self.assertEqual(
            normalize_service_type("_HTTP._TCP.local."), "_http._tcp.local."
        )
        self.assertEqual(normalize_service_type("https"), "_https._tcp.local.")
        self.assertEqual(normalize_service_type("http.udp"), "_http._udp.local.")
        self.assertEqual(normalize_service_type("my_proto"), "_my-proto._tcp.local.")
        self.assertEqual(
            normalize_service_type("_custom._udp.local."), "_custom._udp.local."
        )
        with self.assertRaises(YYDSMDNSError):
            normalize_service_type("verylongservicename")

    def test_service_config(self):
        config = MDNSServiceConfig(
            name="test-server",
            port=8080,
            ip="192.168.1.55",
            protocol="http",
            properties={"version": "1.0"},
        )
        self.assertEqual(config.name, "test-server")
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.ip, "192.168.1.55")
        self.assertEqual(config.host_name, "test-server.local")
        self.assertEqual(config.url, "http://test-server.local:8080")
        self.assertEqual(config.service_name, "test-server._http._tcp.local.")

        sinfo = config.to_service_info()
        self.assertEqual(sinfo.port, 8080)
        self.assertEqual(sinfo.server, "test-server.local.")

    def test_service_config_invalid_port_and_label_length(self):
        with self.assertRaises(ValueError):
            MDNSServiceConfig(name="app", port=0)
        with self.assertRaises(ValueError):
            MDNSServiceConfig(name="app", port=70000)
        with self.assertRaises(ValueError):
            MDNSServiceConfig(name="app", port=True)  # type: ignore[arg-type]
        with self.assertRaises(YYDSMDNSError):
            MDNSServiceConfig(name="a" * 64, port=8000, ip="127.0.0.1")
        with self.assertRaises(YYDSMDNSError):
            MDNSServiceConfig(
                name="app", port=8000, ip="127.0.0.1", properties={"k": "x" * 260}
            )

    def test_service_config_spaces_and_int_property_keys(self):
        cfg = MDNSServiceConfig(
            name="My Cool_App",
            port=8080,
            ip="127.0.0.1",
            properties={123: "ok"},  # type: ignore[dict-item]
        )
        self.assertEqual(cfg.name, "My Cool_App")
        self.assertEqual(cfg.host_name, "my-cool-app.local")
        self.assertEqual(cfg.url, "http://my-cool-app.local:8080")
        self.assertEqual(cfg.properties["123"], "ok")

        # Simulate allow_name_change rename sync
        sinfo = cfg.to_service_info()
        sinfo.name = "My Cool_App-2._http._tcp.local."
        self.assertTrue(cfg.sync_from_service_info(sinfo))
        self.assertEqual(cfg.name, "My Cool_App-2")
        self.assertEqual(cfg.url, "http://my-cool-app-2.local:8080")

    def test_service_config_wildcard_ip_auto_resolves(self):
        config = MDNSServiceConfig(name="app", port=8000, ip="0.0.0.0")
        self.assertNotEqual(config.ip, "0.0.0.0")

    def test_service_config_custom_server(self):
        config = MDNSServiceConfig(
            name="app",
            port=8000,
            ip="127.0.0.1",
            server="custom-node.local",
        )
        self.assertEqual(config.host_name, "custom-node.local")
        self.assertEqual(config.server, "custom-node.local.")

    def test_service_config_unique(self):
        config = MDNSServiceConfig(
            name="worker",
            port=8000,
            ip="127.0.0.1",
            unique=True,
        )
        self.assertEqual(len(config.device_suffix), 4)
        self.assertEqual(config.name, f"worker-{config.device_suffix}")
        self.assertEqual(config.host_name, f"worker-{config.device_suffix}.local")
        self.assertEqual(config.properties.get("device_id"), config.device_suffix)

        # Long base name with unique=True is cleanly trimmed within 63 bytes
        long_cfg = MDNSServiceConfig(
            name="x" * 62,
            port=8000,
            ip="127.0.0.1",
            unique=True,
        )
        self.assertLessEqual(len(long_cfg.name.encode("utf-8")), 63)
        self.assertTrue(long_cfg.name.endswith(f"-{long_cfg.device_suffix}"))

    def test_service_config_template_placeholder(self):
        config = MDNSServiceConfig(
            name="cluster-node-{mac}",
            port=9000,
            ip="127.0.0.1",
        )
        self.assertEqual(len(config.device_suffix), 4)
        self.assertEqual(config.name, f"cluster-node-{config.device_suffix}")
        self.assertEqual(config.host_name, f"cluster-node-{config.device_suffix}.local")
        self.assertEqual(config.properties.get("device_id"), config.device_suffix)


if __name__ == "__main__":
    unittest.main()
