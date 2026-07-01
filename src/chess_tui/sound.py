"""Sound effects for chess-tui."""

from __future__ import annotations

import os
import platform
import subprocess
import threading

# Path to the sound files
_CLICK_WAV = os.path.join(os.path.dirname(__file__), "click.wav")
_CAPTURE_WAV = os.path.join(os.path.dirname(__file__), "capture.wav")

# Global silence flag
SILENT: bool = False


def _play_wav(wav_path: str) -> None:
    """Play a WAV file in a background thread (non-blocking)."""
    if SILENT or not os.path.exists(wav_path):
        return

    def _play() -> None:
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(
                    ["afplay", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif system == "Linux":
                subprocess.Popen(
                    ["aplay", "-q", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif system == "Windows":
                subprocess.Popen(
                    ["powershell", "-c", f'(New-Object Media.SoundPlayer "{wav_path}").Play()'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass  # Silently ignore audio errors

    threading.Thread(target=_play, daemon=True).start()


def play_click() -> None:
    """Play the move sound."""
    _play_wav(_CLICK_WAV)


def play_capture() -> None:
    """Play the capture sound."""
    _play_wav(_CAPTURE_WAV)
