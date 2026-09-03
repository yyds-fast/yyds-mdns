# -*- coding:utf-8 -*-

"""
yyds-mdns: A lightweight zero-config mDNS service registration & discovery library
driven by standard ASGI and WSGI protocols.
"""

from yyds_mdns.__version__ import (
    __author__ as __author__,
    __description__ as __description__,
    __license__ as __license__,
    __title__ as __title__,
    __url__ as __url__,
    __version__ as __version__,
)
from yyds_mdns.asgi import ASGIMDNS, ASGIMDNSMiddleware
from yyds_mdns.core.engine import AsyncMDNSEngine, MDNSEngine
from yyds_mdns.core.exceptions import (
    MDNSNetworkError,
    MDNSRegistrationError,
    MDNSResolutionTimeoutError,
    MDNSServiceConflictError,
    YYDSMDNSError,
)
from yyds_mdns.core.net_utils import (
    get_all_local_ips,
    get_lan_ip,
    ip_to_bytes,
    is_valid_ipv4,
)
from yyds_mdns.core.resolver import MDNSResolver
from yyds_mdns.core.service import MDNSServiceConfig
from yyds_mdns.dispatcher import MDNS
from yyds_mdns.server import AsyncMDNSServer, MDNSServer
from yyds_mdns.wsgi import WSGIMDNS, WSGIMDNSMiddleware

__all__ = [
    "MDNS",
    "ASGIMDNS",
    "ASGIMDNSMiddleware",
    "WSGIMDNS",
    "WSGIMDNSMiddleware",
    "MDNSServer",
    "AsyncMDNSServer",
    "MDNSEngine",
    "AsyncMDNSEngine",
    "MDNSServiceConfig",
    "MDNSResolver",
    "get_lan_ip",
    "get_all_local_ips",
    "ip_to_bytes",
    "is_valid_ipv4",
    "YYDSMDNSError",
    "MDNSNetworkError",
    "MDNSRegistrationError",
    "MDNSServiceConflictError",
    "MDNSResolutionTimeoutError",
    "__title__",
    "__version__",
    "__author__",
    "__description__",
    "__url__",
    "__license__",
]
