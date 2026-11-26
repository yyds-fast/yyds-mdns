# -*- coding:utf-8 -*-

"""
Universal polymorphic MDNS dispatcher.
Driven by ASGI 3.0 and WSGI (PEP 3333) standard protocols.
"""

import inspect
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Optional

from yyds_mdns.asgi import ASGIMDNS
from yyds_mdns.core.service import resolve_port
from yyds_mdns.server import MDNSServer
from yyds_mdns.wsgi import WSGIMDNS

_GENERIC_FRAMEWORK_TITLES = frozenset({"fastapi", "starlette", "litestar", "quart"})
_CLI_RUNNER_NAMES = frozenset(
    {
        "-c",
        "__main__",
        "pytest",
        "unittest",
        "python",
        "python3",
        "uvicorn",
        "gunicorn",
        "hypercorn",
        "daphne",
        "granian",
        "flask",
        "django-admin",
        "manage",
    }
)


def _sanitize_dns_label(raw: str, max_len: int = 55) -> str:
    """
    Sanitize an arbitrary string into a browser-compatible RFC 1123 ASCII DNS label.
    Collapses consecutive non-alphanumeric chars into single hyphens and caps length.
    """
    clean = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip("-")
    return clean


def _is_coroutine_callable(obj: Any) -> bool:
    if inspect.iscoroutinefunction(obj):
        return True
    call_attr = getattr(obj, "__call__", None)
    return bool(call_attr is not None and inspect.iscoroutinefunction(call_attr))


def is_asgi_application(app: Any) -> bool:
    """Detect if object conforms to ASGI 3.0 or async web framework pattern."""
    if app is None or inspect.isclass(app):
        return False
    if hasattr(app, "router") and hasattr(app.router, "lifespan_context"):
        return True
    if type(app).__name__ == "ASGIHandler" or hasattr(app, "asgi_app"):
        return True
    if hasattr(app, "before_server_start") or hasattr(app, "register_listener"):
        return True
    if hasattr(app, "on_startup") and hasattr(app, "on_cleanup"):
        return True

    if callable(app):
        try:
            sig = inspect.signature(app)
            params = list(sig.parameters.keys())
            has_asgi_names = "scope" in params and "receive" in params
            if inspect.isfunction(app) or inspect.ismethod(app):
                return _is_coroutine_callable(app) and (
                    has_asgi_names or params == ["scope", "receive", "send"]
                )
            # Callable class instance (middleware or ASGI framework app)
            if has_asgi_names or (len(params) == 3 and _is_coroutine_callable(app)):
                return True
        except Exception:
            pass
    return False


def is_wsgi_application(app: Any) -> bool:
    """Detect if object conforms to WSGI (PEP 3333) or synchronous framework pattern."""
    if app is None or inspect.isclass(app):
        return False
    if hasattr(app, "wsgi_app"):
        return True
    if type(app).__name__ == "WSGIHandler":
        return True

    if callable(app) and not _is_coroutine_callable(app):
        try:
            sig = inspect.signature(app)
            params = list(sig.parameters.keys())
            has_wsgi_names = len(params) == 2 and (
                params[0] in ("environ", "env") or params[1] == "start_response"
            )
            if inspect.isfunction(app) or inspect.ismethod(app):
                return has_wsgi_names
            # Callable class instance (WSGI app / middleware)
            if len(params) == 2 or has_wsgi_names:
                return True
        except Exception:
            pass
    return False


def is_plain_callable(obj: Any) -> bool:
    """Detect if callable is a normal Python function/coroutine (for bare decorator @MDNS)."""
    if not callable(obj) or inspect.isclass(obj):
        return False
    if is_asgi_application(obj):
        return False
    if is_wsgi_application(obj):
        return False
    return True


def _infer_from_argv() -> str:
    """Extract clean service name from sys.argv, skipping runner binaries like uvicorn/gunicorn."""
    try:
        if not sys.argv:
            return ""
        raw_arg0 = sys.argv[0] or ""
        stem = Path(raw_arg0).stem.lower()

        is_runner = (
            not stem
            or stem in _CLI_RUNNER_NAMES
            or stem.startswith("python")
            or "unittest" in raw_arg0.lower()
            or "pytest" in raw_arg0.lower()
        )
        if not is_runner:
            return _sanitize_dns_label(stem)

        # Look for `module:app` target (e.g. `uvicorn my_shop.main:app`)
        for arg in sys.argv[1:]:
            if arg.startswith("-"):
                continue
            if ":" in arg and os.path.sep not in arg:
                mod_part = arg.split(":", 1)[0].split(".")[-1]
                candidate = _sanitize_dns_label(mod_part)
                if candidate and candidate not in _CLI_RUNNER_NAMES:
                    return candidate
    except Exception:
        pass
    return ""


def infer_service_name(app: Any = None, default: str = "service") -> str:
    """
    Intelligently infer RFC 1123 compliant service name from:
    1. FastAPI / Starlette app.title
    2. Flask / Sanic app.name
    3. Decorated function/coroutine __name__
    4. Server target module (`uvicorn my_api:app`) or executing script basename
    """
    if app is not None:
        title = getattr(app, "title", None)
        if (
            title
            and isinstance(title, str)
            and title.strip().lower() not in _GENERIC_FRAMEWORK_TITLES
        ):
            clean = _sanitize_dns_label(title)
            if clean:
                return clean

        name = getattr(app, "name", None)
        if name and isinstance(name, str) and name != "__main__":
            clean = _sanitize_dns_label(name)
            if clean:
                return clean

        func_name = getattr(app, "__name__", None)
        if (
            func_name
            and isinstance(func_name, str)
            and func_name not in ("__main__", "<lambda>")
        ):
            clean = _sanitize_dns_label(func_name)
            if clean:
                return clean

    argv_candidate = _infer_from_argv()
    if argv_candidate:
        return argv_candidate

    return default


def _infer_port_from_argv() -> Optional[int]:
    """
    Extract server listening port from web runner CLI flags (e.g. `uvicorn main:app --port 9000`,
    `flask run -p 5001`, `gunicorn -b 0.0.0.0:8080`, `manage.py runserver 0.0.0.0:8001`).
    """
    try:
        if not sys.argv:
            return None
        stem = Path(sys.argv[0] or "").stem.lower()
        if stem in ("yyds-mdns", "yyds_mdns", "pytest", "unittest"):
            return None

        args = sys.argv[1:]
        for idx, arg in enumerate(args):
            val: Optional[str] = None
            if arg in ("--port", "-p") and idx + 1 < len(args):
                val = args[idx + 1]
            elif arg.startswith("--port="):
                val = arg.split("=", 1)[1]
            elif arg.startswith("-p="):
                val = arg.split("=", 1)[1]
            elif arg in ("--bind", "-b") and idx + 1 < len(args):
                bind_target = args[idx + 1]
                if ":" in bind_target:
                    val = bind_target.rsplit(":", 1)[1]
            elif arg.startswith("--bind="):
                bind_target = arg.split("=", 1)[1]
                if ":" in bind_target:
                    val = bind_target.rsplit(":", 1)[1]
            elif arg == "runserver" and idx + 1 < len(args):
                nxt = args[idx + 1]
                if not nxt.startswith("-"):
                    val = nxt.rsplit(":", 1)[-1]

            if val and val.isdigit():
                p = int(val)
                if 1 <= p <= 65535:
                    return p
    except Exception:
        pass
    return None


def infer_port(app: Any = None, port: Optional[int] = None) -> int:
    """
    Intelligently infer port from explicit argument, environment variables,
    web runner CLI flags, or framework conventions.
    """
    if port is not None:
        return resolve_port(port)

    for env_k in ("PORT", "SERVER_PORT", "WEB_PORT"):
        val = os.environ.get(env_k)
        if val and val.isdigit():
            p = int(val)
            if 1 <= p <= 65535:
                return p

    argv_port = _infer_port_from_argv()
    if argv_port is not None:
        return argv_port

    default_port = 5000 if (app is not None and hasattr(app, "wsgi_app")) else 8000
    return resolve_port(None, default=default_port)


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
    if app is not None and inspect.isclass(app):
        raise TypeError(
            f"MDNS() expects an application instance or function, got class {app.__name__!r}."
        )

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
