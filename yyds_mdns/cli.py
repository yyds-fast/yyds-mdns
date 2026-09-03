# -*- coding:utf-8 -*-

"""
CLI entry point for yyds-mdns.
"""

import argparse
import signal
import sys
import time

from yyds_mdns.__version__ import __version__
from yyds_mdns.core.engine import MDNSEngine
from yyds_mdns.core.net_utils import (
    get_adapter_candidates,
    get_all_local_ips,
    get_lan_ip,
)
from yyds_mdns.core.resolver import MDNSResolver
from yyds_mdns.core.service import MDNSServiceConfig


def _cmd_run(args: argparse.Namespace) -> None:
    config = MDNSServiceConfig(
        name=args.name,
        port=args.port,
        ip=args.ip,
        interface=args.interface,
        protocol=args.type,
    )
    engine = MDNSEngine(config, use_worker_lock=False)

    print("\n" + "=" * 55)
    print("  🚀 YYDS-mDNS Local Service Broadcaster")
    print("=" * 55)
    print(f"  • Service Name : {config.name}")
    print(f"  • Local Domain : {config.host_name}")
    print(f"  • LAN IP       : {config.ip}")
    if config.interface:
        print(f"  • Interface    : {config.interface}")
    print(f"  • Port         : {config.port}")
    print(f"  • Access URL   : {config.url}")
    print(f"  • DNS-SD Type  : {config.protocol}")
    print("=" * 55)
    print("  Broadcasting... Press Ctrl+C to stop.")
    print("=" * 55 + "\n")

    def _signal_handler(sig, frame):
        print("\n\n  👋 Stopping mDNS broadcast (sending Goodbye packet)...")
        engine.stop()
        print("  ✅ Gracefully stopped. Bye!\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    engine.start()

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        _signal_handler(None, None)


def _cmd_scan(args: argparse.Namespace) -> None:
    stype = args.type or "_http._tcp.local."
    timeout = args.timeout or 2.5
    print(
        f"\n🔍 Scanning LAN for mDNS services (type: {stype}, timeout: {timeout}s)..."
    )
    services = MDNSResolver.discover(service_type=stype, timeout=timeout)

    if not services:
        print("❌ No mDNS services discovered.\n")
        return

    print(f"\n✅ Found {len(services)} active service(s):\n")
    print(f"{'NAME':<30} {'URL':<35} {'IP:PORT'}")
    print("-" * 75)
    for s in services:
        name = s["name"][:28]
        url = (s["url"] or "N/A")[:33]
        addr = f"{s['ip']}:{s['port']}"
        print(f"{name:<30} {url:<35} {addr}")
    print()


def _cmd_resolve(args: argparse.Namespace) -> None:
    name = args.name
    timeout = args.timeout or 2.5
    print(f"\n🔍 Resolving '{name}' on LAN (timeout: {timeout}s)...")
    res = MDNSResolver.resolve(name=name, timeout=timeout)

    if not res:
        print(f"❌ Failed to resolve '{name}'.\n")
        return

    print("\n✅ Successfully resolved:")
    print(f"  • Name    : {res['name']}")
    print(f"  • Host    : {res['server']}")
    print(f"  • IP      : {res['ip']}")
    print(f"  • Port    : {res['port']}")
    print(f"  • URL     : {res['url']}")
    if res["properties"]:
        print(f"  • TXT Props: {res['properties']}")
    print()


def _cmd_ip(args: argparse.Namespace) -> None:
    preferred = get_lan_ip()
    all_ips = get_all_local_ips()
    candidates = get_adapter_candidates()

    print("\n🌐 Network Interfaces & LAN IP Info:")
    print(f"  • Primary LAN IP (Auto Selected) : {preferred}")
    print(
        f"  • Filtered LAN IP(s)             : {', '.join(all_ips) if all_ips else 'None'}\n"
    )

    print("  📋 Scored Adapter Candidates:")
    print(f"  {'ADAPTER':<20} {'IP':<18} {'STATUS/SCORE'}")
    print("  " + "-" * 50)
    for score, adapter, ip in candidates:
        status = f"✅ Score: {score}" if score > 0 else f"❌ Filtered ({score})"
        print(f"  {adapter:<20} {ip:<18} {status}")
    print()


def _cmd_open(args: argparse.Namespace) -> None:
    import webbrowser

    print(f"\n🔍 正在局域网解析服务: {args.name} ...")
    service = MDNSResolver.resolve(args.name, protocol=args.type, timeout=args.timeout)
    if service:
        print(f"✅ 找到服务: {service.url}/  (IP: {service.ip}:{service.port})")
        print(f"🌐 正在默认浏览器中打开: {service.url}/\n")
        webbrowser.open(service.url)
    else:
        print(f"❌ 未在局域网中发现活跃服务: {args.name}\n")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yyds-mdns",
        description="yyds-mdns: Zero-config mDNS service broadcaster & discovery CLI.",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Broadcast an mDNS service")
    run_parser.add_argument(
        "--name", "-n", required=True, help="Service/host name (e.g. 'my-api')"
    )
    run_parser.add_argument(
        "--port", "-p", required=True, type=int, help="Service port (e.g. 8000)"
    )
    run_parser.add_argument(
        "--ip", "-i", default=None, help="Explicit IPv4 binding (optional)"
    )
    run_parser.add_argument(
        "--interface",
        "-I",
        default=None,
        help="Explicit network interface binding (e.g. 'eno1')",
    )
    run_parser.add_argument(
        "--type", "-t", default="_http._tcp.local.", help="DNS-SD service type"
    )

    # Command: scan / discover
    scan_parser = subparsers.add_parser("scan", help="Scan LAN for mDNS services")
    scan_parser.add_argument(
        "--type", "-t", default="_http._tcp.local.", help="DNS-SD service type"
    )
    scan_parser.add_argument(
        "--timeout", type=float, default=2.5, help="Scan timeout in seconds"
    )

    # Command: resolve
    res_parser = subparsers.add_parser(
        "resolve", help="Resolve a specific .local service"
    )
    res_parser.add_argument("name", help="Service name (e.g. 'my-api')")
    res_parser.add_argument(
        "--timeout", type=float, default=2.5, help="Resolution timeout in seconds"
    )

    # Command: open
    open_parser = subparsers.add_parser(
        "open", help="Resolve a .local service and open in browser"
    )
    open_parser.add_argument("name", help="Service name (e.g. 'uo-yskj')")
    open_parser.add_argument(
        "--type", "-t", default="_http._tcp.local.", help="DNS-SD service type"
    )
    open_parser.add_argument(
        "--timeout", type=float, default=2.5, help="Resolution timeout in seconds"
    )

    # Command: ip
    subparsers.add_parser("ip", help="Show detected LAN IP information")

    return parser


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    if args.command == "run":
        _cmd_run(args)
    elif args.command == "scan":
        _cmd_scan(args)
    elif args.command == "resolve":
        _cmd_resolve(args)
    elif args.command == "open":
        _cmd_open(args)
    elif args.command == "ip":
        _cmd_ip(args)
    else:
        parser.print_help(sys.stderr)


if __name__ == "__main__":
    main()
