# -*- coding:utf-8 -*-

"""
Universal polymorphic MDNS dispatcher.
Driven by ASGI 3.0 and WSGI (PEP 3333) standard protocols.
"""

import inspect
from typing import Any, Dict, Optional

from yyds_mdns.asgi import ASGIMDNS
from yyds_mdns.server import MDNSServer
from yyds_mdns.wsgi import WSGIMDNS


def is_asgi_application(app: Any) -> bool:
    """Detect if object conforms to ASGI 3.0 or async web framework pattern."""
    if hasattr(app, "router") and hasattr(app.router, "lifespan_context"):
        return True
    if type(app).__name__ == "ASGIHandler":
        return True
    if hasattr(app, "before_server_start") or hasattr(app, "register_listener"):
        return True
    if hasattr(app, "on_startup") and hasattr(app, "on_cleanup"):
        return True
    if callable(app):
        try:
            sig = inspect.signature(app)
            params = list(sig.parameters.keys())
            if len(params) == 3 or ("scope" in params and "receive" in params):
                return True
        except Exception:
            pass
    return False


def is_wsgi_application(app: Any) -> bool:
    """Detect if object conforms to WSGI (PEP 3333) or synchronous framework pattern."""
    if hasattr(app, "wsgi_app"):
        return True
    if type(app).__name__ == "WSGIHandler":
        return True
    if callable(app):
        try:
            sig = inspect.signature(app)
            params = list(sig.parameters.keys())
            if len(params) == 2 or ("environ" in params and "start_response" in params):
                return True
        except Exception:
            pass
    return False


def MDNS(
    app: Optional[Any] = None,
    name: str = "service",
    port: int = 8000,
    ip: Optional[str] = None,
    interface: Optional[str] = None,
    protocol: str = "_http._tcp.local.",
    properties: Optional[Dict[str, Any]] = None,
    server: Optional[str] = None,
    use_worker_lock: bool = True,
    verbose: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Universal mDNS registration entrypoint.

    - If app is ASGI (FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic, etc.):
      automatically dispatches to ASGIMDNS adapter.
    - If app is WSGI (Flask, Django WSGI, Bottle, Falcon, Pyramid, etc.):
      automatically dispatches to WSGIMDNS adapter.
    - If app is None:
      returns universal MDNSServer (sync/async context manager & decorator).
    """
    # 1. Standalone mode (No app passed)
    if app is None:
        return MDNSServer(
            name=name,
            port=port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            **kwargs,
        )

    # 2. Protocol Check: ASGI (FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic...)
    if is_asgi_application(app):
        return ASGIMDNS(
            app=app,
            name=name,
            port=port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            **kwargs,
        )

    # 3. Protocol Check: WSGI (Flask, Django WSGI, Bottle, Falcon, Pyramid...)
    if is_wsgi_application(app):
        return WSGIMDNS(
            app=app,
            name=name,
            port=port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            **kwargs,
        )

    # 4. Fallback for generic callable (default to WSGI adapter)
    if callable(app):
        return WSGIMDNS(
            app=app,
            name=name,
            port=port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            **kwargs,
        )

    # 5. Non-callable fallback
    return MDNSServer(
        name=name,
        port=port,
        ip=ip,
        interface=interface,
        protocol=protocol,
        properties=properties,
        server=server,
        use_worker_lock=use_worker_lock,
        verbose=verbose,
        **kwargs,
    )
