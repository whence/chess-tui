"""Maia-3-powered network player server for chess-tui.

Run with ``uv run chess-tui-maia [options]``. Spawns the ``maia3-5m`` UCI
engine (from the separate ``maia3`` Python package) and exposes the same
``POST /move`` RESTful protocol as the other chess-tui network players.

The ``maia3`` package is **not** a dependency of chess-tui itself — it is
installed separately. The path to the ``maia3-5m`` executable (or any
compatible ``maia3-uci`` invocation) is read from the ``maia`` section of
``engines.json``.

The API is the same as chess-tui-net:
    POST /move with {"fen": "...", "moves": [...]} → {"san": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.engine

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Navigate from src/chess_tui/ to project root
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# Maia-3's transformer is trained with a fixed 8-position history window
# (maia3-uci's --history default). The released checkpoints do not support
# other values, so we hard-code the same 8 for the history log.
MAIA_HISTORY_WINDOW = 8


def _load_engines_config() -> dict:
    """Load engines.json from project root."""
    config_path = os.path.join(PROJECT_DIR, "engines.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: engines.json not found or invalid: {e}", file=sys.stderr)
        sys.exit(1)


def _start_maia_engine(maia_path: str, use_history: bool) -> chess.engine.SimpleEngine:
    """Spawn the maia3 UCI engine as a long-lived subprocess.

    Forces CPU-only execution and (optionally) enables --use-uci-history so
    maia receives the move history as transformer context.
    """
    cmd = [maia_path, "--device", "cpu", "--no-use-amp"]
    if use_history:
        cmd.append("--use-uci-history")
    return chess.engine.SimpleEngine.popen_uci(cmd)


def _build_board(
    fen: str, moves: list[str], use_history: bool
) -> tuple[chess.Board, bool]:
    """Construct the current board state and report whether history mode was used.

    When use_history is on AND the move list can be replayed from the
    initial position to a board matching ``fen``, do so. python-chess then
    emits ``position startpos moves ...`` over UCI, and maia's
    ``deque(maxlen=8)`` retains the most recent 8 positions as transformer
    context.

    Otherwise (e.g., the game started from a non-standard FEN via
    ``chess-tui --fen``, or the move list is empty, or the moves are
    inconsistent with the FEN) fall back to ``chess.Board(fen)`` so the
    engine emits ``position fen <fen>`` with no move context. The returned
    bool indicates whether history mode was actually used.
    """
    if use_history and moves:
        try:
            reconstructed = chess.Board()
            for san in moves:
                reconstructed.push_san(san)
            # Compare position only (board_fen strips halfmove / fullmove
            # counters and turn, which can differ after reconstruction).
            if reconstructed.board_fen() == chess.Board(fen).board_fen():
                return reconstructed, True
        except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError, chess.InvalidMoveError):
            pass
    return chess.Board(fen), False


def _make_handler(
    engine: chess.engine.SimpleEngine,
    rng: random.Random,
    min_wait: float,
    max_wait: float,
    self_elo: int,
    oppo_elo: int,
    temperature: float,
    top_p: float,
    multi_pv: int,
    use_history: bool,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler with the given Maia configuration."""

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
            if not isinstance(moves, list):
                self._send_json(400, {"error": "'moves' must be a list of SAN strings"})
                return

            try:
                board, used_history = _build_board(fen, moves, use_history)
            except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError, chess.InvalidMoveError) as exc:
                self._send_json(400, {"error": f"bad FEN or move list: {exc}"})
                return

            if use_history and not used_history:
                print(
                    "  [maia] note: history mode requested but falling back to "
                    "FEN-only (game may have started from a non-standard position)",
                    flush=True,
                )

            if board.is_game_over():
                result = board.result(claim_draw=True)
                self._send_json(400, {"error": f"game over: {result}"})
                return

            side = "White" if board.turn else "Black"
            move_num = board.fullmove_number
            print(f"\n{'─' * 40}", flush=True)
            print(f"Move {move_num} — {side} to move", flush=True)
            if moves:
                move_pairs: list[str] = []
                for i in range(0, len(moves), 2):
                    num = i // 2 + 1
                    if i + 1 < len(moves):
                        move_pairs.append(f"{num}. {moves[i]} {moves[i+1]}")
                    else:
                        move_pairs.append(f"{num}. {moves[i]}")
                print("Moves: " + " ".join(move_pairs), flush=True)
            print(board, flush=True)

            if use_history and used_history and moves:
                # Log the moves maia is conditioning on (last 8 of the deque).
                visible = min(MAIA_HISTORY_WINDOW, len(moves))
                visible_start = max(0, len(moves) - MAIA_HISTORY_WINDOW)
                visible_moves = " ".join(moves[visible_start:])
                omitted = len(moves) - visible
                if omitted > 0:
                    print(
                        f"  [maia] use history: ...{omitted} earlier | "
                        f"last {visible}: {visible_moves}",
                        flush=True,
                    )
                else:
                    print(
                        f"  [maia] use history: {visible_moves}",
                        flush=True,
                    )

            # Simulate thinking time
            wait_time = rng.uniform(min_wait, max_wait)
            print(f"  [maia] thinking for {wait_time:.1f}s...", flush=True)
            time.sleep(wait_time)

            # Ask maia for analysis. Limit(nodes=1) is a no-op for maia (no
            # search) but UCI requires a `go` command. We use analyse() with
            # multipv so the engine's sampled `bestmove` (info[0]["pv"][0])
            # is returned alongside the runner-up T=1 candidates for
            # logging.
            try:
                infos = engine.analyse(
                    board,
                    chess.engine.Limit(nodes=1),
                    multipv=multi_pv,
                )
            except chess.engine.EngineError as exc:
                self._send_json(500, {"error": f"maia engine error: {exc}"})
                return
            if not infos or not infos[0].get("pv"):
                self._send_json(500, {"error": "maia returned no move"})
                return

            # info[0] carries the engine's bestmove (the temperature/top-p
            # sampled move). Subsequent infos are the runner-up T=1
            # candidates.
            played = infos[0]["pv"][0]
            played_san = board.san(played)

            if multi_pv > 1:
                print(
                    f"  [maia] top {min(multi_pv, len(infos))} candidates (T=1, raw policy):",
                    flush=True,
                )
                for rank, info in enumerate(infos, start=1):
                    pv = info.get("pv")
                    if not pv:
                        continue
                    cand_san = board.san(pv[0])
                    print(f"    {rank}. {cand_san}", flush=True)

            print(
                f"  [maia] chose: {played_san} "
                f"(self_elo={self_elo}, oppo_elo={oppo_elo}, "
                f"T={temperature}, top_p={top_p})",
                flush=True,
            )

            self._send_json(200, {"san": played_san})

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

    parser = argparse.ArgumentParser(
        prog="chess-tui-maia",
        description=(
            "Maia-3-powered network player server for chess-tui. "
            "Spawns the maia3-5m UCI engine and exposes it over HTTP."
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
        "--elo",
        type=int,
        required=True,
        help="ELO level for both sides (800-2700). Override individually with --self-elo/--oppo-elo.",
    )
    parser.add_argument(
        "--self-elo",
        type=int,
        default=None,
        help="ELO of the side to move. Overrides --elo for self if set.",
    )
    parser.add_argument(
        "--oppo-elo",
        type=int,
        default=None,
        help="ELO of the opponent. Overrides --elo for oppo if set.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help=(
            "sampling temperature (>=0). 0 = argmax; 1.0 = maia's natural "
            "distribution. Default: 1.0"
        ),
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=1.0,
        help=(
            "nucleus sampling threshold (0, 1]. 1.0 = no filtering. "
            "Default: 1.0"
        ),
    )
    parser.add_argument(
        "--multi-pv",
        type=int,
        default=1,
        help=(
            "number of top candidate moves maia reports per move (1-20). "
            "This is a logging-only knob — maia still plays one sampled move. "
            "Default: 1"
        ),
    )
    parser.add_argument(
        "--use-history",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "pass the move history to maia (--use-uci-history). When on, "
            "maia sees the last 8 positions as transformer context, matching "
            "how it was trained. Use --no-use-history to disable. Default: on."
        ),
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
    args = parser.parse_args(argv)

    # Resolve ELOs (individual overrides win over --elo).
    self_elo = args.self_elo if args.self_elo is not None else args.elo
    oppo_elo = args.oppo_elo if args.oppo_elo is not None else args.elo

    # Validate sampling-knob ranges.
    if args.temperature < 0:
        parser.error("--temperature must be >= 0")
    if not (0.0 < args.top_p <= 1.0):
        parser.error("--top-p must be in (0.0, 1.0]")
    if not (1 <= args.multi_pv <= 20):
        parser.error("--multi-pv must be in [1, 20]")

    # Load maia config from engines.json.
    config = _load_engines_config()
    maia_config = config.get("maia")
    if not maia_config:
        print("Error: 'maia' not found in engines.json", file=sys.stderr)
        print("Add a 'maia' entry, e.g. { \"maia\": { \"path\": \"maia3-5m\" } }", file=sys.stderr)
        sys.exit(1)
    maia_path = os.path.expanduser(maia_config.get("path", ""))
    if not maia_path:
        print("Error: 'maia.path' is empty in engines.json", file=sys.stderr)
        sys.exit(1)

    # Spawn the maia engine.
    print(f"Spawning Maia3 UCI engine: {maia_path}...", flush=True)
    try:
        engine = _start_maia_engine(maia_path, use_history=args.use_history)
    except FileNotFoundError as exc:
        print(f"Error: maia engine not found at {maia_path}: {exc}", file=sys.stderr)
        print(
            "Install with: pip install maia3   (then 'maia3-5m' is on PATH)\n"
            "Pre-download the model: maia3-cache",
            file=sys.stderr,
        )
        sys.exit(1)
    except chess.engine.EngineError as exc:
        print(f"Error: maia engine failed to start: {exc}", file=sys.stderr)
        sys.exit(1)

    # Configure UCI options (Elo, Temperature, TopP). MultiPV is intentionally
    # not set here — python-chess auto-manages it. We pass `multipv` to
    # `engine.analyse()` in the handler instead.
    try:
        engine.configure({
            "Elo": self_elo,
            "SelfElo": self_elo,
            "OppoElo": oppo_elo,
            "Temperature": args.temperature,
            "TopP": args.top_p,
        })
    except chess.engine.EngineError as exc:
        print(f"Error configuring maia: {exc}", file=sys.stderr)
        try:
            engine.quit()
        except Exception:
            pass
        sys.exit(1)

    print("Maia3 engine ready.", flush=True)

    # Create RNG
    rng = random.Random()

    handler = _make_handler(
        engine=engine,
        rng=rng,
        min_wait=args.min_wait,
        max_wait=args.max_wait,
        self_elo=self_elo,
        oppo_elo=oppo_elo,
        temperature=args.temperature,
        top_p=args.top_p,
        multi_pv=args.multi_pv,
        use_history=args.use_history,
    )

    server = ThreadingHTTPServer((args.host, args.port), handler)
    from .host import describe_listen
    print(
        f"chess-tui maia server listening on {describe_listen(args.host, args.port)}\n"
        f"  Engine: Maia3-5M (via {maia_path})\n"
        f"  Elo: self={self_elo}, oppo={oppo_elo}\n"
        f"  Temperature: {args.temperature}\n"
        f"  Top-p: {args.top_p}\n"
        f"  MultiPV: {args.multi_pv}\n"
        f"  History mode: {'on' if args.use_history else 'off'}\n"
        f"  Wait: {args.min_wait}-{args.max_wait}s\n"
        "  POST /move with {\"fen\": \"...\", \"moves\": [...]} → {\"san\": \"...\"}\n"
        "  Ctrl-C to stop.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        try:
            engine.quit()
        except Exception:
            pass
        server.shutdown()


if __name__ == "__main__":
    main()
