# -*- coding:utf-8 -*-

"""
Universal standalone controller, context manager (sync & async), and decorator for mDNS.
"""

import asyncio
import functools
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from yyds_mdns.core.engine import AsyncMDNSEngine, MDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


class MDNSServer:
    """
    Universal thread-safe & coroutine-safe mDNS controller.
    Supports:
    1. Synchronous context manager: with MDNSServer(...):
    2. Asynchronous context manager: async with MDNSServer(...):
    3. Function/coroutine decorator: @MDNSServer(...)
    4. Manual lifecycle: server = MDNSServer(...); server.start(); server.stop()
    """

    def __init__(
        self,
        name: str = "service",
        port: int = 8000,
        ip: Optional[str] = None,
        interface: Optional[str] = None,
        protocol: str = "_http._tcp.local.",
        properties: Optional[Dict[str, Any]] = None,
        server: Optional[str] = None,
        use_worker_lock: bool = True,
        verbose: bool = True,
        unique: bool = False,
        suffix_length: int = 4,
        allow_name_change: bool = False,
        strict: bool = True,
        watch_interval: float = 0.0,
        **kwargs: Any,
    ):
        self.verbose = verbose
        self.allow_name_change = allow_name_change
        self.strict = strict
        self.watch_interval = watch_interval
        self.config = MDNSServiceConfig(
            name=name,
            port=port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            unique=unique,
            suffix_length=suffix_length,
            **kwargs,
        )
        self.use_worker_lock = use_worker_lock
        self._sync_engine: Optional[MDNSEngine] = None
        self._async_engine: Optional[AsyncMDNSEngine] = None
        self._sync_lock = threading.RLock()
        self._async_loop_lock: Optional[
            Tuple[asyncio.AbstractEventLoop, asyncio.Lock]
        ] = None
        self._sync_refs = 0
        self._async_refs = 0

    def _get_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._async_loop_lock is None or self._async_loop_lock[0] is not loop:
            self._async_loop_lock = (loop, asyncio.Lock())
        return self._async_loop_lock[1]

    @property
    def engine(self) -> MDNSEngine:
        """Lazily instantiated synchronous engine."""
        if self._sync_engine is None:
            self._sync_engine = MDNSEngine(
                config=self.config,
                use_worker_lock=self.use_worker_lock,
                auto_atexit=True,
                verbose=self.verbose,
                allow_name_change=self.allow_name_change,
                strict=self.strict,
                watch_interval=self.watch_interval,
            )
        return self._sync_engine

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def host_name(self) -> str:
        return self.config.host_name

    @property
    def ip(self) -> str:
        return self.config.ip

    @property
    def port(self) -> int:
        return self.config.port

    @property
    def device_suffix(self) -> str:
        return self.config.device_suffix

    @property
    def is_registered(self) -> bool:
        if self._async_engine is not None and self._async_engine.is_registered:
            return True
        if self._sync_engine is not None and self._sync_engine.is_registered:
            return True
        return False

    def start(self) -> bool:
        """Start mDNS broadcast synchronously."""
        return self.engine.start()

    def stop(self) -> None:
        """Stop mDNS broadcast synchronously."""
        if self._sync_engine is not None:
            self._sync_engine.stop()

    def refresh(self) -> bool:
        """Dynamically re-probe LAN IP and update synchronous mDNS record if changed."""
        if self._sync_engine is not None:
            return self._sync_engine.refresh()
        return False

    async def async_start(self) -> bool:
        """Start mDNS broadcast asynchronously."""
        if self._async_engine is None:
            self._async_engine = AsyncMDNSEngine(
                config=self.config,
                use_worker_lock=self.use_worker_lock,
                auto_atexit=True,
                verbose=self.verbose,
                allow_name_change=self.allow_name_change,
                strict=self.strict,
                watch_interval=self.watch_interval,
            )
        return await self._async_engine.start()

    async def async_stop(self) -> None:
        """Stop mDNS broadcast asynchronously."""
        if self._async_engine is not None:
            await self._async_engine.stop()

    async def async_refresh(self) -> bool:
        """Dynamically re-probe LAN IP and update asynchronous mDNS record if changed."""
        if self._async_engine is not None:
            return await self._async_engine.refresh()
        return False

    # Synchronous Context Manager (thread-safe & re-entrant)
    def __enter__(self) -> "MDNSServer":
        with self._sync_lock:
            if self._sync_refs == 0:
                self.start()
            self._sync_refs += 1
            return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        with self._sync_lock:
            if self._sync_refs > 0:
                self._sync_refs -= 1
            if self._sync_refs == 0:
                self.stop()

    # Asynchronous Context Manager (coroutine-safe & concurrent-decorator safe)
    async def __aenter__(self) -> "MDNSServer":
        async with self._get_async_lock():
            if self._async_refs == 0:
                await self.async_start()
            self._async_refs += 1
            return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        async with self._get_async_lock():
            if self._async_refs > 0:
                self._async_refs -= 1
            if self._async_refs == 0:
                await self.async_stop()

    # Decorator support for functions and coroutines
    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                async with self:
                    return await func(*args, **kwargs)

            return async_wrapped
        else:

            @functools.wraps(func)
            def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
                with self:
                    return func(*args, **kwargs)

            return sync_wrapped


class AsyncMDNSServer(MDNSServer):
    """
    Dedicated asynchronous mDNS server alias for backwards compatibility.
    """

    async def __aenter__(self) -> "AsyncMDNSServer":
        await super().__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await super().__aexit__(exc_type, exc_val, exc_tb)
