"""PyInstaller entry point."""
import sys

from warcrawler.cli import main

if __name__ == "__main__":
    sys.exit(main())
