"""Nova-powered network player server for chess-tui.

Run with ``uv run chess-tui-nova [options]``. Uses the Nova chess predictor
to play moves at different ELO levels with a credit system for variability.

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

        def predict_topk(self, fen, k=3, rating=None, legal_moves=None, board=None):
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

            # Mask legal moves and apply softmax
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

            # Get top-k
            scored = []
            for mv in legal_moves:
                p = probs[self.move_to_index(mv)]
                scored.append((p, mv))
            scored.sort(key=lambda x: -x[0])

            result = []
            for p, mv in scored[:k]:
                san = board.san(mv) if board else mv.uci()
                result.append({"move": san, "p": round(float(p) * 100, 1)})
            return result

    return NovaPredictor(model_path, classical, aggression)


class NovaPlayer:
    """Manages ELO selection with credit system."""

    def __init__(
        self,
        elo: int,
        elo_low: int | None = None,
        elo_low_credit: int = 1,
        elo_high: int | None = None,
        elo_high_credit: int = 1,
    ):
        self.elo = elo
        self.elo_low = elo_low if elo_low is not None else elo
        self.elo_high = elo_high if elo_high is not None else elo
        self.elo_low_credit = elo_low_credit
        self.elo_high_credit = elo_high_credit
        self._elo_low_remaining = elo_low_credit
        self._elo_high_remaining = elo_high_credit

    def reset_credits(self) -> None:
        """Reset credits to initial values."""
        self._elo_low_remaining = self.elo_low_credit
        self._elo_high_remaining = self.elo_high_credit

    def choose_elo(self, rng: random.Random, verbose: bool = False) -> tuple[int, str]:
        """Choose which ELO level to use based on credits.

        Returns (elo, source) where source is 'low', 'high', or 'base'.
        """
        # Calculate probabilities
        low_prob = self._elo_low_remaining / 50 if self._elo_low_remaining > 0 else 0
        high_prob = self._elo_high_remaining / 50 if self._elo_high_remaining > 0 else 0
        base_prob = 1.0 - low_prob - high_prob

        # Roll the dice
        roll = rng.random()

        if roll < low_prob and self._elo_low_remaining > 0:
            self._elo_low_remaining -= 1
            if verbose:
                print(
                    f"  [nova] roll={roll:.3f} < {low_prob:.3f} → elo_low={self.elo_low} "
                    f"(remaining: {self._elo_low_remaining}/{self.elo_low_credit})",
                    flush=True,
                )
            return self.elo_low, "low"
        elif roll < low_prob + high_prob and self._elo_high_remaining > 0:
            self._elo_high_remaining -= 1
            if verbose:
                print(
                    f"  [nova] roll={roll:.3f} < {low_prob + high_prob:.3f} → elo_high={self.elo_high} "
                    f"(remaining: {self._elo_high_remaining}/{self.elo_high_credit})",
                    flush=True,
                )
            return self.elo_high, "high"
        else:
            if verbose:
                print(
                    f"  [nova] roll={roll:.3f} → elo_base={self.elo}",
                    flush=True,
                )
            return self.elo, "base"


def _make_handler(
    nova_model,
    player: NovaPlayer,
    rng: random.Random,
    min_wait: float,
    max_wait: float,
    verbose: bool,
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
            wait_time = rng.uniform(min_wait, max_wait)
            if verbose:
                print(f"  [nova] thinking for {wait_time:.1f}s...", flush=True)
            time.sleep(wait_time)

            # Choose ELO level
            chosen_elo, source = player.choose_elo(rng, verbose=verbose)

            # Get move from Nova
            legal_moves = list(board.legal_moves)
            top_moves = nova_model.predict_topk(
                fen, k=5, rating=chosen_elo, legal_moves=legal_moves, board=board
            )

            if not top_moves:
                self._send_json(500, {"error": "nova failed to produce moves"})
                return

            # Pick the top move (highest probability)
            move_san = top_moves[0]["move"]
            if verbose:
                print(f"  [nova] chose: {move_san} (elo={chosen_elo}, source={source})", flush=True)
                print(f"  [nova] top moves: {top_moves[:3]}", flush=True)

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
        "port",
        nargs="?",
        type=int,
        default=8080,
        help="port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--elo",
        type=int,
        required=True,
        help="base ELO level (required)",
    )
    parser.add_argument(
        "--elo-low",
        type=int,
        default=None,
        help="lower ELO level (default: same as --elo)",
    )
    parser.add_argument(
        "--elo-low-credit",
        type=int,
        default=1,
        help="credit count for elo-low (default: 1)",
    )
    parser.add_argument(
        "--elo-high",
        type=int,
        default=None,
        help="higher ELO level (default: same as --elo)",
    )
    parser.add_argument(
        "--elo-high-credit",
        type=int,
        default=1,
        help="credit count for elo-high (default: 1)",
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
    verbose = args.verbose

    # Load Nova config from engines.json
    config = _load_engines_config()
    nova_config = config.get("nova")
    if not nova_config:
        print("Error: 'nova' not found in engines.json", file=sys.stderr)
        sys.exit(1)

    model_path = os.path.expanduser(nova_config.get("path", ""))
    classical = nova_config.get("classical", 0.5)
    aggression = nova_config.get("aggression", 0.5)

    if not os.path.exists(model_path):
        print(f"Error: Nova model not found at {model_path}", file=sys.stderr)
        print(
            "Download with: huggingface-cli download novachess/novachess-engine nova_v3b.onnx",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Nova
    if verbose:
        print(f"Loading Nova model from {model_path}...", flush=True)
    nova_model = _load_nova_predictor(model_path, classical, aggression)
    if verbose:
        print("Nova model loaded.", flush=True)

    # Create player with credits
    player = NovaPlayer(
        elo=args.elo,
        elo_low=args.elo_low,
        elo_low_credit=args.elo_low_credit,
        elo_high=args.elo_high,
        elo_high_credit=args.elo_high_credit,
    )

    # Create RNG with random seed
    rng = random.Random()

    handler = _make_handler(
        nova_model=nova_model,
        player=player,
        rng=rng,
        min_wait=args.min_wait,
        max_wait=args.max_wait,
        verbose=args.verbose,
    )

    # Print config
    elo_low_desc = f"{args.elo_low} (credit: {args.elo_low_credit})" if args.elo_low != args.elo else "same as --elo"
    elo_high_desc = f"{args.elo_high} (credit: {args.elo_high_credit})" if args.elo_high != args.elo else "same as --elo"

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(
        f"chess-tui nova server listening on http://127.0.0.1:{args.port}\n"
        f"  Model: Nova\n"
        f"  Base ELO: {args.elo}\n"
        f"  Low ELO: {elo_low_desc}\n"
        f"  High ELO: {elo_high_desc}\n"
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
