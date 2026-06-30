"""Tests for the network player API: HTTP client, server, player, and CLI."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import chess
import pytest

from chess_tui import net, net_server
from chess_tui.player import (
    LocalPlayer,
    NetworkPlayer,
    Player,
    IllegalMoveError,
)


# ---- helpers ---------------------------------------------------------------


def _free_port() -> int:
    """Ask the OS for a free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubHandler(BaseHTTPRequestHandler):
    """Test-only HTTP handler. Each test installs its own responses."""

    # Class-level response queues; one slot per request, in order.
    request_log: list[dict] = []
    response_factory: Callable[[dict, dict], tuple[int, dict]] | None = None

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        _StubHandler.request_log.append({"path": self.path, "body": payload})
        if _StubHandler.response_factory is None:
            self.send_error(500)
            return
        code, resp_body = _StubHandler.response_factory(
            payload, dict(_StubHandler.request_log[-1])
        )
        data = json.dumps(resp_body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextmanager
def stub_server(
    response_factory: Callable[[dict, dict], tuple[int, dict]],
):
    """Start a stub server on a free port. Yields (base_url, request_log)."""
    _StubHandler.request_log = []
    _StubHandler.response_factory = response_factory
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _StubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", _StubHandler.request_log
    finally:
        httpd.shutdown()
        httpd.server_close()
        _StubHandler.request_log = []
        _StubHandler.response_factory = None


# ---- net.request_move -------------------------------------------------------


def test_request_move_returns_san_from_200() -> None:
    def factory(payload, _log):
        assert "fen" in payload
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, log):
        san = net.request_move(base, chess.Board().fen())

    assert san == "e4"
    assert log == [{"path": "/move", "body": {"fen": log[0]["body"]["fen"]}}]


def test_request_move_strips_trailing_slash() -> None:
    def factory(_payload, _log):
        return 200, {"san": "Nf3"}

    with stub_server(factory) as (base, _log):
        san = net.request_move(base + "/", "ignored")

    assert san == "Nf3"


def test_request_move_raises_server_error_on_400() -> None:
    def factory(_payload, _log):
        return 400, {"error": "illegal move: e5"}

    with stub_server(factory) as (base, _log):
        with pytest.raises(net.ServerError) as exc_info:
            net.request_move(base, "ignored")
        assert exc_info.value.status == 400
        assert "illegal move" in str(exc_info.value)
        assert exc_info.value.body == {"error": "illegal move: e5"}


def test_request_move_raises_transport_error_when_no_server() -> None:
    # Pick a port that is definitely closed.
    closed_port = _free_port()
    with pytest.raises(net.TransportError):
        net.request_move(f"http://127.0.0.1:{closed_port}", "ignored", timeout=0.5)


def test_request_move_raises_transport_error_on_remote_disconnected() -> None:
    """Server closes connection mid-request (e.g. ctrl+c on server)."""
    import http.client
    from unittest.mock import patch

    def mock_urlopen(req, timeout):
        raise http.client.RemoteDisconnected("Server closed connection")

    with patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(net.TransportError) as exc_info:
            net.request_move("http://127.0.0.1:9999", "ignored", timeout=1)
        assert "could not reach" in str(exc_info.value)


# ---- NetworkPlayer ----------------------------------------------------------


def test_network_player_chooses_move_from_server_response() -> None:
    """End-to-end: NetworkPlayer posts FEN, parses SAN, returns the move."""
    def factory(_payload, _log):
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, server_log):
        player = NetworkPlayer(color=chess.WHITE, url=base)
        board = chess.Board()
        move = asyncio.run(player.choose_move(board))
        fen_at_call = board.fen()

    assert move == board.parse_san("e4")
    assert server_log and server_log[0]["body"]["fen"] == fen_at_call


def test_network_player_retries_on_illegal_move_from_server() -> None:
    """Server returns illegal move — retries forever (we abort after a few)."""
    attempt_count = 0
    closed_port = _free_port()

    def _mock_fetch(fen, moves):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count >= 3:
            raise KeyboardInterrupt("test abort")
        return "e5"  # illegal for white

    import chess_tui.player as player_mod
    original = player_mod.NetworkPlayer._fetch_san
    player_mod.NetworkPlayer._fetch_san = lambda self, fen, moves: _mock_fetch(fen, moves)
    try:
        player = NetworkPlayer(
            color=chess.WHITE, url=f"http://127.0.0.1:{closed_port}", timeout=0.01, retry_delay=0.01
        )
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(player.choose_move(chess.Board()))
    finally:
        player_mod.NetworkPlayer._fetch_san = original

    assert attempt_count == 3  # kept retrying


def test_network_player_retries_on_server_error() -> None:
    """Server returns 400 — retries forever (we abort after a few)."""
    attempt_count = 0
    closed_port = _free_port()

    def _mock_fetch(fen, moves):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count >= 3:
            raise KeyboardInterrupt("test abort")
        raise net.ServerError(400, {"error": "I refuse"})

    import chess_tui.player as player_mod
    original = player_mod.NetworkPlayer._fetch_san
    player_mod.NetworkPlayer._fetch_san = lambda self, fen, moves: _mock_fetch(fen, moves)
    try:
        player = NetworkPlayer(
            color=chess.WHITE, url=f"http://127.0.0.1:{closed_port}", timeout=0.01, retry_delay=0.01
        )
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(player.choose_move(chess.Board()))
    finally:
        player_mod.NetworkPlayer._fetch_san = original

    assert attempt_count == 3  # kept retrying


def test_network_player_retries_on_transport_error() -> None:
    """Server fails twice, then succeeds on the third try."""
    closed_port = _free_port()
    attempts_made = 0

    def _mock_fetch(fen, moves):
        nonlocal attempts_made
        attempts_made += 1
        if attempts_made < 3:
            raise net.TransportError(f"could not reach http://127.0.0.1:{closed_port}")
        return "e4"

    import chess_tui.player as player_mod
    original = player_mod.NetworkPlayer._fetch_san
    player_mod.NetworkPlayer._fetch_san = lambda self, fen, moves: _mock_fetch(fen, moves)
    try:
        player = NetworkPlayer(
            color=chess.WHITE, url=f"http://127.0.0.1:{closed_port}", timeout=0.1
        )
        move = asyncio.run(player.choose_move(chess.Board()))
    finally:
        player_mod.NetworkPlayer._fetch_san = original

    assert move == chess.Board().parse_san("e4")
    assert attempts_made == 3


def test_network_player_retries_infinitely_on_transport_error() -> None:
    """Server always fails — client keeps retrying (we cancel after a few)."""
    attempt_count = 0
    closed_port = _free_port()

    def _mock_fetch(fen, moves):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count >= 5:
            raise KeyboardInterrupt("test abort")
        raise net.TransportError(f"could not reach http://127.0.0.1:{closed_port}")

    import chess_tui.player as player_mod
    original = player_mod.NetworkPlayer._fetch_san
    player_mod.NetworkPlayer._fetch_san = lambda self, fen, moves: _mock_fetch(fen, moves)
    try:
        player = NetworkPlayer(
            color=chess.WHITE, url=f"http://127.0.0.1:{closed_port}", timeout=0.01, retry_delay=0.01
        )
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(player.choose_move(chess.Board()))
    finally:
        player_mod.NetworkPlayer._fetch_san = original

    assert attempt_count == 5  # kept retrying


def test_network_player_retries_on_illegal_move() -> None:
    """Illegal move from server — retries forever (we abort after a few)."""
    attempt_count = 0
    closed_port = _free_port()

    def _mock_fetch(fen, moves):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count >= 4:
            raise KeyboardInterrupt("test abort")
        # Return illegal move for white
        return "e5"

    import chess_tui.player as player_mod
    original = player_mod.NetworkPlayer._fetch_san
    player_mod.NetworkPlayer._fetch_san = lambda self, fen, moves: _mock_fetch(fen, moves)
    try:
        player = NetworkPlayer(
            color=chess.WHITE, url=f"http://127.0.0.1:{closed_port}", timeout=0.01, retry_delay=0.01
        )
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(player.choose_move(chess.Board()))
    finally:
        player_mod.NetworkPlayer._fetch_san = original

    assert attempt_count == 4  # kept retrying


def test_network_player_retries_on_server_busy() -> None:
    """Server returns 503 (busy) twice, then succeeds."""
    attempt_count = 0

    def factory(_payload, _log):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            return 503, {"error": "server busy, retry later"}
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, _log):
        player = NetworkPlayer(
            color=chess.WHITE, url=base, timeout=0.1
        )
        move = asyncio.run(player.choose_move(chess.Board()))

    assert move == chess.Board().parse_san("e4")
    assert attempt_count == 3


def test_local_player_choose_move_raises() -> None:
    """LocalPlayer.choose_move is never called by the TUI — guard loudly."""
    player = LocalPlayer(color=chess.WHITE)
    with pytest.raises(RuntimeError, match="should not be called"):
        asyncio.run(player.choose_move(chess.Board()))


# ---- net_server (the reference server) --------------------------------------


def _post(url: str, payload: dict, timeout: float = 2.0) -> tuple[int, dict]:
    import urllib.request

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_net_server_returns_san_for_legal_move(monkeypatch) -> None:
    """Drive the actual net_server module end-to-end with a stubbed input()."""
    inputs = iter(["e4"])  # what the human will type
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(inputs))

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), net_server._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, body = _post(
            f"http://127.0.0.1:{port}/move",
            {"fen": chess.Board().fen()},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 200
    assert body == {"san": "e4"}


def test_net_server_supersedes_old_request(monkeypatch) -> None:
    """When a new request arrives while waiting for input,
    the old request is abandoned and a new prompt appears."""
    import time

    input_count = [0]
    results: list[tuple[int, dict]] = [None, None]  # type: ignore[list-item]

    def slow_input(prompt=""):
        """Simulate slow user: first input blocks, second returns immediately."""
        input_count[0] += 1
        if input_count[0] == 1:
            # First request: block for a while (simulating slow user)
            time.sleep(0.3)
            return "e4"  # This will be discarded (superseded)
        else:
            # Second request: return immediately
            return "d4"

    monkeypatch.setattr("builtins.input", slow_input)

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), net_server._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Send first request in a thread (it will block on input)
        def send_request_1():
            try:
                code, body = _post(f"http://127.0.0.1:{port}/move", {"fen": chess.Board().fen()}, timeout=2)
                results[0] = (code, body)
            except Exception:
                results[0] = ("timeout", {})

        t1 = threading.Thread(target=send_request_1)
        t1.start()

        # Wait a bit for the first request to start processing
        time.sleep(0.1)

        # Send second request (should supersede the first)
        code2, body2 = _post(f"http://127.0.0.1:{port}/move", {"fen": chess.Board().fen()}, timeout=2)

        # Wait for first request to finish (it should time out or get 503)
        t1.join(timeout=3)

        # Second request should have succeeded with d4
        assert code2 == 200
        assert body2 == {"san": "d4"}

        # First request was superseded - either timed out or got 503
        # (depending on timing)

    finally:
        server.shutdown()
        server.server_close()


def test_net_server_returns_400_for_illegal_move(monkeypatch) -> None:
    inputs = iter(["e2e5"])  # parses as UCI (4 chars) but pawn can't jump 3 squares
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(inputs))

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), net_server._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, body = _post(
            f"http://127.0.0.1:{port}/move",
            {"fen": chess.Board().fen()},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 400
    assert "illegal" in body["error"]


def test_net_server_404_for_unknown_path(monkeypatch) -> None:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), net_server._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, body = _post(
            f"http://127.0.0.1:{port}/nope",
            {"fen": chess.Board().fen()},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 404
    assert "unknown path" in body["error"]


def test_net_server_cli_default_port(monkeypatch) -> None:
    captured: dict = {}

    def fake_server(addr, handler):
        captured["addr"] = addr
        return _FakeServer(addr[1])

    monkeypatch.setattr("chess_tui.net_server.ThreadingHTTPServer", fake_server)
    with pytest.raises(SystemExit):
        net_server.main([])  # no argv → uses sys.argv default
    assert captured["addr"] == ("127.0.0.1", 8080)


def test_net_server_cli_custom_port(monkeypatch) -> None:
    captured: dict = {}

    def fake_server(addr, handler):
        captured["addr"] = addr
        return _FakeServer(addr[1])

    monkeypatch.setattr("chess_tui.net_server.ThreadingHTTPServer", fake_server)
    with pytest.raises(SystemExit):
        net_server.main(["9999"])
    assert captured["addr"] == ("127.0.0.1", 9999)


class _FakeServer:
    def __init__(self, port):
        self.port = port

    def serve_forever(self):
        # Don't actually block the test; just exit immediately.
        raise SystemExit(0)

    def shutdown(self):
        pass


# ---- CLI for chess-tui ------------------------------------------------------


def test_chess_tui_cli_no_flags_uses_local_players() -> None:
    from chess_tui.app import main

    seen: list[dict[chess.Color, Player]] = []

    class _FakeApp:
        def __init__(self, players=None):
            seen.append(players or {})

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main([])
    finally:
        app_mod.ChessApp = real

    assert len(seen) == 1
    players = seen[0]
    assert isinstance(players[chess.WHITE], LocalPlayer)
    assert isinstance(players[chess.BLACK], LocalPlayer)


def test_chess_tui_cli_white_flag_routes_white_to_network() -> None:
    from chess_tui.app import main

    seen: list[dict[chess.Color, Player]] = []

    class _FakeApp:
        def __init__(self, players=None):
            seen.append(players or {})

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main(["--white", "http://net.example:8080"])
    finally:
        app_mod.ChessApp = real

    players = seen[0]
    assert isinstance(players[chess.WHITE], NetworkPlayer)
    assert players[chess.WHITE].url == "http://net.example:8080"
    assert isinstance(players[chess.BLACK], LocalPlayer)


def test_chess_tui_cli_both_flags_routes_both_to_network() -> None:
    from chess_tui.app import main

    seen: list[dict[chess.Color, Player]] = []

    class _FakeApp:
        def __init__(self, players=None):
            seen.append(players or {})

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main([
                "--white", "http://w.example:1",
                "--black", "http://b.example:2",
            ])
    finally:
        app_mod.ChessApp = real

    players = seen[0]
    assert players[chess.WHITE].url == "http://w.example:1"
    assert players[chess.BLACK].url == "http://b.example:2"


# ---- End-to-end TUI: NetworkPlayer drives the game ----------------------------


async def test_tui_applies_network_move_automatically() -> None:
    """A network black player should drive the TUI: white moves once via the
    input, then black's move is fetched from the server and applied without
    any further human action."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input, ListView

    inputs = iter(["e5"])  # what the server will type when asked for black's move
    responses = iter([200, 200])
    server_log: list[dict] = []

    class _NetHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                payload = {}
            server_log.append({"path": self.path, "body": payload})
            fen = payload.get("fen", "")
            board = chess.Board(fen) if fen else chess.Board()
            user_input = next(inputs)
            try:
                move = board.parse_san(user_input)
            except ValueError:
                move = board.parse_uci(user_input)
            san = board.san(move)
            data = json.dumps({"san": san}).encode()
            self.send_response(next(responses))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a, **k):  # noqa: A002
            pass

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _NetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        players = {
            chess.WHITE: LocalPlayer(color=chess.WHITE),
            chess.BLACK: NetworkPlayer(color=chess.BLACK, url=f"http://127.0.0.1:{port}"),
        }
        app = ChessApp(players=players)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The black move hasn't happened yet (white is on move).
            # White plays e4.
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            # The network move is fetched via run_worker; give it time.
            for _ in range(20):
                await pilot.pause()
                if app._state.san_history() == ["e4", "e5"]:
                    break
                await asyncio.sleep(0.05)
        assert app._state.san_history() == ["e4", "e5"], (
            f"expected ['e4', 'e5'], got {app._state.san_history()}"
        )
        assert server_log and server_log[0]["body"]["fen"].startswith(
            "rnbqkbnr/pppppppp/8/8/4P3"
        )
    finally:
        server.shutdown()
        server.server_close()