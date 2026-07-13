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


def test_request_move_passes_moves_list_when_opening_fast_forwarded() -> None:
    """``--opening B90`` fast-forwards the B90 line into the starting
    position.  When the TUI then asks a network player (e.g.
    ``chess-tui-maia --use-history``) for a move, the wire body MUST
    include the full SAN list — otherwise the server falls back to a
    no-context engine call and the transformer's history window is
    empty.

    This is a wire-level lock-in: a future refactor of
    :func:`net.request_move` that drops the ``moves`` kwarg (or of
    ``BoardState.from_pgn`` that fails to populate the SAN stack) would
    silently break Maia's history feature.  This test fails loudly
    instead.
    """
    from chess_tui.openings import find
    from chess_tui.state import BoardState

    # ``resolve("B90")`` is now ambiguous (15 B90 entries) so we
    # use ``find`` and pick the parent (the B90 root, which is the
    # canonical 5-ply Najdorf with ...a6).  This is the same row
    # that ``--opening B90`` would have selected under the old
    # exact-ECO branch, before we made B90 trigger the selector.
    opening = find("B90")[0]
    state = BoardState.from_pgn(opening.pgn)

    received_bodies: list[dict] = []

    def factory(payload, _log):
        received_bodies.append(dict(payload))
        # Reply with a legal move from the opening position; the
        # exact move doesn't matter for this test.
        return 200, {"san": "Be3"}

    with stub_server(factory) as (base, _server_log):
        san = net.request_move(
            base,
            state.fen(),
            moves=state.san_history(),
            timeout=5.0,
        )
    assert san == "Be3"
    # Exactly one request, with the expected body shape.
    assert len(received_bodies) == 1
    body = received_bodies[0]
    assert body["fen"] == opening.to_fen()
    assert body["moves"] == [
        "e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6",
    ]
    # And the FEN must be the post-opening position, not the start.
    assert body["fen"] != chess.STARTING_FEN


def test_request_move_without_moves_sends_empty_list() -> None:
    """Sanity check: when the caller doesn't pass moves (legacy / non-
    opening start), the body has ``"moves": []`` rather than omitting
    the field.  The maia_server.py history branch is gated on
    ``use_history and moves`` so an empty list correctly disables
    history mode rather than crashing.
    """
    received: list[dict] = []

    def factory(payload, _log):
        received.append(dict(payload))
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, _log):
        net.request_move(base, chess.STARTING_FEN, moves=[])
    assert received[0]["moves"] == []


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
        net_server.main([])  # no argv → uses --port default (8080)
    assert captured["addr"] == ("0.0.0.0", 8080)


def test_net_server_cli_custom_port(monkeypatch) -> None:
    captured: dict = {}

    def fake_server(addr, handler):
        captured["addr"] = addr
        return _FakeServer(addr[1])

    monkeypatch.setattr("chess_tui.net_server.ThreadingHTTPServer", fake_server)
    with pytest.raises(SystemExit):
        net_server.main(["--port", "9999"])
    assert captured["addr"] == ("0.0.0.0", 9999)


def test_net_server_cli_custom_host(monkeypatch) -> None:
    """--host lets the user restrict to localhost (or any other interface)."""
    captured: dict = {}

    def fake_server(addr, handler):
        captured["addr"] = addr
        return _FakeServer(addr[1])

    monkeypatch.setattr("chess_tui.net_server.ThreadingHTTPServer", fake_server)
    with pytest.raises(SystemExit):
        net_server.main(["--host", "127.0.0.1", "--port", "9999"])
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
        def __init__(self, state=None, players=None, observers=None, opening=None):
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
        def __init__(self, state=None, players=None, observers=None, opening=None):
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
        def __init__(self, state=None, players=None, observers=None, opening=None):
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


def test_chess_tui_cli_fen_option() -> None:
    from chess_tui.app import main

    seen_state = [None]

    class _FakeApp:
        def __init__(self, state=None, players=None, observers=None, opening=None):
            seen_state[0] = state

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main(["--fen", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"])
    finally:
        app_mod.ChessApp = real

    state = seen_state[0]
    assert state is not None
    # Verify the board is in the expected position (white played e4)
    assert state.turn() == chess.BLACK


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


# ---- Observer mode: net.post_observer + TUI + CLI -------------------------


def test_post_observer_returns_none_on_success() -> None:
    """post_observer must always return None — caller never uses the SAN."""
    def factory(_payload, _log):
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, log):
        result = net.post_observer(base, chess.Board().fen(), quiet=True)

    assert result is None
    # The body that hit the server had the FEN.
    assert log and "fen" in log[0]["body"]


def test_post_observer_sends_san_history_when_provided() -> None:
    """Observers get the SAN history so they can apply --use-history (maia)."""
    def factory(_payload, _log):
        return 200, {"san": "e4"}

    with stub_server(factory) as (base, log):
        net.post_observer(
            base,
            chess.Board().fen(),
            moves=["e4", "e5", "Nf3"],
            quiet=True,
        )

    assert log[0]["body"]["moves"] == ["e4", "e5", "Nf3"]


def test_post_observer_strips_trailing_slash() -> None:
    def factory(_payload, _log):
        return 200, {"san": "Nf3"}

    with stub_server(factory) as (base, log):
        net.post_observer(base + "/", chess.Board().fen(), quiet=True)

    assert log[0]["path"] == "/move"


def test_post_observer_swallows_4xx_server_errors() -> None:
    """A 4xx response (e.g. observer's own bug) must not propagate."""
    def factory(_payload, _log):
        return 400, {"error": "illegal move: e5"}

    with stub_server(factory) as (base, _log):
        result = net.post_observer(
            base, chess.Board().fen(), timeout=0.5, quiet=True
        )

    assert result is None


def test_post_observer_swallows_5xx_server_errors() -> None:
    """A 5xx response must not propagate."""
    def factory(_payload, _log):
        return 500, {"error": "engine crashed"}

    with stub_server(factory) as (base, _log):
        result = net.post_observer(
            base, chess.Board().fen(), timeout=0.5, quiet=True
        )

    assert result is None


def test_post_observer_swallows_503_busy() -> None:
    """The TUI never reads the SAN — 503 from a busy observer is fine."""
    def factory(_payload, _log):
        return 503, {"error": "server busy"}

    with stub_server(factory) as (base, _log):
        result = net.post_observer(
            base, chess.Board().fen(), timeout=0.5, quiet=True
        )

    assert result is None


def test_post_observer_swallows_connection_refused() -> None:
    """No server listening — must not raise."""
    closed_port = _free_port()
    result = net.post_observer(
        f"http://127.0.0.1:{closed_port}", chess.Board().fen(),
        timeout=0.5, quiet=True,
    )
    assert result is None


def test_post_observer_swallows_remote_disconnected() -> None:
    """Server kills the connection mid-request — must not raise."""
    from unittest.mock import patch
    import http.client

    def mock_urlopen(req, timeout):
        raise http.client.RemoteDisconnected("server died")

    with patch("urllib.request.urlopen", mock_urlopen):
        result = net.post_observer(
            "http://127.0.0.1:9999", chess.Board().fen(),
            timeout=0.5, quiet=True,
        )
    assert result is None


def test_post_observer_swallows_unexpected_exception() -> None:
    """A stray bug (e.g. bad URL) must not raise — defensive catch-all."""
    from unittest.mock import patch

    def mock_urlopen(req, timeout):
        raise RuntimeError("something is very wrong")

    with patch("urllib.request.urlopen", mock_urlopen):
        result = net.post_observer(
            "http://127.0.0.1:9999", chess.Board().fen(),
            timeout=0.5, quiet=True,
        )
    assert result is None


def test_post_observer_logs_to_stderr_on_failure_by_default(
    capfd: pytest.CaptureFixture,
) -> None:
    """By default, a failed observer POST is logged to stderr so the
    user can see why their observer isn't being called. (E.g. they ran
    the engine on a remote machine but the server is still bound to
    127.0.0.1, so the connection is refused.) The test uses
    quiet=False implicitly by not passing it."""
    from unittest.mock import patch

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = OSError("Connection refused")
        result = net.post_observer(
            "http://does-not-exist:9999", chess.Board().fen(), timeout=0.5,
        )
    assert result is None
    captured = capfd.readouterr()
    assert "chess-tui observer" in captured.err
    assert "http://does-not-exist:9999" in captured.err
    assert "Connection refused" in captured.err


def test_post_observer_quiet_suppresses_stderr_log(
    capfd: pytest.CaptureFixture,
) -> None:
    """quiet=True silences the stderr log; useful for tests and for
    callers that already handle errors themselves."""
    from unittest.mock import patch

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = OSError("Connection refused")
        net.post_observer(
            "http://does-not-exist:9999", chess.Board().fen(),
            timeout=0.5, quiet=True,
        )
    captured = capfd.readouterr()
    assert "chess-tui observer" not in captured.err


# ---- CLI: --observer flag parsing ------------------------------------------


def test_chess_tui_cli_observer_flag_passes_url_to_app() -> None:
    from chess_tui.app import main

    seen: list[list[str]] = []

    class _FakeApp:
        def __init__(self, state=None, players=None, observers=None, opening=None):
            seen.append(list(observers) if observers else [])

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main(["--observer", "http://obs.example:8084"])
    finally:
        app_mod.ChessApp = real

    assert seen == [["http://obs.example:8084"]]


def test_chess_tui_cli_observer_flag_accepts_multiple_urls() -> None:
    """A single --observer can take multiple URLs."""
    from chess_tui.app import main

    seen: list[list[str]] = []

    class _FakeApp:
        def __init__(self, state=None, players=None, observers=None, opening=None):
            seen.append(list(observers) if observers else [])

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main([
                "--observer", "http://a.example:1", "http://b.example:2",
            ])
    finally:
        app_mod.ChessApp = real

    assert seen == [["http://a.example:1", "http://b.example:2"]]


def test_chess_tui_cli_observer_flag_is_repeatable() -> None:
    """--observer can be repeated; order preserved, de-duped."""
    from chess_tui.app import main

    seen: list[list[str]] = []

    class _FakeApp:
        def __init__(self, state=None, players=None, observers=None, opening=None):
            seen.append(list(observers) if observers else [])

        def run(self):
            raise SystemExit(0)

    import chess_tui.app as app_mod
    real = app_mod.ChessApp
    app_mod.ChessApp = _FakeApp
    try:
        with pytest.raises(SystemExit):
            main([
                "--observer", "http://a.example:1",
                "--observer", "http://b.example:2", "http://c.example:3",
                "--observer", "http://a.example:1",  # duplicate
            ])
    finally:
        app_mod.ChessApp = real

    # Order preserved, de-duped, flat.
    assert seen == [["http://a.example:1", "http://b.example:2", "http://c.example:3"]]


def test_chess_tui_cli_no_observer_flag_means_no_observers() -> None:
    from chess_tui.app import main

    seen: list[list[str]] = []

    class _FakeApp:
        def __init__(self, state=None, players=None, observers=None, opening=None):
            seen.append(list(observers) if observers else [])

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

    assert seen == [[]]


# ---- TUI integration: observer fires after a move --------------------------


class _ObserverHandler(BaseHTTPRequestHandler):
    """Records every POST it receives. The TUI never reads our response."""

    request_log: list[dict] = []
    # When non-None, sleep this many seconds before responding — used to
    # prove that the TUI does NOT wait for observers.
    response_delay: float = 0.0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        _ObserverHandler.request_log.append({"path": self.path, "body": payload})
        if _ObserverHandler.response_delay > 0:
            time.sleep(_ObserverHandler.response_delay)
        data = json.dumps({"san": "ignored"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a, **k):  # noqa: A002
        pass


def _start_observer_server() -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    """Reset, start, and return the observer stub server."""
    _ObserverHandler.request_log = []
    _ObserverHandler.response_delay = 0.0
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _ObserverHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def _stop_observer_server(server: ThreadingHTTPServer) -> None:
    _ObserverHandler.request_log = []
    _ObserverHandler.response_delay = 0.0
    server.shutdown()
    server.server_close()


def _start_tagged_observer_server(
    tag: str,
) -> tuple[ThreadingHTTPServer, threading.Thread, int, list[dict]]:
    """Like ``_start_observer_server`` but each server has its own
    per-tag log list, so we can verify 2+ distinct observers on
    different ports all receive the POST.

    The returned ``log`` is appended to on every incoming POST.
    """
    log: list[dict] = []

    class _TaggedHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                payload = {}
            log.append({"path": self.path, "body": payload, "tag": tag})
            data = json.dumps({"san": "ignored"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a, **k):  # noqa: A002
            pass

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _TaggedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port, log


def _stop_tagged_observer_server(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


async def test_tui_fires_observer_after_move() -> None:
    """After a local player makes a move, the TUI POSTs the new FEN + SAN
    history to every registered observer."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            # Wait for the observer POST to arrive.
            for _ in range(40):
                await pilot.pause()
                if _ObserverHandler.request_log:
                    break
                await asyncio.sleep(0.05)
        assert len(_ObserverHandler.request_log) == 1, (
            f"expected 1 observer POST, got {len(_ObserverHandler.request_log)}"
        )
        post = _ObserverHandler.request_log[0]
        assert post["path"] == "/move"
        # FEN reflects black-to-move after 1.e4.
        assert post["body"]["fen"].startswith(
            "rnbqkbnr/pppppppp/8/8/4P3"
        ), post["body"]["fen"]
        # SAN history includes the just-played move.
        assert post["body"]["moves"] == ["e4"]
    finally:
        _stop_observer_server(server)


async def test_tui_fires_observer_after_every_move() -> None:
    """Observers get a POST after every move, in order, with the full SAN
    history each time."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            for san in ("e4", "e5", "Nf3"):
                await pilot.press(*list(san), "enter")
                await pilot.pause()
            # Wait for all 3 POSTs to arrive.
            for _ in range(60):
                await pilot.pause()
                if len(_ObserverHandler.request_log) >= 3:
                    break
                await asyncio.sleep(0.05)
        log = _ObserverHandler.request_log
        assert len(log) == 3, f"expected 3 observer POSTs, got {len(log)}: {log}"
        assert log[0]["body"]["moves"] == ["e4"]
        assert log[1]["body"]["moves"] == ["e4", "e5"]
        assert log[2]["body"]["moves"] == ["e4", "e5", "Nf3"]
        # After 1.e4 e5 2.Nf3 it's black's turn.
        assert log[2]["body"]["fen"].split()[1] == "b"
    finally:
        _stop_observer_server(server)


async def test_tui_does_not_notify_observer_on_flip() -> None:
    """Flipping the board is a view-only change — observers don't get a POST."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            # Wait for the move POST.
            for _ in range(40):
                await pilot.pause()
                if _ObserverHandler.request_log:
                    break
                await asyncio.sleep(0.05)
            assert len(_ObserverHandler.request_log) == 1
            # Flip — no new POST.
            app.action_flip()
            for _ in range(10):
                await pilot.pause()
            assert len(_ObserverHandler.request_log) == 1
            # Flip back — still no new POST.
            app.action_flip()
            for _ in range(10):
                await pilot.pause()
            assert len(_ObserverHandler.request_log) == 1
    finally:
        _stop_observer_server(server)


async def test_tui_does_not_notify_observer_on_reset() -> None:
    """Resetting the game must not re-notify observers with the start FEN."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            for _ in range(40):
                await pilot.pause()
                if _ObserverHandler.request_log:
                    break
                await asyncio.sleep(0.05)
            assert len(_ObserverHandler.request_log) == 1
            app.action_reset()
            for _ in range(10):
                await pilot.pause()
            assert len(_ObserverHandler.request_log) == 1
    finally:
        _stop_observer_server(server)


async def test_tui_fires_all_observers_in_parallel() -> None:
    """Multiple observers all get a POST. We don't time the parallelism
    (flaky), but we do verify each one receives the move."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url1 = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url1, url1, url1])  # same stub 3x
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            for _ in range(60):
                await pilot.pause()
                if len(_ObserverHandler.request_log) >= 3:
                    break
                await asyncio.sleep(0.05)
        assert len(_ObserverHandler.request_log) == 3
        for post in _ObserverHandler.request_log:
            assert post["body"]["moves"] == ["e4"]
            assert post["body"]["fen"].startswith(
                "rnbqkbnr/pppppppp/8/8/4P3"
            )
    finally:
        _stop_observer_server(server)


async def test_tui_fires_both_observers_when_passed_via_single_flag() -> None:
    """Regression: when two observers are passed as ``--observer A B``,
    BOTH must receive the POST. (Earlier the user reported only the
    second one being called; the actual cause was the remote observer
    being bound to 127.0.0.1, but this test makes sure the in-process
    fan-out is correct.)"""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server1, _t1, port1, log1 = _start_tagged_observer_server("OBS1")
    server2, _t2, port2, log2 = _start_tagged_observer_server("OBS2")
    try:
        url1 = f"http://127.0.0.1:{port1}"
        url2 = f"http://127.0.0.1:{port2}"
        # Same pattern as ``--observer URL1 URL2`` after de-dup.
        app = ChessApp(observers=[url1, url2])
        assert app._observers == [url1, url2]
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            for _ in range(40):
                await pilot.pause()
                if log1 and log2:
                    break
                await asyncio.sleep(0.05)
        # Both observers must have received the POST.
        assert len(log1) == 1, f"OBS1 log: {log1!r}"
        assert len(log2) == 1, f"OBS2 log: {log2!r}"
        assert log1[0]["tag"] == "OBS1"
        assert log2[0]["tag"] == "OBS2"
        # Both POSTs have the same FEN+SAN history (the post-move state).
        assert log1[0]["body"]["moves"] == ["e4"]
        assert log2[0]["body"]["moves"] == ["e4"]
        assert log1[0]["body"]["fen"].startswith(
            "rnbqkbnr/pppppppp/8/8/4P3"
        )
        assert log2[0]["body"]["fen"] == log1[0]["body"]["fen"]
    finally:
        _stop_tagged_observer_server(server1)
        _stop_tagged_observer_server(server2)


async def test_tui_does_not_block_on_slow_observer() -> None:
    """The TUI must NOT wait for a slow observer — next move goes through
    immediately, even if the previous observer is still thinking."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    # Make every observer response take a long time.
    _ObserverHandler.response_delay = 2.0
    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            # The TUI should be ready to accept the next move immediately,
            # even though the observer response is delayed 2s.
            start = time.monotonic()
            await pilot.press("d", "5", "enter")
            await pilot.pause()
            elapsed = time.monotonic() - start
            # Should be well under the observer's 2s delay.
            assert elapsed < 1.0, f"TUI blocked for {elapsed:.2f}s on observer"
            # Both moves have been applied.
            assert app._state.san_history() == ["e4", "d5"]
    finally:
        _stop_observer_server(server)


async def test_tui_observer_failure_does_not_break_game() -> None:
    """A 500 from an observer must not break the TUI — the next move
    still goes through."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    def fail_factory(_payload, _log):
        return 500, {"error": "observer crashed"}

    with stub_server(fail_factory) as (url, _log):
        app = ChessApp(observers=[url])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            # Give the (failed) observer time to reply.
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.05)
            # Second move still works.
            await pilot.press("e", "5", "enter")
            await pilot.pause()
            assert app._state.san_history() == ["e4", "e5"]


async def test_tui_no_observers_means_no_extra_http_traffic() -> None:
    """Sanity: a TUI with no observers makes no POSTs after a move."""
    from chess_tui.app import ChessApp
    from textual.widgets import Input

    server, _thread, port = _start_observer_server()
    try:
        url = f"http://127.0.0.1:{port}"
        app = ChessApp()  # no observers
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#move-input", Input)
            inp.focus()
            await pilot.press("e", "4", "enter")
            await pilot.pause()
            for _ in range(10):
                await pilot.pause()
                await asyncio.sleep(0.05)
        assert _ObserverHandler.request_log == []
    finally:
        _stop_observer_server(server)
        server.shutdown()
        server.server_close()