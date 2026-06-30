"""Sound effects for chess-tui."""

from __future__ import annotations

import os
import platform
import subprocess
import threading

# Path to the click sound
_CLICK_WAV = os.path.join(os.path.dirname(__file__), "click.wav")

# Global silence flag
SILENT: bool = False


def play_click() -> None:
    """Play the click sound in a background thread (non-blocking)."""
    if SILENT or not os.path.exists(_CLICK_WAV):
        return

    def _play() -> None:
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(
                    ["afplay", _CLICK_WAV],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif system == "Linux":
                subprocess.Popen(
                    ["aplay", "-q", _CLICK_WAV],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif system == "Windows":
                subprocess.Popen(
                    ["powershell", "-c", f'(New-Object Media.SoundPlayer "{_CLICK_WAV}").Play()'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass  # Silently ignore audio errors

    threading.Thread(target=_play, daemon=True).start()
