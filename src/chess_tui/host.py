"""Network helpers for the chess-tui servers.

Used by ``chess-tui-engine``, ``chess-tui-nova``, ``chess-tui-maia`` and
``chess-tui-net`` to discover the local IP and format a human-friendly
listen banner.

The pattern is borrowed from ``~/projects/ftp/server.py``: open a UDP
socket against a public address (which makes the OS pick the outgoing
interface), read back the local IP from the socket's sockname, then
close the socket. No packets are actually sent.
"""

from __future__ import annotations

import socket


def get_real_ip() -> str:
    """Discover the local IP address that would be used to reach the
    public internet.

    Returns ``"127.0.0.1"`` if no network is reachable (offline, DNS
    failure, etc.). The function is intentionally non-fatal: callers can
    always fall back to loopback.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.1)
            # Connecting a UDP socket doesn't send any traffic; it just
            # makes the kernel record which interface would be used.
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def describe_listen(host: str, port: int) -> str:
    """Return a one-line, human-friendly description of where the
    server is listening.

    When the server is bound to all interfaces (``0.0.0.0``) the line
    also includes the discovered LAN address so users on other machines
    can reach it. For a specific host (e.g. ``127.0.0.1``) the line just
    says the URL.
    """
    if host in ("0.0.0.0", ""):
        real_ip = get_real_ip()
        return (
            f"http://{host}:{port} (all interfaces; "
            f"LAN: http://{real_ip}:{port})"
        )
    return f"http://{host}:{port}"
