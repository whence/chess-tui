"""Tests for the network helpers in chess_tui.host."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chess_tui.host import describe_listen, get_real_ip


# ---- get_real_ip ----------------------------------------------------------


def test_get_real_ip_returns_a_string() -> None:
    """The function always returns a string (never raises)."""
    ip = get_real_ip()
    assert isinstance(ip, str)
    assert ip  # not empty


def test_get_real_ip_falls_back_to_loopback_on_error() -> None:
    """If the UDP-socket trick fails (offline, no route, etc.), we get
    ``127.0.0.1`` so the caller can still render a meaningful URL."""
    with patch("socket.socket") as mock_socket:
        mock_socket.side_effect = OSError("no network")
        assert get_real_ip() == "127.0.0.1"


def test_get_real_ip_swallows_socket_attribute_errors() -> None:
    """Some sandboxed environments refuse ``connect()``; we still return a string."""
    fake_socket = type(
        "FakeSocket",
        (),
        {
            "connect": lambda self, *a, **kw: (_ for _ in ()).throw(
                PermissionError("not allowed")
            ),
            "getsockname": lambda self: ("127.0.0.1", 0),
            "close": lambda self: None,
            "settimeout": lambda self, *a, **kw: None,
        },
    )()
    with patch("socket.socket", return_value=fake_socket):
        ip = get_real_ip()
    # On PermissionError we should still get *something* (the loopback fallback).
    assert isinstance(ip, str)
    assert ip


# ---- describe_listen ------------------------------------------------------


def test_describe_listen_for_all_interfaces_includes_lan_address() -> None:
    """When bound to 0.0.0.0, the banner line must include the discovered
    LAN address so users on other machines can reach the server."""
    with patch("chess_tui.host.get_real_ip", return_value="192.168.1.42"):
        out = describe_listen("0.0.0.0", 8080)
    assert "0.0.0.0:8080" in out
    assert "192.168.1.42:8080" in out
    assert "all interfaces" in out


def test_describe_listen_for_localhost_does_not_mention_lan() -> None:
    """When bound to 127.0.0.1, no LAN address is shown — the server is
    intentionally not reachable from other machines."""
    out = describe_listen("127.0.0.1", 9000)
    assert out == "http://127.0.0.1:9000"
    assert "all interfaces" not in out
    # The fake get_real_ip should NOT have been consulted for localhost.
    with patch("chess_tui.host.get_real_ip") as mock_ip:
        describe_listen("127.0.0.1", 9000)
        mock_ip.assert_not_called()


def test_describe_listen_for_explicit_external_ip() -> None:
    """A specific non-loopback host is rendered as-is."""
    out = describe_listen("10.0.0.5", 1234)
    assert out == "http://10.0.0.5:1234"
    assert "all interfaces" not in out
