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


# Cap on multipv so a curious user can't ask the engine for 1000 lines.
MAX_MULTIPV = 20


def _normalise_score(raw: chess.engine.PovScore | None) -> chess.engine.PovScore | None:
    """Return the score from White's perspective.

    UCI engines report scores relative to the side to move. To make the
    output of this server match chess-coach-v3's ``/engine`` command — and
    the natural reading of a ``+``/``-`` sign in a log — we always print
    the score as if White were the perspective.
    """
    if raw is None:
        return None
    if isinstance(raw, chess.engine.PovScore) and raw.turn == chess.BLACK:
        return chess.engine.PovScore(-raw.relative, chess.WHITE)
    return raw


def _format_score(score_obj: chess.engine.PovScore | None) -> str:
    """Format a (white-perspective) score for the per-move log.

    Returns ``"+0.32"`` / ``"-1.50"`` for centipawn scores, or
    ``"Mate 3"`` / ``"Mate -2"`` for forced mates. Returns ``"?"`` if
    the engine didn't report a score.
    """
    if score_obj is None:
        return "?"
    mate = score_obj.relative.mate()
    if mate is not None:
        return f"Mate {mate}"
    cp = score_obj.relative.score(mate_score=32000)
    if cp is None:
        return "?"
    sign = "+" if cp > 0 else ""
    return f"{sign}{cp / 100:.2f}"


def _format_pv(board: chess.Board, pv: list[chess.Move]) -> list[str]:
    """Convert a list of moves into SAN strings by replaying them on a copy.

    Uses python-chess's own ``san()`` so the output matches what the rest
    of the chess-tui ecosystem (and the network protocol) uses. If a
    move is illegal in the position (engine bug, position mismatch),
    fall back to its UCI form for that move rather than raising.
    """
    if not pv:
        return []
    moves: list[str] = []
    b = board.copy()
    for m in pv:
        try:
            san = b.san(m)
            b.push(m)
        except ValueError:
            # Either san() rejected the move or push() did (m is illegal
            # in the position). Use the UCI form for this move; the
            # remainder of the PV may be misaligned but the log won't
            # crash.
            san = m.uci()
        moves.append(san)
    return moves


def _make_handler(
    engine_path: str,
    depth: int,
    nodes: int | None,
    time_limit: float | None,
    min_wait: float,
    max_wait: float,
    multipv: int,
    verbose: bool,
    engine_name: str,
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
                    # Format as pairs: "1. e4 e5 2. Nf3 Nf6 ..."
                    move_pairs: list[str] = []
                    for i in range(0, len(moves), 2):
                        num = i // 2 + 1
                        if i + 1 < len(moves):
                            move_pairs.append(f"{num}. {moves[i]} {moves[i+1]}")
                        else:
                            move_pairs.append(f"{num}. {moves[i]}")
                    print("Moves: " + " ".join(move_pairs), flush=True)
                print(board, flush=True)

            # Simulate thinking time
            wait_time = random.uniform(min_wait, max_wait)
            if verbose:
                print(f"Thinking for {wait_time:.1f}s...", flush=True)
            time.sleep(wait_time)

            # Run engine
            result = self._get_engine_analysis(board)
            if result is None:
                self._send_json(500, {"error": "engine failed to produce a move"})
                return
            move, infos = result
            if move is None:
                self._send_json(500, {"error": "engine returned no principal variation"})
                return

            san = board.san(move)

            if verbose:
                limit_desc = (
                    f"time {time_limit}s" if time_limit
                    else f"{nodes} nodes" if nodes
                    else f"depth {depth}"
                )
                print(
                    f"  Engine: {engine_name} ({limit_desc}, multipv {multipv})",
                    flush=True,
                )
                print(f"  FEN: {fen}", flush=True)
                print(flush=True)
                if infos:
                    print("  PVs:", flush=True)
                    for i, info in enumerate(infos, 1):
                        pv = info.get("pv") or []
                        pv_sans = _format_pv(board, pv)
                        score = _format_score(_normalise_score(info.get("score")))
                        depth_reached = info.get("depth") or 0
                        moves_str = " ".join(pv_sans) if pv_sans else "(no moves)"
                        print(
                            f"    #{i}  {score}/{depth_reached}  {moves_str}",
                            flush=True,
                        )
                else:
                    print("  PVs: (none returned)", flush=True)
                print(f"  Engine plays: {san}", flush=True)

            self._send_json(200, {"san": san})

        def _get_engine_analysis(
            self, board: chess.Board
        ) -> tuple[chess.Move | None, list[chess.engine.InfoDict]] | None:
            """Run the engine and return (best_move, infos).

            ``infos`` has up to ``multipv`` entries, each carrying a PV
            and a score (after normalisation in the caller). The best
            move is ``infos[0]['pv'][0]``; we fall back to a legal-move
            sample if the engine somehow didn't return a principal
            variation.
            """
            try:
                eng = chess.engine.SimpleEngine.popen_uci(
                    [engine_path], stderr=subprocess.DEVNULL
                )
            except Exception as exc:
                print(f"Failed to start engine: {exc}", file=sys.stderr)
                return None

            try:
                if time_limit:
                    limit = chess.engine.Limit(time=time_limit)
                elif nodes:
                    limit = chess.engine.Limit(nodes=nodes)
                else:
                    limit = chess.engine.Limit(depth=depth)

                # multipv=1 is the default and the engine's natural
                # behavior; passing it explicitly is harmless and keeps
                # the code path uniform.
                infos = eng.analyse(board, limit, multipv=multipv)
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

            if not infos:
                return None, []

            best_move: chess.Move | None = None
            pv0 = infos[0].get("pv") or []
            if pv0:
                best_move = pv0[0]
            else:
                # Defensive: engine didn't return a pv. Fall back to a
                # random legal move so the game doesn't stall.
                legal = list(board.legal_moves)
                best_move = random.choice(legal) if legal else None
            return best_move, list(infos)

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
        help="print position, thinking info, and per-move engine analysis (PVs + scores) to stdout",
    )
    parser.add_argument(
        "--multipv",
        type=int,
        default=1,
        help=(
            f"number of principal variations to log per move (1-{MAX_MULTIPV}, "
            f"default: 1). With -v/--verbose, the per-move log lists this many "
            f"lines (each with its score, depth, and SAN moves). The engine "
            f"still plays the best one regardless."
        ),
    )
    args = parser.parse_args(argv)

    if not (1 <= args.multipv <= MAX_MULTIPV):
        parser.error(f"--multipv must be between 1 and {MAX_MULTIPV}")

    # Resolve engine path
    if args.engine:
        # Try as path first, then as engine name
        if os.path.exists(args.engine):
            engine_path = resolve_engine_path(args.engine)
            engine_name = os.path.basename(engine_path)
        elif args.engine in engines_config.get("engines", {}):
            # Look up as engine name
            engine_entry = engines_config["engines"][args.engine]
            if isinstance(engine_entry, str):
                engine_path = resolve_engine_path(engine_entry)
            elif isinstance(engine_entry, dict):
                engine_path = resolve_engine_path(engine_entry.get("path", ""))
            else:
                print(f"Error: invalid engine config for '{args.engine}'", file=sys.stderr)
                sys.exit(1)
            engine_name = args.engine
        else:
            print(f"Error: engine not found at '{args.engine}' (not a path or name in engines.json)", file=sys.stderr)
            sys.exit(1)
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
        multipv=args.multipv,
        verbose=args.verbose,
        engine_name=engine_name,
    )

    server = ThreadingHTTPServer((args.host, args.port), handler)
    from .host import describe_listen
    print(
        f"chess-tui engine server listening on {describe_listen(args.host, args.port)}\n"
        f"  Engine: {engine_name} ({os.path.basename(engine_path)})\n"
        f"  Limit: {limit_desc}\n"
        f"  Multipv: {args.multipv}\n"
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
