# -*- coding:utf-8 -*-

"""
Exceptions for yyds-mdns.
"""


class YYDSMDNSError(Exception):
    """Base exception for all yyds-mdns errors."""


class MDNSNetworkError(YYDSMDNSError):
    """Raised when network interface or IP address cannot be resolved."""


class MDNSRegistrationError(YYDSMDNSError):
    """Raised when mDNS service registration fails."""


class MDNSServiceConflictError(MDNSRegistrationError):
    """Raised when mDNS service name already exists in the LAN."""


class MDNSResolutionTimeoutError(YYDSMDNSError):
    """Raised when resolving an mDNS service or host times out."""
