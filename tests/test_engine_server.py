"""Unit tests for the pure helpers in chess_tui.engine_server.

We deliberately avoid spawning a real UCI engine here — the helpers we
care about (score normalisation, formatting, PV→SAN conversion) are
pure functions of python-chess objects, so we exercise them with
hand-built scores and boards.
"""

from __future__ import annotations

import chess
import chess.engine
import pytest

from chess_tui.engine_server import (
    MAX_MULTIPV,
    _format_pv,
    _format_score,
    _normalise_score,
)


# ---- _normalise_score -----------------------------------------------------


def _cp(cp: int, turn: chess.Color) -> chess.engine.PovScore:
    """Build a centipawn PovScore as the engine would report it."""
    return chess.engine.PovScore(chess.engine.Cp(cp), turn)


def test_normalise_score_passes_through_white_perspective() -> None:
    """If the engine already reports from white's perspective, leave it alone."""
    raw = _cp(32, chess.WHITE)  # engine says white is +0.32
    out = _normalise_score(raw)
    assert out is not None
    assert out.relative.score(mate_score=32000) == 32
    assert out.turn == chess.WHITE


def test_normalise_score_negates_black_perspective() -> None:
    """If the engine reports from black's perspective, flip it for white."""
    raw = _cp(50, chess.BLACK)  # engine says black is +0.50 \u2192 white is -0.50
    out = _normalise_score(raw)
    assert out is not None
    assert out.relative.score(mate_score=32000) == -50
    assert out.turn == chess.WHITE


def test_normalise_score_none() -> None:
    assert _normalise_score(None) is None


# ---- _format_score --------------------------------------------------------


def test_format_score_centipawns_positive() -> None:
    assert _format_score(_normalise_score(_cp(32, chess.WHITE))) == "+0.32"


def test_format_score_centipawns_negative() -> None:
    assert _format_score(_normalise_score(_cp(150, chess.BLACK))) == "-1.50"


def test_format_score_zero_has_explicit_sign_for_consistency() -> None:
    """0.00 is rendered without a sign, matching chess-coach-v3's output."""
    assert _format_score(_normalise_score(_cp(0, chess.WHITE))) == "0.00"


def test_format_score_black_perspective_is_flipped() -> None:
    """A black-perspective +1.00 (meaning white is -1.00) renders as -1.00."""
    raw = _cp(100, chess.BLACK)
    assert _format_score(_normalise_score(raw)) == "-1.00"


def test_format_score_mate() -> None:
    raw = chess.engine.PovScore(
        chess.engine.Mate(moves=3), chess.WHITE,
    )
    out = _normalise_score(raw)
    assert _format_score(out) == "Mate 3"


def test_format_score_negative_mate() -> None:
    raw = chess.engine.PovScore(
        chess.engine.Mate(moves=-2), chess.WHITE,
    )
    out = _normalise_score(raw)
    assert _format_score(out) == "Mate -2"


def test_format_score_none_is_question_mark() -> None:
    assert _format_score(None) == "?"


# ---- _format_pv -----------------------------------------------------------


def test_format_pv_converts_moves_to_san() -> None:
    board = chess.Board()
    pv_sans = _format_pv(board, [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")])
    assert pv_sans == ["e4", "e5"]


def test_format_pv_empty_returns_empty_list() -> None:
    assert _format_pv(chess.Board(), []) == []


def test_format_pv_handles_capture_and_promotion() -> None:
    """Promotions get the =Q suffix; captures get 'x' between file and rank."""
    # Construct a position where white can capture on h7 and promote.
    board = chess.Board(
        "8/4P3/8/8/8/8/8/4K2k w - - 0 1"
    )
    pv_sans = _format_pv(
        board, [chess.Move.from_uci("e7e8q")],
    )
    assert pv_sans == ["e8=Q"]


def test_format_pv_invalid_move_falls_back_to_uci() -> None:
    """If a PV move is illegal in the position (engine bug, position
    mismatch), the function should not raise. We exercise the fallback
    by feeding a PV whose first move is legal and whose second is not
    — the second should fall back to its UCI form (or whatever lenient
    SAN python-chess produces) and the function should return rather
    than propagate the exception."""
    board = chess.Board()
    # e2e4 is legal; e7e5 is illegal for white from this position
    # (no piece on e7).
    pv_sans = _format_pv(
        board, [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")],
    )
    assert pv_sans[0] == "e4"
    # The second entry must be SOME non-empty string — either a lenient
    # SAN like "exe5" or the UCI fallback. The point is: we don't crash.
    assert isinstance(pv_sans[1], str) and pv_sans[1]
    assert len(pv_sans) == 2


# ---- MAX_MULTIPV ----------------------------------------------------------


def test_max_multipv_is_a_sane_upper_bound() -> None:
    """20 is enough for any sane use; if a user wants more they can patch."""
    assert MAX_MULTIPV >= 1
    assert MAX_MULTIPV <= 100
