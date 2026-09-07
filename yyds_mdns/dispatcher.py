# -*- coding:utf-8 -*-

"""
Universal polymorphic MDNS dispatcher.
Driven by ASGI 3.0 and WSGI (PEP 3333) standard protocols.
"""

import inspect
import os
import sys
from pathlib import Path
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


def is_plain_callable(obj: Any) -> bool:
    """Detect if callable is a normal Python function/coroutine (for bare decorator @MDNS)."""
    if not callable(obj):
        return False
    if is_asgi_application(obj):
        return False
    if is_wsgi_application(obj):
        return False
    return True


def infer_service_name(app: Any = None, default: str = "service") -> str:
    """
    Intelligently infer service name from:
    1. FastAPI / Starlette app.title
    2. Flask / Sanic app.name
    3. Executing script basename (e.g. 'main.py' -> 'main')
    """
    if app is not None:
        title = getattr(app, "title", None)
        if title and isinstance(title, str) and title != "FastAPI":
            clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in title)
            clean = clean.strip("-").lower()
            if clean:
                return clean

        name = getattr(app, "name", None)
        if name and isinstance(name, str) and name != "__main__":
            clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
            clean = clean.strip("-").lower()
            if clean:
                return clean

    try:
        script = Path(sys.argv[0]).stem
        if script and script not in ("-c", "__main__", "pytest", "python"):
            clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in script)
            clean = clean.strip("-").lower()
            if clean:
                return clean
    except Exception:
        pass

    return default


def infer_port(app: Any = None, port: Optional[int] = None) -> int:
    """
    Intelligently infer port from arguments, environment variables, or framework conventions.
    """
    if port is not None:
        return int(port)

    for env_k in ("PORT", "SERVER_PORT", "WEB_PORT"):
        val = os.environ.get(env_k)
        if val and val.isdigit():
            return int(val)

    if app is not None and hasattr(app, "wsgi_app"):
        return 5000  # Default for Flask
    return 8000  # Default for ASGI / standalone


def MDNS(
    app: Optional[Any] = None,
    name: Optional[str] = None,
    port: Optional[int] = None,
    ip: Optional[str] = None,
    interface: Optional[str] = None,
    protocol: str = "_http._tcp.local.",
    properties: Optional[Dict[str, Any]] = None,
    server: Optional[str] = None,
    use_worker_lock: bool = True,
    verbose: bool = True,
    unique: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Universal mDNS registration entrypoint.

    Zero-config support:
    - If `name` is omitted, it is automatically inferred from `app.title`, `app.name`, or filename.
    - If `port` is omitted, it is resolved from PORT env variable or framework default (Flask: 5000, ASGI: 8000).

    Protocol detection:
    - If used as bare decorator `@MDNS`:
      automatically decorates synchronous function or asynchronous coroutine.
    - If app is ASGI (FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic, etc.):
      automatically dispatches to ASGIMDNS adapter.
    - If app is WSGI (Flask, Django WSGI, Bottle, Falcon, Pyramid, etc.):
      automatically dispatches to WSGIMDNS adapter.
    - If app is None:
      returns universal MDNSServer (sync/async context manager & decorator).
    """
    resolved_name = name or infer_service_name(app, default="service")
    resolved_port = infer_port(app, port)

    # 1. Bare decorator case: @MDNS decorating a plain function or coroutine
    if app is not None and is_plain_callable(app):
        server_inst = MDNSServer(
            name=resolved_name,
            port=resolved_port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            unique=unique,
            **kwargs,
        )
        return server_inst(app)

    # 2. Standalone mode (No app passed)
    if app is None:
        return MDNSServer(
            name=resolved_name,
            port=resolved_port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            unique=unique,
            **kwargs,
        )

    # 3. Protocol Check: ASGI (FastAPI, Starlette, Litestar, Quart, Django ASGI, Sanic...)
    if is_asgi_application(app):
        return ASGIMDNS(
            app=app,
            name=resolved_name,
            port=resolved_port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            unique=unique,
            **kwargs,
        )

    # 4. Protocol Check: WSGI (Flask, Django WSGI, Bottle, Falcon, Pyramid...)
    if is_wsgi_application(app):
        return WSGIMDNS(
            app=app,
            name=resolved_name,
            port=resolved_port,
            ip=ip,
            interface=interface,
            protocol=protocol,
            properties=properties,
            server=server,
            use_worker_lock=use_worker_lock,
            verbose=verbose,
            unique=unique,
            **kwargs,
        )

    # 5. Non-callable fallback
    return MDNSServer(
        name=resolved_name,
        port=resolved_port,
        ip=ip,
        interface=interface,
        protocol=protocol,
        properties=properties,
        server=server,
        use_worker_lock=use_worker_lock,
        verbose=verbose,
        unique=unique,
        **kwargs,
    )
