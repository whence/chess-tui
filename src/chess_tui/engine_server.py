"""Engine-powered network player server for chess-tui.

Run with ``uv run chess-tui-engine [options]``. Uses a UCI chess engine
(like plentychess or stockfish) to play moves automatically.

The API is the same as chess-tui-net: POST /move with {"fen": "..."} → {"san": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.engine


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Navigate from src/chess_tui/ to project root
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def load_engines_config() -> dict:
    """Load engine paths from engines.json."""
    config_path = os.path.join(PROJECT_DIR, "engines.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: engines.json not found or invalid: {e}", file=sys.stderr)
        return {"engines": {}}


def resolve_engine_path(path: str) -> str:
    """Expand ~ and resolve the engine path."""
    return os.path.expanduser(path)


def _make_handler(
    engine_path: str,
    depth: int,
    nodes: int | None,
    time_limit: float | None,
    min_wait: float,
    max_wait: float,
    verbose: bool,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler with the given engine configuration."""

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

            moves = payload.get("moves", [])

            try:
                board = chess.Board(fen)
            except ValueError as exc:
                self._send_json(400, {"error": f"bad FEN: {exc}"})
                return

            if board.is_game_over():
                result = board.result(claim_draw=True)
                self._send_json(400, {"error": f"game over: {result}"})
                return

            if verbose:
                side = "White" if board.turn else "Black"
                move_num = board.fullmove_number
                print(f"\n{'─' * 40}", flush=True)
                print(f"Move {move_num} — {side} to move", flush=True)
                if moves:
                    # Show last few moves
                    recent = moves[-6:] if len(moves) > 6 else moves
                    print(f"Recent: {' '.join(recent)}", flush=True)
                print(board, flush=True)

            # Simulate thinking time
            wait_time = random.uniform(min_wait, max_wait)
            if verbose:
                print(f"Thinking for {wait_time:.1f}s...", flush=True)
            time.sleep(wait_time)

            # Run engine
            move = self._get_engine_move(board)
            if move is None:
                self._send_json(500, {"error": "engine failed to produce a move"})
                return

            san = board.san(move)
            if verbose:
                print(f"Engine plays: {san}", flush=True)

            self._send_json(200, {"san": san})

        def _get_engine_move(self, board: chess.Board) -> chess.Move | None:
            """Get a move from the engine."""
            try:
                eng = chess.engine.SimpleEngine.popen_uci(
                    [engine_path], stderr=subprocess.DEVNULL
                )
            except Exception as exc:
                print(f"Failed to start engine: {exc}", file=sys.stderr)
                return None

            try:
                # Build limit
                if time_limit:
                    limit = chess.engine.Limit(time=time_limit)
                elif nodes:
                    limit = chess.engine.Limit(nodes=nodes)
                else:
                    limit = chess.engine.Limit(depth=depth)

                result = eng.play(board, limit)
                return result.move
            except chess.engine.EngineTerminatedError:
                print("Engine terminated", file=sys.stderr)
                return None
            except Exception as exc:
                print(f"Engine error: {exc}", file=sys.stderr)
                return None
            finally:
                try:
                    eng.quit()
                except Exception:
                    pass

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

    # Load engine config
    engines_config = load_engines_config()
    available_engines = list(engines_config.get("engines", {}).keys())
    default_engine_name = available_engines[0] if available_engines else "plentychess"

    parser = argparse.ArgumentParser(
        prog="chess-tui-engine",
        description=(
            "Engine-powered network player server for chess-tui. "
            "Uses a UCI engine (plentychess, stockfish, etc.) to play moves."
        ),
    )
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=8080,
        help="port to listen on (default: 8080)",
    )
    parser.add_argument(
        "-e", "--engine-name",
        default=default_engine_name,
        choices=available_engines,
        help=f"engine name from engines.json (default: {default_engine_name}). "
             f"Available: {', '.join(available_engines)}",
    )
    parser.add_argument(
        "--engine",
        default=None,
        help="path to UCI engine (overrides --engine-name)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=20,
        help="search depth (default: 20)",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=None,
        help="node limit (overrides --depth if set)",
    )
    parser.add_argument(
        "--time",
        type=float,
        default=None,
        help="time limit in seconds (overrides --depth and --nodes if set)",
    )
    parser.add_argument(
        "--min-wait",
        type=float,
        default=0.5,
        help="minimum thinking time in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--max-wait",
        type=float,
        default=3.0,
        help="maximum thinking time in seconds (default: 3.0)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="print position and thinking info to stdout",
    )
    args = parser.parse_args(argv)

    # Resolve engine path
    if args.engine:
        # Explicit path takes precedence
        engine_path = resolve_engine_path(args.engine)
        engine_name = os.path.basename(engine_path)
    else:
        # Look up from engines.json
        engine_entry = engines_config.get("engines", {}).get(args.engine_name)
        if not engine_entry:
            print(f"Error: engine '{args.engine_name}' not found in engines.json", file=sys.stderr)
            print(f"Available: {', '.join(available_engines)}", file=sys.stderr)
            sys.exit(1)
        if isinstance(engine_entry, str):
            engine_path = resolve_engine_path(engine_entry)
        elif isinstance(engine_entry, dict):
            engine_path = resolve_engine_path(engine_entry.get("path", ""))
        else:
            print(f"Error: invalid engine config for '{args.engine_name}'", file=sys.stderr)
            sys.exit(1)
        engine_name = args.engine_name

    # Validate engine exists
    if not os.path.exists(engine_path):
        print(f"Error: engine not found at {engine_path}", file=sys.stderr)
        sys.exit(1)

    # Determine search limit description
    if args.time:
        limit_desc = f"time {args.time}s"
    elif args.nodes:
        limit_desc = f"{args.nodes} nodes"
    else:
        limit_desc = f"depth {args.depth}"

    handler = _make_handler(
        engine_path=engine_path,
        depth=args.depth,
        nodes=args.nodes,
        time_limit=args.time,
        min_wait=args.min_wait,
        max_wait=args.max_wait,
        verbose=args.verbose,
    )

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(
        f"chess-tui engine server listening on http://127.0.0.1:{args.port}\n"
        f"  Engine: {engine_name} ({os.path.basename(engine_path)})\n"
        f"  Limit: {limit_desc}\n"
        f"  Wait: {args.min_wait}-{args.max_wait}s\n"
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
