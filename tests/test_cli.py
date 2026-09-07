# -*- coding:utf-8 -*-

import unittest
from yyds_mdns.cli import build_parser


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

    def test_resolve_args(self):
        args = self.parser.parse_args(["resolve", "printer.local"])
        self.assertEqual(args.command, "resolve")
        self.assertEqual(args.name, "printer.local")

    def test_open_args(self):
        args = self.parser.parse_args(["open", "my-service", "--timeout", "3.0"])
        self.assertEqual(args.command, "open")
        self.assertEqual(args.name, "my-service")
        self.assertEqual(args.timeout, 3.0)

    def test_ip_args(self):
        args = self.parser.parse_args(["ip"])
        self.assertEqual(args.command, "ip")


if __name__ == "__main__":
    unittest.main()
