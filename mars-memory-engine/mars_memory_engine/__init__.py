"""
MARS Memory Engine package.
"""

# Import main components from parent app directory
import sys
from pathlib import Path

# Add parent app to path
_app_path = Path(__file__).parent.parent / "app"
if str(_app_path) not in sys.path:
    sys.path.insert(0, str(_app_path))

from .cli import main

__version__ = "0.1.0"

__all__ = ["main"]
