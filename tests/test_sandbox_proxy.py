"""Tests for the sandbox MITM proxy (scripts/sandbox/proxy.py)."""

from __future__ import annotations

import pathlib
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROXY_PY = REPO_ROOT / "scripts" / "sandbox" / "proxy.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_proxy_tunnels_raw_tcp_for_non_fixture_host(tmp_path: Path) -> None:
    """Non-fixture hosts must pass through as raw TCP tunnels without TLS interception."""
    # 1. Start a simple upstream echo server
    upstream_port = _free_port()
    upstream_received = []

    def upstream_server():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", upstream_port))
            s.listen(1)
            conn, _ = s.accept()
            with conn:
                data = conn.recv(1024)
                upstream_received.append(data)
                conn.sendall(b"ECHO:" + data)

    upstream_thread = threading.Thread(target=upstream_server, daemon=True)
    upstream_thread.start()

    # 2. Start proxy
    proxy_port = _free_port()
    fixture_root = tmp_path / "http"
    certs_dir = tmp_path / "certs"
    real_ca = tmp_path / "ca.pem"
    fixture_root.mkdir()
    certs_dir.mkdir()
    real_ca.touch()

    # Mock LISTEN_ADDRESS in proxy
    proxy_script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'sandbox')!r})
import proxy
proxy.LISTEN_ADDRESS = ('127.0.0.1', {proxy_port})
proxy.ROOT = {str(fixture_root)!r}
proxy.CERTS = {str(certs_dir)!r}
proxy.REAL_CA = {str(real_ca)!r}
proxy.main()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", proxy_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for proxy to come up
        time.sleep(0.5)

        # 3. Connect to proxy and send CONNECT request for 127.0.0.1:<upstream_port>
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(("127.0.0.1", proxy_port))
            client.sendall(f"CONNECT 127.0.0.1:{upstream_port} HTTP/1.1\r\nHost: 127.0.0.1:{upstream_port}\r\n\r\n".encode())
            
            # Read HTTP 200 response
            resp = client.recv(1024)
            assert b"200 Connection Established" in resp

            # Send payload over tunnel
            client.sendall(b"hello raw tunnel")
            reply = client.recv(1024)
            assert reply == b"ECHO:hello raw tunnel"

        upstream_thread.join(timeout=2)
        assert upstream_received == [b"hello raw tunnel"]
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def test_proxy_serves_http_fixture(tmp_path: Path) -> None:
    """Fixture files under ROOT/<host>/<path> must be served directly."""
    proxy_port = _free_port()
    fixture_root = tmp_path / "http"
    certs_dir = tmp_path / "certs"
    real_ca = tmp_path / "ca.pem"
    host_dir = fixture_root / "hermes-agent.nousresearch.com"
    host_dir.mkdir(parents=True)
    certs_dir.mkdir()
    real_ca.touch()

    (host_dir / "install.sh").write_text("#!/bin/sh\necho 'mock install'\n", encoding="utf-8")

    proxy_script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'sandbox')!r})
import proxy
proxy.LISTEN_ADDRESS = ('127.0.0.1', {proxy_port})
proxy.ROOT = {str(fixture_root)!r}
proxy.CERTS = {str(certs_dir)!r}
proxy.REAL_CA = {str(real_ca)!r}
proxy.main()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", proxy_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        time.sleep(0.5)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(("127.0.0.1", proxy_port))
            req = b"GET http://hermes-agent.nousresearch.com/install.sh HTTP/1.1\r\nHost: hermes-agent.nousresearch.com\r\n\r\n"
            client.sendall(req)
            resp = client.recv(4096)
            assert b"200 OK" in resp
            assert b"mock install" in resp
    finally:
        proc.terminate()
        proc.wait(timeout=2)

