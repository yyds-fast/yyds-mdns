# -*- coding:utf-8 -*-

"""
Universal ASGI 3.0 protocol adapter and lifespan middleware.
Supports all ASGI frameworks: FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic, etc.
"""

from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from yyds_mdns.core.engine import AsyncMDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


class ASGIMDNS:
    """
    Universal ASGI protocol adapter & lifespan middleware.

    Supports:
    1. In-place hook for FastAPI/Starlette:
       MDNS(app, name="fastapi-api", port=8000)
    2. Event hook for Sanic/aiohttp:
       MDNS(app, name="sanic-api", port=8000)
    3. Pure ASGI 3.0 Middleware wrapper for any ASGI app (Litestar, Quart, Django ASGI, etc.):
       app = ASGIMDNS(app, name="asgi-app", port=8000)
    """

    def __init__(
        self,
        app: Optional[Any] = None,
        name: str = "asgi-service",
        port: int = 8000,
        ip: Optional[str] = None,
        interface: Optional[str] = None,
        protocol: str = "_http._tcp.local.",
        properties: Optional[Dict[str, Any]] = None,
        server: Optional[str] = None,
        use_worker_lock: bool = True,
        verbose: bool = True,
        unique: bool = False,
        **kwargs: Any,
    ):
        self.app = app
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
            **kwargs,
        )
        self.use_worker_lock = use_worker_lock
        self.engine = AsyncMDNSEngine(
            config=self.config,
            use_worker_lock=self.use_worker_lock,
            auto_atexit=True,
            verbose=verbose,
        )

        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> Any:
        """Bind mDNS to ASGI application using optimal strategy."""
        self.app = app

        # Strategy 1: FastAPI / Starlette in-place lifespan hook
        if hasattr(app, "router") and hasattr(app.router, "lifespan_context"):
            original_lifespan: Optional[Callable] = getattr(
                app.router, "lifespan_context", None
            )

            @asynccontextmanager
            async def _mdns_lifespan(application: Any):
                await self.engine.start()
                try:
                    if original_lifespan is not None:
                        async with original_lifespan(application) as maybe_state:
                            yield maybe_state
                    else:
                        yield
                finally:
                    await self.engine.stop()

            app.router.lifespan_context = _mdns_lifespan

        # Strategy 2: Sanic listeners
        elif hasattr(app, "before_server_start"):

            @app.before_server_start
            async def _start_sanic(sanic_app, loop):
                await self.engine.start()

            @app.after_server_stop
            async def _stop_sanic(sanic_app, loop):
                await self.engine.stop()

        # Strategy 3: aiohttp signals
        elif hasattr(app, "on_startup") and hasattr(app, "on_cleanup"):

            async def _start_aiohttp(aio_app):
                await self.engine.start()

            async def _stop_aiohttp(aio_app):
                await self.engine.stop()

            app.on_startup.append(_start_aiohttp)
            app.on_cleanup.append(_stop_aiohttp)

        # Attach metadata
        if hasattr(app, "state"):
            app.state.mdns_config = self.config
            app.state.mdns_engine = self.engine
            app.state.mdns_url = self.config.url
        else:
            try:
                setattr(app, "mdns_url", self.config.url)
            except Exception:
                pass

        return self

    async def __call__(
        self, scope: Dict[str, Any], receive: Callable, send: Callable
    ) -> None:
        """
        ASGI 3.0 interface callable.
        Intercepts standard ASGI 'lifespan' scope to control mDNS start/stop.
        """
        if self.app is None:
            raise RuntimeError("ASGI application not provided to ASGIMDNS.")

        if scope["type"] == "lifespan":

            async def lifespan_receive() -> Dict[str, Any]:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await self.engine.start()
                elif message["type"] == "lifespan.shutdown":
                    await self.engine.stop()
                return message

            await self.app(scope, lifespan_receive, send)
        else:
            await self.app(scope, receive, send)

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def is_registered(self) -> bool:
        return self.engine.is_registered


# Friendly aliases
ASGIMDNSMiddleware = ASGIMDNS
MDNS = ASGIMDNS
