"""Standalone test server for the chess-tui network player API.

Run with ``uv run chess-tui-net [port]`` (default port 8080). For each
``POST /move`` request the server prints the board to its own stdout, prompts
the user for a move, and returns it as SAN. This is just enough to mimic a
network opponent when you're testing the TUI in a separate terminal.

The API is described in ``openapi/chess-tui-net.yaml``; this server is the
reference implementation.

Concurrency model:
    The main thread runs the HTTP server and accepts new requests. Each request
    spawns a daemon thread to read user input via ``input()``. If a new request
    arrives while waiting for input, the old request is abandoned (not responded
    to) and a new input prompt is shown. The old thread will finish reading
    input naturally and discard the result.

    We use a generation counter to track which request is "current". When input
    arrives, the thread checks if its generation matches the current one. If
    not, the input is discarded.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess


def _make_handler() -> type[BaseHTTPRequestHandler]:
    # --- shared state across all handler instances ---
    _lock = threading.Lock()  # serializes input() calls
    _generation = 0  # incremented on each new request
    _prompt_count = 0  # how many prompts have been shown (for visual clarity)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server convention
            nonlocal _generation, _prompt_count

            if self.path.rstrip("/") != "/move":
                self._send_json(404, {"error": f"unknown path: {self.path!r}"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return

            fen = payload.get("fen")
            if not isinstance(fen, str):
                self._send_json(400, {"error": "missing 'fen' string in body"})
                return

            moves = payload.get("moves", [])
            if not isinstance(moves, list):
                moves = []

            try:
                board = chess.Board(fen)
            except ValueError as exc:
                self._send_json(400, {"error": f"bad FEN: {exc}"})
                return

            # --- cancel any previous pending input ---
            with _lock:
                _generation += 1
                my_generation = _generation
                _prompt_count += 1
                prompt_num = _prompt_count

            # --- wait for any ongoing input to finish, then do our own ---
            # We acquire the lock to serialize input() calls. If we were
            # superseded while waiting, we return 503 immediately.
            with _lock:
                # Check if we were superseded while waiting for the lock
                if _generation != my_generation:
                    self._send_json(503, {"error": "superseded by new request"})
                    return

                # Print the position and prompt
                print("\n" + "─" * 32, flush=True)
                print(f"[#{prompt_num}] Incoming position — FEN: {fen}", flush=True)
                # Print move history above the board
                if moves:
                    # Format as pairs: "1. e4 e5 2. Nf3 Nf6 ..."
                    move_pairs: list[str] = []
                    for i in range(0, len(moves), 2):
                        move_num = i // 2 + 1
                        if i + 1 < len(moves):
                            move_pairs.append(f"{move_num}. {moves[i]} {moves[i+1]}")
                        else:
                            move_pairs.append(f"{move_num}. {moves[i]}")
                    print("Moves: " + " ".join(move_pairs), flush=True)
                print(board, flush=True)
                print(f"Side to move: {'White' if board.turn else 'Black'}", flush=True)
                try:
                    user_input = input("Your move (SAN or UCI, e.g. e4 / Nf3 / e7e8q): ")
                except EOFError:
                    self._send_json(400, {"error": "no input available"})
                    return

            # Check if we were superseded while waiting for input
            if _generation != my_generation:
                # Discard input from stale request — don't send any response
                # (the client already timed out and moved on)
                print(f"  ↳ (discarded — superseded by newer request)", flush=True)
                return

            user_input = user_input.strip()
            if not user_input:
                self._send_json(400, {"error": "empty move"})
                return

            try:
                move = board.parse_san(user_input)
            except ValueError:
                try:
                    move = board.parse_uci(user_input)
                except ValueError as exc:
                    self._send_json(400, {"error": f"unparseable move: {exc}"})
                    return
            if move not in board.legal_moves:
                self._send_json(
                    400, {"error": f"illegal move in this position: {user_input!r}"}
                )
                return

            self._send_json(200, {"san": board.san(move)})

        def _send_json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):  # noqa: A002 — silence access log
            pass

    return Handler


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    import argparse

    parser = argparse.ArgumentParser(
        prog="chess-tui-net",
        description=(
            "Reference implementation of the chess-tui network player API. "
            "For each POST /move it prints the position to its own stdout, "
            "prompts you for a move, and returns it as SAN."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help=(
            "host/interface to bind to (default: 0.0.0.0 = all interfaces). "
            "Use 127.0.0.1 to restrict to localhost."
        ),
    )
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), _make_handler())
    from .host import describe_listen
    print(
        f"chess-tui network server listening on {describe_listen(args.host, args.port)}\n"
        "  POST /move with {\"fen\": \"...\"} → {\"san\": \"...\"}\n"
        "  If client retries while you're thinking, the old prompt is\n"
        "  abandoned and a fresh prompt appears.\n"
        "  Ctrl-C to stop.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
