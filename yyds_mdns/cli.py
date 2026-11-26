# -*- coding:utf-8 -*-

"""
CLI entry point for yyds-mdns.
"""

import argparse
import signal
import sys
import threading
from typing import Dict, List, Optional

from yyds_mdns.__version__ import __version__
from yyds_mdns.core.engine import MDNSEngine, _safe_print
from yyds_mdns.core.exceptions import MDNSServiceConflictError, YYDSMDNSError
from yyds_mdns.core.net_utils import (
    get_adapter_candidates,
    get_all_local_ips,
    get_lan_ip,
)
from yyds_mdns.core.resolver import MDNSResolver
from yyds_mdns.core.service import MDNSServiceConfig


def _format_ip_port(ip: object, port: object) -> str:
    ip_str = str(ip) if ip else "N/A"
    if ":" in ip_str and not ip_str.startswith("["):
        ip_str = f"[{ip_str}]"
    return f"{ip_str}:{port}" if port is not None else ip_str


def _parse_cli_props(raw_props: Optional[List[str]]) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for item in raw_props or []:
        if "=" not in item:
            raise YYDSMDNSError(f"Invalid --prop '{item}': expected KEY=VALUE format.")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise YYDSMDNSError(f"Invalid --prop '{item}': key cannot be empty.")
        props[k] = v
    return props


def _cmd_run(args: argparse.Namespace) -> None:
    props = _parse_cli_props(getattr(args, "prop", None))
    config = MDNSServiceConfig(
        name=args.name,
        port=args.port,
        ip=args.ip,
        interface=args.interface,
        protocol=args.type,
        properties=props or None,
        unique=getattr(args, "unique", False),
    )
    allow_name_change = getattr(args, "allow_name_change", False)
    engine = MDNSEngine(
        config,
        use_worker_lock=False,
        verbose=False,
        allow_name_change=allow_name_change,
    )

    stop_event = threading.Event()
    is_main_thread = threading.current_thread() is threading.main_thread()
    prev_sigint = signal.getsignal(signal.SIGINT) if is_main_thread else None
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main_thread else None

    def _signal_handler(sig, frame):
        stop_event.set()

    if is_main_thread:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    try:
        engine.start()
    except BaseException:
        if is_main_thread:
            signal.signal(signal.SIGINT, prev_sigint)
            signal.signal(signal.SIGTERM, prev_sigterm)
        raise

    if not stop_event.is_set():
        _safe_print("\n" + "=" * 55)
        _safe_print(
            "  🚀 YYDS-mDNS Local Service Broadcaster",
            fallback="  [YYDS-mDNS] Local Service Broadcaster",
        )
        _safe_print("=" * 55)
        _safe_print(
            f"  • Service Name : {config.name}",
            fallback=f"  * Service Name : {config.name}",
        )
        _safe_print(
            f"  • Local Domain : {config.host_name}",
            fallback=f"  * Local Domain : {config.host_name}",
        )
        if config.device_suffix:
            _safe_print(
                f"  • Device Suffix: {config.device_suffix}",
                fallback=f"  * Device Suffix: {config.device_suffix}",
            )
        _safe_print(
            f"  • LAN IP       : {config.ip}",
            fallback=f"  * LAN IP       : {config.ip}",
        )
        if config.interface:
            _safe_print(
                f"  • Interface    : {config.interface}",
                fallback=f"  * Interface    : {config.interface}",
            )
        _safe_print(
            f"  • Port         : {config.port}",
            fallback=f"  * Port         : {config.port}",
        )
        _safe_print(
            f"  • Access URL   : {config.url}",
            fallback=f"  * Access URL   : {config.url}",
        )
        _safe_print(
            f"  • DNS-SD Type  : {config.protocol}",
            fallback=f"  * DNS-SD Type  : {config.protocol}",
        )
        _safe_print("=" * 55)
        _safe_print("  Broadcasting... Press Ctrl+C to stop.")
        _safe_print("=" * 55 + "\n")

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=3600)
    except KeyboardInterrupt:
        pass
    finally:
        if is_main_thread:
            signal.signal(signal.SIGINT, prev_sigint)
            signal.signal(signal.SIGTERM, prev_sigterm)
        _safe_print(
            "\n\n  👋 Stopping mDNS broadcast (sending Goodbye packet)...",
            fallback="\n\n  [mDNS] Stopping mDNS broadcast (sending Goodbye packet)...",
        )
        engine.stop()
        _safe_print(
            "  ✅ Gracefully stopped. Bye!\n",
            fallback="  [OK] Gracefully stopped. Bye!\n",
        )


def _cmd_scan(args: argparse.Namespace) -> None:
    stype = args.type or "_http._tcp.local."
    timeout = args.timeout if args.timeout is not None else 2.5
    _safe_print(
        f"\n🔍 Scanning LAN for mDNS services (type: {stype}, timeout: {timeout}s)...",
        fallback=f"\n[Scan] Scanning LAN for mDNS services (type: {stype}, timeout: {timeout}s)...",
    )
    services = MDNSResolver.discover(service_type=stype, timeout=timeout)

    if not services:
        _safe_print(
            "❌ No mDNS services discovered.\n",
            fallback="[!] No mDNS services discovered.\n",
        )
        return

    _safe_print(
        f"\n✅ Found {len(services)} active service(s):\n",
        fallback=f"\n[OK] Found {len(services)} active service(s):\n",
    )
    _safe_print(f"{'NAME':<30} {'IP:PORT':<22} {'URL'}")
    _safe_print("-" * 80)
    for s in services:
        raw_name = s["name"]
        svc_type = s.get("type") or ""
        short_name = (
            raw_name[: -(len(svc_type) + 1)]
            if svc_type and raw_name.endswith(f".{svc_type}")
            else raw_name
        )
        url = s.get("url") or "N/A"
        addr = _format_ip_port(s.get("ip"), s.get("port"))
        _safe_print(f"{short_name:<30} {addr:<22} {url}")
    _safe_print("")


def _cmd_resolve(args: argparse.Namespace) -> None:
    name = args.name
    stype = getattr(args, "type", None) or "_http._tcp.local."
    timeout = args.timeout if args.timeout is not None else 2.5
    _safe_print(
        f"\n🔍 Resolving '{name}' on LAN (type: {stype}, timeout: {timeout}s)...",
        fallback=f"\n[Resolve] Resolving '{name}' on LAN (type: {stype}, timeout: {timeout}s)...",
    )
    res = MDNSResolver.resolve(name=name, service_type=stype, timeout=timeout)

    if not res:
        _safe_print(
            f"❌ Failed to resolve '{name}'.\n",
            fallback=f"[!] Failed to resolve '{name}'.\n",
        )
        sys.exit(1)

    _safe_print("\n✅ Successfully resolved:", fallback="\n[OK] Successfully resolved:")
    _safe_print(f"  • Name    : {res['name']}", fallback=f"  * Name    : {res['name']}")
    _safe_print(
        f"  • Host    : {res['server']}", fallback=f"  * Host    : {res['server']}"
    )
    _safe_print(
        f"  • IP      : {res.get('ip') or 'N/A'}",
        fallback=f"  * IP      : {res.get('ip') or 'N/A'}",
    )
    if res.get("port") is not None:
        _safe_print(
            f"  • Port    : {res['port']}", fallback=f"  * Port    : {res['port']}"
        )
    if res.get("url"):
        _safe_print(
            f"  • URL     : {res['url']}", fallback=f"  * URL     : {res['url']}"
        )
    if res.get("properties"):
        _safe_print(
            f"  • TXT Props: {res['properties']}",
            fallback=f"  * TXT Props: {res['properties']}",
        )
    _safe_print("")


def _cmd_ip(args: argparse.Namespace) -> None:
    preferred = get_lan_ip()
    all_ips = get_all_local_ips()
    candidates = get_adapter_candidates()

    _safe_print(
        "\n🌐 Network Interfaces & LAN IP Info:",
        fallback="\n[Network] Interfaces & LAN IP Info:",
    )
    _safe_print(
        f"  • Primary LAN IP (Auto Selected) : {preferred}",
        fallback=f"  * Primary LAN IP (Auto Selected) : {preferred}",
    )
    _safe_print(
        f"  • Filtered LAN IP(s)             : {', '.join(all_ips) if all_ips else 'None'}\n",
        fallback=f"  * Filtered LAN IP(s)             : {', '.join(all_ips) if all_ips else 'None'}\n",
    )

    _safe_print(
        "  📋 Scored Adapter Candidates:", fallback="  Scored Adapter Candidates:"
    )
    _safe_print(f"  {'ADAPTER':<24} {'IP':<18} {'STATUS/SCORE'}")
    _safe_print("  " + "-" * 54)
    for score, adapter, ip in candidates:
        status = f"✅ Score: {score}" if score > 0 else f"❌ Filtered ({score})"
        status_fb = f"[OK] Score: {score}" if score > 0 else f"[Filtered] ({score})"
        _safe_print(
            f"  {adapter:<24} {ip:<18} {status}",
            fallback=f"  {adapter:<24} {ip:<18} {status_fb}",
        )
    _safe_print("")


def _cmd_open(args: argparse.Namespace) -> None:
    import webbrowser

    stype = args.type or "_http._tcp.local."
    timeout = args.timeout if args.timeout is not None else 2.5
    _safe_print(
        f"\n🔍 正在局域网解析服务: {args.name} ...",
        fallback=f"\n[Open] Resolving LAN service: {args.name} ...",
    )
    service = MDNSResolver.resolve(
        args.name,
        service_type=stype,
        timeout=timeout,
    )
    if service:
        url = service.get("url")
        addr = _format_ip_port(service.get("ip"), service.get("port"))
        if not url and service.get("ip") and service.get("port"):
            scheme = "https" if "https" in stype else "http"
            url = f"{scheme}://{addr}"

        if not url:
            _safe_print(
                f"❌ 服务 '{args.name}' 未包含有效访问地址。\n",
                fallback=f"[!] Service '{args.name}' resolved without a valid URL.\n",
            )
            sys.exit(1)

        _safe_print(
            f"✅ 找到服务: {url}/  (IP: {addr})",
            fallback=f"[OK] Found service: {url}/  (IP: {addr})",
        )
        _safe_print(
            f"🌐 正在默认浏览器中打开: {url}/\n",
            fallback=f"[Open] Opening in default browser: {url}/\n",
        )
        webbrowser.open(url)
    else:
        _safe_print(
            f"❌ 未在局域网中发现活跃服务: {args.name}\n",
            fallback=f"[!] Service not found in LAN: {args.name}\n",
        )
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
    run_parser.add_argument(
        "--unique",
        "-u",
        action="store_true",
        default=False,
        help="Append hardware MAC suffix to hostname to prevent LAN conflicts (e.g. 'my-api-a1b2.local')",
    )
    run_parser.add_argument(
        "--allow-name-change",
        action="store_true",
        default=False,
        help="Automatically rename service (e.g. 'my-api-2') if name already exists in LAN",
    )
    run_parser.add_argument(
        "--prop",
        "-P",
        action="append",
        default=None,
        help="Attach TXT metadata KEY=VALUE (repeatable, e.g. -P version=1.0 -P env=prod)",
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
        "resolve", help="Resolve a specific .local service or host"
    )
    res_parser.add_argument("name", help="Service or host name (e.g. 'my-api')")
    res_parser.add_argument(
        "--type", "-t", default="_http._tcp.local.", help="DNS-SD service type"
    )
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
    try:
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
            sys.exit(1)
    except MDNSServiceConflictError as err:
        _safe_print(
            f"\n❌ Conflict Error: {err}\n"
            "💡 Tip: Re-run with `--unique` (`-u`) or `--allow-name-change` to avoid LAN collisions.\n",
            fallback=(
                f"\n[Conflict Error] {err}\n"
                "Tip: Re-run with --unique (-u) or --allow-name-change.\n"
            ),
        )
        sys.exit(1)
    except (YYDSMDNSError, ValueError) as err:
        _safe_print(
            f"\n❌ Error: {err}\n",
            fallback=f"\n[Error] {err}\n",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
