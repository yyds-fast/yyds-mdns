# -*- coding:utf-8 -*-

"""
Universal standalone controller, context manager (sync & async), and decorator for mDNS.
"""

import asyncio
import functools
from typing import Any, Callable, Dict, Optional

from yyds_mdns.core.engine import AsyncMDNSEngine, MDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


class MDNSServer:
    """
    Universal mDNS controller.
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
        use_worker_lock: bool = False,
        verbose: bool = True,
        unique: bool = False,
        suffix_length: int = 4,
        **kwargs: Any,
    ):
        self.verbose = verbose
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
        self.engine = MDNSEngine(
            config=self.config,
            use_worker_lock=use_worker_lock,
            auto_atexit=True,
            verbose=verbose,
        )
        self._async_engine: Optional[AsyncMDNSEngine] = None

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
        return self.engine.is_registered

    def start(self) -> bool:
        """Start mDNS broadcast synchronously."""
        return self.engine.start()

    def stop(self) -> None:
        """Stop mDNS broadcast synchronously."""
        self.engine.stop()

    async def async_start(self) -> bool:
        """Start mDNS broadcast asynchronously."""
        if self._async_engine is None:
            self._async_engine = AsyncMDNSEngine(
                config=self.config,
                use_worker_lock=self.use_worker_lock,
                auto_atexit=True,
                verbose=self.verbose,
            )
        return await self._async_engine.start()

    async def async_stop(self) -> None:
        """Stop mDNS broadcast asynchronously."""
        if self._async_engine is not None:
            await self._async_engine.stop()

    # Synchronous Context Manager
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # Asynchronous Context Manager
    async def __aenter__(self):
        await self.async_start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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

    async def __aenter__(self):
        await self.async_start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.async_stop()
