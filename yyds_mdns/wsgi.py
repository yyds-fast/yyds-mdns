# -*- coding:utf-8 -*-

"""
Universal WSGI (PEP 3333) protocol adapter and middleware.
Supports all WSGI frameworks: Flask, Django WSGI, Bottle, Falcon, Pyramid, CherryPy, etc.
"""

import os
import sys
import threading
from typing import Any, Callable, Dict, Optional

from yyds_mdns.core.engine import MDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


def _is_dev_reloader_parent() -> bool:
    """
    Detect if current process is the outer file-watcher parent of Werkzeug (Flask debug)
    or Django StatReloader, so mDNS only binds inside the actual serving child process.
    """
    if (
        os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
        and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    ):
        return True

    if (
        "runserver" in sys.argv
        and "--noreload" not in sys.argv
        and os.environ.get("RUN_MAIN") != "true"
    ):
        return True

    return False


class WSGIMDNS:
    """
    Universal WSGI protocol adapter & middleware.

    Supports:
    1. In-place extension for Flask:
       MDNS(app, name="flask-api", port=5000)
    2. In-place hook for Django:
       MDNS(application, name="django-api", port=8000)
    3. Standard PEP 3333 WSGI Middleware wrapper for any WSGI application:
       app = WSGIMDNS(app, name="wsgi-app", port=8000)
    """

    def __init__(
        self,
        app: Optional[Any] = None,
        name: str = "wsgi-service",
        port: int = 8000,
        ip: Optional[str] = None,
        interface: Optional[str] = None,
        protocol: str = "_http._tcp.local.",
        properties: Optional[Dict[str, Any]] = None,
        server: Optional[str] = None,
        use_worker_lock: bool = True,
        auto_start: bool = True,
        verbose: bool = True,
        unique: bool = False,
        allow_name_change: bool = False,
        strict: bool = True,
        watch_interval: float = 0.0,
        **kwargs: Any,
    ):
        self.app = app
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
            **kwargs,
        )
        self.use_worker_lock = use_worker_lock
        self.auto_start = auto_start
        self.engine: Optional[MDNSEngine] = None
        self._init_lock = threading.Lock()
        self._lazy_start_attempted = False

        if app is not None:
            self.init_app(app)

    def _sync_app_metadata(self, app: Any) -> None:
        if app is None:
            return
        try:
            setattr(app, "mdns_config", self.config)
            setattr(app, "mdns_url", self.config.url)
            setattr(app, "mdns_engine", self.engine)
            setattr(app, "_yyds_mdns_wsgi", self)
        except Exception:
            pass

    def init_app(self, app: Any) -> "WSGIMDNS":
        """Bind mDNS engine to WSGI application idempotently."""
        self.app = app

        # Stop previous bound instance on same app if re-initialized
        prev = getattr(app, "_yyds_mdns_wsgi", None)
        if prev is not None and prev is not self:
            try:
                prev.stop()
            except Exception:
                pass

        if self.engine is None:
            self.engine = MDNSEngine(
                config=self.config,
                use_worker_lock=self.use_worker_lock,
                auto_atexit=True,
                verbose=self.verbose,
                allow_name_change=self.allow_name_change,
                strict=self.strict,
                watch_interval=self.watch_interval,
            )

        # Flask extension registry
        if hasattr(app, "extensions") and isinstance(app.extensions, dict):
            app.extensions["yyds_mdns"] = self

        self._sync_app_metadata(app)

        if self.auto_start and not _is_dev_reloader_parent():
            self._lazy_start_attempted = True
            self.start()
            self._sync_app_metadata(app)

        return self

    def start(self) -> bool:
        """Trigger mDNS broadcast."""
        if self.engine:
            ok = self.engine.start()
            self._sync_app_metadata(self.app)
            return ok
        return False

    def stop(self) -> None:
        """Stop mDNS broadcast and send Goodbye packet."""
        if self.engine:
            self.engine.stop()

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Any:
        """PEP 3333 standard WSGI application callable interface (zero-syscall fast path)."""
        if self.app is None:
            raise RuntimeError("WSGI application not provided to WSGIMDNS.")
        if self.auto_start and not self._lazy_start_attempted:
            with self._init_lock:
                if not self._lazy_start_attempted:
                    self._lazy_start_attempted = True
                    self.start()
        return self.app(environ, start_response)

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def is_registered(self) -> bool:
        return self.engine.is_registered if self.engine else False


# Friendly aliases
WSGIMDNSMiddleware = WSGIMDNS
MDNS = WSGIMDNS
