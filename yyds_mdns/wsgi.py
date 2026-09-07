# -*- coding:utf-8 -*-

"""
Universal WSGI (PEP 3333) protocol adapter and middleware.
Supports all WSGI frameworks: Flask, Django WSGI, Bottle, Falcon, Pyramid, CherryPy, etc.
"""

import atexit
from typing import Any, Callable, Dict, Optional

from yyds_mdns.core.engine import MDNSEngine
from yyds_mdns.core.service import MDNSServiceConfig


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
        self.auto_start = auto_start
        self.engine: Optional[MDNSEngine] = None

        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> Any:
        """Bind mDNS engine to WSGI application."""
        self.app = app
        if self.engine is None:
            self.engine = MDNSEngine(
                config=self.config,
                use_worker_lock=self.use_worker_lock,
                auto_atexit=True,
                verbose=self.verbose,
            )

        # Flask extension registry
        if hasattr(app, "extensions") and isinstance(app.extensions, dict):
            app.extensions["yyds_mdns"] = self

        # Generic metadata attachment
        try:
            setattr(app, "mdns_config", self.config)
            setattr(app, "mdns_url", self.config.url)
            setattr(app, "mdns_engine", self.engine)
        except Exception:
            pass

        if self.auto_start:
            self.start()

        atexit.register(self.stop)
        return self

    def start(self) -> bool:
        """Trigger mDNS broadcast."""
        if self.engine:
            return self.engine.start()
        return False

    def stop(self) -> None:
        """Stop mDNS broadcast and send Goodbye packet."""
        if self.engine:
            self.engine.stop()

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Any:
        """PEP 3333 standard WSGI application callable interface."""
        if self.app is None:
            raise RuntimeError("WSGI application not provided to WSGIMDNS.")
        if not self.is_registered and self.auto_start:
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
