# -*- coding:utf-8 -*-

import io
import os
import signal
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from yyds_mdns.cli import (
    _cmd_ip,
    _cmd_open,
    _cmd_resolve,
    _cmd_run,
    _cmd_scan,
    build_parser,
)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_run_args(self):
        args = self.parser.parse_args(["run", "--name", "my-api", "--port", "8000"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.name, "my-api")
        self.assertEqual(args.port, 8000)
        self.assertIsNone(args.ip)
        self.assertIsNone(args.interface)
        self.assertEqual(args.type, "_http._tcp.local.")
        self.assertFalse(args.unique)

    def test_run_args_unique(self):
        args1 = self.parser.parse_args(["run", "-n", "app", "-p", "8000", "--unique"])
        self.assertTrue(args1.unique)

        args2 = self.parser.parse_args(["run", "-n", "app", "-p", "8000", "-u"])
        self.assertTrue(args2.unique)

    def test_run_args_with_custom_ip_and_type(self):
        args = self.parser.parse_args(
            [
                "run",
                "-n",
                "custom-api",
                "-p",
                "9000",
                "-i",
                "192.168.1.100",
                "-I",
                "eno1",
                "-t",
                "https",
            ]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.name, "custom-api")
        self.assertEqual(args.port, 9000)
        self.assertEqual(args.ip, "192.168.1.100")
        self.assertEqual(args.interface, "eno1")
        self.assertEqual(args.type, "https")

    def test_scan_args(self):
        args = self.parser.parse_args(["scan", "--timeout", "3.0"])
        self.assertEqual(args.command, "scan")
        self.assertEqual(args.timeout, 3.0)

    def test_resolve_args_and_type(self):
        args = self.parser.parse_args(
            ["resolve", "printer.local", "-t", "_https._tcp.local.", "--timeout", "1.5"]
        )
        self.assertEqual(args.command, "resolve")
        self.assertEqual(args.name, "printer.local")
        self.assertEqual(args.type, "_https._tcp.local.")
        self.assertEqual(args.timeout, 1.5)

    def test_open_args(self):
        args = self.parser.parse_args(["open", "my-service", "--timeout", "3.0"])
        self.assertEqual(args.command, "open")
        self.assertEqual(args.name, "my-service")
        self.assertEqual(args.timeout, 3.0)

    def test_ip_args(self):
        args = self.parser.parse_args(["ip"])
        self.assertEqual(args.command, "ip")

    def test_cmd_open_executes_cleanly_with_dict_result(self):
        fake_res = {
            "name": "my-service._http._tcp.local.",
            "server": "my-service.local",
            "ip": "192.168.1.25",
            "port": 8080,
            "url": "http://my-service.local:8080",
            "properties": {},
        }
        args = self.parser.parse_args(
            ["open", "my-service", "-t", "_http._tcp.local.", "--timeout", "1.0"]
        )
        buf = io.StringIO()
        with patch(
            "yyds_mdns.cli.MDNSResolver.resolve", return_value=fake_res
        ) as mock_resolve, patch("webbrowser.open") as mock_open, patch(
            "sys.stdout", buf
        ):
            _cmd_open(args)
            mock_resolve.assert_called_once_with(
                "my-service",
                service_type="_http._tcp.local.",
                timeout=1.0,
            )
            mock_open.assert_called_once_with("http://my-service.local:8080")

    def test_cmd_open_missing_exits_nonzero(self):
        args = self.parser.parse_args(["open", "nonexistent", "--timeout", "0.2"])
        with patch("yyds_mdns.cli.MDNSResolver.resolve", return_value=None), patch(
            "sys.stdout", io.StringIO()
        ):
            with self.assertRaises(SystemExit) as ctx:
                _cmd_open(args)
            self.assertEqual(ctx.exception.code, 1)

    def test_cmd_scan_resolve_ip_execution(self):
        fake_svc = {
            "name": "very-long-microservice-instance-name._http._tcp.local.",
            "type": "_http._tcp.local.",
            "server": "very-long-microservice-instance-name.local",
            "ip": "192.168.1.10",
            "port": 18080,
            "url": "http://very-long-microservice-instance-name.local:18080",
            "properties": {"env": "dev"},
        }
        buf = io.StringIO()
        with patch(
            "yyds_mdns.cli.MDNSResolver.discover", return_value=[fake_svc]
        ), patch("yyds_mdns.cli.MDNSResolver.resolve", return_value=fake_svc), patch(
            "sys.stdout", buf
        ):
            _cmd_scan(self.parser.parse_args(["scan", "--timeout", "0.2"]))
            _cmd_resolve(
                self.parser.parse_args(
                    ["resolve", "svc", "-t", "http", "--timeout", "0.2"]
                )
            )
            _cmd_ip(self.parser.parse_args(["ip"]))

        # Scan output must NOT truncate :18080 port on long URLs!
        self.assertIn(
            "http://very-long-microservice-instance-name.local:18080", buf.getvalue()
        )

        # Failed resolve must exit with code 1
        with patch("yyds_mdns.cli.MDNSResolver.resolve", return_value=None), patch(
            "sys.stdout", io.StringIO()
        ):
            with self.assertRaises(SystemExit) as ctx:
                _cmd_resolve(
                    self.parser.parse_args(["resolve", "missing", "--timeout", "0.1"])
                )
            self.assertEqual(ctx.exception.code, 1)

    def test_cli_props_and_conflict_error_handling(self):
        from yyds_mdns.cli import main
        from yyds_mdns.core.exceptions import MDNSServiceConflictError

        args = self.parser.parse_args(
            [
                "run",
                "-n",
                "app",
                "-p",
                "8000",
                "--allow-name-change",
                "-P",
                "version=1.2",
                "-P",
                "role=edge",
            ]
        )
        self.assertTrue(args.allow_name_change)
        self.assertEqual(args.prop, ["version=1.2", "role=edge"])

        buf = io.StringIO()
        with patch("sys.argv", ["yyds-mdns", "run", "-n", "dup", "-p", "8000"]), patch(
            "yyds_mdns.cli._cmd_run",
            side_effect=MDNSServiceConflictError("dup already exists"),
        ), patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as ctx:
                main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("--unique", buf.getvalue())

    def test_cmd_run_graceful_single_stop_on_sigterm(self):
        args = self.parser.parse_args(
            ["run", "-n", "cli-test", "-p", "18099", "-i", "127.0.0.1"]
        )
        mock_engine = MagicMock()
        buf = io.StringIO()

        def trigger_sigterm_soon():
            time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGTERM)

        with patch("yyds_mdns.cli.MDNSEngine", return_value=mock_engine), patch(
            "sys.stdout", buf
        ):
            t = threading.Thread(target=trigger_sigterm_soon, daemon=True)
            t.start()
            _cmd_run(args)
            t.join(timeout=1.0)

        # Must start once and stop ONCE (never twice on signal!)
        mock_engine.start.assert_called_once()
        mock_engine.stop.assert_called_once()
        self.assertEqual(buf.getvalue().count("Gracefully stopped"), 1)


if __name__ == "__main__":
    unittest.main()
