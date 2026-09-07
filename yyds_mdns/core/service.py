# -*- coding:utf-8 -*-

"""
mDNS Service configuration and DNS-SD / A-record builder.
"""

import os
from typing import Any, Dict, Optional
from zeroconf import ServiceInfo

from yyds_mdns.core.exceptions import YYDSMDNSError
from yyds_mdns.core.net_utils import (
    get_lan_ip,
    get_mac_suffix,
    ip_to_bytes,
    is_valid_ipv4,
)


def resolve_port(port: Optional[int] = None, default: int = 8000) -> int:
    """
    Resolve port number from argument or standard environment variables (PORT, SERVER_PORT).
    """
    if port is not None:
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
    e.g. 'http' -> '_http._tcp.local.'
    """
    protocol = protocol.strip()
    if not protocol:
        return "_http._tcp.local."
    if not protocol.endswith("."):
        protocol += "."
    if not protocol.endswith(".local."):
        protocol = protocol.rstrip(".") + ".local."
    if not protocol.startswith("_"):
        protocol = f"_{protocol}"
    if "._tcp." not in protocol and "._udp." not in protocol:
        # Default to tcp transport
        parts = protocol.split(".", 1)
        protocol = f"{parts[0]}._tcp.{parts[1]}"
    return protocol


def normalize_host_name(name: str) -> str:
    """
    Normalize service or host name:
    'my-app.local' -> 'my-app'
    'my-app.local.' -> 'my-app'
    """
    name = name.strip()
    if name.endswith(".local."):
        name = name[:-7]
    elif name.endswith(".local"):
        name = name[:-6]
    name = name.rstrip(".")
    if not name:
        raise YYDSMDNSError("Host or service name cannot be empty.")
    return name


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
        self.raw_name = name
        self.interface = interface
        self.unique = unique or kwargs.get("unique_suffix", False)
        self.suffix_length = suffix_length
        self.device_suffix = ""

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
            resolved_name = resolved_name.replace(
                "{mac}", self.device_suffix
            ).replace("{id}", self.device_suffix)
        elif self.unique:
            base = normalize_host_name(resolved_name)
            resolved_name = f"{base}-{self.device_suffix}"

        self.name = normalize_host_name(resolved_name)
        self.port = resolve_port(port, default=8000)
        self.ip = ip if ip else get_lan_ip(interface=interface)
        if not is_valid_ipv4(self.ip):
            raise ValueError(f"Invalid IPv4 address: {self.ip}")

        self.protocol = normalize_service_type(protocol)
        self.properties = dict(properties or {})
        if self.device_suffix and "device_id" not in self.properties:
            self.properties["device_id"] = self.device_suffix

        # Set server hostname (must end with .local.)
        if server:
            resolved_server = server
            if "{mac}" in resolved_server or "{id}" in resolved_server:
                resolved_server = resolved_server.replace(
                    "{mac}", self.device_suffix
                ).replace("{id}", self.device_suffix)
            elif self.unique:
                base_srv = normalize_host_name(resolved_server)
                resolved_server = f"{base_srv}-{self.device_suffix}"
            clean_server = normalize_host_name(resolved_server)
            self.server = f"{clean_server}.local."
        else:
            self.server = f"{self.name}.local."

    @property
    def host_name(self) -> str:
        """Returns the resolved .local hostname without trailing dot."""
        return self.server.rstrip(".")

    @property
    def url(self) -> str:
        """Returns friendly HTTP/HTTPS URL representation."""
        scheme = "https" if "https" in self.protocol else "http"
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
