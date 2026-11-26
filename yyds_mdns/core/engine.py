# -*- coding:utf-8 -*-

"""
mDNS Engine: Handles synchronous and asynchronous Zeroconf lifecycle, concurrency,
failover, and dynamic IP updates.
"""

import asyncio
import atexit
import logging
import os
import sys
import threading
from typing import Any, Dict, Optional, Tuple
from zeroconf import (
    IPVersion,
    InterfaceChoice,
    NonUniqueNameException,
    ServiceInfo,
    Zeroconf,
)
from zeroconf.asyncio import AsyncZeroconf

from yyds_mdns.core.exceptions import (
    MDNSRegistrationError,
    MDNSServiceConflictError,
)
from yyds_mdns.core.lock import WorkerLock
from yyds_mdns.core.net_utils import get_all_local_ips, get_local_interface_ips
from yyds_mdns.core.service import MDNSServiceConfig

logger = logging.getLogger("yyds_mdns")


def _safe_print(text: str, fallback: Optional[str] = None) -> None:
    """Safely print text to terminal, gracefully handling limited encodings (e.g. Windows cp1252/cp437)."""
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        try:
            if fallback is not None:
                print(fallback, flush=True)
            else:
                encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                print(
                    text.encode(encoding, errors="replace").decode(encoding),
                    flush=True,
                )
        except Exception:
            pass
    except Exception:
        pass


def print_banner(url: str, ip: str) -> None:
    """Print visually pleasant service startup banner to terminal."""
    try:
        is_atty = False
        try:
            is_atty = sys.stdout.isatty()
        except Exception:
            pass

        if is_atty:
            text = f"\033[36m🌐 局域网 mDNS .local 访问域名: \033[1;32m{url}/\033[0m  \033[90m(IP: {ip})\033[0m"
        else:
            text = f"🌐 局域网 mDNS .local 访问域名: {url}/  (IP: {ip})"
        _safe_print(text, fallback=f"[mDNS] LAN Domain: {url}/  (IP: {ip})")
    except Exception:
        pass


def print_goodbye(url: str) -> None:
    """Print service deregistration notice to terminal."""
    try:
        is_atty = False
        try:
            is_atty = sys.stdout.isatty()
        except Exception:
            pass

        if is_atty:
            text = f"\033[33m👋 局域网 mDNS 服务已注销 (Goodbye广播已发送): \033[0m\033[90m{url}/\033[0m"
        else:
            text = f"👋 局域网 mDNS 服务已注销 (Goodbye广播已发送): {url}/"
        _safe_print(
            text, fallback=f"[mDNS] Service unregistered (Goodbye sent): {url}/"
        )
    except Exception:
        pass


def _resolve_zeroconf_kwargs(target_ip: Optional[str] = None) -> Dict[str, Any]:
    """
    Build optimal Zeroconf interface binding arguments.
    Scopes multicast sockets to the selected IPv4 interface instead of binding to all
    Docker veth/bridge/VPN/IPv6 adapters on the host. Gracefully falls back when
    advertising an external/NAT IP not directly assigned to a local interface.
    """
    local_ips = get_local_interface_ips(include_loopback=True)
    if target_ip and target_ip in local_ips:
        return {"interfaces": [target_ip], "ip_version": IPVersion.V4Only}

    filtered_lan_ips = [ip for ip in get_all_local_ips() if ip in local_ips]
    if (
        target_ip is None
        and "127.0.0.1" in local_ips
        and "127.0.0.1" not in filtered_lan_ips
    ):
        filtered_lan_ips.append("127.0.0.1")
    if filtered_lan_ips:
        return {"interfaces": filtered_lan_ips, "ip_version": IPVersion.V4Only}

    return {"interfaces": InterfaceChoice.Default, "ip_version": IPVersion.V4Only}


def _create_zeroconf(target_ip: Optional[str] = None) -> Zeroconf:
    """Instantiate Zeroconf scoped to target IPv4 interface with automatic fallback."""
    kwargs = _resolve_zeroconf_kwargs(target_ip)
    try:
        return Zeroconf(**kwargs)
    except OSError:
        return Zeroconf(ip_version=IPVersion.V4Only)


def _create_async_zeroconf(target_ip: Optional[str] = None) -> AsyncZeroconf:
    """Instantiate AsyncZeroconf scoped to target IPv4 interface with automatic fallback."""
    kwargs = _resolve_zeroconf_kwargs(target_ip)
    try:
        return AsyncZeroconf(**kwargs)
    except OSError:
        return AsyncZeroconf(ip_version=IPVersion.V4Only)


class MDNSEngine:
    """Synchronous thread-safe mDNS registration engine."""

    def __init__(
        self,
        config: MDNSServiceConfig,
        use_worker_lock: bool = True,
        auto_atexit: bool = True,
        verbose: bool = True,
        allow_name_change: bool = False,
        strict: bool = True,
        watch_interval: float = 0.0,
    ):
        self.config = config
        self.use_worker_lock = use_worker_lock
        self.auto_atexit = auto_atexit
        self.verbose = verbose
        self.allow_name_change = allow_name_change
        self.strict = strict
        self.watch_interval = max(0.0, float(watch_interval or 0.0))

        self._op_lock = threading.RLock()
        self._zeroconf: Optional[Zeroconf] = None
        self._service_info: Optional[ServiceInfo] = None
        self._worker_lock: Optional[WorkerLock] = (
            WorkerLock(config.service_name) if use_worker_lock else None
        )
        self._is_registered = False
        self._lock_held = False
        self._owner_pid: Optional[int] = None
        self._atexit_registered = False
        self._watch_stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None

    @property
    def is_registered(self) -> bool:
        """True if service is actively broadcasted by this process."""
        return self._is_registered and self._owner_pid == os.getpid()

    def _register_atexit(self) -> None:
        if self.auto_atexit and not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

    def _unregister_atexit(self) -> None:
        if self._atexit_registered:
            try:
                atexit.unregister(self.stop)
            except Exception:
                pass
            self._atexit_registered = False

    def _ensure_watcher(self) -> None:
        if self.watch_interval <= 0:
            return
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="yyds-mdns-watcher",
            daemon=True,
        )
        self._watch_thread.start()

    def _stop_watcher(self) -> None:
        self._watch_stop.set()
        t = self._watch_thread
        self._watch_thread = None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(timeout=self.watch_interval):
            try:
                if self.is_registered:
                    self.refresh()
                elif self._worker_lock is not None:
                    # Standby worker failover: attempt takeover if primary worker exited
                    self._start_once(from_watcher=True)
            except Exception as err:
                logger.debug("mDNS watcher tick error: %s", err)

    def refresh(self) -> bool:
        """
        Re-probe LAN IP (in auto-IP mode) and dynamically update mDNS A-record if IP changed.
        Returns True if record was updated.
        """
        with self._op_lock:
            if not self.is_registered or self._zeroconf is None:
                return False
            if not self.config.refresh_ip():
                return False

            old_info = self._service_info
            old_zc = self._zeroconf
            try:
                new_zc = _create_zeroconf(self.config.ip)
                new_info = self.config.to_service_info()
                new_zc.register_service(
                    new_info,
                    allow_name_change=self.allow_name_change,
                )
                self._zeroconf = new_zc
                self._service_info = new_info
                try:
                    if old_info is not None:
                        old_zc.unregister_service(old_info)
                    old_zc.close()
                except Exception:
                    pass
                logger.info(
                    "mDNS service IP updated dynamically: %s (%s)",
                    self.config.url,
                    self.config.ip,
                )
                return True
            except Exception as err:
                logger.warning(
                    "Failed to refresh mDNS IP to %s: %s", self.config.ip, err
                )
                return False

    def _start_once(self, from_watcher: bool = False) -> bool:
        with self._op_lock:
            if self.is_registered:
                return True

            if self._worker_lock:
                if not self._worker_lock.acquire():
                    logger.debug(
                        "Worker lock already held. Skipping mDNS registration on this worker: %s",
                        self.config.service_name,
                    )
                    if not from_watcher:
                        self._register_atexit()
                        self._ensure_watcher()
                    return False
                self._lock_held = True

            self._owner_pid = os.getpid()
            self._register_atexit()

            try:
                self.config.refresh_ip()
                self._service_info = self.config.to_service_info()
                self._zeroconf = _create_zeroconf(self.config.ip)
                self._zeroconf.register_service(
                    self._service_info,
                    allow_name_change=self.allow_name_change,
                )
                if self.allow_name_change and self.config.sync_from_service_info(
                    self._service_info
                ):
                    self._service_info.server = self.config.server
                    self._zeroconf.update_service(self._service_info)

                self._is_registered = True
                logger.info(
                    "mDNS service registered: %s (%s)", self.config.url, self.config.ip
                )
                if self.verbose:
                    print_banner(self.config.url, self.config.ip)
                self._ensure_watcher()
                return True
            except NonUniqueNameException as err:
                self._cleanup_resources()
                if not self.strict or from_watcher:
                    logger.warning(
                        "mDNS service conflict for '%s'; continuing without mDNS: %s",
                        self.config.service_name,
                        err,
                    )
                    return False
                raise MDNSServiceConflictError(
                    f"Service name '{self.config.service_name}' already exists in LAN."
                ) from err
            except Exception as err:
                self._cleanup_resources()
                if not self.strict or from_watcher:
                    logger.warning(
                        "Failed to register mDNS service '%s'; continuing without mDNS: %s",
                        self.config.service_name,
                        err,
                    )
                    return False
                raise MDNSRegistrationError(
                    f"Failed to register mDNS service: {err}"
                ) from err

    def start(self) -> bool:
        """
        Start broadcasting mDNS service.
        Returns True if registration succeeded, False if skipped (e.g. secondary worker or non-strict failure).
        """
        return self._start_once(from_watcher=False)

    def _cleanup_resources(self) -> None:
        self._unregister_atexit()
        is_owner = self._owner_pid is None or self._owner_pid == os.getpid()

        if self._zeroconf is not None:
            if is_owner:
                if self._service_info is not None and self._is_registered:
                    try:
                        logger.debug(
                            "Unregistering mDNS service: %s", self.config.service_name
                        )
                        self._zeroconf.unregister_service(self._service_info)
                        if self.verbose:
                            print_goodbye(self.config.url)
                    except Exception as err:
                        logger.warning("Error unregistering mDNS service: %s", err)
                try:
                    self._zeroconf.close()
                except Exception:
                    pass
            self._zeroconf = None

        self._service_info = None
        self._is_registered = False
        self._owner_pid = None

        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    def stop(self) -> None:
        """Gracefully unregister mDNS service (Goodbye packet) and cleanup."""
        self._stop_watcher()
        with self._op_lock:
            self._cleanup_resources()

    def __enter__(self) -> "MDNSEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class AsyncMDNSEngine:
    """Asynchronous coroutine-safe mDNS registration engine for asyncio event loops."""

    def __init__(
        self,
        config: MDNSServiceConfig,
        use_worker_lock: bool = True,
        auto_atexit: bool = True,
        verbose: bool = True,
        allow_name_change: bool = False,
        strict: bool = True,
        watch_interval: float = 0.0,
    ):
        self.config = config
        self.use_worker_lock = use_worker_lock
        self.auto_atexit = auto_atexit
        self.verbose = verbose
        self.allow_name_change = allow_name_change
        self.strict = strict
        self.watch_interval = max(0.0, float(watch_interval or 0.0))

        self._loop_lock: Optional[Tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = None
        self._async_zeroconf: Optional[AsyncZeroconf] = None
        self._service_info: Optional[ServiceInfo] = None
        self._worker_lock: Optional[WorkerLock] = (
            WorkerLock(config.service_name) if use_worker_lock else None
        )
        self._is_registered = False
        self._lock_held = False
        self._owner_pid: Optional[int] = None
        self._atexit_registered = False
        self._watch_task: Optional[asyncio.Task] = None

    def _get_op_lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock bound to the currently running event loop (Python 3.8+ safe)."""
        loop = asyncio.get_running_loop()
        if self._loop_lock is None or self._loop_lock[0] is not loop:
            self._loop_lock = (loop, asyncio.Lock())
        return self._loop_lock[1]

    @property
    def is_registered(self) -> bool:
        """True if service is actively broadcasted by this process."""
        return self._is_registered and self._owner_pid == os.getpid()

    def _register_atexit(self) -> None:
        if self.auto_atexit and not self._atexit_registered:
            atexit.register(self._sync_cleanup)
            self._atexit_registered = True

    def _unregister_atexit(self) -> None:
        if self._atexit_registered:
            try:
                atexit.unregister(self._sync_cleanup)
            except Exception:
                pass
            self._atexit_registered = False

    def _ensure_watcher(self) -> None:
        if self.watch_interval <= 0:
            return
        if self._watch_task is not None and not self._watch_task.done():
            return
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def _stop_watcher(self) -> None:
        task = self._watch_task
        self._watch_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _watch_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.watch_interval)
                try:
                    if self.is_registered:
                        await self.refresh()
                    elif self._worker_lock is not None:
                        await self._start_once(from_watcher=True)
                except Exception as err:
                    logger.debug("Async mDNS watcher tick error: %s", err)
        except asyncio.CancelledError:
            return

    def _sync_cleanup(self) -> None:
        """Emergency synchronous cleanup on interpreter shutdown."""
        self._unregister_atexit()
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None

        is_owner = self._owner_pid is None or self._owner_pid == os.getpid()
        if is_owner and self._async_zeroconf is not None:
            try:
                zc = getattr(self._async_zeroconf, "zeroconf", None)
                if zc is not None:
                    zc.close()
            except Exception:
                pass
        self._async_zeroconf = None
        self._service_info = None
        self._is_registered = False
        self._owner_pid = None

        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    async def refresh(self) -> bool:
        """
        Re-probe LAN IP (in auto-IP mode) and dynamically update mDNS A-record if IP changed.
        Returns True if record was updated.
        """
        async with self._get_op_lock():
            if not self.is_registered or self._async_zeroconf is None:
                return False
            if not self.config.refresh_ip():
                return False

            old_info = self._service_info
            old_azc = self._async_zeroconf
            try:
                new_azc = _create_async_zeroconf(self.config.ip)
                new_info = self.config.to_service_info()
                await new_azc.async_register_service(
                    new_info,
                    allow_name_change=self.allow_name_change,
                )
                self._async_zeroconf = new_azc
                self._service_info = new_info
                try:
                    if old_info is not None:
                        await old_azc.async_unregister_service(old_info)
                    await old_azc.async_close()
                except Exception:
                    pass
                logger.info(
                    "Async mDNS service IP updated dynamically: %s (%s)",
                    self.config.url,
                    self.config.ip,
                )
                return True
            except Exception as err:
                logger.warning(
                    "Failed to refresh async mDNS IP to %s: %s", self.config.ip, err
                )
                return False

    async def _start_once(self, from_watcher: bool = False) -> bool:
        async with self._get_op_lock():
            if self.is_registered:
                return True

            if self._worker_lock:
                if not self._worker_lock.acquire():
                    logger.debug(
                        "Worker lock already held. Skipping async mDNS registration on this worker: %s",
                        self.config.service_name,
                    )
                    if not from_watcher:
                        self._register_atexit()
                        self._ensure_watcher()
                    return False
                self._lock_held = True

            self._owner_pid = os.getpid()
            self._register_atexit()

            try:
                self.config.refresh_ip()
                self._service_info = self.config.to_service_info()
                self._async_zeroconf = _create_async_zeroconf(self.config.ip)
                await self._async_zeroconf.async_register_service(
                    self._service_info,
                    allow_name_change=self.allow_name_change,
                )
                if self.allow_name_change and self.config.sync_from_service_info(
                    self._service_info
                ):
                    self._service_info.server = self.config.server
                    async_update = getattr(
                        self._async_zeroconf, "async_update_service", None
                    )
                    if callable(async_update):
                        await async_update(self._service_info)

                self._is_registered = True
                logger.info(
                    "Async mDNS service registered: %s (%s)",
                    self.config.url,
                    self.config.ip,
                )
                if self.verbose:
                    print_banner(self.config.url, self.config.ip)
                self._ensure_watcher()
                return True
            except NonUniqueNameException as err:
                await self._cleanup_resources()
                if not self.strict or from_watcher:
                    logger.warning(
                        "Async mDNS service conflict for '%s'; continuing without mDNS: %s",
                        self.config.service_name,
                        err,
                    )
                    return False
                raise MDNSServiceConflictError(
                    f"Service name '{self.config.service_name}' already exists in LAN."
                ) from err
            except Exception as err:
                await self._cleanup_resources()
                if not self.strict or from_watcher:
                    logger.warning(
                        "Failed to register async mDNS service '%s'; continuing without mDNS: %s",
                        self.config.service_name,
                        err,
                    )
                    return False
                raise MDNSRegistrationError(
                    f"Failed to register async mDNS service: {err}"
                ) from err

    async def start(self) -> bool:
        """
        Asynchronously register mDNS service.
        Returns True if registered, False if secondary worker or non-strict failure.
        """
        return await self._start_once(from_watcher=False)

    async def _cleanup_resources(self) -> None:
        self._unregister_atexit()
        is_owner = self._owner_pid is None or self._owner_pid == os.getpid()

        if self._async_zeroconf is not None:
            if is_owner:
                if self._service_info is not None and self._is_registered:
                    try:
                        logger.debug(
                            "Async unregistering mDNS service: %s",
                            self.config.service_name,
                        )
                        await self._async_zeroconf.async_unregister_service(
                            self._service_info
                        )
                        if self.verbose:
                            print_goodbye(self.config.url)
                    except Exception as err:
                        logger.warning(
                            "Error unregistering async mDNS service: %s", err
                        )
                try:
                    await self._async_zeroconf.async_close()
                except Exception:
                    pass
            self._async_zeroconf = None

        self._service_info = None
        self._is_registered = False
        self._owner_pid = None

        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    async def stop(self) -> None:
        """Gracefully unregister mDNS service asynchronously and close."""
        await self._stop_watcher()
        async with self._get_op_lock():
            await self._cleanup_resources()

    async def __aenter__(self) -> "AsyncMDNSEngine":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
