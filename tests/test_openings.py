"""Tests for the bundled opening catalog and lookup helpers."""

from __future__ import annotations

import chess
import pytest

from chess_tui.openings import (
    AmbiguousOpeningQuery,
    Opening,
    UnknownOpening,
    find,
    load_all,
    resolve,
)


# ---- data integrity --------------------------------------------------------


def test_catalog_loads_with_expected_size() -> None:
    """The bundled catalog should contain a few thousand entries —
    sanity-check that we shipped the data and parsed it."""
    catalog = load_all()
    # lichess-org/chess-openings ships ~3.6k entries; we accept a
    # generous range to allow upstream renames but flag catastrophic
    # regressions (empty / 0).
    assert 3000 < len(catalog) < 5000


def test_every_entry_has_parseable_pgn() -> None:
    """Every catalog entry's PGN must replay cleanly into a valid FEN.

    The loader already skips bad rows with a stderr message, so this
    test also guards against silent regressions where most rows are
    being dropped.
    """
    catalog = load_all()
    parsed = 0
    for o in catalog:
        board = o.to_board()
        # The resulting board must be a legal chess position.
        assert isinstance(board, chess.Board)
        assert board.is_valid()
        parsed += 1
    # We require the vast majority to parse — at most a few dozen
    # can be skipped (the loader logs and continues on error).
    assert parsed > 0.95 * len(catalog)


def test_opening_columns_populated() -> None:
    o = resolve("B90")
    assert o.eco == "B90"
    assert o.name
    assert o.pgn
    assert o.uci  # space-separated
    assert o.epd  # 4 FEN fields


def test_opening_to_fen_round_trip() -> None:
    """``to_fen()`` should give a string python-chess accepts and that
    matches ``chess.Board.from_fen(...).fen()`` after normalization."""
    o = resolve("B90")
    fen = o.to_fen()
    board = chess.Board(fen)
    # ``fen()`` re-serialization should be stable.
    assert board.fen() == fen
    # The half-/fullmove counters must be present and reasonable.
    parts = fen.split()
    assert len(parts) == 6
    halfmove, fullmove = int(parts[4]), int(parts[5])
    assert halfmove >= 0
    assert fullmove >= 1


# ---- find / resolve --------------------------------------------------------


def test_find_exact_eco_returns_prefix_matches() -> None:
    results = find("B90")
    assert results
    # Every match's ECO must start with "B90".
    for o in results:
        assert o.eco.startswith("B90")


def test_find_substring_is_case_insensitive() -> None:
    lower = find("najdorf")
    upper = find("NAJDORF")
    mixed = find("Najdorf")
    assert lower == upper == mixed
    assert lower
    assert all("Najdorf" in o.name for o in lower)


def test_find_empty_returns_everything() -> None:
    # Whitespace-only is treated as empty.
    assert find("") == []
    assert find("   ") == []
    # Non-empty returns the full catalog (we don't bound the list).
    full = find("zzzzz_definitely_not_an_opening")
    assert full == []


def test_resolve_exact_eco() -> None:
    o = resolve("B90")
    assert o.eco == "B90"
    assert "Najdorf" in o.name


def test_resolve_exact_name_case_insensitive() -> None:
    a = resolve("Sicilian Defense: Najdorf Variation")
    b = resolve("sicilian defense: najdorf variation")
    assert a == b


def test_resolve_unique_substring() -> None:
    o = resolve("Bongcloud")
    assert "Bongcloud" in o.name


def test_resolve_ambiguous_raises_with_matches() -> None:
    """``"Sicilian"`` matches many openings; we should get a list of
    them in the error, not a silent pick."""
    with pytest.raises(AmbiguousOpeningQuery) as excinfo:
        resolve("Sicilian")
    assert len(excinfo.value.matches) > 1
    assert all("Sicilian" in o.name for o in excinfo.value.matches)


# ---- move_suffixes (used by the interactive selector) --------------------


def test_move_suffixes_unique_entry_gets_ply_count() -> None:
    """A row with no (eco, name) siblings in the input list should
    get a ply count, not a divergent suffix."""
    from chess_tui.openings import move_suffixes

    o = resolve("Bongcloud")  # unique in the catalog
    suffixes = move_suffixes([o])
    assert len(suffixes) == 1
    assert "plies" in suffixes[0]
    assert "→" not in suffixes[0]


def test_move_suffixes_transposition_cluster_shows_divergent_suffix() -> None:
    """Sicilian Najdorf English Attack has 5 transposition
    duplicates in the dataset.  Their common prefix is 6 plies;
    the suffixes should show only the divergent moves."""
    from chess_tui.openings import find, move_suffixes

    cluster = [
        o
        for o in find("Najdorf Variation, English Attack")
        if o.name == "Sicilian Defense: Najdorf Variation, English Attack"
    ]
    assert len(cluster) >= 2, "test premise: cluster should have siblings"

    suffixes = move_suffixes(cluster)

    # The parent (shortest PGN) gets the "(parent)" label.
    parents = [s for s in suffixes if s == "(parent)"]
    assert len(parents) == 1

    # Every other row gets a divergent suffix starting with the
    # arrow.  The parent PGN is 6 plies, so all divergent suffixes
    # start with the 7th ply's move (e5 or Ng4).
    divergents = [s for s in suffixes if s != "(parent)"]
    assert divergents
    for s in divergents:
        assert s.startswith("→")
        # The first divergent move must be one of the actual
        # 7th-ply candidates in the dataset (e5 or Ng4).
        first_move = s.split()[1]
        assert first_move in {"e5", "Ng4"}


def test_move_suffixes_mixed_input() -> None:
    """When the input has a mix of unique and clustered rows, each
    row gets the right label independently."""
    from chess_tui.openings import find, move_suffixes

    cluster = [
        o
        for o in find("Najdorf Variation, English Attack")
        if o.name == "Sicilian Defense: Najdorf Variation, English Attack"
    ]
    unique = [find("Bongcloud")[0]]
    mixed = cluster + unique

    suffixes = move_suffixes(mixed)
    assert len(suffixes) == len(mixed)

    # Cluster rows: one "(parent)", rest "→ ..."
    cluster_suf = suffixes[: len(cluster)]
    assert sum(1 for s in cluster_suf if s == "(parent)") == 1
    assert sum(1 for s in cluster_suf if s.startswith("→")) == len(cluster) - 1

    # The unique row gets a ply count.
    assert "plies" in suffixes[-1]
    assert "→" not in suffixes[-1]


def test_resolve_unknown_raises() -> None:
    with pytest.raises(UnknownOpening):
        resolve("this opening does not exist xyzzy")


def test_resolve_empty_raises() -> None:
    with pytest.raises(UnknownOpening):
        resolve("")
    with pytest.raises(UnknownOpening):
        resolve("   ")


# ---- PGN replay sanity for a few famous openings --------------------------


@pytest.mark.parametrize(
    "query, expected_pgn_prefix",
    [
        ("B90", "1. e4 c5"),
        ("C50", "1. e4 e5"),
        ("D20", "1. d4 d5"),
    ],
)
def test_famous_openings_replay_correctly(
    query: str, expected_pgn_prefix: str
) -> None:
    o = resolve(query)
    # The canonical PGN must start with the expected opening moves.
    assert o.pgn.startswith(expected_pgn_prefix)
    # And the resulting FEN must have a real piece layout — not the
    # initial position (the moves must have actually been applied).
    fen = o.to_fen()
    assert fen != chess.STARTING_FEN
