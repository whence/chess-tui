"""Tests for the ``--opening`` and ``--list-openings`` CLI flags.

We don't actually start a TUI here (that would block on stdin); we
patch :class:`ChessApp` so :func:`main` runs through the argument
parsing, state construction, and opening-resolution paths and then
exits cleanly.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import chess
import pytest

from chess_tui import app as app_module
from chess_tui.openings import resolve as resolve_opening


# Patch the run() entry point so it never actually starts Textual.
@pytest.fixture
def patched_run() -> None:
    with patch.object(app_module.ChessApp, "run", lambda self: None):
        yield


# ---- --list-openings -------------------------------------------------------


def test_list_openings_no_arg_prints_all(capsys, patched_run) -> None:
    rc = app_module.main(["--list-openings"])
    assert rc is None
    captured = capsys.readouterr()
    # The catalog is ~3.6k entries; we just verify we printed a
    # reasonable sample of the unique-ECO codes and they look like
    # chess openings.
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) > 3000
    # Lines look like "B90  Sicilian Defense: Najdorf Variation".
    assert any(l.startswith("B90") for l in lines)
    assert any(l.startswith("A00") for l in lines)


def test_list_openings_with_substring_filters(capsys, patched_run) -> None:
    rc = app_module.main(["--list-openings", "najdorf"])
    assert rc is None
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    # Every line should mention Najdorf.
    assert lines
    assert all("Najdorf" in l for l in lines)


def test_list_openings_exits_before_construction(capsys, patched_run) -> None:
    """``--list-openings`` should print and return without ever
    trying to construct a BoardState — even if --fen / --opening are
    also passed (we don't actually allow that combo, but we do want
    the discovery helper to take precedence over the normal flow)."""
    rc = app_module.main(["--list-openings", "italian"])
    assert rc is None


# ---- --opening -------------------------------------------------------------


def test_opening_flag_resolves_to_state(patched_run) -> None:
    captured_state: dict = {}

    real_init = app_module.ChessApp.__init__

    def spy_init(self, *args, **kwargs):
        captured_state["state"] = kwargs.get("state")
        captured_state["opening"] = kwargs.get("opening")
        real_init(self, *args, **kwargs)

    with patch.object(app_module.ChessApp, "__init__", spy_init):
        rc = app_module.main(["--opening", "B90"])

    assert rc is None
    state = captured_state["state"]
    opening = captured_state["opening"]
    assert state is not None
    assert opening is not None
    # The starting board must equal the opening's replayed position.
    assert state.fen() == opening.to_fen()
    # And the FEN must not be the standard initial position.
    assert state.fen() != chess.STARTING_FEN
    # CRITICAL: the SAN history must be populated.  Network players
    # like Maia-3 with --use-history need the move list to seed the
    # transformer's position history; an empty history would silently
    # fall back to no-context mode.
    history = state.san_history()
    assert history == [
        "e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6",
    ]


def test_opening_flag_uses_name_lookup(patched_run) -> None:
    captured: dict = {}
    real_init = app_module.ChessApp.__init__

    def spy(self, *args, **kwargs):
        captured["opening"] = kwargs.get("opening")
        real_init(self, *args, **kwargs)

    with patch.object(app_module.ChessApp, "__init__", spy):
        app_module.main(
            ["--opening", "Sicilian Defense: Najdorf Variation"]
        )

    assert captured["opening"].eco == "B90"


def test_opening_and_fen_are_mutually_exclusive(
    capsys, patched_run
) -> None:
    """Passing both should exit non-zero with an error message rather
    than picking one silently."""
    with pytest.raises(SystemExit) as excinfo:
        app_module.main([
            "--opening", "B90",
            "--fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        ])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_opening_unknown_query_exits_with_error(
    capsys, patched_run
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        app_module.main(["--opening", "this is not a real opening xyz"])
    assert excinfo.value.code == 1
    assert "no opening matches" in capsys.readouterr().err


def test_opening_ambiguous_query_passes_choices_to_app(patched_run) -> None:
    """An ambiguous substring used to exit 1; now it hands the list
    of matches to the TUI which shows an interactive selector.  We
    can't drive the selector here (that needs the pilot), but we
    can verify that main() resolves the candidates and passes them
    to ChessApp without exiting."""
    captured: dict = {}
    real_init = app_module.ChessApp.__init__
    app_ref: dict = {}

    def spy(self, *args, **kwargs):
        captured["state"] = kwargs.get("state")
        captured["opening"] = kwargs.get("opening")
        # Hold a reference so we can read the post-__init__ attrs
        # that main() sets when there's an ambiguous query.
        app_ref["app"] = self
        real_init(self, *args, **kwargs)

    with patch.object(app_module.ChessApp, "__init__", spy):
        app_module.main(["--opening", "Sicilian"])

    # No opening resolved yet — the user picks one in the modal.
    assert captured["opening"] is None
    # The state is the default (startpos) until the user picks; the
    # modal will replace it via _on_opening_chosen.
    assert captured["state"] is None
    # The CLI set the candidates on the instance after __init__.
    app = app_ref["app"]
    choices = getattr(app, "_opening_choices", None)
    assert choices is not None
    assert len(choices) > 1
    assert all("Sicilian" in o.name for o in choices)
    # And the modal knows what to label itself with.
    assert getattr(app, "_opening_query", None) == "Sicilian"


def test_no_opening_no_fen_uses_default(patched_run) -> None:
    """Without either flag, the TUI should start from the standard
    position with no opening label."""
    captured: dict = {}
    real_init = app_module.ChessApp.__init__

    def spy(self, *args, **kwargs):
        captured["state"] = kwargs.get("state")
        captured["opening"] = kwargs.get("opening")
        real_init(self, *args, **kwargs)

    with patch.object(app_module.ChessApp, "__init__", spy):
        app_module.main([])

    assert captured["opening"] is None
    # state is None when neither --fen nor --opening is given — the
    # app falls back to its own default BoardState() in __init__.
    assert captured["state"] is None
