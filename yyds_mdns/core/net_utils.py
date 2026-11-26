# -*- coding:utf-8 -*-

"""
Network utilities for intelligent IP and interface resolution.
"""

import hashlib
import ipaddress
import os
import platform
import re
import socket
import uuid
from typing import Any, Iterable, List, Optional, Tuple

try:
    import ifaddr
except ImportError:
    ifaddr = None

from yyds_mdns.core.exceptions import MDNSNetworkError

# Interface names to ignore (virtual, bridge, docker, VPN, proxy TUNs, P2P)
IGNORED_IFACE_PATTERNS = (
    "lo",
    "loopback",
    "docker",
    "podman",
    "br-",
    "bridge",
    "veth",
    "vethernet",
    "tun",
    "wintun",
    "tunnel",
    "tap",
    "utun",
    "wg",
    "wireguard",
    "openvpn",
    "ovpn",
    "tailscale",
    "zerotier",
    "warp",
    "cloudflarewarp",
    "nordlynx",
    "anyconnect",
    "pangp",
    "fortissl",
    "softether",
    "hamachi",
    "radmin",
    "teredo",
    "isatap",
    "mihomo",
    "clash",
    "sing-box",
    "singbox",
    "v2ray",
    "xray",
    "vmnet",
    "vmware",
    "vboxnet",
    "virtualbox",
    "virbr",
    "xenbr",
    "lxcbr",
    "lxdbr",
    "orbstack",
    "wsl",
    "hyper-v",
    "awdl",
    "llw",
    "anpi",
    "dummy",
    "cni",
    "flannel",
    "calico",
    "cali",
    "geneve",
    "vxlan",
    "ovs-system",
)

# Boundary-aware regex so short tokens like 'lo', 'tun', 'tap', 'wg' never
# falsely match real physical adapters such as 'wlo1' or 'Local Area Connection',
# while catching VPN/tunnel/bridge adapters across Linux, macOS, and Windows.
_IGNORED_IFACE_REGEX = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:"
    r"lo\d*|loopback|docker\d*|podman\d*|br-[a-z0-9_.-]*|bridge\d*|"
    r"veth[a-z0-9_.-]*|vethernet|"
    r"tun\d*|wintun[a-z0-9_.-]*|tunnel[a-z0-9_.-]*|tap\d*|utun\d*|wg\d*|wireguard|"
    r"openvpn[a-z0-9_.-]*|ovpn[a-z0-9_.-]*|"
    r"tailscale\d*|zerotier[a-z0-9_.-]*|"
    r"warp|cloudflarewarp|nordlynx|anyconnect|pangp[a-z0-9_.-]*|"
    r"fortissl[a-z0-9_.-]*|softether[a-z0-9_.-]*|hamachi[a-z0-9_.-]*|radmin[a-z0-9_.-]*|"
    r"teredo|isatap|"
    r"mihomo|clash|sing-box|singbox|v2ray|xray|"
    r"vmnet\d*|vmware|vboxnet\d*|virtualbox|"
    r"virbr\d*|xenbr\d*|lxcbr\d*|lxdbr\d*|orbstack[a-z0-9_.-]*|wsl\d*|hyper-v|"
    r"awdl\d*|llw\d*|anpi\d*|"
    r"dummy\d*|cni\d*|flannel[a-z0-9_.-]*|calico[a-z0-9_.-]*|cali[a-z0-9_.-]*|"
    r"geneve\d*|vxlan\d*|ovs-system"
    r")"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)

# Common physical LAN/WiFi interface patterns across Linux/macOS/Windows
PHYSICAL_IFACE_PATTERNS = (
    "eth",
    "en",
    "wl",
    "wifi",
    "wi-fi",
    "wireless",
    "wlan",
    "local area connection",
    "ethernet",
    "以太网",
    "无线",
    "802.11",
)

# Strict prefix regex for OS device names (eno1, en0, enp3s0, enx..., eth0, wlan0, wlp2s0, wlo1, em1)
# so short prefixes like 'en' or 'eth' never match words like 'OpenVPN', 'Generic', or 'SoftEther'.
_PHYSICAL_DEV_PREFIX_REGEX = re.compile(
    r"^(?:eth\d*|en(?:o|p|s|x|\d)[a-z0-9_.-]*|wl(?:an|p|o|s|x|\d)[a-z0-9_.-]*|em\d+)$",
    re.IGNORECASE,
)

# Word-boundary descriptive names (Windows / macOS / NetworkManager FriendlyName & Description)
_PHYSICAL_DESC_REGEX = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:ethernet|wi-fi|wifi|wireless|wlan|local area connection|802\.11)"
    r"(?:$|[^a-z0-9])"
    r"|以太网|无线",
    re.IGNORECASE,
)

_WINDOWS_GUID_REGEX = re.compile(r"^\{[0-9a-fA-F-]{36}\}$")


def is_valid_ipv4(ip: str) -> bool:
    """Check if string is a strict valid IPv4 address (rejects leading zeros, spaces, non-ASCII)."""
    if not ip or not isinstance(ip, str):
        return False
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError:
        return False


def ip_to_bytes(ip: str) -> bytes:
    """Convert IPv4 string to 4-byte packed representation for mDNS."""
    if not isinstance(ip, str):
        raise MDNSNetworkError(f"Invalid IPv4 address '{ip}': expected string")
    try:
        return ipaddress.IPv4Address(ip).packed
    except ValueError as err:
        raise MDNSNetworkError(f"Invalid IPv4 address '{ip}': {err}") from err


def is_ignored_adapter(name: str) -> bool:
    """Check if adapter name matches virtual/docker/proxy/VPN blacklist."""
    if not name:
        return False
    return bool(_IGNORED_IFACE_REGEX.search(name))


def is_ignored_ip(ip: str) -> bool:
    """Check if IP is unspecified, loopback, link-local, multicast, broadcast, or proxy TUN fake-ip."""
    if not is_valid_ipv4(ip):
        return True
    addr = ipaddress.IPv4Address(ip)
    if (
        addr.is_unspecified
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or ip == "255.255.255.255"
    ):
        return True
    return (
        ip.startswith("0.")
        or ip.startswith("198.18.")  # RFC 2544 / Clash & Mihomo TUN Fake-IP
        or ip.startswith("198.19.")
    )


def _has_physical_pattern(name: str) -> bool:
    stripped = name.strip()
    if not stripped or _WINDOWS_GUID_REGEX.match(stripped):
        return False
    if _PHYSICAL_DEV_PREFIX_REGEX.match(stripped):
        return True
    return bool(_PHYSICAL_DESC_REGEX.search(stripped))


def score_adapter_ip(
    adapter_name: str,
    ip: str,
    extra_names: Optional[Iterable[str]] = None,
) -> int:
    """
    Score adapter + IP combination.
    Prioritizes real physical LAN network cards (e.g. eno1, eth0, wlan0, wlo1, WLAN, 以太网)
    and RFC 1918 private IPs (192.168.x.x, 10.x.x.x).
    """
    if is_ignored_ip(ip):
        return -1000

    all_names = [n for n in [adapter_name, *(extra_names or ())] if n]
    if any(is_ignored_adapter(n) for n in all_names):
        return -500

    score = 0

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

    # Physical interface bonus (eno1, eth0, wlan0, wlp2s0, wlo1, Wi-Fi, WLAN, 无线网络连接, etc.)
    if any(_has_physical_pattern(n) for n in all_names):
        score += 500

    return score


def _extract_adapter_labels(adapter: Any, ip_obj: Any) -> Tuple[str, List[str]]:
    """
    Extract human-friendly display name and all candidate names for an ifaddr adapter/IP.
    On Windows, `adapter.name` is a GUID while `ip_obj.nice_name` holds FriendlyName (e.g. 'WLAN')
    and `adapter.nice_name` holds hardware Description (e.g. 'Intel(R) Wi-Fi 6...').
    """
    raw_name = getattr(adapter, "name", "") or ""
    adapter_nice = getattr(adapter, "nice_name", "") or ""
    ip_nice = getattr(ip_obj, "nice_name", "") or ""

    if _WINDOWS_GUID_REGEX.match(raw_name):
        display_name = ip_nice or adapter_nice or raw_name
    else:
        display_name = raw_name or ip_nice or adapter_nice

    all_names: List[str] = []
    for n in (raw_name, ip_nice, adapter_nice):
        if n and n not in all_names:
            all_names.append(n)
    return display_name, all_names


def get_adapter_candidates() -> List[Tuple[int, str, str]]:
    """
    Enumerate all network adapters and IPv4 addresses, scored and sorted.
    Returns list of (score, adapter_display_name, ip).
    """
    candidates: List[Tuple[int, str, str]] = []
    if ifaddr is not None:
        try:
            for adapter in ifaddr.get_adapters():
                for ip_obj in adapter.ips:
                    if isinstance(ip_obj.ip, str) and is_valid_ipv4(ip_obj.ip):
                        display_name, all_names = _extract_adapter_labels(
                            adapter, ip_obj
                        )
                        score = score_adapter_ip(
                            display_name, ip_obj.ip, extra_names=all_names
                        )
                        candidates.append((score, display_name, ip_obj.ip))
        except Exception:
            pass

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def get_local_interface_ips(include_loopback: bool = True) -> List[str]:
    """Return all valid IPv4 addresses currently assigned to local OS interfaces."""
    ips: List[str] = []
    if ifaddr is not None:
        try:
            for adapter in ifaddr.get_adapters():
                for ip_obj in adapter.ips:
                    if isinstance(ip_obj.ip, str) and is_valid_ipv4(ip_obj.ip):
                        if not include_loopback and ip_obj.ip.startswith("127."):
                            continue
                        if ip_obj.ip not in ips:
                            ips.append(ip_obj.ip)
        except Exception:
            pass
    return ips


def get_all_local_ips() -> List[str]:
    """Retrieve all distinct non-ignored IPv4 addresses associated with local host."""
    candidates = get_adapter_candidates()
    ips = list(dict.fromkeys(c[2] for c in candidates if c[0] > 0))
    if not ips:
        ips = list(dict.fromkeys(c[2] for c in candidates if not is_ignored_ip(c[2])))
    return ips if ips else ["127.0.0.1"]


def get_lan_ip(
    preferred_ip: Optional[str] = None,
    interface: Optional[str] = None,
    target_probe: str = "8.8.8.8",
) -> str:
    """
    Intelligently determine the real LAN IPv4 address.

    1. If `preferred_ip` is specified (and not '0.0.0.0'), validates and returns it.
    2. If `interface` is specified, matches adapter name / FriendlyName / Description.
    3. Scans all physical/virtual adapters, strictly filtering out
       Docker bridges, loopbacks, VPNs, and proxy TUN devices (e.g. Mihomo/Clash).
    4. Probes OS routing table via dummy UDP socket connection as fallback.
    5. Falls back to 127.0.0.1 if nothing else is available.
    """
    # 1. Explicit preferred IP (treat 0.0.0.0 as auto-detect)
    if preferred_ip and preferred_ip != "0.0.0.0":
        if not is_valid_ipv4(preferred_ip):
            raise MDNSNetworkError(
                f"Provided preferred_ip '{preferred_ip}' is not valid IPv4."
            )
        return preferred_ip

    # 2. Explicit interface (supports Linux/macOS name as well as Windows FriendlyName/Description/GUID)
    if interface and ifaddr is not None:
        target_low = interface.strip().lower()
        for adapter in ifaddr.get_adapters():
            for ip_obj in adapter.ips:
                if isinstance(ip_obj.ip, str) and is_valid_ipv4(ip_obj.ip):
                    _, all_names = _extract_adapter_labels(adapter, ip_obj)
                    if any(n.lower() == target_low for n in all_names):
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
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 80))
                probed_ip = s.getsockname()[0]
            if is_valid_ipv4(probed_ip) and not is_ignored_ip(probed_ip):
                return probed_ip
        except Exception:
            continue

    # 5. Non-ignored fallback from all candidates
    for c in candidates:
        if not is_ignored_ip(c[2]):
            return c[2]

    return "127.0.0.1"


def _read_linux_interface_mac(interface: str) -> Optional[str]:
    """Read physical MAC address directly from Linux sysfs."""
    try:
        safe_iface = os.path.basename(interface.strip())
        if not safe_iface or safe_iface in (".", ".."):
            return None
        path = f"/sys/class/net/{safe_iface}/address"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                mac = f.read().strip().replace(":", "").replace("-", "").lower()
                if mac and mac != "000000000000" and len(mac) == 12:
                    return mac
    except Exception:
        pass
    return None


def _get_system_machine_id() -> Optional[str]:
    """Retrieve platform-specific persistent hardware/OS machine identifier."""
    system = platform.system()
    try:
        if system == "Linux":
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content
                except FileNotFoundError:
                    continue
        elif system == "Darwin":
            import subprocess

            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
                errors="ignore",
                timeout=2,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split("=")
                    if len(parts) > 1:
                        val = parts[-1].replace('"', "").strip()
                        if val:
                            return val
        elif system == "Windows":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "MachineGuid")
                if val:
                    return str(val).strip()
    except Exception:
        pass
    return None


def _md5_hex(data: bytes) -> str:
    """FIPS-safe MD5 hex digest for non-cryptographic hardware ID hashing."""
    try:
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except TypeError:
        return hashlib.md5(data).hexdigest()


def get_mac_suffix(length: int = 4, interface: Optional[str] = None) -> str:
    """
    Return a stable, deterministic hexadecimal hardware suffix of the local machine.

    1. If `interface` is given (or Linux primary physical adapter exists), attempts to read its MAC.
    2. Fallback to `uuid.getnode()` if it returns a globally unique burned-in IEEE MAC
       (neither multicast/random bit 0x01 nor locally-administered virtual/randomized bit 0x02).
    3. Fallback to persistent OS machine ID hash (/etc/machine-id, IOPlatformUUID, MachineGuid),
       or locally-administered MAC if no persistent OS machine ID is available.
    4. Slices the last `length` characters in lowercase.
    """
    if isinstance(length, bool) or length <= 0:
        raise ValueError(f"suffix length must be positive, got {length}")

    mac_hex: Optional[str] = None

    # 1. Try reading directly from Linux sysfs if interface specified or primary interface available
    if interface:
        mac_hex = _read_linux_interface_mac(interface)

    if not mac_hex:
        candidates = get_adapter_candidates()
        valid = [c for c in candidates if c[0] > 0]
        if valid:
            primary_adapter_name = valid[0][1]
            mac_hex = _read_linux_interface_mac(primary_adapter_name)

    # 2. uuid.getnode() — distinguish burned-in universal MAC vs locally-administered virtual MAC
    local_admin_mac: Optional[str] = None
    if not mac_hex:
        try:
            node = uuid.getnode()
            first_octet = (node >> 40) & 0xFF
            is_multicast_random = bool(first_octet & 0x01)
            is_locally_administered = bool(first_octet & 0x02)
            formatted = f"{node:012x}"
            if not is_multicast_random and formatted != "000000000000":
                if not is_locally_administered:
                    mac_hex = formatted
                else:
                    local_admin_mac = formatted
        except Exception:
            pass

    # 3. Persistent OS machine ID hash (stable across WSL2/Docker/macOS virtual MAC regeneration)
    if not mac_hex:
        machine_id = _get_system_machine_id()
        if machine_id:
            mac_hex = _md5_hex(machine_id.encode("utf-8"))
        elif local_admin_mac:
            mac_hex = local_admin_mac
        else:
            fallback_id = f"{platform.node()}-{platform.machine()}"
            mac_hex = _md5_hex(fallback_id.encode("utf-8"))

    clean_hex = re.sub(r"[^0-9a-fA-F]", "", mac_hex).lower()
    if len(clean_hex) < length:
        clean_hex = clean_hex.rjust(length, "0")
    return clean_hex[-length:]


get_device_suffix = get_mac_suffix
