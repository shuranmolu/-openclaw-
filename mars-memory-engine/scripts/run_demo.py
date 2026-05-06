#!/usr/bin/env python3
"""
Demo script for MARS Memory Engine.

Runs the complete demo pipeline and shows results.
"""

import sys
from pathlib import Path

# Add parent directory to path to import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.cli import main


if __name__ == "__main__":
    sys.argv = ["run_demo", "run-demo"]
    sys.exit(main())
