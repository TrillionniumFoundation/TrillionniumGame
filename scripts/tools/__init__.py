"""Temporary import bridge for direct script execution during package generation."""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "tools")]
