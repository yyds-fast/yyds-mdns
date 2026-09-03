# -*- coding:utf-8 -*-

"""
mDNS Resolver and LAN Service Discovery client.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from yyds_mdns.core.service import normalize_host_name, normalize_service_type


def _decode_properties(props: Dict[Any, Any]) -> Dict[str, Any]:
    """Safely decode binary TXT record properties to strings."""
    result = {}
    for k, v in props.items():
        key_str = k.decode("utf-8", "ignore") if isinstance(k, bytes) else str(k)
        if isinstance(v, bytes):
            val_str = v.decode("utf-8", "ignore")
        else:
            val_str = v
        result[key_str] = val_str
    return result


def _format_service_info(info: ServiceInfo) -> Dict[str, Any]:
    addresses = info.parsed_addresses()
    primary_ip = addresses[0] if addresses else "127.0.0.1"
    host = info.server.rstrip(".") if info.server else ""
    props = _decode_properties(info.properties)
    scheme = "https" if "https" in info.type else "http"
    url = f"{scheme}://{host}:{info.port}" if host and info.port else None

    return {
        "name": info.name,
        "type": info.type,
        "server": host,
        "port": info.port,
        "addresses": addresses,
        "ip": primary_ip,
        "url": url,
        "properties": props,
    }


class _DiscoveryListener:
    def __init__(self):
        self.found_services = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.found_services.append((type_, name))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass


class MDNSResolver:
    """Client for discovering and resolving mDNS services across the LAN."""

    @classmethod
    def discover(
        cls,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
    ) -> List[Dict[str, Any]]:
        """
        Discover all active services of the given service_type in the LAN.
        """
        stype = normalize_service_type(service_type)
        zc = Zeroconf()
        listener = _DiscoveryListener()
        browser = ServiceBrowser(zc, stype, listener)
        try:
            time.sleep(timeout)
            results = []
            for t, n in listener.found_services:
                info = zc.get_service_info(t, n, timeout=int(timeout * 1000))
                if info:
                    results.append(_format_service_info(info))
            return results
        finally:
            browser.cancel()
            zc.close()

    @classmethod
    def resolve(
        cls,
        name: str,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve a specific service or host name to its connection details.
        """
        clean_name = normalize_host_name(name)
        stype = normalize_service_type(service_type)
        full_service_name = f"{clean_name}.{stype}"

        zc = Zeroconf()
        try:
            info = zc.get_service_info(
                stype, full_service_name, timeout=int(timeout * 1000)
            )
            if info:
                return _format_service_info(info)
            return None
        finally:
            zc.close()

    @classmethod
    async def async_discover(
        cls,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
    ) -> List[Dict[str, Any]]:
        """Asynchronously discover services in LAN."""
        stype = normalize_service_type(service_type)
        azc = AsyncZeroconf()
        found_services = []

        class AsyncListener:
            def add_service(self, zc, type_, name):
                found_services.append((type_, name))

            def remove_service(self, zc, type_, name):
                pass

            def update_service(self, zc, type_, name):
                pass

        listener = AsyncListener()
        browser = AsyncServiceBrowser(azc.zeroconf, stype, listener)
        try:
            await asyncio.sleep(timeout)
            results = []
            for t, n in found_services:
                info = AsyncServiceInfo(t, n)
                if await info.async_request(azc.zeroconf, timeout=int(timeout * 1000)):
                    results.append(_format_service_info(info))
            return results
        finally:
            await browser.async_cancel()
            await azc.async_close()
