# -*- coding:utf-8 -*-

"""
mDNS Resolver and LAN Service Discovery client.
Supports both stateless classmethod calls and stateful context-managed instance reuse.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from zeroconf import (
    AddressResolverIPv4,
    IPVersion,
    ServiceBrowser,
    ServiceInfo,
    Zeroconf,
)
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from yyds_mdns.core.engine import _create_async_zeroconf, _create_zeroconf
from yyds_mdns.core.exceptions import MDNSResolutionTimeoutError, YYDSMDNSError
from yyds_mdns.core.net_utils import is_valid_ipv4
from yyds_mdns.core.service import (
    infer_url_scheme,
    normalize_host_name,
    normalize_service_type,
)


class _DualMethod:
    """Descriptor allowing methods to be called either on MDNSResolver class or on an instance."""

    def __init__(self, func: Callable[..., Any]):
        self._func = func

    def __get__(self, instance: Any, owner: Any = None) -> Callable[..., Any]:
        target = owner if instance is None else instance
        return lambda *args, **kwargs: self._func(target, *args, **kwargs)


def _decode_properties(props: Dict[Any, Any]) -> Dict[str, Any]:
    """Safely decode binary TXT record properties to strings."""
    result: Dict[str, Any] = {}
    for k, v in (props or {}).items():
        key_str = k.decode("utf-8", "ignore") if isinstance(k, bytes) else str(k)
        if isinstance(v, bytes):
            val_str: Any = v.decode("utf-8", "ignore")
        else:
            val_str = v
        result[key_str] = val_str
    return result


def _format_service_info(info: ServiceInfo) -> Dict[str, Any]:
    """Format zeroconf ServiceInfo into a clean dictionary, preferring IPv4 addresses."""
    try:
        v4_addrs = info.parsed_addresses(IPVersion.V4Only)
    except TypeError:
        v4_addrs = []
    all_addrs = info.parsed_addresses()
    addresses = v4_addrs + [a for a in all_addrs if a not in v4_addrs]

    primary_ip: Optional[str] = addresses[0] if addresses else None
    host = info.server.rstrip(".") if info.server else ""
    props = _decode_properties(info.properties)
    scheme = infer_url_scheme(info.type)

    url: Optional[str] = None
    if host and info.port:
        url = f"{scheme}://{host}:{info.port}"
    elif primary_ip and info.port:
        ip_host = f"[{primary_ip}]" if ":" in primary_ip else primary_ip
        url = f"{scheme}://{ip_host}:{info.port}"

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


def _format_host_record(host_fqdn: str, addresses: List[str]) -> Dict[str, Any]:
    """Format a pure mDNS A-record host resolution result."""
    clean_host = host_fqdn.rstrip(".")
    return {
        "name": clean_host,
        "type": None,
        "server": clean_host,
        "port": None,
        "addresses": addresses,
        "ip": addresses[0] if addresses else None,
        "url": None,
        "properties": {},
    }


def _build_full_service_name(name: str, stype: str) -> Tuple[str, str]:
    """
    Build (full_service_name, clean_host_prefix) from short name, .local host,
    or an already-qualified service name (e.g. from discover() output).
    """
    if not isinstance(name, str) or not name.strip():
        raise YYDSMDNSError("Host or service name cannot be empty.")

    raw = name.strip()
    raw_dotted = raw if raw.endswith(".") else f"{raw}."
    suffix = f".{stype.lower()}"
    if raw_dotted.lower().endswith(suffix):
        prefix = raw_dotted[: -len(suffix)]
        if not prefix:
            raise YYDSMDNSError("Host or service name cannot be empty.")
        return f"{prefix}.{stype}", prefix

    clean_name = normalize_host_name(raw)
    return f"{clean_name}.{stype}", clean_name


def _resolve_host_ipv4_sync(
    zc: Zeroconf, host_prefix: str, timeout_ms: int
) -> Optional[Dict[str, Any]]:
    """Fallback mDNS A-record resolution for pure .local hosts (e.g. raspberrypi.local)."""
    if not isinstance(zc, Zeroconf):
        return None
    host_fqdn = f"{host_prefix}.local."
    try:
        resolver = AddressResolverIPv4(host_fqdn)
        if resolver.load_from_cache(zc) or resolver.request(zc, timeout=timeout_ms):
            addrs = [
                ip
                for ip in resolver.parsed_addresses(IPVersion.V4Only)
                if is_valid_ipv4(ip)
            ]
            if addrs:
                return _format_host_record(host_fqdn, addrs)
    except Exception:
        pass
    return None


async def _resolve_host_ipv4_async(
    zc: Zeroconf, host_prefix: str, timeout_ms: int
) -> Optional[Dict[str, Any]]:
    """Async fallback mDNS A-record resolution for pure .local hosts."""
    if not isinstance(zc, Zeroconf):
        return None
    host_fqdn = f"{host_prefix}.local."
    try:
        resolver = AddressResolverIPv4(host_fqdn)
        if resolver.load_from_cache(zc) or await resolver.async_request(
            zc, timeout=timeout_ms
        ):
            addrs = [
                ip
                for ip in resolver.parsed_addresses(IPVersion.V4Only)
                if is_valid_ipv4(ip)
            ]
            if addrs:
                return _format_host_record(host_fqdn, addrs)
    except Exception:
        pass
    return None


class _DiscoveryListener:
    """Thread-safe listener tracking active mDNS services (deduplicated & Goodbye-aware)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Dict[Tuple[str, str], None] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self._active[(type_, name)] = None

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self._active.pop((type_, name), None)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self._active[(type_, name)] = None

    def snapshot(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._active.keys())


class MDNSResolver:
    """
    Client for discovering and resolving mDNS services and .local hosts across the LAN.

    Can be used statelessly via class methods:
        MDNSResolver.resolve("my-api")
        await MDNSResolver.async_discover()

    Or as a reusable context manager keeping a warm mDNS cache across lookups:
        with MDNSResolver() as resolver:
            resolver.resolve("my-api")
    """

    def __init__(self) -> None:
        self._zc: Optional[Zeroconf] = None
        self._azc: Optional[AsyncZeroconf] = None

    def close(self) -> None:
        """Close underlying synchronous Zeroconf instance if open."""
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None

    async def async_close(self) -> None:
        """Close underlying asynchronous AsyncZeroconf instance if open."""
        if self._azc is not None:
            try:
                await self._azc.async_close()
            except Exception:
                pass
            self._azc = None

    def __enter__(self) -> "MDNSResolver":
        if self._zc is None:
            self._zc = _create_zeroconf()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "MDNSResolver":
        if self._azc is None:
            self._azc = _create_async_zeroconf()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.async_close()

    @_DualMethod
    def discover(
        target: Any,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
    ) -> List[Dict[str, Any]]:
        """
        Discover all active services of the given service_type in the LAN.
        Resolves discovered service records concurrently within a bounded timeout budget.
        """
        stype = normalize_service_type(service_type)
        wait_sec = max(0.0, float(timeout))
        followup_ms = min(1000, max(100, int(wait_sec * 1000)))

        is_instance = isinstance(target, MDNSResolver)
        owns_zc = not is_instance or target._zc is None
        zc: Zeroconf = _create_zeroconf() if owns_zc else target._zc  # type: ignore[assignment]
        if is_instance and target._zc is None:
            target._zc = zc
            owns_zc = False

        listener = _DiscoveryListener()
        browser = ServiceBrowser(zc, stype, listener)
        try:
            time.sleep(wait_sec)
            targets = listener.snapshot()
            if not targets:
                return []

            def _fetch_one(item: Tuple[str, str]) -> Optional[Dict[str, Any]]:
                t, n = item
                cached = ServiceInfo(t, n)
                try:
                    if cached.load_from_cache(zc) and cached.parsed_addresses():
                        return _format_service_info(cached)
                except Exception:
                    pass
                info = zc.get_service_info(t, n, timeout=followup_ms)
                return _format_service_info(info) if info else None

            if len(targets) == 1:
                single = _fetch_one(targets[0])
                return [single] if single else []

            max_workers = min(8, len(targets))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                resolved = list(pool.map(_fetch_one, targets))
            return [r for r in resolved if r is not None]
        finally:
            browser.cancel()
            if owns_zc:
                zc.close()

    @_DualMethod
    def resolve(
        target: Any,
        name: str,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
        raise_on_timeout: bool = False,
        fallback_host_lookup: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve a specific DNS-SD service or pure .local host name to connection details.
        """
        stype = normalize_service_type(service_type)
        full_service_name, host_prefix = _build_full_service_name(name, stype)
        total_sec = max(0.0, float(timeout))
        timeout_ms = max(100, int(total_sec * 1000))
        deadline = time.monotonic() + total_sec

        is_instance = isinstance(target, MDNSResolver)
        owns_zc = not is_instance or target._zc is None
        zc: Zeroconf = _create_zeroconf() if owns_zc else target._zc  # type: ignore[assignment]
        if is_instance and target._zc is None:
            target._zc = zc
            owns_zc = False

        try:
            # Fast path: check warm cache if reusing MDNSResolver instance
            if isinstance(zc, Zeroconf):
                cached = ServiceInfo(stype, full_service_name)
                if cached.load_from_cache(zc) and cached.parsed_addresses():
                    return _format_service_info(cached)

            svc_timeout_ms = (
                max(100, int(timeout_ms * 0.7))
                if (fallback_host_lookup and isinstance(zc, Zeroconf))
                else timeout_ms
            )
            info = zc.get_service_info(stype, full_service_name, timeout=svc_timeout_ms)
            if info:
                return _format_service_info(info)

            if fallback_host_lookup and isinstance(zc, Zeroconf):
                rem_ms = max(100, int((deadline - time.monotonic()) * 1000))
                host_res = _resolve_host_ipv4_sync(zc, host_prefix, rem_ms)
                if host_res:
                    return host_res

            if raise_on_timeout:
                raise MDNSResolutionTimeoutError(
                    f"Timed out resolving '{full_service_name}' after {timeout}s."
                )
            return None
        finally:
            if owns_zc:
                zc.close()

    @_DualMethod
    async def async_discover(
        target: Any,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
    ) -> List[Dict[str, Any]]:
        """Asynchronously discover services in LAN with parallel record resolution."""
        stype = normalize_service_type(service_type)
        wait_sec = max(0.0, float(timeout))
        followup_ms = min(1000, max(100, int(wait_sec * 1000)))

        is_instance = isinstance(target, MDNSResolver)
        owns_azc = not is_instance or target._azc is None
        azc: AsyncZeroconf = (
            _create_async_zeroconf() if owns_azc else target._azc  # type: ignore[assignment]
        )
        if is_instance and target._azc is None:
            target._azc = azc
            owns_azc = False

        listener = _DiscoveryListener()
        browser = AsyncServiceBrowser(azc.zeroconf, stype, listener)
        try:
            await asyncio.sleep(wait_sec)
            targets = listener.snapshot()
            if not targets:
                return []

            async def _async_fetch_one(
                item: Tuple[str, str],
            ) -> Optional[Dict[str, Any]]:
                t, n = item
                info = AsyncServiceInfo(t, n)
                try:
                    if info.load_from_cache(azc.zeroconf) and info.parsed_addresses():
                        return _format_service_info(info)
                except Exception:
                    pass
                if await info.async_request(azc.zeroconf, timeout=followup_ms):
                    return _format_service_info(info)
                return None

            resolved = await asyncio.gather(*(_async_fetch_one(t) for t in targets))
            return [r for r in resolved if r is not None]
        finally:
            await browser.async_cancel()
            if owns_azc:
                await azc.async_close()

    @_DualMethod
    async def async_resolve(
        target: Any,
        name: str,
        service_type: str = "_http._tcp.local.",
        timeout: float = 2.5,
        raise_on_timeout: bool = False,
        fallback_host_lookup: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Asynchronously resolve a DNS-SD service or pure .local host name without blocking."""
        stype = normalize_service_type(service_type)
        full_service_name, host_prefix = _build_full_service_name(name, stype)
        timeout_ms = max(100, int(max(0.0, float(timeout)) * 1000))

        is_instance = isinstance(target, MDNSResolver)
        owns_azc = not is_instance or target._azc is None
        azc: AsyncZeroconf = (
            _create_async_zeroconf() if owns_azc else target._azc  # type: ignore[assignment]
        )
        if is_instance and target._azc is None:
            target._azc = azc
            owns_azc = False

        try:
            info = AsyncServiceInfo(stype, full_service_name)
            if isinstance(azc.zeroconf, Zeroconf):
                if info.load_from_cache(azc.zeroconf) and info.parsed_addresses():
                    return _format_service_info(info)

            if fallback_host_lookup and isinstance(azc.zeroconf, Zeroconf):
                # Query DNS-SD service and pure .local A-record concurrently within single timeout!
                svc_ok, host_res = await asyncio.gather(
                    info.async_request(azc.zeroconf, timeout=timeout_ms),
                    _resolve_host_ipv4_async(azc.zeroconf, host_prefix, timeout_ms),
                )
                if svc_ok:
                    return _format_service_info(info)
                if host_res:
                    return host_res
            else:
                if await info.async_request(azc.zeroconf, timeout=timeout_ms):
                    return _format_service_info(info)

            if raise_on_timeout:
                raise MDNSResolutionTimeoutError(
                    f"Timed out resolving '{full_service_name}' after {timeout}s."
                )
            return None
        finally:
            if owns_azc:
                await azc.async_close()
