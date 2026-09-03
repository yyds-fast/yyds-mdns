# -*- coding:utf-8 -*-

"""
Example: Generic Context Manager (usable with any TCP server / Sanic / Tornado / etc.).
"""

import time
from yyds_mdns import MDNS


def run_custom_server():
    print("Starting custom server logic...")
    for i in range(3):
        print(f"Server heartbeat tick {i + 1}...")
        time.sleep(1)


def main():
    # 1. Context manager mode (automatically handles Goodbye packet on exit)
    with MDNS(name="custom-node", port=9090) as server:
        print("🚀 mDNS broadcast active!")
        print(f"👉 Local URL  : {server.url}")
        print(f"👉 LAN IP     : {server.ip}")
        run_custom_server()

    print("Server stopped. Goodbye broadcast sent successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
