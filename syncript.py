#!/usr/bin/env python3
"""
Backward-compatible wrapper for syncript
This allows running: python syncript.py [options]
"""

if __name__ == "__main__":
    from syncript.cli import main
    main()
