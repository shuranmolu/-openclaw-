"""
CLI bridge for mars_memory_engine package.

This module bridges the package CLI to the app CLI.
"""

import sys
from pathlib import Path

# Add parent app to path
_app_path = Path(__file__).parent.parent / "app"
if str(_app_path) not in sys.path:
    sys.path.insert(0, str(_app_path))

from app.cli import main

__all__ = ["main"]
