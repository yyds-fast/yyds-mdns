# -*- coding:utf-8 -*-

"""
Example: Discovering and Resolving services on LAN.
"""

from yyds_mdns import MDNSResolver


def main():
    print("Scanning LAN for HTTP services (timeout 2s)...")
    services = MDNSResolver.discover(service_type="_http._tcp.local.", timeout=2.0)
    print(f"Discovered {len(services)} services:")
    for s in services:
        print(f"  - {s['name']} -> {s['url']} ({s['ip']}:{s['port']})")

    print("\nResolving 'fastapi-demo' specifically...")
    res = MDNSResolver.resolve("fastapi-demo", timeout=1.5)
    if res:
        print(f"Found: {res['url']} at IP {res['ip']}")
    else:
        print("fastapi-demo is not currently online in LAN.")


if __name__ == "__main__":
    main()
