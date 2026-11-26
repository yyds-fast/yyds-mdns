# -*- coding:utf-8 -*-

"""
Universal ASGI 3.0 protocol adapter and lifespan middleware.
Supports all ASGI frameworks: FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic, etc.
"""

from contextlib import asynccontextmanager
import inspect
from typing import Any, Callable, Dict, Optional

from yyds_mdns.core.engine import AsyncMDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


class ASGIMDNS:
    """
    Universal ASGI protocol adapter & lifespan middleware.

    Supports:
    1. In-place hook for FastAPI/Starlette:
       MDNS(app, name="fastapi-api", port=8000)
    2. In-place hook for Django ASGI / Litestar / Quart / Sanic / aiohttp:
       MDNS(application, name="django-api", port=8000)
    3. Pure ASGI 3.0 Middleware wrapper for any ASGI callable:
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
        allow_name_change: bool = False,
        strict: bool = True,
        watch_interval: float = 0.0,
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
            allow_name_change=allow_name_change,
            strict=strict,
            watch_interval=watch_interval,
        )
        self._hooked_native = False
        self._wrapped_inplace = False
        self._lifespan_seen = False
        self._lazy_start_attempted = False

        if app is not None:
            self.init_app(app)

    def _sync_app_metadata(self, app: Any) -> None:
        if app is None:
            return
        if hasattr(app, "state"):
            try:
                app.state.mdns_config = self.config
                app.state.mdns_engine = self.engine
                app.state.mdns_url = self.config.url
            except Exception:
                pass
        else:
            try:
                setattr(app, "mdns_config", self.config)
                setattr(app, "mdns_engine", self.engine)
                setattr(app, "mdns_url", self.config.url)
            except Exception:
                pass

    async def _start_and_sync(self) -> bool:
        ok = await self.engine.start()
        self._sync_app_metadata(self.app)
        return ok

    def init_app(self, app: Any) -> "ASGIMDNS":
        """Bind mDNS to ASGI application idempotently using optimal strategy."""
        self.app = app

        # Strategy 1: FastAPI / Starlette in-place lifespan hook
        if hasattr(app, "router") and hasattr(app.router, "lifespan_context"):
            # Unwrap previous yyds_mdns lifespan wrapper if re-bound on same app
            base_lifespan: Optional[Callable] = getattr(
                app.router,
                "_yyds_mdns_orig_lifespan",
                getattr(app.router, "lifespan_context", None),
            )
            try:
                setattr(app.router, "_yyds_mdns_orig_lifespan", base_lifespan)
            except Exception:
                pass

            @asynccontextmanager
            async def _mdns_lifespan(application: Any):
                await self._start_and_sync()
                try:
                    if base_lifespan is not None:
                        async with base_lifespan(application) as maybe_state:
                            yield maybe_state
                    else:
                        yield
                finally:
                    await self.engine.stop()

            app.router.lifespan_context = _mdns_lifespan
            self._hooked_native = True

        # Strategy 2: Sanic listeners
        elif hasattr(app, "before_server_start") and hasattr(app, "after_server_stop"):

            @app.before_server_start
            async def _start_sanic(sanic_app, loop):
                await self._start_and_sync()

            @app.after_server_stop
            async def _stop_sanic(sanic_app, loop):
                await self.engine.stop()

            self._hooked_native = True

        elif hasattr(app, "register_listener"):

            async def _start_sanic_legacy(sanic_app, loop):
                await self._start_and_sync()

            async def _stop_sanic_legacy(sanic_app, loop):
                await self.engine.stop()

            app.register_listener(_start_sanic_legacy, "before_server_start")
            app.register_listener(_stop_sanic_legacy, "after_server_stop")
            self._hooked_native = True

        # Strategy 3: aiohttp signals
        elif hasattr(app, "on_startup") and hasattr(app, "on_cleanup"):

            async def _start_aiohttp(aio_app):
                await self._start_and_sync()

            async def _stop_aiohttp(aio_app):
                await self.engine.stop()

            app.on_startup.append(_start_aiohttp)
            app.on_cleanup.append(_stop_aiohttp)
            self._hooked_native = True

        # Strategy 4: Generic ASGI class instance (e.g. Django ASGIHandler, Litestar, Quart)
        # Enables one-liner `MDNS(application, ...)` without requiring `application = MDNS(...)`.
        elif (
            callable(app) and not inspect.isfunction(app) and not inspect.ismethod(app)
        ):
            if getattr(app, "_yyds_mdns_wrapped", False):
                # Re-binding onto an already wrapped instance: swap adapter reference without re-subclassing
                setattr(app, "_yyds_mdns_adapter", self)
                self._wrapped_inplace = True
            else:
                orig_cls = type(app)
                orig_call = orig_cls.__call__

                try:

                    class _MDNSWrappedASGI(orig_cls):  # type: ignore[valid-type, misc]
                        async def __call__(
                            self_app: Any,
                            scope: Dict[str, Any],
                            receive: Callable,
                            send: Callable,
                        ) -> Any:
                            bound_orig = orig_call.__get__(self_app, orig_cls)
                            active_adapter: ASGIMDNS = getattr(
                                self_app, "_yyds_mdns_adapter"
                            )
                            return await active_adapter._handle_asgi(
                                bound_orig, scope, receive, send
                            )

                    _MDNSWrappedASGI.__name__ = orig_cls.__name__
                    _MDNSWrappedASGI.__qualname__ = orig_cls.__qualname__
                    _MDNSWrappedASGI.__module__ = orig_cls.__module__
                    setattr(app, "_yyds_mdns_adapter", self)
                    setattr(app, "_yyds_mdns_wrapped", True)
                    app.__class__ = _MDNSWrappedASGI
                    self._wrapped_inplace = True
                except TypeError:
                    pass

        self._sync_app_metadata(app)
        return self

    async def _run_standalone_lifespan(self, receive: Callable, send: Callable) -> None:
        """
        Handle ASGI 3.0 lifespan directly when the wrapped application (e.g. Django ASGIHandler)
        does not implement the ASGI lifespan protocol.
        """
        while True:
            message = await receive()
            msg_type = message.get("type")
            if msg_type == "lifespan.startup":
                try:
                    await self._start_and_sync()
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif msg_type == "lifespan.shutdown":
                await self.engine.stop()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _handle_asgi(
        self,
        inner_app: Callable,
        scope: Dict[str, Any],
        receive: Callable,
        send: Callable,
    ) -> Any:
        """Core ASGI 3.0 request & lifespan interceptor."""
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            self._lifespan_seen = True
            saw_startup = False
            saw_shutdown = False
            sent_startup_complete = False
            startup_failed = False

            async def lifespan_receive() -> Dict[str, Any]:
                nonlocal saw_startup, saw_shutdown
                message = await receive()
                if message["type"] == "lifespan.startup":
                    saw_startup = True
                    await self._start_and_sync()
                elif message["type"] == "lifespan.shutdown":
                    saw_shutdown = True
                return message

            async def lifespan_send(message: Dict[str, Any]) -> None:
                nonlocal sent_startup_complete, startup_failed
                mtype = message.get("type")
                if mtype == "lifespan.startup.complete":
                    sent_startup_complete = True
                elif mtype == "lifespan.startup.failed":
                    startup_failed = True
                await send(message)

            try:
                return await inner_app(scope, lifespan_receive, lifespan_send)
            except Exception:
                if saw_startup or sent_startup_complete:
                    if self.engine.is_registered:
                        await self.engine.stop()
                    raise
                # Inner app rejected lifespan scope before consuming startup (e.g. Django ASGIHandler)
                await self._run_standalone_lifespan(receive, send)
                return None
            finally:
                if (saw_shutdown or startup_failed) and self.engine.is_registered:
                    await self.engine.stop()

        # One-shot lazy fallback when ASGI server runs with lifespan disabled (--lifespan off)
        if not self._lifespan_seen and not self._lazy_start_attempted:
            self._lazy_start_attempted = True
            await self._start_and_sync()

        return await inner_app(scope, receive, send)

    async def __call__(
        self, scope: Dict[str, Any], receive: Callable, send: Callable
    ) -> Any:
        """
        ASGI 3.0 interface callable.
        Intercepts standard ASGI 'lifespan' scope to control mDNS start/stop.
        """
        if self.app is None:
            raise RuntimeError("ASGI application not provided to ASGIMDNS.")

        if self._hooked_native or self._wrapped_inplace:
            return await self.app(scope, receive, send)

        return await self._handle_asgi(self.app, scope, receive, send)

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def is_registered(self) -> bool:
        return self.engine.is_registered


# Friendly aliases
ASGIMDNSMiddleware = ASGIMDNS
MDNS = ASGIMDNS
