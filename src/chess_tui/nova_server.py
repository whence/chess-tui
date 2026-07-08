"""Nova-powered network player server for chess-tui.

Run with ``uv run chess-tui-nova [options]``. Uses the Nova chess predictor
to play moves at a chosen ELO, with per-move sampling knobs
(``--temperature``, ``--top-p``, ``--blunder-rate``) to vary playing strength
and style.

The API is the same as chess-tui-net: POST /move with {"fen": "..."} → {"san": "..."}
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Navigate from src/chess_tui/ to project root
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def _load_engines_config() -> dict:
    """Load engines.json from project root."""
    config_path = os.path.join(PROJECT_DIR, "engines.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: engines.json not found or invalid: {e}", file=sys.stderr)
        sys.exit(1)


def _load_nova_predictor(model_path: str, classical: float = 0.5, aggression: float = 0.5):
    """Lazy-load NovaPredictor to avoid import errors if dependencies missing."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        print(
            f"Error: nova requires numpy and onnxruntime.\n"
            f"Install with: uv add numpy onnxruntime",
            file=sys.stderr,
        )
        sys.exit(1)

    class NovaPredictor:
        PIECE = {
            "P": 0, "N": 1, "B": 2, "R": 3, "Q": 4, "K": 5,
            "p": 6, "n": 7, "b": 8, "r": 9, "q": 10, "k": 11,
        }

        def __init__(self, model_path, classical=0.5, aggression=0.5):
            self.model_path = model_path
            self.classical = classical
            self.aggression = aggression
            self._session = None

        def _ensure_session(self):
            if self._session is None:
                self._session = ort.InferenceSession(
                    self.model_path, providers=["CPUExecutionProvider"],
                )
            return self._session

        @staticmethod
        def fen_to_planes(fen):
            planes = np.zeros((18, 8, 8), dtype=np.float32)
            parts = fen.split()
            board_str, turn, castling, ep = parts[0], parts[1], parts[2], parts[3]
            for ri, rank_str in enumerate(board_str.split("/")):
                rank_idx, file_idx = 7 - ri, 0
                for ch in rank_str:
                    if ch.isdigit():
                        file_idx += int(ch)
                    else:
                        planes[NovaPredictor.PIECE[ch], rank_idx, file_idx] = 1.0
                        file_idx += 1
            if turn == "w":
                planes[12].fill(1.0)
            if "K" in castling:
                planes[13].fill(1.0)
            if "Q" in castling:
                planes[14].fill(1.0)
            if "k" in castling:
                planes[15].fill(1.0)
            if "q" in castling:
                planes[16].fill(1.0)
            if ep != "-" and len(ep) == 2:
                planes[17, 0, ord(ep[0]) - ord("a")] = 1.0
            return planes

        @staticmethod
        def move_to_index(move):
            idx = move.from_square * 64 + move.to_square
            if move.promotion:
                prom_map = {chess.KNIGHT: 1, chess.BISHOP: 2, chess.ROOK: 3}
                idx += 4096 * prom_map.get(move.promotion, 0)
            return idx

        def predict_distribution(self, fen, rating, legal_moves):
            """Return Nova's probability vector (length 16384) over legal moves.

            Illegal move indices are masked to zero. Returns None if no legal
            move receives nonzero probability.
            """
            session = self._ensure_session()
            pos = self.fen_to_planes(fen)[np.newaxis]
            if rating is not None:
                rating_norm = (rating - 800) / (2700 - 800)
                rating_norm = max(0.0, min(1.0, rating_norm))
            else:
                rating_norm = 0.5
            cond = np.array([[rating_norm, self.classical, self.aggression]], dtype=np.float32)
            logits, = session.run(None, {"positions": pos, "conditioning": cond})
            logits = logits[0]

            legal = np.zeros(16384, dtype=bool)
            for mv in legal_moves:
                legal[self.move_to_index(mv)] = True
            masked = np.where(legal, logits, -1e9)
            shifted = masked - masked.max()
            probs = np.exp(shifted)
            probs *= legal
            total = probs.sum()
            if total <= 0:
                return None
            probs /= total
            return probs

        def sample_move(
            self,
            probs,
            legal_moves,
            *,
            temperature=1.0,
            top_p=1.0,
            blunder_rate=0.0,
            rng,
        ):
            """Sample a move from Nova's policy with temperature, top-p, and blunder rate.

            Returns a (chess.Move, was_blunder) tuple.

            - temperature: 0 = greedy; <1 sharpens (more confident);
              >1 flattens (more random).
            - top_p: nucleus sampling — keep only the smallest set of top moves
              whose cumulative probability exceeds this threshold. 1.0 = no
              filtering.
            - blunder_rate: probability of replacing the sampled move with a
              uniformly random legal move, simulating an outright mistake.
            """
            if not legal_moves:
                raise ValueError("legal_moves is empty")

            # 1. Optional outright blunder: pick a uniformly random legal move.
            if blunder_rate > 0 and rng.random() < blunder_rate:
                return rng.choice(legal_moves), True

            # 2. Build a probability array aligned with legal_moves.
            p = np.array(
                [probs[self.move_to_index(mv)] for mv in legal_moves],
                dtype=np.float64,
            )

            # 3. Greedy mode (T=0): always pick the most probable legal move.
            if temperature == 0:
                best_i = max(range(len(legal_moves)), key=lambda i: p[i])
                return legal_moves[best_i], False

            # 4. Apply temperature (T<1 sharpens, T>1 flattens).
            if temperature != 1.0:
                logits = np.log(np.clip(p, 1e-12, 1.0)) / temperature
                logits -= logits.max()
                p = np.exp(logits)
                p /= p.sum()

            # 5. Top-p / nucleus filtering.
            if top_p < 1.0:
                order = np.argsort(p)[::-1]
                cumsum = np.cumsum(p[order])
                n_keep = max(1, int((cumsum < top_p).sum()) + 1)
                kept = order[:n_keep]
                p = p[kept]
                p /= p.sum()
                legal_moves = [legal_moves[i] for i in kept]

            # 6. Weighted sample from the (possibly filtered) distribution.
            i = int(rng.choices(range(len(legal_moves)), weights=p, k=1)[0])
            return legal_moves[i], False

    return NovaPredictor(model_path, classical, aggression)


def _make_handler(
    nova_model,
    rng: random.Random,
    min_wait: float,
    max_wait: float,
    elo: int,
    temperature: float,
    top_p: float,
    blunder_rate: float,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler with Nova configuration."""

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
            wait_time = rng.uniform(min_wait, max_wait)
            print(f"  [nova] thinking for {wait_time:.1f}s...", flush=True)
            time.sleep(wait_time)

            # Get Nova's full policy distribution over legal moves.
            legal_moves = list(board.legal_moves)
            probs = nova_model.predict_distribution(
                fen, rating=elo, legal_moves=legal_moves
            )
            if probs is None:
                self._send_json(500, {"error": "nova failed to produce moves"})
                return

            # Print top 5 raw moves from Nova (for debugging)
            top5 = sorted(
                [(mv, float(probs[nova_model.move_to_index(mv)])) for mv in legal_moves],
                key=lambda x: -x[1]
            )[:5]
            print("  [nova] top 5:", flush=True)
            for mv, p in top5:
                san = board.san(mv)
                print(f"    {san}: {p*100:.1f}%", flush=True)

            # Sample a move with temperature / top-p / blunder-rate.
            move, was_blunder = nova_model.sample_move(
                probs,
                legal_moves,
                temperature=temperature,
                top_p=top_p,
                blunder_rate=blunder_rate,
                rng=rng,
            )
            move_san = board.san(move)
            p_chosen = float(probs[nova_model.move_to_index(move)]) * 100
            if was_blunder:
                print(
                    f"  [nova] BLUNDER: {move_san} (p={p_chosen:.1f}%, "
                    f"elo={elo}, T={temperature}, top_p={top_p})",
                    flush=True,
                )
            else:
                print(
                    f"  [nova] chose: {move_san} (p={p_chosen:.1f}%, "
                    f"elo={elo}, T={temperature}, top_p={top_p})",
                    flush=True,
                )

            self._send_json(200, {"san": move_san})

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
        prog="chess-tui-nova",
        description=(
            "Nova-powered network player server for chess-tui. "
            "Uses the Nova chess predictor to play moves at different ELO levels."
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
        help="ELO level to condition Nova on (required, 800-2700)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help=(
            "sampling temperature (>=0). 0 = greedy; 1.0 = Nova's natural "
            "distribution; <1 sharpens (more confident); >1 flattens (more random). "
            "Default: 1.0"
        ),
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=1.0,
        help=(
            "nucleus sampling cutoff (0, 1]. Keep only the smallest set of top "
            "moves whose cumulative probability exceeds this value. "
            "1.0 = no filtering. Default: 1.0"
        ),
    )
    parser.add_argument(
        "--blunder-rate",
        type=float,
        default=0.0,
        help=(
            "probability of replacing the sampled move with a uniformly random "
            "legal move, to simulate outright mistakes (0.0-1.0). Default: 0.0"
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
    parser.add_argument(
        "--classical",
        type=float,
        default=0.5,
        help="classical vs neural network weight (0.0-1.0, default: 0.5)",
    )
    parser.add_argument(
        "--aggression",
        type=float,
        default=0.5,
        help="aggression level (0.0-1.0, default: 0.5)",
    )
    args = parser.parse_args(argv)

    # Validate sampling-knob ranges.
    if args.temperature < 0:
        parser.error("--temperature must be >= 0")
    if not (0.0 < args.top_p <= 1.0):
        parser.error("--top-p must be in (0.0, 1.0]")
    if not (0.0 <= args.blunder_rate <= 1.0):
        parser.error("--blunder-rate must be in [0.0, 1.0]")

    # Load Nova config from engines.json
    config = _load_engines_config()
    nova_config = config.get("nova")
    if not nova_config:
        print("Error: 'nova' not found in engines.json", file=sys.stderr)
        sys.exit(1)

    model_path = os.path.expanduser(nova_config.get("path", ""))
    classical = args.classical
    aggression = args.aggression

    if not os.path.exists(model_path):
        print(f"Error: Nova model not found at {model_path}", file=sys.stderr)
        print(
            "Download with: huggingface-cli download novachess/novachess-engine nova_v3b.onnx",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Nova
    print(f"Loading Nova model from {model_path}...", flush=True)
    nova_model = _load_nova_predictor(model_path, classical, aggression)
    print("Nova model loaded.", flush=True)

    # Create RNG
    rng = random.Random()

    handler = _make_handler(
        nova_model=nova_model,
        rng=rng,
        min_wait=args.min_wait,
        max_wait=args.max_wait,
        elo=args.elo,
        temperature=args.temperature,
        top_p=args.top_p,
        blunder_rate=args.blunder_rate,
    )

    server = ThreadingHTTPServer((args.host, args.port), handler)
    from .host import describe_listen
    print(
        f"chess-tui nova server listening on {describe_listen(args.host, args.port)}\n"
        f"  Model: Nova\n"
        f"  ELO: {args.elo}\n"
        f"  Temperature: {args.temperature}\n"
        f"  Top-p: {args.top_p}\n"
        f"  Blunder rate: {args.blunder_rate}\n"
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
