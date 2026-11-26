# -*- coding:utf-8 -*-

"""
mDNS Service configuration and DNS-SD / A-record builder.
"""

import os
import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from zeroconf import ServiceInfo

from yyds_mdns.core.exceptions import YYDSMDNSError
from yyds_mdns.core.net_utils import (
    get_lan_ip,
    get_mac_suffix,
    ip_to_bytes,
    is_valid_ipv4,
)

MAX_DNS_LABEL_BYTES = 63
MAX_SERVICE_TYPE_BYTES = 15
MAX_TXT_PAIR_BYTES = 255
MAX_TXT_TOTAL_BYTES = 4000
_SERVICE_TYPE_LABEL_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_ASCII_HOST_LABEL_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def resolve_port(port: Optional[int] = None, default: int = 8000) -> int:
    """
    Resolve port number from argument or standard environment variables (PORT, SERVER_PORT, WEB_PORT).
    """
    if port is not None:
        if isinstance(port, bool):
            raise ValueError(
                f"Port must be an integer between 1 and 65535, got {port!r}"
            )
        p = int(port)
        if not (1 <= p <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {port}")
        return p

    for env_key in ("PORT", "SERVER_PORT", "WEB_PORT"):
        val = os.environ.get(env_key)
        if val and val.isdigit():
            p = int(val)
            if 1 <= p <= 65535:
                return p

    return default


def normalize_service_type(protocol: str) -> str:
    """
    Ensure service type is formatted properly according to DNS-SD RFC 6763.
    e.g. 'http' -> '_http._tcp.local.', '_HTTP._TCP.local.' -> '_http._tcp.local.',
         'http.udp' -> '_http._udp.local.', 'my_proto' -> '_my-proto._tcp.local.'
    """
    raw = (protocol or "").strip().rstrip(".").lower()
    if not raw:
        return "_http._tcp.local."
    if raw.endswith(".local"):
        raw = raw[:-6].rstrip(".")
    if not raw:
        return "_http._tcp.local."

    parts = [part for part in raw.split(".") if part]
    if parts[-1] in ("tcp", "_tcp"):
        transport = "_tcp"
        svc_parts = parts[:-1]
    elif parts[-1] in ("udp", "_udp"):
        transport = "_udp"
        svc_parts = parts[:-1]
    else:
        transport = "_tcp"
        svc_parts = parts

    if not svc_parts:
        svc_parts = ["_http"]

    # Validate primary service type token per RFC 6763 Section 7.2 (<= 15 bytes, [a-z0-9-])
    primary_raw = svc_parts[-1].lstrip("_").replace("_", "-")
    if not primary_raw or not _SERVICE_TYPE_LABEL_REGEX.match(primary_raw):
        raise YYDSMDNSError(
            f"Invalid DNS-SD service type '{protocol}': must contain ASCII letters, digits, or hyphens."
        )
    if len(primary_raw.encode("utf-8")) > MAX_SERVICE_TYPE_BYTES:
        raise YYDSMDNSError(
            f"DNS-SD service type '{primary_raw}' exceeds {MAX_SERVICE_TYPE_BYTES} bytes (RFC 6763 limit)."
        )

    normalized_parts = []
    for idx, p in enumerate(svc_parts):
        if idx == len(svc_parts) - 1:
            normalized_parts.append(f"_{primary_raw}")
        elif p == "_sub":
            normalized_parts.append("_sub")
        else:
            clean_sub = p if p.startswith("_") else f"_{p}"
            if len(clean_sub.encode("utf-8")) > MAX_DNS_LABEL_BYTES:
                raise YYDSMDNSError(
                    f"DNS-SD subtype '{clean_sub}' exceeds {MAX_DNS_LABEL_BYTES} bytes."
                )
            normalized_parts.append(clean_sub)

    return f"{'.'.join(normalized_parts)}.{transport}.local."


def normalize_host_name(name: str, service_type: Optional[str] = None) -> str:
    """
    Normalize service or host name:
    'my-app.local' -> 'my-app'
    'my-app.local.' -> 'my-app'
    'my-app._http._tcp.local.' -> 'my-app' (when service_type provided)
    """
    if not isinstance(name, str):
        raise YYDSMDNSError("Host or service name must be a string.")
    clean = name.strip()
    if "://" in clean:
        parsed = urlsplit(clean)
        if not parsed.hostname:
            raise YYDSMDNSError(f"Invalid URL or host name: '{name}'")
        clean = parsed.hostname
    if service_type:
        dotted = clean if clean.endswith(".") else f"{clean}."
        suffix = f".{service_type.lower()}"
        if dotted.lower().endswith(suffix):
            clean = dotted[: -len(suffix)]

    low = clean.lower()
    if low.endswith(".local."):
        clean = clean[:-7]
    elif low.endswith(".local"):
        clean = clean[:-6]
    clean = clean.rstrip(".")
    if not clean:
        raise YYDSMDNSError("Host or service name cannot be empty.")
    return clean


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate string cleanly on UTF-8 character boundary within max_bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip("-")


def _validate_dns_labels(name: str, context: str = "name") -> None:
    """Validate that every dot-separated label is non-empty and <= 63 UTF-8 bytes."""
    labels = name.split(".")
    for label in labels:
        if not label:
            raise YYDSMDNSError(f"Invalid empty DNS label in {context}: '{name}'")
        if len(label.encode("utf-8")) > MAX_DNS_LABEL_BYTES:
            raise YYDSMDNSError(
                f"DNS label '{label}' in {context} exceeds {MAX_DNS_LABEL_BYTES} bytes."
            )


def _encode_host_label(raw_label: str) -> str:
    """
    Convert a single host label into an RFC 1123 / IDNA Punycode ASCII label
    so browsers resolving `http://客厅音箱.local:8000` (which query `xn--...local.`)
    match the registered mDNS A-record.
    """
    cleaned = re.sub(r"[\s_:/?#\[\]@!$&'()*+,;=]+", "-", raw_label.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-").lower()
    if not cleaned:
        return ""

    if cleaned.isascii():
        ascii_clean = re.sub(r"[^a-z0-9-]+", "-", cleaned)
        ascii_clean = re.sub(r"-{2,}", "-", ascii_clean).strip("-")
        return ascii_clean[:MAX_DNS_LABEL_BYTES].rstrip("-")

    # Internationalized domain label (e.g. Chinese/Japanese/accented characters) -> IDNA Punycode
    try:
        puny = cleaned.encode("idna").decode("ascii").lower().strip("-")
        if (
            puny
            and len(puny) <= MAX_DNS_LABEL_BYTES
            and _ASCII_HOST_LABEL_REGEX.match(puny)
        ):
            return puny
    except UnicodeError:
        pass

    # Strip non-ASCII symbols (e.g. emojis in `"🚀 my-api"` -> `"my-api"`)
    ascii_fallback = re.sub(r"[^a-z0-9-]+", "-", cleaned)
    ascii_fallback = re.sub(r"-{2,}", "-", ascii_fallback).strip("-")
    return ascii_fallback[:MAX_DNS_LABEL_BYTES].rstrip("-")


def _normalize_server_hostname(
    raw_host: str, allow_fallback: bool = False, interface: Optional[str] = None
) -> str:
    """
    Normalize a host string into a URL-safe, browser-resolvable .local hostname prefix.
    """
    labels = []
    for raw_label in raw_host.split("."):
        encoded_label = _encode_host_label(raw_label)
        if encoded_label:
            labels.append(encoded_label)

    if not labels:
        if allow_fallback:
            labels = [f"service-{get_mac_suffix(4, interface=interface)}"]
        else:
            raise YYDSMDNSError(
                f"Cannot derive a valid DNS hostname from '{raw_host}'."
            )
    result = ".".join(labels)
    _validate_dns_labels(result, context="server")
    return result


def _normalize_txt_properties(props: Optional[Dict[Any, Any]]) -> Dict[str, Any]:
    """
    Normalize TXT property keys to strings and validate RFC 6763 per-entry (<= 255 bytes)
    and total TXT payload size limits.
    """
    if not props:
        return {}
    normalized: Dict[str, Any] = {}
    total_wire_bytes = 0
    for raw_k, val in props.items():
        key = (
            raw_k.decode("utf-8", errors="replace")
            if isinstance(raw_k, bytes)
            else str(raw_k)
        )
        if not key or "=" in key:
            raise YYDSMDNSError(
                f"Invalid TXT property key {raw_k!r}: key must be non-empty and cannot contain '='."
            )
        key_bytes = key.encode("utf-8")
        if val is None:
            pair_len = len(key_bytes)
        elif isinstance(val, bytes):
            pair_len = len(key_bytes) + 1 + len(val)
        else:
            pair_len = len(key_bytes) + 1 + len(str(val).encode("utf-8"))

        if pair_len > MAX_TXT_PAIR_BYTES:
            raise YYDSMDNSError(
                f"TXT property '{key}' exceeds {MAX_TXT_PAIR_BYTES} bytes (got {pair_len} bytes)."
            )
        total_wire_bytes += 1 + pair_len
        if total_wire_bytes > MAX_TXT_TOTAL_BYTES:
            raise YYDSMDNSError(
                f"Total TXT properties payload exceeds {MAX_TXT_TOTAL_BYTES} bytes."
            )
        normalized[key] = val
    return normalized


def _append_unique_suffix(base: str, suffix: str) -> str:
    """Append -{suffix} while keeping label length <= 63 UTF-8 bytes."""
    suffix_part = f"-{suffix}"
    max_base_bytes = MAX_DNS_LABEL_BYTES - len(suffix_part.encode("utf-8"))
    if max_base_bytes <= 0:
        raise YYDSMDNSError("Device suffix is too long for DNS label limit (63 bytes).")
    trimmed_base = _truncate_utf8_bytes(base, max_base_bytes)
    return f"{trimmed_base}{suffix_part}"


def infer_url_scheme(service_type: str) -> str:
    """Infer URL scheme from DNS-SD service type."""
    low = (service_type or "").lower()
    if low.startswith("_https.") or "._https." in low or "https" in low:
        return "https"
    if low.startswith("_wss.") or "._wss." in low:
        return "wss"
    if low.startswith("_ws.") or "._ws." in low:
        return "ws"
    return "http"


class MDNSServiceConfig:
    """Configuration for an mDNS service registration."""

    def __init__(
        self,
        name: str,
        port: Optional[int] = None,
        ip: Optional[str] = None,
        interface: Optional[str] = None,
        protocol: str = "_http._tcp.local.",
        properties: Optional[Dict[str, Any]] = None,
        server: Optional[str] = None,
        unique: bool = False,
        suffix_length: int = 4,
        **kwargs: Any,
    ):
        if not isinstance(name, str) or not name.strip():
            raise YYDSMDNSError("Host or service name cannot be empty.")

        self.raw_name = name
        self.interface = interface
        self.unique = bool(unique or kwargs.get("unique_suffix", False))
        self.suffix_length = suffix_length
        self.device_suffix = ""
        self._custom_server = bool(server)

        # Normalize protocol early so full service names in `name` can strip `.{self.protocol}`
        self.protocol = normalize_service_type(protocol)

        # Resolve suffix if template placeholder or unique flag is present
        has_placeholder = (
            ("{mac}" in name)
            or ("{id}" in name)
            or bool(server and ("{mac}" in server or "{id}" in server))
        )
        if has_placeholder or self.unique:
            self.device_suffix = get_mac_suffix(
                length=suffix_length, interface=interface
            )

        resolved_name = name
        if "{mac}" in resolved_name or "{id}" in resolved_name:
            resolved_name = resolved_name.replace("{mac}", self.device_suffix).replace(
                "{id}", self.device_suffix
            )
            self.name = normalize_host_name(resolved_name, service_type=self.protocol)
        elif self.unique:
            base = normalize_host_name(resolved_name, service_type=self.protocol)
            self.name = _append_unique_suffix(base, self.device_suffix)
        else:
            self.name = normalize_host_name(resolved_name, service_type=self.protocol)

        if len(self.name.encode("utf-8")) > MAX_DNS_LABEL_BYTES:
            raise YYDSMDNSError(
                f"Service instance name '{self.name}' exceeds {MAX_DNS_LABEL_BYTES} bytes."
            )

        self.port = resolve_port(port, default=8000)

        # Treat None, empty, or '0.0.0.0' (bind-all wildcard) as automatic LAN IP detection
        self.auto_ip = ip in (None, "", "0.0.0.0")
        effective_ip = None if self.auto_ip else ip
        self.ip = effective_ip if effective_ip else get_lan_ip(interface=interface)
        if not is_valid_ipv4(self.ip) or self.ip in ("0.0.0.0", "255.255.255.255"):
            raise ValueError(f"Invalid IPv4 address: {self.ip}")

        self.properties = _normalize_txt_properties(properties)
        if self.device_suffix and "device_id" not in self.properties:
            self.properties["device_id"] = self.device_suffix

        # Set server hostname (must end with .local. and be browser/IDNA resolvable)
        if server:
            resolved_server = server
            if "{mac}" in resolved_server or "{id}" in resolved_server:
                resolved_server = resolved_server.replace(
                    "{mac}", self.device_suffix
                ).replace("{id}", self.device_suffix)
                clean_server = normalize_host_name(resolved_server)
            elif self.unique:
                base_srv = normalize_host_name(resolved_server)
                clean_server = _append_unique_suffix(base_srv, self.device_suffix)
            else:
                clean_server = normalize_host_name(resolved_server)
            safe_server = _normalize_server_hostname(
                clean_server, allow_fallback=False, interface=interface
            )
            self.server = f"{safe_server}.local."
        else:
            safe_server = _normalize_server_hostname(
                self.name, allow_fallback=True, interface=interface
            )
            self.server = f"{safe_server}.local."

    def refresh_ip(self) -> bool:
        """
        Re-probe LAN IP when configured in automatic IP detection mode.
        Returns True if `self.ip` changed to a new valid IPv4 address.
        """
        if not self.auto_ip:
            return False
        new_ip = get_lan_ip(interface=self.interface)
        if (
            new_ip != self.ip
            and is_valid_ipv4(new_ip)
            and new_ip not in ("0.0.0.0", "255.255.255.255")
        ):
            # Never downgrade a valid LAN IP to loopback if Wi-Fi briefly blips
            if new_ip == "127.0.0.1" and self.ip != "127.0.0.1":
                return False
            self.ip = new_ip
            return True
        return False

    def sync_from_service_info(self, info: ServiceInfo) -> bool:
        """
        Synchronize `self.name` and default `self.server` if Zeroconf renamed
        `info.name` during `allow_name_change=True` conflict resolution.
        Returns True if `self.server` changed and needs A-record update.
        """
        suffix = f".{self.protocol}"
        if not info.name.endswith(suffix):
            return False
        actual_instance = info.name[: -len(suffix)]
        if not actual_instance or actual_instance == self.name:
            return False

        self.name = actual_instance
        if not self._custom_server:
            safe_host = _normalize_server_hostname(
                actual_instance, allow_fallback=True, interface=self.interface
            )
            new_server = f"{safe_host}.local."
            if new_server != self.server:
                self.server = new_server
                return True
        return False

    @property
    def host_name(self) -> str:
        """Returns the resolved .local hostname without trailing dot."""
        return self.server.rstrip(".")

    @property
    def url(self) -> str:
        """Returns friendly HTTP/HTTPS/WS URL representation."""
        scheme = infer_url_scheme(self.protocol)
        return f"{scheme}://{self.host_name}:{self.port}"

    @property
    def service_name(self) -> str:
        """Returns the full DNS-SD service instance name."""
        return f"{self.name}.{self.protocol}"

    def to_service_info(self) -> ServiceInfo:
        """Convert configuration to zeroconf.ServiceInfo."""
        return ServiceInfo(
            type_=self.protocol,
            name=self.service_name,
            addresses=[ip_to_bytes(self.ip)],
            port=self.port,
            properties=self.properties,
            server=self.server,
        )

    def __repr__(self) -> str:
        return f"<MDNSServiceConfig {self.url} -> {self.ip}:{self.port}>"
