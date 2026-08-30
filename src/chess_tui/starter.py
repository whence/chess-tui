#!/usr/bin/env python3
"""Starter for the "chess-tui-managed" cmux workspace.

Run from anywhere inside the repo (uses the project environment, so it
can import ``chess_tui.openings`` and friends directly)::

    uv run starter

Requirements:
  * Must run inside a cmux terminal (https://cmux.com/).
  * If a workspace named "chess-tui-managed" already exists, the user is
    asked (multiple-choice screen) to close it first — killing every
    process under it so ports cannot clash with a fresh setup.
    Declining exits the script.

Iteration 1 (main game only):
  * Interactive wizard for the game options, with sensible defaults:
    - opening: search + disambiguation choice screens over the bundled
      chess_tui.openings catalog (Enter = standard game)
  * Creates the 2-column workspace:
    - left column: the main game (uv run chess-tui ...)
    - right column: default shell (reserved for future iterations)

Iteration 2 (Nova opponent):
  * Side selection: you play white (default) / you play black / you
    play both (no opponent).
  * Nova options: ELO plus advanced tuning knobs. Starter-level
    defaults for temperature (0.1) and blunder rate (0.015) differ
    from the server's own flag defaults (1.0 / 0.0) — they are passed
    explicitly. The server runs in the "opponent" tab on an
    auto-picked free port (bound to 127.0.0.1), and the game connects
    via --black/--white URL.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chess_tui import openings

WORKSPACE_NAME = "chess-tui-managed"

EXIT_OK = 0
EXIT_NOT_IN_CMUX = 1
EXIT_USER_DECLINED = 2


# --------------------------------------------------------------------------
# cmux integration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Workspace:
    """One entry of ``cmux list-workspaces --json`` (relevant fields only)."""

    ref: str
    id: str
    title: str
    custom_title: str | None
    ports: tuple[int, ...]

    @classmethod
    def from_json(cls, payload: dict) -> Workspace:
        return cls(
            ref=str(payload.get("ref") or payload.get("id") or ""),
            id=str(payload.get("id") or ""),
            title=str(payload.get("title") or ""),
            custom_title=payload.get("custom_title"),
            ports=tuple(payload.get("listening_ports") or ()),
        )

    def display_name(self) -> str:
        return self.custom_title or self.title


def _cmux(*args: str, check: bool = True) -> str:
    """Run a cmux CLI command and return stdout."""
    proc = subprocess.run(
        ["cmux", *args], capture_output=True, text=True, check=check
    )
    return proc.stdout


def inside_cmux() -> bool:
    """cmux auto-sets these in every terminal it manages."""
    return bool(os.environ.get("CMUX_WORKSPACE_ID")) and bool(
        os.environ.get("CMUX_SURFACE_ID")
    )


def cmux_socket_responsive() -> bool:
    try:
        out = _cmux("ping", check=False)
    except FileNotFoundError:
        return False
    return "pong" in out.lower()


def list_workspaces() -> list[Workspace]:
    out = _cmux("list-workspaces", "--json")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [Workspace.from_json(w) for w in data.get("workspaces", [])]


def find_workspace(name: str) -> Workspace | None:
    """Find a workspace by custom title (or auto title) matching *name*."""
    for ws in list_workspaces():
        if name in {ws.custom_title or "", ws.title}:
            return ws
    return None


def close_workspace(ws: Workspace) -> None:
    """Close the workspace; cmux terminates every process running under it."""
    _cmux("close-workspace", "--workspace", ws.ref)


# --------------------------------------------------------------------------
# Interactive prompts (choice screens + raw input)
# --------------------------------------------------------------------------


def ask_yes_no(question: str, default: bool = False) -> bool:
    """Raw yes/no input. Empty answer takes the default."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = input(f"{question}{suffix}").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer 'y' or 'n'.")


def choose(
    question: str,
    options: list[str | tuple[str, object]],
    allow_free_text: bool = False,
    default_index: int | None = None,
):
    """Numbered multiple-choice screen.

    Each option is a label string, or a ``(label, payload)`` tuple —
    tuples display the label but return the payload, so callers never
    parse display strings back into data.

    Returns the chosen label/payload, or the raw user input when
    ``allow_free_text`` is true and the user types something that does not
    match a listed number.
    """
    labels: list[str] = []
    payloads: list[object] = []
    for opt in options:
        if isinstance(opt, tuple):
            labels.append(opt[0])
            payloads.append(opt[1])
        else:
            labels.append(opt)
            payloads.append(opt)

    print(f"\n{question}")
    for i, label in enumerate(labels, start=1):
        print(f"  {i}) {label}")
    hint = "Enter a number" + (" (or type your own answer)" if allow_free_text else "")
    while True:
        answer = input(f"{hint}: ").strip()
        if not answer:
            if default_index is not None:
                return payloads[default_index]
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(labels):
            return payloads[int(answer) - 1]
        if allow_free_text:
            return answer
        print(f"Please enter a number between 1 and {len(labels)}.")


def ask_float(question: str, default: float, lo: float, hi: float) -> float:
    """Raw numeric input with bounds; empty answer takes the default."""
    while True:
        answer = input(f"{question} [{lo}-{hi}, default {default}]: ").strip()
        if not answer:
            return default
        try:
            value = float(answer)
        except ValueError:
            print("Please enter a number.")
            continue
        if lo <= value <= hi:
            return value
        print(f"Value must be between {lo} and {hi}.")


# --------------------------------------------------------------------------
# Opening catalog integration (iteration 1: main game only)
# --------------------------------------------------------------------------


def _openings():
    from chess_tui import openings  # noqa: PLC0415 - local import, see module docstring

    return openings


def pick_opening() -> "openings.Opening | None":
    """Pick an opening with a Textual live-filter selector.

    Launches :class:`chess_tui.opening_picker.OpeningPickerApp`, a
    single-screen Textual app: a search box over the bundled catalog
    with a selectable list that filters as you type.  Returns the
    chosen ``openings.Opening``, or ``None`` when the user presses
    Escape (interpreted as "standard game / start position").

    Kept as a thin, separately-monkeypatchable function so tests can
    drive :func:`configure` without launching Textual — they stub this
    out and feed the rest of the wizard (opponent, Nova, observer) via
    plain ``input()``.
    """
    from .opening_picker import OpeningPickerApp  # noqa: PLC0415 - local import

    print(
        "\nOpening setup\n"
        "-------------\n"
        "Opening a Textual picker over the bundled catalog — "
        "type to search, Enter to select, Esc for a standard game."
    )
    return OpeningPickerApp().run()


# --------------------------------------------------------------------------
# Configuration wizard
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NovaConfig:
    """Tuning knobs for ``chess-tui-nova``.

    Temperature and blunder-rate defaults are starter-level choices
    (0.1 / 0.015) that differ from the server's own flag defaults
    (1.0 / 0.0); they are always passed explicitly.
    """

    elo: int = 1500
    temperature: float = 0.1
    top_p: float = 1.0
    blunder_rate: float = 0.015
    classical: float = 0.5
    aggression: float = 0.5


@dataclass(frozen=True)
class EngineObserver:
    """A chess-tui-engine watcher on the game."""

    engine_name: str
    depth: int = 20


@dataclass(frozen=True)
class Setup:
    """Everything the wizard collected."""

    opening: "openings.Opening | None"
    # Side Nova plays; None = no opponent (human plays both sides).
    opponent_color: str | None  # "black" | "white" | None
    nova: NovaConfig | None = None
    nova_port: int | None = None
    observer: "EngineObserver | NovaConfig | None" = None
    observer_port: int | None = None


def pick_opponent_color() -> str | None:
    """Choose who plays which side (Enter = you play white), or both."""
    return choose(
        "Who plays which side?",
        [
            ("You play white, Nova plays black", "black"),
            ("You play black, Nova plays white", "white"),
            ("You play both white and black (no Nova)", None),
        ],
        default_index=0,
    )


def configure_nova() -> NovaConfig:
    """Collect Nova options. Temperature/blunder-rate defaults are
    starter-level values (see NovaConfig), not the server flag defaults."""
    while True:
        answer = input("Nova ELO [800-2700, default 1500]: ").strip()
        if not answer:
            elo = 1500
            break
        if answer.isdigit() and 800 <= int(answer) <= 2700:
            elo = int(answer)
            break
        print("Please enter an integer between 800 and 2700.")

    return NovaConfig(
        elo=elo,
        temperature=ask_float("Sampling temperature (0 = super focused)", 0.1, 0.0, 5.0),
        top_p=ask_float("Nucleus sampling top-p", 1.0, 0.000001, 1.0),
        blunder_rate=ask_float("Blunder rate (random legal moves)", 0.015, 0.0, 1.0),
        classical=ask_float("Classical vs neural weight", 0.5, 0.0, 1.0),
        aggression=ask_float("Aggression level", 0.5, 0.0, 1.0),
    )


def _available_engines() -> list[str]:
    """Engine names from engines.json (same source as chess-tui-engine)."""
    from chess_tui.engine_server import load_engines_config  # noqa: PLC0415

    return list(load_engines_config().get("engines", {}).keys())


def configure_observer() -> EngineObserver | NovaConfig:
    """Pick the observer kind and its settings.

    Returns a NovaConfig (Nova player) or an EngineObserver. The Nova
    path goes through the same settings as the opponent.
    """
    choices: list[tuple[str, object]] = [("Nova player", "nova")]
    choices += [(f"{name} engine", name) for name in _available_engines()]
    kind = choose("Observer:", choices)
    if kind == "nova":
        return configure_nova()
    while True:
        answer = input("Observer search depth [default 20]: ").strip()
        if not answer:
            return EngineObserver(engine_name=kind, depth=20)
        if answer.isdigit() and int(answer) > 0:
            return EngineObserver(engine_name=kind, depth=int(answer))
        print("Please enter a positive integer.")


def configure() -> Setup:
    """Ask for all options, with sensible defaults. Loops until confirmed."""
    while True:
        opening = pick_opening()
        opponent_color = pick_opponent_color()
        nova = configure_nova() if opponent_color else None
        nova_port = free_port() if opponent_color else None
        observer = None
        observer_port = None
        if ask_yes_no("Add an observer watching the game?", default=False):
            observer = configure_observer()
            observer_port = free_port()

        print("\nSummary\n-------")
        print(
            "  opening:       "
            + (opening.name if opening else "standard game (start position)")
        )
        if opponent_color:
            side = "you play white, Nova plays black" if opponent_color == "black" else "you play black, Nova plays white"
            print(f"  opponent:      chess-tui-nova, ELO {nova.elo} ({side})")
            print(f"                 server http://127.0.0.1:{nova_port} in 'opponent' tab")
        else:
            print("  opponent:      none — you play both sides")
        if observer:
            kind = (
                f"chess-tui-nova, ELO {observer.elo}"
                if isinstance(observer, NovaConfig)
                else f"chess-tui-engine ({observer.engine_name}, depth {observer.depth})"
            )
            print(f"  observer:      {kind}")
            print(f"                 server http://127.0.0.1:{observer_port} in 'observer 1' tab")
        else:
            print("  observers:     none (right column stays an empty shell)")
        if ask_yes_no("Create the workspace with this configuration?", default=True):
            return Setup(
                opening=opening,
                opponent_color=opponent_color,
                nova=nova,
                nova_port=nova_port,
                observer=observer,
                observer_port=observer_port,
            )
        print("\nLet's reconfigure.\n")


# --------------------------------------------------------------------------
# Workspace provisioning
# --------------------------------------------------------------------------


def opening_args(opening: "openings.Opening") -> list[str]:
    """CLI args that launch chess-tui exactly at *opening*.

    Prefer ``--opening <name>`` (keeps the move history in the TUI) when
    the name uniquely identifies the row. Names like "Sicilian Defense:
    Najdorf Variation, English Attack" appear on several transposition
    rows, so no query can single them out — fall back to ``--fen`` of the
    chosen row's position (exact, no TUI selector, but no move history).
    """
    openings = _openings()
    if openings.find(opening.name) == [opening]:
        return ["--opening", shlex.quote(opening.name)]
    try:
        return ["--fen", shlex.quote(opening.to_fen())]
    except ValueError:
        return ["--opening", shlex.quote(opening.name)]


def free_port() -> int:
    """Pick a free localhost port for the Nova server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def game_command(setup: Setup) -> str:
    """Build the shell command that runs the main game in the left column.

    Wires the opponent via --black/--white when Nova is configured.
    """
    parts = ["uv", "run", "chess-tui"]
    if setup.opening:
        # The layout command is executed via a shell, so quote the
        # name/FEN (openings contain colons, commas, spaces).
        parts += opening_args(setup.opening)
    if setup.opponent_color:
        url = f"http://127.0.0.1:{setup.nova_port}"
        parts += [f"--{setup.opponent_color}", shlex.quote(url)]
    if setup.observer:
        url = f"http://127.0.0.1:{setup.observer_port}"
        parts += ["--observer", shlex.quote(url)]
    return " ".join(parts)


def _nova_cmd(nova: NovaConfig, port: int) -> str:
    """Shell command that runs a chess-tui-nova server."""
    return " ".join(
        [
            "uv",
            "run",
            "chess-tui-nova",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--elo",
            str(nova.elo),
            "--temperature",
            str(nova.temperature),
            "--top-p",
            str(nova.top_p),
            "--blunder-rate",
            str(nova.blunder_rate),
            "--classical",
            str(nova.classical),
            "--aggression",
            str(nova.aggression),
        ]
    )


def observer_command(setup: Setup) -> str:
    """Shell command that runs the observer server (engine or Nova)."""
    obs = setup.observer
    assert obs is not None and setup.observer_port is not None
    if isinstance(obs, NovaConfig):
        return _nova_cmd(obs, setup.observer_port)
    return " ".join(
        [
            "uv",
            "run",
            "chess-tui-engine",
            "--host",
            "127.0.0.1",
            "--port",
            str(setup.observer_port),
            "--engine-name",
            obs.engine_name,
            "--depth",
            str(obs.depth),
        ]
    )


def nova_command(setup: Setup) -> str:
    """Shell command that runs the Nova opponent server."""
    nova = setup.nova
    assert nova is not None and setup.nova_port is not None
    return _nova_cmd(nova, setup.nova_port)


def create_workspace(setup: Setup) -> str:
    """Create the 2-column workspace; return its ref (e.g. 'workspace:16').

    Left column: the main game (uv run chess-tui ...), plus an
    "opponent" tab running the Nova server when one is configured.
    Right column: an empty "observer 1" tab (shells for now).
    """
    repo_root = Path.cwd()
    opponent_cmd = nova_command(setup) if setup.opponent_color else None
    observer_cmd = observer_command(setup) if setup.observer else None
    left_surfaces: list[dict] = [
        {"type": "terminal", "command": game_command(setup)}
    ]
    if opponent_cmd:
        left_surfaces.append({"type": "terminal", "command": opponent_cmd})
    else:
        left_surfaces.append({"type": "terminal"})
    right_surface: dict = (
        {"type": "terminal", "command": observer_cmd}
        if observer_cmd
        else {"type": "terminal"}
    )
    layout = {
        "direction": "horizontal",
        "split": 0.5,
        "children": [
            {"pane": {"surfaces": left_surfaces}},
            {"pane": {"surfaces": [right_surface]}},
        ],
    }
    out = _cmux(
        "new-workspace",
        "--name",
        WORKSPACE_NAME,
        "--cwd",
        str(repo_root),
        "--layout",
        json.dumps(layout),
        "--focus",
        "true",
    )
    for token in out.split():
        if token.startswith("workspace:"):
            return token
    raise RuntimeError(f"could not parse new-workspace output: {out!r}")


def _workspace_surfaces(ws_ref: str) -> list[list[list[str]]]:
    """Return surfaces per pane of *ws_ref* (outer list: panes in layout
    order; inner list: surfaces in tab order)."""
    data = json.loads(_cmux("tree", "--workspace", ws_ref, "--json"))
    for window in data.get("windows", []):
        for ws in window.get("workspaces", []):
            if ws.get("ref") == ws_ref:
                return [
                    [s["ref"] for s in pane.get("surfaces", [])]
                    for pane in ws.get("panes", [])
                ]
    return []


def name_tabs(ws_ref: str) -> None:
    """Rename the tabs of a freshly created workspace.

    The layout schema ignores surface titles, so naming happens after
    creation: pane 0 -> [chess-tui, opponent], pane 1 -> [observer 1].
    """
    names = [
        ["chess-tui", "opponent"],  # left column
        ["observer 1"],  # right column
    ]
    for pane_surfaces, pane_names in zip(_workspace_surfaces(ws_ref), names):
        for surface_ref, title in zip(pane_surfaces, pane_names):
            # --workspace is required context for the rename to resolve.
            _cmux("rename-tab", "--workspace", ws_ref, "--surface", surface_ref, title)


def main() -> int:
    # -- 1. Refuse to run outside of cmux -----------------------------------
    if not inside_cmux():
        print(
            "starter: ERROR: not running inside cmux "
            "(CMUX_WORKSPACE_ID/CMUX_SURFACE_ID are not set).\n"
            "Open a cmux terminal and re-run this script.",
            file=sys.stderr,
        )
        return EXIT_NOT_IN_CMUX

    if shutil.which("cmux") is None:
        print(
            "starter: ERROR: cmux CLI not found on PATH. "
            "See https://cmux.com/docs/getting-started",
            file=sys.stderr,
        )
        return EXIT_NOT_IN_CMUX

    if not cmux_socket_responsive():
        print(
            "starter: ERROR: cmux control socket is not responsive "
            "(socket access may be disabled). Check Settings -> Socket access.",
            file=sys.stderr,
        )
        return EXIT_NOT_IN_CMUX

    print(f"starter: cmux environment detected (workspace={os.environ['CMUX_WORKSPACE_ID']}).")

    # -- 2. Existing "chess-tui-managed" workspace must go first -------------
    existing = find_workspace(WORKSPACE_NAME)
    if existing is not None:
        ports = f", listening ports: {', '.join(map(str, existing.ports))}" if existing.ports else ""
        if not ask_yes_no(
            f'\nA workspace named "{WORKSPACE_NAME}" already exists '
            f"({existing.ref}{ports}).\n"
            "It must be closed before continuing — this kills ALL processes "
            "running under it (servers, agents, shells) and frees their ports.\n"
            "Close it now?",
            default=True,
        ):
            print(
                f'starter: user declined to close "{WORKSPACE_NAME}" — '
                "exiting to avoid port clashes.",
                file=sys.stderr,
            )
            return EXIT_USER_DECLINED
        close_workspace(existing)
        print(
            f'starter: workspace "{WORKSPACE_NAME}" closed '
            "(processes terminated, ports freed)."
        )
    else:
        print(f'starter: no existing "{WORKSPACE_NAME}" workspace found.')

    # -- 3. Configure the game via the interactive wizard ---------------------
    setup = configure()

    # -- 4. Create the "chess-tui-managed" workspace --------------------------
    print(f"\nstarter: creating workspace \"{WORKSPACE_NAME}\" ...")
    try:
        ref = create_workspace(setup)
        name_tabs(ref)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"starter: ERROR: workspace creation failed: {exc}", file=sys.stderr)
        return EXIT_NOT_IN_CMUX
    print("starter: workspace created and focused:")
    print(f"         left column:  {game_command(setup)} + 'opponent' tab")
    if setup.opponent_color:
        print(f"           opponent tab: {nova_command(setup)}")
    else:
        print("           opponent tab: empty shell")
    if setup.observer:
        print(f"         right column: 'observer 1' tab -> {observer_command(setup)}")
    else:
        print("         right column: 'observer 1' (empty shell)")

    # -- 5. TODO — future iterations ------------------------------------------
    #   * Right column: chess-tui-net / engine / nova / maia servers as tabs,
    #     wired as --black or --observer of the main game.
    #   * Port management (detect + forward), health checks, restart helpers.
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
