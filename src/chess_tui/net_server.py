"""Standalone test server for the chess-tui network player API.

Run with ``uv run chess-tui-net [port]`` (default port 8080). For each
``POST /move`` request the server prints the board to its own stdout, prompts
the user for a move, and returns it as SAN. This is just enough to mimic a
network opponent when you're testing the TUI in a separate terminal.

The API is described in ``openapi/chess-tui-net.yaml``; this server is the
reference implementation.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server convention
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

            try:
                board = chess.Board(fen)
            except ValueError as exc:
                self._send_json(400, {"error": f"bad FEN: {exc}"})
                return

            # Print to the server's own stdout so a human in the terminal
            # can see what's being asked and type the next move.
            print("\n" + "─" * 32, flush=True)
            print(f"Incoming position — FEN: {fen}", flush=True)
            print(board, flush=True)
            print(f"Side to move: {'White' if board.turn else 'Black'}", flush=True)
            try:
                user_input = input("Your move (SAN or UCI, e.g. e4 / Nf3 / e7e8q): ")
            except EOFError:
                self._send_json(400, {"error": "no input available"})
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
        "port",
        nargs="?",
        type=int,
        default=8080,
        help="port to listen on (default 8080)",
    )
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler())
    print(
        f"chess-tui network server listening on http://127.0.0.1:{args.port}\n"
        "  POST /move with {\"fen\": \"...\"} → {\"san\": \"...\"}\n"
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