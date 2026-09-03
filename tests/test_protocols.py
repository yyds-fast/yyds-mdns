# -*- coding:utf-8 -*-

import asyncio
import unittest

from yyds_mdns import MDNS
from yyds_mdns.asgi import ASGIMDNS
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
        settings.configure(DEBUG=True, SECRET_KEY="test-secret")
    django.setup()
    from django.core.asgi import get_asgi_application
    from django.core.wsgi import get_wsgi_application
except ImportError:
    django = None


class TestProtocols(unittest.TestCase):
    def test_generic_sync_adapter(self):
        with MDNSServer(name="test-gen-sync", port=18090, ip="127.0.0.1") as srv:
            self.assertTrue(srv.is_registered)
            self.assertEqual(srv.url, "http://test-gen-sync.local:18090")
            self.assertEqual(srv.host_name, "test-gen-sync.local")
        self.assertFalse(srv.is_registered)

    def test_generic_async_adapter(self):
        async def run_async():
            async with AsyncMDNSServer(
                name="test-gen-async", port=18091, ip="127.0.0.1"
            ) as srv:
                self.assertTrue(srv.is_registered)
                self.assertEqual(srv.url, "http://test-gen-async.local:18091")
            self.assertFalse(srv.is_registered)

        asyncio.run(run_async())

    def test_asgi_adapter_fastapi(self):
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

    def test_wsgi_adapter_flask(self):
        if Flask is None:
            self.skipTest("Flask not installed")

        app = Flask(__name__)
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
            queue = asyncio.Queue()
            await queue.put({"type": "lifespan.startup"})

            async def mock_receive():
                return await queue.get()

            async def mock_send(msg):
                pass

            await wrapped_app(
                {"type": "lifespan"},
                mock_receive,
                mock_send,
            )
            self.assertTrue(wrapped_app.is_registered)

            await queue.put({"type": "lifespan.shutdown"})
            await wrapped_app(
                {"type": "lifespan"},
                mock_receive,
                mock_send,
            )
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

    def test_unified_dispatcher(self):
        # 1. FastAPI -> ASGIMDNS
        if FastAPI is not None:
            fa_app = FastAPI()
            m_fa = MDNS(
                fa_app, name="u-fa", port=8000, ip="127.0.0.1", use_worker_lock=False
            )
            self.assertIsInstance(m_fa, ASGIMDNS)

        # 2. Flask -> WSGIMDNS
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

        # 3. Django
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

            dj_asgi = get_asgi_application()
            m_dj_asgi = MDNS(
                dj_asgi,
                name="u-dj-asgi",
                port=8000,
                ip="127.0.0.1",
                use_worker_lock=False,
                verbose=False,
            )
            self.assertIsInstance(m_dj_asgi, ASGIMDNS)

        # 4. Standalone Context Manager
        with MDNS(
            name="u-standalone", port=9000, ip="127.0.0.1", use_worker_lock=False
        ) as srv:
            self.assertTrue(srv.is_registered)
        self.assertFalse(srv.is_registered)

        # 5. Standalone Async Context Manager
        async def run_async_unified():
            async with MDNS(
                name="u-async-standalone",
                port=9001,
                ip="127.0.0.1",
                use_worker_lock=False,
            ) as srv:
                self.assertTrue(srv.is_registered)
            self.assertFalse(srv.is_registered)

        asyncio.run(run_async_unified())

        # 6. Function Decorator
        @MDNS(name="u-deco", port=9002, ip="127.0.0.1", use_worker_lock=False)
        def sample_worker():
            return 42

        self.assertEqual(sample_worker(), 42)


if __name__ == "__main__":
    unittest.main()
