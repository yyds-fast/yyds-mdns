# -*- coding:utf-8 -*-

"""
Core mDNS engine and networking internals.
"""

from yyds_mdns.core.engine import AsyncMDNSEngine, MDNSEngine
from yyds_mdns.core.exceptions import (
    MDNSNetworkError,
    MDNSRegistrationError,
    MDNSResolutionTimeoutError,
    MDNSServiceConflictError,
    YYDSMDNSError,
)
from yyds_mdns.core.lock import WorkerLock
from yyds_mdns.core.net_utils import (
    get_device_suffix,
    get_lan_ip,
    get_mac_suffix,
    ip_to_bytes,
    is_valid_ipv4,
)
from yyds_mdns.core.resolver import MDNSResolver
from yyds_mdns.core.service import MDNSServiceConfig

__all__ = [
    "YYDSMDNSError",
    "MDNSNetworkError",
    "MDNSRegistrationError",
    "MDNSServiceConflictError",
    "MDNSResolutionTimeoutError",
    "get_lan_ip",
    "get_mac_suffix",
    "get_device_suffix",
    "ip_to_bytes",
    "is_valid_ipv4",
    "MDNSServiceConfig",
    "WorkerLock",
    "MDNSEngine",
    "AsyncMDNSEngine",
    "MDNSResolver",
]
