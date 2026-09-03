# -*- coding:utf-8 -*-

"""
Example: Django zero-config mDNS integration in wsgi.py or asgi.py:

    from django.core.wsgi import get_wsgi_application
    from yyds_mdns import MDNS

    application = get_wsgi_application()
    MDNS(application, name="django-demo", port=8000)
"""

import django
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.http import JsonResponse
from django.urls import path

# Minimal standalone Django configuration
if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="yyds-secret-key",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
    )
    django.setup()


def index(request):
    return JsonResponse(
        {
            "status": "ok",
            "framework": "Django",
            "message": "Hello from yyds-mdns Django server!",
            "access_via": "http://django-demo.local:8000",
        }
    )


urlpatterns = [
    path("", index),
]

application = get_wsgi_application()

# One-liner to register mDNS: Accessible at http://django-demo.local:8000
from yyds_mdns import MDNS  # noqa: E402

MDNS(application, name="django-demo", port=8000)

if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("\n🚀 Serving Django demo on http://0.0.0.0:8000")
    print("👉 Accessible in LAN via: http://django-demo.local:8000\n")
    try:
        with make_server("0.0.0.0", 8000, application) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
