# -*- coding:utf-8 -*-

"""
mDNS Resolver and LAN Service Discovery client.
Supports both stateless classmethod calls and stateful context-managed instance reuse.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import math
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
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


def _validate_timeout(timeout: float) -> float:
    """Validate that timeout is a finite non-negative number."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ValueError(
            f"timeout must be a non-negative finite number, got {timeout!r}"
        )
    return float(timeout)


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


_HOST_PORT_SUFFIX_REGEX = re.compile(r"^([^:/\s]+):\d+$")


def _extract_target_and_service_type(name: str, service_type: str) -> Tuple[str, str]:
    """
    Support URL or `host:port` inputs (e.g. `http://my-api.local:8000/` or `my-api.local:8000`)
    and automatically infer `_https._tcp.local.` / `_wss._tcp.local.` from URL scheme when default.
    """
    if not isinstance(name, str) or not name.strip():
        raise YYDSMDNSError("Host or service name cannot be empty.")

    raw = name.strip()
    inferred_type = service_type
    is_default_http = (service_type or "").strip().lower() in (
        "",
        "http",
        "_http._tcp",
        "_http._tcp.local",
        "_http._tcp.local.",
    )

    if "://" in raw:
        parsed = urlsplit(raw)
        if not parsed.hostname:
            raise YYDSMDNSError(f"Invalid URL or service name: '{name}'")
        raw = parsed.hostname
        if is_default_http:
            scheme = (parsed.scheme or "").lower()
            if scheme == "https":
                inferred_type = "_https._tcp.local."
            elif scheme == "wss":
                inferred_type = "_wss._tcp.local."
            elif scheme == "ws":
                inferred_type = "_ws._tcp.local."
    else:
        raw = raw.split("/", 1)[0].strip()
        m = _HOST_PORT_SUFFIX_REGEX.match(raw)
        if m:
            raw = m.group(1)

    stype = normalize_service_type(inferred_type)
    return raw, stype


def _build_full_service_name(name: str, stype: str) -> Tuple[str, str]:
    """
    Build (full_service_name, clean_host_prefix) from short name, .local host,
    URL, host:port, or an already-qualified service name (e.g. from discover() output).
    """
    if not isinstance(name, str) or not name.strip():
        raise YYDSMDNSError("Host or service name cannot be empty.")

    clean_name = normalize_host_name(name, service_type=stype)
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
        self._sync_lock = threading.Lock()
        self._async_loop_lock: Optional[
            Tuple[asyncio.AbstractEventLoop, asyncio.Lock]
        ] = None
        self._in_sync_cm = False
        self._in_async_cm = False

    def _get_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._async_loop_lock is None or self._async_loop_lock[0] is not loop:
            self._async_loop_lock = (loop, asyncio.Lock())
        return self._async_loop_lock[1]

    def close(self) -> None:
        """Close underlying synchronous Zeroconf instance if open."""
        with self._sync_lock:
            self._in_sync_cm = False
            if self._zc is not None:
                try:
                    self._zc.close()
                except Exception:
                    pass
                self._zc = None

    async def async_close(self) -> None:
        """Close underlying asynchronous AsyncZeroconf instance if open."""
        async with self._get_async_lock():
            self._in_async_cm = False
            if self._azc is not None:
                try:
                    await self._azc.async_close()
                except Exception:
                    pass
                self._azc = None

    def __enter__(self) -> "MDNSResolver":
        with self._sync_lock:
            self._in_sync_cm = True
            if self._zc is None:
                self._zc = _create_zeroconf()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "MDNSResolver":
        async with self._get_async_lock():
            self._in_async_cm = True
            if self._azc is None:
                self._azc = _create_async_zeroconf()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.async_close()

    def _acquire_sync_zc(self) -> Tuple[Zeroconf, bool]:
        """Return (zc, should_close_after_call)."""
        with self._sync_lock:
            if self._in_sync_cm and self._zc is not None:
                return self._zc, False
        return _create_zeroconf(), True

    async def _acquire_async_zc(self) -> Tuple[AsyncZeroconf, bool]:
        """Return (azc, should_close_after_call)."""
        async with self._get_async_lock():
            if self._in_async_cm and self._azc is not None:
                return self._azc, False
        return _create_async_zeroconf(), True

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
        wait_sec = _validate_timeout(timeout)
        followup_ms = min(1000, max(100, int(wait_sec * 1000)))

        if isinstance(target, MDNSResolver):
            zc, owns_zc = target._acquire_sync_zc()
        else:
            zc, owns_zc = _create_zeroconf(), True

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
        raw_target, stype = _extract_target_and_service_type(name, service_type)
        full_service_name, host_prefix = _build_full_service_name(raw_target, stype)
        total_sec = _validate_timeout(timeout)
        timeout_ms = max(100, int(total_sec * 1000))
        deadline = time.monotonic() + total_sec

        if isinstance(target, MDNSResolver):
            zc, owns_zc = target._acquire_sync_zc()
        else:
            zc, owns_zc = _create_zeroconf(), True

        try:
            if isinstance(zc, Zeroconf):
                cached = ServiceInfo(stype, full_service_name)
                if cached.load_from_cache(zc) and cached.parsed_addresses():
                    return _format_service_info(cached)

            if (
                fallback_host_lookup
                and isinstance(zc, Zeroconf)
                and getattr(zc, "loop", None) is not None
            ):

                async def _parallel_lookup() -> Optional[Dict[str, Any]]:
                    ainfo = AsyncServiceInfo(stype, full_service_name)
                    svc_ok, host_res = await asyncio.gather(
                        ainfo.async_request(zc, timeout=timeout_ms),
                        _resolve_host_ipv4_async(zc, host_prefix, timeout_ms),
                    )
                    if svc_ok:
                        return _format_service_info(ainfo)
                    return host_res

                fut = asyncio.run_coroutine_threadsafe(_parallel_lookup(), zc.loop)
                res = fut.result(timeout=(total_sec + 1.0))
                if res is not None:
                    return res
            else:
                info = zc.get_service_info(stype, full_service_name, timeout=timeout_ms)
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
        wait_sec = _validate_timeout(timeout)
        followup_ms = min(1000, max(100, int(wait_sec * 1000)))

        if isinstance(target, MDNSResolver):
            azc, owns_azc = await target._acquire_async_zc()
        else:
            azc, owns_azc = _create_async_zeroconf(), True

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
        raw_target, stype = _extract_target_and_service_type(name, service_type)
        full_service_name, host_prefix = _build_full_service_name(raw_target, stype)
        total_sec = _validate_timeout(timeout)
        timeout_ms = max(100, int(total_sec * 1000))

        if isinstance(target, MDNSResolver):
            azc, owns_azc = await target._acquire_async_zc()
        else:
            azc, owns_azc = _create_async_zeroconf(), True

        try:
            info = AsyncServiceInfo(stype, full_service_name)
            if isinstance(azc.zeroconf, Zeroconf):
                if info.load_from_cache(azc.zeroconf) and info.parsed_addresses():
                    return _format_service_info(info)

            if fallback_host_lookup and isinstance(azc.zeroconf, Zeroconf):
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
