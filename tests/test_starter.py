"""Tests for the cmux starter wizard (chess_tui.starter).

The opening step now launches a Textual picker, so :func:`pick_opening`
is monkeypatched (the test seam) to return a fixed result instead of
running Textual. The rest of the wizard (opponent side, Nova knobs,
observer, confirm) still uses plain ``input()``, driven by the
``feed`` helper. cmux itself is never touched (create_workspace /
name_tabs are stubbed out).
"""

from __future__ import annotations

import chess_tui.starter as starter


def feed(monkeypatch, answers: list[str]) -> None:
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))


def no_opening(monkeypatch) -> None:
    """Stub the opening picker to return a standard game (None)."""
    monkeypatch.setattr(starter, "pick_opening", lambda: None)


def pick_opening(monkeypatch, opening) -> None:
    """Stub the opening picker to return *opening* (an openings.Opening)."""
    monkeypatch.setattr(starter, "pick_opening", lambda: opening)


# Fully-default flow with a Nova opponent: side, ELO, temperature,
# top-p, blunder rate, classical, aggression, observer?, confirm.
# (The opening query is no longer read from input — see no_opening.)
ALL_DEFAULTS = ["", "", "", "", "", "", "", "", ""]

# Knob prompts after ELO (temperature, top-p, blunder, classical, aggression).
KNOB_DEFAULTS = ["", "", "", "", ""]


def test_configure_standard_game_defaults(monkeypatch) -> None:
    """Default flow: you play white, Nova black, ELO 1500, starter-level
    temperature (0.1) and blunder rate (0.015)."""
    monkeypatch.setattr(starter, "free_port", lambda: 18081)
    no_opening(monkeypatch)
    feed(monkeypatch, ALL_DEFAULTS)
    setup = starter.configure()
    assert setup.opening is None
    # Empty answer on the side screen takes the default: you play white.
    assert setup.opponent_color == "black"
    assert setup.nova.elo == 1500
    assert setup.nova.temperature == 0.1
    assert setup.nova.blunder_rate == 0.015
    assert setup.nova_port == 18081


def test_configure_no_opponent(monkeypatch) -> None:
    no_opening(monkeypatch)
    feed(
        monkeypatch,
        [
            "3",  # you play both sides
            "",  # observer: none (default)
            "",  # confirm: yes
        ],
    )
    setup = starter.configure()
    assert setup.opponent_color is None
    assert setup.nova is None
    assert setup.nova_port is None
    assert setup.observer is None


def test_configure_with_observer_engine(monkeypatch) -> None:
    monkeypatch.setattr(starter, "free_port", lambda: 18082)
    monkeypatch.setattr(
        starter, "_available_engines", lambda: ["plentychess", "stockfish", "dragon"]
    )
    no_opening(monkeypatch)
    feed(
        monkeypatch,
        [
            "3",  # you play both sides
            "y",  # add observer
            "2",  # observer: plentychess engine
            "12",  # depth
            "",  # confirm: yes
        ],
    )
    setup = starter.configure()
    assert setup.observer is not None
    assert setup.observer.engine_name == "plentychess"
    assert setup.observer.depth == 12
    assert setup.observer_port == 18082
    cmd = starter.game_command(setup)
    assert "--observer http://127.0.0.1:18082" in cmd
    obs_cmd = starter.observer_command(setup)
    assert "chess-tui-engine" in obs_cmd
    assert "--engine-name plentychess" in obs_cmd
    assert "--depth 12" in obs_cmd


def test_configure_with_observer_nova(monkeypatch) -> None:
    """Choosing 'Nova player' goes through the same settings as the
    opponent (ELO + knobs), and the observer command is a Nova server."""
    monkeypatch.setattr(starter, "free_port", lambda: 18082)
    no_opening(monkeypatch)
    feed(
        monkeypatch,
        [
            "3",  # you play both sides
            "y",  # add observer
            "1",  # observer: Nova player
            "1800",  # ELO
            *KNOB_DEFAULTS,
            "",  # confirm: yes
        ],
    )
    setup = starter.configure()
    assert isinstance(setup.observer, starter.NovaConfig)
    assert setup.observer.elo == 1800
    obs_cmd = starter.observer_command(setup)
    assert "chess-tui-nova" in obs_cmd
    assert "--port 18082" in obs_cmd
    assert "--elo 1800" in obs_cmd
    assert "chess-tui-engine" not in obs_cmd


def test_observer_defaults(monkeypatch) -> None:
    monkeypatch.setattr(starter, "_available_engines", lambda: ["stockfish"])
    feed(
        monkeypatch,
        [
            "2",  # observer: stockfish engine
            "",  # depth: default 20
        ],
    )
    obs = starter.configure_observer()
    assert obs.engine_name == "stockfish"
    assert obs.depth == 20


def test_configure_unique_opening_and_you_play_black(monkeypatch) -> None:
    import chess_tui.openings as openings

    monkeypatch.setattr(starter, "free_port", lambda: 18081)
    opening = openings.find("Four Knights Game: Italian Variation, Noa Gambit")[0]
    pick_opening(monkeypatch, opening)
    feed(
        monkeypatch,
        [
            "2",  # you play black, Nova white
            "2000",  # ELO
            *KNOB_DEFAULTS,
            "",  # observer: none (default)
            "",  # confirm: yes
        ],
    )
    setup = starter.configure()
    assert setup.opening is not None
    assert setup.opponent_color == "white"
    assert setup.nova.elo == 2000
    cmd = starter.game_command(setup)
    assert "--opening" in cmd
    assert "--white http://127.0.0.1:18081" in cmd
    assert "--black" not in cmd


def test_configure_decline_then_confirm(monkeypatch) -> None:
    """Answering 'n' at the summary loops back into the wizard."""
    no_opening(monkeypatch)
    feed(
        monkeypatch,
        [
            "3",  # you play both sides
            "n",  # confirm: decline -> reconfigure
            "3",  # you play both sides (second pass)
            "",  # confirm: yes
        ],
    )
    setup = starter.configure()
    assert setup.opening is None
    assert setup.opponent_color is None


def test_game_command_standard_game() -> None:
    setup = starter.Setup(opening=None, opponent_color=None)
    assert starter.game_command(setup) == "uv run chess-tui"


def test_game_command_unique_opening_is_quoted() -> None:
    import chess_tui.openings as openings

    opening = openings.find("Four Knights Game: Italian Variation, Noa Gambit")[0]
    setup = starter.Setup(opening=opening, opponent_color=None)
    cmd = starter.game_command(setup)
    assert "'Four Knights Game: Italian Variation, Noa Gambit'" in cmd
    assert "--silent" not in cmd


def test_nova_command_contains_knobs() -> None:
    setup = starter.Setup(
        opening=None,
        opponent_color="black",
        nova=starter.NovaConfig(elo=2200, temperature=0.8),
        nova_port=18081,
    )
    cmd = starter.nova_command(setup)
    assert "chess-tui-nova" in cmd
    assert "--host 127.0.0.1" in cmd
    assert "--port 18081" in cmd
    assert "--elo 2200" in cmd
    assert "--temperature 0.8" in cmd
    assert "--top-p 1.0" in cmd
    assert "--blunder-rate 0.015" in cmd
    assert "--classical 0.5" in cmd
    assert "--aggression 0.5" in cmd


def _patch_main(monkeypatch, ws, closed, created) -> None:
    """Stub cmux detection and workspace ops so main() runs hermetically."""
    monkeypatch.setattr(starter, "inside_cmux", lambda: True)
    monkeypatch.setattr(starter, "cmux_socket_responsive", lambda: True)
    monkeypatch.setattr(starter.shutil, "which", lambda _name: "/usr/bin/cmux")
    monkeypatch.setattr(starter, "find_workspace", lambda _name: ws)
    monkeypatch.setattr(starter, "close_workspace", lambda _ws: closed.append(_ws))
    monkeypatch.setattr(starter, "name_tabs", lambda _ref: None)
    monkeypatch.setattr(starter, "free_port", lambda: 18081)
    # main() prints a line that reads the cmux env vars directly even
    # though inside_cmux() is stubbed; set them so that line works.
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
    monkeypatch.setenv("CMUX_SURFACE_ID", "surf-test")
    # The opening picker is a Textual app; stub it to a standard game
    # so main()'s configure() wizard stays on plain input().
    no_opening(monkeypatch)

    def fake_create(setup):
        created.append(setup)
        return "workspace:99"

    monkeypatch.setattr(starter, "create_workspace", fake_create)


def _fake_workspace() -> starter.Workspace:
    return starter.Workspace(
        ref="workspace:42", id="ID", title="cmd", custom_title=starter.WORKSPACE_NAME, ports=(8080,)
    )


def test_main_declining_close_exits_without_creating(monkeypatch) -> None:
    ws = _fake_workspace()
    closed, created = [], []
    _patch_main(monkeypatch, ws, closed, created)
    feed(
        monkeypatch,
        [
            "n",  # close existing workspace? -> decline
        ],
    )
    assert starter.main() == starter.EXIT_USER_DECLINED
    assert closed == []
    assert created == []


def test_main_close_then_create(monkeypatch) -> None:
    ws = _fake_workspace()
    closed, created = [], []
    _patch_main(monkeypatch, ws, closed, created)
    feed(
        monkeypatch,
        [
            "",  # close existing workspace: default Y
            "3",  # you play both sides
            "",  # observer: none
            "",  # confirm: yes
        ],
    )
    assert starter.main() == starter.EXIT_OK
    assert closed == [ws]
    assert len(created) == 1
    assert created[0].opening is None
    assert created[0].opponent_color is None


def test_main_without_existing_workspace_goes_straight_to_wizard(monkeypatch) -> None:
    closed, created = [], []
    _patch_main(monkeypatch, None, closed, created)
    feed(
        monkeypatch,
        [
            "3",  # you play both sides
            "",  # observer: none
            "",  # confirm: yes
        ],
    )
    assert starter.main() == starter.EXIT_OK
    assert closed == []
    assert created[0].opponent_color is None


def test_main_nova_opponent_gets_port_and_wiring(monkeypatch) -> None:
    """Default opponent (you play white, ELO 1500) wires --black into the
    game and the Nova server into the opponent tab on the auto-picked port."""
    closed, created = [], []
    _patch_main(monkeypatch, None, closed, created)
    feed(monkeypatch, ALL_DEFAULTS)
    assert starter.main() == starter.EXIT_OK
    setup = created[0]
    assert setup.opponent_color == "black"
    assert setup.nova_port == 18081
    game = starter.game_command(setup)
    assert "--black http://127.0.0.1:18081" in game
