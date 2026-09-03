# -*- coding:utf-8 -*-

"""
mDNS Engine: Handles synchronous and asynchronous Zeroconf lifecycle and registration.
"""

import atexit
import logging
import sys
from typing import Optional
from zeroconf import NonUniqueNameException, ServiceInfo, Zeroconf
from zeroconf.asyncio import AsyncZeroconf

from yyds_mdns.core.exceptions import (
    MDNSRegistrationError,
    MDNSServiceConflictError,
)
from yyds_mdns.core.lock import WorkerLock
from yyds_mdns.core.service import MDNSServiceConfig

logger = logging.getLogger("yyds_mdns")


def print_banner(url: str, ip: str) -> None:
    """Print visually pleasant service startup banner to terminal."""
    if sys.stdout.isatty():
        print(
            f"\033[36m🌐 局域网 mDNS .local 访问域名: \033[1;32m{url}/\033[0m  \033[90m(IP: {ip})\033[0m",
            flush=True,
        )
    else:
        print(f"🌐 局域网 mDNS .local 访问域名: {url}/  (IP: {ip})", flush=True)


def print_goodbye(url: str) -> None:
    """Print service deregistration notice to terminal."""
    if sys.stdout.isatty():
        print(
            f"\033[33m👋 局域网 mDNS 服务已注销 (Goodbye广播已发送): \033[0m\033[90m{url}/\033[0m",
            flush=True,
        )
    else:
        print(f"👋 局域网 mDNS 服务已注销 (Goodbye广播已发送): {url}/", flush=True)


class MDNSEngine:
    """Synchronous mDNS registration engine."""

    def __init__(
        self,
        config: MDNSServiceConfig,
        use_worker_lock: bool = True,
        auto_atexit: bool = True,
        verbose: bool = True,
    ):
        self.config = config
        self.use_worker_lock = use_worker_lock
        self.auto_atexit = auto_atexit
        self.verbose = verbose

        self._zeroconf: Optional[Zeroconf] = None
        self._service_info: Optional[ServiceInfo] = None
        self._worker_lock: Optional[WorkerLock] = (
            WorkerLock(config.service_name) if use_worker_lock else None
        )
        self._is_registered = False
        self._lock_held = False

        if self.auto_atexit:
            atexit.register(self.stop)

    @property
    def is_registered(self) -> bool:
        """True if service is actively broadcasted."""
        return self._is_registered

    def start(self) -> bool:
        """
        Start broadcasting mDNS service.
        Returns True if registration succeeded, False if skipped (e.g. secondary worker).
        """
        if self._is_registered:
            return True

        if self._worker_lock:
            if not self._worker_lock.acquire():
                logger.debug(
                    "Worker lock already held. Skipping mDNS registration on this worker: %s",
                    self.config.service_name,
                )
                return False
            self._lock_held = True

        try:
            self._service_info = self.config.to_service_info()
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(self._service_info)
            self._is_registered = True
            logger.info(
                "mDNS service registered: %s (%s)", self.config.url, self.config.ip
            )
            if self.verbose:
                print_banner(self.config.url, self.config.ip)
            return True
        except NonUniqueNameException as err:
            self.stop()
            raise MDNSServiceConflictError(
                f"Service name '{self.config.service_name}' already exists in LAN."
            ) from err
        except Exception as err:
            self.stop()
            raise MDNSRegistrationError(
                f"Failed to register mDNS service: {err}"
            ) from err

    def stop(self) -> None:
        """Gracefully unregister mDNS service (Goodbye packet) and cleanup."""
        if self._zeroconf is not None:
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

        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class AsyncMDNSEngine:
    """Asynchronous mDNS registration engine for asyncio event loops."""

    def __init__(
        self,
        config: MDNSServiceConfig,
        use_worker_lock: bool = True,
        auto_atexit: bool = True,
        verbose: bool = True,
    ):
        self.config = config
        self.use_worker_lock = use_worker_lock
        self.auto_atexit = auto_atexit
        self.verbose = verbose

        self._async_zeroconf: Optional[AsyncZeroconf] = None
        self._service_info: Optional[ServiceInfo] = None
        self._worker_lock: Optional[WorkerLock] = (
            WorkerLock(config.service_name) if use_worker_lock else None
        )
        self._is_registered = False
        self._lock_held = False

        if self.auto_atexit:
            atexit.register(self._sync_cleanup)

    @property
    def is_registered(self) -> bool:
        """True if service is actively broadcasted."""
        return self._is_registered

    def _sync_cleanup(self) -> None:
        """Emergency synchronous cleanup on interpreter shutdown."""
        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    async def start(self) -> bool:
        """
        Asynchronously register mDNS service.
        Returns True if registered, False if secondary worker.
        """
        if self._is_registered:
            return True

        if self._worker_lock:
            if not self._worker_lock.acquire():
                logger.debug(
                    "Worker lock already held. Skipping async mDNS registration on this worker: %s",
                    self.config.service_name,
                )
                return False
            self._lock_held = True

        try:
            self._service_info = self.config.to_service_info()
            self._async_zeroconf = AsyncZeroconf()
            await self._async_zeroconf.async_register_service(self._service_info)
            self._is_registered = True
            logger.info(
                "Async mDNS service registered: %s (%s)",
                self.config.url,
                self.config.ip,
            )
            if self.verbose:
                print_banner(self.config.url, self.config.ip)
            return True
        except NonUniqueNameException as err:
            await self.stop()
            raise MDNSServiceConflictError(
                f"Service name '{self.config.service_name}' already exists in LAN."
            ) from err
        except Exception as err:
            await self.stop()
            raise MDNSRegistrationError(
                f"Failed to register async mDNS service: {err}"
            ) from err

    async def stop(self) -> None:
        """Gracefully unregister mDNS service asynchronously and close."""
        if self._async_zeroconf is not None:
            if self._service_info is not None and self._is_registered:
                try:
                    logger.debug(
                        "Async unregistering mDNS service: %s", self.config.service_name
                    )
                    await self._async_zeroconf.async_unregister_service(
                        self._service_info
                    )
                    if self.verbose:
                        print_goodbye(self.config.url)
                except Exception as err:
                    logger.warning("Error unregistering async mDNS service: %s", err)
            try:
                await self._async_zeroconf.async_close()
            except Exception:
                pass
            self._async_zeroconf = None

        self._service_info = None
        self._is_registered = False

        if self._worker_lock and self._lock_held:
            self._worker_lock.release()
            self._lock_held = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
