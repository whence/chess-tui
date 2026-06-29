"""Unit tests for the pure game logic in chess_tui.state."""

from __future__ import annotations

import chess
import pytest

from chess_tui.state import BoardState, IllegalMoveError


# ---- construction -----------------------------------------------------------


def test_default_state_is_standard_starting_position() -> None:
    state = BoardState()
    assert state.turn() == chess.WHITE
    assert state.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert state.piece_at(chess.E7) == chess.Piece(chess.PAWN, chess.BLACK)
    assert state.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK)
    assert state.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE)
    assert state.fullmove_number() == 1
    assert state.flipped is False


def test_from_fen_loads_position() -> None:
    state = BoardState.from_fen(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    )
    assert state.turn() == chess.WHITE
    assert state.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE)


def test_from_pgn_replays_moves() -> None:
    pgn = (
        "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *"
    )
    state = BoardState.from_pgn(pgn)
    # After 1.e4 e5 the pawn is on e4, after Nf3 Nc6 knights are developed, etc.
    assert state.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert state.piece_at(chess.E5) == chess.Piece(chess.PAWN, chess.BLACK)
    assert state.piece_at(chess.G1) is None  # knight moved
    assert state.piece_at(chess.F3) == chess.Piece(chess.KNIGHT, chess.WHITE)


def test_from_pgn_raises_on_empty() -> None:
    with pytest.raises(ValueError):
        BoardState.from_pgn("")


# ---- moves ------------------------------------------------------------------


def test_apply_san_pushes_move_and_flips_turn() -> None:
    state = BoardState()
    state.apply_san("e4")
    assert state.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert state.piece_at(chess.E2) is None
    assert state.turn() == chess.BLACK


def test_apply_san_rejects_illegal_move() -> None:
    state = BoardState()
    with pytest.raises(IllegalMoveError):
        state.apply_san("e5")  # white pawn can't reach e5 in one move


def test_apply_san_accepts_uci_fallback() -> None:
    state = BoardState()
    state.apply_san("e2e4")
    assert state.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)


def test_apply_san_rejects_empty_input() -> None:
    state = BoardState()
    with pytest.raises(IllegalMoveError):
        state.apply_san("   ")


def test_legal_moves_reflect_position() -> None:
    state = BoardState()
    moves = state.legal_moves()
    # 20 legal moves at the start (16 pawn + 4 knight).
    assert len(moves) == 20
    uci = {m.uci() for m in moves}
    assert "e2e4" in uci
    assert "g1f3" in uci


def test_legal_moves_from_filters_by_square() -> None:
    state = BoardState()
    moves = state.legal_moves_from(chess.E2)
    uci = {m.uci() for m in moves}
    assert uci == {"e2e3", "e2e4"}


def test_legal_moves_empty_when_no_piece() -> None:
    state = BoardState()
    assert state.legal_moves_from(chess.E4) == []


def test_ambiguous_san_requires_disambiguation() -> None:
    # Position where two knights can move to the same square.
    pgn = "1. Nf3 Nc6 2. Nbd2 *"
    state = BoardState.from_pgn(pgn)
    with pytest.raises(IllegalMoveError):
        state.apply_san("Nc4")  # ambiguous — which knight?


def test_checkmate_detected() -> None:
    # Fool's mate: 1.f3 e5 2.g4 Qh4#
    pgn = "1. f3 e5 2. g4 Qh4#"
    state = BoardState.from_pgn(pgn)
    assert state.is_checkmate()
    assert state.is_game_over()
    assert state.result() == "0-1"


# ---- flip -------------------------------------------------------------------


def test_flip_toggles_orientation() -> None:
    state = BoardState()
    assert state.flipped is False
    state.flip()
    assert state.flipped is True
    state.flip()
    assert state.flipped is False


def test_reset_clears_board_and_flip() -> None:
    state = BoardState()
    state.apply_san("e4")
    state.flip()
    state.reset()
    assert state.turn() == chess.WHITE
    assert state.flipped is False
    assert state.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)


# ---- display mapping --------------------------------------------------------


def test_square_at_unflipped_top_left_is_a8() -> None:
    state = BoardState()
    assert state.square_at(0, 0) == chess.A8
    assert state.square_at(0, 7) == chess.H8
    assert state.square_at(7, 0) == chess.A1
    assert state.square_at(7, 7) == chess.H1


def test_square_at_flipped_top_left_is_h1() -> None:
    state = BoardState(flipped=True)
    assert state.square_at(0, 0) == chess.H1
    assert state.square_at(0, 7) == chess.A1
    assert state.square_at(7, 0) == chess.H8
    assert state.square_at(7, 7) == chess.A8


def test_display_position_round_trip() -> None:
    for flipped in (False, True):
        state = BoardState(flipped=flipped)
        for square in chess.SQUARES:
            row, col = state.display_position(square)
            assert state.square_at(row, col) == square


def test_file_labels_unflipped() -> None:
    state = BoardState()
    assert [state.file_label(c) for c in range(8)] == list("abcdefgh")


def test_file_labels_flipped() -> None:
    state = BoardState(flipped=True)
    assert [state.file_label(c) for c in range(8)] == list("hgfedcba")


def test_rank_labels_unflipped() -> None:
    state = BoardState()
    assert [state.rank_label(r) for r in range(8)] == [str(i) for i in range(8, 0, -1)]


def test_rank_labels_flipped() -> None:
    state = BoardState(flipped=True)
    assert [state.rank_label(r) for r in range(8)] == [str(i) for i in range(1, 9)]


# ---- parse_display_square ---------------------------------------------------


def test_parse_display_square_unflipped() -> None:
    state = BoardState()
    assert state.parse_display_square("e2") == chess.E2
    assert state.parse_display_square("a1") == chess.A1
    assert state.parse_display_square("h8") == chess.H8


def test_parse_display_square_flipped_returns_same_square() -> None:
    """Algebraic labels are fixed to squares; flip is purely cosmetic."""
    flipped = BoardState(flipped=True)
    unflipped = BoardState(flipped=False)
    for label, expected in [
        ("a1", chess.A1),
        ("h1", chess.H1),
        ("a8", chess.A8),
        ("h8", chess.H8),
        ("e2", chess.E2),
        ("e7", chess.E7),
        ("d4", chess.D4),
    ]:
        assert flipped.parse_display_square(label) == expected
        assert unflipped.parse_display_square(label) == expected


def test_parse_display_square_rejects_garbage() -> None:
    state = BoardState()
    assert state.parse_display_square("") is None
    assert state.parse_display_square("e") is None
    assert state.parse_display_square("e9") is None
    assert state.parse_display_square("z2") is None
    assert state.parse_display_square("e2 extra") is None


def test_piece_at_corner_after_flip_still_returns_correct_piece() -> None:
    """Flipping only changes display orientation, not piece identities."""
    state = BoardState(flipped=True)
    # H1 holds a white rook; flipping doesn't change what's on the square.
    assert state.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE)
    assert state.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK)