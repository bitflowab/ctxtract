#!/usr/bin/env python3
"""Attacktestsvit for kompressorns SSRF-skydd.

Tacker granskningens tre kritiska hoal (27/10):
  1. Omdirigeringar valideras per hopp
  2. DNS-rebinding: anslutning sker till det validerade IP:t (pinnat)
  3. CGNAT 100.64.0.0/10 blockeras
Plus regression: schema-sparr, filrots-sparr, normal hantering.
"""
import http.server
import ipaddress
import os
import socket
import threading
import unittest
from unittest import mock

import compressor


class _Handler(http.server.BaseHTTPRequestHandler):
    """Lokal server: /ok -> sida, /redirect -> 302 till mal, /loop -> 302 till sig sjalv."""

    target = "http://169.254.169.254/latest/meta-data/"

    def do_GET(self):
        if self.path == "/ok":
            body = b"<html><body><p>MARKERAR_TEXT_OK smarta kontrakt</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.target)
            self.end_headers()
        elif self.path == "/redirect-ok":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class _Server:
    def __enter__(self):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _allow_localhost_only():
    """Patch: tillat 127.0.0.1 (testservern) men anvand riktig logik for ovriga."""
    real = compressor._ip_is_blocked

    def fake(ip):
        if str(ip) == "127.0.0.1":
            return False
        return real(ip)

    return mock.patch.object(compressor, "_ip_is_blocked", fake)


class TestSchemeBlock(unittest.TestCase):
    def test_file_scheme_blocked(self):
        with self.assertRaises(ValueError):
            compressor.fetch_url("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        with self.assertRaises(ValueError):
            compressor.fetch_url("ftp://example.com/x")


class TestPrivateHostsBlocked(unittest.TestCase):
    """Kritiskt 3 + grundskydd: alla icke-publika omraden."""

    def test_blocked_hosts(self):
        for url in (
            "http://127.0.0.1/",
            "http://localhost/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # molnmetadata
            "http://100.64.0.1/",                          # CGNAT (kritiskt 3)
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fd00::1]/",
            "http://[::ffff:127.0.0.1]/",                  # IPv4-mappad loopback
        ):
            with self.assertRaises(ValueError, msg=url):
                compressor.fetch_url(url)

    def test_dns_rebind_names_blocked(self):
        # localhost gar att sla upp och ska blockeras via uppslaget
        self.assertTrue(compressor._is_private_host("localhost"))

    def test_public_ok(self):
        self.assertFalse(compressor._is_private_host("8.8.8.8"))


class TestRedirectValidation(unittest.TestCase):
    """Kritiskt 1: varje omdirigerings-hopp valideras."""

    def test_redirect_to_metadata_blocked(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            _Handler.target = "http://169.254.169.254/latest/meta-data/"
            with _allow_localhost_only():
                with self.assertRaises(ValueError):
                    compressor.fetch_url("http://127.0.0.1:%d/redirect" % srv.port)

    def test_redirect_to_file_scheme_blocked(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            _Handler.target = "file:///etc/passwd"
            with _allow_localhost_only():
                with self.assertRaises(ValueError):
                    compressor.fetch_url("http://127.0.0.1:%d/redirect" % srv.port)

    def test_redirect_to_public_followed(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            _Handler.target = "/ok"
            with _allow_localhost_only():
                text = compressor.fetch_url("http://127.0.0.1:%d/redirect-ok" % srv.port)
        self.assertIn("MARKERAR_TEXT_OK", text)

    def test_redirect_loop_capped(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            _Handler.target = "/redirect-ok"  # /redirect-ok pekar pa /ok – ingen loop
            # bygg en rakta loop: /redirect -> /redirect
            _Handler.target = "http://127.0.0.1:%d/redirect" % srv.port
            with _allow_localhost_only():
                with self.assertRaises(ValueError):
                    compressor.fetch_url("http://127.0.0.1:%d/redirect" % srv.port)


class TestDnsPinning(unittest.TestCase):
    """Kritiskt 2: anslutning till det validerade IP:t, ingen ny uppslagning."""

    def test_http_connects_to_pinned_ip(self):
        attempts = []

        def fake_create(addr, *args, **kwargs):
            attempts.append(addr)
            raise OSError("stopp")

        with mock.patch("socket.create_connection", fake_create):
            conn = compressor._PinnedHTTPConnection("example.com", 80, "203.0.113.5")
            with self.assertRaises(OSError):
                conn.connect()
        self.assertEqual(attempts, [("203.0.113.5", 80)])

    def test_https_connects_to_pinned_ip(self):
        attempts = []

        def fake_create(addr, *args, **kwargs):
            attempts.append(addr)
            raise OSError("stopp")

        with mock.patch("socket.create_connection", fake_create):
            conn = compressor._PinnedHTTPSConnection("example.com", 443, "203.0.113.5")
            with self.assertRaises(OSError):
                conn.connect()
        self.assertEqual(attempts, [("203.0.113.5", 443)])

    def test_resolve_and_pin_validates_all(self):
        # 127.0.0.1 ar blockerad -> hela uppslaget ska avvisas
        with mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            with self.assertRaises(ValueError):
                compressor._resolve_and_pin("localhost", 80)


class TestFileRoots(unittest.TestCase):
    def test_etc_passwd_blocked(self):
        with self.assertRaises(ValueError):
            compressor.safe_file_path("/etc/passwd")

    def test_secrets_blocked(self):
        with self.assertRaises(ValueError):
            compressor.safe_file_path(os.path.expanduser("~/.secrets/payment-wallet-key"))

    def test_traversal_blocked(self):
        with self.assertRaises(ValueError):
            compressor.safe_file_path("/tmp/../etc/passwd")

    def test_allowed_root_ok(self):
        self.assertEqual(compressor.safe_file_path("/tmp/testdoc.txt"), "/tmp/testdoc.txt")


class TestRegression(unittest.TestCase):
    def test_fetch_ok_strips_html(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            with _allow_localhost_only():
                text = compressor.fetch_url("http://127.0.0.1:%d/ok" % srv.port)
        self.assertIn("MARKERAR_TEXT_OK", text)
        self.assertNotIn("<p>", text)

    def test_http_error_raises(self):
        with _Server() as srv, mock.patch.object(compressor, "ALLOW_PRIVATE", False):
            with _allow_localhost_only():
                with self.assertRaises(ValueError):
                    compressor.fetch_url("http://127.0.0.1:%d/saknas" % srv.port)


if __name__ == "__main__":
    unittest.main(verbosity=2)
