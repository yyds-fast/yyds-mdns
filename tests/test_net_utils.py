# -*- coding:utf-8 -*-

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from yyds_mdns.core.exceptions import MDNSNetworkError
from yyds_mdns.core.net_utils import (
    get_adapter_candidates,
    get_all_local_ips,
    get_device_suffix,
    get_lan_ip,
    get_mac_suffix,
    ip_to_bytes,
    is_ignored_adapter,
    is_ignored_ip,
    is_valid_ipv4,
    score_adapter_ip,
)


class TestNetUtils(unittest.TestCase):
    def test_is_valid_ipv4(self):
        self.assertTrue(is_valid_ipv4("192.168.1.1"))
        self.assertTrue(is_valid_ipv4("10.0.0.1"))
        self.assertTrue(is_valid_ipv4("127.0.0.1"))
        self.assertFalse(is_valid_ipv4("192.168.1.256"))
        self.assertFalse(is_valid_ipv4("abc.def.ghi.jkl"))
        self.assertFalse(is_valid_ipv4("192.168.1"))
        self.assertFalse(is_valid_ipv4(""))
        self.assertFalse(is_valid_ipv4(None))  # type: ignore[arg-type]
        # Reject octal leading zeros, whitespace/signs, and non-ASCII digits
        self.assertFalse(is_valid_ipv4("192.168.010.1"))
        self.assertFalse(is_valid_ipv4("192.168. +1 .1"))
        self.assertFalse(is_valid_ipv4("１.２.３.４"))

    def test_ip_to_bytes(self):
        b = ip_to_bytes("192.168.1.100")
        self.assertEqual(len(b), 4)
        self.assertEqual(b, bytes([192, 168, 1, 100]))

        for bad in ("invalid.ip", "192.168.010.1", "192.168. +1 .1", "１.２.３.４"):
            with self.assertRaises(MDNSNetworkError):
                ip_to_bytes(bad)

    def test_is_ignored_adapter_and_ip(self):
        self.assertTrue(is_ignored_adapter("lo"))
        self.assertTrue(is_ignored_adapter("lo0"))
        self.assertTrue(is_ignored_adapter("docker0"))
        self.assertTrue(is_ignored_adapter("br-30d0c76f44f2"))
        self.assertTrue(is_ignored_adapter("Mihomo"))
        self.assertTrue(is_ignored_adapter("veth12345"))
        self.assertTrue(is_ignored_adapter("vEthernet (WSL)"))
        self.assertTrue(is_ignored_adapter("VMware Virtual Ethernet Adapter"))
        self.assertTrue(is_ignored_adapter("Wintun"))
        self.assertTrue(is_ignored_adapter("OpenVPN Wintun"))
        self.assertTrue(is_ignored_adapter("OpenVPN Data Channel Offload"))
        self.assertTrue(is_ignored_adapter("Cloudflare WARP"))
        self.assertTrue(is_ignored_adapter("NordLynx"))
        self.assertTrue(is_ignored_adapter("Generic VPN Tunnel"))
        self.assertTrue(is_ignored_adapter("bridge100"))
        self.assertTrue(is_ignored_adapter("awdl0"))

        # Must NOT false-positive on 'wlo1' or 'Local Area Connection'
        self.assertFalse(is_ignored_adapter("eno1"))
        self.assertFalse(is_ignored_adapter("eth0"))
        self.assertFalse(is_ignored_adapter("wlan0"))
        self.assertFalse(is_ignored_adapter("wlo1"))
        self.assertFalse(is_ignored_adapter("Local Area Connection"))

        self.assertTrue(is_ignored_ip("0.0.0.0"))
        self.assertTrue(is_ignored_ip("255.255.255.255"))
        self.assertTrue(is_ignored_ip("127.0.0.1"))
        self.assertTrue(is_ignored_ip("169.254.1.1"))
        self.assertTrue(is_ignored_ip("198.18.0.1"))  # Mihomo Fake IP
        self.assertFalse(is_ignored_ip("192.168.20.10"))

    def test_score_adapter_ip(self):
        self.assertEqual(score_adapter_ip("lo", "127.0.0.1"), -1000)
        self.assertEqual(score_adapter_ip("Mihomo", "198.18.0.1"), -1000)
        self.assertEqual(score_adapter_ip("docker0", "172.17.0.1"), -500)
        # Physical Linux / macOS / Windows adapters (+500 + 1000 = 1500)
        self.assertEqual(score_adapter_ip("eno1", "192.168.20.10"), 1500)
        self.assertEqual(score_adapter_ip("wlan0", "192.168.1.50"), 1500)
        self.assertEqual(score_adapter_ip("wlp2s0", "192.168.31.102"), 1500)
        self.assertEqual(score_adapter_ip("wlo1", "192.168.31.103"), 1500)
        self.assertEqual(
            score_adapter_ip("Local Area Connection", "192.168.1.55"), 1500
        )
        self.assertEqual(score_adapter_ip("Wi-Fi", "192.168.1.88"), 1500)
        self.assertEqual(score_adapter_ip("无线网络连接", "192.168.0.105"), 1500)
        self.assertEqual(score_adapter_ip("WLAN", "192.168.10.15"), 1500)
        # Generic English words containing 'en' must NOT get +500 physical bonus
        self.assertEqual(score_adapter_ip("Generic Adapter", "10.0.0.5"), 800)
        self.assertEqual(
            score_adapter_ip("OpenVPN Data Channel Offload", "10.8.0.2"), -500
        )

    def test_windows_guid_adapter_handling(self):
        fake_adapters = [
            SimpleNamespace(
                name="{11111111-2222-3333-4444-555555555555}",
                nice_name="VMware Virtual Ethernet Adapter for VMnet8",
                ips=[SimpleNamespace(ip="192.168.88.1", nice_name="以太网 2")],
            ),
            SimpleNamespace(
                name="{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
                nice_name="Intel(R) Wi-Fi 6 AX200 160MHz",
                ips=[SimpleNamespace(ip="192.168.1.66", nice_name="WLAN")],
            ),
        ]
        with patch("yyds_mdns.core.net_utils.ifaddr") as mock_ifaddr:
            mock_ifaddr.get_adapters.return_value = fake_adapters
            candidates = get_adapter_candidates()
            # WLAN should score +1500, VMware VMnet8 should be filtered (-500)
            self.assertEqual(candidates[0], (1500, "WLAN", "192.168.1.66"))
            self.assertEqual(candidates[1], (-500, "以太网 2", "192.168.88.1"))

            # Explicit interface selection by FriendlyName works on Windows
            self.assertEqual(get_lan_ip(interface="WLAN"), "192.168.1.66")

    def test_get_lan_ip_explicit(self):
        ip = get_lan_ip(preferred_ip="192.168.31.200")
        self.assertEqual(ip, "192.168.31.200")

        with self.assertRaises(MDNSNetworkError):
            get_lan_ip(preferred_ip="999.999.999.999")

    def test_get_lan_ip_auto(self):
        ip = get_lan_ip()
        self.assertTrue(is_valid_ipv4(ip))
        # Wildcard 0.0.0.0 should trigger auto-detection rather than returning 0.0.0.0
        auto_from_zero = get_lan_ip(preferred_ip="0.0.0.0")
        self.assertNotEqual(auto_from_zero, "0.0.0.0")

    def test_get_all_local_ips(self):
        ips = get_all_local_ips()
        self.assertIsInstance(ips, list)
        self.assertEqual(len(ips), len(set(ips)))
        for ip in ips:
            self.assertTrue(is_valid_ipv4(ip))

    def test_get_adapter_candidates(self):
        candidates = get_adapter_candidates()
        self.assertIsInstance(candidates, list)

    def test_get_mac_suffix(self):
        suffix = get_mac_suffix()
        self.assertIsInstance(suffix, str)
        self.assertEqual(len(suffix), 4)
        self.assertTrue(all(c in "0123456789abcdef" for c in suffix))

        suffix6 = get_mac_suffix(length=6)
        self.assertEqual(len(suffix6), 6)
        self.assertTrue(suffix6.endswith(suffix))

        self.assertEqual(get_mac_suffix(), suffix)
        self.assertEqual(get_device_suffix(), suffix)

    def test_get_mac_suffix_skips_locally_administered_virtual_mac(self):
        # 0x0242ac110001 is a Docker locally-administered MAC (0x02 U/L bit set)
        with patch(
            "yyds_mdns.core.net_utils._read_linux_interface_mac", return_value=None
        ), patch(
            "yyds_mdns.core.net_utils.uuid.getnode", return_value=0x0242AC110001
        ), patch(
            "yyds_mdns.core.net_utils._get_system_machine_id",
            return_value="stable-machine-uuid-1234",
        ):
            s = get_mac_suffix(length=4)
            # Should use stable machine-id hash rather than volatile 0001 virtual MAC
            self.assertNotEqual(s, "0001")
            self.assertEqual(len(s), 4)


if __name__ == "__main__":
    unittest.main()
