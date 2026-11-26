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

    def test_chinese_idna_hostname_and_full_service_name_input(self):
        # Chinese service name keeps readable instance name while IDNA-encoding .local A-record
        cn_cfg = MDNSServiceConfig(name="客厅音箱", port=8000, ip="127.0.0.1")
        self.assertEqual(cn_cfg.name, "客厅音箱")
        self.assertEqual(
            cn_cfg.host_name, f"{'客厅音箱'.encode('idna').decode('ascii')}.local"
        )

        # Emoji name encodes via Punycode; pure punctuation falls back cleanly to service-<suffix>.local
        emoji_cfg = MDNSServiceConfig(name="🚀", port=8000, ip="127.0.0.1")
        self.assertTrue(emoji_cfg.host_name.startswith("xn--"))
        punct_cfg = MDNSServiceConfig(name="!!!", port=8000, ip="127.0.0.1")
        self.assertTrue(punct_cfg.host_name.startswith("service-"))

        # Full service name input does not duplicate ._http._tcp.local.
        full_cfg = MDNSServiceConfig(
            name="my-api._http._tcp.local.", port=8000, ip="127.0.0.1"
        )
        self.assertEqual(full_cfg.name, "my-api")
        self.assertEqual(full_cfg.service_name, "my-api._http._tcp.local.")

        # Total TXT payload > 4000 bytes raises YYDSMDNSError
        huge_props = {f"k{i}": "v" * 200 for i in range(25)}
        with self.assertRaises(YYDSMDNSError):
            MDNSServiceConfig(
                name="app", port=8000, ip="127.0.0.1", properties=huge_props
            )

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
