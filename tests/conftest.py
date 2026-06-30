"""Pytest configuration - silence sounds during tests."""

from chess_tui import sound

# Disable click sounds during tests
sound.SILENT = True
