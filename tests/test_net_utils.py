# -*- coding:utf-8 -*-

import unittest
from yyds_mdns.core.exceptions import MDNSNetworkError
from yyds_mdns.core.net_utils import (
    get_adapter_candidates,
    get_all_local_ips,
    get_lan_ip,
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
        self.assertFalse(is_valid_ipv4(None))

    def test_ip_to_bytes(self):
        b = ip_to_bytes("192.168.1.100")
        self.assertEqual(len(b), 4)
        self.assertEqual(b, bytes([192, 168, 1, 100]))

        with self.assertRaises(MDNSNetworkError):
            ip_to_bytes("invalid.ip")

    def test_is_ignored_adapter_and_ip(self):
        self.assertTrue(is_ignored_adapter("docker0"))
        self.assertTrue(is_ignored_adapter("br-30d0c76f44f2"))
        self.assertTrue(is_ignored_adapter("Mihomo"))
        self.assertTrue(is_ignored_adapter("veth12345"))
        self.assertFalse(is_ignored_adapter("eno1"))
        self.assertFalse(is_ignored_adapter("eth0"))
        self.assertFalse(is_ignored_adapter("wlan0"))

        self.assertTrue(is_ignored_ip("127.0.0.1"))
        self.assertTrue(is_ignored_ip("169.254.1.1"))
        self.assertTrue(is_ignored_ip("198.18.0.1"))  # Mihomo Fake IP
        self.assertFalse(is_ignored_ip("192.168.20.10"))

    def test_score_adapter_ip(self):
        self.assertEqual(score_adapter_ip("lo", "127.0.0.1"), -1000)
        self.assertEqual(score_adapter_ip("Mihomo", "198.18.0.1"), -1000)
        self.assertEqual(score_adapter_ip("docker0", "172.17.0.1"), -500)
        # eno1 has physical prefix (+500) + 192.168 (+1000) = 1500
        self.assertEqual(score_adapter_ip("eno1", "192.168.20.10"), 1500)
        # Wi-Fi on Linux / macOS / Windows
        self.assertEqual(score_adapter_ip("wlan0", "192.168.1.50"), 1500)
        self.assertEqual(score_adapter_ip("wlp2s0", "192.168.31.102"), 1500)
        self.assertEqual(score_adapter_ip("Wi-Fi", "192.168.1.88"), 1500)
        self.assertEqual(score_adapter_ip("无线网络连接", "192.168.0.105"), 1500)
        self.assertEqual(score_adapter_ip("WLAN", "192.168.10.15"), 1500)

    def test_get_lan_ip_explicit(self):
        ip = get_lan_ip(preferred_ip="192.168.31.200")
        self.assertEqual(ip, "192.168.31.200")

        with self.assertRaises(MDNSNetworkError):
            get_lan_ip(preferred_ip="999.999.999.999")

    def test_get_lan_ip_auto(self):
        ip = get_lan_ip()
        self.assertTrue(is_valid_ipv4(ip))
        self.assertFalse(is_ignored_ip(ip))

    def test_get_all_local_ips(self):
        ips = get_all_local_ips()
        self.assertIsInstance(ips, list)
        for ip in ips:
            self.assertTrue(is_valid_ipv4(ip))

    def test_get_adapter_candidates(self):
        candidates = get_adapter_candidates()
        self.assertIsInstance(candidates, list)

    def test_get_mac_suffix(self):
        from yyds_mdns.core.net_utils import get_device_suffix, get_mac_suffix

        suffix = get_mac_suffix()
        self.assertIsInstance(suffix, str)
        self.assertEqual(len(suffix), 4)
        self.assertTrue(all(c in "0123456789abcdef" for c in suffix))

        # Test custom length
        suffix6 = get_mac_suffix(length=6)
        self.assertEqual(len(suffix6), 6)
        self.assertTrue(suffix6.endswith(suffix))

        # Determinism test
        self.assertEqual(get_mac_suffix(), suffix)
        self.assertEqual(get_device_suffix(), suffix)


if __name__ == "__main__":
    unittest.main()
