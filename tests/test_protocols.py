# -*- coding:utf-8 -*-

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from yyds_mdns import MDNS
from yyds_mdns.asgi import ASGIMDNS
from yyds_mdns.dispatcher import infer_service_name
from yyds_mdns.server import AsyncMDNSServer, MDNSServer
from yyds_mdns.wsgi import WSGIMDNS

try:
    from fastapi import FastAPI
except ImportError:
    FastAPI = None

try:
    from flask import Flask
except ImportError:
    Flask = None

try:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY="test-secret",
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=["*"],
        )
    django.setup()
    from django.core.asgi import get_asgi_application
    from django.core.wsgi import get_wsgi_application
    from django.http import HttpResponse
    from django.urls import path

    def _dj_ping(request):
        return HttpResponse("pong")

    urlpatterns = [path("ping", _dj_ping)]
except ImportError:
    django = None


def _make_mock_sync_zc():
    return MagicMock()


def _make_mock_async_zc():
    azc = MagicMock()

    async def _noop(*args, **kwargs):
        return None

    azc.async_register_service = MagicMock(side_effect=_noop)
    azc.async_unregister_service = MagicMock(side_effect=_noop)
    azc.async_close = MagicMock(side_effect=_noop)
    return azc


class TestProtocols(unittest.TestCase):
    def setUp(self):
        self._p_sync = patch(
            "yyds_mdns.core.engine._create_zeroconf",
            side_effect=lambda ip=None: _make_mock_sync_zc(),
        )
        self._p_async = patch(
            "yyds_mdns.core.engine._create_async_zeroconf",
            side_effect=lambda ip=None: _make_mock_async_zc(),
        )
        self._p_sync.start()
        self._p_async.start()

    def tearDown(self):
        self._p_sync.stop()
        self._p_async.stop()

    def test_generic_sync_adapter(self):
        with MDNSServer(
            name="test-gen-sync", port=18090, ip="127.0.0.1", verbose=False
        ) as srv:
            self.assertTrue(srv.is_registered)
            self.assertEqual(srv.url, "http://test-gen-sync.local:18090")
            self.assertEqual(srv.host_name, "test-gen-sync.local")
        self.assertFalse(srv.is_registered)

    def test_generic_async_adapter_and_reentrant_refs(self):
        async def run_async():
            srv = AsyncMDNSServer(
                name="test-gen-async", port=18091, ip="127.0.0.1", verbose=False
            )
            async with srv:
                self.assertTrue(srv.is_registered)
                async with srv:
                    self.assertTrue(srv.is_registered)
                # Inner context exited, outer still active -> must remain registered!
                self.assertTrue(srv.is_registered)
            self.assertFalse(srv.is_registered)

        asyncio.run(run_async())

    def test_asgi_adapter_fastapi_and_no_double_hook(self):
        if FastAPI is None:
            self.skipTest("FastAPI not installed")

        app = FastAPI()
        mdns = ASGIMDNS(
            app,
            name="test-fastapi",
            port=18092,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        )
        self.assertEqual(app.state.mdns_url, "http://test-fastapi.local:18092")

        async def run_lifespan():
            async with app.router.lifespan_context(app):
                self.assertTrue(mdns.engine.is_registered)
            self.assertFalse(mdns.engine.is_registered)

        asyncio.run(run_lifespan())

    def test_wsgi_adapter_flask_and_reloader_parent_skip(self):
        if Flask is None:
            self.skipTest("Flask not installed")

        app = Flask(__name__)
        with patch.dict(os.environ, {"FLASK_DEBUG": "1"}, clear=False):
            os.environ.pop("WERKZEUG_RUN_MAIN", None)
            mdns_parent = WSGIMDNS(
                app,
                name="test-flask-parent",
                port=18093,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
                auto_start=True,
            )
            # Outer reloader watcher must skip eager registration
            self.assertFalse(mdns_parent.is_registered)

        mdns = WSGIMDNS(
            app,
            name="test-flask",
            port=18093,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
            auto_start=False,
        )
        self.assertEqual(app.extensions["yyds_mdns"], mdns)
        self.assertEqual(mdns.url, "http://test-flask.local:18093")
        self.assertTrue(mdns.start())
        self.assertTrue(mdns.is_registered)
        mdns.stop()
        self.assertFalse(mdns.is_registered)

    def test_django_asgi_inplace_lifespan(self):
        if django is None:
            self.skipTest("Django not installed")

        dj_asgi = get_asgi_application()
        mdns = MDNS(
            dj_asgi,
            name="test-django-asgi",
            port=18096,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        )
        self.assertIsInstance(mdns, ASGIMDNS)

        async def simulate_uvicorn_lifespan_on_django():
            in_q: asyncio.Queue = asyncio.Queue()
            out_msgs = []

            await in_q.put({"type": "lifespan.startup"})

            async def receive():
                return await in_q.get()

            async def send(msg):
                out_msgs.append(msg)
                if msg["type"] == "lifespan.startup.complete":
                    # Verify mDNS is actively registered while server is live!
                    self.assertTrue(mdns.is_registered)
                    await in_q.put({"type": "lifespan.shutdown"})

            # Call dj_asgi directly (in-place mode without reassigning application = MDNS(...))
            await dj_asgi({"type": "lifespan"}, receive, send)
            self.assertFalse(mdns.is_registered)
            self.assertEqual(
                [m["type"] for m in out_msgs],
                ["lifespan.startup.complete", "lifespan.shutdown.complete"],
            )

        asyncio.run(simulate_uvicorn_lifespan_on_django())

    def test_pure_asgi_callable_middleware(self):
        received_types = []

        async def dummy_asgi_app(scope, receive, send):
            if scope["type"] == "lifespan":
                msg = await receive()
                received_types.append(msg["type"])

        wrapped_app = ASGIMDNS(
            dummy_asgi_app,
            name="dummy-asgi",
            port=18094,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        )

        async def simulate_lifespan():
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put({"type": "lifespan.startup"})

            async def mock_receive():
                return await queue.get()

            async def mock_send(msg):
                pass

            await wrapped_app({"type": "lifespan"}, mock_receive, mock_send)
            self.assertTrue(wrapped_app.is_registered)

            await queue.put({"type": "lifespan.shutdown"})
            await wrapped_app({"type": "lifespan"}, mock_receive, mock_send)
            self.assertFalse(wrapped_app.is_registered)

        asyncio.run(simulate_lifespan())

    def test_pure_wsgi_callable_middleware(self):
        def dummy_wsgi_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"OK"]

        wrapped_app = WSGIMDNS(
            dummy_wsgi_app,
            name="dummy-wsgi",
            port=18095,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
            auto_start=True,
        )
        self.assertTrue(wrapped_app.is_registered)
        resp = wrapped_app({}, lambda s, h: None)
        self.assertEqual(resp, [b"OK"])
        wrapped_app.stop()
        self.assertFalse(wrapped_app.is_registered)

    def test_wsgi_no_per_request_retry_storm_when_skipped(self):
        def dummy_wsgi_app(environ, start_response):
            return [b"OK"]

        wrapped_app = WSGIMDNS(
            dummy_wsgi_app,
            name="storm-check-wsgi",
            port=18097,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
            auto_start=False,
        )
        wrapped_app.auto_start = True
        with patch.object(wrapped_app, "start", return_value=False) as mock_start:
            for _ in range(10):
                wrapped_app({}, lambda s, h: None)
            # Must attempt lazy start ONCE, never on every HTTP request!
            mock_start.assert_called_once()

    def test_concurrent_async_decorator_creates_single_zeroconf(self):
        created_count = 0

        def counting_azc(ip=None):
            nonlocal created_count
            created_count += 1
            azc = _make_mock_async_zc()
            orig_reg = azc.async_register_service.side_effect

            async def slow_reg(*a, **k):
                await asyncio.sleep(0.05)
                return await orig_reg(*a, **k)

            azc.async_register_service.side_effect = slow_reg
            return azc

        @MDNS(
            name="concurrent-job",
            port=18098,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        )
        async def job(x):
            await asyncio.sleep(0.02)
            return x * 2

        async def run_concurrent():
            with patch(
                "yyds_mdns.core.engine._create_async_zeroconf",
                side_effect=counting_azc,
            ):
                results = await asyncio.gather(job(1), job(2), job(3))
                self.assertEqual(results, [2, 4, 6])
                self.assertEqual(created_count, 1)

        asyncio.run(run_concurrent())

    def test_asgi_middleware_cleans_up_on_startup_failed(self):
        async def failing_startup_asgi(scope, receive, send):
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.failed", "message": "db error"})

        mw = ASGIMDNS(
            failing_startup_asgi,
            name="fail-startup",
            port=18099,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        )

        async def run_fail():
            q: asyncio.Queue = asyncio.Queue()
            await q.put({"type": "lifespan.startup"})
            sent = []

            async def recv():
                return await q.get()

            async def snd(m):
                sent.append(m)

            await mw({"type": "lifespan"}, recv, snd)
            self.assertFalse(mw.is_registered)
            self.assertEqual(sent[0]["type"], "lifespan.startup.failed")

        asyncio.run(run_fail())

    def test_bare_decorator_with_0_2_3_params(self):
        # 0-arg sync & async
        @MDNS
        def sample_bare_sync():
            return 100

        @MDNS
        async def sample_bare_async():
            return 200

        # 2-arg sync & async (must NOT be misclassified as WSGI!)
        @MDNS
        def add_two(a, b):
            return a + b

        @MDNS
        async def async_add_two(a, b):
            return a + b

        # 3-arg sync & async (must NOT be misclassified as ASGI!)
        @MDNS
        def add_three(a, b, c):
            return a + b + c

        @MDNS
        async def async_add_three(a, b, c):
            return a + b + c

        self.assertEqual(sample_bare_sync(), 100)
        self.assertEqual(asyncio.run(sample_bare_async()), 200)
        self.assertEqual(add_two(3, 4), 7)
        self.assertEqual(asyncio.run(async_add_two(5, 6)), 11)
        self.assertEqual(add_three(1, 2, 3), 6)
        self.assertEqual(asyncio.run(async_add_three(4, 5, 6)), 15)

    def test_infer_service_name_edge_cases(self):
        class FakeApp:
            def __init__(self, title=None, name=None):
                self.title = title
                self.name = name

        # Underscores & spaces sanitized to single RFC 1123 hyphens
        self.assertEqual(
            infer_service_name(FakeApp(title="My_Order  API v2!")),
            "my-order-api-v2",
        )
        # Long title > 63 chars safely truncated
        long_inferred = infer_service_name(FakeApp(title="a" * 100))
        self.assertLessEqual(len(long_inferred), 63)

        # Pure non-ASCII title falls back cleanly to ASCII name / argv / default
        with patch("sys.argv", ["uvicorn", "order_service.main:app", "--port", "8000"]):
            self.assertEqual(
                infer_service_name(FakeApp(title="订单服务")),
                "main",
            )
            # Default FastAPI title skipped -> extracts module from uvicorn CLI target
            self.assertEqual(
                infer_service_name(FakeApp(title="FastAPI")),
                "main",
            )

        # Function __name__ inferred cleanly
        def my_background_worker():
            pass

        self.assertEqual(
            infer_service_name(my_background_worker),
            "my-background-worker",
        )

    def test_infer_port_from_cli_flags_and_class_guard(self):
        from yyds_mdns.dispatcher import infer_port

        with patch.dict(os.environ, {}, clear=False):
            for k in ("PORT", "SERVER_PORT", "WEB_PORT"):
                os.environ.pop(k, None)

            with patch("sys.argv", ["uvicorn", "main:app", "--port", "9090"]):
                self.assertEqual(infer_port(), 9090)
            with patch("sys.argv", ["flask", "run", "-p", "5005"]):
                self.assertEqual(infer_port(), 5005)
            with patch("sys.argv", ["gunicorn", "main:app", "-b", "0.0.0.0:8088"]):
                self.assertEqual(infer_port(), 8088)
            with patch("sys.argv", ["manage.py", "runserver", "0.0.0.0:8001"]):
                self.assertEqual(infer_port(), 8001)

        class NotAnInstance:
            pass

        with self.assertRaises(TypeError):
            MDNS(NotAnInstance)

    def test_unified_dispatcher(self):
        if FastAPI is not None:
            fa_app = FastAPI()
            m_fa = MDNS(
                fa_app,
                name="u-fa",
                port=8000,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
            )
            self.assertIsInstance(m_fa, ASGIMDNS)

        if Flask is not None:
            fl_app = Flask("u-flask")
            m_fl = MDNS(
                fl_app,
                name="u-fl",
                port=5000,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
                auto_start=False,
            )
            self.assertIsInstance(m_fl, WSGIMDNS)

        if django is not None:
            dj_wsgi = get_wsgi_application()
            m_dj = MDNS(
                dj_wsgi,
                name="u-dj",
                port=8000,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
                auto_start=False,
            )
            self.assertIsInstance(m_dj, WSGIMDNS)

        with MDNS(
            name="u-standalone",
            port=9000,
            ip="127.0.0.1",
            use_worker_lock=False,
            verbose=False,
        ) as srv:
            self.assertTrue(srv.is_registered)
        self.assertFalse(srv.is_registered)

        async def run_async_unified():
            async with MDNS(
                name="u-async-standalone",
                port=9001,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
            ) as srv:
                self.assertTrue(srv.is_registered)
            self.assertFalse(srv.is_registered)

        asyncio.run(run_async_unified())


if __name__ == "__main__":
    unittest.main()
