# -*- coding:utf-8 -*-

"""
Network utilities for intelligent IP and interface resolution.
"""

import socket
from typing import List, Optional, Tuple

try:
    import ifaddr
except ImportError:
    ifaddr = None

from yyds_mdns.core.exceptions import MDNSNetworkError

# Interface names to ignore (virtual, bridge, docker, VPN, proxy TUNs)
IGNORED_IFACE_PATTERNS = (
    "lo",
    "loopback",
    "docker",
    "br-",
    "veth",
    "tun",
    "tap",
    "utun",
    "wg",
    "wireguard",
    "tailscale",
    "zerotier",
    "mihomo",
    "clash",
    "sing-box",
    "singbox",
    "v2ray",
    "xray",
    "vmnet",
    "vboxnet",
    "virbr",
    "dummy",
    "cni",
    "flannel",
    "calico",
)

# Common physical LAN/WiFi interface patterns across Linux/macOS/Windows
PHYSICAL_IFACE_PATTERNS = (
    # Linux Ethernet & Wireless
    "eth",
    "en",
    "wl",  # matches wlan0, wlp2s0, wls1, wlo1, wlx...
    "wifi",
    "wi-fi",
    "wireless",
    # Windows adapters (English & Chinese system locales)
    "wlan",
    "local area connection",
    "ethernet",
    "以太网",
    "无线",
    "802.11",
)


def is_valid_ipv4(ip: str) -> bool:
    """Check if string is a valid IPv4 address."""
    if not ip or not isinstance(ip, str):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def ip_to_bytes(ip: str) -> bytes:
    """Convert IPv4 string to 4-byte packed representation for mDNS."""
    try:
        return socket.inet_aton(ip)
    except OSError as err:
        raise MDNSNetworkError(f"Invalid IPv4 address '{ip}': {err}") from err


def is_ignored_adapter(name: str) -> bool:
    """Check if adapter name matches virtual/docker/proxy blacklist."""
    name_low = name.lower()
    return any(p in name_low for p in IGNORED_IFACE_PATTERNS)


def is_ignored_ip(ip: str) -> bool:
    """Check if IP is loopback, link-local, or proxy TUN fake-ip."""
    if not is_valid_ipv4(ip):
        return True
    return (
        ip.startswith("127.")
        or ip.startswith("169.254.")
        or ip.startswith("198.18.")  # RFC 2544 / Clash & Mihomo TUN Fake-IP
        or ip.startswith("198.19.")
    )


def score_adapter_ip(adapter_name: str, ip: str) -> int:
    """
    Score adapter + IP combination.
    Prioritizes real physical LAN network cards (e.g. eno1, eth0, wlan0)
    and RFC 1918 private IPs (192.168.x.x, 10.x.x.x).
    """
    if is_ignored_ip(ip):
        return -1000

    if is_ignored_adapter(adapter_name):
        return -500

    score = 0
    name_low = adapter_name.lower()

    # IP ranges: RFC 1918 Class C highest priority for LAN
    if ip.startswith("192.168."):
        score += 1000
    elif ip.startswith("10."):
        score += 800
    elif ip.startswith("172."):
        try:
            second_octet = int(ip.split(".")[1])
            if 16 <= second_octet <= 31:
                score += 600
        except (ValueError, IndexError):
            pass
    else:
        score += 200

    # Physical interface bonus (eno1, eth0, wlan0, wlp2s0, Wi-Fi, WLAN, 无线网络连接, etc.)
    if any(p in name_low for p in PHYSICAL_IFACE_PATTERNS):
        score += 500

    return score


def get_adapter_candidates() -> List[Tuple[int, str, str]]:
    """
    Enumerate all network adapters and IPv4 addresses, scored and sorted.
    Returns list of (score, adapter_name, ip).
    """
    candidates = []
    if ifaddr is not None:
        try:
            for adapter in ifaddr.get_adapters():
                for ip_obj in adapter.ips:
                    if isinstance(ip_obj.ip, str) and is_valid_ipv4(ip_obj.ip):
                        score = score_adapter_ip(adapter.name, ip_obj.ip)
                        candidates.append((score, adapter.name, ip_obj.ip))
        except Exception:
            pass

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def get_all_local_ips() -> List[str]:
    """Retrieve all distinct non-ignored IPv4 addresses associated with local host."""
    candidates = get_adapter_candidates()
    ips = [c[2] for c in candidates if c[0] > 0]
    if not ips:
        # Fallback to all detected if none scored > 0
        ips = [c[2] for c in candidates if not is_ignored_ip(c[2])]
    return ips if ips else ["127.0.0.1"]


def get_lan_ip(
    preferred_ip: Optional[str] = None,
    interface: Optional[str] = None,
    target_probe: str = "8.8.8.8",
) -> str:
    """
    Intelligently determine the real LAN IPv4 address.

    1. If `preferred_ip` is specified, validates and returns it.
    2. If `interface` is specified, finds matching adapter's IPv4.
    3. Scans all physical/virtual adapters, strictly filtering out
       Docker bridges, loopbacks, and proxy TUN devices (e.g. Mihomo/Clash).
    4. Probes OS routing table via dummy UDP socket connection as fallback.
    5. Falls back to 127.0.0.1 if nothing else is available.
    """
    # 1. Explicit preferred IP
    if preferred_ip:
        if not is_valid_ipv4(preferred_ip):
            raise MDNSNetworkError(
                f"Provided preferred_ip '{preferred_ip}' is not valid IPv4."
            )
        return preferred_ip

    # 2. Explicit interface
    if interface and ifaddr is not None:
        for adapter in ifaddr.get_adapters():
            if adapter.name.lower() == interface.lower():
                for ip_obj in adapter.ips:
                    if isinstance(ip_obj.ip, str) and is_valid_ipv4(ip_obj.ip):
                        return ip_obj.ip
        raise MDNSNetworkError(f"Interface '{interface}' not found or has no IPv4.")

    # 3. Adapter-based smart ranking (preferred)
    candidates = get_adapter_candidates()
    valid_candidates = [c for c in candidates if c[0] > 0]
    if valid_candidates:
        return valid_candidates[0][2]

    # 4. Outbound socket probe fallback
    targets = [target_probe, "1.1.1.1", "192.168.1.1", "10.0.0.1"]
    for target in targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 80))
            probed_ip = s.getsockname()[0]
            s.close()
            if is_valid_ipv4(probed_ip) and not is_ignored_ip(probed_ip):
                return probed_ip
        except Exception:
            continue

    # 5. Non-ignored fallback from all candidates
    for c in candidates:
        if not is_ignored_ip(c[2]):
            return c[2]

    return "127.0.0.1"
