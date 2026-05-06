"""
Entry point for mars_memory_engine package.

Allows running: python -m mars_memory_engine <command>
"""

from .cli import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
